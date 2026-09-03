# SawOS design 24 — the M4 docs sweep (unit 6, closes M4)

The ladder's last rung (#10: "§2.1 flips to BUILT, §2.2 waitable
list closes, §11 refresh, the M4 recap"), run on design 11's M3
model: an owed list with sources, a consistency grep, no code. When
this integrates, M4 CLOSES and the M5 scoping session unlocks
(designs/019 the anchor). Review WAIVED (user, Sep 2 night — "i will
take a look at the final result tomorrow morning"): this brief is
the record; the user reviews the landed sweep, not the plan.

## The owed list (each with its source)

1. **spec.md §2.1 flips to BUILT** — the ratified pipe surface is
   whole: connection (unit 1), one-shot pair (2), waitability (3),
   fused paths (3.5), handles-in-messages + delegation (4), the
   one-shot discipline (4.5), driver-as-service three-process real
   (5). The section reads today as ratified-text-plus-seven-unit-
   annotations; the sweep rewrites it as the built thing, with the
   unit history compressed to citations.
2. **§2.2 waitable list closes** — all four pipe kinds + inlet room
   level + the delivery-payload record + consuming attach + the
   completion-queue attachment; persistent-by-default (ruling 9's
   amendment) stated once, in place.
3. **§11 refresh** (design 11 §11's rescope note carries the still-
   parked F2 nod — carry it forward untouched, flag it in the
   As-built if §11's text collides with it).
4. **The M4 recap** — the milestone paragraph: what M4 set out
   (sawlang#232's ladder as amended), what landed (units 0-5 + 4.5
   + 16-stats), the trap ladder 3→2→1 with unit 5's answered/
   discarded refinement, and the standing tail unchanged.
5. **The design-10 audit**: every ruling (1-12) traced to its
   landed doc line; any promised-but-missing doc is the sweep's
   real work list (the As-built records the trace).
6. **The consistency grep** (design 11's method): stale spellings —
   `WouldBlock` as an Ok-channel answer, `Ok(None)` at resolve,
   minted one-shot siblings, `Waiter.give` phrasing vs consuming
   attach, MAX_PROCESSES=2 relics, "one trap per message" without
   the discard caveat — swept across spec.md, README, CLAUDE.md,
   kernel doc banners; each hit fixed or argued in place.

## What this unit does NOT do

No code, no transcript movement (the gate must be byte-identical
both arches), no ABI or numbering edits, no backlog closure, no M5
content beyond the recap's pointer to designs/019. The
`PipeRequestRight.Reply` coverage hole stays a backlog item — a doc
sweep does not write tests.

## The proof

`make sos-test` byte-identical; the As-built carries the audit
trace, the grep catch list in full (design 11's tradition), and the
M4-CLOSES statement the lead moves on at integration.
