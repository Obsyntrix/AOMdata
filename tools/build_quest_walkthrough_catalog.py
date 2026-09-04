#!/usr/bin/env python3
"""Build wiki-ready walkthrough dossiers from preserved AOmega quest evidence.

Authority order for WALKTHROUGH TEXT only:
1. Dedicated historical Angels Wiki quest page.
2. Period 2012 community guide when no dedicated walkthrough survives.
3. Existing hand-curated canonical AOmega walkthrough.
4. Historical area-table short description as the minimal fallback.

This builder does not collapse source disagreements. Raw evidence and source provenance
remain attached to every record. Current Booklet data is corroboration only.
"""

from __future__ import annotations

from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path
import re
import yaml

ROOT = Path(__file__).resolve().parents[1]
QUEST_ROOT = ROOT / "data" / "quests"
HIST = QUEST_ROOT / "research" / "walkthrough-intake"
GUIDES = QUEST_ROOT / "research" / "legacy-guides"
BOOKLET = QUEST_ROOT / "research" / "current-booklet"
OUT = QUEST_ROOT / "walkthrough-catalog"

REGIONS = ["aurora", "breeze", "steel", "dark", "dungeon", "atlantis"]
PARSE_ERRORS: list[dict[str, str]] = []


def load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        PARSE_ERRORS.append({"path": str(path.relative_to(ROOT)), "error": type(exc).__name__, "detail": str(exc)})
        return {}
    return value if isinstance(value, dict) else {}


def dump(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False, allow_unicode=True, width=200), encoding="utf-8")


def norm(value: object) -> str:
    text = str(value or "").casefold().replace("’", "'").replace("&", " and ")
    text = re.sub(r"\b\d+\s+of\s+\d+\b", " ", text)
    text = re.sub(r"\b(?:part|step)\s*\d+\b", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def slug(value: object) -> str:
    text = norm(value)
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-") or "unknown"


def split_historical_walkthrough(raw: object) -> list[str]:
    text = str(raw or "").strip()
    if not text:
        return []
    # Dedicated pages commonly render all headings into one line: "Step 1 ... Step 2 ...".
    matches = list(re.finditer(r"\bStep\s+(\d+)\b", text, re.I))
    if not matches:
        return [text]
    steps: list[str] = []
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip(" :-\n\t")
        if body:
            steps.append(body)
    return steps


def historical_pages() -> tuple[dict[str, list[dict]], dict[str, dict]]:
    by_title: dict[str, list[dict]] = defaultdict(list)
    by_id: dict[str, dict] = {}
    for path in sorted((HIST / "quest-pages").glob("*.yaml")):
        doc = load(path)
        if not doc:
            continue
        doc["_path"] = str(path.relative_to(ROOT))
        title_key = norm(doc.get("title_raw"))
        if title_key:
            by_title[title_key].append(doc)
        by_id[str(doc.get("page_id") or path.stem)] = doc
    return by_title, by_id


def canonical_records() -> tuple[dict[str, list[dict]], dict[tuple[str, str], list[dict]]]:
    by_name: dict[str, list[dict]] = defaultdict(list)
    by_area_name: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for directory, kind in [(QUEST_ROOT / "areas", "area"), (QUEST_ROOT / "series", "series")]:
        for path in sorted(directory.glob("*.yaml")):
            doc = load(path)
            area = doc.get("area") or {}
            series = doc.get("series") or {}
            for quest in doc.get("quests", []) or []:
                if not isinstance(quest, dict):
                    continue
                q = dict(quest)
                q["_path"] = str(path.relative_to(ROOT))
                q["_kind"] = kind
                q["_area_id"] = area.get("id") if kind == "area" else None
                q["_series_id"] = series.get("id") if kind == "series" else None
                key = norm(q.get("name"))
                if not key:
                    continue
                by_name[key].append(q)
                if q.get("_area_id"):
                    by_area_name[(str(q["_area_id"]), key)].append(q)
    return by_name, by_area_name


def guide_blocks() -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = defaultdict(list)
    for region in REGIONS:
        for path in sorted((GUIDES / region).glob("*.yaml")):
            doc = load(path)
            source = doc.get("source") or {}
            for block in doc.get("candidate_quest_blocks", []) or []:
                if not isinstance(block, dict):
                    continue
                result[region].append({
                    **block,
                    "_path": str(path.relative_to(ROOT)),
                    "_guide_id": doc.get("guide_id"),
                    "_source": source,
                    "_title_key": norm(block.get("title_raw")),
                })
    return result


def booklet_pages() -> dict[str, list[dict]]:
    by_title: dict[str, list[dict]] = defaultdict(list)
    if not (BOOKLET / "quest-pages").exists():
        return by_title
    for path in sorted((BOOKLET / "quest-pages").glob("*.yaml")):
        doc = load(path)
        key = norm(doc.get("name_raw"))
        if not key:
            continue
        doc["_path"] = str(path.relative_to(ROOT))
        by_title[key].append(doc)
    return by_title


def best_fuzzy(title: str, candidates: list[dict], title_field: str) -> tuple[dict | None, float]:
    target = norm(title)
    if not target:
        return None, 0.0
    scored = []
    for candidate in candidates:
        ck = norm(candidate.get(title_field))
        if not ck:
            continue
        score = SequenceMatcher(None, target, ck).ratio()
        scored.append((score, candidate))
    if not scored:
        return None, 0.0
    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best = scored[0]
    second = scored[1][0] if len(scored) > 1 else 0.0
    # Conservative: do not turn a vaguely similar title into a false merge.
    if best_score >= 0.86 and (best_score - second >= 0.06 or best_score >= 0.96):
        return best, best_score
    return None, best_score


def select_historical_page(row: dict, by_title: dict[str, list[dict]], by_id: dict[str, dict]) -> list[dict]:
    matches: list[dict] = []
    for link in row.get("quest_links", []) or []:
        if not isinstance(link, dict):
            continue
        page_id = Path(str(link.get("url") or "")).stem
        page = by_id.get(page_id)
        if page is not None and page not in matches:
            matches.append(page)
    for page in by_title.get(norm(row.get("name_raw")), []):
        if page not in matches:
            matches.append(page)
    return matches


def source_ref(source: object, path: object = None) -> dict[str, object]:
    s = source if isinstance(source, dict) else {}
    return {
        "path": path,
        "origin_url": s.get("origin_url"),
        "retrieval_url": s.get("retrieval_url"),
        "source_kind": s.get("source_kind"),
        "archive_timestamp": s.get("archive_timestamp"),
        "source_html_sha256": s.get("source_html_sha256"),
        "authority_role": s.get("authority_role"),
    }


def main() -> None:
    hist_by_title, hist_by_id = historical_pages()
    canonical_by_name, canonical_by_area_name = canonical_records()
    guides_by_region = guide_blocks()
    booklet_by_title = booklet_pages()

    manifest_regions: dict[str, dict[str, int]] = {}
    total_records = 0
    total_primary_walkthrough = 0
    total_guide_walkthrough = 0
    total_minimal = 0

    for region in REGIONS:
        records: list[dict] = []
        seen: set[tuple[str, str]] = set()
        region_area_docs = []
        for path in sorted((HIST / "areas").glob("*.yaml")):
            doc = load(path)
            area = doc.get("area") or {}
            if area.get("region") == region:
                doc["_path"] = str(path.relative_to(ROOT))
                region_area_docs.append(doc)

        matched_hist_paths: set[str] = set()
        matched_guide_paths_titles: set[tuple[str, str]] = set()

        for area_doc in region_area_docs:
            area = area_doc.get("area") or {}
            area_id = str(area.get("id") or "")
            area_name = area.get("name_raw") or area_id
            area_source = area_doc.get("source") or {}
            for row in area_doc.get("quest_rows", []) or []:
                if not isinstance(row, dict):
                    continue
                title = str(row.get("name_raw") or "").strip()
                title_key = norm(title)
                identity = (area_id, title_key)
                if not title_key or identity in seen:
                    continue
                seen.add(identity)

                hist_pages = select_historical_page(row, hist_by_title, hist_by_id)
                for page in hist_pages:
                    matched_hist_paths.add(str(page.get("_path")))

                canon = canonical_by_area_name.get(identity, []) or canonical_by_name.get(title_key, [])

                guide_matches = [g for g in guides_by_region.get(region, []) if g.get("_title_key") == title_key]
                fuzzy_guide_score = None
                if not guide_matches:
                    fuzzy, score = best_fuzzy(title, guides_by_region.get(region, []), "title_raw")
                    if fuzzy is not None:
                        guide_matches = [fuzzy]
                        fuzzy_guide_score = round(score, 4)
                for guide in guide_matches:
                    matched_guide_paths_titles.add((str(guide.get("_path")), str(guide.get("title_raw"))))

                booklet = booklet_by_title.get(title_key, [])

                walkthrough_steps: list[str] = []
                walkthrough_source = "none"
                selected_hist = next((p for p in hist_pages if p.get("walkthrough_steps") or p.get("walkthrough_raw")), None)
                if selected_hist is not None:
                    explicit = selected_hist.get("walkthrough_steps") or []
                    if explicit:
                        for step in explicit:
                            if isinstance(step, dict) and step.get("text_raw"):
                                walkthrough_steps.append(str(step["text_raw"]))
                    if not walkthrough_steps:
                        walkthrough_steps = split_historical_walkthrough(selected_hist.get("walkthrough_raw"))
                    if walkthrough_steps:
                        walkthrough_source = "historical_dedicated_quest_page"
                        total_primary_walkthrough += 1

                if not walkthrough_steps and guide_matches:
                    steps = guide_matches[0].get("steps_raw") or []
                    walkthrough_steps = [str(x) for x in steps if str(x).strip()]
                    if walkthrough_steps:
                        walkthrough_source = "period_2012_community_guide"
                        total_guide_walkthrough += 1

                if not walkthrough_steps:
                    canon_with_walkthrough = next((q for q in canon if q.get("walkthrough")), None)
                    if canon_with_walkthrough:
                        walkthrough_steps = [str(x) for x in canon_with_walkthrough.get("walkthrough", []) if str(x).strip()]
                        if walkthrough_steps:
                            walkthrough_source = "existing_canonical_aomega"

                if not walkthrough_steps:
                    short = (row.get("row_raw") or {}).get("Short Description") if isinstance(row.get("row_raw"), dict) else None
                    if short:
                        walkthrough_steps = [str(short)]
                        walkthrough_source = "historical_area_table_minimal"
                        total_minimal += 1

                row_raw = row.get("row_raw") if isinstance(row.get("row_raw"), dict) else {}
                record = {
                    "id": f"{area_id}-{slug(title)}",
                    "name": title,
                    "region": region,
                    "area": {"id": area_id, "name": area_name},
                    "historical_area_table": {
                        "repeatable_raw": row_raw.get("Repeat."),
                        "requirements_raw": row_raw.get("Requirements"),
                        "reward_raw": row_raw.get("Reward"),
                        "short_description_raw": row_raw.get("Short Description"),
                        "row_raw": row_raw,
                        "source": source_ref(area_source, area_doc.get("_path")),
                    },
                    "walkthrough": {
                        "source_class": walkthrough_source,
                        "steps": walkthrough_steps,
                    },
                    "historical_dedicated_pages": [
                        {
                            "title_raw": p.get("title_raw"),
                            "summary_raw": p.get("summary_raw"),
                            "information_raw": p.get("information_raw"),
                            "requirements_raw": p.get("requirements_raw"),
                            "reward_raw": p.get("reward_raw"),
                            "walkthrough_raw": p.get("walkthrough_raw"),
                            "walkthrough_steps": p.get("walkthrough_steps"),
                            "source": source_ref(p.get("source"), p.get("_path")),
                        }
                        for p in hist_pages
                    ],
                    "period_guide_matches": [
                        {
                            "title_raw": g.get("title_raw"),
                            "section_raw": g.get("section_raw"),
                            "steps_raw": g.get("steps_raw"),
                            "fuzzy_match_score": fuzzy_guide_score,
                            "source": source_ref(g.get("_source"), g.get("_path")),
                        }
                        for g in guide_matches
                    ],
                    "existing_canonical_matches": [
                        {
                            "id": q.get("id"),
                            "path": q.get("_path"),
                            "category": q.get("category"),
                            "repeatable": q.get("repeatable"),
                            "giver": q.get("giver"),
                            "requirements": q.get("requirements"),
                            "rewards": q.get("rewards"),
                            "objectives": q.get("objectives"),
                            "walkthrough": q.get("walkthrough"),
                            "verification": q.get("verification"),
                        }
                        for q in canon
                    ],
                    "current_client_corroboration": [
                        {
                            "quest_id": q.get("quest_id"),
                            "name_raw": q.get("name_raw"),
                            "basic_fields_raw": q.get("basic_fields_raw"),
                            "quest_flow_rows_raw": q.get("quest_flow_rows_raw"),
                            "quest_flow_text_raw": q.get("quest_flow_text_raw"),
                            "map_links": q.get("map_links"),
                            "source": source_ref(q.get("source"), q.get("_path")),
                        }
                        for q in booklet
                    ],
                    "verification": {
                        "historical_primary_present": True,
                        "dedicated_historical_page_present": bool(hist_pages),
                        "period_guide_present": bool(guide_matches),
                        "current_client_match_present": bool(booklet),
                        "aomega_client_confirmation": "pending",
                    },
                }
                records.append(record)

        # Preserve dedicated historical pages that do not map to an area-table row. These are
        # often faction/world chains and are essential to a complete walkthrough collection.
        supplemental_hist = []
        for pages in hist_by_title.values():
            for page in pages:
                path = str(page.get("_path"))
                if path in matched_hist_paths:
                    continue
                matches = page.get("matched_target_areas") or []
                if not any(isinstance(m, dict) and m.get("region") == region for m in matches):
                    continue
                supplemental_hist.append({
                    "title_raw": page.get("title_raw"),
                    "summary_raw": page.get("summary_raw"),
                    "requirements_raw": page.get("requirements_raw"),
                    "reward_raw": page.get("reward_raw"),
                    "walkthrough_steps": split_historical_walkthrough(page.get("walkthrough_raw")) or page.get("walkthrough_steps"),
                    "matched_target_areas": matches,
                    "source": source_ref(page.get("source"), path),
                })

        supplemental_guides = []
        for guide in guides_by_region.get(region, []):
            identity = (str(guide.get("_path")), str(guide.get("title_raw")))
            if identity in matched_guide_paths_titles:
                continue
            supplemental_guides.append({
                "title_raw": guide.get("title_raw"),
                "section_raw": guide.get("section_raw"),
                "steps_raw": guide.get("steps_raw"),
                "source": source_ref(guide.get("_source"), guide.get("_path")),
            })

        counts = {
            "area_table_quest_records": len(records),
            "with_historical_dedicated_page": sum(1 for r in records if r["verification"]["dedicated_historical_page_present"]),
            "with_period_guide_match": sum(1 for r in records if r["verification"]["period_guide_present"]),
            "with_current_client_match": sum(1 for r in records if r["verification"]["current_client_match_present"]),
            "walkthrough_historical_dedicated": sum(1 for r in records if r["walkthrough"]["source_class"] == "historical_dedicated_quest_page"),
            "walkthrough_period_guide": sum(1 for r in records if r["walkthrough"]["source_class"] == "period_2012_community_guide"),
            "walkthrough_existing_canonical": sum(1 for r in records if r["walkthrough"]["source_class"] == "existing_canonical_aomega"),
            "walkthrough_area_minimal": sum(1 for r in records if r["walkthrough"]["source_class"] == "historical_area_table_minimal"),
            "walkthrough_missing": sum(1 for r in records if not r["walkthrough"]["steps"]),
            "supplemental_historical_pages": len(supplemental_hist),
            "supplemental_period_guide_blocks": len(supplemental_guides),
        }
        manifest_regions[region] = counts
        total_records += len(records)
        dump(OUT / f"{region}.yaml", {
            "schema_version": 1,
            "dataset": "aomega_walkthrough_catalog",
            "region": region,
            "authority_policy": {
                "historical_angels_wiki": "primary",
                "period_2012_community_guides": "secondary_historical",
                "current_booklet_client_database": "secondary_cross_version_corroboration_only",
                "aomega_target_client": "final_verification_pending",
            },
            "counts": counts,
            "quests": records,
            "supplemental_historical_quest_pages": supplemental_hist,
            "supplemental_period_guide_blocks": supplemental_guides,
        })

    dump(OUT / "manifest.yaml", {
        "schema_version": 1,
        "dataset": "aomega_walkthrough_catalog_manifest",
        "scope": REGIONS,
        "regions": manifest_regions,
        "totals": {
            "area_table_quest_records": total_records,
            "historical_dedicated_walkthroughs_selected": total_primary_walkthrough,
            "period_guide_walkthroughs_selected": total_guide_walkthrough,
            "area_minimal_walkthroughs_selected": total_minimal,
            "yaml_parse_errors": len(PARSE_ERRORS),
        },
        "yaml_parse_errors": PARSE_ERRORS,
        "notes": [
            "The catalog is a reconciliation/query layer, not permission to erase contradictory source observations.",
            "A quest can appear in both an area table and a cross-area/faction series. Supplemental pages preserve those relationships until canonical series reconciliation is complete.",
            "Current Booklet evidence never becomes historical truth solely because its structure is cleaner.",
        ],
    })
    print(yaml.safe_dump({"regions": manifest_regions, "yaml_parse_errors": PARSE_ERRORS}, sort_keys=False, allow_unicode=True, width=200), end="")


if __name__ == "__main__":
    main()
