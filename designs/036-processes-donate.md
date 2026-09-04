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

# As built (Sep 4 2026)

**Status: BUILT.** `SlabKind` grows `Processes = 11`; `ProcessSlot` absorbs the
four tables a process index used to key outside it; `PROCESSES` is an ordinary
`Slab` chain, and a deployment that donates runs more concurrent processes than
the compiled floor. Both halves of the priced arm64 contact landed — the wall is
enforced and answers `NoResource`, and `PROT_DOMAIN_SLOTS` went 3 → 5 with the
arithmetic recorded below. The gate is the new case's rows only, both arches,
with no image-size drift.

**THE BRIEF'S SECOND HALF RESTED ON A PREMISE THAT WAS ALREADY KNOWN FALSE, and
correcting it is the first thing this record owes** — see "The domain pool" below.

## The module that was born, and why a module was the answer

The brief says "each becomes a FIELD of `ProcessSlot`". That is exactly what
landed, but it could not land where `ProcessSlot` was, and the obstacle is the
one the old code had already written down.

Four storages sat in `kcore.objects`: `HANDLES`, `BOOT_HANDLES` with
`BOOT_HANDLE_COUNT`/`_NEXT`, and `QUOTA_USED`/`QUOTA_LIMIT`. `PROCESSES` sat in
`kcore.process`. **`objects` sits ABOVE `process` in the module order**, and the
quota ledger's own header said why it lived there rather than on the slot: §12
promised "a field on the process slot", and the promise was "kept one module
down, for a reason the module order decides rather than taste" — the HANDLE row
is charged inside `mint_handle` and credited inside `unbind_handle`, both in
`objects`, and a field of a record `process` owned could not be reached from
them. An import cycle is not diagnosed here (DF-232e); it empties an export table
and blames a third file.

So making the tables fields meant one of two moves, and this unit took the
smaller one:

- **move the FUNCTIONS down** — `mint_handle`, `unbind_handle`, `lookup_handle`,
  `table_room`, the six quota functions and the six boot-handle functions, all
  out of the module whose other 2,400 lines call them; or
- **move the RECORD down**, below both files, and leave every function exactly
  where it was.

`kcore.pslot` is the second. It holds `HandleEntry`, `BootHandleSlot`,
`QuotaKind`/`QUOTA_KINDS`, `ProcessState`, `GrantRow`, `ProcessSlot` and
`PROCESSES`, and it does no work at all: it is the per-process record, its field
types, and the slab. **Not one function moved between modules**, which is why the
diff is 426 insertions against 445 deletions for a change this structural.

That is `kcore`'s own module-order rule — "a module sits at the altitude its CALL
GRAPH allows" — applied to STORAGE rather than to code, and the list in
`kcore/lib.saw` now carries `pslot` with that sentence. It also carries `slab`,
which design 32 added without updating the list; noted rather than left.

## The slot layout as landed

```saw
public(package) struct ProcessSlot {
    state, entry, stack_top, region_base, region_top, rw_base, rw_top,
    grant_count, grants: [GrantRow; MAX_GRANT_ROWS], exit_kind, exit_code,
    handles:      [HandleEntry;    MAX_HANDLES],   // was HANDLES
    boot_handles: [BootHandleSlot; MAX_HANDLES],   // was BOOT_HANDLES
    boot_count:   Int,                             // was BOOT_HANDLE_COUNT
    boot_next:    Int,                             // was BOOT_HANDLE_NEXT
    quota_used:   [UInt16; QUOTA_KINDS],           // was QUOTA_USED
    quota_limit:  [UInt16; QUOTA_KINDS],           // was QUOTA_LIMIT
    refs, attachment, syscalls, interrupts, faults,
}
public(package) unsafe static var PROCESSES: Slab<ProcessSlot, MAX_PROCESSES>
```

Every inner dimension is a per-process constant, so the kind is a PLAIN
one-array chain — `slab_geometry`'s `Processes` arm is one line like the first
nine kinds, and `slab_install`'s is `PROCESSES.donate(base, slots)`. It needs
neither `Threads`' side arena nor `Pipes`' second chain, which is the whole
reason unit 6b was the easy one of the three: a pipe's satellites hung off a
DERIVED index and a thread's frame arena could not be a field at all
(alignment), while every table here was already keyed by the process slot
itself.

**MEASURED, against the pinned sawc (0.5.0), both targets:**

| | riscv32 | arm64 |
| --- | --- | --- |
| `sizeof(ProcessSlot)` | **816** | **1432** |
| `PROCESSES` whole (inline + extent table) | 2,504 (`0x9c8`) | 4,408 (`0x1138`) |

**THE FATTENING COST ZERO BYTES, and that is worth stating because it is not
obvious.** On arm64 the storage before this unit was `PROCESSES` 1,440 + `HANDLES`
1,152 + `BOOT_HANDLES` 1,536 + `BOOT_HANDLE_COUNT` 24 + `BOOT_HANDLE_NEXT` 24 +
`QUOTA_USED` 60 + `QUOTA_LIMIT` 60 = **4,296 bytes**. The fattened inline array is
4,408 − 112 (the extent table) = **4,296 bytes**. Byte for byte a relocation: no
padding was introduced, because every field that moved is at least as aligned as
what it landed beside.

**WHAT DID MOVE IS WHICH SECTION IT IS IN, and it is a real cost paid in the
kernel image.** The five satellites were `.bss`; `PROCESSES` is `.data`, because
one field's rest value is not zero — `GrantRow.region`'s `NO_MEMORY` is `-1`, so
design 149's zerofill does not apply to the record. So ~2,856 bytes per image
move from `.bss` into the loaded kernel image on arm64 (~1,700 on riscv32).

Measured like for like on `trap_fault.elf` (arm64), merge base against this
tree: `.data` **23,712 -> 26,688 (+2,976)**, which is `PROCESSES` growing from
`0x5a0` to `0x1138` plus eight bytes of alignment. `.bss` moves the other way for
this reason and the pool's way for the other: **232,336 -> 279,120 (+46,784)** =
the pool's +49,152, less the five retired satellites' -2,856, plus 488 bytes of
padding shifting around a 4096-aligned static.

It costs **no transcript motion at all**, and the reason is worth pinning: the
image-size column of the gate is each test's *userspace* `sosimg`, not the
kernel ELF, so the kernel's own `.data` and `.bss` never appear there. Recorded
as a seed rather than fixed — a plus-one encoding for `GrantRow.region` (the
idiom `ProcessSlot.attachment` already uses) would put the whole record back in
`.bss`, but it is a change to design 28's back-reference and this unit had no
need of it.

## The flat-index unlearning — every site

The census was exhaustive rather than sampled, because the compiler cannot catch
a missed one: a `p * MAX_HANDLES + i` that survived would still compile and
would index the wrong process's row.

**`p * MAX_HANDLES` — 12 sites, all gone:**

| function | file | became |
| --- | --- | --- |
| `lookup_handle` | objects | `PROCESSES[p].handles[index]` |
| `mint_handle` (read + write) | objects | `PROCESSES[p].handles[h]` |
| `table_room` | objects | `PROCESSES[p].handles[h].obj_type` |
| `unbind_handle` (read + write) | objects | `PROCESSES[p].handles[index]` |
| `end_process` | sched | `PROCESSES[p].handles[h]` |
| `boot_tag_used` | objects | `PROCESSES[p].boot_handles[i]` |
| `boot_handle_take` | objects | one value read (below) |
| `queue_boot_handle` | objects | `PROCESSES[p].boot_handles[n]` |
| `boot_handle_cursor` | objects | `PROCESSES[p].boot_handles[at]` |
| `boot_handle_next` | dispatch | `PROCESSES[p].boot_handles[cursor]` |

**`p * QUOTA_KINDS` — 4 sites, and the helper became a different function.**
`quota_at(p, kind) -> p * QUOTA_KINDS + kind` is now
`quota_column(kind) -> (kind as UInt8) as Int`: the process half of the address
is the slab lookup and what is left is the column. The name survives because "the
value IS the column index" is a property of `QuotaKind` worth spelling once
rather than casting at six sites.

**No division or modulo anywhere** — unlike the pipe conversion, no index here
was ever derived, so nothing renumbers and nothing had to be proven uniform
across the chain.

**THREE SITES WERE REWRITTEN RATHER THAN RENAMED, and each is a place-window
question rather than an arithmetic one.** Design 34's SL-17 lesson is that two
place windows on one root in one expression is refused in several of the shapes
this code already had, and `PROCESSES[...]` is an accessor now where it used to
be a plain fixed array:

- `boot_handle_cursor`'s condition was
  `while at < BOOT_HANDLE_COUNT[p] && BOOT_HANDLES[p * MAX_HANDLES + at].taken`
  — two windows on one root in one `&&`. The count is loop-invariant, so it is
  hoisted to a local and the condition opens one window.
- `boot_handle_take` tested, marked and returned through three separate
  subscripts of one record. `BootHandleSlot` is trivially copyable, so the
  record is read out once into a local and the three uses work on that copy.
- `quota_room` / `quota_room_for` compared `QUOTA_USED[at]` against
  `QUOTA_LIMIT[at]`; the limit is hoisted, so the comparison opens one window.
  This one also reads better — the `QUOTA_UNLIMITED` test and the comparison now
  visibly ask about the same loaded value.

**AND ~40 SITES DID NOT MOVE AT ALL**, which is design 32's property holding for
a third kind: `PROCESSES[p].state` means what it always meant, because `[]` is a
`borrows` accessor lending the slot where it sits. That includes the shapes I
expected to have to fix and did not — `PROCESSES[slot].refs = PROCESSES[slot].refs + 1`
(the RHS is lifted out of the target's window, DF-248a) and
`hal.frame_init(frame_slot(slot), PROCESSES[child].entry, PROCESSES[child].stack_top, …)`
(two SHARED reads of one root compose; the Law of Exclusivity's "many `&`").

## The domain pool — **and the brief's premise was wrong**

The brief asks: "per-process sets SHRANK at unit 1.5 (user windows only —
measure the real per-set size first and record it)… Pick the count that keeps the
pool under ~64 KiB of .bss".

**THE SETS DID NOT SHRINK, AND DESIGN 29'S OWN AS-BUILT ALREADY SAYS SO.** Its
section "The set-shrink arithmetic — and **the brief's expectation was WRONG**"
records a `.bss` delta of **+16 KiB**, positive, and the reason: the sets
genuinely lost their kernel content, but that content was ENTRIES INSIDE TABLES
THE USER WINDOWS STILL NEED, not tables of their own. A translation table is a
page; removing entries frees descriptors, never a table. So a set is
`512 + 512 + 1024 + 512 + 512 = 3072` descriptors — 24 KiB — before 1.5 and
after it.

Re-measured here rather than taken on trust, with `llvm-nm --print-size` on the
arm64 kernel at the merge base: `saw.static.PAGE_TABLES$m$hal` is `0x16000` =
90,112 bytes at `PROT_DOMAIN_SLOTS` 3, which is exactly
`3072 * 3 + 1536 (linmap) + 512 (slack)` descriptors × 8. Confirmed.

**SO THE "~64 KiB OF POOL .bss" BUDGET IS UNSATISFIABLE AS WRITTEN** — the pool
is already 88 KiB at three slots, and 64 KiB would be a REDUCTION to two. Read
as the budget it was plainly meant to be — keep the ADDED `.bss` under about
64 KiB — the arithmetic is:

| slots | pool descriptors | pool bytes | delta from 3 |
| --- | --- | --- | --- |
| 3 (before) | 3072·3 + 1536 + 512 = 11,264 | 90,112 (`0x16000`) | — |
| 4 | 14,336 | 114,688 (`0x1C000`) | +24 KiB |
| **5 (chosen)** | 3072·5 + 1536 + 512 = 17,408 | **139,264 (`0x22000`)** | **+48 KiB** |
| 6 | 20,480 | 163,840 (`0x28000`) | +72 KiB — over |

**FIVE**, and the choice is verified rather than computed: `PAGE_TABLES` measures
`0x22000` after the edit, exactly as predicted. Five rather than six because
+48 KiB is the largest raise under the budget; five rather than four so the
gate's own case (root plus three children) sits one slot BELOW the wall rather
than exactly on it.

`.bss` is `NOLOAD`, so this costs zero image bytes; what it costs is `boot.S`'s
zerofill and two more set-builds at boot, each of which writes two live entries
at rest (design 29's measurement). Headroom is unchanged in character —
`_bss_end` stays about 1.3 MiB below `ROOT_LOAD_BASE`, and `virt.ld`'s existing
`ASSERT` is what would catch a raise that went too far.

**ASID STAYS 8-BIT-FINE, and it is now CHECKED rather than reasoned about.** The
ASID is the process slot index and `TCR_EL1.AS` = 0 gives the field 8 bits; five
is not close to 256, but aliasing two domains' TLB entries onto one tag is the
kind of thing that fails silently, so `static_assert(PROT_DOMAIN_SLOTS <= 256)`
now sits beside the constant. Nothing in the HAL said this before.

## The wall — enforced, and WITNESSED by probe

**The mechanism.** `kcore.process.process_slots_usable()` answers
`min(PROCESSES.capacity(), hal.PROT_DOMAIN_SLOTS)`, and `alloc_process` scans to
it instead of to `MAX_PROCESSES`. One bound carries both halves: a donated slot
has to be VISIBLE here or donation buys nothing, and a slot the tier cannot
isolate must not be handed out. A create past it is the `NoResource` a full table
already answers — an advertised wall, not a fault, because the caller cannot know
the number (the kernel does not publish it).

`kcore.limits`' `static_assert(MAX_PROCESSES <= hal.PROT_DOMAIN_SLOTS)` is
re-scoped to the FLOOR in its wording: extent 0 must fit the pool or the kernel
could not boot the processes it was built for. Above the floor the question is
answered at run time, because that is where donation happens.

**THE WITNESS, AND IT IS A MEASURED PROBE RATHER THAN A GATED CASE.** With
`PROT_DOMAIN_SLOTS` temporarily returned to 3 and everything else unchanged,
`tests/process-donate` on arm64 produces:

```
SOS procdonate: floor=3 third child refused: out of kernel objects
SOS procdonate: donated a region of process slots
SOS procdonate: the donated extent gave nothing: out of kernel objects
```

The donation SUCCEEDS — the slab really did grow, by ~91 slots — and the create
still answers `NoResource`. That is the wall, in its own words, on the tier that
has one. Restored to 5 immediately after; the probe is reproducible in one line.

**WHY IT IS NOT A GATED CASE — the portability decision the brief asks for.**

1. **The number is per-tier**, so no portable assertion exists: arm64's wall is 5
   and riscv32-common publishes 256. Witnessing riscv32's would need 256
   concurrent processes — each with its own identity-placed image (see below) and
   ~96 KiB of RAM, so ~24 MiB and 256 linker scripts. It is arithmetically
   impossible rather than merely unwritten, which is `slab_donate_free_nodes`'
   situation with the arches reversed.
2. **An arm64-only leg would assert a HAL CONSTANT a future unit will change.**
   The wall's number is not a property of this unit's mechanism; it is this
   unit's own arbitrary choice of pool size, and a case asserting 5 would have to
   be edited by whoever next raises it — pinning a decision rather than a
   behaviour.
3. **The MECHANISM is exercised on every gate run of both arches**, because
   `process_slots_usable` is the bound `alloc_process` scans to on every create
   the suite makes. What the probe adds is the specific number, and it is
   recorded here with the command that reproduces it.

**AND BOTH HALVES OF THE CONTACT ARE LOAD-BEARING, which that probe also
proves.** Without the raise, an arm64 process donation would buy exactly nothing:
the slab would grow and the wall would refuse every slot above it. The `floor=3`
line before the donation and the failure after it are the same case saying so.

## Consequences swept

- **The teardown scans.** `end_process`'s handle sweep reads
  `PROCESSES[p].handles[h]`; its bound was already `MAX_HANDLES` (a per-process
  constant, not a slab size) and is unchanged. `clear_boot_handles` and
  `quota_clear` are two field writes and a `QUOTA_KINDS` loop, all per-process.
  **No teardown bound moved**, which follows from the shape: everything this unit
  touched is keyed by a per-process constant, and the only `MAX_PROCESSES`-shaped
  loop in the tree was `alloc_process`.
- **`alloc_process` is the one loop bound that moved**, to
  `process_slots_usable()`. Design 1's audit verdict is unchanged and the
  argument is the same one with a different number: the walk is bounded because
  extents are finitely many and each is finitely sized, the body is O(1) in RAM,
  no preemption point. What is new is that the bound is now ALSO the tier's, and
  it is the smaller of two finite numbers.
- **`free_object`'s Process arm** (`refs.saw`) reads `PROCESSES[slot].attachment`
  and is untouched — it indexes by slot, which is what the slab lends.
  `ref_object` / `drop_reference` maintain `PROCESSES[slot].refs` through the
  accessor; both compile as written (the RHS-names-the-root rule).
- **`Stats` attribution** (`process_stats`, `count_trap`) reads and writes the
  three trap columns on the slot; they were already fields and stay fields. A
  donated process counts its own syscalls in its own donated slot.
- **`Give` / `Start` / `BootHandleNext`** all go through the boot set, which is
  now three fields. `boot_handle_next`'s `BOOT_HANDLE_COUNT[p]` /
  `BOOT_HANDLE_NEXT[p]` became `PROCESSES[p].boot_count` / `.boot_next`;
  `start(boot_tag:)`'s resolver is `boot_handle_take`, rewritten as above.
- **The reclaim machinery** (`reclaim_process_slot`) is unchanged in meaning and
  now reaches into donated extents: the case's claim 5 is exactly that a `Gone`
  slot ABOVE the compiled floor is freed and spent again, at a new generation.
- **`MAX_ATTACHMENTS`' derivation is re-stated as a FLOOR** in `kcore.limits`,
  and the restatement is a correction rather than a formality. The sum's old
  justification — "nothing can need more attachments than there are waitables" —
  is no longer true of a donating machine, because all four terms are donatable
  kinds now. What makes that cost nothing is that `Attachments` is ITSELF a
  donatable kind (design 32 converted it), so a deployment raising a waitable
  slab raises this one in the same vocabulary with the same op: the relation is
  preserved by the DEPLOYMENT rather than by the compile, which is where D-6
  moved every one of these numbers. The arithmetic still buys the boot minimum
  exactly as before.
- **The filler-process cases hold, and the brief's expectation was right.**
  `process-reclaim` and `death-late-attach` each create an unstarted FILLER
  process "written against the table is full". Neither donates, so
  `PROCESSES.capacity()` is `MAX_PROCESSES` throughout and
  `process_slots_usable()` is `min(3, 5)` = 3 on arm64 and `min(3, 256)` = 3 on
  riscv32 — the same number `alloc_process` compared against before this unit.
  Both cases pass unchanged, and their rows are byte-identical.

## The proof — `tests/process-donate`

FIVE claims, in order, and the case is the canonical donation shape with two
extra rungs:

1. **The floor is real.** `MAX_PROCESSES` is 3 and ROOT IS ONE OF THEM, so root
   plus two children is the whole table and the third create answers
   `NoResource`. `floor=3` is printed so a kernel bump fails this case loudly
   rather than quietly making the claim vacuous.
2. **The donation** — an ordinary region, cut from what root was given.
3. **The ceiling moved** — the refused create succeeds, making a FOURTH live
   process on a machine compiled for three.
4. **A donated slot is a whole process.** All three children are started and each
   says its own line before exiting with its own code. That line is the donated
   slot's handle table, quota ledger and boot set working at once: the child
   drains a boot-set record (new fields), derives a Process object (a mint into
   the new handle field, charged against the new ledger field) and makes a
   syscall (a lookup in the new handle field). The three exit codes are distinct,
   so `a=65577 b=65578 c=65579` is a claim about WHICH process ended.
5. **The donated slot comes back and is spent again**, at a new generation —
   which needs both the reclaim walk and the allocator scan to reach above the
   floor.

**THE EXITS ARE READ IN KEY ORDER, NOT COMPLETION ORDER**, which is what keeps
the line deterministic however the three children interleave: a death is a
terminal level, so attaching after the fact still wakes, and the case attaches,
waits and removes one key at a time. No timing-dependent row is added to the
suite.

**TWO THINGS THE CASE HAD TO DESIGN AROUND, both worth recording:**

- **THE DONATED REGION IS A FRESH CUT, NOT A SPLIT'S REMAINDER.** The obvious
  spelling — carve the child's span off a destination region and donate what is
  left — FAULTS: a region that has lent bytes out is a pool root with
  `out_bytes != 0`, and design 32's safety condition refuses one by name
  (retiring a root under its own pieces would leave their back-references naming
  a stranger). So the case takes a second cut from the same region instead; a
  freshly cut piece has lent nothing, is named by one handle and is mapped
  nowhere, satisfying all three counts by construction. This is a real trap for
  the next case that wants to donate out of a region it is also using.
- **THREE ECHO CHILDREN EXIST FOR ONE PROFILE'S SAKE.** riscv32 places by
  identity (`hal.image_link_base` is the identity there), so three CONCURRENT
  children need three load addresses, therefore three linker scripts, therefore
  three packages — `hal/riscv32/user/child3.ld` is new, at
  `child_region_base + 2 * CHILD_REGION_LEN`, and the three packages differ only
  in that line and an exit code. On arm64 design 33's placement means all three
  name one `user.ld` and the duplication buys nothing. The case's span carving is
  the same asymmetry once more: each child's region is cut off the FRONT of its
  destination row so the piece keeps that row's base, which is a free choice on
  the translating tier and not one on the other.

The case lists three children and asks for NO POOL, which is why the runner's
"two children cannot also ask for a pool" refusal never comes up — the donation
comes out of the third child's own destination row. No runner machinery was
widened.

## Findings

1. **THE BRIEF'S "SETS SHRANK AT 1.5" PREMISE WAS FALSE AND DESIGN 29 HAD ALREADY
   RECORDED IT.** Priced above. It matters beyond this unit because the brief
   derived a `.bss` budget from it that is unsatisfiable as written; the budget
   was re-read as "added `.bss`", which is plainly what was meant, and the
   re-measurement is in the record so the next raise starts from a true number.
2. **THE MODULE ORDER, NOT THE FIELD LAYOUT, WAS THIS UNIT'S REAL WORK.** The
   brief's "mechanical renames on the same indices" is accurate about the ~16
   access sites and silent about the reason those sites could not simply be
   renamed: the record and the functions that index it were in modules on
   opposite sides of a dependency edge. The old code had written the obstacle
   down at the quota ledger; a unit that had not read that comment would have
   discovered it as an undiagnosed cycle emptying a third module's export table.
3. **THE FATTENING IS BYTE-FOR-BYTE FREE IN SIZE AND NOT FREE IN SECTION.** 4,296
   bytes before and after on arm64, but `.bss` → `.data` because one field's
   sentinel is `-1`. Invisible to the gate (the transcript's image column is the
   userspace sosimg), real in the kernel image, and closable by a plus-one
   encoding — filed as a seed rather than built.
4. **DESIGN 32's FINDING 3 IS NOW THE ONLY PER-PROCESS CEILING LEFT, AND IT IS
   UNTOUCHED.** "A root server can size the machine but not name what it sized":
   `MAX_HANDLES` is 16, and after this unit a process can hold at most 16 handles
   however many processes the machine runs. The handle table is a FIELD now, so
   growing it is no longer a `MAX_PROCESSES`-shaped array problem — which is what
   design 32 said made it unit 6b's subject — but a growth OP is out of scope by
   the brief and is filed as a seed below.
5. **NO SL ENTRY IS OWED.** The place-window shapes design 34 recorded (SL-17's
   lesson, SL-23, SL-24) were enough to write this unit without meeting a new
   language wall; the three rewrites named above are applications of what 6a
   already learned, and two shapes I expected to be refused (an assignment whose
   RHS names its own root; two shared reads of one root in one call) are
   documented as legal and are. Current highest entry remains SL-24.

## Seeds filed

- **The `MAX_HANDLES` naming ceiling** — recorded here as the brief asks, and NOT
  built. It is now an ordinary per-process array inside a record, so a
  handle-table growth op is a self-contained unit rather than a table-shape
  problem; the shape is a second chain hanging off the slot, or a larger compiled
  table with the same donation trick one level down. Design 34's finding 4
  (`pipe-donate` met the handle table before it met any slab) is the standing
  evidence that this is the tree's next-met wall.
- **`GrantRow.region`'s plus-one encoding** — would return `PROCESSES` (~4.3 KiB
  arm64) to `.bss` and make every future field added to `ProcessSlot` free in
  image bytes. Mechanical: four sites, and the idiom is already in the record
  next door (`ProcessSlot.attachment` is slot + 1, 0 = unattached).
- **A fourth concurrent child on riscv32 wants a `child4.ld`** — noted at
  `child3.ld`, since the identity-placement tax is one script per concurrent
  child and the next case to want one should find the note rather than the
  arithmetic.

## Gate

Both runs in this worktree, back to back under the machine-wide suite lock, same
toolchain (sawlang 0.5.0; `sawlang.pin` untouched).

**Baseline**, on the pristine tree at `ebf76d0` before any edit: **246 passed
across riscv32 + arm64**, 123 cases, a 514-line transcript hashing to
`c823c1b5cab6977ea7f57bee6d72091580e2998531487dc0f19378df39667034` — **exactly
the hash the brief records**, so the baseline is the tree's standard one and the
toolchain is where it should be.

**After: 248 passed across riscv32 + arm64**, 124 cases, 524 lines,
`fac30a94f8bbe4023f46fea8d3e44929026055b890df21b035abc7539c169d1e`.

**THE DIFF IS TEN NEW ROWS AND THE TOTAL LINE. Nothing else.**

1. 4 new riscv32 image lines — `process-donate` and the three echo children.
2. 1 new riscv32 case line.
3. 4 new arm64 image lines — the same four.
4. 1 new arm64 case line.
5. The pass-count line, 246 -> 248 (+2 = 1 case x 2 arches).

**PROVEN RATHER THAN ASSERTED.** Removing this unit's own rows and normalising
the mechanical `[n/N]` ordinal makes the two transcripts **identical at 515
lines, with exactly ONE diff hunk** — the whole-run total. So every one of the
246 pre-existing case rows is byte-identical and in its original ordinal, and so
is **every one of the 123 pre-existing image sizes on both architectures**. The
check is arch-blind, so it establishes both halves at once; design 34's
per-arch split is not needed here because there is no residue to attribute.

**NO IMAGE-SIZE DRIFT, and it is structural rather than lucky**: this unit adds
no `@export` to the sysapi floor, so nothing new links into images that do not
call it — design 32's +40 bytes per riscv32 image does not recur, exactly as it
did not for unit 6a. `SlabKind` gains a CASE, and a case is not a name, so no
facade line moved either (design 34's correction, holding a second time).

The three documented timing-dependent rows never enter the comparison at all — a
case's console output is printed only on FAILURE, and nothing failed.

The new case also passed in isolation on both arches before the full run
(`--case process_donate`, 1/1 each).
