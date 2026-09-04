# SawOS design 36 — M5 unit 6b: processes donate (the slot absorbs its tables)

**Status: BRIEF, Sep 4 2026 — dispatches under `designs/025` D-6
("unit 6b is its own rung") and the As-builts of designs 32/34.**
The allocator track's last rung. Arch-free in kernel/core; ONE
priced contact with the arm64 HAL (below).

## The one-sentence goal

`SlabKind` grows `Processes`: a `ProcessSlot` absorbs its parallel
`MAX_PROCESSES ×` tables (handle table, boot-handle set, quota
rows) so `PROCESSES` becomes a plain extent chain, and a deployment
that donates can run MORE CONCURRENT PROCESSES than the compiled
floor — the last of the user's three motivating kinds.

## The restructure

Today four storages are keyed by process index outside the slot:
`HANDLES` (`MAX_PROCESSES × MAX_HANDLES`), `BOOT_HANDLES` (same),
`BOOT_HANDLE_COUNT`/`_NEXT`, and `QUOTA_USED`/`QUOTA_LIMIT`
(`MAX_PROCESSES × QUOTA_KINDS`). The shape that worked twice
already (6a's "fatten one level down"): each becomes a FIELD of
`ProcessSlot` (`[HandleEntry; MAX_HANDLES]` etc. — all inner
dimensions are per-process constants, so the slot is big but
plain), and `PROCESSES: Slab<ProcessSlot, MAX_PROCESSES>` is an
ordinary chain. Access sites are mechanical renames on the same
indices (`HANDLES[p * MAX_HANDLES + h]` → `PROCESSES[p].handles[h]`);
scout for flat-index arithmetic that must unlearn the multiply.
Watch the place-window discipline (SL-17): two windows on one root
in one expression is refused — 6a's As-built records the idioms
that pass.

## The priced contact: the arm64 domain-pool wall

`static_assert(MAX_PROCESSES <= hal.PROT_DOMAIN_SLOTS)` (design 27)
binds the FLOOR, but donation moves capacity past it, and the arm64
table pool is STATIC (D-3, ruled — donation does not reach
HAL-internal storage). Two honest moves, take BOTH:

1. **The wall is advertised and enforced**: process creation beyond
   the tier's domain capacity answers `NoResource` — the machine's
   real answer on that tier, not a fault; the assert re-scopes to
   the floor (extent 0). riscv32-common already publishes 256.
2. **Raise the arm64 pool modestly**: per-process sets SHRANK at
   unit 1.5 (user windows only — measure the real per-set size
   first and record it). Pick the count that keeps the pool under
   ~64 KiB of .bss and record the arithmetic; ASID-as-slot-index
   must still fit 8 bits, which it will.

The MAX_HANDLES naming ceiling (what one process can NAME, design
32's corrected premise) is RECORDED here as the surviving
per-process bound — a handle-table growth op is deliberately NOT
this unit; note it as a seed.

## Consequences to sweep

- The teardown scans, `free_object`'s Process arm, `Stats`
  attribution, `Give`/`Start`/`BootHandleNext`, the death/reclaim
  machinery — all index through the new slot layout; bounded walks
  restated where loop bounds changed (the design-1 audit).
- `process-reclaim` and `death-late-attach` create FILLER processes
  "written against the table is full" — verify their logic still
  holds at the compiled floor with no donation (it should: donation
  never happens in those cases), and say so in the As-built.
- `MAX_ATTACHMENTS` derives from MAX_PROCESSES — check the
  derivation's meaning under donation (attachments already donate
  as their own kind since unit 6; the derived constant is only the
  FLOOR now — restate it).

## The proof

- **`process-donate`** (new): donate a region to `Processes`,
  create + start MORE concurrent processes than the compiled floor
  (a small echo child per slot proves each is real — scheduling,
  handles, teardown all exercised in extent 1), then tear them down
  and re-create (slot reuse in a donated extent, generations
  honest). On arm64 the count stays within the raised domain pool;
  if the case wants to witness the WALL (`NoResource` past the
  pool), that leg is arm64-shaped — the agent decides whether the
  witness is portable and records the choice.
- Negative leg: the maps/refs refusal conditions are already
  covered (unit 6a); nothing new owed there.

## Authorized transcript motion

The new case's rows only, both arches. Everything else
byte-identical against the current baseline (246/246, hash
`c823c1b5…`). No new floor export unless one is genuinely owed —
if `slab_donate`'s existing wrapper suffices (it should), no
image-size drift is authorized.

## Out of scope

Handle-table growth (seeded, not built); donated-slab reclamation;
any tier/HAL work beyond the priced pool raise; spec.md prose
(unit 8 — §2's Process row and §12's capacity sentences are
located-not-edited in the As-built).

## Recording duties

As-built here: the slot layout as landed, the flat-index unlearning
sites, the domain-pool measurement + chosen count, the wall's
witness (or its portability rationale), gate evidence, seeds filed.
SL entries if the language bites (verify the current highest in
todo.md before numbering). Close the tracker entry in place.
