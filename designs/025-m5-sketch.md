# SawOS design 25 — M5 SKETCH: the memory milestone (agenda for the scoping session)

**Status: RULED Sep 3 2026 (user, the scoping session — all eight
agenda items; see "As ruled" at the end) — THE PLAN OF RECORD for
M5.** Drafted the same day as the session, the design-10 pattern.
**Anchor: `designs/019-m5-memory-story.md` (RULED Sep 2)**, carried
whole and not reopened here: one idiom set (alloc → map → unmap)
over three protection tiers, difference advertised never faked,
`load_domain` the one seam. **The opening item was USER-RULED ahead
of the session (Sep 2 night): Tier 1 — MMU configuration,
per-process address spaces over the existing `load_domain` seam,
arm64-virt the first climb — with riscv Sv32 following.**

## What is carried whole (not on the agenda)

- Design 19 entire: the tier definitions, tier-3 honesty (`map`
  trivially true, denial the platform attribute, the kernel boundary
  surviving any hardware line), the tier-2 16-slot floor, the flat
  tier as a BUILD PROFILE of the existing virt boards first.
- §2.5 as built: `Memory`/`IoMemory`/`Mapping`, split/carve/map/give,
  executable-as-a-right, the W^X refusal, object-counted quotas, the
  refcount, a Mapping owning its row.
- The seam's law: live-domain rule, replay-by-index compaction,
  no-preemption-point in the `prot_*` walks.
- §2.1/§2.2 and everything M4 closed. This milestone adds no IPC.

## The ground truth Tier 1 climbs from (verified Sep 3, tree at `56ad00e`)

**arm64-virt today is an MMU worn as an MPU.** One table set built at
boot, shared by every process: identity 2 MiB EL1-only blocks over
RAM, a 4 MiB GRANT WINDOW (`0x4000_0000..0x4040_0000`) of level-3
pages whose EL0 permission bits are the protection, plus a device
level-3 window for the console page. `load_domain` = reset both
windows + replay the process's grant rows + commit (~1.6k descriptor
stores and a full TLB flush per process switch, accepted at M3 switch
rates, design 2 D-5). No ASIDs. `run_thread` skips equal domains —
design 2 D-5's marked point, the seat Tier 1 was always going to take.

**Addresses are identity everywhere** (§5.5: an address is the same
number in every process; the §2 Mapping row: "no virtual half").
Processes therefore link at build-time distinct bases — root
`0x4020_0000`, child `0x4024_0000`, child2 `0x4028_0000` — via
per-child `.ld` scripts whose arithmetic `tools/sos_runner.py`
duplicates by design.

**riscv32-virt today is PMP**: numbered regions replayed per switch
through the same seam (`hal.GRANT_ROW_BUDGET`, static-asserted ≤ 9).
QEMU virt has S-mode and Sv32; the C3-class parts do not (M+U only),
which is exactly design 19's tier split.

**Where the kernel's RAM actually goes (measured Sep 3, `llvm-nm`
over the riscv32 kernel image, .bss 152,176 B).** The object slabs —
all fifteen kinds, plus handle tables, boot-handle sets, attachments
and quota tables — total **~5 KiB**; with the pipe rendezvous
buffers (`PIPE_BODIES` 8 KiB, `PIPE_MSG_HELD` 3 KiB) everything
object-shaped is ~16 KiB. The dominant items are the **64 KiB kernel
boot stack** (a linker-script constant) and the **64 KiB `sosrt`
ARENA** — which is also linked into EVERY process image (design 20
finding 4: the child is 67.6 KB, 65.5 KB of it arena). So on a
400 KB part the RAM story is two constants, not the slab
architecture — and conversely, the slabs' cheapness is BECAUSE the
limits are suite-sized (`MAX_PIPES` 4, `MAX_PROCESSES` 3), which is
its own problem: the compiled constants forbid a REAL system. Both
facts drive the seed rulings below.

## D-1: what Tier 1 does to §5.5 — and the two-step proposal

Design 19 defines Tier 1 as "page tables, VA placement on `map`,
many mappings". The portable idiom is already ruled: **a mapping's
address is the ANSWER — programs treat it as data**, which the
tree's programs already do. The open question is staging, because
"per-process tables" and "the kernel chooses your addresses" are
separable, and the gate tradition wants the mechanism swap witnessed
alone before the semantics move:

- **Step one — TRANSLATION AS ISOLATION (identity placement).**
  Per-process PERSISTENT table sets; `load_domain` collapses to a
  TTBR0+ASID write (the ~1.6k-store replay retires); a process's
  tables map ONLY its own grants, so peer isolation is by absence
  rather than by permission bits in a shared window. VAs stay
  identity: §5.5 survives verbatim, the `.ld` scripts survive, every
  transcript can stay BYTE-IDENTICAL. The observable difference is
  the switch cost and the isolation proofs' mechanism.
- **Step two — PLACEMENT (the address becomes the kernel's answer).**
  `map` places; every image links at ONE base and the per-child `.ld`
  scripts + the runner's base arithmetic collapse; §5.5 re-scopes to
  tiers 2/3 ("identity is the tier-2 answer, not the system's law");
  shared regions may sit at per-process VAs, amending §2.5's
  "shared at its own address" sentence to tier vocabulary. This is
  also the step vDSO true-mapping actually wants.

**Proposed ruling: take both, in that order, as separate units.**
Open sub-questions for the session: (a) does step two land in M5 or
is step one + Sv32 enough for the milestone's isolation claim; (b) on
step two, does `map` take a VA HINT or answer unconditionally
(proposal: no hint — the address is the answer, callers hold no
opinion the tiers can't all honor).

## D-2: the seam under persistence — edits, not just switches

Today the HAL's `prot_*` surface is REPLAY-SHAPED: the kernel-side
grant record is authoritative and the hardware image is rebuilt at
every switch, so an edit to a non-current domain costs nothing and
the live-domain rule (`domain_changed`) handles the current one.
Persistent per-process tables invert that: the table IS the standing
image, so

- `map`/`unmap` must reach the TABLE of the target process at edit
  time (a new HAL pair — `prot_install(p, row)` / `prot_remove(p,
  row)` or similar), not only the record;
- the live-domain rule's reload becomes TLB MAINTENANCE (invalidate
  by ASID/page) rather than a window rebuild;
- `load_domain` becomes design 19's "table+ASID switch" and touches
  no descriptors.

**Proposed ruling:** the seam grows the edit pair; the MPU/flat HALs
implement it as record-append + the existing replay (nothing moves
for them); the grant record STAYS authoritative everywhere (it is
what teardown and compaction walk). Replay-at-switch remains the
MPU tier's shape, not a permitted MMU implementation — an ASID
means nothing if tables are rebuilt under it.

## D-3: page-table provenance (zero-dynamic-allocation holds)

Per-process table sets need pages. **Proposed ruling: static
per-process table pools in the HAL** — MAX_PROCESSES × a fixed table
budget, the same species of number as today's grant window, sized at
the same 4 MiB span. The growable-slab donation op (010 ruling 10's
seed) is NOT a Tier-1 dependency and stays on the seed list; what
tier 1 changes is only which constant prices `DEFAULT_QUOTA_MAPPINGS`
(a table-pool budget rather than a slot count). Kernel placement
under translation is HAL-INTERNAL (arm64: kernel blocks in each set
or TTBR1; Sv32: global-bit kernel rows) — the seam and the spec never
see it.

## D-4: the tier word

Design 19: a platform attribute — `Isolated` / `Flat` — advertised
in the boot info / on System, checked once by a program that relies
on isolation; `unmap`'s revocation half is what `Flat` disclaims.
For the session: (a) the spelling and the surface (proposal: a
`SystemOp` getter beside `ClockGet`, plus the boot info record —
the op is the checkable one); (b) confirm TWO words only — MMU vs
MPU is not advertised, because slot pricing and the map answer are
already the visible difference and a third word would invite
tier-sniffing.

## D-5: the real allocator — spent bytes come back (RULED IN, user, Sep 3)

The tree's genuinely improper memory management: a RAM pool is a
one-way front-cut cursor, a freed region returns its slab SLOT but
its RANGE returns to no pool, and a system with process churn leaks
physical RAM permanently. §2.5 quotes the deferral honestly; the
§5.9 pin has waited on "a real allocator" since M1. **The unit:**

- **Return-on-last-drop returns the RANGE**: when a Memory's count
  reaches zero (the existing synchronous free), its `[base, len)`
  goes onto its POOL ROOT's free list; `split` allocates from the
  free list first (best/first-fit — the unit's call), falling back
  to the cursor; adjacent free ranges COALESCE.
- **Each Memory knows its pool root** (a slab-slot back-reference,
  set at split, carried through re-splits) — that is the whole of
  the new per-object state.
- **Byte accounting** rides the same bookkeeping: a pool root can
  answer bytes-outstanding vs bytes-free, which is what §5.9's
  "byte accounting" has always meant. Quotas STAY object-counted;
  bytes are the pool's ledger, not a per-process authority.
- **Metadata storage**: free-list nodes are a new fixed slab in v1
  — and the first customer of D-6's donation when it runs dry.
- `IoMemory` is untouched (carve leaves the parent whole; MMIO is
  pinned; its count stays effectively pinned — §5.9's own carve-out,
  now stated where the mechanism lives).
- **§5.9 / F2 CLOSES with this unit** — the pin's answer is built,
  not re-worded a third time.

**RULED (user, Sep 3): RETURN TO ROOT** — the derivation chain is a
cursor artifact, not an ownership tree. **And the release condition
is ruled with it: the range returns to the pool only when BOTH
counts are zero — memory handles AND mappings.** This is §2.5's
quoted refcount invariant ("freed only when the LAST reference of
either kind — any MemoryObject handle OR any Mapping — drops")
finally coming true as written: the unit tracks both against the
region and the pool return fires on the second zero, so a
mapped-but-handle-free region stays out of the pool until its rows
are gone. Still open: fit policy (first-fit proposed — the free
list is short and coalescing does the real work).

## D-6: slab donation — capacity becomes policy (RULED IN, user, Sep 3)

The user's motivation, recorded: the compiled limits are suite-sized
and FORBID a real system — many processes, pipes, threads — and the
namespace vision (out of M5 itself) needs that ceiling gone before
it can ever be built. 010 ruling 10's seed graduates to a unit:

- **The op**: root (or any holder the rights allow) DONATES a Memory
  region to the kernel to back a named KIND's slab —
  `SystemOp.SlabDonate(kind, memory)`-shaped, on a `SystemRight` of
  its own. The capability is CONSUMED (a move, like `give`);
  donation is PERMANENT in v1 (the mapping-leak stance: safe, never
  reclaimed — pool returns for donated slabs are a later refinement,
  recorded not built).
- **Safety condition**: the region must have NO live Mappings and no
  sibling references — the kernel refuses (`BadState`) rather than
  ever sharing slab backing with user-reachable memory. On the MMU
  tier the donated range enters the kernel's own map; on MPU/flat it
  is simply RAM the kernel uses and user tables never grant.
- **Slab shape**: a kind's slab becomes a CHAIN OF EXTENTS (the
  static array is extent 0 — boot minimums stay compiled, so the
  kernel boots before any donation exists). A handle word's index
  bits span the chain; lookup is index → (extent, offset). Slot
  reuse, generations, refcounts, teardown walks: unchanged in
  meaning, re-plumbed to walk extents.
- **THE HARD PART, named honestly**: `MAX_PROCESSES` is not one slab
  — `HANDLES`, `BOOT_HANDLES`, and the quota tables are
  `MAX_PROCESSES ×` arrays, and the per-domain grant records live in
  the process slot. Growing PROCESSES means a process slot carries
  its own tables (one bigger object per slot, donation-backed)
  rather than rows in parallel global arrays. That is a real
  restructure and may want its own rung (donate-everything-else
  first, processes second).
- Quotas stay per-process authority; donation bounds the MACHINE.
  `NoResource` keeps its meaning ("the slab is full") — what changes
  is who decides how big the slab is: the deployment, not the
  compile.

## The tier table (design 20 finding 3: BOTH dimensions priced)

| target | tier | slots/pages | RAM | notes |
|---|---|---|---|---|
| arm64 virt | 1 (MMU) | pages (4 MiB window today) | plenty | the first climb, in-tree |
| riscv32 virt | 1 (Sv32) | pages | 128 MiB | the second climb, in-tree |
| ESP32-P4 | 2 (MPU) | 32 PMP + 16 PMA, 128 B gran | 768 KB | first real tier-2 part; NO QEMU machine yet — a later board |
| ESP32-C3 | 2, AT the floor twice | 16 PMP exactly | 400 KB < 445 KB image | XIP is the ENTRY PRICE, not a preference (design 20) |
| ESP32-S3-class | 3 (flat) | WORLD0/1 only | — | Xtensa target = `--target` + esp-clang, not a new backend |
| virt boards, flat BUILD PROFILE | 3 | — | — | the flat tier's FIRST test target, zero new toolchain |

Tier-2 floor arithmetic (design 19's open cell) rides to the session:
TOR-costs-2/NAPOT-costs-1 held HAL-INTERNAL under a conservative
quota so a budget never varies by allocation luck;
`DEFAULT_QUOTA_MAPPINGS` prices at 6–8. **Proposal: ratify the shape
now, DEFER the number to the first tier-2 board unit** — no in-tree
target consumes it yet and a number nobody runs is a number nobody
believes.

## The seed pile: in or out of M5

1. **Shared stats region — the userspace `top`** (design 16's seed).
   First cross-process read-only map with a published record layout;
   wants the tier word (it is the first mapping whose CONSUMER cares
   about denial). **Proposal: IN, a late unit** — it is the memory
   milestone's own demo the way the uart service was M4's.
2. **Growable-slab donation op** (010 ruling 10). **RULED IN (user,
   Sep 3) — D-6.** Not for the RAM (the slabs are ~5 KiB); for the
   CEILING: suite-sized limits forbid the larger systems the
   namespace vision will want. Tier-1 table pools still do NOT
   depend on it (D-3 stands).
3. **The namespace / `open()`** (§2.1's path-attached acceptor).
   **RULED OUT of M5 (user, Sep 3)** — it is an IPC-and-root-server
   ladder, not a memory one, and the lazy-decode disposition's "the
   M5 namespace" premise is STALE (rider owed on that tracker entry:
   the disposition's revisit trigger stands on the tight-SRAM leg
   alone).
4. **`sosrt` arena sizing** (design 20 finding 4): the 64 KiB arena
   dominates every process image on MCU-class parts — measured, it
   and the 64 KiB kernel boot stack ARE the small-board RAM story.
   **Proposal: IN as a small `rt/` unit** (per-package arena size),
   picking up the kernel-stack size as a board-scaled HAL constant
   in the same pass.
5. **F2 / the §5.9 pin** (per-physical-region refcount, open since
   M1): **CLOSES with D-5's allocator unit** — built, not re-worded.
   The M3-close header still gains "and unmoved at M4's" (design 24
   F2's minimal edit) in the meantime.
6. **SL-18 (size levels)** closes upstream at a pin bump; the C3
   re-measure rides whatever unit is open when it lands (standing
   morning item, not an M5 unit).

## The ladder (proposed slicing — the session's main artifact)

- **Unit 1 — arm64 translation-as-isolation.** D-1 step one + D-2's
  seam edit pair + D-3's static pools, identity placement, ASIDs.
  Gate ambition: transcripts BYTE-IDENTICAL (the design-23 tradition
  — a mechanism swap the diff cannot see), plus the isolation proofs
  re-witnessed under the new mechanism.
- **Unit 2 — placement.** D-1 step two on arm64: `map` answers, one
  link base, `child*.ld` and the runner arithmetic collapse, §5.5 +
  §2.5 amended to tier vocabulary. Transcript rows may move
  (authorized by name here).
- **Unit 3 — riscv32 Sv32.** Both steps at the other arch, `rv32core`
  growing the paging half beside PMP (the C3 board keeps PMP —
  design 23's split is what makes this a board-family fork rather
  than a rewrite).
- **Unit 4 — the tier word + the flat profile.** D-4's surface; a
  flat build profile of one virt board; the runner learns TIER-SORTED
  case lists (isolation proofs Isolated-only — design 19's "one
  story, one test" sentence executed); tier table into spec.md.
- **Unit 5 — the real allocator** (D-5): pool returns, coalescing,
  byte accounting; §5.9/F2 closes. Arch-free — no ordering edge to
  units 1–4, so it can run in a parallel worktree if the session
  wants the milestone pipelined.
- **Unit 6 — slab donation** (D-6): the op, the extent-chain slab
  shape, every kind except processes; **unit 6b if needed**: the
  process-slot restructure (per-slot tables) that lets PROCESSES
  grow. After unit 5 (the free-node slab is donation's first
  customer, and the extent walk wants the allocator's vocabulary).
- **Unit 7 — the shared stats region**: RO cross-process map,
  published layout, `top` in a test package; the op-shaped `Stats`
  v1 stays as the compatibility floor.
- **Unit 8 — docs sweep, M5 closes** (the design-11/24 tradition).

Arena + kernel-stack sizing (seed 4) slots wherever convenient — it
has no ordering edge to any unit above.

## Explicitly out (the standing tail, unmoved)

`kill`; priorities and the §7 band map; SMP + `IntrSpinLock`;
FP-in-userspace; the IOMMU driver + critical processes; P4/S3 board
ports (no QEMU machine / a `--target` port, both parked at design
19's addenda); lazy handle decode (user-annotated UNSCHEDULED, its
disposition unchanged). vDSO true-mapping: UNLOCKED by unit 2 but
proposed OUT of M5 — record it as the first M6 candidate unless the
session pulls it in.

## As ruled (user, Sep 3 2026 — the scoping session)

1. **D-1: BOTH STEPS IN M5**, sequential units — unit 1
   translation-as-isolation (identity VAs, byte-identical ambition),
   unit 2 placement (one link base, `child*.ld` collapses). `map`
   takes NO VA hint on any tier: the address is the answer.
2. **D-2 + D-3: APPROVED AS PROPOSED** — the seam grows the
   `prot_install`/`prot_remove` edit pair; MPU/flat HALs implement
   it as record-append + today's replay; replay-at-switch stays
   MPU-only; static per-process table pools (no donation
   dependency); kernel placement under translation is HAL-internal.
3. **D-4: SystemOp GETTER + BOOT INFO**, two words only
   (`Isolated`/`Flat`; MMU vs MPU not advertised).
4. **Floor cell: SHAPE RATIFIED, NUMBER DEFERRED** to the first
   tier-2 board unit. TOR/NAPOT stays HAL-internal.
5. **D-5: RETURN TO ROOT; release on the SECOND zero** (memory
   handles AND mappings — §2.5's quoted invariant, built as
   written). First-fit stands unless the unit's brief argues
   otherwise. §5.9/F2 closes here.
6. **D-6: AS PROPOSED** — `SystemOp.SlabDonate(kind, memory)` on its
   own `SystemRight`, consuming, permanent in v1; unit 6 converts
   every kind EXCEPT processes; **unit 6b is its own rung** for the
   process-slot restructure. Motivation recorded: capacity becomes
   policy, toward the (post-M5) namespace vision.
7. **Seeds: stats region IN (unit 7), arena + kernel-stack sizing
   IN** (a small `rt/`+HAL unit, slotted at convenience). Namespace
   OUT (ruled earlier the same day; the tracker's "M5 namespace"
   premise was stale and carries a rider).
8. **Ordering: PIPELINE WHERE CLEAN** — tier climbs 1→4 in order;
   the allocator track (5→6→6b) is arch-free and may run in a
   parallel worktree when the queue has room; the lead serializes
   merges as ever.
9. **Unit 2's transcript moves: AUTHORIZED NOW, BY NAME** — this
   ruling is the authorization; unit 2's brief lists the exact rows
   before dispatch and the As-built diffs them.
