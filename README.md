# AOMdata

Canonical research data for the AOmega Angels Online preservation project.

This repository is intended to feed the AOmega internal information hub, a future wiki, and preservation/runtime verification work.

## Core rule

**Source facts, normalized facts, and verified client/runtime facts are not the same thing.**

A community page can be useful evidence without being silently promoted to confirmed game truth. Every record keeps provenance and a verification state. When target-client data, PAK/DAT evidence, packet captures, or runtime behavior confirms or disproves a field, update the normalized record and preserve the evidence/conflict trail.

Do not fill unknown values by guesswork.

## Layout

```text
data/
  quests/
    index.yaml
    areas/
      <area-slug>.yaml

docs/
  methodology.md
  quest-schema.md
```

The first dataset is **quests**. Bestiary data, including drop tables and drop rates, follows after quest research.

## Quest records

Quest records are machine-readable but human-reviewable. They are designed so an information hub/wiki does not need to reparse prose. Records can include stable IDs, names, repeatability, series position, quest giver, coordinates, faction/level/rank/skill/prerequisite requirements, structured objectives, rewards, walkthrough steps, related entities, provenance, conflicts, unknowns, and client-confirmation state.

## Current primary web source

- https://angels.wikidot.com/directory:areas

The source is an old community wiki and contains incomplete pages, placeholders, and occasional contradictory values. Those limitations are represented explicitly in the data rather than guessed away.
