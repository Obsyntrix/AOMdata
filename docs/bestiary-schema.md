# Bestiary data model

## Purpose

The bestiary dataset preserves the Angels Online Wiki data used as AOmega's historical reference while also exposing machine-readable indexes for the future information hub and wiki.

The source snapshot and derived indexes are intentionally separate. A derived index must never rewrite, "fix", or silently normalize a source value.

## Source scope

Primary historical source:

- `https://angels.wikidot.com/bestiary:by-area`
- `https://angels.wikidot.com/bestiary:by-level`
- `https://angels.wikidot.com/bestiary:more-map`
- individual `monster:*` pages
- individual area pages and their `Zone Drops` tables

Because Wikidot currently redirects unreliably, `aowiki.uk/pages/area_<slug>.html` may be used as a retrieval mirror when the page explicitly identifies itself as original `angels.wikidot.com` content. In that case:

- `origin` remains `angels.wikidot.com`
- the mirror URL is recorded separately as `retrieval_url`
- mirrored values are not treated as a different game version

The newer AOWiki database is a separate evidence source and must not be silently merged into the historical Angels Wiki snapshot.

## Core record shape

Area snapshot files live under `data/bestiary/site/areas/`.

```yaml
area:
  id: spike-farm
  name_raw: "Spike Farm"
  source:
    origin_url: "https://angels.wikidot.com/area:spike-farm"
    retrieval_url: "https://www.aowiki.uk/pages/area_spike-farm.html"
    source_kind: angels_wiki_area_mirror

monsters:
  - spawn_id: spike-farm__eggshell-chick
    name_raw: "Eggshell Chick"
    level_raw: "6"
    boss_raw: "No"
    aggressive_raw: "No"
    normalized:
      level: 6
      boss: false
      aggressive: false

drops:
  - monster_name_raw: "Eggshell Chick"
    item_name_raw: "Biscuits"
    drop_share_raw: "2.0000%"
    quantity_raw: "1"
    normalized:
      drop_share_percent: 2.0
      quantity: 1
```

`*_raw` fields are the preservation fields. Normalized fields are conveniences only and may be null when a source value cannot be safely interpreted.

## Monster identity

The historical wiki contains repeated names, reused monsters, and in some cases same-name monsters with different stat profiles. Therefore an area occurrence is always independently addressable by a stable `spawn_id`:

`<area-slug>__<monster-slug>`

A later canonical entity layer may relate two spawn records when evidence proves they are the same game entity. It must not merge them merely because their display names match.

## Bestiary summary rows

The historical bestiary tables expose:

- Image
- Name
- Level
- HP
- Atk Type
- Atk
- Dfs
- Spl Atk
- Spl Dfs
- Agr.
- Exp
- Area

When captured, every displayed value is retained verbatim in `*_raw` fields. Strings such as `Boss Smn`, `No%2`, `?`, `-`, malformed punctuation, and legacy spelling are source data and are not corrected in the preservation layer.

## Individual monster pages

Individual monster pages can additionally expose:

- Type
- Boss
- Aggressive
- Level
- HP
- Rigor
- Agility
- Attack
- Defense
- Spell Attack
- Spell Defense
- Special Attack
- Special Defense
- Strong Against
- Weak Against
- Attack Range
- Speed
- Attack Speed
- Critical
- Stamina
- Soul
- Item Drop Chance
- Experience
- Gold
- item drop table

Store page values verbatim. Conflicts with bestiary summary rows are preserved as separate source observations rather than silently resolved.

## Drop semantics

The site exposes more than one kind of percentage. They must never be collapsed.

### `zone_drop_share`

Area-page `Zone Drops` tables use the heading `Drop Share`. Store the displayed percentage as `drop_share_raw` and tag its semantics as `zone_drop_share`.

### `monster_item_drop_chance`

Some individual monster pages show an overall `Item Drop Chance`. Preserve it separately as `item_drop_chance_raw`.

### `monster_drop_table_share`

Individual monster pages can also show `Chance to drop` per item. Preserve this separately from both the overall item-drop chance and area-page `Drop Share`.

Do not multiply these percentages to create a new rate unless target game data proves the relationship.

## Reverse item lookup

`data/bestiary/indexes/by-item.yaml` is derived from area drop rows and later monster-page rows. Each source entry identifies:

- item name
- monster/spawn
- area
- whether the source is a boss when known
- displayed rate
- rate semantic
- quantity
- source URL

This permits future queries such as:

`Wonder Gown -> Big Piggy -> Building Blocks County -> 48.1733%`

without losing the exact source row.

## Completeness and uncertainty

Each snapshot records capture status. Missing data is never converted into a claim that no data exists.

Recommended values:

- `complete` — source section was captured in full
- `partial` — source was available but only part was captured
- `not_present` — source page explicitly has no such section
- `unavailable` — source could not be retrieved

Question marks and dashes shown by the website remain literal raw values.

## Verification

Historical wiki data starts with:

```yaml
verification:
  client_confirmation: pending
```

Later AOmega PAK/DAT, packet, or runtime evidence may confirm or supersede a field. The historical source observation remains preserved for traceability.
