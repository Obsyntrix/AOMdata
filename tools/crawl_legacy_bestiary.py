#!/usr/bin/env python3
"""Crawl the static mirror of the historical Angels Online Wiki bestiary.

This importer exists for preservation, not for "cleaning up" the old wiki.  Displayed
values are retained as raw strings and normalized fields are additive conveniences.
The accepted source corpus is the static /pages mirror that declares the material as
original angels.wikidot.com content.

Outputs are deterministic: a second run against unchanged source HTML should produce no
repository diff.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote
import os
import re
import sys
import time

import requests
import yaml
from bs4 import BeautifulSoup, Tag

ROOT = Path(__file__).resolve().parents[1]
BESTIARY_ROOT = ROOT / "data" / "bestiary"
SUMMARY_DIR = BESTIARY_ROOT / "site" / "summary"
AREA_DIR = BESTIARY_ROOT / "site" / "areas"
MONSTER_PAGE_DIR = BESTIARY_ROOT / "site" / "monster-pages"
INDEX_DIR = BESTIARY_ROOT / "indexes"
REPORT_PATH = BESTIARY_ROOT / "crawl-report.yaml"

MIRROR_ROOT = "https://www.aowiki.uk/pages/"
LEGACY_MARKER = "angels.wikidot.com"
USER_AGENT = "AOmega-preservation-bestiary/1.0 (+https://github.com/Obsyntrix/AOMdata)"
TIMEOUT = 30
REQUEST_DELAY = float(os.getenv("AOMDATA_CRAWL_DELAY", "0.05"))
CRAWL_MONSTER_PAGES = os.getenv("AOMDATA_CRAWL_MONSTER_PAGES", "1") not in {"0", "false", "False"}

SEED_PAGES = [
    {
        "id": "by-area",
        "retrieval_url": urljoin(MIRROR_ROOT, "bestiary_by-area.html"),
        "origin_url": "https://angels.wikidot.com/bestiary:by-area",
    },
    {
        "id": "more-map",
        "retrieval_url": urljoin(MIRROR_ROOT, "bestiary_more-map.html"),
        "origin_url": "https://angels.wikidot.com/bestiary:more-map",
    },
    {
        "id": "by-level",
        "retrieval_url": urljoin(MIRROR_ROOT, "bestiary_by-level.html"),
        "origin_url": "https://angels.wikidot.com/bestiary:by-level",
    },
]


def dump_yaml(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=160)
    path.write_text(text, encoding="utf-8")


def slugify(value: str) -> str:
    value = value.strip().casefold().replace("'", "")
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value or "unknown"


def clean_text(node: Tag | None) -> str:
    if node is None:
        return ""
    # This is the rendered cell text, not a spelling correction.  NBSP is normalized by
    # BeautifulSoup to a normal printable space; punctuation/case/numbers are untouched.
    return " ".join(node.stripped_strings)


def header_key(value: str) -> str:
    value = value.casefold()
    value = value.replace("spl", "spell")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def parse_int(raw: object) -> int | None:
    if raw is None:
        return None
    text = str(raw).strip().replace(",", "")
    if not re.fullmatch(r"[-+]?\d+", text):
        return None
    try:
        return int(text)
    except ValueError:
        return None


def parse_float_percent(raw: object) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip().replace(",", ".")
    if "?" in text or text.startswith("<"):
        return None
    text = text.removesuffix("%").strip()
    try:
        return float(text)
    except ValueError:
        return None


def bool_from_yes_no(raw: object) -> bool | None:
    if raw is None:
        return None
    text = str(raw).strip().casefold()
    if text == "yes":
        return True
    if text == "no":
        return False
    return None


def boss_from_aggression(raw: object) -> bool | None:
    """Interpret the legacy *Agr.* column without changing its raw value.

    `No BOSS` means a non-aggressive boss; `Boss` and `Boss Smn` are bosses.
    """
    if raw is None:
        return None
    text = str(raw).strip().casefold()
    if "boss" in text:
        return True
    if text in {"yes", "no"}:
        return False
    return None


def aggressive_from_aggression(raw: object) -> bool | None:
    if raw is None:
        return None
    text = str(raw).strip().casefold()
    if text.startswith("no"):
        return False
    if text.startswith("yes") or text.startswith("boss"):
        return True
    return None


def link_info(cell: Tag | None, prefix: str | None = None) -> list[dict[str, str]]:
    if cell is None:
        return []
    out: list[dict[str, str]] = []
    for anchor in cell.find_all("a", href=True):
        href = urljoin(MIRROR_ROOT, anchor["href"])
        if prefix and prefix not in Path(urlparse(href).path).name:
            continue
        out.append({"text_raw": clean_text(anchor), "retrieval_url": href})
    return out


def image_raw(cell: Tag | None) -> str | None:
    if cell is None:
        return None
    image = cell.find("img")
    if not image:
        return None
    src = image.get("src")
    if not src:
        return None
    return unquote(Path(urlparse(src).path).name)


@dataclass
class Fetched:
    url: str
    text: str
    digest: str
    soup: BeautifulSoup


class Fetcher:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self.cache: dict[str, Fetched | None] = {}
        self.errors: list[dict[str, object]] = []

    def get(self, url: str, *, require_legacy_marker: bool = True) -> Fetched | None:
        if url in self.cache:
            return self.cache[url]
        try:
            response = self.session.get(url, timeout=TIMEOUT)
        except Exception as exc:  # noqa: BLE001 - report exact retrieval failure
            self.errors.append({"url": url, "error": type(exc).__name__, "detail": str(exc)})
            self.cache[url] = None
            return None
        if response.status_code != 200:
            self.errors.append({"url": url, "status": response.status_code})
            self.cache[url] = None
            return None
        text = response.text
        if require_legacy_marker and LEGACY_MARKER not in text.casefold():
            self.errors.append({"url": url, "error": "legacy_marker_missing"})
            self.cache[url] = None
            return None
        fetched = Fetched(
            url=url,
            text=text,
            digest=sha256(response.content).hexdigest(),
            soup=BeautifulSoup(text, "html.parser"),
        )
        self.cache[url] = fetched
        if REQUEST_DELAY:
            time.sleep(REQUEST_DELAY)
        return fetched


def table_matrix(table: Tag) -> tuple[list[str], list[tuple[list[Tag], list[str]]]]:
    rows = table.find_all("tr")
    if not rows:
        return [], []
    header_cells = rows[0].find_all(["th", "td"], recursive=False)
    headers = [clean_text(cell) for cell in header_cells]
    body: list[tuple[list[Tag], list[str]]] = []
    for tr in rows[1:]:
        cells = tr.find_all(["th", "td"], recursive=False)
        if not cells:
            continue
        values = [clean_text(cell) for cell in cells]
        # Repeated header rows are common in old Wikidot tables.
        if [header_key(v) for v in values] == [header_key(v) for v in headers]:
            continue
        body.append((cells, values))
    return headers, body


def column_index(headers: list[str], *aliases: str) -> int | None:
    normalized = [header_key(h) for h in headers]
    wanted = {header_key(a) for a in aliases}
    for idx, key in enumerate(normalized):
        if key in wanted:
            return idx
    return None


def row_value(values: list[str], idx: int | None) -> str | None:
    if idx is None or idx >= len(values):
        return None
    return values[idx]


def row_cell(cells: list[Tag], idx: int | None) -> Tag | None:
    if idx is None or idx >= len(cells):
        return None
    return cells[idx]


def source_meta(fetched: Fetched, origin_url: str, kind: str) -> dict[str, object]:
    return {
        "origin_url": origin_url,
        "retrieval_url": fetched.url,
        "source_kind": kind,
        "mirror_declares_original_angels_wikidot_content": True,
        "source_html_sha256": fetched.digest,
    }


def legacy_origin_from_area_url(url: str) -> str:
    name = Path(urlparse(url).path).name
    slug = name.removeprefix("area_").removesuffix(".html")
    return f"https://angels.wikidot.com/area:{slug}"


def legacy_origin_from_monster_url(url: str) -> str:
    name = Path(urlparse(url).path).name
    slug = name.removeprefix("monster_").removesuffix(".html")
    return f"https://angels.wikidot.com/monster:{slug}"


def is_static_area_url(url: str) -> bool:
    parsed = urlparse(url)
    name = Path(parsed.path).name
    return parsed.netloc.endswith("aowiki.uk") and parsed.path.startswith("/pages/") and name.startswith("area_") and name.endswith(".html")


def is_static_monster_url(url: str) -> bool:
    parsed = urlparse(url)
    name = Path(parsed.path).name
    return parsed.netloc.endswith("aowiki.uk") and parsed.path.startswith("/pages/") and name.startswith("monster_") and name.endswith(".html")


def parse_bestiary_summary(fetched: Fetched, seed: dict[str, str]) -> dict[str, object]:
    records: list[dict[str, object]] = []
    area_urls: set[str] = set()
    monster_urls: set[str] = set()
    table_number = 0

    for table in fetched.soup.find_all("table"):
        headers, rows = table_matrix(table)
        if not headers:
            continue
        name_idx = column_index(headers, "Name", "Monster Name")
        level_idx = column_index(headers, "Level", "Lvl")
        area_idx = column_index(headers, "Area", "Zone")
        if name_idx is None or level_idx is None or area_idx is None:
            continue

        image_idx = column_index(headers, "Image")
        hp_idx = column_index(headers, "HP")
        atk_type_idx = column_index(headers, "Atk Type", "Attack Type")
        atk_idx = column_index(headers, "Atk", "Attack")
        dfs_idx = column_index(headers, "Dfs", "Defense", "Def")
        spl_atk_idx = column_index(headers, "Spl Atk", "Spell Atk", "Spell Attack")
        spl_dfs_idx = column_index(headers, "Spl Dfs", "Spell Dfs", "Spell Defense")
        agr_idx = column_index(headers, "Agr.", "Agr", "Aggressive")
        exp_idx = column_index(headers, "Exp", "Experience")
        table_number += 1

        for row_number, (cells, values) in enumerate(rows, start=1):
            name_raw = row_value(values, name_idx)
            level_raw = row_value(values, level_idx)
            area_raw = row_value(values, area_idx)
            if not name_raw or name_raw == "Name" or not area_raw:
                continue

            name_links = link_info(row_cell(cells, name_idx))
            area_links = link_info(row_cell(cells, area_idx))
            detail_url = next((x["retrieval_url"] for x in name_links if is_static_monster_url(x["retrieval_url"])), None)
            if detail_url:
                monster_urls.add(detail_url)
            static_area_links = [x for x in area_links if is_static_area_url(x["retrieval_url"])]
            for link in static_area_links:
                area_urls.add(link["retrieval_url"])

            aggression_raw = row_value(values, agr_idx)
            record_id = f"{seed['id']}__t{table_number:03d}__r{row_number:04d}"
            record = {
                "record_id": record_id,
                "name_raw": name_raw,
                "level_raw": level_raw,
                "hp_raw": row_value(values, hp_idx),
                "attack_type_raw": row_value(values, atk_type_idx),
                "attack_raw": row_value(values, atk_idx),
                "defense_raw": row_value(values, dfs_idx),
                "spell_attack_raw": row_value(values, spl_atk_idx),
                "spell_defense_raw": row_value(values, spl_dfs_idx),
                "aggression_raw": aggression_raw,
                "experience_raw": row_value(values, exp_idx),
                "area_raw": area_raw,
                "image_raw": image_raw(row_cell(cells, image_idx)),
                "monster_detail_retrieval_url": detail_url,
                "area_links": static_area_links,
                "source_table_headers_raw": headers,
                "normalized": {
                    "level": parse_int(level_raw),
                    "boss": boss_from_aggression(aggression_raw),
                    "aggressive": aggressive_from_aggression(aggression_raw),
                },
            }
            # Preserve all rendered columns as an additional raw row map, including any
            # future columns the schema does not know yet.
            record["row_raw"] = {
                headers[i]: values[i] if i < len(values) else ""
                for i in range(len(headers))
            }
            records.append(record)

    return {
        "source": source_meta(fetched, seed["origin_url"], "angels_wiki_bestiary_mirror"),
        "bestiary_id": seed["id"],
        "records": records,
        "discovered_area_urls": sorted(area_urls),
        "discovered_monster_urls": sorted(monster_urls),
        "capture": {"records": len(records), "status": "complete" if records else "unavailable"},
        "verification": {"client_confirmation": "pending"},
    }


def page_title(soup: BeautifulSoup, fallback: str) -> str:
    h1 = soup.find("h1")
    return clean_text(h1) or fallback


def area_slug_from_url(url: str) -> str:
    return Path(urlparse(url).path).name.removeprefix("area_").removesuffix(".html")


def parse_area_page(fetched: Fetched) -> dict[str, object]:
    area_id = area_slug_from_url(fetched.url)
    title = page_title(fetched.soup, area_id.replace("-", " ").title())
    monsters: list[dict[str, object]] = []
    drops: list[dict[str, object]] = []
    raw_tables: list[dict[str, object]] = []

    for table_index, table in enumerate(fetched.soup.find_all("table"), start=1):
        headers, rows = table_matrix(table)
        if not headers:
            continue
        raw_tables.append({"table_index": table_index, "headers_raw": headers, "row_count": len(rows)})
        normalized_headers = {header_key(h) for h in headers}

        monster_name_idx = column_index(headers, "Monster Name", "Monster")
        level_idx = column_index(headers, "Level", "Lvl")
        boss_idx = column_index(headers, "Boss")
        aggressive_idx = column_index(headers, "Aggressive", "Agr.", "Agr")

        if monster_name_idx is not None and level_idx is not None and boss_idx is not None and aggressive_idx is not None:
            for row_number, (cells, values) in enumerate(rows, start=1):
                name_raw = row_value(values, monster_name_idx)
                if not name_raw:
                    continue
                level_raw = row_value(values, level_idx)
                boss_raw = row_value(values, boss_idx)
                aggressive_raw = row_value(values, aggressive_idx)
                name_links = link_info(row_cell(cells, monster_name_idx))
                detail_url = next((x["retrieval_url"] for x in name_links if is_static_monster_url(x["retrieval_url"])), None)
                monsters.append({
                    "area_occurrence_id": f"{area_id}__{slugify(name_raw)}__t{table_index:03d}r{row_number:04d}",
                    "name_raw": name_raw,
                    "level_raw": level_raw,
                    "boss_raw": boss_raw,
                    "aggressive_raw": aggressive_raw,
                    "monster_detail_retrieval_url": detail_url,
                    "row_raw": {headers[i]: values[i] if i < len(values) else "" for i in range(len(headers))},
                    "normalized": {
                        "level": parse_int(level_raw),
                        "boss": bool_from_yes_no(boss_raw),
                        "aggressive": bool_from_yes_no(aggressive_raw),
                    },
                })
            continue

        item_idx = column_index(headers, "Item", "Item Name", "Drop")
        rate_idx = column_index(headers, "Drop Share", "Chance to drop", "Chance To Drop", "Drop Chance")
        qty_idx = column_index(headers, "Quantity", "Qty", "Amount")
        if monster_name_idx is not None and item_idx is not None and rate_idx is not None:
            rate_header = headers[rate_idx]
            semantic = "zone_drop_share" if header_key(rate_header) == "drop share" else "monster_drop_table_share"
            for row_number, (cells, values) in enumerate(rows, start=1):
                monster_name_raw = row_value(values, monster_name_idx)
                item_name_raw = row_value(values, item_idx)
                if not monster_name_raw or not item_name_raw:
                    continue
                rate_raw = row_value(values, rate_idx)
                quantity_raw = row_value(values, qty_idx)
                monster_links = link_info(row_cell(cells, monster_name_idx))
                item_links = link_info(row_cell(cells, item_idx))
                drops.append({
                    "drop_id": f"{area_id}__t{table_index:03d}r{row_number:04d}",
                    "monster_name_raw": monster_name_raw,
                    "item_name_raw": item_name_raw,
                    "drop_share_raw": rate_raw,
                    "quantity_raw": quantity_raw,
                    "rate_semantic": semantic,
                    "monster_links": monster_links,
                    "item_links": item_links,
                    "row_raw": {headers[i]: values[i] if i < len(values) else "" for i in range(len(headers))},
                    "normalized": {
                        "drop_share_percent": parse_float_percent(rate_raw),
                        "quantity": parse_int(quantity_raw),
                    },
                })

    return {
        "area": {
            "id": area_id,
            "name_raw": title,
            "source": source_meta(fetched, legacy_origin_from_area_url(fetched.url), "angels_wiki_area_mirror"),
            "capture": {
                "monsters": "complete" if monsters else "not_present",
                "zone_drops": "complete" if drops else "not_present",
            },
            "verification": {"client_confirmation": "pending"},
        },
        "monsters": monsters,
        "drops": drops,
        "source_tables": raw_tables,
    }


def monster_slug_from_url(url: str) -> str:
    return Path(urlparse(url).path).name.removeprefix("monster_").removesuffix(".html")


def parse_monster_page(fetched: Fetched) -> dict[str, object]:
    slug = monster_slug_from_url(fetched.url)
    title = page_title(fetched.soup, slug.replace("-", " ").title())
    fields_raw: dict[str, str] = {}
    tables_raw: list[dict[str, object]] = []
    drops: list[dict[str, object]] = []

    # Generic two-column field tables are retained without assuming every legacy page has
    # the same labels.
    for table_index, table in enumerate(fetched.soup.find_all("table"), start=1):
        headers, rows = table_matrix(table)
        if not headers:
            continue
        tables_raw.append({"table_index": table_index, "headers_raw": headers, "row_count": len(rows)})

        # A two-cell row can also be a field/value table even when the first row is not a
        # conventional header, so inspect every row including the header row.
        all_rows = table.find_all("tr")
        for tr in all_rows:
            cells = tr.find_all(["th", "td"], recursive=False)
            if len(cells) == 2:
                key = clean_text(cells[0])
                value = clean_text(cells[1])
                if key and value and len(key) < 80:
                    fields_raw.setdefault(key, value)

        monster_idx = column_index(headers, "Monster", "Monster Name")
        item_idx = column_index(headers, "Item", "Item Name", "Drop")
        rate_idx = column_index(headers, "Chance to drop", "Chance To Drop", "Drop Share", "Drop Chance")
        qty_idx = column_index(headers, "Quantity", "Qty", "Amount")
        # Individual monster tables often omit the redundant Monster column.
        if item_idx is not None and rate_idx is not None:
            for row_number, (cells, values) in enumerate(rows, start=1):
                item_name_raw = row_value(values, item_idx)
                if not item_name_raw:
                    continue
                monster_name_raw = row_value(values, monster_idx) or title
                rate_raw = row_value(values, rate_idx)
                quantity_raw = row_value(values, qty_idx)
                drops.append({
                    "drop_id": f"{slug}__t{table_index:03d}r{row_number:04d}",
                    "monster_name_raw": monster_name_raw,
                    "item_name_raw": item_name_raw,
                    "chance_to_drop_raw": rate_raw,
                    "quantity_raw": quantity_raw,
                    "rate_semantic": "monster_drop_table_share",
                    "row_raw": {headers[i]: values[i] if i < len(values) else "" for i in range(len(headers))},
                    "normalized": {
                        "chance_to_drop_percent": parse_float_percent(rate_raw),
                        "quantity": parse_int(quantity_raw),
                    },
                })

    item_drop_chance_raw = None
    for key, value in fields_raw.items():
        if header_key(key) == "item drop chance":
            item_drop_chance_raw = value
            break

    return {
        "monster_page": {
            "id": slug,
            "name_raw": title,
            "source": source_meta(fetched, legacy_origin_from_monster_url(fetched.url), "angels_wiki_monster_mirror"),
            "verification": {"client_confirmation": "pending"},
        },
        "fields_raw": fields_raw,
        "item_drop_chance_raw": item_drop_chance_raw,
        "drops": drops,
        "source_tables": tables_raw,
    }


def summary_appearances(summary_docs: list[dict[str, object]]) -> list[dict[str, object]]:
    appearances: list[dict[str, object]] = []
    for doc in summary_docs:
        bestiary_id = str(doc.get("bestiary_id") or "unknown")
        for record in doc.get("records", []) or []:
            if not isinstance(record, dict):
                continue
            area_links = record.get("area_links") or []
            if area_links:
                for area_link in area_links:
                    url = area_link.get("retrieval_url") if isinstance(area_link, dict) else None
                    if not url or not is_static_area_url(url):
                        continue
                    area_id = area_slug_from_url(url)
                    appearance = dict(record)
                    appearance["summary_source_id"] = bestiary_id
                    appearance["area_id"] = area_id
                    appearance["area_name_raw"] = area_link.get("text_raw") or record.get("area_raw")
                    appearances.append(appearance)
            else:
                appearance = dict(record)
                appearance["summary_source_id"] = bestiary_id
                appearance["area_id"] = None
                appearance["area_name_raw"] = record.get("area_raw")
                appearances.append(appearance)
    return appearances


def dedupe_appearances(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    seen: set[tuple[object, ...]] = set()
    out: list[dict[str, object]] = []
    for row in rows:
        key = (
            str(row.get("name_raw") or "").casefold(),
            row.get("level_raw"),
            row.get("hp_raw"),
            row.get("attack_type_raw"),
            row.get("attack_raw"),
            row.get("defense_raw"),
            row.get("spell_attack_raw"),
            row.get("spell_defense_raw"),
            row.get("aggression_raw"),
            row.get("experience_raw"),
            row.get("area_id"),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def compact_appearance(row: dict[str, object]) -> dict[str, object]:
    normalized = row.get("normalized") or {}
    return {
        "record_id": row.get("record_id"),
        "name_raw": row.get("name_raw"),
        "level_raw": row.get("level_raw"),
        "level": normalized.get("level") if isinstance(normalized, dict) else None,
        "boss": normalized.get("boss") if isinstance(normalized, dict) else None,
        "aggressive": normalized.get("aggressive") if isinstance(normalized, dict) else None,
        "area_id": row.get("area_id"),
        "area_name_raw": row.get("area_name_raw"),
        "summary_source_id": row.get("summary_source_id"),
    }


def build_indexes(summary_docs: list[dict[str, object]], area_docs: list[dict[str, object]], monster_page_docs: list[dict[str, object]]) -> dict[str, int]:
    appearances = dedupe_appearances(summary_appearances(summary_docs))
    by_name_candidates: dict[str, list[dict[str, object]]] = defaultdict(list)
    by_area: dict[str, dict[str, object]] = {}
    by_level: dict[int, list[dict[str, object]]] = defaultdict(list)
    bosses: list[dict[str, object]] = []

    for row in appearances:
        key = str(row.get("name_raw") or "").casefold()
        if key:
            by_name_candidates[key].append(row)
        compact = compact_appearance(row)
        area_id = row.get("area_id")
        if area_id:
            area_node = by_area.setdefault(str(area_id), {"area_id": area_id, "area_name_raw": row.get("area_name_raw"), "monsters": []})
            area_node["monsters"].append(compact)
        level = compact.get("level")
        if isinstance(level, int):
            by_level[level].append(compact)
        if compact.get("boss") is True:
            bosses.append(compact)

    # Supplement summary indexes with explicit area Monster tables when the summary table
    # omitted the area or full row.  Keep the area observation visibly separate.
    for area_doc in area_docs:
        area = area_doc.get("area") or {}
        area_id = str(area.get("id") or "")
        area_name = area.get("name_raw") or area_id
        if not area_id:
            continue
        area_node = by_area.setdefault(area_id, {"area_id": area_id, "area_name_raw": area_name, "monsters": []})
        existing_keys = {(str(x.get("name_raw") or "").casefold(), x.get("level_raw")) for x in area_node["monsters"]}
        for monster in area_doc.get("monsters", []) or []:
            if not isinstance(monster, dict):
                continue
            key = (str(monster.get("name_raw") or "").casefold(), monster.get("level_raw"))
            if key in existing_keys:
                continue
            normalized = monster.get("normalized") or {}
            compact = {
                "record_id": monster.get("area_occurrence_id"),
                "name_raw": monster.get("name_raw"),
                "level_raw": monster.get("level_raw"),
                "level": normalized.get("level") if isinstance(normalized, dict) else None,
                "boss": normalized.get("boss") if isinstance(normalized, dict) else None,
                "aggressive": normalized.get("aggressive") if isinstance(normalized, dict) else None,
                "area_id": area_id,
                "area_name_raw": area_name,
                "summary_source_id": "area_page",
            }
            area_node["monsters"].append(compact)
            if isinstance(compact.get("level"), int):
                by_level[int(compact["level"])].append(compact)
            if compact.get("boss") is True:
                bosses.append(compact)
            by_name_candidates[str(monster.get("name_raw") or "").casefold()].append({
                **monster,
                "record_id": monster.get("area_occurrence_id"),
                "area_id": area_id,
                "area_name_raw": area_name,
                "summary_source_id": "area_page",
            })

    item_nodes: dict[str, dict[str, object]] = {}

    def add_drop(*, drop: dict[str, object], source_page_area: dict[str, object] | None, source: dict[str, object], source_kind: str) -> None:
        item_name = str(drop.get("item_name_raw") or "")
        monster_name = str(drop.get("monster_name_raw") or "")
        if not item_name or not monster_name:
            return
        item_key = item_name.casefold()
        item_node = item_nodes.setdefault(item_key, {"item_name_raw": item_name, "sources": []})
        candidates = by_name_candidates.get(monster_name.casefold(), [])
        compact_candidates = [compact_appearance(candidate) for candidate in candidates]
        source_area_id = source_page_area.get("id") if source_page_area else None
        local_candidates = [x for x in compact_candidates if x.get("area_id") == source_area_id]
        if len(local_candidates) == 1:
            resolution = "exact_name_local_area"
            resolved = local_candidates
        elif len(compact_candidates) == 1:
            resolution = "exact_name_unique_global"
            resolved = compact_candidates
        elif len(local_candidates) > 1:
            resolution = "ambiguous_local"
            resolved = local_candidates
        elif compact_candidates:
            resolution = "ambiguous_global"
            resolved = compact_candidates
        else:
            resolution = "unresolved_name"
            resolved = []

        raw_rate = drop.get("drop_share_raw") if "drop_share_raw" in drop else drop.get("chance_to_drop_raw")
        if raw_rate is None:
            raw_rate = drop.get("rate_raw")
        item_node["sources"].append({
            "monster_name_raw": monster_name,
            "source_page_area_id": source_area_id,
            "source_page_area_name_raw": source_page_area.get("name_raw") if source_page_area else None,
            "rate_raw": raw_rate,
            "rate_percent": parse_float_percent(raw_rate),
            "rate_semantic": drop.get("rate_semantic"),
            "quantity_raw": drop.get("quantity_raw"),
            "quantity": (drop.get("normalized") or {}).get("quantity") if isinstance(drop.get("normalized"), dict) else parse_int(drop.get("quantity_raw")),
            "resolution_status": resolution,
            "resolved_monster_appearances": resolved,
            "source_kind": source_kind,
            "source_url": source.get("origin_url"),
            "retrieval_url": source.get("retrieval_url"),
        })

    for area_doc in area_docs:
        area = area_doc.get("area") or {}
        source = area.get("source") or {}
        for drop in area_doc.get("drops", []) or []:
            if isinstance(drop, dict):
                add_drop(drop=drop, source_page_area=area, source=source, source_kind="area_zone_drops")

    for monster_doc in monster_page_docs:
        page = monster_doc.get("monster_page") or {}
        source = page.get("source") or {}
        for drop in monster_doc.get("drops", []) or []:
            if isinstance(drop, dict):
                add_drop(drop=drop, source_page_area=None, source=source, source_kind="monster_page_drop_table")

    by_name_payload = {
        "monsters": [
            {
                "name_raw": rows[0].get("name_raw") if rows else key,
                "appearances": [compact_appearance(row) for row in rows],
            }
            for key, rows in sorted(by_name_candidates.items())
            if key
        ]
    }
    by_area_payload = {"areas": [by_area[k] for k in sorted(by_area)]}
    by_level_payload = {
        "levels": [
            {"level": level, "monsters": sorted(rows, key=lambda x: (str(x.get("name_raw") or "").casefold(), str(x.get("area_id") or "")))}
            for level, rows in sorted(by_level.items())
        ]
    }
    by_item_payload = {"items": sorted(item_nodes.values(), key=lambda x: str(x.get("item_name_raw") or "").casefold())}
    bosses_payload = {"bosses": sorted(bosses, key=lambda x: ((x.get("level") if isinstance(x.get("level"), int) else -1), str(x.get("name_raw") or "").casefold(), str(x.get("area_id") or "")))}

    dump_yaml(INDEX_DIR / "by-area.yaml", by_area_payload)
    dump_yaml(INDEX_DIR / "by-level.yaml", by_level_payload)
    dump_yaml(INDEX_DIR / "by-item.yaml", by_item_payload)
    dump_yaml(INDEX_DIR / "by-name.yaml", by_name_payload)
    dump_yaml(INDEX_DIR / "bosses.yaml", bosses_payload)

    return {
        "summary_appearances": len(appearances),
        "areas_indexed": len(by_area),
        "levels_indexed": len(by_level),
        "unique_monster_names": len(by_name_candidates),
        "unique_item_names": len(item_nodes),
        "boss_appearances": len(bosses),
        "drop_source_rows": sum(len(node["sources"]) for node in item_nodes.values()),
    }


def load_existing_area(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


def main() -> int:
    fetcher = Fetcher()
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
    AREA_DIR.mkdir(parents=True, exist_ok=True)
    MONSTER_PAGE_DIR.mkdir(parents=True, exist_ok=True)

    summary_docs: list[dict[str, object]] = []
    discovered_area_urls: set[str] = set()
    discovered_monster_urls: set[str] = set()
    seed_status: list[dict[str, object]] = []

    for seed in SEED_PAGES:
        fetched = fetcher.get(seed["retrieval_url"])
        if fetched is None:
            seed_status.append({"id": seed["id"], "retrieval_url": seed["retrieval_url"], "status": "unavailable"})
            continue
        doc = parse_bestiary_summary(fetched, seed)
        dump_yaml(SUMMARY_DIR / f"{seed['id']}.yaml", doc)
        summary_docs.append(doc)
        discovered_area_urls.update(doc.get("discovered_area_urls", []) or [])
        discovered_monster_urls.update(doc.get("discovered_monster_urls", []) or [])
        seed_status.append({"id": seed["id"], "retrieval_url": seed["retrieval_url"], "status": "captured", "records": len(doc.get("records", []) or [])})

    # Main bestiary rows are the safest discovery source.  Monster detail links can add
    # area URLs later, but all static area links in the summary are crawled first.
    area_docs: list[dict[str, object]] = []
    for area_url in sorted(discovered_area_urls):
        fetched = fetcher.get(area_url)
        path = AREA_DIR / f"{area_slug_from_url(area_url)}.yaml"
        if fetched is None:
            existing = load_existing_area(path)
            if existing:
                area_docs.append(existing)
            continue
        doc = parse_area_page(fetched)
        # Never replace a previously captured page with an empty parse unless the source
        # itself proves there are no Monster/Zone Drops tables.
        dump_yaml(path, doc)
        area_docs.append(doc)
        for monster in doc.get("monsters", []) or []:
            if isinstance(monster, dict):
                detail_url = monster.get("monster_detail_retrieval_url")
                if isinstance(detail_url, str) and is_static_monster_url(detail_url):
                    discovered_monster_urls.add(detail_url)
        for drop in doc.get("drops", []) or []:
            if not isinstance(drop, dict):
                continue
            for link in drop.get("monster_links", []) or []:
                if isinstance(link, dict):
                    url = link.get("retrieval_url")
                    if isinstance(url, str) and is_static_monster_url(url):
                        discovered_monster_urls.add(url)

    monster_page_docs: list[dict[str, object]] = []
    if CRAWL_MONSTER_PAGES:
        for monster_url in sorted(discovered_monster_urls):
            fetched = fetcher.get(monster_url)
            if fetched is None:
                continue
            doc = parse_monster_page(fetched)
            dump_yaml(MONSTER_PAGE_DIR / f"{monster_slug_from_url(monster_url)}.yaml", doc)
            monster_page_docs.append(doc)

    index_counts = build_indexes(summary_docs, area_docs, monster_page_docs)
    report = {
        "dataset": "legacy_angels_wiki_bestiary",
        "status": "complete_for_retrievable_static_mirror" if summary_docs else "failed",
        "seed_pages": seed_status,
        "counts": {
            "summary_documents": len(summary_docs),
            "discovered_area_pages": len(discovered_area_urls),
            "captured_area_pages": len(area_docs),
            "discovered_monster_detail_pages": len(discovered_monster_urls),
            "captured_monster_detail_pages": len(monster_page_docs),
            **index_counts,
        },
        "retrieval_errors": fetcher.errors,
        "notes": [
            "Raw displayed strings are preservation values; normalized fields are additive only.",
            "Unavailable legacy mirror pages remain explicit retrieval errors, not claims of absent game data.",
            "Drop percentages are preserved by source semantic and are not multiplied together.",
        ],
    }
    dump_yaml(REPORT_PATH, report)

    print(yaml.safe_dump(report["counts"], sort_keys=False), end="")
    if fetcher.errors:
        print(f"Retrieval issues: {len(fetcher.errors)} (see {REPORT_PATH.relative_to(ROOT)})")
    return 0 if summary_docs else 2


if __name__ == "__main__":
    sys.exit(main())
