#!/usr/bin/env python3
"""Capture the legacy Angels Wiki alphabetical bestiary as an independent observation."""

from __future__ import annotations

from pathlib import Path
import sys

import crawl_legacy_bestiary as legacy

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "bestiary" / "site" / "summary" / "alphabetic.yaml"

SEED = {
    "id": "alphabetic",
    "retrieval_url": "https://www.aowiki.uk/pages/bestiary_alphabetic.html",
    "origin_url": "https://angels.wikidot.com/bestiary:alphabetic",
}


def main() -> int:
    fetcher = legacy.Fetcher()
    fetched = fetcher.get(SEED["retrieval_url"])
    if fetched is None:
        print("Unable to retrieve legacy alphabetical bestiary.", file=sys.stderr)
        for error in fetcher.errors:
            print(error, file=sys.stderr)
        return 2
    doc = legacy.parse_bestiary_summary(fetched, SEED)
    legacy.dump_yaml(OUT, doc)
    print(f"Captured {len(doc.get('records', []) or [])} alphabetical bestiary rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
