#!/usr/bin/env python3
"""Audit walkthrough readiness for AOmega's first six preservation regions.

This does not decide truth. It compares canonical quest records with preserved source
research and reports which quests are ready for a full walkthrough, which are only
area-table-derived, and which remain missing or contradictory.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import re
import yaml

ROOT = Path(__file__).resolve().parents[1]
QUEST_ROOT = ROOT / "data" / "quests"
RESEARCH = QUEST_ROOT / "research" / "walkthrough-intake"
AREA_RESEARCH = RESEARCH / "areas"
PAGE_RESEARCH = RESEARCH / "quest-pages"
OUT = RESEARCH / "audit.yaml"
PARSE_ERRORS: list[dict[str, str]] = []

REGIONS: dict[str, list[str]] = {
    "aurora": ["aurora-city", "spike-farm", "sunflower-plain", "dawn-harbor", "riprap-coast", "cherry-village", "crashing-hillock", "thunder-ruins", "thorn-wasteland"],
    "breeze": ["breeze-woods", "dense-forest", "mushroom-forest", "cryptic-moon-swamp", "jade-vale", "mysterious-garden", "quiet-vale", "south-of-mirror-lake", "north-of-mirror-lake"],
    "steel": ["iron-castle", "wishing-tear", "scrap-iron-village", "cactus-plain", "burning-desert", "gebuer-vale", "megalith-plain", "dragon-graveyard", "deity-palace-ruins"],
    "dark": ["dark-city", "shadowy-path", "fungus-forest-south", "bottomless-pit", "degula-maze", "memory-cave", "fungus-forest-north", "foggy-forest", "mysterious-wetland"],
    "dungeon": ["sad-abyss", "fiery-path", "lava-cave", "flaming-door", "underground-square", "hell-palace", "magic-kitchen-path", "gulp-room"],
    "atlantis": ["puqi-village", "golden-beach", "palm-base", "shining-coast", "colorful-coral-reefs", "blue-sea", "wave-harbor", "blue-ocean", "sunken-ruins", "lost-region", "horrible-lost-region", "dream-ocean", "raging-reefs", "quiet-ocean", "coral-vale", "evil-ship"],
}


def load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 - malformed canonical files must be surfaced
        PARSE_ERRORS.append({"path": str(path.relative_to(ROOT)), "error": type(exc).__name__, "detail": str(exc)})
        return {}
    return value if isinstance(value, dict) else {}


def norm(value: object) -> str:
    text = str(value or "").casefold().replace("’", "'")
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"\b(?:part|step)\s*\d+\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def canonical_records() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted((QUEST_ROOT / "areas").glob("*.yaml")):
        doc = load(path)
        area = doc.get("area") or {}
        for quest in doc.get("quests", []) or []:
            if isinstance(quest, dict):
                rows.append({"kind": "area", "path": str(path.relative_to(ROOT)), "area_id": area.get("id") or path.stem, **quest})
    for path in sorted((QUEST_ROOT / "series").glob("*.yaml")):
        doc = load(path)
        series = doc.get("series") or {}
        for quest in doc.get("quests", []) or []:
            if isinstance(quest, dict):
                rows.append({"kind": "series", "path": str(path.relative_to(ROOT)), "series_id": series.get("id") or path.stem, **quest})
    return rows


def area_source_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted(AREA_RESEARCH.glob("*.yaml")):
        doc = load(path)
        area = doc.get("area") or {}
        for row in doc.get("quest_rows", []) or []:
            if isinstance(row, dict):
                rows.append({"area_id": area.get("id") or path.stem, "region": area.get("region"), "source_path": str(path.relative_to(ROOT)), **row})
    return rows


def dedicated_pages() -> list[dict[str, object]]:
    pages: list[dict[str, object]] = []
    for path in sorted(PAGE_RESEARCH.glob("*.yaml")):
        doc = load(path)
        title = doc.get("title_raw")
        matches = doc.get("matched_target_areas", []) or []
        steps = doc.get("walkthrough_steps", []) or []
        walkthrough_raw = doc.get("walkthrough_raw")
        pages.append({
            "path": str(path.relative_to(ROOT)),
            "page_id": doc.get("page_id") or path.stem,
            "title_raw": title,
            "title_key": norm(title),
            "matched_target_areas": matches,
            "has_numbered_steps": bool(steps),
            "numbered_step_count": len(steps),
            "has_walkthrough_prose": bool(walkthrough_raw),
            "source": doc.get("source"),
        })
    return pages


def main() -> None:
    canonical = canonical_records()
    area_rows = area_source_rows()
    pages = dedicated_pages()

    canonical_by_area_name: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)
    canonical_by_name: dict[str, list[dict[str, object]]] = defaultdict(list)
    for quest in canonical:
        name_key = norm(quest.get("name"))
        if name_key:
            canonical_by_name[name_key].append(quest)
            area_id = str(quest.get("area_id") or "")
            if area_id:
                canonical_by_area_name[(area_id, name_key)].append(quest)

    pages_by_title: dict[str, list[dict[str, object]]] = defaultdict(list)
    pages_by_id: dict[str, dict[str, object]] = {}
    for page in pages:
        if page["title_key"]:
            pages_by_title[str(page["title_key"])].append(page)
        pages_by_id[str(page.get("page_id") or "")] = page

    region_for_area = {area: region for region, areas in REGIONS.items() for area in areas}
    detailed: list[dict[str, object]] = []
    status_counts: dict[str, int] = defaultdict(int)
    per_region: dict[str, dict[str, int]] = {region: defaultdict(int) for region in REGIONS}  # type: ignore[assignment]

    seen_area_name: set[tuple[str, str]] = set()
    for row in area_rows:
        area_id = str(row.get("area_id") or "")
        region = region_for_area.get(area_id, str(row.get("region") or "unknown"))
        name_raw = row.get("name_raw")
        name_key = norm(name_raw)
        pair = (area_id, name_key)
        if not name_key or pair in seen_area_name:
            continue
        seen_area_name.add(pair)

        canonical_matches = canonical_by_area_name.get(pair, []) or canonical_by_name.get(name_key, [])
        page_matches = list(pages_by_title.get(name_key, []))
        for link in row.get("quest_links", []) or []:
            if not isinstance(link, dict):
                continue
            filename = Path(str(link.get("url") or "")).stem
            candidate = pages_by_id.get(filename)
            if candidate is not None and candidate not in page_matches:
                page_matches.append(candidate)

        if any(p.get("has_numbered_steps") for p in page_matches):
            status = "dedicated_numbered_walkthrough"
        elif any(p.get("has_walkthrough_prose") for p in page_matches):
            status = "dedicated_walkthrough_prose"
        elif page_matches:
            status = "dedicated_page_without_walkthrough"
        elif canonical_matches and any(q.get("walkthrough") for q in canonical_matches):
            status = "canonical_area_derived_walkthrough"
        elif canonical_matches:
            status = "canonical_without_walkthrough"
        else:
            status = "missing_from_canonical"

        status_counts[status] += 1
        per_region.setdefault(region, defaultdict(int))[status] += 1
        per_region[region]["area_table_quests"] += 1
        detailed.append({
            "region": region,
            "area_id": area_id,
            "quest_name_raw": name_raw,
            "status": status,
            "canonical_ids": [q.get("id") for q in canonical_matches],
            "dedicated_pages": [{"path": p.get("path"), "numbered_step_count": p.get("numbered_step_count"), "has_walkthrough_prose": p.get("has_walkthrough_prose"), "source": p.get("source")} for p in page_matches],
            "source_path": row.get("source_path"),
        })

    orphan_canonical: list[dict[str, object]] = []
    seen_name_keys = {name for _, name in seen_area_name}
    for quest in canonical:
        name_key = norm(quest.get("name"))
        if not name_key or name_key in seen_name_keys:
            continue
        page_matches = pages_by_title.get(name_key, [])
        orphan_canonical.append({
            "id": quest.get("id"),
            "name": quest.get("name"),
            "area_id": quest.get("area_id"),
            "series_id": quest.get("series_id"),
            "canonical_path": quest.get("path"),
            "has_canonical_walkthrough": bool(quest.get("walkthrough")),
            "dedicated_pages": [p.get("path") for p in page_matches],
        })

    missing_area_snapshots = []
    for region, areas in REGIONS.items():
        for area in areas:
            if not (AREA_RESEARCH / f"{area}.yaml").exists():
                missing_area_snapshots.append({"region": region, "area_id": area})

    table_title_keys = {norm(x.get("quest_name_raw")) for x in detailed}
    unlinked_detailed_pages = [
        {"title_raw": p.get("title_raw"), "path": p.get("path"), "matched_target_areas": p.get("matched_target_areas"), "numbered_step_count": p.get("numbered_step_count")}
        for p in pages
        if p.get("has_numbered_steps") and norm(p.get("title_raw")) not in table_title_keys
    ]

    payload = {
        "schema_version": 1,
        "dataset": "quest_walkthrough_readiness_audit",
        "scope": list(REGIONS),
        "counts": {
            "canonical_quest_records_seen": len(canonical),
            "area_source_quest_rows_raw": len(area_rows),
            "unique_area_source_quests": len(detailed),
            "dedicated_quest_pages_preserved": len(pages),
            "dedicated_pages_with_numbered_steps": sum(1 for p in pages if p.get("has_numbered_steps")),
            "missing_area_snapshots": len(missing_area_snapshots),
            "yaml_parse_errors": len(PARSE_ERRORS),
            "status_counts": dict(sorted(status_counts.items())),
        },
        "per_region": {region: dict(sorted(values.items())) for region, values in per_region.items()},
        "yaml_parse_errors": PARSE_ERRORS,
        "missing_area_snapshots": missing_area_snapshots,
        "quest_readiness": detailed,
        "canonical_records_not_seen_in_area_tables": orphan_canonical,
        "numbered_walkthrough_pages_not_linked_to_area_table_rows": unlinked_detailed_pages,
        "pass_conditions": {
            "canonical_yaml_all_parseable": not PARSE_ERRORS,
            "all_target_area_pages_preserved": not missing_area_snapshots,
            "no_area_table_quest_missing_from_canonical": status_counts.get("missing_from_canonical", 0) == 0,
            "all_area_table_quests_have_dedicated_walkthrough": all(x.get("status") in {"dedicated_numbered_walkthrough", "dedicated_walkthrough_prose"} for x in detailed),
        },
        "notes": [
            "Area-table-derived walkthroughs are useful but are not labeled equivalent to a dedicated quest page.",
            "A dedicated page with explicit numbered steps is the strongest web walkthrough evidence in this audit.",
            "Cross-area and faction series may not appear in a local area table and are surfaced separately.",
            "Malformed canonical YAML is reported as a preservation defect instead of aborting the audit.",
            "Source contradictions and target-client verification remain separate from this completeness classification.",
        ],
    }
    output = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=180)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(output, encoding="utf-8")
    print(yaml.safe_dump({"counts": payload["counts"], "per_region": payload["per_region"], "yaml_parse_errors": PARSE_ERRORS, "pass_conditions": payload["pass_conditions"]}, sort_keys=False, allow_unicode=True, width=180), end="")


if __name__ == "__main__":
    main()
