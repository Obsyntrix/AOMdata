#!/usr/bin/env python3
"""Recover historical Angels Wiki monster pages from Internet Archive snapshots.

The aowiki.uk static bestiary/area pages still preserve legacy angels.wikidot.com
content, but their monster links now resolve to a separate 2026 client-derived database.
This harvester therefore uses archived copies of the original ``monster:*`` pages and
records the archive snapshot separately from the historical origin URL.

The process is resumable: existing monster-page YAML files are never refetched unless
AOMDATA_WAYBACK_REFRESH=1 is explicitly set.
"""

from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
from pathlib import Path
from urllib.parse import unquote, urlparse
import os
import re
import sys
import time

import requests
import yaml
from bs4 import BeautifulSoup

import crawl_legacy_bestiary as legacy

ROOT = Path(__file__).resolve().parents[1]
BESTIARY = ROOT / "data" / "bestiary"
SUMMARY_DIR = BESTIARY / "site" / "summary"
MONSTER_DIR = BESTIARY / "site" / "monster-pages"
REPORT = BESTIARY / "wayback-monster-report.yaml"

CDX_URL = "https://web.archive.org/cdx/search/cdx"
WAYBACK_RAW = "https://web.archive.org/web/{timestamp}id_/{original}"
USER_AGENT = "AOmega-preservation-bestiary/1.0 (+https://github.com/Obsyntrix/AOMdata)"
TIMEOUT = int(os.getenv("AOMDATA_WAYBACK_TIMEOUT", "45"))
DELAY = float(os.getenv("AOMDATA_WAYBACK_DELAY", "0.35"))
BATCH = int(os.getenv("AOMDATA_WAYBACK_BATCH", "0"))
REFRESH = os.getenv("AOMDATA_WAYBACK_REFRESH", "0") in {"1", "true", "True"}
MAX_RETRIES = int(os.getenv("AOMDATA_WAYBACK_RETRIES", "4"))


def load_yaml(path: Path) -> dict:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 - corrupt preservation file must not kill discovery
        return {}
    return value if isinstance(value, dict) else {}


def expected_slugs() -> set[str]:
    slugs: set[str] = set()
    for path in sorted(SUMMARY_DIR.glob("*.yaml")):
        doc = load_yaml(path)
        for url in doc.get("discovered_monster_urls", []) or []:
            name = Path(urlparse(str(url)).path).name
            if name.startswith("monster_") and name.endswith(".html"):
                slugs.add(name.removeprefix("monster_").removesuffix(".html"))
    return slugs


def slug_from_original(original: str) -> str | None:
    path = unquote(urlparse(original).path).lstrip("/")
    if not path.casefold().startswith("monster:"):
        return None
    slug = path.split(":", 1)[1].split("/", 1)[0].strip()
    if not slug:
        return None
    return slug.casefold()


def cdx_inventory(session: requests.Session) -> tuple[dict[str, list[dict[str, str]]], list[dict[str, object]]]:
    params = {
        "url": "angels.wikidot.com/monster:",
        "matchType": "prefix",
        "output": "json",
        "fl": "timestamp,original,statuscode,mimetype,digest",
        "filter": ["statuscode:200", "mimetype:text/html"],
        "collapse": "digest",
    }
    errors: list[dict[str, object]] = []
    try:
        response = session.get(CDX_URL, params=params, timeout=TIMEOUT)
        response.raise_for_status()
        rows = response.json()
    except Exception as exc:  # noqa: BLE001
        return {}, [{"stage": "cdx_inventory", "error": type(exc).__name__, "detail": str(exc)}]

    if not isinstance(rows, list) or not rows:
        return {}, [{"stage": "cdx_inventory", "error": "empty_response"}]

    header = rows[0]
    if not isinstance(header, list):
        return {}, [{"stage": "cdx_inventory", "error": "invalid_header"}]
    by_slug: dict[str, list[dict[str, str]]] = defaultdict(list)
    for raw in rows[1:]:
        if not isinstance(raw, list) or len(raw) != len(header):
            continue
        row = {str(header[i]): str(raw[i]) for i in range(len(header))}
        slug = slug_from_original(row.get("original", ""))
        if slug:
            by_slug[slug].append(row)

    # Prefer the newest archived representation. If that snapshot cannot be fetched,
    # recovery falls back through older captures of the same page.
    for captures in by_slug.values():
        captures.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return dict(by_slug), errors


def fetch_snapshot(session: requests.Session, slug: str, captures: list[dict[str, str]]) -> tuple[legacy.Fetched | None, dict[str, str] | None, list[dict[str, object]]]:
    errors: list[dict[str, object]] = []
    for capture in captures:
        timestamp = capture.get("timestamp", "")
        original = capture.get("original", "")
        if not timestamp or not original:
            continue
        archive_url = WAYBACK_RAW.format(timestamp=timestamp, original=original)
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = session.get(archive_url, timeout=TIMEOUT)
            except Exception as exc:  # noqa: BLE001
                errors.append({"slug": slug, "retrieval_url": archive_url, "attempt": attempt + 1, "error": type(exc).__name__, "detail": str(exc)})
                break

            if response.status_code == 200:
                text = response.text
                lower = text.casefold()
                # Reject Wayback error shells, redirects, or unrelated responses.
                if "angels online wiki" not in lower and "home » bestiary" not in lower and "item drop chance" not in lower:
                    errors.append({"slug": slug, "retrieval_url": archive_url, "status": 200, "error": "legacy_content_marker_missing"})
                    break
                synthetic_url = f"https://www.aowiki.uk/pages/monster_{slug}.html"
                fetched = legacy.Fetched(
                    url=synthetic_url,
                    text=text,
                    digest=sha256(response.content).hexdigest(),
                    soup=BeautifulSoup(text, "html.parser"),
                )
                return fetched, {**capture, "retrieval_url": archive_url}, errors

            if response.status_code in {429, 500, 502, 503, 504} and attempt < MAX_RETRIES:
                retry_after = response.headers.get("Retry-After")
                try:
                    pause = float(retry_after) if retry_after else min(30.0, 2.0 ** (attempt + 1))
                except ValueError:
                    pause = min(30.0, 2.0 ** (attempt + 1))
                time.sleep(pause)
                continue

            errors.append({"slug": slug, "retrieval_url": archive_url, "status": response.status_code, "attempt": attempt + 1})
            break
        if DELAY:
            time.sleep(DELAY)
    return None, None, errors


def main() -> int:
    MONSTER_DIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    expected = expected_slugs()
    existing = {p.stem.casefold() for p in MONSTER_DIR.glob("*.yaml")}
    inventory, errors = cdx_inventory(session)
    archived_expected = expected & set(inventory)
    unavailable_in_cdx = sorted(expected - set(inventory))

    todo = sorted(expected if REFRESH else expected - existing)
    if BATCH > 0:
        todo = todo[:BATCH]

    captured_this_run: list[str] = []
    attempted = 0
    for slug in todo:
        captures = inventory.get(slug, [])
        if not captures:
            continue
        attempted += 1
        fetched, capture, fetch_errors = fetch_snapshot(session, slug, captures)
        errors.extend(fetch_errors)
        if fetched is None or capture is None:
            continue

        doc = legacy.parse_monster_page(fetched)
        page = doc.get("monster_page", {})
        page["source"] = {
            "origin_url": f"https://angels.wikidot.com/monster:{slug}",
            "retrieval_url": capture["retrieval_url"],
            "source_kind": "angels_wiki_monster_wayback",
            "archive_timestamp": capture.get("timestamp"),
            "archive_original_url": capture.get("original"),
            "archive_digest": capture.get("digest"),
            "source_html_sha256": fetched.digest,
        }
        page["verification"] = {"client_confirmation": "pending"}
        doc["monster_page"] = page
        legacy.dump_yaml(MONSTER_DIR / f"{slug}.yaml", doc)
        captured_this_run.append(slug)
        if DELAY:
            time.sleep(DELAY)

    now_existing = {p.stem.casefold() for p in MONSTER_DIR.glob("*.yaml")}
    payload = {
        "dataset": "legacy_angels_wiki_monster_pages",
        "recovery_source": "Internet Archive Wayback Machine",
        "policy": "Archived original monster:* pages are a separate retrieval provenance; aowiki.uk 2026 client-derived monster pages are not imported into the historical layer.",
        "counts": {
            "expected_from_legacy_bestiary": len(expected),
            "expected_with_wayback_inventory": len(archived_expected),
            "missing_from_wayback_inventory": len(unavailable_in_cdx),
            "existing_before_run": len(existing),
            "attempted_this_run": attempted,
            "captured_this_run": len(captured_this_run),
            "captured_total": len(now_existing & expected),
            "remaining_expected": len(expected - now_existing),
        },
        "missing_from_wayback_inventory": unavailable_in_cdx,
        "errors": errors,
        "notes": [
            "Newest archived capture is preferred; older captures are tried if retrieval fails.",
            "Raw historical page fields and displayed drop percentages are preserved verbatim by the shared legacy parser.",
            "No 2026 aowiki.uk monster database values are merged into these historical observations.",
        ],
    }
    legacy.dump_yaml(REPORT, payload)
    print(yaml.safe_dump(payload["counts"], sort_keys=False), end="")
    return 0 if inventory else 2


if __name__ == "__main__":
    sys.exit(main())
