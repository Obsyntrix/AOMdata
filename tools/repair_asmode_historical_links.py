#!/usr/bin/env python3
"""Recover dedicated historical Asmode quest pages hidden by display-name suffixes.

Area-table rows often append metadata such as `Asmode 8 of 8`, while their direct quest
link text uses only the base title. This pass accepts that one narrowly-defined naming
variation and prefers a real historical walkthrough over weaker fallback text.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse
import re
import yaml

ROOT = Path(__file__).resolve().parents[1]
QUEST = ROOT / "data" / "quests"
CAT = QUEST / "walkthrough-catalog"
HIST = QUEST / "research" / "walkthrough-intake"
REGIONS = ["aurora", "breeze", "steel", "dark", "dungeon", "atlantis"]


def load(path: Path) -> dict:
    if not path.exists():
        return {}
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return value if isinstance(value, dict) else {}


def dump(path: Path, value: object) -> None:
    path.write_text(yaml.safe_dump(value, sort_keys=False, allow_unicode=True, width=200), encoding="utf-8")


def norm(value: object) -> str:
    text = str(value or "").casefold().replace("’", "'").replace("&", " and ")
    text = re.sub(r"\b\d+\s+of\s+\d+\b", " ", text)
    text = re.sub(r"\b(?:part|step)\s*\d+\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def asmode_base(value: object) -> str:
    tokens = [t for t in norm(value).split() if t != "asmode"]
    return " ".join(tokens)


def split_walkthrough(raw: object) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    matches = list(re.finditer(r"\bStep\s+(\d+)\b", text, re.I))
    if not matches:
        return [text]
    out = []
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip(" :-\n\t")
        if body:
            out.append(body)
    return out


def page_payload(page: dict) -> dict:
    src = page.get("source") or {}
    return {
        "title_raw": page.get("title_raw"),
        "summary_raw": page.get("summary_raw"),
        "information_raw": page.get("information_raw"),
        "requirements_raw": page.get("requirements_raw"),
        "reward_raw": page.get("reward_raw"),
        "walkthrough_raw": page.get("walkthrough_raw"),
        "walkthrough_steps": page.get("walkthrough_steps"),
        "matched_via": "exact_area_quest_link_text_asmode_suffix",
        "source": {
            "path": page.get("_path"),
            "origin_url": src.get("origin_url"),
            "retrieval_url": src.get("retrieval_url"),
            "source_kind": src.get("source_kind"),
            "archive_timestamp": src.get("archive_timestamp"),
            "source_html_sha256": src.get("source_html_sha256"),
            "authority_role": src.get("authority_role"),
        },
    }


def main() -> None:
    pages = {}
    for path in (HIST / "quest-pages").glob("*.yaml"):
        page = load(path)
        if page:
            page["_path"] = str(path.relative_to(ROOT))
            pages[str(page.get("page_id") or path.stem)] = page

    area_rows = {}
    for path in (HIST / "areas").glob("*.yaml"):
        doc = load(path)
        aid = str((doc.get("area") or {}).get("id") or path.stem)
        for row in doc.get("quest_rows", []) or []:
            if isinstance(row, dict) and row.get("name_raw"):
                area_rows[(aid, norm(row.get("name_raw")))] = row

    repaired = []
    for region in REGIONS:
        path = CAT / f"{region}.yaml"
        doc = load(path)
        changed = False
        for q in doc.get("quests", []) or []:
            if not isinstance(q, dict) or "asmode" not in norm(q.get("name")):
                continue
            aid = str((q.get("area") or {}).get("id") or "")
            row = area_rows.get((aid, norm(q.get("name"))))
            if not row:
                continue
            base = asmode_base(q.get("name"))
            chosen = None
            for link in row.get("quest_links", []) or []:
                if not isinstance(link, dict) or norm(link.get("text_raw")) != base:
                    continue
                page_id = Path(urlparse(str(link.get("url") or "")).path).stem
                page = pages.get(page_id)
                if not page:
                    continue
                steps = split_walkthrough(page.get("walkthrough_raw"))
                if steps:
                    chosen = (page, steps)
                    break
            if not chosen:
                continue
            page, steps = chosen
            existing_paths = {str((p.get("source") or {}).get("path") or "") for p in q.get("historical_dedicated_pages", []) or [] if isinstance(p, dict)}
            payload = page_payload(page)
            if str((payload.get("source") or {}).get("path") or "") not in existing_paths:
                q.setdefault("historical_dedicated_pages", []).append(payload)
            current_source = str((q.get("walkthrough") or {}).get("source_class") or "none")
            if current_source != "historical_dedicated_quest_page":
                q["walkthrough"] = {"source_class": "historical_dedicated_quest_page", "steps": steps}
            q.setdefault("verification", {})["dedicated_historical_page_present"] = True
            repaired.append({"region": region, "id": q.get("id"), "name": q.get("name"), "page_id": page.get("page_id")})
            changed = True
        if changed:
            dump(path, doc)

    dump(CAT / "asmode-link-repair.yaml", {"schema_version": 1, "repaired": repaired})
    print(yaml.safe_dump({"repaired": repaired}, sort_keys=False, allow_unicode=True, width=180), end="")


if __name__ == "__main__":
    main()
