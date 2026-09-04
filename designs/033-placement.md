# SawOS design 33 — M5 unit 2: placement (the address becomes the kernel's answer)

**Status: BUILT Sep 3 2026 (see "As built" below) — dispatches under
`designs/025` (RULED): D-1 step two, arm64 ONLY.** riscv32 keeps identity linking and its
`child*.ld` deliberately (ruling 11: "identity is the tier-2 answer"
stays a living demonstration). Builds on units 1 + 1.5 as merged:
the three-name seam, per-process TTBR0 sets, the high-half kernel
over the linmap, the whole low half now user territory.

## The one-sentence goal

On arm64, a user address is the KERNEL'S ANSWER: every user image
links at ONE canonical base and loads wherever physical frames are
(the allocator's answer), `map` returns a kernel-chosen VA (no hint
— ruled), the per-child `.ld` scripts and the runner's base
arithmetic collapse on that arch, and §5.5's identity law re-scopes
to the tiers that keep it.

## The structural change, named honestly

Until now a grant row's `base` was one number serving two meanings
(VA = PA). Placement splits them: **a `GrantRow` carries a user VA
range AND the physical base it translates to** (addresses-as-data
stay physical everywhere else — Memory capabilities, the region
table, sosimg records are untouched). Consequences, each a section
of the work:

1. **The seam**: `prot_update(p, base, top, perms, device)` grows a
   `pa` argument (or the row itself — the agent picks the spelling
   that keeps the MPU tier's identity implementation trivial: there
   `pa == base` always, assert it). The arm64 body installs
   descriptors whose output address is `pa`, input range the VA.
2. **The copy funnels**: resolving a user VA to a PA walks the
   TARGET's grant rows (bounded: `MAX_GRANT_ROWS`), then
   dereferences through the linmap — the shape 029 said would exist
   ahead of need, now real. A VA that resolves to no row is the
   fault it always was.
3. **The loader**: `place_image` copies segments to PHYSICAL frames
   and installs rows mapping the canonical VA to them. Physical
   placement comes from the existing region/pool machinery — the
   allocator composes here, deliberately.
4. **`map`**: chooses the VA (a per-process cursor above the image
   region is sufficient v1 policy — record the policy at the site);
   answers it in the op's existing return shape. IoMemory likewise.
   The `Mapping` object records the row it owns, unchanged in
   meaning.
5. **Link bases**: ONE arm64 user base (keep `0x4020_0000` — the
   canonical number every process now sees). `child.ld`/`child2.ld`
   (arm64) collapse into `root.ld`'s base or one shared script; the
   runner's arm64 base arithmetic (`child_region_base` etc.)
   simplifies to match. riscv32's scripts and constants DO NOT MOVE.

## §5.5 and §2.5, at doc-comment level only

The identity law's re-scope ("an address is the same number in every
process" becomes the TIER-2 answer; on tier 1 an address is
per-process and kernel-chosen) and §2.5's "shared at its own
address" amendment are UNIT 8's prose. This unit updates doc
comments at edited declarations and the two ABI.md files, nothing
else in spec.md.

## Authorized transcript motion (025 ruling 9 — the exact list)

Verified against the 0.5.0 baseline (495 lines, hash `801a0f98…`):
the gated report contains NO raw addresses — only case rows and
image sizes — and the runner's address expectations are
TEMPLATE CONSTANTS (`root_entry`, `child_region_base`, `pool_base`,
arm64 block, `tools/sos_runner.py` ~line 613).

- AUTHORIZED: every **aarch64** `.sosimg` size row (relinking at one
  base may move any of them); the runner's arm64 constants block and
  any console rows templated on it; case rows only if a case is
  RENAMED (do not).
- NOT AUTHORIZED: any **riscv32** row of any kind — that half of the
  diff must be BYTE-IDENTICAL, and it is the proof the tier split
  is real. Anything else moving on arm64 beyond the size rows and
  templated address rows is a finding.

## The proof

- Existing suite: every case now runs with per-process, kernel-
  chosen addresses on arm64 — the suite IS the placement proof.
- One new case, `map_placed` (or a leg on an existing map case):
  two processes map the same region and receive DIFFERENT VAs; both
  read the shared bytes — "shared at per-process VAs", witnessed.
- `share_double_map` (identity-shared today) is the case most
  likely to need retargeting — its rows are covered by the
  authorization above only if the change is address-template or
  size; a semantic retarget must be named in the As-built.

## Out of scope

riscv32 anything; the tier word (unit 4); vDSO true-mapping (M6+);
spec.md prose (unit 8); COW/overcommit (never in v1); guard pages
(record as a refinement if the VA policy makes them cheap).

## Recording duties

As-built here: the GrantRow/seam spelling as landed, the VA policy,
the funnel-resolution shape, the collapsed-vs-kept linker scripts,
the enumerated row diff (before/after for every moved row class),
gate evidence (riscv32 byte-identical + arm64 motion within
authorization). SL entries if the language bites. Close the tracker
entry in place.

## As built (Sep 3 2026)

**Status: BUILT.** On arm64 a user address is the kernel's answer: every image
links at one canonical base and loads at whatever frames the allocator supplied,
`map` places, and the copy funnels resolve a user address through the target's
grant record before dereferencing it through the linear map. riscv32 is untouched
and **its half of the gate transcript is byte-identical**, which is the proof the
tier split is real rather than asserted.

### The seam, as landed

Placement is expressed as FUNCTIONS on the existing HAL seam, not as a tier flag
the kernel branches on. Three names arrived and one split in two:

```saw
image_link_base(dest_base: UInt) -> UInt          // NEW
map_place(cursor: UInt, pa: UInt, device: Bool) -> UInt   // NEW
map_source_ok(base: UInt, top: UInt) -> Bool      // NEW (the PA half)
map_target_ok(base: UInt, top: UInt) -> Bool      // unchanged body, now the VA half
prot_update(p, base, top, pa, perms, device)      // grew `pa`
```

| | arm64 | riscv32 (both boards) |
|---|---|---|
| `image_link_base(d)` | `USER_IMAGE_BASE` (0x4020_0000) | `d` — **the identity** |
| `map_place(c, pa, dev)` | `pa` if `dev` else `c` | `pa` — **the identity** |
| `map_source_ok` | RAM, 128 MiB | `RAM_BASE..RAM_TOP` |
| `map_target_ok` | the 4 MiB grant window | `RAM_BASE..RAM_TOP` — *the same body* |

**THE MPU TIER'S ANSWER IS THE IDENTITY AND THAT IS WHY THERE IS NO TIER
CONDITIONAL.** Design 27 needed an advertised `PROT_REPLAY_AT_SWITCH` because the
replay must read `PROCESSES[p].grants` and a HAL cannot import the kernel. Nothing
here has that problem: both questions are pure functions of numbers the kernel
already holds, so each HAL states its own answer and `kernel/core` spells one
sequence. It is design 29's `phys_to_virt` pair, at the other address vocabulary,
and it folds away on riscv32 exactly as that one does.

`map_source_ok` exists because placement split ONE question into two by splitting
one address into two. `map_target_ok` used to answer both — a mapped region's
range had to be inside the 4 MiB grant window because that range was simultaneously
the descriptor's input and its output. Now the INPUT must fit the window (it is the
level-3 table's own span) and the OUTPUT must be RAM the linear map covers, and
those are 4 MiB and 128 MiB respectively. On riscv32 the two predicates are
deliberately identical rather than one forwarding to the other: where a user
address IS its physical address the two questions are one question, and writing it
twice is what says so.

**`GrantRow` GREW ONE FIELD, `pa`.** `base`/`top` stayed the user's range and `pa`
is the physical base it translates to; the mapping is affine over a row. Nothing
else moved — `remove_grant`, the compaction and `MappingSlot.row` are untouched in
shape. The MPU tier's invariant `pa == base` is ASSERTED, in `prot_update`, which
is the funnel every row passes through at the moment it is recorded (the replay
reads the record afterwards, so a bad row would already be stored by then). One
comparison per grant, on a path that runs a handful of times per process.

`load_domain`'s replay now spells `row.pa` and `row.pa + (row.top - row.base)`.
Same numbers on that tier; what changed is that the line says which of a row's two
addresses it means, now that there are two.

### The VA policy — **AND IT IS NOT THE BRIEF'S CURSOR**

The brief proposed "a per-process cursor above the image region is sufficient v1
policy". **It is not sufficient, and the suite is what refused it.** What landed is
`kcore.process.free_user_va`: **first fit at or above the target's own
`region_top`, over the rows that target already has**, device rows skipped
(they live in a window of their own and never contend with RAM for an address).

A monotonic cursor never reuses an address, which is the safer story — a stale
pointer into an unmapped region keeps faulting for ever. Two existing cases need
the address back:

- **`mapping_slot_free`** maps and unmaps thirteen times and then expects the
  fourteenth mapping, over a re-split range, to answer at the address the
  thirteenth wrote through. Under a monotonic cursor those are thirteen pages
  apart.
- **`memory_split`** writes at `base` and `base + CHUNK` through two mappings of
  two consecutive splits, so it needs consecutive mappings at consecutive
  addresses.

Both are statements about RECYCLING, which is what M5 unit 5 built underneath, and
a virtual address that never came back would have made the physical recycling
unobservable. **The cost is named rather than hidden: an address freed by `unmap`
may be handed to a later mapping of different bytes**, so a program that keeps a
pointer past its own `unmap` can reach a stranger's memory instead of faulting.
That is the same hazard `mapping_slot_free` already documents one level down for
the physical range; closing it wants a generation or a quarantine v1 does not have.
Recorded at `free_user_va` itself, not only here.

The consequence that makes the whole unit work: **every image links at one base
and every region is one size, so a process's FIRST mapping lands at the same
number in every process** — 0x4024_0000 on this board. That is what lets one
arm64 constant keep serving the ten older map cases (below), and it is why
`map-placed` needs a spacer to make two processes disagree.

The floor is `region_top` because everything below it is the image and its stack.
The answer is not checked against the window inside `free_user_va`: `install_row`
asks `hal.map_target_ok` about the range it returns, which is the one place a
refusal has somewhere to go. **Running out of address space is `NoResource`, not
`BadArg`** — the caller chose no address, so it cannot have chosen a bad one — and
the check sits AHEAD of `alloc_mapping`/`mint_handle` so a refusal leaks neither.

`ProcessSlot` gained NO field. The brief's cursor would have been per-process
state; first fit is derived from the rows, which the slot already carries.

### The funnels

`copy_out`/`copy_in` keep their window checks verbatim and gain a resolution:

```saw
let pa = try translate_user(p, dst, bytes) catch { return error }
copy_bytes(hal.phys_to_virt(pa), src, bytes)
```

`translate_user(p, va, bytes) -> Result<UInt, FaultReason>` walks the target's
rows (bounded by `MAX_GRANT_ROWS`, nine), skips device rows, and answers
`row.pa + (va - row.base)`. **The whole range must lie in ONE row**, which is the
honest bound rather than a convenience: a row is the largest span over which the
mapping is affine, so a copy crossing a row boundary would need two translations
and could not be one `copy_bytes`. It costs nothing real — a buffer is a variable
in one segment or one mapped region.

`copy_out_check`/`copy_in_check` resolve too and discard the answer, because
`pipe_call` validates when it PARKS and copies at the resume: a destination that
passed the early check and failed the late one would be a kernel invariant broken
rather than a caller's mistake. The copy re-walks; nine iterations of a trivial
loop against a byte copy is not a cost worth a second code path.

**The window checks were NOT replaced.** They are the conservative span
`rw_base`/`rw_top` and `region_base`/`region_top` have always been, and the
resolution is strictly narrower — so the funnels now refuse an address inside the
window but inside no row (the gap between the image's writable end and the stack).
That is a real semantic tightening on BOTH tiers, and it is the improvement
`ProcessSlot`'s own doc comment has predicted since M3 ("a row-walk is the
improvement, not the correction"). No program in the tree names the gap; the gate
is the evidence.

### The loader

`LoadRegion` gained `link_base` and two derived accessors, `link_top()` and
`phys_of(va)`. `base`/`top` stayed the PHYSICAL destination — addresses-as-data
under design 29's discipline — and validation moved wholesale into the VIRTUAL
domain, because `seg.load_addr` is what the LINKER wrote and the region it must fit
is the one the image was linked into. `place_image` copies to `dest.phys_of(va)`
and records `(va, va_top, pa)`. The stack row's two ranges sit at the two ends: the
user sees the top of the linked region, the bytes are the top of the frames.

Every address on a `ProcessSlot` is now a USER address — `entry`, `stack_top`,
`region_base`/`region_top`, `rw_base`/`rw_top` — which is what those fields always
meant and could not previously distinguish.

### `map` answers, and the op that answers it

`MemoryOp.Map`'s return shape is UNCHANGED: it still answers a Mapping handle,
because the capability is what a caller must not lose. The address hangs off the
object that owns the row:

- **`MappingOp.Base = 1`** — answers the row's user base, through the value
  channel. Two refusals, `Unmap`'s verbatim: a freed slot and a `NO_ROW` husk.
- **`MappingRight.Base = 1 << 9`**, in `mapping_rights()`. A separate bit from
  `Unmap` because the two are separable authorities and a mask should be able to
  say so — reading an address is not the power to take the memory away.
- **`Mapping.base() -> Result<UInt, SosStatus>`** in `kernel/sysapi`.

This is the op M5 unit 2 owed. Once the kernel chooses, a program has no other way
to find out: `map` answers a capability, a `Memory` has no bounds reader by design,
and the build-time constant every earlier case uses is a number about the frames.

**NO `sos_mapping_base` C WRAPPER, AND THE OMISSION IS MEASURED RATHER THAN
PREFERRED.** `kernel/sysapi/src/floor.saw`'s per-op C surface should have one. An
`@export`ed symbol is externally visible and therefore never garbage-collected, so
adding one grows EVERY user image on every architecture: **measured at exactly +24
bytes per riscv32 image, across all sixteen images in the map family**, which would
have moved sixteen riscv32 size rows this unit is forbidden to move. It is recorded
as owed work at the site, with the measurement, rather than smuggled past the gate.
Nothing else about the op is provisional. (arm64 images did not move: `user.ld`
page-pads `.text` and `.data`, so +24 bytes crosses no boundary there — which is
also why no existing arm64 size row moved for the relink.)

### The linker scripts — collapsed on one board, kept on the other

**arm64: THREE became ONE.** `hal/arm64/user/{root,child,child2}.ld` are deleted
and `hal/arm64/user/user.ld` replaces them; **121 `Saw.toml` files** had their
`[sos.aarch64-unknown-none-elf] linker-script` line repointed (96 root, 23 child,
2 child2). The three differed in exactly one number, the base, and existed only
because a user address used to be a physical address.

**riscv32: THREE STAY, deliberately** (design 25 ruling 11). There a user address
IS its physical address, so two resident images cannot share a base. `root.ld`,
`child.ld` and `child2.ld` are untouched, and both `user/ABI.md` files now say why
in each other's terms. **That pair of directories is the tier split as a thing you
can look at** — one script against three, in the tree, checked by the gate every
run.

### The runner, and the constant that could not be renamed

`child_region_base` and `pool_base` DID NOT MOVE, and that is the finding rather
than an omission: they are PHYSICAL — `_region_rows` builds the boot region table
out of them — and placement did not move a single frame. `root_entry` did not move
either, because `USER_IMAGE_BASE` was deliberately given `ROOT_LOAD_BASE`'s value,
so root is the one process whose two addresses still coincide and the console's
`entry=` row is untouched. All three gained comments saying which half of the split
they are on. **The runner's arm64 "base arithmetic" turned out not to need
simplifying; it needed disambiguating.**

**`tests/poolbase_arm64.c` IS THE ONE LEVER, AND IT NOW RETURNS A VIRTUAL
ADDRESS UNDER A NAME THAT SAYS "POOL BASE".** Ten test packages read
`sos_test_pool_base()`; all ten compile for BOTH architectures, and this unit's
gate forbids moving a riscv32 row — so a shared `.saw` source cannot be touched at
all. The per-triple `native` slot is the only arm64-only lever a shared test source
leaves, so the arm64 file returns `0x4024_0000` (where a process's first mapping
lands) while `poolbase_riscv32.c` still returns `0x8028_0000` and is still telling
the whole truth there. The misnomer is documented at length in the file.
**Migrating those ten packages onto `Mapping.base()` is owed work**, named here and
there; it is a shared-source edit and wants a unit with authorization to move
riscv32 size rows.

**`share_double_map` NEEDED NO RETARGETING**, against the brief's expectation that
it was the likeliest case to. Root's first mapping and the child's first mapping
both land at 0x4024_0000 — one link base, one region size, one policy — so the
single constant still serves both processes. What the brief priced as a semantic
retarget cost one number in one C file.

### The proof case

**`map_placed`** (arm64 only) with **`tests/map-placed`** + **`tests/child-placed`**,
neither of which contains an address. Root splits a spacer page and maps it (root's
first mapping, 0x4024_0000), then splits the shared page and maps it (root's
second, 0x4024_1000); writes 0xC3; GIVES the region to the child; the child maps it
into ITSELF (the child's first, 0x4024_0000), asks `Mapping.base()`, reads 0xC3,
writes 0x2D and carries 0xC3 out as its exit code; root reads 0x2D back through its
own different address.

**One region, two address spaces, two different addresses, the same bytes.** The
handoff is the "hand over authority" direction (root gives, the child maps) because
a Mapping is not transferable — `mapping_rights()` withholds `Transfer` — so the
child must hold the capability to be able to ask the question at all.

The spacer is the whole trick and it is one page: since every process's first
mapping lands at the same number, root maps a throwaway first so the shared one is
pushed a page higher. **The case therefore does not depend on the policy being
first fit** — it depends on the address being the kernel's answer, and would keep
its meaning under any policy that answers per-process. Both addresses are asserted
as literals in the runner, which makes the case an oracle rather than a tautology:
a policy change has to come there and say so.

### Gate evidence

| | |
|---|---|
| Baseline, this worktree, before any edit | **238/238, 119 cases, 495 lines**, `801a0f98fd521978…` — reproducing the brief's stated baseline exactly |
| After | **239/239, 120 cases, 498 lines**, `2238afc852236605…` |
| **riscv32 half (250 lines, both runs)** | **BYTE-IDENTICAL — `1ca29323e71253bd…` both sides, diff of zero lines** |

**THE ENUMERATED ROW DIFF — 120 lines removed, 123 added, every one classified:**

| class | count | detail |
|---|---|---|
| arm64 case rows RENUMBERED | **119** | payload byte-identical; only `[i/119]` → `[i/120]` |
| NEW case row | **1** | `[120/120] ✓ map_placed` |
| NEW aarch64 `.sosimg` size rows | **2** | `map-placed` 41032 B, `child-placed` 16456 B |
| existing `.sosimg` size rows whose VALUE moved | **0** | *on either architecture* |
| the total | **1 pair** | `238 passed` → `239 passed` |
| anything else | **0** | |

Every moved row is inside the brief's authorization. The 119 renumbered rows are
the mechanical consequence of appending a case — the case was appended at the END
precisely so that every existing row keeps its index and only the denominator moves
— and design 27's As-built treats the same 116→117 renumbering the same way. The
two new size rows are aarch64 size rows, which the brief authorizes by name.
**NOT ONE riscv32 row moved, of any kind.**

Worth stating because the brief priced it and it did not happen: **no existing
aarch64 size row moved either.** The brief authorized "every aarch64 `.sosimg`
size row (relinking at one base may move any of them)"; relinking `child.ld` and
`child2.ld` images down to 0x4020_0000 moved none, because `user.ld` page-pads
`.text` and `.data` and the base is page-aligned in all three old scripts.

### Findings

1. **THE BRIEF'S VA POLICY WAS INSUFFICIENT AND THE SUITE IS WHAT SAID SO.** A
   monotonic cursor breaks `mapping_slot_free` and `memory_split`, both of which
   assert that a recycled physical range comes back at a usable address. First fit
   over the rows is what landed; its cost (VA reuse after `unmap`) is recorded at
   the site. This is the unit's one deviation from the brief and it is behavioural,
   not cosmetic.
2. **`map_target_ok` HAD TO SPLIT IN TWO.** One predicate answered two questions
   while there was one address. The new `map_source_ok` is the physical half; on
   riscv32 the two have the same body, which is the tier split legible in a single
   file.
3. **AN `@export` COSTS EVERY IMAGE ON EVERY ARCHITECTURE — measured at +24 bytes
   per riscv32 image.** That is what kept `sos_mapping_base` out of the per-op C
   surface, and it is a general fact worth carrying: a unit under a
   byte-identical gate cannot add an exported symbol, on either architecture, for
   any reason. Design 32's As-built reported the same mechanism from the other
   side (+40 bytes when it DID add one).
4. **THE ARCH-FREE LINT READ PROSE AGAIN** — design 27's finding 2b, re-met
   verbatim: `sos-test` refused the build over the word `arm64` in a
   `kernel/core/process.saw` doc comment describing a HAL mechanism. Reworded to
   "a translating HAL's own walk". The rule governs what a kernel file SAYS, and it
   is right to.
5. **`sos_test_pool_base()` NOW LIES ON arm64, ON PURPOSE.** The per-triple
   `native` slot is the only arm64-only lever a shared test source leaves under
   this gate. Ten packages should ask `Mapping.base()` instead; that migration is
   owed and needs a unit authorized to move riscv32 size rows.
6. **`map_basics`'s HEADER IS NOW WRONG ON arm64, and it cannot be fixed here.**
   Its double map used to be two rows over one region at ONE address, with "the
   hardware's own matching rule" deciding which answered; under placement they are
   two rows at two addresses and nothing is aliased. The case still passes and
   still asserts the right value (its reads go through the first, RW, row) — but
   its prose describes a machine arm64 no longer is. Same shared-source
   constraint as finding 5; owed with it.
7. **`process_isolation` NOW FAULTS FOR A BETTER REASON.** `child-poke` locates
   root's region by rounding its own address down and subtracting sixteen. Every
   image links at one base now, so that address is below the child's own region and
   inside NO row of it — the child can no longer reach root's memory at all, rather
   than reaching it and being refused. Same observable (one fault, `faults=1`), a
   strictly stronger isolation claim.
8. **NO SL ENTRY OWED.** The language did not bite once. `Result<UInt, FaultReason>`
   with the ruled `try f() catch { return error }` guard shape, the error-channel
   auto-wrap, and a `borrows`-free struct extension on `LoadRegion` all worked
   first time.
