# AOMdata

Canonical research data for the AOmega Angels Online preservation project.

This repository feeds the AOmega internal information hub, future wiki, and preservation/runtime verification work.

## Core rule

**Source facts, normalized facts, and verified client/runtime facts are not the same thing.**

A community page can be useful evidence without being silently promoted to confirmed game truth. Every record keeps provenance and a verification state. When target-client data, PAK/DAT evidence, packet captures, or runtime behavior confirms or disproves a field, update the verified/normalized interpretation without erasing the historical source observation that led to it.

Do not fill unknown values by guesswork.

## Layout

```text
data/
  quests/
    index.yaml
    areas/
      <area-slug>.yaml

  bestiary/
    manifest.yaml
    crawl-report.yaml
    wayback-area-report.yaml
    wayback-monster-report.yaml
    audit.yaml
    site/
      summary/
        alphabetic.yaml
        by-area.yaml
        by-level.yaml
        more-map.yaml
      areas/
        <area-slug>.yaml
      monster-pages/
        <monster-slug>.yaml
    indexes/
      by-area.yaml
      by-level.yaml
      by-name.yaml
      by-item.yaml
      bosses.yaml

docs/
  methodology.md
  quest-schema.md
  bestiary-schema.md
```

## Bestiary query contract

The bestiary is stored as preservation snapshots plus derived indexes. Consumers should use the indexes for lookup and follow their source references back to the snapshots when displaying evidence.

Supported lookup directions are:

- area -> monsters
- level -> monsters/occurrences
- monster name -> historical appearances
- item -> documented monster/drop sources
- boss -> level/area occurrence

The reverse item index deliberately preserves ambiguity. Same-name monsters are not merged solely because their display names match.

Historical percentage types remain separate:

- area `Zone Drops` -> `zone_drop_share`
- monster-page overall `Item Drop Chance` -> `monster_item_drop_chance`
- monster-page per-item `Chance to drop` -> `monster_drop_table_share`

No effective probability is invented by multiplying those values unless target-version evidence later proves that relationship.

## Historical web provenance

The historical Angels Wiki at `angels.wikidot.com` is the source identity for the current quest and bestiary research layer.

Static `aowiki.uk/pages/...` copies are accepted only when the page identifies itself as original Angels Wikidot material. Missing historical area/monster pages may be recovered from Internet Archive snapshots of their original Wikidot URLs. Mirror and archive retrieval URLs are recorded separately from source origin.

Current AOWiki monster pages that identify themselves as client-derived 2026 data are not merged into the historical layer.

## Completeness

Crawler success is not considered proof of dataset completeness. `data/bestiary/audit.yaml` measures the actual committed source coverage and derived lookup graph. Missing pages, unresolved item-source names, ambiguous same-name relationships, question-mark values, and other source gaps remain explicit until evidence resolves them.

Historical wiki data begins with `client_confirmation: pending`. AOmega PAK/DAT, packet, and runtime evidence can later confirm or supersede interpretations without deleting the historical observation.
