#!/usr/bin/env python3
"""Preserve period community walkthroughs relevant to AOmega's first quest regions.

These 2012 guides are secondary historical evidence. They are especially useful where
Wikidot pages are unfinished, missing coordinates, or only contain a short objective.
They do not overwrite the canonical historical-wiki layer; contradictions remain visible.
"""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import os
import re
import time

import requests
import yaml
from bs4 import BeautifulSoup, Tag

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "quests" / "research" / "legacy-guides"
REPORT = OUT / "coverage.yaml"
USER_AGENT = "AOmega-preservation-legacy-quest-guides/1.0 (+https://github.com/Obsyntrix/AOMdata)"
DELAY = float(os.getenv("AOMDATA_GUIDE_DELAY", "0.4"))
TIMEOUT = int(os.getenv("AOMDATA_GUIDE_TIMEOUT", "35"))
RETRIES = int(os.getenv("AOMDATA_GUIDE_RETRIES", "4"))

GUIDES = [
    ("aurora", "aurora-quests", "https://ao-q.blogspot.com/2012/06/aurora-quests.html"),
    ("breeze", "beast-quests", "https://ao-q.blogspot.com/2012/07/beast-quests.html"),
    ("steel", "steel-quests", "https://ao-q.blogspot.com/2012/06/steel-quests.html"),
    ("dark", "shadow-quests", "https://ao-q.blogspot.com/2012/06/shadow-quests.html"),
    ("dungeon", "dungeon-main-quest", "https://ao-q.blogspot.com/2012/07/dungeon-main-quest.html"),
    ("dungeon", "demons-kitchen", "https://ao-q.blogspot.com/2012/07/demons-kitchen.html"),
    ("dungeon", "chef-is-wanted", "https://ao-q.blogspot.com/2012/07/chef-is-wanted.html"),
    ("dungeon", "lantern-underground", "https://ao-q.blogspot.com/2012/07/lantern-underground.html"),
    ("dungeon", "wander-around-city", "https://ao-q.blogspot.com/2012/07/wander-around-city.html"),
    ("dungeon", "clean-demons-kitchen", "https://ao-q.blogspot.com/2012/07/clean-demons-kitchen.html"),
    ("dungeon", "visit-demons-at-night", "https://ao-q.blogspot.com/2012/07/visit-demons-at-night.html"),
    ("atlantis", "atlantis-main-quest", "https://ao-q.blogspot.com/2012/06/atlantis-main-quest.html"),
    ("atlantis", "diving-training", "https://ao-q.blogspot.com/2012/07/diving-training.html"),
    ("atlantis", "fantastic-voyage", "https://ao-q.blogspot.com/2012/07/fantastic-voyage.html"),
    ("atlantis", "holiday-in-puqi-village", "https://ao-q.blogspot.com/2012/07/holiday-in-puqi-village.html"),
    ("atlantis", "soldiers-on-sea", "https://ao-q.blogspot.com/2012/07/soldiers-on-sea.html"),
    ("atlantis", "calm-souls-down", "https://ao-q.blogspot.com/2012/07/calm-souls-down.html"),
]

GROUP_HEADERS = {
    "executive newbie aurora", "executive assistant aurora", "executive aurora", "senior executive aurora", "chief executive aurora", "elite executive aurora", "aurora quest expert",
    "executive newbie beast", "executive assistant beast", "executive beast", "senior executive beast", "chief executive beast", "elite executive beast", "beast quest expert",
    "executive newbie steel", "executive assistant steel", "executive steel", "senior executive steel", "chief executive steel", "elite executive steel", "steel quest expert",
    "executive newbie shadow", "executive assistant shadow", "executive shadow", "senior executive shadow", "chief executive shadow", "elite executive shadow", "shadow quest expert",
}


def norm(value: str) -> str:
    text = value.casefold().replace("’", "'")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False, allow_unicode=True, width=180), encoding="utf-8")


class Fetcher:
    def __init__(self) -> None:
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": USER_AGENT})
        self.errors: list[dict[str, object]] = []

    def get(self, url: str) -> requests.Response | None:
        for attempt in range(RETRIES + 1):
            try:
                r = self.s.get(url, timeout=TIMEOUT)
            except Exception as exc:  # noqa: BLE001
                self.errors.append({"url": url, "attempt": attempt + 1, "error": type(exc).__name__, "detail": str(exc)})
                return None
            if r.status_code == 200:
                if DELAY:
                    time.sleep(DELAY)
                return r
            if r.status_code in {429, 500, 502, 503, 504} and attempt < RETRIES:
                pause = min(45, 3 * (2 ** attempt))
                time.sleep(pause)
                continue
            self.errors.append({"url": url, "status": r.status_code, "attempt": attempt + 1})
            return None
        return None


def post_body(soup: BeautifulSoup) -> Tag | None:
    selectors = [
        "div.post-body.entry-content", "div.post-body", "div.entry-content",
        "article", "main",
    ]
    for selector in selectors:
        node = soup.select_one(selector)
        if node is not None:
            return node
    return None


def body_lines(node: Tag) -> list[str]:
    text = node.get_text("\n", strip=True)
    out = []
    for raw in text.splitlines():
        line = " ".join(raw.split())
        if not line:
            continue
        if line.startswith("Posted by ") or line.startswith("Labels:"):
            break
        out.append(line)
    return out


def is_step(line: str) -> bool:
    return bool(re.match(r"^(?:\d+[.)]|step\s+(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+))\s*", line, re.I))


def parse_blocks(lines: list[str]) -> list[dict[str, object]]:
    """Heuristically retain quest-title + numbered-step blocks without pretending the
    community guide's formatting is perfectly machine-readable.
    """
    blocks: list[dict[str, object]] = []
    current_title: str | None = None
    current_steps: list[str] = []
    section: str | None = None

    def flush() -> None:
        nonlocal current_title, current_steps
        if current_title and current_steps:
            blocks.append({"title_raw": current_title, "section_raw": section, "steps_raw": list(current_steps)})
        current_title = None
        current_steps = []

    for line in lines:
        n = norm(line)
        if not n or set(line) <= {"-", "_", "="}:
            continue
        if n in GROUP_HEADERS or n.startswith("executive ") or n.endswith(" quest expert"):
            flush()
            section = line
            continue
        if is_step(line):
            if current_title:
                current_steps.append(line)
            continue
        # Lines that are obviously explanatory notes belong to the current step/block.
        if current_title and current_steps and (line.casefold().startswith(("note:", "advice:", "tip:")) or line.startswith(("-", "("))):
            current_steps.append(line)
            continue
        # A new non-step line after one or more steps is generally the next quest title.
        if current_title and current_steps:
            flush()
        # Ignore generic post title echoes and requirements prose as quest titles.
        if n in {"aurora quests", "beast quests", "steel quests", "shadow quests", "dungeon main quest", "demon s kitchen", "diving training", "fantastic voyage", "holiday in puqi village", "soldiers on the sea", "calm souls down", "clean the demon s kitchen", "visit demons at night", "lantern underground", "wander around the city", "chef is wanted"}:
            section = line
            continue
        if line.casefold().startswith(("requirements:", "you start", "we start", "this guide", "ok so", "posted by")):
            continue
        current_title = line
    flush()
    return blocks


def main() -> int:
    fetcher = Fetcher()
    captured = []
    missing = []
    per_region: dict[str, dict[str, int]] = {}

    for region, guide_id, url in GUIDES:
        response = fetcher.get(url)
        if response is None:
            missing.append({"region": region, "guide_id": guide_id, "url": url})
            continue
        soup = BeautifulSoup(response.text, "html.parser")
        body = post_body(soup)
        if body is None:
            missing.append({"region": region, "guide_id": guide_id, "url": url, "reason": "post_body_not_found"})
            continue
        lines = body_lines(body)
        blocks = parse_blocks(lines)
        doc = {
            "schema_version": 1,
            "dataset": "legacy_community_quest_guide",
            "region": region,
            "guide_id": guide_id,
            "title_raw": " ".join((soup.find("h3") or soup.find("h1") or soup.title).stripped_strings) if (soup.find("h3") or soup.find("h1") or soup.title) else guide_id,
            "source": {
                "origin_url": url,
                "retrieval_url": response.url,
                "source_kind": "period_community_walkthrough_2012",
                "source_html_sha256": sha256(response.content).hexdigest(),
                "authority_role": "secondary_historical_walkthrough",
                "client_confirmation_for_aomega": "pending",
            },
            "body_lines_raw": lines,
            "candidate_quest_blocks": blocks,
        }
        dump(OUT / region / f"{guide_id}.yaml", doc)
        captured.append({"region": region, "guide_id": guide_id, "candidate_blocks": len(blocks), "url": url})
        stats = per_region.setdefault(region, {"guides_captured": 0, "candidate_quest_blocks": 0})
        stats["guides_captured"] += 1
        stats["candidate_quest_blocks"] += len(blocks)

    report = {
        "schema_version": 1,
        "dataset": "legacy_quest_guide_coverage",
        "counts": {"guides_expected": len(GUIDES), "guides_captured": len(captured), "guides_missing": len(missing), "candidate_quest_blocks": sum(x["candidate_blocks"] for x in captured)},
        "per_region": per_region,
        "captured": captured,
        "missing": missing,
        "retrieval_errors": fetcher.errors,
        "notes": [
            "These guides are period community walkthroughs, not a replacement for original Angels Wiki pages.",
            "Candidate blocks are heuristic parsing aids; body_lines_raw preserves the complete post body used for manual/automated reconciliation.",
            "Coordinates and counts are retained exactly as written, including historical typos and disagreements.",
        ],
    }
    dump(REPORT, report)
    print(yaml.safe_dump({"counts": report["counts"], "per_region": per_region, "missing": missing}, sort_keys=False, allow_unicode=True, width=180), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
