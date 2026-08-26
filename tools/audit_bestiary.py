#!/usr/bin/env python3
"""Audit the derived AOmega bestiary graph and actual preserved source coverage."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from urllib.parse import urlparse
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
BESTIARY = ROOT / "data" / "bestiary"
INDEXES = BESTIARY / "indexes"
SUMMARY_DIR = BESTIARY / "site" / "summary"
AREA_DIR = BESTIARY / "site" / "areas"
MONSTER_DIR = BESTIARY / "site" / "monster-pages"
OUT = BESTIARY / "audit.yaml"


def load(path: Path) -> object:
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_optional(path: Path) -> dict:
    if not path.exists():
        return {}
    value = load(path) or {}
    return value if isinstance(value, dict) else {}


def expected_source_slugs() -> tuple[set[str], set[str]]:
    area_slugs: set[str] = set()
    monster_slugs: set[str] = set()
    for path in sorted(SUMMARY_DIR.glob("*.yaml")):
        doc = load_optional(path)
        for url in doc.get("discovered_area_urls", []) or []:
            name = Path(urlparse(str(url)).path).name
            if name.startswith("area_") and name.endswith(".html"):
                area_slugs.add(name.removeprefix("area_").removesuffix(".html"))
        for url in doc.get("discovered_monster_urls", []) or []:
            name = Path(urlparse(str(url)).path).name
            if name.startswith("monster_") and name.endswith(".html"):
                monster_slugs.add(name.removeprefix("monster_").removesuffix(".html"))

    # Area pages can expose monster links that do not occur in a summary view.
    for path in sorted(AREA_DIR.glob("*.yaml")):
        doc = load_optional(path)
        for monster in doc.get("monsters", []) or []:
            if not isinstance(monster, dict):
                continue
            url = monster.get("monster_detail_retrieval_url")
            if url:
                name = Path(urlparse(str(url)).path).name
                if name.startswith("monster_") and name.endswith(".html"):
                    monster_slugs.add(name.removeprefix("monster_").removesuffix(".html"))
        for drop in doc.get("drops", []) or []:
            if not isinstance(drop, dict):
                continue
            for link in drop.get("monster_links", []) or []:
                if not isinstance(link, dict):
                    continue
                url = link.get("retrieval_url")
                if url:
                    name = Path(urlparse(str(url)).path).name
                    if name.startswith("monster_") and name.endswith(".html"):
                        monster_slugs.add(name.removeprefix("monster_").removesuffix(".html"))
    return area_slugs, monster_slugs


def main() -> int:
    by_item = load_optional(INDEXES / "by-item.yaml")
    by_name = load_optional(INDEXES / "by-name.yaml")
    by_area = load_optional(INDEXES / "by-area.yaml")
    by_level = load_optional(INDEXES / "by-level.yaml")
    bosses = load_optional(INDEXES / "bosses.yaml")
    crawl_report = load_optional(BESTIARY / "crawl-report.yaml")
    wayback_report = load_optional(BESTIARY / "wayback-monster-report.yaml")

    items = by_item.get("items", []) or []
    monsters = by_name.get("monsters", []) or []
    areas = by_area.get("areas", []) or []
    levels = by_level.get("levels", []) or []
    boss_rows = bosses.get("bosses", []) or []

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

    expected_areas, expected_monsters = expected_source_slugs()
    captured_areas = {path.stem for path in AREA_DIR.glob("*.yaml")}
    captured_monsters = {path.stem for path in MONSTER_DIR.glob("*.yaml")}
    missing_areas = sorted(expected_areas - captured_areas)
    missing_monsters = sorted(expected_monsters - captured_monsters)

    exact_link_rows = sum(count for status, count in resolution.items() if status.startswith("exact_"))
    ambiguous_link_rows = sum(count for status, count in resolution.items() if status.startswith("ambiguous"))
    unresolved_link_rows = resolution.get("unresolved_name", 0)

    payload = {
        "dataset": "aomega_bestiary",
        "audit_kind": "derived_graph_and_source_completeness",
        "source_snapshot_counts": {
            "summary_documents": len(list(SUMMARY_DIR.glob("*.yaml"))),
            "expected_area_pages": len(expected_areas),
            "captured_area_pages": len(captured_areas & expected_areas),
            "expected_monster_detail_pages": len(expected_monsters),
            "captured_historical_monster_detail_pages": len(captured_monsters & expected_monsters),
        },
        "crawl_report_counts": crawl_report.get("counts", {}),
        "wayback_recovery_counts": wayback_report.get("counts", {}),
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
            "missing_area_pages": len(missing_areas),
            "missing_historical_monster_detail_pages": len(missing_monsters),
            "missing_area_slugs": missing_areas,
            "missing_monster_slugs_sample": missing_monsters[:250],
            "missing_monster_slugs_sample_truncated": len(missing_monsters) > 250,
        },
        "unresolved_item_source_examples": unresolved_examples,
        "ambiguous_item_source_examples": ambiguous_examples,
        "pass_conditions": {
            "all_discovered_area_pages_accounted_for": not missing_areas,
            "all_discovered_historical_monster_pages_accounted_for": not missing_monsters,
            "no_unresolved_item_source_names": unresolved_link_rows == 0,
            "indexes_nonempty": bool(areas and levels and monsters and items),
        },
        "notes": [
            "Coverage is computed from files actually preserved in the repository, not from a crawler's transient in-memory count.",
            "Ambiguous links are not silently collapsed because same-name monsters can represent multiple source observations.",
            "Question-mark rates are preserved source values and are not treated as parsing failures.",
            "The 2026 aowiki.uk client-derived monster database is intentionally excluded from historical-monster coverage.",
            "Target-client verification remains a separate AOmega preservation step.",
        ],
    }

    OUT.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=160), encoding="utf-8")
    print(yaml.safe_dump(payload["pass_conditions"], sort_keys=False), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
