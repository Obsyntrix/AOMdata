# Quest data schema

Per-area quest files live at `data/quests/areas/<area-slug>.yaml`. Cross-area/global chains live once under `data/quests/series/` and area bundles reference their stable quest IDs rather than duplicating canonical records.

## Area bundle

```yaml
schema_version: 1
area:
  id: angel-lyceum
  name: Angel Lyceum
  region: Heaven
research_status: researched
sources:
  area_page: https://...
asmodeum_quest_refs: []
quests: []
```

## Quest record

```yaml
- id: stable-slug
  name: Exact display name
  repeatable: false
  category: single | series | repeatable | training | weekly | special | card
  series:
    name: optional series name
    position: 1
    total: 4
    previous: optional-id
    next: optional-id
  giver:
    npc: NPC name
    area: Area name
    coordinates: [x, y]
  requirements:
    factions: [Aurora]
    level: {min: 15, max: null}
    rank: {min: null}
    skills: []
    prerequisite_quests: []
    availability_window: {}
  rewards:
    experience: 0
    credit: 0
    gold: 0
    credit_hours: 0
    items: []
    choose_one_item: []
  objectives:
    - type: kill | kill_combined | collect_drop | gather | produce | talk | visit | interact | choose_faction | obtain | reach_progression
      target: optional target
      count: optional integer
      item: optional item
      area: optional area
      coordinates: [x, y]
  walkthrough:
    - Human-readable step using only supported facts.
  notes: []
  related_entities:
    areas: []
    npcs: []
    monsters: []
    items: []
  verification:
    status: web_documented | web_area_derived | web_partial | web_conflict | client_confirmed
    client_confirmation: pending | confirmed | contradicted
    conflicts: []
  source_refs: [area_page]
```

Fields with unknown values should be omitted or set to null. Never use an invented value merely to satisfy the shape.

`walkthrough` is presentation-ready data for the future hub/wiki. `objectives` is the structured equivalent for filtering, cross-linking, and eventual runtime comparison.

## Canonical ownership

A quest has exactly one canonical record. If a global series crosses multiple areas, the series file owns the quest and area files contain references to it. This prevents contradictory duplicate copies when a later client-data correction is made.

## Drop-rate rule inside quest objectives

A quest objective may name a monster that drops an item. `drop_rate` remains `null` unless the percentage itself is documented by evidence. "Drops from X" must never be interpreted as 100% or any other rate.
