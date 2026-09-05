#!/usr/bin/env python3
"""Capture legacy Angels Wiki quest evidence for AOmega preservation.

This crawler targets the six quest regions currently required for full walkthroughs:
Aurora, Breeze, Steel/Iron, Dark/Shadow, Dungeon (Demon Kitchen), and Atlantis.

The static aowiki.uk `/pages/` mirror explicitly identifies these pages as original
angels.wikidot.com content.  We preserve rendered source observations and provenance;
normalization into canonical walkthrough records is a separate step.

Outputs are resumable and deterministic. Existing successful snapshots are reused unless
AOMDATA_QUEST_REFRESH=1 is set.
"""

from __future__ import annotations

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
QUEST_ROOT = ROOT / "data" / "quests"
SITE_ROOT = QUEST_ROOT / "site"
AREA_DIR = SITE_ROOT / "areas"
QUEST_DIR = SITE_ROOT / "quest-pages"
REPORT = QUEST_ROOT / "crawl-report.yaml"

MIRROR = "https://www.aowiki.uk/pages/"
LEGACY_ORIGIN = "https://angels.wikidot.com/"
USER_AGENT = "AOmega-preservation-quests/1.0 (+https://github.com/Obsyntrix/AOMdata)"
TIMEOUT = int(os.getenv("AOMDATA_QUEST_TIMEOUT", "40"))
DELAY = float(os.getenv("AOMDATA_QUEST_DELAY", "0.20"))
RETRIES = int(os.getenv("AOMDATA_QUEST_RETRIES", "4"))
REFRESH = os.getenv("AOMDATA_QUEST_REFRESH", "0") in {"1", "true", "True"}

REGIONS: dict[str, list[str]] = {
    "aurora": [
        "aurora-city", "spike-farm", "sunflower-plain", "dawn-harbor", "riprap-coast",
        "cherry-village", "crashing-hillock", "thunder-ruins", "thorn-wasteland",
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
        "sad-abyss", "fiery-path", "lava-cave", "flaming-door", "underground-square",
        "hell-palace", "magic-kitchen-path", "gulp-room",
    ],
    "atlantis": [
        "puqi-village", "golden-beach", "palm-base", "shining-coast",
        "colorful-coral-reefs", "blue-sea", "wave-harbor", "blue-ocean",
        "sunken-ruins", "lost-region", "horrible-lost-region", "dream-ocean",
        "raging-reefs", "quiet-ocean", "coral-vale", "evil-ship",
    ],
}


def dump_yaml(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=180), encoding="utf-8")


def clean(node: Tag | None) -> str:
    if node is None:
        return ""
    return " ".join(node.stripped_strings)


def slug_from_static(url: str, prefix: str) -> str:
    name = Path(urlparse(url).path).name
    return name.removeprefix(prefix).removesuffix(".html")


def static_area_url(slug: str) -> str:
    return urljoin(MIRROR, f"area_{slug}.html")


def legacy_area_url(slug: str) -> str:
    return f"{LEGACY_ORIGIN}area:{slug}"


def legacy_quest_url(slug: str) -> str:
    return f"{LEGACY_ORIGIN}quest:{slug}"


def is_quest_link(href: str) -> bool:
    name = Path(urlparse(href).path).name
    return name.startswith("quest_") and name.endswith(".html")


def extract_tables(soup: BeautifulSoup) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for i, table in enumerate(soup.find_all("table"), start=1):
        rows: list[list[str]] = []
        for tr in table.find_all("tr"):
            cells = tr.find_all(["th", "td"], recursive=False)
            if cells:
                rows.append([clean(c) for c in cells])
        if rows:
            out.append({"table_index": i, "rows_raw": rows})
    return out


def extract_headings_and_text(soup: BeautifulSoup) -> list[dict[str, object]]:
    """Preserve readable page flow without storing giant HTML blobs."""
    out: list[dict[str, object]] = []
    current = "page"
    bucket: list[str] = []

    def flush() -> None:
        nonlocal bucket
        text = "\n".join(x for x in bucket if x).strip()
        if text:
            out.append({"section": current, "text_raw": text})
        bucket = []

    root = soup.find("main") or soup.find(id="main-content") or soup.body or soup
    for node in root.find_all(["h1", "h2", "h3", "h4", "p", "li"], recursive=True):
        text = clean(node)
        if not text:
            continue
        if node.name in {"h1", "h2", "h3", "h4"}:
            flush()
            current = text
        else:
            bucket.append(text)
    flush()
    return out


def extract_quest_links(soup: BeautifulSoup) -> list[dict[str, str]]:
    found: dict[str, dict[str, str]] = {}
    for a in soup.find_all("a", href=True):
        href = urljoin(MIRROR, a["href"])
        if not is_quest_link(href):
            continue
        slug = slug_from_static(href, "quest_")
        found[slug] = {"slug": slug, "name_raw": clean(a), "retrieval_url": href, "origin_url": legacy_quest_url(slug)}
    return [found[k] for k in sorted(found)]


def request(session: requests.Session, url: str) -> tuple[requests.Response | None, list[dict[str, object]]]:
    errors: list[dict[str, object]] = []
    for attempt in range(RETRIES + 1):
        try:
            r = session.get(url, timeout=TIMEOUT)
        except Exception as exc:  # noqa: BLE001
            errors.append({"url": url, "attempt": attempt + 1, "error": type(exc).__name__, "detail": str(exc)})
            if attempt < RETRIES:
                time.sleep(min(20.0, 2 ** attempt))
                continue
            return None, errors
        if r.status_code == 200:
            return r, errors
        errors.append({"url": url, "attempt": attempt + 1, "status": r.status_code})
        if r.status_code in {429, 500, 502, 503, 504} and attempt < RETRIES:
            retry_after = r.headers.get("Retry-After")
            try:
                pause = float(retry_after) if retry_after else min(30.0, 2 ** (attempt + 1))
            except ValueError:
                pause = min(30.0, 2 ** (attempt + 1))
            time.sleep(pause)
            continue
        return None, errors
    return None, errors


def source_meta(*, origin_url: str, retrieval_url: str, content: bytes, source_kind: str) -> dict[str, object]:
    return {
        "origin_url": origin_url,
        "retrieval_url": retrieval_url,
        "source_kind": source_kind,
        "mirror_declares_original_angels_wikidot_content": True,
        "source_html_sha256": sha256(content).hexdigest(),
    }


def load_existing(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return None
    return value if isinstance(value, dict) else None


def capture_area(session: requests.Session, region: str, slug: str) -> tuple[dict[str, object] | None, list[dict[str, object]]]:
    path = AREA_DIR / region / f"{slug}.yaml"
    if not REFRESH:
        existing = load_existing(path)
        if existing and existing.get("capture", {}).get("status") == "captured":
            return existing, []
    url = static_area_url(slug)
    r, errors = request(session, url)
    if r is None:
        return None, errors
    soup = BeautifulSoup(r.text, "html.parser")
    title = clean(soup.find("h1")) or slug.replace("-", " ").title()
    doc = {
        "schema_version": 1,
        "dataset": "quest_source_snapshot",
        "page_kind": "area",
        "region": region,
        "area": {"id": slug, "name_raw": title},
        "source": source_meta(origin_url=legacy_area_url(slug), retrieval_url=url, content=r.content, source_kind="angels_wiki_area_mirror"),
        "quest_links": extract_quest_links(soup),
        "tables_raw": extract_tables(soup),
        "sections_raw": extract_headings_and_text(soup),
        "capture": {"status": "captured"},
        "verification": {"client_confirmation": "pending"},
    }
    dump_yaml(path, doc)
    if DELAY:
        time.sleep(DELAY)
    return doc, errors


def capture_quest(session: requests.Session, link: dict[str, str], regions: list[str], areas: list[str]) -> tuple[dict[str, object] | None, list[dict[str, object]]]:
    slug = link["slug"]
    path = QUEST_DIR / f"{slug}.yaml"
    if not REFRESH:
        existing = load_existing(path)
        if existing and existing.get("capture", {}).get("status") == "captured":
            # Merge discovery context without refetching source.
            existing_regions = set(existing.get("discovered_from_regions", []) or [])
            existing_areas = set(existing.get("discovered_from_areas", []) or [])
            changed = False
            for region in regions:
                if region not in existing_regions:
                    existing_regions.add(region); changed = True
            for area in areas:
                if area not in existing_areas:
                    existing_areas.add(area); changed = True
            if changed:
                existing["discovered_from_regions"] = sorted(existing_regions)
                existing["discovered_from_areas"] = sorted(existing_areas)
                dump_yaml(path, existing)
            return existing, []

    r, errors = request(session, link["retrieval_url"])
    if r is None:
        return None, errors
    soup = BeautifulSoup(r.text, "html.parser")
    title = clean(soup.find("h1")) or link.get("name_raw") or slug.replace("-", " ").title()
    doc = {
        "schema_version": 1,
        "dataset": "quest_source_snapshot",
        "page_kind": "quest",
        "id": slug,
        "name_raw": title,
        "discovered_from_regions": sorted(set(regions)),
        "discovered_from_areas": sorted(set(areas)),
        "source": source_meta(origin_url=legacy_quest_url(slug), retrieval_url=link["retrieval_url"], content=r.content, source_kind="angels_wiki_quest_mirror"),
        "tables_raw": extract_tables(soup),
        "sections_raw": extract_headings_and_text(soup),
        "capture": {"status": "captured"},
        "verification": {"client_confirmation": "pending"},
    }
    dump_yaml(path, doc)
    if DELAY:
        time.sleep(DELAY)
    return doc, errors


def main() -> int:
    AREA_DIR.mkdir(parents=True, exist_ok=True)
    QUEST_DIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    errors: list[dict[str, object]] = []
    area_docs: list[dict[str, object]] = []
    quest_discovery: dict[str, dict[str, object]] = {}
    missing_areas: list[dict[str, str]] = []

    for region, slugs in REGIONS.items():
        for slug in slugs:
            doc, errs = capture_area(session, region, slug)
            errors.extend(errs)
            if doc is None:
                missing_areas.append({"region": region, "area": slug})
                continue
            area_docs.append(doc)
            for link in doc.get("quest_links", []) or []:
                if not isinstance(link, dict) or not link.get("slug"):
                    continue
                qslug = str(link["slug"])
                node = quest_discovery.setdefault(qslug, {"link": link, "regions": set(), "areas": set()})
                node["regions"].add(region)
                node["areas"].add(slug)

    captured_quests = 0
    missing_quests: list[str] = []
    for qslug in sorted(quest_discovery):
        node = quest_discovery[qslug]
        doc, errs = capture_quest(session, node["link"], sorted(node["regions"]), sorted(node["areas"]))
        errors.extend(errs)
        if doc is None:
            missing_quests.append(qslug)
        else:
            captured_quests += 1

    counts_by_region = {}
    for region, slugs in REGIONS.items():
        captured = sum(1 for slug in slugs if (AREA_DIR / region / f"{slug}.yaml").exists())
        quest_slugs = set()
        for slug in slugs:
            doc = load_existing(AREA_DIR / region / f"{slug}.yaml")
            if not doc:
                continue
            quest_slugs.update(str(x.get("slug")) for x in doc.get("quest_links", []) or [] if isinstance(x, dict) and x.get("slug"))
        captured_q = sum(1 for q in quest_slugs if (QUEST_DIR / f"{q}.yaml").exists())
        counts_by_region[region] = {
            "expected_area_pages": len(slugs),
            "captured_area_pages": captured,
            "discovered_dedicated_quest_pages": len(quest_slugs),
            "captured_dedicated_quest_pages": captured_q,
        }

    report = {
        "schema_version": 1,
        "dataset": "legacy_angels_wiki_quest_evidence",
        "target_regions": list(REGIONS),
        "counts": {
            "target_area_pages": sum(len(v) for v in REGIONS.values()),
            "captured_area_pages": len(area_docs),
            "discovered_dedicated_quest_pages": len(quest_discovery),
            "captured_dedicated_quest_pages": captured_quests,
            "missing_area_pages": len(missing_areas),
            "missing_quest_pages": len(missing_quests),
        },
        "by_region": counts_by_region,
        "missing_areas": missing_areas,
        "missing_quests": missing_quests,
        "errors": errors,
        "notes": [
            "Area pages preserve quest-table observations even when no dedicated quest page exists.",
            "Dedicated quest pages are captured whenever linked from a target area page.",
            "Missing pages remain explicit evidence gaps; they are not interpreted as no quest.",
            "Source strings are historical observations pending AOmega target-client verification.",
        ],
    }
    dump_yaml(REPORT, report)
    print(yaml.safe_dump(report["counts"], sort_keys=False), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
