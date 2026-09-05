#!/usr/bin/env python3
"""Replace non-walkthrough placeholder text with the strongest available real fallback.

Some historical Angels Wiki quest pages exist but their walkthrough body contains only
editor placeholders such as "Details here.". Presence of a dedicated page must not beat
a period guide containing actual playable steps.
"""
from __future__ import annotations

from pathlib import Path
import re
import yaml

ROOT = Path(__file__).resolve().parents[1]
CAT = ROOT / "data" / "quests" / "walkthrough-catalog"
REGIONS = ["aurora", "breeze", "steel", "dark", "dungeon", "atlantis"]

PLACEHOLDER_PATTERNS = [
    re.compile(r"^details\s+here\.?$", re.I),
    re.compile(r"^details\.?$", re.I),
    re.compile(r"^walkthrough\s+here\.?$", re.I),
    re.compile(r"^tbd\.?$", re.I),
    re.compile(r"^todo\.?$", re.I),
]


def load(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return value if isinstance(value, dict) else {}


def dump(path: Path, value: object) -> None:
    path.write_text(yaml.safe_dump(value, sort_keys=False, allow_unicode=True, width=200), encoding="utf-8")


def is_placeholder(step: object) -> bool:
    text = " ".join(str(step or "").split()).strip()
    return bool(text) and any(p.fullmatch(text) for p in PLACEHOLDER_PATTERNS)


def main() -> None:
    repaired = []
    unresolved = []
    for region in REGIONS:
        path = CAT / f"{region}.yaml"
        doc = load(path)
        changed = False
        for q in doc.get("quests", []) or []:
            if not isinstance(q, dict):
                continue
            walk = q.get("walkthrough") or {}
            steps = [str(x).strip() for x in walk.get("steps", []) or [] if str(x).strip()]
            if not steps or not all(is_placeholder(s) for s in steps):
                continue

            replacement = None
            for guide in q.get("period_guide_matches", []) or []:
                candidate = [str(x).strip() for x in guide.get("steps_raw", []) or [] if str(x).strip()]
                if candidate and not all(is_placeholder(s) for s in candidate):
                    replacement = {
                        "source_class": "period_2012_community_guide_placeholder_recovery",
                        "steps": candidate,
                        "caveat": "The dedicated historical quest page contains only placeholder walkthrough text; a period community guide supplies the playable steps.",
                    }
                    break

            if replacement is None:
                short = str((q.get("historical_area_table") or {}).get("short_description_raw") or "").strip()
                if short and short not in {"?", "-"}:
                    replacement = {
                        "source_class": "historical_area_table_placeholder_recovery",
                        "steps": [short],
                        "caveat": "The dedicated historical quest page contains only placeholder walkthrough text; the historical area table supplies the objective.",
                    }

            if replacement:
                q["walkthrough"] = replacement
                repaired.append({"region": region, "id": q.get("id"), "name": q.get("name"), "source_class": replacement["source_class"]})
                changed = True
            else:
                unresolved.append({"region": region, "id": q.get("id"), "name": q.get("name")})
        if changed:
            dump(path, doc)

    report = CAT / "placeholder-repair.yaml"
    dump(report, {"schema_version": 1, "repaired": repaired, "unresolved": unresolved})
    print(yaml.safe_dump({"repaired": repaired, "unresolved": unresolved}, sort_keys=False, allow_unicode=True, width=180), end="")


if __name__ == "__main__":
    main()
