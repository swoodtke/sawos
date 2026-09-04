# SawOS design 37 — M5 unit 7: the shared stats region (the userspace top)

**Status: BRIEF, Sep 4 2026 — dispatches under `designs/025` (RULED:
seed 1 IN as a late unit) and design 16's recorded future shape.**
The milestone's own demo, the way the uart service was M4's. Runs
after units 4 + 6b merge; baseline is the THREE-profile gate.

## The one-sentence goal

The per-process trap counters live in ONE kernel-owned page a
process maps READ-ONLY — a purely-userspace `top`, reads free — with
the record layout published in `sosabi`, the op-shaped `Stats` v1
kept as the compatibility floor, and the counters updated by the
SAME increments that exist today (one storage, no mirror to skew).

## The shape

- **The storage moves, the counting sites do not.** The three
  columns (`syscalls`/`interrupts`/`faults`) relocate from
  `ProcessSlot` into a kernel-owned, page-aligned static region
  (`STATS_REGION`), laid out as a published record: a header (slot
  count, layout version word) + one row per process slot. Trap-entry
  attribution writes there; `ProcessOp.Stats` READS there — v1's
  surface answers identical numbers from the new home, so no
  transcript row moves for the relocation.
- **The capability**: a `SystemOp` getter (`StatsRegionMap`-shaped,
  its own `SystemRight`, root set) that INSTALLS an RO row of the
  region into the CALLER and answers `(va, Mapping)` in `map`'s
  existing shape. RO is enforced where the tier can (Isolated); on
  Flat the word already disclaims denial — nothing new to say.
  Attenuation: the right travels by `give`/mint like any bit; a
  process without it simply cannot map the region.
- **Donated process slots** (unit 6b) must be covered: decide the
  region's row capacity honestly — the compiled floor's rows in the
  static page, with rows-beyond-floor either (a) a second donated
  page recorded as a follow-on, or (b) the region sized for
  `PROT_DOMAIN_SLOTS`-scale counts up front. Scout, pick, record.
- **32-bit tearing, stated not hidden**: counters are `UInt64`; a
  32-bit reader can tear a word. v1's answer is DOCUMENTATION plus
  monotonicity (a torn read is between two true values of the low
  word; the columns are printed-not-asserted for exactly the
  volatile one). A seqlock is recorded as the refinement if a
  consumer ever needs exactness — do not build it.

## The proof

- **`stats-region`** (new): root maps the region, spins a known
  number of syscalls, and asserts its own row's `syscalls` moved by
  the region-read matching a `Stats`-op read (the two surfaces
  agree — the compatibility floor witnessed). A child with the
  right maps and READS root's row (cross-process visibility — the
  `top` shape). The RO half: the child attempts a write and faults
  — an ISOLATED-tier leg, excluded by name on flat per unit 4's
  machinery.
- Existing `process_stats` rows must not move (same numbers, new
  home).

## Authorized transcript motion

New-case rows only, all three profiles' sections (with the RO-write
leg Isolated-only). Everything else byte-identical against the
post-unit-4 baseline. No new floor `@export` (unit 4's finding
stands: it breaks the fence; the typed surface suffices).

## Out of scope

Per-thread stats (still waiting for a consumer); the seqlock; the
buffered `debug_print` backlog item (restate its interrupt-column
note here if touched); spec.md prose (unit 8 — design 16's
"recorded future shape" paragraph is located for it).

## Recording duties

As-built here: the layout as published, the capacity decision, the
tearing statement's wording, gate evidence, findings. SL entries if
the language bites (verify the highest — SL-25 exists). Close the
tracker entry in place.
