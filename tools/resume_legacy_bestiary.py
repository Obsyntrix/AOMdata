#!/usr/bin/env python3
"""Resume the legacy Angels Wiki bestiary harvest without refetching good snapshots.

This complements crawl_legacy_bestiary.py.  The first crawler establishes the corpus and
summary discovery set.  This script fills missing area/monster pages conservatively,
backs off when the preservation mirror rate-limits requests, and rebuilds the indexes
from all snapshots already present in the repository.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from email.utils import parsedate_to_datetime
import os
import sys
import time
from datetime import datetime, timezone

import requests
import yaml
from bs4 import BeautifulSoup

import crawl_legacy_bestiary as legacy

ROOT = Path(__file__).resolve().parents[1]
BESTIARY_ROOT = ROOT / "data" / "bestiary"
SUMMARY_DIR = BESTIARY_ROOT / "site" / "summary"
AREA_DIR = BESTIARY_ROOT / "site" / "areas"
MONSTER_PAGE_DIR = BESTIARY_ROOT / "site" / "monster-pages"
REPORT_PATH = BESTIARY_ROOT / "crawl-report.yaml"

REQUEST_DELAY = float(os.getenv("AOMDATA_RESUME_DELAY", "0.75"))
MAX_RETRIES = int(os.getenv("AOMDATA_RESUME_RETRIES", "8"))
BASE_BACKOFF = float(os.getenv("AOMDATA_RESUME_BACKOFF", "5"))
MAX_BACKOFF = float(os.getenv("AOMDATA_RESUME_MAX_BACKOFF", "120"))


def load_yaml(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return payload if isinstance(payload, dict) else None


def retry_after_seconds(response: requests.Response, attempt: int) -> float:
    raw = response.headers.get("Retry-After")
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            try:
                target = parsedate_to_datetime(raw)
                if target.tzinfo is None:
                    target = target.replace(tzinfo=timezone.utc)
                return max(0.0, (target - datetime.now(timezone.utc)).total_seconds())
            except Exception:  # noqa: BLE001
                pass
    return min(MAX_BACKOFF, BASE_BACKOFF * (2 ** max(0, attempt - 1)))


class ResumeFetcher:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": legacy.USER_AGENT})
        self.cache: dict[str, legacy.Fetched | None] = {}
        self.errors: list[dict[str, object]] = []
        self.retry_events: list[dict[str, object]] = []

    def get(self, url: str, *, require_legacy_marker: bool = True) -> legacy.Fetched | None:
        if url in self.cache:
            return self.cache[url]

        last_status: int | None = None
        last_detail: str | None = None
        for attempt in range(1, MAX_RETRIES + 2):
            try:
                response = self.session.get(url, timeout=legacy.TIMEOUT)
            except Exception as exc:  # noqa: BLE001
                last_detail = f"{type(exc).__name__}: {exc}"
                if attempt > MAX_RETRIES:
                    break
                delay = min(MAX_BACKOFF, BASE_BACKOFF * (2 ** max(0, attempt - 1)))
                self.retry_events.append({"url": url, "attempt": attempt, "reason": type(exc).__name__, "sleep_seconds": delay})
                time.sleep(delay)
                continue

            last_status = response.status_code
            if response.status_code == 200:
                text = response.text
                if require_legacy_marker and legacy.LEGACY_MARKER not in text.casefold():
                    self.errors.append({"url": url, "error": "legacy_marker_missing"})
                    self.cache[url] = None
                    return None
                fetched = legacy.Fetched(
                    url=url,
                    text=text,
                    digest=sha256(response.content).hexdigest(),
                    soup=BeautifulSoup(text, "html.parser"),
                )
                self.cache[url] = fetched
                if REQUEST_DELAY:
                    time.sleep(REQUEST_DELAY)
                return fetched

            if response.status_code == 404:
                self.errors.append({"url": url, "status": 404})
                self.cache[url] = None
                return None

            retryable = response.status_code == 429 or 500 <= response.status_code < 600
            if not retryable or attempt > MAX_RETRIES:
                break

            delay = retry_after_seconds(response, attempt)
            self.retry_events.append({"url": url, "attempt": attempt, "status": response.status_code, "sleep_seconds": delay})
            time.sleep(delay)

        error: dict[str, object] = {"url": url}
        if last_status is not None:
            error["status"] = last_status
        if last_detail:
            error["detail"] = last_detail
        self.errors.append(error)
        self.cache[url] = None
        return None


def discover_from_area_doc(doc: dict[str, object], monster_urls: set[str]) -> None:
    for monster in doc.get("monsters", []) or []:
        if not isinstance(monster, dict):
            continue
        url = monster.get("monster_detail_retrieval_url")
        if isinstance(url, str) and legacy.is_static_monster_url(url):
            monster_urls.add(url)
    for drop in doc.get("drops", []) or []:
        if not isinstance(drop, dict):
            continue
        for link in drop.get("monster_links", []) or []:
            if not isinstance(link, dict):
                continue
            url = link.get("retrieval_url")
            if isinstance(url, str) and legacy.is_static_monster_url(url):
                monster_urls.add(url)


def main() -> int:
    fetcher = ResumeFetcher()
    summary_docs: list[dict[str, object]] = []
    discovered_area_urls: set[str] = set()
    discovered_monster_urls: set[str] = set()

    for path in sorted(SUMMARY_DIR.glob("*.yaml")):
        doc = load_yaml(path)
        if not doc:
            continue
        summary_docs.append(doc)
        discovered_area_urls.update(str(x) for x in (doc.get("discovered_area_urls", []) or []) if isinstance(x, str))
        discovered_monster_urls.update(str(x) for x in (doc.get("discovered_monster_urls", []) or []) if isinstance(x, str))

    if not summary_docs:
        print("No captured summary documents. Run crawl_legacy_bestiary.py first.", file=sys.stderr)
        return 2

    AREA_DIR.mkdir(parents=True, exist_ok=True)
    MONSTER_PAGE_DIR.mkdir(parents=True, exist_ok=True)

    area_docs: list[dict[str, object]] = []
    newly_captured_areas = 0
    for area_url in sorted(discovered_area_urls):
        path = AREA_DIR / f"{legacy.area_slug_from_url(area_url)}.yaml"
        existing = load_yaml(path)
        if existing:
            area_docs.append(existing)
            discover_from_area_doc(existing, discovered_monster_urls)
            continue

        fetched = fetcher.get(area_url)
        if fetched is None:
            continue
        doc = legacy.parse_area_page(fetched)
        legacy.dump_yaml(path, doc)
        area_docs.append(doc)
        discover_from_area_doc(doc, discovered_monster_urls)
        newly_captured_areas += 1

    monster_docs: list[dict[str, object]] = []
    newly_captured_monsters = 0
    for monster_url in sorted(discovered_monster_urls):
        path = MONSTER_PAGE_DIR / f"{legacy.monster_slug_from_url(monster_url)}.yaml"
        existing = load_yaml(path)
        if existing:
            monster_docs.append(existing)
            continue

        fetched = fetcher.get(monster_url)
        if fetched is None:
            continue
        doc = legacy.parse_monster_page(fetched)
        legacy.dump_yaml(path, doc)
        monster_docs.append(doc)
        newly_captured_monsters += 1

    index_counts = legacy.build_indexes(summary_docs, area_docs, monster_docs)

    captured_area_ids = {
        str((doc.get("area") or {}).get("id") or "")
        for doc in area_docs
        if isinstance(doc.get("area"), dict)
    }
    captured_monster_ids = {
        str((doc.get("monster_page") or {}).get("id") or "")
        for doc in monster_docs
        if isinstance(doc.get("monster_page"), dict)
    }
    unresolved_area_urls = [
        url for url in sorted(discovered_area_urls)
        if legacy.area_slug_from_url(url) not in captured_area_ids
    ]
    unresolved_monster_urls = [
        url for url in sorted(discovered_monster_urls)
        if legacy.monster_slug_from_url(url) not in captured_monster_ids
    ]

    retryable_errors = [e for e in fetcher.errors if e.get("status") == 429 or (isinstance(e.get("status"), int) and int(e["status"]) >= 500)]
    status = "complete_for_retrievable_static_mirror" if not retryable_errors else "partial_retryable_retrieval_errors"

    report = {
        "dataset": "legacy_angels_wiki_bestiary",
        "status": status,
        "mode": "resumable_throttled_harvest",
        "counts": {
            "summary_documents": len(summary_docs),
            "discovered_area_pages": len(discovered_area_urls),
            "captured_area_pages": len(area_docs),
            "newly_captured_area_pages": newly_captured_areas,
            "unresolved_area_pages": len(unresolved_area_urls),
            "discovered_monster_detail_pages": len(discovered_monster_urls),
            "captured_monster_detail_pages": len(monster_docs),
            "newly_captured_monster_detail_pages": newly_captured_monsters,
            "unresolved_monster_detail_pages": len(unresolved_monster_urls),
            **index_counts,
        },
        "unresolved_area_urls": unresolved_area_urls,
        "unresolved_monster_urls": unresolved_monster_urls,
        "retrieval_errors_this_pass": fetcher.errors,
        "retry_events_this_pass": fetcher.retry_events,
        "notes": [
            "Existing successful snapshots are reused and are not re-requested during a resume pass.",
            "Raw displayed strings are preservation values; normalized fields are additive only.",
            "Unavailable mirror pages remain explicit gaps, not claims that the game entity did not exist.",
            "Drop percentages remain separated by source semantic and are never multiplied together without evidence.",
        ],
    }
    legacy.dump_yaml(REPORT_PATH, report)

    print(yaml.safe_dump(report["counts"], sort_keys=False), end="")
    print(f"Retrieval errors this pass: {len(fetcher.errors)}")
    print(f"Retry events this pass: {len(fetcher.retry_events)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
