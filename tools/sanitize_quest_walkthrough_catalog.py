#!/usr/bin/env python3
"""Sanitize generated quest walkthrough catalogs before they become wiki-facing data.

The raw builder intentionally keeps broad evidence. This pass is conservative:
- literal template rows such as ``Quest Name`` are removed from the quest corpus;
- a historical dedicated page is attached only when the area's quest link text names the
  current quest, or the dedicated page title exactly names the current quest;
- local canonical records may only attach from the same area;
- known research gaps can be filled only from an explicit, provenance-rich gap file.

No source evidence is deleted from the research directories. This only protects the
query/wiki-facing reconciliation layer from accidental cross-quest joins.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from urllib.parse import urlparse
import re
import yaml

ROOT = Path(__file__).resolve().parents[1]
QUEST_ROOT = ROOT / "data" / "quests"
HIST = QUEST_ROOT / "research" / "walkthrough-intake"
CAT = QUEST_ROOT / "walkthrough-catalog"
GAPS = QUEST_ROOT / "research" / "gap-corroboration.yaml"
REGIONS = ["aurora", "breeze", "steel", "dark", "dungeon", "atlantis"]


def load(path: Path) -> dict:
    if not path.exists():
        return {}
    value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return value if isinstance(value, dict) else {}


def dump(path: Path, value: object) -> None:
    path.write_text(yaml.safe_dump(value, sort_keys=False, allow_unicode=True, width=200), encoding="utf-8")


def norm(value: object) -> str:
    text = str(value or "").casefold().replace("’", "'").replace("&", " and ")
    text = re.sub(r"\b\d+\s+of\s+\d+\b", " ", text)
    text = re.sub(r"\b(?:part|step)\s*\d+\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def split_walkthrough(raw: object) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    matches = list(re.finditer(r"\bStep\s+(\d+)\b", text, re.I))
    if not matches:
        return [text]
    out: list[str] = []
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip(" :-\n\t")
        if body:
            out.append(body)
    return out


def historical_page_payload(doc: dict, matched_via: str) -> dict:
    source = doc.get("source") or {}
    return {
        "title_raw": doc.get("title_raw"),
        "summary_raw": doc.get("summary_raw"),
        "information_raw": doc.get("information_raw"),
        "requirements_raw": doc.get("requirements_raw"),
        "reward_raw": doc.get("reward_raw"),
        "walkthrough_raw": doc.get("walkthrough_raw"),
        "walkthrough_steps": doc.get("walkthrough_steps"),
        "matched_via": matched_via,
        "source": {
            "path": doc.get("_path"),
            "origin_url": source.get("origin_url"),
            "retrieval_url": source.get("retrieval_url"),
            "source_kind": source.get("source_kind"),
            "archive_timestamp": source.get("archive_timestamp"),
            "source_html_sha256": source.get("source_html_sha256"),
            "authority_role": source.get("authority_role"),
        },
    }


def load_historical_pages() -> tuple[dict[str, dict], dict[str, list[dict]]]:
    by_id: dict[str, dict] = {}
    by_title: dict[str, list[dict]] = defaultdict(list)
    for path in sorted((HIST / "quest-pages").glob("*.yaml")):
        doc = load(path)
        if not doc:
            continue
        doc["_path"] = str(path.relative_to(ROOT))
        page_id = str(doc.get("page_id") or path.stem)
        by_id[page_id] = doc
        title_key = norm(doc.get("title_raw"))
        if title_key and title_key != "angels online wiki":
            by_title[title_key].append(doc)
    return by_id, by_title


def load_area_rows() -> dict[tuple[str, str], dict]:
    out: dict[tuple[str, str], dict] = {}
    for path in sorted((HIST / "areas").glob("*.yaml")):
        doc = load(path)
        area = doc.get("area") or {}
        area_id = str(area.get("id") or path.stem)
        for row in doc.get("quest_rows", []) or []:
            if not isinstance(row, dict):
                continue
            name_key = norm(row.get("name_raw"))
            if name_key:
                out.setdefault((area_id, name_key), row)
    return out


def safe_historical_matches(*, area_id: str, quest_name: str, area_rows: dict, by_id: dict, by_title: dict) -> list[dict]:
    qkey = norm(quest_name)
    row = area_rows.get((area_id, qkey), {})
    matches: list[dict] = []
    seen_paths: set[str] = set()

    # Strongest join: the AREA QUEST TABLE itself links text naming this exact quest to a
    # dedicated quest URL. Previous/next links in the same row are deliberately ignored.
    for link in row.get("quest_links", []) or []:
        if not isinstance(link, dict) or norm(link.get("text_raw")) != qkey:
            continue
        name = Path(urlparse(str(link.get("url") or "")).path).stem
        page = by_id.get(name)
        if page is None:
            continue
        path = str(page.get("_path"))
        if path not in seen_paths:
            matches.append(historical_page_payload(page, "exact_area_quest_link_text"))
            seen_paths.add(path)

    # Fallback only when the dedicated page's own title exactly names this quest. Archived
    # pages whose H1 collapsed to "Angels Online Wiki" cannot qualify through this route.
    for page in by_title.get(qkey, []):
        path = str(page.get("_path"))
        if path not in seen_paths:
            matches.append(historical_page_payload(page, "exact_dedicated_page_title"))
            seen_paths.add(path)
    return matches


def is_template_placeholder(q: dict) -> bool:
    if norm(q.get("name")) != "quest name":
        return False
    row = ((q.get("historical_area_table") or {}).get("row_raw") or {})
    values = [str(v or "").strip().casefold() for v in row.values()]
    meaningful = [v for v in values if v and v not in {"quest name", "yes/no", "xxx faction; character lvl xx", "exp+xx, credit+xx, gold+xx, item"}]
    return not meaningful


def gap_records() -> dict[str, dict]:
    doc = load(GAPS)
    return {str(q.get("id")): q for q in doc.get("quests", []) or [] if isinstance(q, dict) and q.get("id")}


def same_area_canonical(matches: list[dict], area_id: str) -> list[dict]:
    safe = []
    for record in matches:
        path = str(record.get("path") or "")
        if path.startswith("data/quests/areas/") and Path(path).stem != area_id:
            continue
        safe.append(record)
    return safe


def choose_walkthrough(q: dict, historical: list[dict], canonical: list[dict], gap: dict | None) -> dict:
    for page in historical:
        steps = []
        for step in page.get("walkthrough_steps") or []:
            if isinstance(step, dict) and step.get("text_raw"):
                steps.append(str(step["text_raw"]))
        if not steps:
            steps = split_walkthrough(page.get("walkthrough_raw"))
        if steps:
            return {"source_class": "historical_dedicated_quest_page", "steps": steps}

    # Period guides were already matched conservatively by region/title or a high-threshold
    # fuzzy title matcher. Keep them ahead of hand-curated/area fallback evidence.
    for guide in q.get("period_guide_matches", []) or []:
        steps = [str(x) for x in guide.get("steps_raw", []) or [] if str(x).strip()]
        if steps:
            return {"source_class": "period_2012_community_guide", "steps": steps}

    for record in canonical:
        steps = [str(x) for x in record.get("walkthrough", []) or [] if str(x).strip()]
        if steps:
            return {"source_class": "existing_canonical_aomega", "steps": steps}

    if gap:
        recovered = gap.get("recovered_walkthrough") or {}
        steps = [str(x) for x in recovered.get("steps", []) or [] if str(x).strip()]
        if steps:
            return {
                "source_class": str(recovered.get("source_class") or "explicit_gap_corroboration"),
                "steps": steps,
                "caveat": recovered.get("caveat"),
            }

    short = ((q.get("historical_area_table") or {}).get("short_description_raw"))
    if short and str(short).strip() not in {"?", ""}:
        return {"source_class": "historical_area_table_minimal", "steps": [str(short)]}
    return {"source_class": "none", "steps": []}


def main() -> None:
    by_id, by_title = load_historical_pages()
    area_rows = load_area_rows()
    gaps = gap_records()
    manifest = load(CAT / "manifest.yaml")
    region_counts: dict[str, dict[str, int]] = {}
    removed_templates: list[dict[str, str]] = []

    for region in REGIONS:
        path = CAT / f"{region}.yaml"
        doc = load(path)
        sanitized: list[dict] = []
        for q in doc.get("quests", []) or []:
            if not isinstance(q, dict):
                continue
            if is_template_placeholder(q):
                removed_templates.append({"region": region, "area_id": str((q.get("area") or {}).get("id") or ""), "name": str(q.get("name") or "")})
                continue
            area_id = str((q.get("area") or {}).get("id") or "")
            historical = safe_historical_matches(area_id=area_id, quest_name=str(q.get("name") or ""), area_rows=area_rows, by_id=by_id, by_title=by_title)
            canonical = same_area_canonical(q.get("existing_canonical_matches", []) or [], area_id)
            q["historical_dedicated_pages"] = historical
            q["existing_canonical_matches"] = canonical
            q["walkthrough"] = choose_walkthrough(q, historical, canonical, gaps.get(str(q.get("id"))))
            verification = q.get("verification") or {}
            verification["dedicated_historical_page_present"] = bool(historical)
            verification["gap_corroboration_present"] = str(q.get("id")) in gaps
            q["verification"] = verification
            sanitized.append(q)

        doc["quests"] = sanitized
        counts = {
            "area_table_quest_records": len(sanitized),
            "with_historical_dedicated_page": sum(1 for q in sanitized if q.get("historical_dedicated_pages")),
            "with_period_guide_match": sum(1 for q in sanitized if q.get("period_guide_matches")),
            "with_current_client_match": sum(1 for q in sanitized if q.get("current_client_corroboration")),
            "with_gap_corroboration": sum(1 for q in sanitized if (q.get("verification") or {}).get("gap_corroboration_present")),
            "walkthrough_historical_dedicated": sum(1 for q in sanitized if (q.get("walkthrough") or {}).get("source_class") == "historical_dedicated_quest_page"),
            "walkthrough_period_guide": sum(1 for q in sanitized if (q.get("walkthrough") or {}).get("source_class") == "period_2012_community_guide"),
            "walkthrough_existing_canonical": sum(1 for q in sanitized if (q.get("walkthrough") or {}).get("source_class") == "existing_canonical_aomega"),
            "walkthrough_area_minimal": sum(1 for q in sanitized if (q.get("walkthrough") or {}).get("source_class") == "historical_area_table_minimal"),
            "walkthrough_gap_corroboration": sum(1 for q in sanitized if "corroboration" in str((q.get("walkthrough") or {}).get("source_class") or "")),
            "walkthrough_missing": sum(1 for q in sanitized if not (q.get("walkthrough") or {}).get("steps")),
        }
        doc["counts"] = counts
        doc["sanitization"] = {
            "policy": "Exact area quest-link text or exact page-title joins only; same-area canonical joins only; template placeholders removed.",
            "gap_corroboration_file": str(GAPS.relative_to(ROOT)),
        }
        region_counts[region] = counts
        dump(path, doc)

    manifest["regions"] = region_counts
    manifest["sanitization"] = {
        "applied": True,
        "removed_template_rows": removed_templates,
        "historical_join_policy": "exact area quest-link text or exact dedicated-page title",
        "canonical_join_policy": "same-area only for area records",
        "gap_corroboration_file": str(GAPS.relative_to(ROOT)),
    }
    manifest["totals"] = {
        "area_table_quest_records": sum(x["area_table_quest_records"] for x in region_counts.values()),
        "walkthrough_missing": sum(x["walkthrough_missing"] for x in region_counts.values()),
        "walkthrough_area_minimal": sum(x["walkthrough_area_minimal"] for x in region_counts.values()),
        "gap_corroborated_walkthroughs": sum(x["walkthrough_gap_corroboration"] for x in region_counts.values()),
        "removed_template_rows": len(removed_templates),
    }
    dump(CAT / "manifest.yaml", manifest)
    print(yaml.safe_dump({"regions": region_counts, "removed_template_rows": removed_templates, "totals": manifest["totals"]}, sort_keys=False, allow_unicode=True, width=200), end="")


if __name__ == "__main__":
    main()
