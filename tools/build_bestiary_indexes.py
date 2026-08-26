#!/usr/bin/env python3
"""Build query indexes from the preserved Angels Wiki bestiary snapshot.

Source YAML is authoritative. Files under data/bestiary/indexes are derived and may be
regenerated at any time.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import re

import yaml

ROOT = Path(__file__).resolve().parents[1]
AREA_DIR = ROOT / "data" / "bestiary" / "site" / "areas"
INDEX_DIR = ROOT / "data" / "bestiary" / "indexes"


def slugify(value: str) -> str:
    value = value.strip().lower().replace("'", "")
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "unknown"


def parse_percent(raw: object) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip().replace(",", ".")
    if text.startswith("<") or "?" in text:
        return None
    if text.endswith("%"):
        text = text[:-1]
    try:
        return float(text)
    except ValueError:
        return None


def parse_int(raw: object) -> int | None:
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "")
    try:
        return int(text)
    except ValueError:
        return None


def dump_yaml(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=120),
        encoding="utf-8",
    )


def main() -> None:
    by_area: dict[str, dict] = {}
    by_level: dict[int, list[dict]] = defaultdict(list)
    by_item: dict[str, dict] = {}
    by_name: dict[str, dict] = {}
    bosses: list[dict] = []

    for path in sorted(AREA_DIR.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        area = doc.get("area", {})
        area_id = area.get("id") or path.stem
        area_name = area.get("name_raw") or area_id
        source = area.get("source", {})

        monster_entries = []
        for monster in doc.get("monsters", []) or []:
            spawn_id = monster.get("spawn_id") or f"{area_id}__{slugify(monster.get('name_raw', 'unknown'))}"
            level_raw = monster.get("level_raw")
            level = (monster.get("normalized") or {}).get("level")
            if level is None:
                level = parse_int(level_raw)

            boss_raw = monster.get("boss_raw")
            boss = (monster.get("normalized") or {}).get("boss")
            if boss is None and boss_raw is not None:
                boss = str(boss_raw).strip().lower() in {"yes", "boss", "boss smn", "no boss"} and str(boss_raw).strip().lower() != "no"

            ref = {
                "spawn_id": spawn_id,
                "name_raw": monster.get("name_raw"),
                "level_raw": level_raw,
                "level": level,
                "boss_raw": boss_raw,
                "boss": boss,
                "aggressive_raw": monster.get("aggressive_raw"),
                "area_id": area_id,
                "area_name_raw": area_name,
                "source_file": str(path.relative_to(ROOT)),
                "source_url": source.get("origin_url"),
            }
            monster_entries.append(ref)

            if level is not None:
                by_level[level].append(ref)
            if boss:
                bosses.append(ref)

            name_key = str(monster.get("name_raw") or "").casefold()
            if name_key:
                node = by_name.setdefault(
                    name_key,
                    {"name_raw": monster.get("name_raw"), "appearances": []},
                )
                node["appearances"].append(ref)

        by_area[area_id] = {
            "area_id": area_id,
            "area_name_raw": area_name,
            "monsters": monster_entries,
            "source_file": str(path.relative_to(ROOT)),
            "source_url": source.get("origin_url"),
        }

        boss_lookup = {
            (m.get("name_raw") or "").casefold(): (m.get("normalized") or {}).get("boss")
            for m in doc.get("monsters", []) or []
        }
        spawn_lookup = {
            (m.get("name_raw") or "").casefold(): m.get("spawn_id")
            for m in doc.get("monsters", []) or []
        }

        for drop in doc.get("drops", []) or []:
            item_name = drop.get("item_name_raw")
            monster_name = drop.get("monster_name_raw")
            if not item_name or not monster_name:
                continue
            item_key = str(item_name).casefold()
            node = by_item.setdefault(item_key, {"item_name_raw": item_name, "sources": []})
            raw_rate = drop.get("drop_share_raw")
            source_entry = {
                "monster_name_raw": monster_name,
                "spawn_id": drop.get("spawn_id") or spawn_lookup.get(str(monster_name).casefold()),
                "area_id": area_id,
                "area_name_raw": area_name,
                "boss": boss_lookup.get(str(monster_name).casefold()),
                "rate_raw": raw_rate,
                "rate_percent": parse_percent(raw_rate),
                "rate_semantic": drop.get("rate_semantic", "zone_drop_share"),
                "quantity_raw": drop.get("quantity_raw"),
                "source_file": str(path.relative_to(ROOT)),
                "source_url": source.get("origin_url"),
            }
            node["sources"].append(source_entry)

    level_payload = {
        "levels": [
            {"level": level, "monsters": sorted(rows, key=lambda x: (x["name_raw"] or "", x["area_id"]))}
            for level, rows in sorted(by_level.items())
        ]
    }
    area_payload = {"areas": list(by_area.values())}
    item_payload = {"items": sorted(by_item.values(), key=lambda x: (x["item_name_raw"] or "").casefold())}
    name_payload = {"monsters": sorted(by_name.values(), key=lambda x: (x["name_raw"] or "").casefold())}
    boss_payload = {"bosses": sorted(bosses, key=lambda x: ((x["level"] or -1), x["name_raw"] or "", x["area_id"]))}

    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    dump_yaml(INDEX_DIR / "by-area.yaml", area_payload)
    dump_yaml(INDEX_DIR / "by-level.yaml", level_payload)
    dump_yaml(INDEX_DIR / "by-item.yaml", item_payload)
    dump_yaml(INDEX_DIR / "by-name.yaml", name_payload)
    dump_yaml(INDEX_DIR / "bosses.yaml", boss_payload)

    print(f"Indexed {sum(len(v['monsters']) for v in by_area.values())} area monster appearances")
    print(f"Indexed {len(by_item)} unique item names with recorded drop sources")


if __name__ == "__main__":
    main()
