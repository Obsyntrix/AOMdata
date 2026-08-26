#!/usr/bin/env python3
"""Recover missing historical Angels Wiki area pages from Internet Archive snapshots.

The static mirror is the preferred retrieval source when available. This tool only fills
area pages that are absent from ``data/bestiary/site/areas`` and keeps their original
``angels.wikidot.com/area:*`` provenance. Archived pages are especially important for
Zone Drops tables that cannot be reconstructed from summary bestiary rows alone.
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

ROOT = Path(__file__).resolve().parents[1]
BESTIARY = ROOT / "data" / "bestiary"
SUMMARY_DIR = BESTIARY / "site" / "summary"
AREA_DIR = BESTIARY / "site" / "areas"
REPORT = BESTIARY / "wayback-area-report.yaml"

AVAILABLE_URL = "https://archive.org/wayback/available"
USER_AGENT = "AOmega-preservation-bestiary/1.0 (+https://github.com/Obsyntrix/AOMdata)"
TIMEOUT = int(os.getenv("AOMDATA_WAYBACK_TIMEOUT", "45"))
DELAY = float(os.getenv("AOMDATA_WAYBACK_AREA_DELAY", "0.35"))
MAX_RETRIES = int(os.getenv("AOMDATA_WAYBACK_RETRIES", "4"))
LOOKUP_WORKERS = int(os.getenv("AOMDATA_WAYBACK_LOOKUP_WORKERS", "4"))
REFRESH = os.getenv("AOMDATA_WAYBACK_AREA_REFRESH", "0") in {"1", "true", "True"}


def load_yaml(path: Path) -> dict:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}
    return value if isinstance(value, dict) else {}


def expected_area_slugs() -> set[str]:
    slugs: set[str] = set()
    for path in sorted(SUMMARY_DIR.glob("*.yaml")):
        doc = load_yaml(path)
        for url in doc.get("discovered_area_urls", []) or []:
            name = Path(urlparse(str(url)).path).name
            if name.startswith("area_") and name.endswith(".html"):
                slugs.add(name.removeprefix("area_").removesuffix(".html").casefold())
    return slugs


def raw_wayback_url(snapshot_url: str) -> str:
    match = re.match(r"https?://web\.archive\.org/web/(\d+)(?:[a-z_]+)?/(.+)", snapshot_url)
    if not match:
        return snapshot_url.replace("http://web.archive.org/", "https://web.archive.org/", 1)
    return f"https://web.archive.org/web/{match.group(1)}id_/{match.group(2)}"


def lookup_snapshot(slug: str) -> tuple[str, dict[str, str] | None, list[dict[str, object]]]:
    errors: list[dict[str, object]] = []
    headers = {"User-Agent": USER_AGENT}
    for origin in (
        f"https://angels.wikidot.com/area:{slug}",
        f"http://angels.wikidot.com/area:{slug}",
    ):
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
        if not isinstance(closest, dict) or not closest.get("available") or str(closest.get("status") or "") != "200":
            continue
        snapshot_url = str(closest.get("url") or "")
        timestamp = str(closest.get("timestamp") or "")
        if not snapshot_url or not timestamp:
            continue
        snapshot_url = snapshot_url.replace("http://web.archive.org/", "https://web.archive.org/", 1)
        return slug, {
            "origin_url": origin,
            "timestamp": timestamp,
            "snapshot_url": snapshot_url,
            "raw_url": raw_wayback_url(snapshot_url),
        }, errors
    return slug, None, errors


def valid_area_html(text: str) -> bool:
    lower = text.casefold()
    if "wayback machine" in lower and "this url has been excluded" in lower:
        return False
    return "angels online wiki" in lower or "zone drops" in lower or "home » areas" in lower or "home » area" in lower


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
                if not valid_area_html(text):
                    errors.append({"slug": slug, "stage": "snapshot", "retrieval_url": retrieval_url, "status": 200, "error": "legacy_area_marker_missing"})
                    break
                synthetic_url = f"https://www.aowiki.uk/pages/area_{slug}.html"
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


def preserve_table_rows(fetched: legacy.Fetched) -> list[dict[str, object]]:
    tables: list[dict[str, object]] = []
    for table_index, table in enumerate(fetched.soup.find_all("table"), start=1):
        rows_raw: list[list[str]] = []
        for row in table.find_all("tr"):
            cells = row.find_all(["th", "td"], recursive=False)
            if cells:
                rows_raw.append([legacy.clean_text(cell) for cell in cells])
        tables.append({"table_index": table_index, "rows_raw": rows_raw, "row_count": len(rows_raw)})
    return tables


def main() -> int:
    AREA_DIR.mkdir(parents=True, exist_ok=True)
    expected = expected_area_slugs()
    existing = {p.stem.casefold() for p in AREA_DIR.glob("*.yaml")}
    todo = sorted(expected if REFRESH else expected - existing)

    errors: list[dict[str, object]] = []
    available: dict[str, dict[str, str]] = {}
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
    captured: list[str] = []
    for slug in todo:
        capture = available.get(slug)
        if capture is None:
            continue
        fetched, retrieval_url, fetch_errors = fetch_snapshot(session, slug, capture)
        errors.extend(fetch_errors)
        if fetched is None or retrieval_url is None:
            continue
        doc = legacy.parse_area_page(fetched)
        area = doc.get("area", {})
        area["source"] = {
            "origin_url": f"https://angels.wikidot.com/area:{slug}",
            "retrieval_url": retrieval_url,
            "source_kind": "angels_wiki_area_wayback",
            "archive_timestamp": capture.get("timestamp"),
            "archive_snapshot_url": capture.get("snapshot_url"),
            "archive_original_lookup_url": capture.get("origin_url"),
            "source_html_sha256": fetched.digest,
        }
        area["verification"] = {"client_confirmation": "pending"}
        doc["area"] = area
        doc["source_tables_raw"] = preserve_table_rows(fetched)
        legacy.dump_yaml(AREA_DIR / f"{slug}.yaml", doc)
        captured.append(slug)
        if DELAY:
            time.sleep(DELAY)

    now_existing = {p.stem.casefold() for p in AREA_DIR.glob("*.yaml")}
    missing_snapshot = sorted(set(todo) - set(available))
    failed_fetch = sorted(set(available) - set(captured) - existing)
    payload = {
        "dataset": "legacy_angels_wiki_area_pages",
        "recovery_source": "Internet Archive Wayback Machine",
        "policy": "Use static historical mirror first; use archived original area:* pages only for missing mirror snapshots, preserving separate retrieval provenance.",
        "counts": {
            "expected_from_legacy_bestiary": len(expected),
            "existing_before_run": len(existing),
            "looked_up_this_run": len(todo),
            "archive_snapshots_found_this_run": len(available),
            "captured_this_run": len(captured),
            "captured_total": len(now_existing & expected),
            "remaining_expected": len(expected - now_existing),
            "no_snapshot_found_this_run": len(missing_snapshot),
            "snapshot_fetch_failed_this_run": len(failed_fetch),
        },
        "no_snapshot_found_this_run": missing_snapshot,
        "snapshot_fetch_failed_this_run": failed_fetch,
        "errors": errors,
        "notes": [
            "Every rendered archived table row is retained in source_tables_raw in addition to parsed Monster and Zone Drops rows.",
            "Mirror 404 is not interpreted as absence of historical game data.",
            "Historical drop percentages remain raw source observations pending target-version verification.",
        ],
    }
    legacy.dump_yaml(REPORT, payload)
    print(yaml.safe_dump(payload["counts"], sort_keys=False), end="")
    if errors:
        print(f"Recovery issues recorded: {len(errors)} (see {REPORT.relative_to(ROOT)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
