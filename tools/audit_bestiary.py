#!/usr/bin/env python3
"""Audit the derived AOmega bestiary graph for preservation/query completeness."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
BESTIARY = ROOT / "data" / "bestiary"
INDEXES = BESTIARY / "indexes"
OUT = BESTIARY / "audit.yaml"


def load(path: Path) -> object:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def main() -> int:
    by_item = load(INDEXES / "by-item.yaml") or {}
    by_name = load(INDEXES / "by-name.yaml") or {}
    by_area = load(INDEXES / "by-area.yaml") or {}
    by_level = load(INDEXES / "by-level.yaml") or {}
    bosses = load(INDEXES / "bosses.yaml") or {}
    report = load(BESTIARY / "crawl-report.yaml") or {}

    items = by_item.get("items", []) if isinstance(by_item, dict) else []
    monsters = by_name.get("monsters", []) if isinstance(by_name, dict) else []
    areas = by_area.get("areas", []) if isinstance(by_area, dict) else []
    levels = by_level.get("levels", []) if isinstance(by_level, dict) else []
    boss_rows = bosses.get("bosses", []) if isinstance(bosses, dict) else []

    resolution = Counter()
    source_kinds = Counter()
    rate_semantics = Counter()
    raw_rate_unknown = 0
    source_rows = 0
    items_with_multiple_sources = 0
    unresolved_examples: list[dict[str, object]] = []
    ambiguous_examples: list[dict[str, object]] = []

    for item in items:
        if not isinstance(item, dict):
            continue
        sources = item.get("sources", []) or []
        if len(sources) > 1:
            items_with_multiple_sources += 1
        for source in sources:
            if not isinstance(source, dict):
                continue
            source_rows += 1
            status = str(source.get("resolution_status") or "missing")
            resolution[status] += 1
            source_kinds[str(source.get("source_kind") or "missing")] += 1
            rate_semantics[str(source.get("rate_semantic") or "missing")] += 1
            raw = source.get("rate_raw")
            if raw is None or str(raw).strip() == "" or "?" in str(raw):
                raw_rate_unknown += 1
            if status == "unresolved_name" and len(unresolved_examples) < 100:
                unresolved_examples.append({
                    "item_name_raw": item.get("item_name_raw"),
                    "monster_name_raw": source.get("monster_name_raw"),
                    "area_id": source.get("source_page_area_id"),
                    "rate_raw": source.get("rate_raw"),
                    "source_kind": source.get("source_kind"),
                })
            if status.startswith("ambiguous") and len(ambiguous_examples) < 100:
                ambiguous_examples.append({
                    "item_name_raw": item.get("item_name_raw"),
                    "monster_name_raw": source.get("monster_name_raw"),
                    "area_id": source.get("source_page_area_id"),
                    "rate_raw": source.get("rate_raw"),
                    "source_kind": source.get("source_kind"),
                    "candidate_count": len(source.get("resolved_monster_appearances", []) or []),
                })

    names_with_multiple_appearances = 0
    names_with_level_variants = 0
    names_with_area_variants = 0
    for monster in monsters:
        if not isinstance(monster, dict):
            continue
        appearances = monster.get("appearances", []) or []
        if len(appearances) > 1:
            names_with_multiple_appearances += 1
        level_values = {str(x.get("level_raw")) for x in appearances if isinstance(x, dict)}
        area_values = {str(x.get("area_id")) for x in appearances if isinstance(x, dict)}
        if len(level_values) > 1:
            names_with_level_variants += 1
        if len(area_values) > 1:
            names_with_area_variants += 1

    report_counts = report.get("counts", {}) if isinstance(report, dict) else {}
    unresolved_areas = report.get("unresolved_area_urls", []) if isinstance(report, dict) else []
    unresolved_monsters = report.get("unresolved_monster_urls", []) if isinstance(report, dict) else []

    exact_link_rows = sum(count for status, count in resolution.items() if status.startswith("exact_"))
    ambiguous_link_rows = sum(count for status, count in resolution.items() if status.startswith("ambiguous"))
    unresolved_link_rows = resolution.get("unresolved_name", 0)

    payload = {
        "dataset": "aomega_bestiary",
        "audit_kind": "derived_graph_completeness",
        "crawl_status": report.get("status") if isinstance(report, dict) else None,
        "crawl_counts": report_counts,
        "index_counts": {
            "areas": len(areas),
            "levels": len(levels),
            "monster_names": len(monsters),
            "items": len(items),
            "boss_appearances": len(boss_rows),
            "drop_source_rows": source_rows,
        },
        "item_source_linkage": {
            "exact_rows": exact_link_rows,
            "ambiguous_rows": ambiguous_link_rows,
            "unresolved_rows": unresolved_link_rows,
            "resolution_status_counts": dict(sorted(resolution.items())),
            "source_kind_counts": dict(sorted(source_kinds.items())),
            "rate_semantic_counts": dict(sorted(rate_semantics.items())),
            "raw_rate_unknown_or_question_mark_rows": raw_rate_unknown,
            "items_with_multiple_source_observations": items_with_multiple_sources,
        },
        "monster_name_reuse": {
            "names_with_multiple_appearances": names_with_multiple_appearances,
            "names_with_multiple_levels": names_with_level_variants,
            "names_with_multiple_areas": names_with_area_variants,
            "note": "Name reuse is evidence to preserve, not a request to merge records.",
        },
        "retrieval_gaps": {
            "unresolved_area_pages": len(unresolved_areas or []),
            "unresolved_monster_detail_pages": len(unresolved_monsters or []),
        },
        "unresolved_item_source_examples": unresolved_examples,
        "ambiguous_item_source_examples": ambiguous_examples,
        "pass_conditions": {
            "all_discovered_monster_pages_accounted_for": (
                isinstance(report_counts, dict)
                and report_counts.get("captured_monster_detail_pages") == report_counts.get("discovered_monster_detail_pages")
            ),
            "no_unresolved_item_source_names": unresolved_link_rows == 0,
            "indexes_nonempty": bool(areas and levels and monsters and items),
        },
        "notes": [
            "Ambiguous links are not silently collapsed because same-name monsters can represent multiple source observations.",
            "Question-mark rates are preserved source values and are not treated as parsing failures.",
            "This audit evaluates the derived lookup graph; target-client verification remains a separate preservation step.",
        ],
    }

    OUT.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=160), encoding="utf-8")
    print(yaml.safe_dump(payload["pass_conditions"], sort_keys=False), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
