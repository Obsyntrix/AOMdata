# Research methodology

## Purpose

AOMdata is the canonical preservation knowledge store, not a verbatim mirror of a community wiki.

## Evidence hierarchy

As stronger evidence becomes available, prefer:

1. directly observed target-client/runtime behavior
2. target-version PAK/DAT/client data
3. target-version packet captures or server/client traces
4. authoritative first-party material for the same version
5. well-supported community documentation
6. single community claims or incomplete wiki entries

Community documentation is still preserved as evidence, but uncertain values stay uncertain.

## Normalization rules

- Never invent a missing coordinate, drop rate, reward, requirement, or quest step.
- Keep exact game-facing names where known, including awkward legacy spelling; aliases can be added separately.
- Record source conflicts in each record's `verification.conflicts`.
- `client_confirmation` remains `pending` until AOmega evidence confirms the record.
- A walkthrough synthesized from an area quest table must be marked `web_area_derived`.
- A dedicated quest page with explicit steps can be marked `web_documented`.
- Missing values stay null/omitted rather than becoming placeholders presented as facts.
- A statement that an item "drops from" a monster is not a drop percentage.

## Quest research sequence

1. Walk the area directory.
2. Read each area's NPC and quest tables.
3. Search for dedicated pages for every named quest.
4. Merge only non-conflicting details.
5. Record contradictions rather than picking silently.
6. Store one canonical per-area machine-readable bundle.
7. Later cross-reference NPCs, monsters, items, and areas to their own entity datasets.

## Bestiary phase

After quests, bestiary records use the same provenance model. Drop tables must distinguish:

- item known to drop, rate unknown
- documented rate
- client-data-defined rate
- empirically observed rate plus sample size

Those cases must never be collapsed into one value.
