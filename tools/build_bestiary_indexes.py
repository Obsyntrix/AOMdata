#!/usr/bin/env python3
"""Rebuild AOmega bestiary indexes from every preserved source layer.

The shared crawler owns index semantics. This offline entry point loads the committed
summary, area, and historical monster-page snapshots and delegates to that same builder,
so a post-harvest rebuild cannot accidentally discard summary or monster-page evidence.
"""

from __future__ import annotations

from pathlib import Path

import yaml

import crawl_legacy_bestiary as legacy

ROOT = Path(__file__).resolve().parents[1]
BESTIARY = ROOT / "data" / "bestiary"
SUMMARY_DIR = BESTIARY / "site" / "summary"
AREA_DIR = BESTIARY / "site" / "areas"
MONSTER_DIR = BESTIARY / "site" / "monster-pages"


def load_docs(directory: Path) -> list[dict[str, object]]:
    docs: list[dict[str, object]] = []
    for path in sorted(directory.glob("*.yaml")):
        try:
            value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"Unable to parse {path.relative_to(ROOT)}: {exc}") from exc
        if isinstance(value, dict):
            docs.append(value)
    return docs


def main() -> None:
    summary_docs = load_docs(SUMMARY_DIR)
    area_docs = load_docs(AREA_DIR)
    monster_docs = load_docs(MONSTER_DIR)
    counts = legacy.build_indexes(summary_docs, area_docs, monster_docs)
    print(yaml.safe_dump({
        "summary_documents": len(summary_docs),
        "area_documents": len(area_docs),
        "monster_page_documents": len(monster_docs),
        **counts,
    }, sort_keys=False), end="")


if __name__ == "__main__":
    main()
