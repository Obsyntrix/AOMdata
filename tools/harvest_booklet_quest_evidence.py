#!/usr/bin/env python3
"""Harvest current-client quest evidence from Angels Online Booklet.

This is a SECONDARY corroboration layer. The target preservation corpus is historical
Angels Wiki data, but aog.dvg.cn exposes quest/map records extracted from a current game
client. Those records are invaluable for recovering quest IDs, coordinates and flow text
where the historical wiki is missing or ambiguous. They must never silently overwrite
historical observations.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from urllib.parse import urljoin
import os
import re
import time

import requests
import yaml
from bs4 import BeautifulSoup, Tag

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "quests" / "research" / "current-booklet"
MAP_OUT = OUT / "maps"
QUEST_OUT = OUT / "quest-pages"
REPORT = OUT / "coverage.yaml"
BASE = "https://aog.dvg.cn/"
USER_AGENT = "AOmega-preservation-quest-corroboration/1.0 (+https://github.com/Obsyntrix/AOMdata)"
DELAY = float(os.getenv("AOMDATA_BOOKLET_DELAY", "0.08"))
TIMEOUT = int(os.getenv("AOMDATA_BOOKLET_TIMEOUT", "30"))
MAX_MAP_ID = int(os.getenv("AOMDATA_BOOKLET_MAX_MAP_ID", "220"))
MAX_RETRIES = int(os.getenv("AOMDATA_BOOKLET_RETRIES", "3"))

TARGETS: dict[str, list[str]] = {
    "aurora": ["Aurora City", "Spike Farm", "Sunflower Plain", "Dawn Harbor", "Riprap Coast", "Cherry Village", "Crashing Hillock", "Thunder Ruins", "Thorn Wasteland"],
    "breeze": ["Breeze Woods", "Dense Forest", "Mushroom Forest", "Cryptic Moon Swamp", "Jade Vale", "Mysterious Garden", "Quiet Vale", "South of Mirror Lake", "North of Mirror Lake"],
    "steel": ["Iron Castle", "Wishing Tear", "Scrap Iron Village", "Cactus Plain", "Burning Desert", "Gebuer Vale", "Megalith Plain", "Dragon Graveyard", "Deity Palace Ruins"],
    "dark": ["Dark City", "Shadowy Path", "Fungus Forest South", "Bottomless Pit", "Degula Maze", "Memory Cave", "Fungus Forest North", "Foggy Forest", "Mysterious Wetland"],
    "dungeon": ["Sad Abyss", "Fiery Path", "Lava Cave", "Flaming Door", "Underground Square", "Hell Palace", "Magic Kitchen Path", "Gulp Room"],
    "atlantis": ["Puqi Village", "Golden Beach", "Palm Base", "Shining Coast", "Colorful Coral Reefs", "Blue Sea", "Wave Harbor", "Blue Ocean", "Sunken Ruins", "Lost Region", "Horrible Lost Region", "Dream Ocean", "Raging Reefs", "Quiet Ocean", "Coral Vale", "Evil Ship"],
}

ALIASES = {
    "cactus plains": "cactus plain",
    "sad abbys": "sad abyss",
    "south mirror lake": "south of mirror lake",
    "north mirror lake": "north of mirror lake",
}


def clean(node: Tag | None) -> str:
    return " ".join(node.stripped_strings) if node is not None else ""


def norm(value: object) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()
    return ALIASES.get(text, text)


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")


def dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False, allow_unicode=True, width=180), encoding="utf-8")


class Fetcher:
    def __init__(self) -> None:
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": USER_AGENT})
        self.errors: list[dict[str, object]] = []

    def get(self, url: str) -> requests.Response | None:
        for attempt in range(MAX_RETRIES + 1):
            try:
                r = self.s.get(url, timeout=TIMEOUT)
            except Exception as exc:  # noqa: BLE001
                self.errors.append({"url": url, "attempt": attempt + 1, "error": type(exc).__name__, "detail": str(exc)})
                return None
            if r.status_code == 200:
                if DELAY:
                    time.sleep(DELAY)
                return r
            if r.status_code in {429, 500, 502, 503, 504} and attempt < MAX_RETRIES:
                time.sleep(min(20, 2 ** (attempt + 1)))
                continue
            # Iterating sparse map IDs naturally produces 404s; do not flood the report.
            if r.status_code != 404:
                self.errors.append({"url": url, "status": r.status_code, "attempt": attempt + 1})
            return None
        return None


def source(url: str, content: bytes) -> dict[str, object]:
    return {
        "origin_url": url,
        "retrieval_url": url,
        "source_kind": "aog_booklet_current_client_database",
        "source_html_sha256": sha256(content).hexdigest(),
        "authority_role": "secondary_cross_version_corroboration",
        "historical_authority": False,
        "client_confirmation_for_aomega": "pending",
    }


def section_table(soup: BeautifulSoup, heading_pattern: str) -> list[dict[str, str]]:
    heading = next((h for h in soup.find_all(["h2", "h3"]) if re.search(heading_pattern, clean(h), re.I)), None)
    if heading is None:
        return []
    table = heading.find_next("table")
    if table is None:
        return []
    trs = table.find_all("tr")
    if not trs:
        return []
    headers = [clean(x) for x in trs[0].find_all(["th", "td"], recursive=False)]
    rows = []
    for tr in trs[1:]:
        values = [clean(x) for x in tr.find_all(["th", "td"], recursive=False)]
        if not values:
            continue
        rows.append({headers[i] if i < len(headers) and headers[i] else f"column_{i+1}": value for i, value in enumerate(values)})
    return rows


def all_two_cell_fields(soup: BeautifulSoup) -> dict[str, str]:
    fields: dict[str, str] = {}
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if len(cells) != 2:
            continue
        k, v = clean(cells[0]), clean(cells[1])
        if k and v and len(k) < 120:
            fields.setdefault(k, v)
    return fields


def parse_map(map_id: int, response: requests.Response, region: str, canonical_name: str) -> tuple[dict[str, object], set[int]]:
    soup = BeautifulSoup(response.text, "html.parser")
    title = clean(soup.find("h1"))
    quest_links: dict[int, dict[str, object]] = {}
    for a in soup.find_all("a", href=True):
        href = urljoin(BASE, a["href"])
        match = re.search(r"quest_info\.php\?[^#]*\bid=(\d+)", href)
        if not match:
            continue
        qid = int(match.group(1))
        quest_links.setdefault(qid, {"quest_id": qid, "name_raw": clean(a), "url": f"{BASE}quest_info.php?id={qid}&lang=eng"})

    # Preserve lines around the two quest-map sections because the site exposes coordinate
    # records even when the anchors themselves carry little text.
    section_texts: dict[str, str] = {}
    for label, pattern in [("accepted", r"Quests Accepted on This Map"), ("coordinates_and_steps", r"Quest Coordinates.*Steps")]:
        heading = next((h for h in soup.find_all(["h2", "h3"]) if re.search(pattern, clean(h), re.I)), None)
        if heading is None:
            continue
        bits = []
        for node in heading.next_siblings:
            if isinstance(node, Tag) and node.name in {"h2", "h3"}:
                break
            if isinstance(node, Tag):
                text = clean(node)
                if text:
                    bits.append(text)
        section_texts[label] = "\n".join(bits)

    return ({
        "schema_version": 1,
        "dataset": "current_client_quest_map_evidence",
        "map_id": map_id,
        "name_raw": title,
        "canonical_target_name": canonical_name,
        "region": region,
        "source": source(response.url, response.content),
        "quest_links": sorted(quest_links.values(), key=lambda x: int(x["quest_id"])),
        "quest_section_text_raw": section_texts,
    }, set(quest_links))


def parse_quest(qid: int, response: requests.Response) -> dict[str, object]:
    soup = BeautifulSoup(response.text, "html.parser")
    title = clean(soup.find("h1")) or f"Quest {qid}"
    map_links = []
    for a in soup.find_all("a", href=True):
        href = urljoin(BASE, a["href"])
        match = re.search(r"map_info\.php\?[^#]*\bid=(\d+)", href)
        if match:
            map_links.append({"map_id": int(match.group(1)), "text_raw": clean(a), "url": href})

    # Quest Flow is the critical current-client corroboration. Preserve both parsed table rows
    # and the rendered section text because localization can make column headers inconsistent.
    flow_heading = next((h for h in soup.find_all(["h2", "h3"]) if "quest flow" in clean(h).casefold()), None)
    flow_text = None
    if flow_heading is not None:
        bits = []
        for node in flow_heading.next_siblings:
            if isinstance(node, Tag) and node.name in {"h2", "h3"}:
                break
            if isinstance(node, Tag):
                text = clean(node)
                if text:
                    bits.append(text)
        flow_text = "\n".join(bits) or None

    return {
        "schema_version": 1,
        "dataset": "current_client_quest_evidence",
        "quest_id": qid,
        "name_raw": title,
        "source": source(response.url, response.content),
        "basic_fields_raw": all_two_cell_fields(soup),
        "quest_flow_rows_raw": section_table(soup, r"Quest Flow"),
        "quest_flow_text_raw": flow_text,
        "map_links": map_links,
        "page_text_raw": clean(soup),
    }


def main() -> int:
    fetcher = Fetcher()
    target_lookup = {norm(name): (region, name) for region, names in TARGETS.items() for name in names}
    found_maps: dict[str, dict[str, object]] = {}
    quest_ids: set[int] = set()

    for map_id in range(1, MAX_MAP_ID + 1):
        url = f"{BASE}map_info.php?id={map_id}&lang=eng"
        response = fetcher.get(url)
        if response is None:
            continue
        soup = BeautifulSoup(response.text, "html.parser")
        title = clean(soup.find("h1"))
        match = target_lookup.get(norm(title))
        if not match:
            continue
        region, canonical_name = match
        doc, qids = parse_map(map_id, response, region, canonical_name)
        dump(MAP_OUT / f"{slug(canonical_name)}.yaml", doc)
        found_maps[norm(canonical_name)] = {"map_id": map_id, "name_raw": title, "canonical_target_name": canonical_name, "region": region}
        quest_ids.update(qids)

    captured_quests = 0
    for qid in sorted(quest_ids):
        response = fetcher.get(f"{BASE}quest_info.php?id={qid}&lang=eng")
        if response is None:
            continue
        doc = parse_quest(qid, response)
        dump(QUEST_OUT / f"{qid:05d}-{slug(str(doc['name_raw']))}.yaml", doc)
        captured_quests += 1

    expected = {norm(name): {"region": region, "canonical_target_name": name} for region, names in TARGETS.items() for name in names}
    missing = [value for k, value in expected.items() if k not in found_maps]
    per_region: dict[str, dict[str, int]] = {}
    for region, names in TARGETS.items():
        found = sum(1 for name in names if norm(name) in found_maps)
        per_region[region] = {"expected_maps": len(names), "captured_maps": found, "missing_maps": len(names) - found}

    report = {
        "schema_version": 1,
        "dataset": "current_client_quest_corroboration_coverage",
        "authority_role": "secondary_cross_version_corroboration",
        "warning": "This source reflects a current client database. It may corroborate or expose gaps in historical Angels Wiki data, but it must not silently replace historical values for AOmega.",
        "map_id_scan": {"first": 1, "last": MAX_MAP_ID},
        "per_region": per_region,
        "counts": {"target_maps_expected": len(expected), "target_maps_captured": len(found_maps), "quest_ids_discovered": len(quest_ids), "quest_pages_captured": captured_quests},
        "captured_maps": sorted(found_maps.values(), key=lambda x: (str(x["region"]), str(x["canonical_target_name"]))),
        "missing_target_maps": missing,
        "retrieval_errors": fetcher.errors,
    }
    dump(REPORT, report)
    print(yaml.safe_dump({"per_region": per_region, "counts": report["counts"], "missing_target_maps": missing}, sort_keys=False, allow_unicode=True, width=180), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
