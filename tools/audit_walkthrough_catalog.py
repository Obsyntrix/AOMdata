#!/usr/bin/env python3
"""Audit generated quest walkthrough catalogs for real user-facing holes and bad links."""
from __future__ import annotations

from pathlib import Path
import re
import yaml

ROOT = Path(__file__).resolve().parents[1]
CAT = ROOT / "data" / "quests" / "walkthrough-catalog"
OUT = CAT / "gaps.yaml"
REGIONS = ["aurora", "breeze", "steel", "dark", "dungeon", "atlantis"]


def load(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return value if isinstance(value, dict) else {}


def norm(value: object) -> str:
    text = str(value or "").casefold().replace("’", "'").replace("&", " and ")
    text = re.sub(r"\b\d+\s+of\s+\d+\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def main() -> None:
    missing = []
    minimal = []
    mismatched_historical_pages = []
    suspicious_canonical_cross_area = []
    per_region = {}

    for region in REGIONS:
        doc = load(CAT / f"{region}.yaml")
        stats = {"quests": 0, "missing": 0, "minimal": 0, "mismatched_historical_page_links": 0, "suspicious_canonical_cross_area": 0}
        for q in doc.get("quests", []) or []:
            if not isinstance(q, dict):
                continue
            stats["quests"] += 1
            area = q.get("area") or {}
            area_id = str(area.get("id") or "")
            name = q.get("name")
            source_class = ((q.get("walkthrough") or {}).get("source_class"))
            if source_class == "none":
                stats["missing"] += 1
                missing.append({"region": region, "area_id": area_id, "id": q.get("id"), "name": name, "short_description_raw": ((q.get("historical_area_table") or {}).get("short_description_raw"))})
            elif source_class == "historical_area_table_minimal":
                stats["minimal"] += 1
                minimal.append({"region": region, "area_id": area_id, "id": q.get("id"), "name": name, "steps": ((q.get("walkthrough") or {}).get("steps"))})

            qkey = norm(name)
            for page in q.get("historical_dedicated_pages", []) or []:
                if not isinstance(page, dict):
                    continue
                pkey = norm(page.get("title_raw"))
                if pkey and qkey and pkey != qkey:
                    stats["mismatched_historical_page_links"] += 1
                    mismatched_historical_pages.append({"region": region, "area_id": area_id, "quest": name, "page_title": page.get("title_raw"), "page_source": (page.get("source") or {}).get("path")})

            for c in q.get("existing_canonical_matches", []) or []:
                if not isinstance(c, dict):
                    continue
                path = str(c.get("path") or "")
                if path.startswith("data/quests/areas/") and area_id and Path(path).stem != area_id:
                    stats["suspicious_canonical_cross_area"] += 1
                    suspicious_canonical_cross_area.append({"region": region, "area_id": area_id, "quest": name, "canonical_id": c.get("id"), "canonical_path": path})
        per_region[region] = stats

    payload = {
        "schema_version": 1,
        "dataset": "walkthrough_catalog_gap_audit",
        "per_region": per_region,
        "totals": {
            "missing_walkthroughs": len(missing),
            "area_minimal_walkthroughs": len(minimal),
            "mismatched_historical_page_links": len(mismatched_historical_pages),
            "suspicious_canonical_cross_area_links": len(suspicious_canonical_cross_area),
        },
        "missing_walkthroughs": missing,
        "area_minimal_walkthroughs": minimal,
        "mismatched_historical_page_links": mismatched_historical_pages,
        "suspicious_canonical_cross_area_links": suspicious_canonical_cross_area,
        "pass_conditions": {
            "no_missing_walkthroughs": not missing,
            "no_mismatched_historical_page_links": not mismatched_historical_pages,
            "no_cross_area_canonical_links": not suspicious_canonical_cross_area,
        },
    }
    OUT.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=180), encoding="utf-8")
    print(yaml.safe_dump({"per_region": per_region, "totals": payload["totals"], "pass_conditions": payload["pass_conditions"]}, sort_keys=False, allow_unicode=True, width=180), end="")


if __name__ == "__main__":
    main()
