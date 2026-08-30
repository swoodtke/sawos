# SawOS design 11 — the M3 docs sweep (M3 unit 7, the close)

Status: AUTHORED Aug 30 2026 (lead). The milestone's last unit —
sawlang#232 item 7 plus the debts individual units recorded and
deferred here. DOCS ONLY: spec.md and doc comments; no op, no right,
no behavior. Transcript expectation: **ZERO moved rows** — the gate
still runs (doc comments live in compiled files), and every row must
come back byte-identical.

## The owed list (each with its source)

1. **§3 FULL REWRITE** (owed since the Aug-17 handle-lifecycle
   ruling; sawlang#232 "THE §3 REWRITE THIS OWES"): the no-duplicate
   rule's real invariant was never uniqueness — it is **NO
   AMPLIFICATION**. A mint always carries the kind's DEFAULT set (or
   narrows it by keep mask); minting authority is itself rights-gated;
   attenuating a handle you give away is meaningful exactly when the
   receiver lacks its own minting authority. State it with the clock
   example so mint-per-call is never read as a hole in attenuation.
   Fold in what M3 actually built: MINT_OP universal, keep masks,
   give-moves-rights-verbatim, and unit 6's exec-gate as the worked
   example of policy-by-mask. §3 still describes the pre-2.75 world;
   this is a REWRITE, not an amendment.
2. **§2.3 Memory rename** (sawlang#232 item 7): the section still
   speaks the Jul-29 draft's vocabulary; align it with the built
   §2.5 surface (Memory/IoMemory/Mapping, split/carve/map/give) and
   point at the shared-memory demonstration that now EXISTS
   (share-double-map — two processes, one region, proven bytes).
3. **§5.7 amendment** (sawlang#232 item 7): rights-gated ops as
   built; the boot set stays ONE HANDLE WIDE at the entry register
   (everything else drains); the copy funnels as the only
   user-memory doors.
4. **The DMA-TCB note** (178 round 4, carried by 232): a DMA-capable
   device's driver is inside the TCB until an IOMMU exists — the
   sentence the M4+ IOMMU item hangs off. Place it where device
   grants are ruled (§2.5/§9 seam).
5. **§11 REFRESH for M3**: the full what-is-built ledger pass. Unit
   6 flipped its own rows and the roadmap line; the sweep OWNS the
   whole section — every M3 unit's rows present and accurate, counts
   current (158/79×2), stale "will"/"remains" sentences flipped or
   struck, M4 pointed at designs/010 (the ruled plan of record).
6. **The consistency pass the per-unit edits could not do**: grep
   for stale promises across spec.md and the tree's doc comments —
   "unit 5.5 will", "M3 will", "not yet built", the §8 kill/thread
   deferrals stated once and correctly, `Process.attach` mentions
   (all should be gone — the respell landed), `SegFlag.Device`
   dormancy stated where the loader documents it. Fix what the grep
   finds; list what was found in the As-built.

## What this unit does NOT do

No behavior, no renumbering, no test changes, no new sections for
M4 (design 10 is the M4 record; spec §2.1 gains nothing until M4
units land it), no HANDOFF/tracker archaeology beyond the entry this
brief adds.

## The proof

The gate, both arches, EVERY row byte-identical — a docs unit that
moves a transcript row has changed something it claimed not to.
The As-built lists every section touched and every stale sentence
the consistency grep caught.

## Docs owed (meta)

Tracker entry closed in place; As-built here filled; SL-11+ only if
a genuine language deficiency is met (unlikely in prose).

## As built

(Implementer: sections as landed, the grep's catch list, findings.)
