#!/usr/bin/env python3
"""Recover walkthrough-grade quest evidence for AOmega's preservation regions.

Target scope is intentionally limited to the first preservation walkthrough set:
Aurora, Breeze, Steel/Iron, Dark/Shadow, Dungeon/Demon's Kitchen, and Atlantis.

The harvester prefers the static aowiki.uk mirror when it preserves the old
angels.wikidot.com page. Missing pages fall back to Internet Archive snapshots of the
original Wikidot URL. Displayed text is preserved as evidence; normalized fields are
additive and never overwrite the source wording.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from urllib.parse import urljoin, urlparse
import os
import re
import time

import requests
import yaml
from bs4 import BeautifulSoup, Tag

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "quests" / "research" / "walkthrough-intake"
AREA_OUT = OUT / "areas"
QUEST_OUT = OUT / "quest-pages"
REPORT = OUT / "coverage.yaml"
MIRROR = "https://www.aowiki.uk/pages/"
AVAILABLE = "https://archive.org/wayback/available"
USER_AGENT = "AOmega-preservation-quest-research/1.0 (+https://github.com/Obsyntrix/AOMdata)"
DELAY = float(os.getenv("AOMDATA_QUEST_DELAY", "0.12"))
TIMEOUT = int(os.getenv("AOMDATA_QUEST_TIMEOUT", "35"))
MAX_RETRIES = int(os.getenv("AOMDATA_QUEST_RETRIES", "4"))

REGIONS: dict[str, list[str]] = {
    "aurora": [
        "aurora-city", "spike-farm", "sunflower-plain", "dawn-harbor",
        "riprap-coast", "cherry-village", "crashing-hillock", "thunder-ruins",
        "thorn-wasteland",
    ],
    "breeze": [
        "breeze-woods", "dense-forest", "mushroom-forest", "cryptic-moon-swamp",
        "jade-vale", "mysterious-garden", "quiet-vale", "south-of-mirror-lake",
        "north-of-mirror-lake",
    ],
    "steel": [
        "iron-castle", "wishing-tear", "scrap-iron-village", "cactus-plain",
        "burning-desert", "gebuer-vale", "megalith-plain", "dragon-graveyard",
        "deity-palace-ruins",
    ],
    "dark": [
        "dark-city", "shadowy-path", "fungus-forest-south", "bottomless-pit",
        "degula-maze", "memory-cave", "fungus-forest-north", "foggy-forest",
        "mysterious-wetland",
    ],
    "dungeon": [
        "sad-abyss", "fiery-path", "lava-cave", "flaming-door",
        "underground-square", "hell-palace", "magic-kitchen-path", "gulp-room",
    ],
    "atlantis": [
        "puqi-village", "golden-beach", "palm-base", "shining-coast",
        "colorful-coral-reefs", "blue-sea", "wave-harbor", "blue-ocean",
        "sunken-ruins", "lost-region", "horrible-lost-region", "dream-ocean",
        "raging-reefs", "quiet-ocean", "coral-vale", "evil-ship",
    ],
}

# Pages that enumerate quests but are not ordinary area pages.
SPECIAL_SEEDS = [
    "quests_dungeon-quest.html",
    "directory_asmodeum.html",
]


def clean(node: Tag | None) -> str:
    if node is None:
        return ""
    return " ".join(node.stripped_strings)


def key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False, allow_unicode=True, width=180), encoding="utf-8")


def table_rows(table: Tag) -> tuple[list[str], list[tuple[list[Tag], list[str]]]]:
    trs = table.find_all("tr")
    if not trs:
        return [], []
    heads = trs[0].find_all(["th", "td"], recursive=False)
    headers = [clean(x) for x in heads]
    rows: list[tuple[list[Tag], list[str]]] = []
    for tr in trs[1:]:
        cells = tr.find_all(["th", "td"], recursive=False)
        if not cells:
            continue
        values = [clean(x) for x in cells]
        if [key(x) for x in values] == [key(x) for x in headers]:
            continue
        rows.append((cells, values))
    return headers, rows


def links(node: Tag | None) -> list[dict[str, str]]:
    if node is None:
        return []
    result = []
    for a in node.find_all("a", href=True):
        result.append({"text_raw": clean(a), "url": urljoin(MIRROR, a["href"])})
    return result


def quest_filename(url: str) -> str | None:
    name = Path(urlparse(url).path).name
    if (name.startswith("quest_") or name.startswith("quests_")) and name.endswith(".html"):
        return name
    return None


def page_region(area_slug: str) -> str | None:
    for region, areas in REGIONS.items():
        if area_slug in areas:
            return region
    return None


@dataclass
class Page:
    retrieval_url: str
    origin_url: str
    source_kind: str
    text: str
    archive_timestamp: str | None = None

    @property
    def digest(self) -> str:
        return sha256(self.text.encode("utf-8", errors="replace")).hexdigest()

    @property
    def soup(self) -> BeautifulSoup:
        return BeautifulSoup(self.text, "html.parser")


class Retriever:
    def __init__(self) -> None:
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": USER_AGENT})
        self.errors: list[dict[str, object]] = []

    def request(self, url: str) -> requests.Response | None:
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self.s.get(url, timeout=TIMEOUT)
            except Exception as exc:  # noqa: BLE001
                self.errors.append({"url": url, "attempt": attempt + 1, "error": type(exc).__name__, "detail": str(exc)})
                return None
            if response.status_code == 200:
                if DELAY:
                    time.sleep(DELAY)
                return response
            if response.status_code in {429, 500, 502, 503, 504} and attempt < MAX_RETRIES:
                retry = response.headers.get("Retry-After")
                try:
                    pause = float(retry) if retry else min(30.0, 2 ** (attempt + 1))
                except ValueError:
                    pause = min(30.0, 2 ** (attempt + 1))
                time.sleep(pause)
                continue
            self.errors.append({"url": url, "status": response.status_code, "attempt": attempt + 1})
            return None
        return None

    def wayback(self, origin_url: str) -> Page | None:
        response = self.request(AVAILABLE + "?url=" + requests.utils.quote(origin_url, safe=""))
        if response is None:
            return None
        try:
            closest = ((response.json().get("archived_snapshots") or {}).get("closest") or {})
        except Exception:  # noqa: BLE001
            return None
        if not closest.get("available") or str(closest.get("status")) != "200":
            return None
        snap = str(closest.get("url") or "").replace("http://web.archive.org/", "https://web.archive.org/", 1)
        timestamp = str(closest.get("timestamp") or "") or None
        if not snap:
            return None
        raw = re.sub(r"/web/(\d+)(?:[a-z_]+)?/", r"/web/\1id_/", snap)
        response = self.request(raw)
        if response is None:
            response = self.request(snap)
        if response is None:
            return None
        return Page(raw, origin_url, "angels_wikidot_wayback", response.text, timestamp)

    def area(self, slug: str) -> Page | None:
        mirror_url = urljoin(MIRROR, f"area_{slug}.html")
        origin = f"https://angels.wikidot.com/area:{slug}"
        response = self.request(mirror_url)
        if response is not None and "angels.wikidot.com" in response.text.casefold():
            return Page(mirror_url, origin, "aowiki_static_legacy_mirror", response.text)
        return self.wayback(origin)

    def named_page(self, filename: str) -> Page | None:
        mirror_url = urljoin(MIRROR, filename)
        if filename.startswith("quest_"):
            origin = "https://angels.wikidot.com/quest:" + filename.removeprefix("quest_").removesuffix(".html")
        elif filename.startswith("quests_"):
            origin = "https://angels.wikidot.com/quests:" + filename.removeprefix("quests_").removesuffix(".html")
        elif filename.startswith("directory_"):
            origin = "https://angels.wikidot.com/directory:" + filename.removeprefix("directory_").removesuffix(".html")
        else:
            origin = mirror_url
        response = self.request(mirror_url)
        if response is not None and "angels.wikidot.com" in response.text.casefold():
            return Page(mirror_url, origin, "aowiki_static_legacy_mirror", response.text)
        return self.wayback(origin) if origin.startswith("https://angels.wikidot.com/") else None


def source(page: Page) -> dict[str, object]:
    return {
        "origin_url": page.origin_url,
        "retrieval_url": page.retrieval_url,
        "source_kind": page.source_kind,
        "archive_timestamp": page.archive_timestamp,
        "source_html_sha256": page.digest,
    }


def parse_area(page: Page, slug: str, region: str) -> tuple[dict[str, object], set[str]]:
    soup = page.soup
    title = clean(soup.find("h1")) or slug.replace("-", " ").title()
    quest_rows: list[dict[str, object]] = []
    npcs: list[dict[str, object]] = []
    discovered: set[str] = set()
    raw_tables: list[dict[str, object]] = []

    for i, table in enumerate(soup.find_all("table"), start=1):
        headers, rows = table_rows(table)
        if not headers:
            continue
        raw_tables.append({"table_index": i, "headers_raw": headers, "row_count": len(rows)})
        normalized = [key(h) for h in headers]

        if "npc name" in normalized and "location" in normalized:
            ni = normalized.index("npc name")
            li = normalized.index("location")
            di = normalized.index("description") if "description" in normalized else None
            for cells, values in rows:
                if ni >= len(values):
                    continue
                npcs.append({
                    "name_raw": values[ni],
                    "location_raw": values[li] if li < len(values) else None,
                    "description_raw": values[di] if di is not None and di < len(values) else None,
                    "links": links(cells[ni] if ni < len(cells) else None),
                })

        qidx = next((j for j, h in enumerate(normalized) if h in {"quest name", "quest"}), None)
        if qidx is not None:
            for row_no, (cells, values) in enumerate(rows, start=1):
                if qidx >= len(values) or not values[qidx] or values[qidx] in {"---", "-"}:
                    continue
                row_links = []
                for cell in cells:
                    row_links.extend(links(cell))
                qlinks = [x for x in row_links if quest_filename(x["url"])]
                for item in qlinks:
                    filename = quest_filename(item["url"])
                    if filename:
                        discovered.add(filename)
                quest_rows.append({
                    "row_id": f"{slug}__t{i:03d}r{row_no:04d}",
                    "name_raw": values[qidx],
                    "row_raw": {headers[j]: values[j] if j < len(values) else "" for j in range(len(headers))},
                    "quest_links": qlinks,
                })

    # Capture quest links outside the table too, especially Important NPC descriptions.
    for a in soup.find_all("a", href=True):
        filename = quest_filename(urljoin(MIRROR, a["href"]))
        if filename:
            discovered.add(filename)

    return ({
        "schema_version": 1,
        "dataset": "quest_source_research",
        "area": {"id": slug, "name_raw": title, "region": region},
        "source": source(page),
        "npcs": npcs,
        "quest_rows": quest_rows,
        "source_tables": raw_tables,
        "discovered_quest_pages": sorted(discovered),
    }, discovered)


def section_text(soup: BeautifulSoup, heading_text: str) -> str | None:
    target = key(heading_text)
    heading = next((h for h in soup.find_all(["h2", "h3"]) if key(clean(h)) == target), None)
    if heading is None:
        return None
    bits: list[str] = []
    for node in heading.next_siblings:
        if isinstance(node, Tag) and node.name in {"h2", "h3"}:
            break
        if isinstance(node, Tag):
            text = clean(node)
            if text:
                bits.append(text)
    return "\n".join(bits) or None


def parse_quest(page: Page, filename: str) -> tuple[dict[str, object], set[str]]:
    soup = page.soup
    title = clean(soup.find("h1")) or filename
    breadcrumbs = []
    # Wikidot mirror breadcrumb text is useful for area attribution even when no explicit field exists.
    for node in soup.find_all(string=re.compile(r"Home\s*»", re.I)):
        text = " ".join(str(node).split())
        if text:
            breadcrumbs.append(text)

    tables: list[dict[str, object]] = []
    flat_fields: dict[str, str] = {}
    for i, table in enumerate(soup.find_all("table"), start=1):
        headers, rows = table_rows(table)
        table_payload = {"table_index": i, "headers_raw": headers, "rows_raw": []}
        for cells, values in rows:
            table_payload["rows_raw"].append({headers[j] if j < len(headers) else str(j): values[j] for j in range(len(values))})
        tables.append(table_payload)
        # Two-column field/value rows are useful for generic quest metadata.
        for tr in table.find_all("tr"):
            cells = tr.find_all(["th", "td"], recursive=False)
            if len(cells) == 2:
                k, v = clean(cells[0]), clean(cells[1])
                if k and v and len(k) < 100:
                    flat_fields.setdefault(k, v)

    discovered: set[str] = set()
    page_links = []
    for a in soup.find_all("a", href=True):
        url = urljoin(MIRROR, a["href"])
        page_links.append({"text_raw": clean(a), "url": url})
        qf = quest_filename(url)
        if qf and qf != filename:
            discovered.add(qf)

    # Preserve numbered Step headings and their text independently for walkthrough generation.
    steps: list[dict[str, object]] = []
    for heading in soup.find_all(["h2", "h3", "h4"]):
        label = clean(heading)
        if not re.match(r"^(?:step\s*\d+|first step|second step|third step|last step)$", label, re.I):
            continue
        bits: list[str] = []
        for node in heading.next_siblings:
            if isinstance(node, Tag) and node.name in {"h2", "h3", "h4"}:
                break
            if isinstance(node, Tag):
                text = clean(node)
                if text:
                    bits.append(text)
        steps.append({"label_raw": label, "text_raw": "\n".join(bits)})

    # Fallback: some walkthroughs have a Walkthrough heading and prose without Step headings.
    walkthrough_raw = section_text(soup, "Walkthrough")

    # Infer target area only as an additive convenience. Preserve breadcrumb/page text regardless.
    full_text = clean(soup)
    matched_areas = []
    for region, area_slugs in REGIONS.items():
        for slug in area_slugs:
            display = slug.replace("-", " ")
            if display.casefold() in full_text.casefold():
                matched_areas.append({"region": region, "area_id": slug})

    return ({
        "schema_version": 1,
        "dataset": "quest_walkthrough_source",
        "page_id": filename.removesuffix(".html"),
        "title_raw": title,
        "source": source(page),
        "breadcrumbs_raw": breadcrumbs,
        "summary_raw": section_text(soup, "Summary"),
        "information_raw": section_text(soup, "Information"),
        "requirements_raw": section_text(soup, "Requirements"),
        "reward_raw": section_text(soup, "Reward"),
        "walkthrough_raw": walkthrough_raw,
        "walkthrough_steps": steps,
        "fields_raw": flat_fields,
        "source_tables": tables,
        "links": page_links,
        "matched_target_areas": matched_areas,
        "verification": {"client_confirmation": "pending"},
    }, discovered)


def parse_special_for_quest_links(page: Page) -> set[str]:
    found: set[str] = set()
    for a in page.soup.find_all("a", href=True):
        qf = quest_filename(urljoin(MIRROR, a["href"]))
        if qf:
            found.add(qf)
    return found


def main() -> int:
    retriever = Retriever()
    AREA_OUT.mkdir(parents=True, exist_ok=True)
    QUEST_OUT.mkdir(parents=True, exist_ok=True)

    area_counts: dict[str, dict[str, int]] = {}
    discovered: set[str] = set()
    missing_areas: list[dict[str, str]] = []

    for region, slugs in REGIONS.items():
        captured = rows = 0
        for slug in slugs:
            page = retriever.area(slug)
            if page is None:
                missing_areas.append({"region": region, "area_id": slug})
                continue
            doc, qpages = parse_area(page, slug, region)
            dump(AREA_OUT / f"{slug}.yaml", doc)
            discovered.update(qpages)
            captured += 1
            rows += len(doc.get("quest_rows", []))
        area_counts[region] = {"expected_areas": len(slugs), "captured_areas": captured, "area_quest_rows": rows}

    # Seed cross-area quest directories that matter to the requested walkthrough set.
    special_seed_status = []
    for filename in SPECIAL_SEEDS:
        page = retriever.named_page(filename)
        if page is None:
            special_seed_status.append({"filename": filename, "status": "unavailable"})
            continue
        qpages = parse_special_for_quest_links(page)
        discovered.update(qpages)
        special_seed_status.append({"filename": filename, "status": "captured", "quest_links": len(qpages)})

    # Recursively follow quest-chain links. Pages are kept when they mention at least one target
    # area, or when they were directly discovered by a target area/special directory. This lets
    # previous/next chain pages fill gaps without turning the crawl into the entire wiki.
    direct = set(discovered)
    queue = deque(sorted(discovered))
    seen: set[str] = set()
    captured_quests = 0
    kept_by_region: dict[str, int] = {k: 0 for k in REGIONS}
    no_target_context: list[str] = []

    while queue:
        filename = queue.popleft()
        if filename in seen:
            continue
        seen.add(filename)
        page = retriever.named_page(filename)
        if page is None:
            continue
        doc, more = parse_quest(page, filename)
        matches = doc.get("matched_target_areas", []) or []
        keep = filename in direct or bool(matches)
        if not keep:
            no_target_context.append(filename)
            continue
        dump(QUEST_OUT / f"{filename.removesuffix('.html')}.yaml", doc)
        captured_quests += 1
        match_regions = {x.get("region") for x in matches if isinstance(x, dict)}
        for region in match_regions:
            if region in kept_by_region:
                kept_by_region[region] += 1
        for qf in more:
            if qf not in seen:
                queue.append(qf)

    report = {
        "schema_version": 1,
        "dataset": "walkthrough_intake_coverage",
        "scope": list(REGIONS),
        "area_counts": area_counts,
        "special_seeds": special_seed_status,
        "quest_pages": {
            "directly_discovered": len(direct),
            "visited": len(seen),
            "captured": captured_quests,
            "captured_pages_mentioning_region": kept_by_region,
            "visited_but_outside_target_context": len(no_target_context),
        },
        "missing_areas": missing_areas,
        "retrieval_errors": retriever.errors,
        "notes": [
            "Area quest rows and dedicated quest pages are separate evidence layers; neither silently overwrites the other.",
            "Dedicated quest pages preserve full source tables plus walkthrough prose and numbered steps when present.",
            "Missing pages remain explicit gaps and should be pursued with search/alternate archives instead of guessed.",
            "All imported web evidence remains pending AOmega target-client confirmation.",
        ],
    }
    dump(REPORT, report)
    print(yaml.safe_dump({"area_counts": area_counts, "quest_pages": report["quest_pages"], "missing_areas": len(missing_areas), "retrieval_errors": len(retriever.errors)}, sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
