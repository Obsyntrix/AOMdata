#!/usr/bin/env python3
"""Render a compact player-facing quest walkthrough from the sanitized AOmega catalog.

This is a presentation layer only. It never invents quest data and never alters the
preserved evidence. Requirements/rewards come from the historical area table and steps
come from the catalog's selected, sanitized walkthrough.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import argparse
import yaml

ROOT = Path(__file__).resolve().parents[1]
CAT = ROOT / "data" / "quests" / "walkthrough-catalog"
OUT = ROOT / "data" / "quests" / "player-guides"

AREA_ORDER = {
    "aurora": ["aurora-city", "spike-farm", "sunflower-plain", "dawn-harbor", "riprap-coast", "cherry-village", "crashing-hillock", "thunder-ruins", "thorn-wasteland"],
    "breeze": ["breeze-woods", "dense-forest", "mushroom-forest", "cryptic-moon-swamp", "jade-vale", "mysterious-garden", "quiet-vale", "south-of-mirror-lake", "north-of-mirror-lake"],
    "steel": ["iron-castle", "wishing-tear", "scrap-iron-village", "cactus-plain", "burning-desert", "gebuer-vale", "megalith-plain", "dragon-graveyard", "deity-palace-ruins"],
    "dark": ["dark-city", "shadowy-path", "fungus-forest-south", "bottomless-pit", "degula-maze", "memory-cave", "fungus-forest-north", "foggy-forest", "mysterious-wetland"],
    "dungeon": ["sad-abyss", "fiery-path", "lava-cave", "flaming-door", "underground-square", "hell-palace", "magic-kitchen-path", "gulp-room"],
    "atlantis": ["puqi-village", "golden-beach", "palm-base", "shining-coast", "colorful-coral-reefs", "blue-sea", "wave-harbor", "blue-ocean", "sunken-ruins", "lost-region", "horrible-lost-region", "dream-ocean", "raging-reefs", "quiet-ocean", "coral-vale", "evil-ship"],
}


def load(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return value if isinstance(value, dict) else {}


def yes_no(value: object) -> str:
    text = str(value or "").strip().casefold()
    if text == "yes":
        return "Yes"
    if text == "no":
        return "No"
    return str(value or "Unknown")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("region", choices=sorted(AREA_ORDER))
    args = parser.parse_args()
    region = args.region
    doc = load(CAT / f"{region}.yaml")
    quests = [q for q in doc.get("quests", []) or [] if isinstance(q, dict)]
    grouped: dict[str, list[dict]] = defaultdict(list)
    area_names: dict[str, str] = {}
    for q in quests:
        area = q.get("area") or {}
        aid = str(area.get("id") or "unknown")
        grouped[aid].append(q)
        area_names[aid] = str(area.get("name") or aid)

    ordered = list(AREA_ORDER.get(region, []))
    ordered += sorted(a for a in grouped if a not in ordered)

    lines = [
        f"# {region.title()} Quest Walkthrough",
        "",
        f"Canonical quest records: **{len(quests)}**",
        "",
        "> Generated directly from `data/quests/walkthrough-catalog/` after sanitization. Requirements and rewards are preserved from the historical Angels Wiki area tables. Walkthrough steps use the strongest available preserved evidence. Unknown historical values remain unknown.",
        "",
    ]

    n = 0
    for aid in ordered:
        records = grouped.get(aid, [])
        if not records:
            continue
        lines += [f"## {area_names.get(aid, aid)}", ""]
        for q in records:
            n += 1
            hist = q.get("historical_area_table") or {}
            walk = q.get("walkthrough") or {}
            raw = hist.get("row_raw") or {}
            repeatable = raw.get("Repeat.") if "Repeat." in raw else raw.get("Repeatable")
            requirements = hist.get("requirements_raw") or raw.get("Requirements") or "Unknown"
            reward = hist.get("reward_raw") or raw.get("Reward") or "Unknown"
            lines += [
                f"### {n}. {q.get('name')}",
                "",
                f"- **Repeatable:** {yes_no(repeatable)}",
                f"- **Requirements:** {requirements}",
                f"- **Reward:** {reward}",
                f"- **Walkthrough evidence:** `{walk.get('source_class') or 'unknown'}`",
                "",
            ]
            for i, step in enumerate(walk.get("steps", []) or [], start=1):
                lines.append(f"{i}. {str(step).strip()}")
            if walk.get("caveat"):
                lines += ["", f"**Evidence caveat:** {walk['caveat']}"]
            lines.append("")

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{region}.md"
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"Rendered {len(quests)} quests to {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
