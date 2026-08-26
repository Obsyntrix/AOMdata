#!/usr/bin/env python3
"""Recover historical Angels Wiki monster pages from Internet Archive snapshots.

The static aowiki.uk bestiary/area pages still preserve legacy angels.wikidot.com
content, but their monster links now resolve to a separate 2026 client-derived database.
This harvester therefore recovers archived copies of the original ``monster:*`` pages
and records archive retrieval provenance separately from the historical origin URL.

Recovery is resumable. Existing historical monster-page YAML files are not refetched
unless AOMDATA_WAYBACK_REFRESH=1 is explicitly set.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlparse
import os
import re
import sys
import time

import requests
import yaml
from bs4 import BeautifulSoup

import crawl_legacy_bestiary as legacy
import parse_historical_monster_page as historical_parser

ROOT = Path(__file__).resolve().parents[1]
BESTIARY = ROOT / "data" / "bestiary"
SUMMARY_DIR = BESTIARY / "site" / "summary"
AREA_DIR = BESTIARY / "site" / "areas"
MONSTER_DIR = BESTIARY / "site" / "monster-pages"
REPORT = BESTIARY / "wayback-monster-report.yaml"

AVAILABLE_URL = "https://archive.org/wayback/available"
USER_AGENT = "AOmega-preservation-bestiary/1.0 (+https://github.com/Obsyntrix/AOMdata)"
TIMEOUT = int(os.getenv("AOMDATA_WAYBACK_TIMEOUT", "45"))
DELAY = float(os.getenv("AOMDATA_WAYBACK_DELAY", "0.25"))
BATCH = int(os.getenv("AOMDATA_WAYBACK_BATCH", "0"))
REFRESH = os.getenv("AOMDATA_WAYBACK_REFRESH", "0") in {"1", "true", "True"}
MAX_RETRIES = int(os.getenv("AOMDATA_WAYBACK_RETRIES", "4"))
LOOKUP_WORKERS = int(os.getenv("AOMDATA_WAYBACK_LOOKUP_WORKERS", "4"))


def load_yaml(path: Path) -> dict:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}
    return value if isinstance(value, dict) else {}


def add_monster_slug(slugs: set[str], url: object) -> None:
    if not url:
        return
    name = Path(urlparse(str(url)).path).name
    if name.startswith("monster_") and name.endswith(".html"):
        slugs.add(name.removeprefix("monster_").removesuffix(".html").casefold())


def expected_slugs() -> set[str]:
    """Collect every monster detail page discovered by summary or preserved area pages."""
    slugs: set[str] = set()
    for path in sorted(SUMMARY_DIR.glob("*.yaml")):
        doc = load_yaml(path)
        for url in doc.get("discovered_monster_urls", []) or []:
            add_monster_slug(slugs, url)

    # Some monster links occur only on area pages, including Zone Drops rows. Those are
    # preservation targets too and must not disappear merely because a summary omitted them.
    for path in sorted(AREA_DIR.glob("*.yaml")):
        doc = load_yaml(path)
        for monster in doc.get("monsters", []) or []:
            if isinstance(monster, dict):
                add_monster_slug(slugs, monster.get("monster_detail_retrieval_url"))
        for drop in doc.get("drops", []) or []:
            if not isinstance(drop, dict):
                continue
            for link in drop.get("monster_links", []) or []:
                if isinstance(link, dict):
                    add_monster_slug(slugs, link.get("retrieval_url"))
    return slugs


def raw_wayback_url(snapshot_url: str) -> str:
    """Convert a normal Wayback snapshot URL to the raw id_ representation."""
    match = re.match(r"https?://web\.archive\.org/web/(\d+)(?:[a-z_]+)?/(.+)", snapshot_url)
    if not match:
        return snapshot_url.replace("http://web.archive.org/", "https://web.archive.org/", 1)
    return f"https://web.archive.org/web/{match.group(1)}id_/{match.group(2)}"


def lookup_snapshot(slug: str) -> tuple[str, dict[str, str] | None, list[dict[str, object]]]:
    """Find the closest archived original page, trying historical https and http forms."""
    errors: list[dict[str, object]] = []
    headers = {"User-Agent": USER_AGENT}
    candidates = [
        f"https://angels.wikidot.com/monster:{slug}",
        f"http://angels.wikidot.com/monster:{slug}",
    ]
    for origin in candidates:
        try:
            response = requests.get(AVAILABLE_URL, params={"url": origin}, headers=headers, timeout=TIMEOUT)
        except Exception as exc:  # noqa: BLE001
            errors.append({"slug": slug, "stage": "availability", "origin_url": origin, "error": type(exc).__name__, "detail": str(exc)})
            continue
        if response.status_code != 200:
            errors.append({"slug": slug, "stage": "availability", "origin_url": origin, "status": response.status_code})
            continue
        try:
            data = response.json()
        except ValueError as exc:
            errors.append({"slug": slug, "stage": "availability", "origin_url": origin, "error": "invalid_json", "detail": str(exc)})
            continue
        closest = ((data.get("archived_snapshots") or {}).get("closest") or {}) if isinstance(data, dict) else {}
        if not isinstance(closest, dict) or not closest.get("available"):
            continue
        if str(closest.get("status") or "") != "200":
            continue
        snapshot_url = str(closest.get("url") or "")
        timestamp = str(closest.get("timestamp") or "")
        if not snapshot_url or not timestamp:
            continue
        return slug, {
            "origin_url": origin,
            "timestamp": timestamp,
            "snapshot_url": snapshot_url.replace("http://web.archive.org/", "https://web.archive.org/", 1),
            "raw_url": raw_wayback_url(snapshot_url),
        }, errors
    return slug, None, errors


def valid_legacy_html(text: str) -> bool:
    lower = text.casefold()
    if "wayback machine" in lower and "this url has been excluded" in lower:
        return False
    return (
        "angels online wiki" in lower
        or "home » bestiary" in lower
        or "item drop chance" in lower
        or ("statistics" in lower and "monster" in lower)
    )


def fetch_snapshot(session: requests.Session, slug: str, capture: dict[str, str]) -> tuple[legacy.Fetched | None, str | None, list[dict[str, object]]]:
    errors: list[dict[str, object]] = []
    urls = [capture["raw_url"]]
    if capture["snapshot_url"] != capture["raw_url"]:
        urls.append(capture["snapshot_url"])

    for retrieval_url in urls:
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = session.get(retrieval_url, timeout=TIMEOUT)
            except Exception as exc:  # noqa: BLE001
                errors.append({"slug": slug, "stage": "snapshot", "retrieval_url": retrieval_url, "attempt": attempt + 1, "error": type(exc).__name__, "detail": str(exc)})
                break

            if response.status_code == 200:
                text = response.text
                if not valid_legacy_html(text):
                    errors.append({"slug": slug, "stage": "snapshot", "retrieval_url": retrieval_url, "status": 200, "error": "legacy_content_marker_missing"})
                    break
                synthetic_url = f"https://www.aowiki.uk/pages/monster_{slug}.html"
                return legacy.Fetched(
                    url=synthetic_url,
                    text=text,
                    digest=sha256(response.content).hexdigest(),
                    soup=BeautifulSoup(text, "html.parser"),
                ), retrieval_url, errors

            if response.status_code in {429, 500, 502, 503, 504} and attempt < MAX_RETRIES:
                retry_after = response.headers.get("Retry-After")
                try:
                    pause = float(retry_after) if retry_after else min(30.0, 2.0 ** (attempt + 1))
                except ValueError:
                    pause = min(30.0, 2.0 ** (attempt + 1))
                time.sleep(pause)
                continue

            errors.append({"slug": slug, "stage": "snapshot", "retrieval_url": retrieval_url, "status": response.status_code, "attempt": attempt + 1})
            break
    return None, None, errors


def main() -> int:
    MONSTER_DIR.mkdir(parents=True, exist_ok=True)
    expected = expected_slugs()
    existing = {p.stem.casefold() for p in MONSTER_DIR.glob("*.yaml")}
    todo = sorted(expected if REFRESH else expected - existing)
    if BATCH > 0:
        todo = todo[:BATCH]

    errors: list[dict[str, object]] = []
    available: dict[str, dict[str, str]] = {}

    # The bulk CDX endpoint is unreliable for Wikidot colon paths. The availability API
    # resolves each exact historical URL instead. A small worker pool keeps this bounded.
    with ThreadPoolExecutor(max_workers=max(1, LOOKUP_WORKERS)) as pool:
        futures = {pool.submit(lookup_snapshot, slug): slug for slug in todo}
        for future in as_completed(futures):
            slug = futures[future]
            try:
                result_slug, capture, lookup_errors = future.result()
            except Exception as exc:  # noqa: BLE001
                errors.append({"slug": slug, "stage": "availability_worker", "error": type(exc).__name__, "detail": str(exc)})
                continue
            errors.extend(lookup_errors)
            if capture is not None:
                available[result_slug] = capture

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    captured_this_run: list[str] = []

    for slug in todo:
        capture = available.get(slug)
        if capture is None:
            continue
        fetched, retrieval_url, fetch_errors = fetch_snapshot(session, slug, capture)
        errors.extend(fetch_errors)
        if fetched is None or retrieval_url is None:
            continue

        doc = historical_parser.parse_monster_page(fetched, slug)
        page = doc.get("monster_page", {})
        page["source"] = {
            "origin_url": f"https://angels.wikidot.com/monster:{slug}",
            "retrieval_url": retrieval_url,
            "source_kind": "angels_wiki_monster_wayback",
            "archive_timestamp": capture.get("timestamp"),
            "archive_snapshot_url": capture.get("snapshot_url"),
            "archive_original_lookup_url": capture.get("origin_url"),
            "source_html_sha256": fetched.digest,
        }
        page["verification"] = {"client_confirmation": "pending"}
        doc["monster_page"] = page
        legacy.dump_yaml(MONSTER_DIR / f"{slug}.yaml", doc)
        captured_this_run.append(slug)
        if DELAY:
            time.sleep(DELAY)

    now_existing = {p.stem.casefold() for p in MONSTER_DIR.glob("*.yaml")}
    unavailable = sorted(set(todo) - set(available))
    failed_fetch = sorted(set(available) - set(captured_this_run) - existing)
    payload = {
        "dataset": "legacy_angels_wiki_monster_pages",
        "recovery_source": "Internet Archive Wayback Machine",
        "policy": "Archived original monster:* pages are separate historical retrieval evidence; aowiki.uk 2026 client-derived monster pages are not imported into this layer.",
        "counts": {
            "expected_from_legacy_bestiary": len(expected),
            "existing_before_run": len(existing),
            "looked_up_this_run": len(todo),
            "archive_snapshots_found_this_run": len(available),
            "captured_this_run": len(captured_this_run),
            "captured_total": len(now_existing & expected),
            "remaining_expected": len(expected - now_existing),
            "no_snapshot_found_this_run": len(unavailable),
            "snapshot_fetch_failed_this_run": len(failed_fetch),
        },
        "no_snapshot_found_this_run": unavailable,
        "snapshot_fetch_failed_this_run": failed_fetch,
        "errors": errors,
        "notes": [
            "Internet Archive availability lookups are performed against exact original Wikidot monster URLs.",
            "Raw id_ snapshots are preferred; normal archived snapshots are a fallback if the raw representation is unavailable.",
            "Every rendered historical table row is preserved; recognizable grouped stat fields are additionally exposed in fields_raw.",
            "Raw historical fields and displayed drop percentages remain authoritative preservation values.",
            "No 2026 aowiki.uk monster database values are merged into these historical observations.",
        ],
    }
    legacy.dump_yaml(REPORT, payload)
    print(yaml.safe_dump(payload["counts"], sort_keys=False), end="")
    if errors:
        print(f"Recovery issues recorded: {len(errors)} (see {REPORT.relative_to(ROOT)})")
    # Partial archive coverage is a preservation fact, not a process crash. Audit/reporting
    # must still run and commit the evidence gathered in this pass.
    return 0


if __name__ == "__main__":
    sys.exit(main())
