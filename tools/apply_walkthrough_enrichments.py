#!/usr/bin/env python3
"""Overlay researched, provenance-bearing walkthrough enrichments onto generated catalogs.

The base catalog builder remains a mechanical reconciliation of preserved sources. This pass upgrades
only specifically researched fallback records. It never mutates source evidence and never replaces a
walkthrough sourced from a surviving dedicated historical quest page or period guide.
"""
from __future__ import annotations

from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
QUEST = ROOT / "data" / "quests"
CAT = QUEST / "walkthrough-catalog"
ENRICH = QUEST / "research" / "walkthrough-enrichments.yaml"
REGIONS = ["aurora", "breeze", "steel", "dark", "dungeon", "atlantis"]


def load(path: Path) -> dict:
    if not path.exists():
        return {}
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return value if isinstance(value, dict) else {}


def dump(path: Path, value: object) -> None:
    path.write_text(yaml.safe_dump(value, sort_keys=False, allow_unicode=True, width=200), encoding="utf-8")


def main() -> None:
    enrich_doc = load(ENRICH)
    enrichments = enrich_doc.get("quests", {}) or {}
    applied: set[str] = set()
    skipped_nonminimal: list[str] = []
    missing_ids: list[str] = []
    per_region: dict[str, int] = {}

    all_catalog_ids: set[str] = set()
    for region in REGIONS:
        path = CAT / f"{region}.yaml"
        doc = load(path)
        count = 0
        for quest in doc.get("quests", []) or []:
            if not isinstance(quest, dict):
                continue
            qid = str(quest.get("id") or "")
            if qid:
                all_catalog_ids.add(qid)
            enrichment = enrichments.get(qid)
            if not isinstance(enrichment, dict):
                continue
            current = quest.get("walkthrough") or {}
            source_class = str(current.get("source_class") or "none")
            if source_class not in {"historical_area_table_minimal", "none"}:
                skipped_nonminimal.append(qid)
                continue
            steps = [str(x) for x in enrichment.get("steps", []) or [] if str(x).strip()]
            if not steps:
                continue
            quest["walkthrough"] = {
                "source_class": enrichment.get("source_class") or "researched_historical_enrichment",
                "steps": steps,
                "caveat": enrichment.get("caveat"),
                "conflicts": enrichment.get("conflicts", []) or [],
                "evidence_paths": enrichment.get("evidence_paths", []) or [],
                "enrichment_layer": str(ENRICH.relative_to(ROOT)),
            }
            verification = quest.setdefault("verification", {})
            verification["researched_enrichment_present"] = True
            verification["aomega_client_confirmation"] = verification.get("aomega_client_confirmation", "pending")
            applied.add(qid)
            count += 1
        per_region[region] = count
        dump(path, doc)

    missing_ids = sorted(set(enrichments) - all_catalog_ids)

    # Recalculate manifest walkthrough-source counts after overlays.
    manifest_path = CAT / "manifest.yaml"
    manifest = load(manifest_path)
    regions = manifest.setdefault("regions", {})
    total_enriched = 0
    total_minimal = 0
    total_missing = 0
    for region in REGIONS:
        doc = load(CAT / f"{region}.yaml")
        quests = [q for q in doc.get("quests", []) or [] if isinstance(q, dict)]
        source_counts: dict[str, int] = {}
        for q in quests:
            source = str((q.get("walkthrough") or {}).get("source_class") or "none")
            source_counts[source] = source_counts.get(source, 0) + 1
        node = regions.setdefault(region, {})
        enriched = sum(v for k, v in source_counts.items() if "enrichment" in k)
        minimal = source_counts.get("historical_area_table_minimal", 0)
        missing = source_counts.get("none", 0)
        node["walkthrough_researched_enrichment"] = enriched
        node["walkthrough_area_minimal"] = minimal
        node["walkthrough_missing"] = missing
        total_enriched += enriched
        total_minimal += minimal
        total_missing += missing

    totals = manifest.setdefault("totals", {})
    totals["researched_enriched_walkthroughs"] = total_enriched
    totals["walkthrough_area_minimal"] = total_minimal
    totals["walkthrough_missing"] = total_missing
    manifest["enrichment"] = {
        "applied": True,
        "source": str(ENRICH.relative_to(ROOT)),
        "applied_records": len(applied),
        "per_region": per_region,
        "skipped_because_stronger_walkthrough_exists": sorted(skipped_nonminimal),
        "enrichment_ids_not_found_in_catalog": missing_ids,
        "policy": "Only none/minimal fallbacks are overlaid; stronger historical/period/canonical walkthroughs win.",
    }
    dump(manifest_path, manifest)

    print(yaml.safe_dump({
        "enrichments_declared": len(enrichments),
        "applied": len(applied),
        "per_region": per_region,
        "skipped_nonminimal": len(skipped_nonminimal),
        "missing_ids": missing_ids,
        "remaining_minimal": total_minimal,
        "remaining_missing": total_missing,
    }, sort_keys=False), end="")


if __name__ == "__main__":
    main()
