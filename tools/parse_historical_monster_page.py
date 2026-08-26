#!/usr/bin/env python3
"""Parse an archived historical Angels Wiki monster page without losing table fields.

Wikidot monster statistics are commonly laid out as alternating multi-column header and
value rows. The generic crawler's two-cell field extraction is insufficient for that
shape, so this module preserves every rendered row and additionally maps recognizable
header/value groups into ``fields_raw``. Raw strings remain authoritative.
"""

from __future__ import annotations

from bs4 import Tag

import crawl_legacy_bestiary as legacy


def direct_cells(row: Tag) -> list[Tag]:
    return row.find_all(["th", "td"], recursive=False)


def rendered_rows(table: Tag) -> list[list[str]]:
    out: list[list[str]] = []
    for row in table.find_all("tr"):
        cells = direct_cells(row)
        if cells:
            out.append([legacy.clean_text(cell) for cell in cells])
    return out


def is_drop_header(values: list[str]) -> bool:
    keys = {legacy.header_key(value) for value in values}
    return bool(keys & {"item", "item name", "drop"}) and bool(
        keys & {"chance to drop", "drop share", "drop chance"}
    )


def extract_grouped_fields(table: Tag, fields_raw: dict[str, str]) -> None:
    rows = table.find_all("tr")
    for index, row in enumerate(rows):
        cells = direct_cells(row)
        if not cells:
            continue

        # Traditional two-column key/value row.
        if len(cells) == 2:
            key = legacy.clean_text(cells[0])
            value = legacy.clean_text(cells[1])
            if key and value and len(key) < 100:
                fields_raw.setdefault(key, value)

        # Wikidot stat blocks use a header row followed by an equally sized value row:
        # Level | Hit Points | Rigor | Agility
        # 17    | 6524       | 147   | 93
        if index + 1 >= len(rows):
            continue
        next_cells = direct_cells(rows[index + 1])
        if len(cells) != len(next_cells) or not cells:
            continue

        # Header cells normally render as <th>. Requiring at least one TH avoids treating
        # arbitrary data rows as field names while still tolerating mixed legacy markup.
        if not any(cell.name == "th" for cell in cells):
            continue
        labels = [legacy.clean_text(cell) for cell in cells]
        values = [legacy.clean_text(cell) for cell in next_cells]
        if is_drop_header(labels):
            continue
        for key, value in zip(labels, values):
            if key and value and len(key) < 100:
                fields_raw.setdefault(key, value)


def parse_monster_page(fetched: legacy.Fetched, slug: str) -> dict[str, object]:
    title = legacy.page_title(fetched.soup, slug.replace("-", " ").title())
    fields_raw: dict[str, str] = {}
    tables_raw: list[dict[str, object]] = []
    drops: list[dict[str, object]] = []

    for table_index, table in enumerate(fetched.soup.find_all("table"), start=1):
        rows_raw = rendered_rows(table)
        headers, rows = legacy.table_matrix(table)
        tables_raw.append({
            "table_index": table_index,
            "headers_raw": headers,
            "rows_raw": rows_raw,
            "row_count": len(rows_raw),
        })
        extract_grouped_fields(table, fields_raw)
        if not headers:
            continue

        monster_idx = legacy.column_index(headers, "Monster", "Monster Name")
        item_idx = legacy.column_index(headers, "Item", "Item Name", "Drop")
        rate_idx = legacy.column_index(headers, "Chance to drop", "Chance To Drop", "Drop Share", "Drop Chance")
        qty_idx = legacy.column_index(headers, "Quantity", "Qty", "Amount")
        if item_idx is None or rate_idx is None:
            continue

        for row_number, (cells, values) in enumerate(rows, start=1):
            item_name_raw = legacy.row_value(values, item_idx)
            if not item_name_raw:
                continue
            monster_name_raw = legacy.row_value(values, monster_idx) or title
            rate_raw = legacy.row_value(values, rate_idx)
            quantity_raw = legacy.row_value(values, qty_idx)
            drops.append({
                "drop_id": f"{slug}__t{table_index:03d}r{row_number:04d}",
                "monster_name_raw": monster_name_raw,
                "item_name_raw": item_name_raw,
                "chance_to_drop_raw": rate_raw,
                "quantity_raw": quantity_raw,
                "rate_semantic": "monster_drop_table_share",
                "row_raw": {
                    headers[i]: values[i] if i < len(values) else ""
                    for i in range(len(headers))
                },
                "normalized": {
                    "chance_to_drop_percent": legacy.parse_float_percent(rate_raw),
                    "quantity": legacy.parse_int(quantity_raw),
                },
            })

    item_drop_chance_raw = None
    for key, value in fields_raw.items():
        if legacy.header_key(key) == "item drop chance":
            item_drop_chance_raw = value
            break

    return {
        "monster_page": {
            "id": slug,
            "name_raw": title,
            "verification": {"client_confirmation": "pending"},
        },
        "fields_raw": fields_raw,
        "item_drop_chance_raw": item_drop_chance_raw,
        "drops": drops,
        "source_tables": tables_raw,
    }
