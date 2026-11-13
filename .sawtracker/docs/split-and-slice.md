# SO-92: `split` only allocates; `slice` makes views that hold their parent

Design pass. Nothing is built and nothing was run: every statement about
today's kernel below was read from the code on main at `ba67dc4c` and carries
its file and function. Anything not read in the code is marked unverified.

## 1. Verdict

1. The idea holds up. I found no sequence that frees, donates or re-grants
   bytes a live row or a live slice names, and it removes the one
   cross-process scan `split` does today.
2. What decides it: a row installed through a slice counts on the slice's
   *backing* region, and a slice of a slice holds that backing, never the
   slice. Then "in use" is two counters read in O(1), and releasing a slice
   is a walk of at most three fixed steps (slice, backing, pool root).
3. The price: a slice pins its whole parent and freezes it (no split, no
   donation) for as long as anyone holds it; the kernel gains two fields, an
   op, a right and a release arm. In lines it costs more than it removes.
4. It closes no hole that is open on main. What it buys is a smaller rule:
   bytes leave a region only through `split`, and `split` touches only a
   region nobody uses.
5. What I would build: rules R1 to R19 below. `split` answers a status while
   the region has rows or slices; `Memory.slice(offset, len)` spends a new
   `MemoryRight.Slice`; a slice never carries `Split` and is never donatable.
   *(Superseded by §11: the owner ruled pools and allocations, no slice now.)*

## 2. Today's model, exactly

### The objects

- **A region** is one `MemorySlot` in the `MEMORIES` slab
  (`kernel/core/objects.saw`, `MemorySlot`): `state`, `process` (the record
  charged for it, `NO_PROCESS` once disowned), `base`, `len`, `refs`, `maps`,
  `root`, and three fields used only on a pool root: `free_head`, `out_bytes`
  and `dropped`.
- **A pool root** is a region minted at boot from a `Ram` row of the region
  table (`kernel/core/process.saw`, `mint_boot_regions`): `root` names
  itself, `refs` is 1, it is charged to root's `QuotaKind.Memory`. Its
  `base`/`len` are the pool's unspent frontier (the cursor); bytes returned
  by pieces sit on its free list (`FREE_RANGES`), sorted and coalesced
  (`pool_insert_range`), and a free range touching the frontier folds back
  into it, moving `base` down (`pool_fold_frontier`). `out_bytes` counts the
  bytes on loan to pieces (`pool_cut`, `pool_return`).
- **A piece** is a region made by `MemoryOp.Split` (`kernel/core/dispatch.saw`,
  `memory_op_decoded`, `case Split`). It carries its parent's `root` down,
  so the derivation chain is not an ownership tree: a piece cut from a piece
  returns its range to the pool root, not to the region it was cut from
  (`MemorySlot.root`'s doc; spec §5.9).
- **A handle** is a table entry naming the slot. `refs` counts handle entries
  and references parked in staged messages: raised by `ref_object`
  (`kernel/core/refs.saw`, Memory arm) at every bind, lowered by
  `drop_reference` (Memory arm) at every release, unbind and teardown
  close-all. A fresh slot is written with `refs: 1` by its creator (`Split`,
  `mint_boot_regions`).
- **A row** is a `GrantRow` `{base, top, pa, perms, device, region}` in a
  process body's grant record. `region` is the `Memory` whose bytes the row
  grants, or `NO_MEMORY`. Rows are made only by `record_grant`
  (`kernel/core/process.saw`), whose callers are `place_image`
  (`kernel/core/loader.saw`: a child's copied segments and stack name its
  destination, its in-place segments name its image, root's rows and device
  segments name `NO_MEMORY`) and `install_row` (`kernel/core/dispatch.saw`:
  `MemoryOp.Map` passes the region, `IoMemoryOp.Map` and the stats map pass
  `NO_MEMORY`). Rows are taken away only by `remove_grant` (whose one caller
  is `MappingOp.Unmap`) and `clear_domain` (the teardown).
- **`maps`** counts the rows naming the region. It moves in exactly four
  places: `ref_region_row` from `record_grant` (+1); `unref_region_row` from
  `remove_grant` and `clear_domain` (−1, then a release check); and
  `rows_move_to_piece` (`kernel/core/process.saw`), which at a split moves
  rows from the parent's count to the piece's.
- **A `Mapping`** (`MAPPINGS` slab, `MappingSlot` `{process, target, row,
  refs}`) owns one row and is not a reference on the region
  (`kernel/core/refs.saw`, `free_object`, Mapping arm: the slot frees and the
  row stays). The row's count on the region is what holds the bytes.
- **Husks.** Three states keep a slot after its last handle, all in the code:
  - a region with `refs == 0` and `maps > 0`: its bytes are still granted, so
    `memory_release_if_idle` (`kernel/core/objects.saw`) keeps the slot and
    its charge until the last row goes;
  - a pool root with `refs == 0`, `maps == 0` and `out_bytes > 0`: the same
    function keeps it, because pieces on loan carry its index in `root`;
  - a Mapping, in two kinds: unmapped but referenced (`row == NO_ROW`, a
    later `Unmap` is `BadState`) and mapped but unreferenced (slot freed, row
    installed until the target's teardown; `MappingOp.Unmap`'s comment).

### Release

`memory_release_if_idle(slot)` (`kernel/core/objects.saw`) frees a region
when `refs == 0` and `maps == 0`, and for a pool root also `out_bytes == 0`.
A piece's range goes to its root through `pool_return`, which lowers
`out_bytes`, inserts and folds; then the function tail-calls itself on the
root, which is one level deep because a root's root is itself. The slot is
zeroed and `credit_shared(owner, QuotaKind.Memory)` refunds the charge
(nobody, if disowned). It is called from `free_object`'s Memory arm
(`kernel/core/refs.saw`, when `refs` reaches zero) and from
`unref_region_row` (when a row goes). The order "row out of the record and
revoked in hardware, then the count drops" is the contract in `remove_grant`
and `clear_domain`.

A donated region never returns: `memory_retire_donated` frees the slot and
leaves the range counted in its root's `out_bytes` forever.

### `split`

`MemoryOp.Split` (`kernel/core/dispatch.saw`, `memory_op_decoded`), in order:

1. `MemoryRight.Split` or `AccessDenied` (fault).
2. `len == 0`, or `pool_can_serve` says no: `BadArg` (fault).
3. `len % hal.PROT_GRAIN != 0`: `BadArg` (fault).
4. `pool_cut_base` predicts where the cut lands (first free range that holds
   `len` on a root, else the region's own `base`); `rows_clear_of_cut` scans
   every body's every row and answers false if any row counted on this
   region straddles the cut: `NoResource` (status), nothing changed.
5. Quota (`QuotaExceeded`), slab (`NoResource`), handle (`mint_refusal`).
6. `pool_cut`: on a root, first fit off the free list, else the front of the
   frontier, and `out_bytes += len`; on a piece, the front of its own range,
   `base += len`, `len -= len`, with `out_bytes` unchanged (the bytes were
   already on loan).
7. The new slot: `{process: caller, base, len, refs: 1, maps: 0, root:
   parent's root}`, minted with the source handle's rights capped by the
   caller's ceiling (`mint_capped`).
8. `rows_move_to_piece` scans every body's every row again and moves each
   row counted on the parent and lying wholly in the cut to the piece's
   count.

The two scans are `process_slots_usable()` bodies times each body's
`grant_count` rows, which is at most `hal.GRANT_ROW_BUDGET` (8 on riscv32,
9 on arm64; `MAX_GRANT_ROWS` is the record's size, 9), skipped when
`maps == 0`; neither takes a preemption point. *(Revised after review: the
row bound is the budget, not the record size.)*

### `IoMemory` and `carve`

`IoMemorySlot` (`kernel/core/objects.saw`) is `{state, process, base, len,
refs}`: no `maps`, no `root`. `IoMemoryOp.Carve` (`kernel/core/dispatch.saw`,
`iomemory_op_decoded`) takes `offset` and `len`, faults `BadArg` for a range
outside the parent or one `hal.device_window_ok` refuses, and writes an
independent slot over `[parent.base + offset, + len)`. The parent is not
touched and nothing records the relation. A device row names `NO_MEMORY`, so
mapping a window counts on nothing. `free_object`'s IoMemory arm
(`kernel/core/refs.saw`) frees the slot and credits the charge and touches no
bytes.

**So carve is not a view that holds its parent. It is a view that holds
nothing**, and that is sound only because MMIO bytes are never returned to
anyone (spec §5.9: "IoMemory is the carve-out"). A carved window may outlive
the slot it was carved from. A `Memory` slice cannot copy that lifetime,
because RAM bytes do go back to a pool: it can copy carve's argument shape
and its absolute-base recording, not its lifetime.

### Donation

`slab_donate` (`kernel/core/dispatch.saw`) checks, in order: the kind
(`BadArg`), the handle (`BadHandle`), a region with bytes that wraps the
address space (`region_wraps`) or that `hal.map_source_ok` refuses
(`BadArg`), `refs != 1` (`BadState`),
`maps != 0` (`BadState`), a root with `out_bytes != 0` (`BadState`), then the
geometry answers (`NoResource`), zeroes, installs, unbinds the handle and
calls `memory_retire_donated`.

### Quota

A split charges the caller's `QuotaKind.Memory` (`quota_charge` in the
Split arm); the refund is at the slot's free (`credit_shared` in
`memory_release_if_idle` or `memory_retire_donated`). A child's limit is
`DEFAULT_QUOTA_MEMORIES` (4, `kernel/core/limits.saw`); root's is unlimited.
Bytes are not charged (spec §2.9). At a teardown, `disown_shared` →
`disown_region` (`kernel/core/refs.saw`) writes `NO_PROCESS` into every live
region still charged to the dying record, before `clear_domain` drops its
rows (`kernel/core/sched.saw`, `teardown_process`, steps 3 and 4).

## 3. The proposed model, as rules

Terms used below:

- A **backing region** is a `Memory` with `parent == NO_MEMORY`: a pool root
  or a piece. A **slice** is a `Memory` whose `parent` names a backing
  region. `backing(m)` is `m` for a backing region and `m.parent` for a
  slice.
- A backing region is **in use** when `maps > 0` or `slices > 0`.

**R1. Two fields.** `MemorySlot` gains `parent: Int` (`NO_MEMORY` on a
backing region) and `slices: UInt32` (the number of live slices whose
`parent` is this slot; always zero on a slice). On a slice, `maps` is always
zero (R9), and `root`, `free_head`, `out_bytes` and `dropped` are unused and
written as `NO_MEMORY` and zero.

*(R2 to R4: superseded by §11, where an allocation has no `split` at all.)*

**R2. `split` refuses a region in use.** `MemoryOp.Split` on `m` keeps steps
1 to 3 of today's order (rights, then the three `BadArg` faults). Then, if
`m.maps != 0 || m.slices != 0`, it answers a status with nothing changed.
Which status is open question Q1; I recommend `WouldBlock`. It is a status
and not a fault because the caller cannot always know: a sibling holder may
have mapped the region, a slice may sit in another process's table or a
staged message, a child's teardown may be in progress, and a Mapping dropped
without an unmap leaves its row until its target dies. Step 4 of today's
order (the cut prediction and the row scan) is deleted.

**R3. A slice never splits.** A slice is minted without
`MemoryRight.Split` (R8), so `split` through a slice handle is
`AccessDenied`, a fault the holder can check by reading `rights()`. The
Split arm also asserts `m.parent == NO_MEMORY` and stops the machine
(`fatal_kernel`) if not, as an invariant no syscall can reach.

**R4. Everything else in `split` is unchanged.** First fit off a root's free
list, else the front of the region's range; the piece is a backing region
with `parent: NO_MEMORY`, `root` carried down, no rows. `rows_clear_of_cut`,
`rows_move_to_piece`, `row_pa_end` and `pool_cut_base` are deleted.

**R5. `slice(offset, len)`.** A new `MemoryOp.Slice` on `m`, a backing region
or a slice. In this order:

1. `MemoryRight.Slice` on the handle, or `AccessDenied` (fault).
2. `region_wraps(m.base, m.len)`, `len == 0`, `offset > m.len`,
   `len > m.len - offset`, `(m.base + offset) % hal.PROT_GRAIN != 0` or
   `len % hal.PROT_GRAIN != 0`: `BadArg` (fault), as `Map` faults a
   wrapping region and carve a window outside its parent. The wrap test
   comes first, so `m.base + offset` cannot overflow. The bounds are `m`'s
   own range, so a slice of a slice is never wider than the slice it came
   through. The alignment is of the slice's own address, not of the
   offset, so every slice can be mapped without `Map` rounding its top past
   it. Consequently nothing of a region whose base is off the grain (a
   child image blob on arm64) and nothing of a region's last partial grain
   can be sliced; such bytes are reached only through the region itself.
   *(Revised after review: the wrap test was missing, and the alignment was
   relative to the source's base.)*
3. Quota (`QuotaExceeded`), slab (`NoResource`), handle (`mint_refusal`),
   each with nothing changed.
4. The new slot: `{process: caller, base: m.base + offset, len, refs: 1,
   maps: 0, slices: 0, parent: backing(m)}`; then `backing(m).slices += 1`.
   `m` is not otherwise touched.

Consequences stated as rules:
- **R5a. Flattening.** A slice of a slice names the backing region as its
  parent. No slice is ever the parent of anything, so there is no chain.
- **R5b. A region in use may be sliced.** That is the point of the op.
- *(R5c: superseded by §11; a pool has no `slice`.)*
- **R5c. A pool root may be sliced** (open question Q3). Its range is its
  unspent frontier, so the offset is relative to wherever the frontier is
  at the call. While the slice lives the root is in use and serves no split
  (R2): the pool is frozen. A piece returning while the root has a slice
  still folds into the frontier, which only moves `base` down, so no
  existing slice leaves the root's range.

**R6. Slices may overlap; one process's rows may not.** Slices may overlap
each other and anything else on their backing, and may be mapped into
different processes freely. A `map` whose placed range meets any row the
target already holds answers `NoResource` and installs nothing
(`meets_image_row` widened from the first `image_rows` rows to every row).
On arm64 `free_user_va` already places each mapping clear of every RAM row,
so the test never fires there; on the identity profiles it refuses a second
row over the same addresses, so a process's rows never overlap on any
profile. The sanctioned double map (§2.5) survives as two processes mapping
one region, or two addresses in one process on arm64; within one process on
an identity profile it is refused, which removes a promise the hardware did
not keep (below). The per-row W^X rule, the execute gate and the image-row
rule apply unchanged, and the any-covering-row rule becomes a defence that
no state can reach. *(Revised after review; the first text called overlap
the double map.)*

Why: on the PMP profile row `i` is region `i` (`load_domain` replays rows in
record order through `hal.prot_region(i, ...)`, and
`hal/riscv32-common/kernel/lib.saw`'s `prot_region` programs row `i` as the
TOR pair at entries `2i`, `2i + 1`), and PMP's lowest-numbered matching
entry decides an access. So an overlap has the first-mapped row's
permissions, not the union, and an access running from the overlap into the
later row's own bytes faults. The kernel's own questions disagree with
that: `translate_user` resolves through the first covering row (the same
answer as the hardware for a read), but `executable_at` accepts an entry in
any executable covering row, which the first fetch may fault on. That is
already so on main for identical ranges (a region mapped read-write then
read-execute into one process executes nothing while the read-write row
stands; `map_wx_refused` never fetches). Slices would add partial overlaps.
The alternative rule, "on a filter profile the first-mapped row decides",
would keep the overlap and make `executable_at` ask the first covering row
only; it is smaller but makes a row's effect depend on mapping order and on
whether an earlier row was unmapped, which no program can see. I recommend
the refusal.

**R7. A slice is a `Memory`.** Same `ObjType.Memory`, same handle type, same
sysapi `Memory`. Every op that takes a `Memory` takes one: `map` (over its
own range), `slice`, `MINT_OP`, `give`, a staged message, `process_create`
as image or destination. `split` is refused by R3 and `slab_donate` by R12.
A holder cannot tell a slice from a backing region except that a slice
lacks `Split`, and a backing region minted without `Split` looks the same.
That is intended: a region is a capability, not a description (§2.5).

**R8. Rights.** A slice is minted with the source handle's rights minus
`MemoryRight.Split`, capped by the caller's `Memory` ceiling
(`mint_capped`). A slice therefore never carries a right the handle it was
cut through lacked, and a sub-slice is cut through the slice's handle, not
the backing's. `MemoryRight.Slice` is a new bit (bit 11), minted by
`memory_rights()` at boot, because attenuation is monotonic and a bit not
minted at boot could never appear later.

**R9. Rows count on the backing.** Every row whose bytes are a slice's is
counted on `backing(m)`: `MemoryOp.Map` passes `backing(obj.slot)` to
`install_row`, and `process_create` passes `backing(dst)` and
`backing(img)` to `place_image`. So a backing region's `maps` counts every
row over its bytes however it was installed, and no row ever names a slice.
`ref_region_row` asserts its argument is not a slice (`fatal_kernel`).

**R10. Release.** In `memory_release_if_idle(slot)`:
- **a slice** is freed when `refs == 0` (its `maps` is always zero): the
  slot is zeroed, its charge credited to `process` (nobody if disowned),
  `parent.slices -= 1` (stopping the machine if it was already zero), then
  `memory_release_if_idle(parent)`. A slice never calls `pool_return`.
- **a backing region** is freed when `refs == 0`, `maps == 0` and
  `slices == 0`, and for a pool root also `out_bytes == 0`, exactly as today
  otherwise.

The longest walk is slice → backing piece → pool root: three calls, fixed by
R5a and by a root's root being itself. Nothing enumerates a region's
slices; there is no back-link from a parent to them.

**R11. Quota.** Creator pays: a slice charges the slicer's
`QuotaKind.Memory`, one per slice, as a split does, and is refunded when the
slice's slot frees, or written off when the slicer dies (`disown_region`
already covers every Memory slot). Bytes are not charged: a slice adds no
bytes to anyone's ledger, since its backing's bytes are already counted once
in its root's `out_bytes`. A process that holds only a slice it was given is
charged a `Handle` row for the entry and nothing else.

**R12. Donation.** `slab_donate` faults `BadState` for a slice
(`parent != NO_MEMORY`) and for a backing region with `slices != 0`, beside
today's `refs != 1`, `maps != 0` and a root's `out_bytes != 0`, after the
region rule's `BadArg`. A fault, as today's state refusals are. The
reasoning is the comment above `slab_donate` (`dispatch.saw`: the caller
"could have checked ... (it minted the siblings, installed the rows, split
the pieces)"); spec §2.8 states only the refusal. That reasoning is false for
a region received from another process, which may be a slice or have one,
and R2 answers the same kind of unknowable condition with a status. The
difference is justified only by who donates: `SystemRight.SlabDonate` is a
machine-wide right, and a donor's contract is to donate memory it allocated
and never handed on, which it can know. Under forms B and D (§10) R2 has no
in-use case left, so the inconsistency disappears with it; under the design
as written the owner may prefer the status for both (Q1's word), which would
also move today's `refs` and `maps` refusals and the cases asserting them.
*(Revised after review: the reason given was wrong for a received
region.)*

**R13. Teardown: no new step.** A slice in the dying table is released by
the close-all like any handle, which may free the slice and, through R10,
its backing and its pool root in the same release. A slice or backing that
survives and is charged to the dying record is disowned by `disown_region`.
A backing whose charged process dies while slices or rows remain stays live,
owned by nobody, until the last of them goes.

**R14. `process_create`** is unchanged except for R9. A slice is accepted as
image and as destination; the loader uses the slice's own range, and the
disjointness test, the destination size test and `hal.map_source_ok` apply
to it. Two creates over overlapping ranges of one backing are not policed,
as today one region passed twice is not (the doc above `process_create`:
"a root double-spending what it holds corrupts only what it holds").

**R15. Segments used in place.** A slice of an image `Memory` is an image
like any other; its in-place rows name its backing (R9). A slice of a flash
region may be an image; as a destination it is `BadImage`, mapped it is
`BadArg` (`hal.map_source_ok` in `MemoryOp.Map`), donated it is `BadArg`
(the region rule comes first).

**R16. `map` is unchanged** except R9. `meets_image_row` compares the placed
range with the target's image rows, so a slice whose range meets one is
`NoResource` on the identity profiles, as its parent would be; on arm64 a
mapping is placed above the region and meets no image row.

**R17. The count is wide enough by construction.** Every slice is a Memory
slot, so `slices` is below the Memory slab's capacity, at most
`MAX_SLAB_CAPACITY` (2^20, `kernel/core/limits.saw`). `slices` is `UInt32`,
with `static_assert(MAX_SLAB_CAPACITY <= UInt32.max)` beside it. Overflow
checks are always on, so an overflowing count would be a kernel panic; this
makes it unreachable.

**R18. The copy windows.** On the identity profiles a slice of a process's
own destination, lying in the hole between its data and its stack, can be
mapped into it, and the row is then inside both copy windows. This is
possible on main through a split piece, and I recommend it stays possible
(Q6): it is the launcher's authority over its own region, and it is the
route by which the late copy doors are tested.
*(Revised after review: §10.10 weighs the opposite answer, refusing any
mapping that meets the target's copy windows, and recommends it.)*

**R19. `IoMemory` is unchanged.** Its carve keeps its lifetime (it holds
nothing), because device bytes are never returned.

## 4. What each rule is for

- **R1** carries R5 and R10: without `parent` a slice's release would return
  bytes (a double return: `pool_return`'s "more bytes than it ever lent"
  stop, or worse, a range on the free list twice); without `slices` a
  backing could be freed under a live view.
- **R2** is the owner's rule and replaces SO-91's. SO-91's finding was that
  a split could move a live child's bytes into a piece whose release
  returned them to the pool (`tests/split-live-bytes`). Main closed it by
  moving rows to the piece and refusing a cut through a row. R2 closes it by
  never cutting a region anybody uses, which also closes SO-91's two
  neighbours (a mapped region split and released while mapped; a piece of a
  child's destination accepted by `slab_donate`) without a scan.
- **R2's `slices` half** is new and prevents N2 in §5: a split of a backing
  region under a slice would move the slice's bytes into an independent
  piece whose release frees them while the slice still names them.
- **R3** keeps the allocator out of views: a split of a slice would cut a
  "piece" whose bytes are another region's, and its release would return
  them (N4-like).
- **R4** is the simplification.
- **R5** step 2 against `m`'s own range prevents a sub-slice wider than its
  source (N5). Grain alignment keeps every slice mappable without granting
  bytes past it (a grant covers whole units, `MemoryOp.Map`'s
  `align_up`). **R5a** bounds the release walk and rules out cycles (N7,
  N8).
- **R6** is the double map as already sanctioned; restricting overlap would
  need a scan of the backing's slices and buys nothing a holder of the
  parent cannot do anyway.
- **R7** lets a slice be a destination, which the late-door route and the
  "child plus shared buffer from one allocation" use need, and keeps one
  type in the sysapi.
- **R8** is no-amplification (§3), applied to range as `MINT_OP` applies it
  to rights. Dropping `Split` is R3's enforcement.
- **R9** is what makes R2 one O(1) test and keeps a row's `region` naming a
  slot that cannot free under it (N10). It also keeps true the sentence the
  spec already has: a row counts on the region whose bytes it grants.
- **R10** is the second-zero rule, extended by one count, and the bound on
  the walk.
- **R11** keeps creator-pays and quota ≤ wall: each slice is a slab slot,
  charged once.
- **R12** extends the rule SO-91's review found broken on main before its
  fix (kernel slabs donated inside a child's image) to views: a donated
  slice would have the kernel zero and own bytes other handles, slices and
  rows still name.
- **R13** says the existing teardown order is enough; the disown before the
  domain clear (§2.10) still makes every late free credit nobody.
- **R14, R15, R16** say slices change nothing the image rules defend
  (SO-84's in-place rows hold the image; SO-85's mapping over an image row).
- **R17** prevents a kernel panic from a count.
- **R18** keeps SO-90's late doors reachable by a program, so their case
  still holds them.

## 5. Attack

### The earlier findings

- **A live child's bytes freed (SO-91).** Impossible by construction under
  R2 and R10: a region with rows cannot be cut, a slice never returns bytes,
  and a backing returns its range only when it has no rows, no slices and no
  handles. Main's scan and its mid-row refusal go.
- **Kernel slabs donated inside a child's image (SO-91's neighbour,
  SO-84.p2's review item 6).** Still needs donation's checks, now
  `maps == 0`, `slices == 0` and "not a slice" (R12). What becomes true by
  construction is that the check is sufficient: no piece can be cut from a
  region with rows, so every row's bytes stay inside the region it counts on.
- **A mapped buffer unmapped under a parked thread (SO-90).** Still
  reachable, by design (R18): map a slice of the child's destination into
  its hole, park, unmap. It still needs main's late doors (`wake_one_waiter`,
  `wake_call_reply`, `resume_call_room` re-asking the early check, and
  `doom_process`). The route exists on main through a split; R18 keeps it.
- **A mapping laid over a child's image (SO-85).** Still needs
  `meets_image_row` in `install_row`, which catches a slice exactly as it
  catches its parent (R16).
- **A writable and an executable view of the same bytes (SO-85).** Still
  expressible, as on main (the sanctioned double map; R6), so the
  any-covering-row rule in `translate_user` is still needed. On arm64 the
  two views are two address ranges (SO-89, unchanged by slices).

### New holes, each with a sequence

**N1. A slice outliving its parent's handles.** L holds piece R; `S =
R.slice(0, 0x1000)`; L releases R; L releases nothing else. Without R1/R10
R's `refs` reaches zero and its range returns while S names it. *Closed by*
`slices` in R10: R stays live, `refs == 0`, `slices == 1`, a husk of a new
kind, until S's last handle goes.

**N2. A split under a slice.** L holds piece R (unused); `S = R.slice(0,
0x1000)`; `P = R.split(0x1000)`: P is S's bytes, as an independent piece;
L releases P: the bytes return to the pool while S can still map them. *Closed
by* R2's `slices != 0` test.

**N3. Donating a parent with slices.** As N2, then `slab_donate(R)`: the
kernel zeroes and owns bytes S still maps. *Closed by* R12. Note that on main
`refs != 1` would not catch it, since a slice is not a handle on R.

**N4. Donating a slice.** `S = R.slice(...)`; `slab_donate(S)`: S has
`refs == 1` and `maps == 0`, so today's checks pass it, and the kernel would
write slabs into bytes R and its other users still name. *Closed by* R12's
"not a slice".

**N5. A sub-slice wider than its source.** L gives B `S = R.slice(0,
0x1000)` of a 1 MiB R; B calls `S.slice(0, 0x10_0000)`. If the bounds were
checked against the backing (because of flattening), B would get all of R.
*Closed by* R5 step 2: bounds are `m`'s own range.

**N6. Rights through flattening.** As N5, with S minted without `MapExecute`;
if a sub-slice took its rights from the backing it names, B would gain them.
*Closed by* R8: rights come from the handle the op went through.

**N7. The depth of the release walk.** `S1 = R.slice`, `S2 = S1.slice`,
`S3 = S2.slice`, ... If each slice held the one it was cut from, releasing
the last handle of a deep chain would recurse once per level, with depth set
by a program. *Closed by* R5a: every slice's parent is a backing region, so
the walk is at most slice → piece → root.

**N8. Cycles.** A slice holds its backing; a backing holds nothing (a piece's
`root` is a back-reference kept valid by the root's `out_bytes`, not a hold
the piece makes); a slice is never a parent. A staged message can hold a
slice, and a slice holds no pipe. No cycle can form.

**N9. The parent's count overflowing.** Root has an unlimited Memory quota
and may donate slots to the Memory slab up to `MAX_SLAB_CAPACITY`, so a
`UInt16` count of slices could pass 65535 and panic the kernel. *Closed by*
R17.

**N10. A row naming a slot that freed.** If rows counted on the slice (the
alternative, below), a slice's slot would have to stay live while any row
names it, or `unref_region_row` would decrement a freed or reused slot.
*Closed by* R9: rows never name a slice.

**N11. A pool frozen by a slice.** Root holds pool root P; `S = P.slice(0,
0x1000)`; every later `P.split` answers the in-use status until S's last
handle goes. Not a hole (sound by R2 and R5c) but a trap for a program
author; Q3 asks whether to refuse slicing a pool root instead.
See §10: with pools and allocations as two kinds, N11 cannot be written.

**N12. Pinning and freezing a parent from another process.** L holds a 1 MiB
piece R meant to be split into buffers; L gives B `R.slice(0, 0x1000)`; B
never releases it. R can no longer be split or donated, and none of its
1 MiB returns, until B lets go or dies (and `kill` is not built, §11).
*Not closed.* Compare main: B holding a sibling handle of R already pins all
of R; B holding a split piece pins only the piece; B having R mapped pins it
until B's teardown. A slice pins like a sibling and, new, freezes the split.
I do not think the kernel should police it: the giver chose a slice over a
piece, and a piece is the remedy when the receiver is not trusted to let go.
The spec should say "a slice pins its whole parent". (Q5.)

**N13. A split refused forever by a leaked row.** L maps R into child A and
releases the Mapping handle without unmapping (§2.5's safe-but-leaked
stance). R is in use until A's teardown, so `R.split` answers the in-use
status until then. *Not closed*; it follows from the leak stance and is the
same bound the leak already has.

**N14. A second create into a running child's bytes.** L creates A in slice
S1 of R, then creates B in slice S2 overlapping A's image; B's load
overwrites A's memory. *Not closed*, by the existing doctrine: on main L can
pass the same region to two creates (`tests/image-split` and
`tests/process-donate` do pass one destination to several creates). Slices
add nothing L could not already do.

**N15. A second route to a mapped row in a copy window.** On an identity
profile, a child whose image has two non-adjacent read-only segments used in
place has a copy-in window (`ro_base`..`ro_top`, `place_image`) spanning the
gap; a slice of the image `Memory` over the gap, mapped into the child,
meets no image row and lies in the window. Main allows the same through a
split piece of the image. *Not closed*, and needs nothing new: the late
doors handle any mapped row. Unverified: whether any board's link script
can produce such an image; none in the tree does (the ESP32-S3's read-only
data is one segment, and its flash cannot be mapped), so it would take a
hand-assembled payload.

**N16. Quota evasion.** A child with Memory quota 4 slices four times and
gives the slices away: they stay charged to it, so a fifth is
`QuotaExceeded`. If it dies, they are disowned and charged to nobody, bounded
by the slab, as split pieces are today. No evasion found.

**N17. More than a holder could have been given whole.** B can hold a slice
only if somebody holding the backing (or a wider slice of it) cut it, with
no more rights than that handle had (R8) and no wider range (R5). From a
slice B can map its range, slice inside it, mint, give, create a process in
it or use it as an image. It cannot split (R3), donate (R12), map, slice or
load anything outside its range. Its only effect on the parent is N12.

**N18. Time.** No op scans. `slice` is `alloc_memory`'s slab walk, as
`split` is; R2 is two field reads; R10 adds a constant step. Main's split
scan is removed.

### Counting rows on the slice instead (rejected)

The other reading of "a slice holds its parent" is: a row installed through a
slice counts on the slice, and the slice holds the parent while its own
`refs` or `maps` is nonzero. It works, but every slice then needs the husk
logic, the walk gains a level (row → slice → backing → root), and R2 must
ask "does any slice of this region have rows", which is either a third count
on the backing or a scan. R9 makes the backing the one place every use is
counted.

## 6. What it costs and what it buys

**Goes from the kernel, sysapi and spec** (line counts are of main):

- `kernel/core/process.saw`: `row_pa_end`, `rows_clear_of_cut`,
  `rows_move_to_piece` and their docs (lines 678 to 755, 78 lines).
- `kernel/core/objects.saw`: `pool_cut_base` (lines 1016 to 1034, 19 lines);
  the sentence in `MemorySlot.maps`'s doc about the split.
- `kernel/core/dispatch.saw`: the Split arm's cut prediction, row check and
  row move, their comments and three imports (about 17 lines).
- `spec.md` §2.5: the bullet "A split carries a region's rows to whichever
  piece covers them" (11 lines).
- `kernel/sysapi/src/memory.saw`: the paragraph on rows travelling with the
  piece (3 lines).
- Concepts: attributing a row to a piece by its physical range; the mid-row
  refusal; the place where `maps` moves between regions (it then moves only
  at `record_grant`, `remove_grant` and `clear_domain`).

About 128 lines.

**Comes in** (estimates, not counted):

- `kernel/abi`: `MemoryOp.Slice` and its doc, `MemoryRight.Slice` with its
  `static_assert`, `memory_rights()` (about 25 lines).
- `kernel/core/objects.saw`: `parent` and `slices` with docs, the two new
  fields in every `MemorySlot` literal (the slab initializer,
  `mint_boot_regions`, the Split arm, the two retire sites), the slice arm
  of `memory_release_if_idle`, `backing_of` (about 45 lines).
- `kernel/core/dispatch.saw`: the Slice arm (about 45 lines with comments,
  on carve's model), the in-use test in Split, two donation tests,
  `backing_of` at `Map` and `process_create` (about 20 lines).
- `kernel/sysapi`: `Memory.slice(offset:len:)` with its doc, the floor
  export `sos_memory_slice`, the facade line (about 50 lines).
- `spec.md`: the slice paragraph, the in-use rule, the third count in the
  refcount invariant, the donation sentence, §2's table row and §11's
  "a slice pins its whole parent" (about 40 lines).

About 180 to 220 lines: a net gain of 50 to 90. Concepts added: a view kind
of region with a parent link, a third count, an op, a right. Honest summary:
it is not smaller. What it buys is a simpler invariant and a cheaper split:

- **Spec:** "Bytes leave a region only through `split`, and `split` refuses a
  region with rows or slices" replaces the row-carrying rule and its
  mid-row status. Every row and every slice lies inside its backing for the
  backing's whole life (a range shrinks only by split, and only an unused
  region splits), so the second-zero rule needs no qualification about
  which piece a row moved to.
- **Time:** main's split of a region with rows does two scans of
  `process_slots_usable()` × `grant_count` rows with no preemption point
  (with donated bodies, up to 256 × 8 on riscv32, whose `PROT_DOMAIN_SLOTS`
  is 256 and `GRANT_ROW_BUDGET` 8; revised after review); the new split
  does none.
- **Reasoning:** the release condition is local to one slot again; nothing
  about a region depends on scanning other processes' records.

## 7. Existing cases that split a region in use

*(Superseded by §11.5 for what is built.)*

Every case that calls `split`, `carve` or `slab_donate` was read for the
order of its splits against its creates and maps. Only these split a region
that has rows at the time; the rest split pool roots or unused pieces and
are unchanged (`memory_split`, `memory_recycle`, `map_*`, `mapping_*`,
`split_keeps_rights*`, `slab_donate*`, `process_donate`, `image_held`,
`donate_empty`, `flash_region`, `unstarted_released`, `ceiling_derived`,
`child_*`, `pipe_donate`, `thread_donate`, `quota_vs_wall`,
`share_double_map`, `mint_mapping`, and the IoMemory carve cases).

- **`split_live_bytes`** (all three profiles). Today: the child parks in
  `dest` (a boot region); root cuts the front of `dest`, releases it, cuts
  the next piece and maps it, and finds the child's pattern absent; after
  the death a fresh cut of `dest` carries it. **Becomes:** the first cut is
  the in-use status (the line asserting it changes); there is no "next
  piece" while the child lives, since `dest` cannot be cut; after the death
  `dest.split` succeeds and the piece carries the pattern. A leg on `slice`
  can keep the release half: a slice of the front, released, and the
  child's pattern intact. Mutant: drop R2's test; the cut is taken.
- **`split_mapped_bytes`** (all three). Today: a cut through a mapped
  two-page region is refused; the whole region is cut off and released
  while mapped; a fresh pool piece does not share the bytes; after the
  unmap one carries the mark. **Becomes:** both cuts are refused (the first
  line's status may change, Q1); the "released while mapped" leg is
  rewritten as releasing the region's own handle while mapped (the husk of
  §2.5), which keeps the fresh-piece measurement and the after-unmap one.
- **`split_rows_held`** (all three). Today: cuts through the image and
  through the stack are refused; a front cut between them carries the image
  rows and its donation is `BadState`. **Becomes:** every cut is refused,
  so the three cut lines collapse into one refusal; the donation leg is
  rewritten on a slice of the front (`BadState` by R12), and a second
  variant donates `dest` itself (`BadState` by `maps != 0`, which main
  already enforces). The SO-91 neighbour it guards stays guarded.
- **`image_split`** (riscv32, riscv32-flat) and **`image_split_flash`**
  (ESP32-S3). Today: a cut through the in-place code row is refused; the
  whole image is cut off with the child's row, released, and the blob's
  next cut is filler; after the death the image comes back; a second
  child's image cut off with its row and donated ends root (`BadState`, or
  `BadArg` on flash). **Becomes:** both cuts of the image are the in-use
  status; the "bytes held" leg is written by releasing the `image` handle
  itself while the child runs (the rows hold it; this is `image_held`'s
  shape, so the leg may simply be dropped as duplicate); the after-death
  leg is unchanged; the donation is of the image region itself or of a
  slice of it, with the same final fault line. Main's mutant
  ("`rows_move_to_piece` skipping image rows") has nothing left to mutate;
  the new mutant is R2's test or R9's `backing_of` in `process_create`.
- **`late_door_buffer_gone`** (riscv32, riscv32-flat). Today: `region()`
  cuts `dst` for the child, creates it, then cuts `front` and `hole` off
  `dst` and maps `hole` into the child; every trial, the exec-row trials
  that hold SO-85's any-covering-row rule included, uses that hole.
  **Becomes:** `let hole = try dst.slice(offset: HOLE_AT, len: HOLE_LEN)`,
  with `front` gone. The transcript rows are unchanged. The trials' mutants
  (each door's re-check, each disposal, the drain order, the any-covering
  rule) are unchanged, since none touches the region path. One effect: each
  trial's `dst` now returns as one range at the child's death and the
  hole's release, instead of as three.

New cases the model needs (names suggested): `split_in_use` (the status for
rows and for slices; split works once they are gone), `slice_bounds`
(offset, length and alignment faults; a sub-slice bounded by its slice;
`Split` absent from a slice's rights), `slice_holds_parent` (a backing's
bytes return only after its last slice and its last row; a slice of a slice
outlives the slice it was cut through), `slice_not_donated` (a slice and a
parent with a slice are `BadState`), `slice_owner_dies` (the slicer's
teardown with its slice mapped in another process; the bytes come back at
that process's unmap and release), `slice_quota` (R11: a slice is charged
to the slicer, the limit is met with `QuotaExceeded`, the release refunds
it, the slicer's death disowns it), `slice_keeps_rights` (N6: a sub-slice
of a slice minted without `MapExecute` cannot map executable), and, with
R6 as revised, `map_overlap_refused` (a second row over a target's
addresses is `NoResource` on the identity profiles). *(The last three added
after review.)* Under R6 as revised, `map_basics`, `map_wx_refused` and
`map_exec_gated` change on riscv32 and riscv32-flat: each maps one region
twice into the same process, and the second `map` is then `NoResource`
there while arm64 installs it at a second address; each is rewritten to
map the second view into another process.

## 8. Alternatives

**Keep main as it is.** It is correct and tested on every profile: SO-91's
rule carries rows to pieces and refuses a mid-row cut, and three cases plus
two image cases hold it. Its costs are a cross-process scan in every split
of a region with rows, a status (`NoResource`) whose text the reviewer
noted does not fit, and a rule about attribution by physical range that a
reader has to understand to trust the second-zero rule. It keeps a use the
new model loses: reclaiming the unused middle of a child's region as an
independent piece (which, with the child's region, I found no program
doing). If the owner values the smaller kernel in lines over the simpler
invariant, this is the choice; it is not wrong.

**Refuse the split and add nothing.** The smallest change: R2 and nothing
else. What becomes unreachable through the syscall path: every mapped row
inside a copy window (the route was a piece of a child's destination, and
the image-gap route of N15 needs a split of the image too), so the late
doors' re-checks and `doom_process` disposals are dead code to every
program, none of the trials of `late_door_buffer_gone` can be written,
the any-covering-row rule has no case at all, and SO-84's and
SO-91's split cases become refusals. The doors could be held instead by a
harness kernel (`tests/*.saw` entries build kernel state directly), at the
cost of testing them below the syscall path. I would not choose this: it
removes a sharing shape (part of a buffer handed on) and leaves correct code
only indirectly tested.

**`slice` as views, `split` unchanged.** Add slices to main. Split must then
refuse a region with slices anyway (N2), so it is R2's slice half plus
main's row scan: two mechanisms over one question. Not chosen.

**A sub-range `map` instead of a slice object.** `map(process, access,
offset, len)` installs a row over part of a region, with rows counting on
the region and R2 as the split rule. No new object, no parent link, no third
count; the late-door route works (the launcher maps part of the child's
region into it). It cannot hand a sub-range on as a capability (another
process cannot install it itself, cannot create a child in it), and it needs
four argument words where the ABI has three registers on a 32-bit tier
(spec §5.7, the reason `Timer.Arm` uses copy-in), so it would need a record
through the copy-in door. A real contender if handing sub-ranges on is not
wanted; I prefer the slice for that one capability.

**Forbid a mapping inside the target's copy windows.** Then every buffer row
is an image row, which nothing removes, so a buffer can never vanish under a
parked thread and the late-door re-checks could become assertions; it would
also make the identity profiles agree with arm64, where mapped memory is
never a buffer. A larger change of a different unit (it takes away
something SO-90 deliberately made safe); noted, not proposed.
*(Revised after review: §10.10 weighs this as the other answer to Q6 and
recommends it.)*

## 9. Open questions for the owner

**Q1. What a split of a region in use answers.** `WouldBlock` ("not right
now — try again": true, and its doc already says "the peer's to clear rather
than the machine's"), `NoResource` (main's answer, whose text "out of kernel
objects" is false here), or a new status such as `InUse`. **Recommend
`WouldBlock`**, with the doc noting that nothing signals when the region
becomes idle. *(Revised after review: recommend a new status word instead.
`WouldBlock`'s doc promises that a peer's act changes the answer, and a row
left by a Mapping dropped without an unmap may never clear before its
target dies. Moot under forms B and D, §10.)*

**Q2. Which right `slice` spends.** A new `MemoryRight.Slice` (independent
masking; a launcher's allow-list masks grant nothing new), `Mint` (a slice is
a sibling narrowed by range, as `MINT_OP` narrows by rights; no new bit, but
then a child that may make siblings may also freeze its memory), or `Split`
(then a slice, which lacks `Split`, could not be sliced). **Recommend a new
`Slice` bit.**

**Q3. Whether a pool root may be sliced.** Allowing it (R5c) is the uniform
rule and freezes the pool while the slice lives (N11), with offsets relative
to a frontier that moves. Refusing it (`BadArg`) is a special case a child
given a root cannot check. **Recommend allowing it**, and saying in the
sysapi doc that a program slices pieces, not pools.
Superseded if §10's form B is chosen: a pool has no `slice` op at all.

**Q4. The name.** `slice` (the owner's word) or `carve` (§2.5 already defines
carve as "the parent stays whole", and the arguments are carve's). **Recommend
`slice`**: the lifetime differs (a slice pins its parent, a carve holds
nothing), and that is the one thing a program author must not assume.

**Q5. Whether "a slice pins and freezes its whole parent" is acceptable**
(N12), or slices should be bounded somehow (a per-backing limit, or slices
refused on a region that is still being allocated from). **Recommend
accepting it and documenting it**; the remedy is to give a split piece.
(After review: `kill` is not on main at `cc89c82a`, `rights.saw` still
says "There is no `Kill` right"; it is in review.)

**Q6. Whether a slice of a process's own destination may be mapped into it**
(R18). **Recommend yes**: it is possible on main, it is the launcher's own
memory, and it keeps the late doors testable from a program.
*(Revised after review: §10.10 gives both answers and recommends no.)*

## Found on the way

- **Spec §2.5, `split`'s first bullet** says a split is "one cut from the
  front, and the parent becomes the remainder". On a pool root it is first
  fit off the free list, and the root's range is unchanged when a free range
  serves it (`pool_cut`); §5.9 says so, §2.5 does not.
- **Spec §2.5's release rule** ("the range returns to its pool at the second
  zero") omits a pool root's third condition: its slot stays while
  `out_bytes != 0` (`memory_release_if_idle`). §5.9 names `out_bytes` only as
  accounting.
- **The SO-92 brief's "a mapped row inside a process's copy windows could be
  made only by cutting a piece from a child's destination"**: also from its
  image, on an identity profile, when the copy-in window spans a gap between
  in-place read-only segments (N15). Unverified that any buildable image has
  such a gap.
- **Unverified, pre-existing:** `MemorySlot.refs` is `UInt16` and counts
  staged-message references as well as handle entries; with pipe slabs
  donated, staged references are bounded by ring capacity, not by
  `MAX_HANDLES`, so a holder able to mint and post repeatedly might exceed
  65535 and trip the overflow check. Not worked through; worth a look
  separately from this unit.
- **Unverified, pre-existing:** `MemoryOp.Map` rounds the top up to the
  grain ("a region shorter than one grant unit still gets a whole one"), so
  a region whose length is not a grain multiple (a boot region with an
  unaligned length, or the remainder of one) grants bytes past its end. I
  did not check whether any region table has such a length.

## 10. Addendum: pools and allocations as two concepts

The owner's follow-up: "Does it make sense to have 2 concepts of memory -
allocated (split from the pool) and unallocated (in the pool). Then a slice
operation on unallocated memory would fail so the issue of holding the root
memory pool by creating a slice is impossible." Read against main at
`25394018`, whose kernel, tests and runner are the same as at `ba67dc4c`.
Nothing was run except a read-only script that classifies the test packages'
uses of their boot regions (below).

### 10.1 Verdict

1. Yes, it makes sense, and it is the stronger form of the design: with a
   pool and an allocation as two kinds, `split` can never meet a region that
   has rows or slices, so R2's in-use status, Q1 and Q3 disappear, and N11
   and N2 cannot be written.
2. What decides it is the boot path, not the kernel: today every `Ram` row
   is minted as a pool root, and about a hundred packages pass such a row
   straight to `process_create`. The build must type each row (pool or
   allocation), which keeps those packages unchanged.
3. The minimal form (A: one kind, `slice` refuses a pool root) is cheap but
   fixes only the slice route; a root used as a destination still freezes
   its pool under R2.
4. The price of the two-kind form (B) is a new object kind, about 350 to
   450 lines over the design as written, one-line type edits in about thirty
   packages, eleven packages reworked, and the loss of bounded sub-allocation
   by front cuts (no package uses it).
5. Among A, B and C I would build B, with the build typing boot rows and
   an allocation's rights handed down through the pool handle it was split
   from. Form D (hierarchical pools, §10.8) extends B; §10.9 weighs all
   five forms and is the final recommendation.

### 10.2 What a program can tell today

Nothing distinguishes a pool root from a piece to a holder:

- Both carry the same rights. A boot root is minted with `memory_rights()`
  (`kernel/core/process.saw`, `mint_boot_regions`: `Transfer | Mint | Split
  | Map | MapExecute`, `kernel/abi/src/rights.saw`); a piece with the
  source handle's rights capped by the caller's ceiling (`dispatch.saw`,
  `memory_op_decoded`, Split arm).
- `INFO_OP` answers only the entry's rights word or its `HandleKind`, and
  "reads the entry and never the object" (`dispatch.saw`, `handle_info`);
  both are `HandleKind.Memory`.
- A boot record says `BootHandleKind.Memory` for every `Ram` row
  (`mint_boot_regions`, `queue_boot_handle`).
- Only the slot knows (`root == slot`, read by `pool_can_serve` and
  `pool_cut` in `objects.saw`), and only config tells root which ordinal is
  which (spec §12; `tools/sos_runner.py`, `_region_rows`).

### 10.3 What uses a boot region directly today

The boot path: `mint_boot_regions` mints every `Ram` row as a pool root
(`root: slot`) charged to root. The runner's `_region_rows` emits, in order,
one image-blob row and one destination row per child, then the pool row if
the case asks for one, then the device row; all three RAM uses are
`REGION_KIND_RAM`. The C3 stub (`c3_regions_stub`) and the S3 flash stitcher
emit rows too. The root program (`root/src/main.saw`) uses no `Memory`.

Counts, from a regex over `tests/*/src/*.saw` that follows each name bound by
`take_memory()` to `split`, `process_create`, `map`, `slab_donate` and
`give`. A region passed to a helper under another name is missed, so each
count is a floor:

- 125 packages take a boot `Memory`.
- **P1, a fixed-purpose row used whole:** about 100 pass a blob row
  directly as `image:` and about 101 a destination row directly as
  `memory:`; 2 map one directly (`inplace-launch` maps its blob,
  `map-over-image` its destination).
- **P2, the pool row split into pieces:** 28 packages (every `"pool": True`
  case's package).
- **P3, a fixed-purpose row split:** 7 (`flash-region`, `image-held`,
  `image-split` cut an in-place image off a blob; `late-door-gone`,
  `process-donate`, `split-live-bytes`, `split-rows-held` cut a
  destination).
- **P4, a piece split again:** 5 (`split-keeps-rights`,
  `split-keeps-rights-twice`, `donate-empty`, `image-split`'s image,
  `late-door-gone`'s destination piece).
- **P5, a pool given to a child that splits it:** 2 launchers
  (`charge-disown`, `charge-reap`) and their 2 children (`child-disown`,
  `child-keeper`, which split what they were given).
- **P6, a pool donated whole:** 1 (`charge-reap`).

### 10.4 A: one kind, `slice` refuses a pool root

- **A1.** `MemoryOp.Slice` on a slot with `root == slot` faults `BadArg`.
- **Knowable by config,** which is the doctrine §2.5 already applies to
  bounds ("what a process knows about where its memory is, it knows from
  the config that gave it the region") and §12 to ordinals: carve and split
  fault on bounds a holder can only know that way. A child handed a pool
  was told so by its launcher's config. Nothing smaller makes it readable:
  rights cannot carry it (a pool's sibling narrowed to drop `Split` would
  look like a piece and still be a pool), and an `INFO_OP` selector that
  read the slot would break `handle_info`'s "never the object".
- **Cost:** about ten lines and a spec sentence; no case changes beyond
  the design as written.
- **What it leaves:** N11 by rows. Under R2 a root with rows is in use and
  refuses `split`, and P1 is exactly a root with rows: a boot destination
  passed to `process_create` freezes that row for the child's life. Today
  those rows are never split afterwards, so nothing breaks, but the pool
  and the buffer are still one concept, and Q1 stays open.

### 10.5 B: a pool and an allocation, with disjoint operations

**Rules.**

- **B1. Two kinds, one slab.** A **pool** is a new kind (`ObjType.Pool`,
  `HandleKind.Pool`, `BootHandleKind.Pool`; sysapi `Pool`). An
  **allocation** is today's `Memory`. Both live in `MEMORIES`: a slot is a
  pool exactly when `root == slot`, and the handle's kind says the same
  thing, so no op has to ask the slot.
- **B2. A pool's ops:** `split(len)` (served first fit from its free list,
  else from its frontier, as `pool_cut`'s root branch does today), plus the
  universal `MINT_OP`, `give` and release. No `map`, no `slice`, and it is
  not accepted by `process_create` (`memory_arg` faults `BadHandle` for a
  non-`Memory` handle, as it does today for any other kind).
- **B3. An allocation's ops:** `map`, `slice`, `MINT_OP`, `give`, a staged
  message, `process_create` as image or destination, `slab_donate`. No
  `split`: the op is not in `MemoryOp`, so it is `BadOp`, and the sysapi
  `Memory` has no such method, so it does not compile.
- **B4. Rights.** `PoolRight` is `Transfer`, `Mint`, `Split`, and `Map`,
  `MapExecute`, `Slice` at `MemoryRight`'s bit numbers. The last three are
  not spendable on a pool; they say what its allocations may carry. An
  allocation is minted with `(pool handle's rights) & (Transfer | Mint |
  Map | MapExecute | Slice)`, capped by the splitter's `Memory` ceiling. So
  a launcher's keep mask on a pool sibling still decides what pieces can do
  (spec §2.5: "a mask that keeps `Split` while dropping `MapExecute` yields
  pieces that cannot map executable either"). Q7 asks about the
  alternative.
- **B5. Rows and slices are an allocation's only.** A pool can have neither,
  by B2. R5 to R10 apply to allocations as written, with "backing region"
  meaning an allocation; a slice's parent is always an allocation.
- **B6. An allocation's release:** at `refs == 0`, `maps == 0` and
  `slices == 0` its range goes to its pool (`pool_return`), or, for an
  allocation minted at boot (`root: NO_MEMORY`), nowhere: the slot frees
  and the bytes are gone. That is today's fate of a released boot root
  with nothing on loan (`memory_release_if_idle`'s root arm drops its nodes
  and returns nothing).
- **B7. A pool's lifetime is unchanged:** it is held while `refs > 0` or
  `out_bytes > 0`; its free list, coalescing and frontier fold are as
  today. Its `maps` and `slices` are always zero.
- **B8. Boot.** The region table's kind byte gains `Pool` beside `Ram`
  (now meaning an allocation) and `Device`; `mint_boot_regions` mints one or
  the other. The runner types blob and destination rows as allocations and
  the pool row as a pool, and a case may type a blob or destination row as
  a pool. The table is sawos's own format (the comment above `fits_word` in
  `process.saw`), so this is a version bump in the kernel, `_region_rows`,
  the S3 stitcher and the C3 stub, and no sawlang change.
- **B9. Donation** takes a pool (`refs == 1`, `out_bytes == 0`) or an
  allocation (`refs == 1`, `maps == 0`, `slices == 0`, not a slice), with
  the region rule's `BadArg` first, as today a root and a piece are both
  donatable.
- **B10. Quota:** an allocation charges the splitter's `QuotaKind.Memory`,
  as a piece does today; a slice likewise; pools are minted only at boot,
  charged to root. No new quota column.
- **B11. Ceilings:** `HandleKind.Pool` needs its own ceiling column
  (`HANDLE_KINDS` grows). The kernel never mints a pool after boot (root is
  uncapped; `MINT_OP` siblings and gives are not capped, spec §2.7), so the
  column caps nothing reachable today. It is there because the table is
  indexed by kind.
- **B12. Unchanged:** flash (a row of either kind; its allocations are
  images only: `map` is `BadArg`, a destination `BadImage`, a donation
  `BadArg`, so no third concept is needed); `IoMemory` and its carve; the
  stats region (no `Memory` is minted).

**What simplifies or goes, against §3's rules.** R2 becomes vacuous and goes
with Q1: no region that can have rows can be split. R3 is enforced by
vocabulary instead of a dropped right, so a slice may keep every right its
source had. R4 loses `pool_cut`'s and `pool_can_serve`'s non-root branches
(the front cut of a piece). R5c and Q3 go: a pool has no `slice`. R12 and
R14 simplify to "an allocation" and "a pool". R1, R5 to R11, R13, R15 to
R19 stand.

**N1 to N18 under B.** N2 (split under a slice) and N11 (a pool frozen by a
slice) cannot be written. N13 changes: a leaked row pins its allocation but
blocks nothing, since nothing splits an allocation. N12, the cross-process
pin of a whole parent by a slice, is untouched. N3 to N10 and N14 to N18
are as before.

**What is lost.** An allocation can no longer be cut into pieces released
independently. Today a piece's sub-pieces return to the pool root, not to
the piece (`MemorySlot.root`'s doc), so the shape is a bounded bump
allocator handed to a child: give it a 64 KiB piece with `Split`, and it
cuts at most 64 KiB. No package does this (P5 hands children the pool
itself, unbounded). Under B a child given an allocation makes sub-buffers
by `slice`, bounded by the allocation, but they are released only
together. If bounded, independently released sub-allocation is ever
wanted, the op is a sub-pool (`Pool.split_pool(len)` returning a pool over
a range of its parent, with its own free list), which is new work, not part
of this unit. The late-door hole is a slice under B, as under the design as
written.

**New holes under B,** each with a sequence:

- **NB1. Rights through the kind boundary.** If an allocation were minted
  from a default set rather than from the pool handle, `L` could give a
  child a pool sibling minted without `MapExecute` and the child's
  allocations would still map executable. *Closed by* B4's intersection.
- **NB2. A boot row typed as the wrong kind.** The build types a
  destination row as a pool; root's `take_memory()` on that record answers
  nothing. A config error met at the drain, not a kernel hole; the runner
  should type rows by their role, which `_region_rows` already knows.
- **NB3. Two rows over the same bytes,** a pool and an allocation. The
  build's responsibility, as today (`_region_rows` already refuses a pool
  beside two children for that reason).
- **NB4. A pool in a message.** A pool is a new carried kind: the staged
  message's handle decode in `sos.pipe` and `give(pool:)` must name it, or a
  pool cannot travel. A forgotten arm is a compile error by spec §2's
  "a new kind fails to compile until somebody says what references it",
  not a hole.
- **NB5. Dead paths.** `slab_donate`'s empty-region answer (`NoResource`
  for `len == 0`) is reachable only through a zero-length boot row, since an
  allocation is never empty and an exhausted pool has `out_bytes > 0`; and
  the Pool ceiling column caps nothing. Either delete them or say so.

**Cases (counts from §10.3).** Type edits only (a pool is taken with a pool
accessor and passed as `&Pool`): the 28 P2 packages and the 4 P5 packages.
Unchanged: every P1 use (about 100 packages), since their rows become
allocations. Reworked, 11 packages:
- `flash-region`, `image-held`, `image-split` (and `image_split_flash`):
  the blob typed as a pool for the case; its allocation is the image. In
  `image-split` the cuts of the image are no longer expressible (the type
  has no `split`), so those legs go; the after-death and donation legs stay.
  In `flash-region`, passing the pool as a destination no longer compiles;
  an allocation over flash is passed instead and is `BadImage` as before.
- `late-door-gone`: one destination per trial as `ram.slice(0, REGION_LEN)`
  and the hole as a slice of that, or `ram` typed as a pool for the case.
  Transcript unchanged either way.
- `process-donate`: its two destination rows typed as pools for the case.
- `split-live-bytes`, `split-rows-held`, `split-mapped-bytes`: the hazard
  they hold is not expressible; they become the husk and donation
  measurements on a slice (as in §7), or are retired with the reason.
- `split-keeps-rights`, `split-keeps-rights-twice`: rewritten as a pool
  sibling's narrowed rights flowing to its allocations (B4) and a slice of
  a slice keeping its source's rights.
- `donate-empty`: not expressible without a zero-length row (NB5).

Plus the runner's region kinds and version, the S3 stitcher and the C3 stub.

### 10.6 C: between and beyond

- **C1. B, with an unused allocation still front-cut.** Keeps the bounded
  bump allocator and leaves `split-keeps-rights*` and `donate-empty`
  unchanged, but brings back R2's in-use status and Q1 for allocations, and
  two `split`s on two kinds. N11 stays impossible (pools have no rows or
  slices). It spends B's clarity to keep a shape nothing uses; not chosen.
- **C2. One kind, typed by the boot row and the slot.** No new `ObjType`:
  the region table's kind decides whether a slot is a pool, and the ops
  refuse by slot state (`split` on an allocation, `map`, `slice` and
  `process_create` on a pool, each a fault knowable by config as in A).
  B's semantics for about 60 lines, but the refusals are checks rather than
  vocabulary, and a holder cannot read which it has. The cheap fallback if
  a new kind is judged too heavy.

### 10.7 Recommendation among A, B and C

Build **B**, with the build typing boot rows (B8) and allocation rights
handed down through the pool handle (B4). §10.9 revisits this with form D.

Costs against main, in kernel, abi, sysapi and spec lines (estimates) and
in cases:
- **The design as written:** about +200 / −128 lines; 5 cases reworked,
  about 5 new.
- **A:** that plus about 10 lines and one trial; Q1 stays open, and N11 by
  rows remains.
- **B:** about +550 to +650 / −150 (the design as written, plus a new kind's
  plumbing at the IoMemory kind's scale (its name appears on 245 lines of
  `kernel/`), a sysapi `Pool` type, `give(pool:)`, message carrying,
  donation overload and boot kind; less R2's status and the piece branches
  of `pool_cut` and `pool_can_serve`); 32 packages edited for type, 11
  reworked, about 5 new, plus the runner and two stitchers.
- **C1:** B plus about 25 lines; 8 reworked.
- **C2:** the design as written plus about 60 lines; the 7 P3 and 5 P4
  packages reworked, no type edits.

What the choice settles in §9: B settles **Q1** (a split of a region in use
cannot be asked) and **Q3** (a pool cannot be sliced); A settles Q3 only
(refuse, by config). Q2 (the `Slice` bit), Q4 (the name), Q5 (the pin) and
Q6 (the late-door route) stand under every form.

**New questions for the owner under B:**

- **Q7. Where an allocation's rights come from:** the pool handle it was
  split through (B4; recommended, it keeps the keep-mask idiom of §2.5), or
  `memory_rights()` capped by the splitter's ceiling (then a launcher
  restricts a child's allocations with `RestrictRights` on the child, not
  with the mask on the pool it gives).
- **Q8. The names:** `Pool` for the new kind and `Memory` kept for an
  allocation (recommended: about a hundred packages hold allocations and
  keep their spelling), or `Memory` for the pool and a new name for the
  allocation.
- **Q9. Bounded sub-allocation:** accept its loss now (recommended; nothing
  uses it, and slices bound a child's buffers), or plan a sub-pool op.

### 10.8 D: hierarchical pools

The owner's refinement: "2 operations on a pool - allocate and split.
Allocate creates a memory region which can be slice and mapped. Split
creates a smaller pool which can be allocated and split. This allows a
memory manager process to split its pool into smaller pools for use by
either another memory manager or by an individual process e.g. a process
can own its memory for allocating provided at process creation."

D is B with the pool's `split` renamed `allocate`, plus sub-pools. Three
roles: a **pool** (`allocate`, `split`), an **allocation** (`map`, `slice`,
image or destination, given; never split or allocated from) and a **slice**
(a view holding its allocation, §3 to §5).

**Verdict, in five lines.**
1. D holds up: a sub-pool is an outstanding range of its parent exactly as
   an allocation is, so the parent's existing third count (`out_bytes`)
   already holds it, and no cycle can form.
2. It needs two things B does not: each sub-pool remembers the range it
   was lent, so it can return it whole even after its own free list
   dropped bytes; and the release walk becomes as deep as the nesting,
   which I bound with an iterative walk and a depth limit of 8.
3. It gives the kernel what it lacks today: a reusable byte budget a
   process can hold. Quotas count objects (spec §2.9; `quota_charge` with
   `QuotaKind.Memory` in the Split arm), never bytes.
4. Its weakness is reclamation: a sub-pool comes back only when every
   holder of anything cut from it lets go, and there is no revoke (and no
   `kill` on main). A manager serving untrusted clients needs revoke, which
   is what capability kernels with this shape found too.
5. Build B now, under D's names (`allocate`, `Pool`, `Memory`) and slot
   layout, and add `split` (sub-pools) when a memory manager exists to use
   it; nothing in B then needs rework.

**Rules.** D1 to D4 replace B2 and B4; B1, B3, B5 to B12 stand with "pool"
covering sub-pools.

- **D1. A pool's ops:** `allocate(len) -> Memory` (B2's `split`, renamed)
  and `split(len) -> Pool`, plus `MINT_OP`, `give` and release. Both take
  their range the same way: first fit off the pool's free list, else the
  front of its frontier (`pool_cut`'s root branch), and both add `len` to
  the pool's `out_bytes`.
- **D2. A sub-pool** is a pool slot with `parent` naming the pool it was
  split from, `depth = parent.depth + 1`, its frontier its whole range,
  an empty free list, and `lent_base`/`lent_len` recording the range it
  was given. A boot pool has `parent: NO_POOL`, `depth: 0`.
- **D3. Release, at each level.** An allocation: at `refs == 0`,
  `maps == 0`, `slices == 0`, its range returns to its pool (`pool_return`)
  and the pool is asked. A pool: at `refs == 0` and `out_bytes == 0` its
  free-list nodes go back to the node slab (`pool_drop_nodes`) and its slot
  frees; a sub-pool then returns `[lent_base, lent_base + lent_len)` to its
  parent, and the parent is asked; a boot pool returns nothing (today's
  root arm). The walk is a loop, not a recursion.
- **D4. Depth.** `split` on a pool at `depth == MAX_POOL_DEPTH` (8) answers
  `NoResource`, a status with nothing changed: a limit of the machine, not
  something the caller computes. Rights: `PoolRight` is `Transfer`, `Mint`,
  `Allocate`, `Split`, and the hand-down bits `Map`, `MapExecute`,
  `Slice`. A sub-pool is minted with its source handle's rights capped by
  the caller's `Pool` ceiling; an allocation as in B4, capped by the
  `Memory` ceiling. So the `Pool` ceiling column (B11) now caps something
  real, and a launcher can withhold `Split` from a child to stop it
  making managers.
- **D5. Quota:** a sub-pool charges the splitter's `QuotaKind.Memory`, as
  an allocation does; no new column.

**What a sub-pool is to its parent:** an outstanding range, counted in
`out_bytes` (D1), held out of the parent's free list and frontier until it
returns whole (D3). The parent cannot free while it is out (today's root
rule in `memory_release_if_idle`). Splitting or allocating from a pool
that has sub-pools and allocations outstanding is safe: it takes only the
free list and frontier, and no outstanding range lies there (a range is on
the free list only after its holder returned it).

**The cascade and its bound.** One release can empty a pool, which can
empty its parent, up to the boot pool. Flattening (as R5a does for slices)
does not apply: a sub-pool's bytes belong to its parent's manager and must
go back there, not to the boot pool. A fixed depth bounds the levels; the
work at each level is one insertion into that pool's free list
(`pool_insert_range` walks it) and, when a pool frees, one walk to drop its
nodes. Free-list nodes come from one slab (`FREE_RANGES`) and each belongs
to one pool, so the whole cascade touches at most every node once: the
worst case is `MAX_POOL_DEPTH` + 3 levels (a slice, its allocation, and
nine pools at depths 0 to 8) and at most `FREE_RANGES.capacity()` node
visits in total, the same order
as today's worst single `pool_insert_range` on a fragmented root, with no
preemption point (as today). The depth limit is there to keep the level
count a small constant to state, not because the loop needs it; an
iterative walk with no limit is bounded by the Memory slab's capacity
instead, which is too large a number for a teardown sentence.

**Where a pool's free list lives.** Today a root keeps `free_head` (a list
of `FreeRange` nodes in the donatable `FREE_RANGES` slab), `out_bytes` and
`dropped` on its own `MemorySlot` (`objects.saw`); nodes are taken in
`pool_insert_range` (`alloc_free_range`) and charged to nobody, and a
release that finds no node adds to `dropped` and loses the bytes. A
sub-pool uses the same three fields and needs three more (`parent`,
`lent_base`/`lent_len`, `depth`). The lent range is what makes `dropped`
harmless one level down: a sub-pool with nothing outstanding has all its
lent bytes free whether or not its list tracked them, so it returns its
lent range, not its list. Without that, a sub-pool that dropped bytes would
return less than it borrowed and its parent's `out_bytes` would never reach
zero. Since pool and allocation fields no longer overlap, D wants pools in
a slab of their own (`POOLS`, donatable as `SlabKind.Pools`) rather than
three more fields on every `MemorySlot`. A fragmented sub-pool costs shared
nodes: at most one per hole, and holes are at most its live allocations
plus one, which the holder's Memory quota bounds (root's is unlimited, as
today).

**Fragmentation and pinning.** A sub-pool returns only whole, so one live
allocation in it pins its whole range in the parent. That is N12 one level
up, and it composes: a stranger holding a 4 KiB slice pins its 64 KiB
allocation, which pins its 1 MiB sub-pool, which stays out of the
manager's pool. What a program author must know: a pool comes back when
every holder of anything cut from it has let go, so a manager hands out
the smallest durable thing (an allocation rather than a sub-pool, a
sub-pool rather than its own pool) to anyone it does not trust to let go.

**Byte budgets.** A pool is a byte budget by construction: nothing can be
allocated from it beyond its range, and returned bytes can be allocated
again. Today a piece with `Split` bounds a child's bytes too, but only as
a bump allocator, since its sub-pieces return to the root, not to it
(`MemorySlot.root`'s doc). What D does not bound: the kernel objects the
holder creates (slots, free-list nodes, threads, handles), which stay on
the object quotas and the slabs.

**Process creation with a pool.** `process_create` is unchanged from B: an
image allocation and a destination allocation (copied segments and stack).
A child gets a pool of its own by `give` like any handle; no new argument.
`sosrt`'s arena (`rt/common/src/lib.saw`, "The bump arena") is a fixed
range the link script reserves in `.bss` (`sos_arena_base`,
`sos_arena_size` in `rt/common_c/support.c`), and stays so; a child's pool
is simply available beside it. One consequence the child's author must
know: an allocation it maps into itself lies outside its image region, so
it is not a system-call buffer on any profile (the copy windows are the
image region, spec §2.2), and the runtime cannot hand such memory to the
kernel.

**Reclaiming a sub-pool given away.** There is no revoke. A manager gets a
sub-pool back when its holder releases it or dies. `kill` is not on main
(`kernel/abi/src/rights.saw`: "There is no `Kill` right"; spec §8, not yet
enforced); with it, killing the holder releases its handles, but anything
the holder gave onward (allocations, slices, a sub-sub-pool) to processes
the manager cannot kill keeps the sub-pool's `out_bytes` above zero, and
its range stays out. So D without revoke is usable for managers whose
clients are trusted or killable as a tree, and not for one serving
strangers. Revoke can wait for that consumer, but D must not foreclose it:
each allocation and sub-pool records its pool (`root` today, `parent` and
`pool` under D), which is the derivation tree a revoke would walk.
Revoking an allocation means removing rows from other processes' domains
under possibly parked threads, which SO-90's late doors already make safe.

**Boot.** As B8: the region table's `Pool` rows mint boot pools and its
`Ram` rows mint allocations; nothing else is pre-allocated. "Allocate
first" instead of typed rows does not work for the image blobs: an
allocation needs a length, and a launcher cannot read a blob's length (a
region is not a description, §2.5; the runner's blob length is a linker
expression). So the ~100 P1 uses stay unchanged only with typed rows, as in
B.

**Donation, flash, in-place images, `IoMemory`:** as B9 and B12. A donated
sub-pool's range never returns to its parent (the parent's `out_bytes`
keeps it), as a donated piece's never returns to its root today.

**New holes, each with a sequence.**

- **ND1. A sub-pool outliving its parent's handles.** Manager M holds pool
  P, splits S, gives S to C, releases P. P is held by `out_bytes` (today's
  root rule), and returns when S comes back. Closed by D3.
- **ND2. A dropped range one level down.** C fragments S until nodes run
  out; a release adds to S's `dropped`. Without the lent range S would
  return short and P would never free. Closed by D2/D3.
- **ND3. A sub-pool released while one of its allocations is mapped
  elsewhere.** C allocates A from S, maps A into D, releases S and A. S is
  held by `out_bytes`, A by D's row; at D's unmap A returns to S, S frees
  and returns to P. Nothing is freed early; `pool_return` finds S live.
- **ND4. Unbounded cascade.** Closed by D3's loop and D4's depth.
- **ND5. Counts.** `out_bytes` is a byte count (`UInt`) and cannot exceed
  the pool's range; `depth` ≤ 8; `slices` as R17. Nothing new overflows.
- **ND6. Cycles.** A pool is held only by its own handles and its
  `out_bytes`; a parent holds nothing of a child; `split` makes a new slot
  from the pool's own free range, so a pool can never be its own ancestor.
  The graph is a tree.
- **ND7. A receiver affecting the giver.** C, holding sub-pool S of M's P,
  can allocate and split only inside S, can release only what S lent out,
  and cannot name P. Its effects on M are pinning S's range (open, as N12)
  and donating S, which takes S's range from P forever (open; it is today's
  piece donation, and a manager that minds withholds `Transfer` or never
  gives `SystemRight.SlabDonate`).
- **ND8. Node exhaustion as denial.** C fragments its sub-pool to consume
  shared `FREE_RANGES` nodes, so other pools' releases land in `dropped`.
  Bounded per process by its Memory quota (holes ≤ live allocations + 1),
  as today with pieces; root unlimited. Open, unchanged in kind.

**Prior art, from memory, not checked against a source.** seL4's untyped
memory is retyped into smaller untyped regions or into kernel objects; it
keeps a watermark rather than a free list, and memory is reused only after
revoking every capability derived from it, which resets the region
(revocation walks the capability derivation tree, preemptibly). EROS and
Coyotos have hierarchical space banks, and destroying a bank reclaims
everything allocated from it. Both make revocation the way memory comes
back to a manager, which bears on D: reference counting reclaims from
cooperative holders, and a manager of strangers needs revoke.

**Cost of D.** B plus: `PoolOp.Split`, `PoolRight.Split` and `Allocate`,
three fields (or the `POOLS` slab), the cascade loop, the depth limit, the
sysapi `Pool.split`, the spec's sub-pool paragraph and release ladder:
about 150 lines more. Cases: B's, plus about four new (a sub-pool returns
whole; a dropped range one level down; the depth limit; a sub-pool
released under a mapped allocation).

### 10.9 Recommendation across the five forms

- **The design as written:** about +200 / −128 lines; fields `parent`,
  `slices`; one op (`slice`), one right; 5 cases reworked, about 5 new.
  Settles nothing in §9 by itself.
- **A:** that plus about 10 lines; settles Q3 only; N11 by rows remains.
- **B:** about +550 to +650 / −150; one new kind (`Pool`) with its slab or
  fields, ops `split`/`allocate` on it and `map`/`slice` on `Memory`; 32
  packages type-edited, 11 reworked, about 5 new, the runner and two
  stitchers. Settles Q1 and Q3.
- **C1:** B plus about 25 lines; 8 reworked; Q3 only.
- **C2:** the design as written plus about 60 lines; 12 reworked; Q3 by
  config, Q1 for allocations stays.
- **D:** B plus about 150 lines; three fields or a `POOLS` slab, one more
  op and right; B's cases plus about 4 new. Settles Q1, Q3 and Q9.

*(Superseded by §11: the owner ruled D as the model, its minimum built now.)*

**Recommendation: D as the model, built as B first.** D is the model the
owner describes and it is sound with D2's lent range and D3/D4's bounded
walk. But sub-pools have no consumer in the tree, and their usefulness to
a manager of strangers waits on revoke. So build B now under D's names and
layout, and add `Pool.split` when a manager is written. Decide now, so the
rest follows without rework:
- the names: `Pool.allocate` for an allocation, `Pool.split` for a
  sub-pool, `Memory` for an allocation (not B's `Pool.split` for an
  allocation, which D would have to rename);
- the rights layout: `PoolRight.Allocate` now and `PoolRight.Split`
  reserved, with the hand-down bits (D4);
- pools in their own slot shape with room for `parent`, the lent range
  and `depth`, and every allocation recording its pool, so sub-pools and a
  later revoke need no layout change;
- typed boot rows (B8).

**Questions this adds for the owner (D):**
- **Q10. Revoke:** needed before sub-pools are useful to a manager of
  untrusted clients. Recommend: not in this unit; keep the derivation
  recorded so it can be added.
- **Q11. The depth limit:** 8 levels, `NoResource` at the limit.
  Recommend 8 (any small constant; two or three levels are the expected
  shape).
- **Q12. Memory a child allocates from its own pool is not a system-call
  buffer** (it lies outside the image region). Accept and document
  (recommended), or widen the copy windows to a process's own mappings,
  which is the mapped-row-in-window route SO-90 made safe and would make
  arm64 and the identity profiles agree.

### 10.10 Q6 answered both ways, and what is left for `slice`

The reviewer's comment on sections 1 to 9 was not on the issue when I
fetched main (`cc89c82a`); I worked from the lead's summary of it. Their
measurement, which I did not repeat: with `install_row` refusing a mapping
that meets the target's region or read-only window, one case fails in the
whole gate and both smokes, `late_door_buffer_gone`.

**Q6 = yes (a launcher may map memory into a child's copy windows; R18).**
The late copy doors stay reachable on the identity profiles and keep their
case, which needs a `slice` (or main's row-carrying split) to build the
hole row once `split` refuses a region in use. Everything SO-90 built
stays: the re-checks in `wake_one_waiter`, `wake_call_reply` and
`resume_call_room` (`kernel/core/refs.saw`), their disposals, and
`doom_process` with `drain_doomed` (`kernel/core/sched.saw`). Under R6 as
revised, the case's exec-row trials (the hole mapped read-write and
read-execute into one process) become `NoResource` on the identity
profiles, so the any-covering-row rule loses its only case either way.

**Q6 = no (a `map` whose range meets the target's region window
`[region_base, region_top)` or read-only window `[ro_base, ro_top)` answers
`NoResource`, beside `meets_image_row`, which is still needed for in-place
code rows outside both windows).** Then:
- **True by construction:** every buffer lies in the windows
  (`copy_in_check`, `copy_out_check`), only image rows lie there, and no
  image row is ever removed (`remove_grant`'s one caller is
  `MappingOp.Unmap` of a mapped row; `clear_domain` runs at the teardown).
  So the answer of an early door cannot change before its late door, on any
  profile; arm64 is already so, since `free_user_va` places every mapping
  above `region_top` and the read-only window is empty there.
- **What goes:** the late doors' refusal paths become `fatal_kernel`
  assertions, true this time, and their disposals go (the Event keeping its
  bits, the reply consumed and its claim dropped, the parked call's pending
  state cleared and the next room-waiter served, and `wake_one_waiter`'s
  pass-over of a doomed process's thread). On main every `doom_process` call
  is a late door (`refs.saw`, the four sites in `wake_one_waiter`,
  `wake_call_reply` and `resume_call_room`), so the doom field,
  `DOOM_PENDING`, `drain_doomed`, `end_doomed` and `pay_doom` would have no
  raiser left. **This depends on `kill`**, a patch in review that I have not
  read: a kill of a process whose thread is not the running one has to defer
  the death, which is exactly a doom, so I expect `kill` to raise one. If it
  does, the drain stays as kill's mechanism and only the re-checks'
  disposals go. Decide that when `kill` has merged.
- **What is lost:** a process can never hand the kernel a buffer in a row
  `map` installed, on any profile. On arm64 that is already true, so no
  portable program relies on it. The cost is a shared buffer passed straight
  to a system call: a server replying from a shared ring copies into its own
  memory first. The kernel copies in either case (§2.1's bodies are copied),
  so this is a user-space copy, not a lost zero-copy path. A launcher also
  can no longer add buffer space inside a child's region after its creation;
  it sizes the image's arena instead. And an allocation from a process's own
  pool, reached through a row `map` installed (Q12), is never a buffer.
- **Cases:** `late_door_buffer_gone` is removed (with `child-late-door` and
  `child-late-door2`), and so would be `kill_late_door` from the kill patch
  if it builds the same row (unread). Nothing is left for them to hold: the
  invariant they tested a recovery from no longer arises, and a new case
  holds the refusal that guarantees it (`map_into_window_refused`, mutant:
  the window test off and the `map` is taken).

*(Superseded by §11.9, which weighs a third answer, widening, and
recommends it.)*

**Recommendation: no**, with the reviewer. It makes buffers immutable for a
process's life on every profile, removes recovery code for a state that then
cannot occur, and makes the identity profiles agree with arm64. What it
gives up has no consumer.

**How each form meets the Q6 answer, and what is left for `slice`.**

- **Under Q6 = yes,** every form that stops splitting a region in use (the
  design as written, A, B, C, D) needs `slice` now, because it is the only
  remaining way to put a row into a child's hole and keep the late doors
  tested.
- **Under Q6 = no,** `slice` has no kernel path to serve and no consumer in
  the tree. Its remaining uses are a sub-range of one region handed on as a
  narrower capability (a server's ring with a window per client), a child
  created in part of a region, and bounded sub-buffers inside a region given
  to a child. Form by form:
  - *The design as written and A:* without `slice` they reduce to §8's
    "refuse the split and add nothing", which under Q6 = no loses nothing
    tested.
  - *B and C:* pools and allocations; a sub-range needs an allocation of its
    own, and a child in part of a region becomes "allocate the child its own
    region".
  - *D:* sub-pools serve the bounded-buffers use better than slices (their
    parts are returned independently); only the sub-range-of-one-allocation
    use is left.

**Plainly: under Q6 = no the model is pools and allocations, and `slice` can
wait for a consumer.** (§11.9: the same holds under the widened answer, where
an allocation's own row reaches the late doors without a slice.) Nothing in
the minimum forecloses it: rows already count on the allocation whose
bytes they grant, so adding `slice` later is
R1 and R5 to R12 applied to allocations, with no change to any rule that
exists by then.

## 11. The minimum build

The owner's ruling: "I think for pool, a revocation would be useful too.
But both pool splitting and revocation can be added later. Let's do the
minimum pool implementation now." So form D is the model, and what is built
now is its minimum: pools and allocations (form B) under D's names and
layout. This section is the brief for a spec author and an implementer; it
supersedes the build recommendations of §1, §3 (R2 to R4), §7, §9 and §10.

### 11.1 In and out

**In:**
- **Two kinds.** `Pool` (new: `ObjType.Pool`, `HandleKind.Pool`,
  `BootHandleKind.Pool`, sysapi `Pool`) is unallocated memory. `Memory`
  (today's kind) is an allocation.
- **`Pool.allocate(len) -> Memory`**: today's split of a pool root, renamed.
  First fit off the free list, else the front of the frontier; `len` a
  non-zero multiple of `hal.PROT_GRAIN` and servable in one range, else
  `BadArg`; quota, slab and handle refusals as today.
- **No `split` on `Memory`.** An allocation is mapped, used as an image or a
  destination, given, sent, minted and donated, and never cut.
- **Typed boot rows** (11.4).
- **Release of an allocation to its pool**: at `refs == 0` and `maps == 0`
  its range returns to its pool; a boot allocation's bytes go nowhere, as a
  released boot root's do today. A pool frees at `refs == 0` and
  `out_bytes == 0`.
- **Donation** of a pool (`refs == 1`, `out_bytes == 0`) or of an
  allocation (`refs == 1`, `maps == 0`).
- **Quota:** `allocate` charges the caller's `QuotaKind.Memory`, as split
  does; no new column.
- **Rights** (Q7): an allocation carries the pool handle's hand-down bits.

**Out, with what is reserved:**
- **`Pool.split`** (sub-pools): reserved `PoolOp.Split` and
  `PoolRight.Split`; the pool fields `parent`, `lent_base`, `lent_len` and
  `depth`; the release written as a walk up `parent` (11.7).
- **Revoke:** reserved `PoolRight.Revoke` (or `PoolOp.Revoke`); the
  derivation recorded (every allocation names its pool; every row names its
  allocation).
- **`slice`:** in or out by Q6 (11.2). The minimums are below; §11.9 adds a
  third, M-wide, and recommends it.

**The minimum two ways** (M-wide in §11.9).
- In the names below, "a row the loader recorded" is an image segment or the
  stack grant (sealed by `seal_image_rows`, never removed), and "a row `map`
  installed" is everything `install_row` adds. A process's stack is the
  former, so a buffer on it is legal under every answer to Q6.
- **M-no (Q6 = no, no `slice`):** pools and allocations; a `map` meeting the
  target's copy windows or any row it already holds is refused; the late
  doors become assertions.
- **M-yes (Q6 = yes, with `slice`):** the same plus `Memory.slice` (R1, R5
  to R12 applied to allocations) and the late doors kept, their case
  rebuilt on a slice.
- **Difference:** M-yes adds one op (`MemoryOp.Slice`), one right
  (`MemoryRight.Slice` and its hand-down bit on `PoolRight`), two fields
  (`parent`, `slices`), a release arm, two donation refusals and the sysapi
  method: about 180 lines, and six new cases (slice bounds, holds its
  parent, not donated, owner dies, quota, keeps rights). M-no instead adds
  the window refusal (about 15 lines and one case) and removes the late
  doors' disposals (about 100 lines), and up to about 150 more if `kill`
  does not use the doom drain (unread).

### 11.2 Decisions the owner still has to make

1. **Q6. May a row `map` installed be a system-call buffer?** Three
   answers, weighed in §11.9: restrict (the reviewer's view), leave as it
   is, or widen. Recommend **widen** (§11.9), placed before the removal of
   the split of an allocation. *(Revised: this item first recommended
   restrict.)*
2. **Is `slice` built now?** Under restrict or widen, recommend **not
   now**: no kernel path needs it and nothing in the tree uses it, so the
   model is pools and allocations. Only "leave as it is" with the late doors
   kept tested would need it (§11.9).
3. **R6. What two overlapping rows of one process mean.** Recommend
   **refuse**: a `map` whose range meets any row the target holds is
   `NoResource`, so a process's rows never overlap on any profile; the
   double map survives across processes and at two addresses on arm64. The
   alternative is that the first-mapped row decides on a filter profile and
   `executable_at` asks only that row. The reviewer offered both and asked
   for a decision.
4. **Q7. Where an allocation's rights come from.** Recommend **the pool
   handle it was allocated through** (`PoolRight` carries `Map` and
   `MapExecute` as hand-down bits at `MemoryRight`'s numbers), so a keep
   mask on a pool given to a child still bounds the child's allocations,
   capped by the caller's `Memory` ceiling. The alternative is
   `memory_rights()` capped by the ceiling, with `RestrictRights` on the
   child as the only lever.
5. **Q8. Names.** Recommend **`Pool` and `Memory`**, with the op
   `allocate`. About a hundred packages hold allocations and keep their
   spelling.

Q1 does not arise in the minimum: `allocate` never meets a region with rows,
and nothing splits an allocation. If a status word for "in use" is ever
needed, the reviewer's view (a new word, not `WouldBlock`) stands. Q2, Q4
and Q5 arise only with `slice`. Q3 is settled: a pool has no `slice`.

### 11.3 What the minimum does to main

**Goes:**
- `kernel/core/process.saw`: `row_pa_end`, `rows_clear_of_cut`,
  `rows_move_to_piece`.
- `kernel/core/objects.saw`: `pool_cut_base`; `pool_cut`'s non-root branch
  (the front cut of a piece); `pool_can_serve`'s non-root branch.
- `kernel/core/dispatch.saw`: `MemoryOp.Split`'s arm, whose root path moves
  to `PoolOp.Allocate`; its imports of the three helpers.
- `kernel/abi`: `MemoryOp.Split`, `MemoryRight.Split`.
- `kernel/sysapi/src/memory.saw`: `Memory.split`; `floor.saw`:
  `sos_memory_split`.
- Spec §2.5: the row-carrying split bullet and the "one cut from the front"
  bullet; §5.9's sentence on cuts from a derived region.
- M-no only: the late doors' disposals in `wake_one_waiter`,
  `wake_call_reply` and `resume_call_room`; their re-checks become
  `fatal_kernel` assertions. The doom path (`doom_process`, `DOOM_PENDING`,
  `drain_doomed`, `end_doomed`, `pay_doom`, the `doom` field) goes only if
  `kill` does not use it.

**Stays as it is:**
- `meets_image_row` (still needed for in-place code rows, which lie outside
  both windows).
- The any-covering-row rule in `translate_user` and `meets_execute`: kept as
  a defence. Under R6's refusal no state reaches it, and no case can hold
  it. Under M-wide it is needed again, compared by physical range (§11.9,
  W2), and arm64's alias case holds it.
- `install_row`'s other refusals; `record_grant`, `remove_grant`,
  `clear_domain`; the free list, coalescing and frontier fold;
  `memory_release_if_idle`'s root third count.
- M-yes: the late doors and the doom path unchanged.

**Added:**
- `kernel/abi`: `ObjType.Pool`, `HandleKind.Pool` (`HANDLE_KINDS` 16),
  `BootHandleKind.Pool`, `PoolOp` (`Allocate`, with `Split` and `Revoke`
  numbers reserved), `PoolRight` (`Transfer`, `Mint`, `Allocate`, the
  hand-down bits at `MemoryRight.Map`'s and `.MapExecute`'s numbers, and
  `Split`/`Revoke` reserved), `pool_rights()`.
- `kernel/core/objects.saw`: pools stay in `MEMORIES` (one slab; a slot is a
  pool exactly when its handle kind is `Pool` and `root == slot`), with the
  fields `parent` (`NO_MEMORY`), `lent_base`, `lent_len`, `depth` (zero) on
  `MemorySlot`, and the release rewritten as a loop up `parent` that runs
  once. A separate `POOLS` slab is an internal change that can follow.
- `kernel/core/dispatch.saw`: `pool_op` / `pool_op_decoded` (`Allocate`);
  `slab_donate` accepting a `Pool` or a `Memory`; `install_row` refusing a
  range meeting any row of the target (R6), and under M-no meeting its
  windows.
- `kernel/core/refs.saw`: `Pool` arms in `ref_object`, `drop_reference`,
  `free_object` (to `memory_release_if_idle`) and the teardown's release;
  `disown_region` covers pools already (same slots).
- `kernel/core/process.saw`: `mint_boot_regions` mints a `Pool` row as a
  pool (`root: slot`) and a `Ram` row as an allocation (`root: NO_MEMORY`,
  no pool), and queues `BootHandleKind.Pool` or `.Memory`;
  `RegionKind.Pool`; the region table's version is bumped.
- `kernel/core/pslot.saw` and the ceilings: the `Pool` column (inert in the
  minimum: the kernel mints a pool only at boot, to root).
- `kernel/sysapi`: `pool.saw` (`Pool`, `allocate`, `mint`, `rights`, drop
  as release), `BootHandle.take_pool`, `Process.give(pool:)`,
  `System.slab_donate(kind:pool:)`, `PipeHandle.Pool` in `sos.pipe`'s
  decode, the floor exports.
- M-yes: §3's R1 and R5 to R12 on `Memory`, with R1's slice field named
  `view_of` rather than `parent`, since a pool's `parent` is the pool it
  was split from.

Estimated size: M-no about +400 / −250 lines (more removed if the doom path
goes); M-yes about +580 / −150.

### 11.4 Boot

- The region table's kind byte gains `Pool` beside `Ram` (now an
  allocation) and `Device`. Kernel (`RegionKind`, `REGION_TABLE_VERSION`),
  runner (`REGION_KIND_*`, `REGION_TABLE_VERSION`, `_region_rows`), the
  ESP32-S3 flash stitcher and the C3 stub change together. It is sawos's own
  format, so there is no sawlang change.
- The roles `_region_rows` writes: a child's image blob is an
  **allocation**; a child's destination is an **allocation**; the pool row
  is a **pool**. A case may type a blob or a destination as a pool (a new
  case key) where it allocates from it, as `image-held`, `image-split` and
  `process-donate` do.
- An allocation must not have to be cut whole first: a launcher cannot read
  a blob's length (§2.5), and the runner writes it as a linker expression.
  That is why the rows are typed rather than allocated at run time.
- **The ESP32-S3's flash blob rows** are allocations (or pools where a case
  allocates images from them). Their allocations are images only: `map` is
  `BadArg`, a destination is `BadImage`, a donation is `BadArg`, all by
  today's `hal.map_source_ok` tests. **In-place images** are allocations
  whose bytes the in-place rows count on, as today with the image's
  `Memory`.
- `IoMemory` and the stats region are unchanged.

### 11.5 Every existing case

Counts from §10.3. **Read** means I read the package; **pattern** means the
regex count.

**Unchanged (pattern):** every package that passes a blob row as the image
and a destination row as the memory and splits neither, about 100. Their
rows become allocations and keep `take_memory`. Also every `IoMemory` case.

**Type edits** (`take_pool`, `&Pool`, `split` renamed `allocate`):
- the 28 packages that split the pool row (pattern), `memory_split` and
  `memory_recycle` among them (read);
- the 2 launchers that give a pool sibling to a child (`charge-disown`,
  `charge-reap`, read), with `give(pool:)`, and their 2 children
  (`child-disown`, `child-keeper`, read);
- `charge-reap`'s donation of the pool (read), with
  `slab_donate(kind:pool:)`.

**Rewritten, under both answers to Q6:**
- `flash_region` and its package (S3): the blob typed as a pool; its
  allocation is passed as the destination (`BadImage`) and donated
  (`BadArg`). Passing a pool as a destination no longer compiles.
- `image_held`, `image_split` and `image_split_flash`: the blob typed as a
  pool. `image_held` is otherwise unchanged (it allocates the image from the
  blob). `image_split`'s cuts of the image are no longer expressible and go;
  its after-death and donation legs stay (read).
- `process_donate`: its destination rows typed as pools (read).
- `split_live_bytes`: becomes "a destination allocation released while its
  child runs is held until the child's death" (read).
- `split_rows_held`: becomes the donation of a destination allocation with
  rows (`BadState`, already true on main) (read).
- `split_mapped_bytes`: becomes the release of a mapped allocation's handle
  (the husk) with the fresh-allocation measurement (read).
- `split_keeps_rights`, `split_keeps_rights_twice`: become a pool sibling
  minted without `MapExecute` whose allocations cannot map executable,
  narrowed once and twice (pattern; the shape read in §7).
- `donate_empty`: retired. No empty region can be made (an allocation is
  never empty; an exhausted pool has `out_bytes > 0`), so `slab_donate`'s
  empty-region answer is reachable only through a zero-length boot row; the
  runner may add one to keep it (read).
- R6: `map_basics`, `map_wx_refused`, `map_exec_gated` (read) map one region
  twice into one process; on riscv32 and riscv32-flat the second `map` is
  then `NoResource`. Each maps its second view into another process.

**Under Q6 = no (M-no):**
- **Removed:** `late_door_buffer_gone` with `child-late-door` and
  `child-late-door2`; `kill_late_door` (in review) likewise if it builds the
  same row (unread). What holds the path: nothing remains to recover from,
  and the refusal that guarantees it is held by `map_into_window_refused`.
- **New:** `map_into_window_refused`, `map_overlap_refused`, `pool_basics`
  (allocate, the return, the boot kinds, a pool given and allocated from),
  `donate_kinds` (a pool, an allocation, each refused while in use).

**Under Q6 = yes (M-yes):**
- **Rewritten:** `late_door_buffer_gone` builds its hole as
  `dst.slice(offset: HOLE_AT, len: HOLE_LEN)`. Its exec-row trials become
  `NoResource` under R6 and are dropped.
- **New:** M-no's list without `map_into_window_refused`, plus the six slice
  cases.

### 11.6 Order of work

*(Under M-wide, the recommended minimum, the order is §11.9's: step 3 below
becomes the widening, and it lands before step 4.)*

One spec patch first (§2, §2.5, §2.8, §2.9, §5.9, §12, §11), reviewed by
the owner. Then implementation patches, each keeping the gate green:

1. **The kinds and the typed boot rows.** Add `Pool` and `Pool.allocate`
   (today's root split); type the pool row `Pool`; edit the 32 packages and
   charge-reap's donation. `Memory.split` still exists, so nothing else
   moves.
2. **R6, the overlap refusal**, with `map_basics`, `map_wx_refused` and
   `map_exec_gated` rewritten and `map_overlap_refused` added. Independent
   of 1; either order.
3. **Q6.** M-no: the window refusal, the late doors' re-checks as
   assertions, their disposals removed, `late_door_buffer_gone` removed,
   `map_into_window_refused` added; the doom path decided against `kill`
   (after it merges). M-yes: `Memory.slice` with its six cases, and
   `late_door_buffer_gone` rebuilt on it.
4. **Removing the split of an allocation:** `Memory.split`,
   `MemoryOp.Split`, the row-carrying helpers and the piece branches go;
   the rewritten and retired cases of 11.5 land with it. Under M-yes it
   needs 3 first, since the late-door case then uses `slice`.

### 11.7 What must hold so sub-pools and revoke follow without rework

A reviewer can check the minimum against each of these:

1. **Every allocation names the pool it came from** (`root`, renamed
   `pool`), or `NO_MEMORY` for a boot allocation; nothing else is written
   there.
2. **A pool's `out_bytes` is exactly the sum of its outstanding
   allocations' lengths**, and a pool frees exactly when `refs == 0` and
   `out_bytes == 0`. A sub-pool will be one more kind of outstanding range.
3. **A pool slot has `parent`, `lent_base`, `lent_len` and `depth`**,
   `NO_MEMORY` and zero in the minimum, and the release is a loop that
   returns a freed pool's lent range to `parent` and asks it again. In the
   minimum the loop runs once, because every pool's `parent` is `NO_MEMORY`.
4. **Only a pool's own handles and its `out_bytes` hold it**; no other edge
   points at a pool. So a sub-pool adds one edge (its lent range in its
   parent's `out_bytes`), and the graph stays a tree.
5. **Every row names the allocation whose bytes it grants**
   (`GrantRow.region`), so a revoke can find a pool's rows through its
   allocations.
6. **An allocation's rights are the pool handle's hand-down bits**, so a
   sub-pool's handle can carry the same bits down another level.
7. **`PoolOp` and `PoolRight` keep `Split` and `Revoke` numbers unused.**
   The rights are minted when the ops exist; no handle survives a boot, so
   nothing minted now has to carry them.
8. **No op enumerates a pool's allocations.** Revoke will need a walk
   (bounded by the Memory slab) or a per-pool list, decided when it is
   designed; the minimum commits to neither.
9. **Under M-yes, a slice names an allocation, never a pool**, so the
   allocation, not the slice, is what a revoke must find.

### 11.8 Dependence on `kill`

`kill` is in review and expected to merge. The design depends on it in two
places: whether the doom path survives M-no (11.3), and §10.8's view that a
manager without revoke can reclaim a sub-pool by killing its holder (only
from holders it can kill, and not what they handed onward). Neither is in
the minimum's critical path.

### 11.9 Q6 has three answers

Terms as in 11.1: a row the loader recorded (an image segment or the stack
grant, sealed by `seal_image_rows`, never removed) and a row `map` installed
(everything `install_row` adds). A process's stack is a row the loader
recorded, so a buffer on it is legal under every answer.

Today a buffer must lie in a window first (`copy_in_check`: the region
`[region_base, region_top)` or the read-only window `[ro_base, ro_top)`;
`copy_out_check`: `[rw_base, rw_top)`) and then resolve through one
non-device row with no executable row covering any byte
(`translate_user`). A thread's stack top must lie in `(region_base,
region_top]` (`dispatch.saw`, `thread_create`), and nothing asks a row for
it. On arm64 a row `map` installed is placed at or above `region_top`
(`free_user_va`) and the read-only window is empty, so such a row is never a
buffer there.

**(a) Restrict** (the reviewer's view; M-no). A `map` meeting the target's
region or read-only window is `NoResource`, so every buffer lies in a row
the loader recorded. Consequences and cost are §10.10 and 11.1. For the
owner's pool model it means a process that allocates from its own pool can
reach that memory only by load and store: never as a system-call buffer, and
never as a thread's stack (`thread_create`'s region test).

**(b) Leave as it is** (M-leave). With no `slice`, no row `map` installed can
lie inside a copy window on any profile:
- the region window is the destination allocation's range (`process_create`
  sets `region_base`/`region_top` from `dest`, and `image_link_base` is the
  identity there); every other allocation is disjoint from it (a pool hands
  a range out once; boot rows are disjoint by the build), and the
  destination itself cannot be mapped into its child because it covers the
  stack row (`meets_image_row`);
- the read-only window spans only in-place read-only segments, which lie
  inside the image allocation (`check_in_place` holds them there); every
  other allocation is disjoint from it, and the image allocation cannot be
  mapped into its child because it covers those rows;
- root's region is the HAL's, which no region-table row may overlap (the
  build's responsibility; I did not check that the runner enforces it);
- on arm64, as above.

So the late doors are unreachable in M-leave, `late_door_buffer_gone` and
`kill_late_door` lose their route, and nothing holds those kernel paths:
the recovery code stays, untested, its mutants surviving. The invariant
holds by construction of the allocation model but is stated nowhere, so a
later `slice` or a region table with overlapping rows would reopen the
route silently. The only alternatives are a harness kernel that builds the
state directly, or `slice` now (M-yes). Cost: no kernel change; the case is
removed or parked.

**(c) Widen** (M-wide). Any memory a process's grant lets it reach, by a row
the loader recorded or a row `map` installed, is a legal buffer on every
profile, judged by the row:
- **W1. A source** is legal when its bytes lie in one non-device row that
  permits read, and no row of the process that permits execute grants any
  of those bytes. **A destination** is legal when its bytes lie in one
  non-device row that permits write, with the same execute test. The
  alignment rules are unchanged.
- **W2. The execute test compares physical ranges** (`row.pa` and the
  row's length), not user addresses. On the identity profiles the two are
  the same; on arm64 one region mapped read-write at one address and
  read-execute at another would otherwise let a buffer through at the first
  while its bytes are executable at the second, which is the user's SO-85
  ruling broken by an alias.
- **W3. The write test is new.** Today `copy_out_check` reads the writable
  window and `translate_user` never reads a row's write bit; the reviewer
  suspects a hole there and is probing it separately. Under W1 the door
  reads the bit.
- **W4. A thread's stack top** is legal when the word below it lies in a
  non-device row that permits write and not execute (either kind of row),
  replacing the region test. A stack in an allocation becomes legal;
  unmapping it under its thread makes the thread fault in user mode, which
  ends its process by §8's fault rule. The kernel keeps a thread's saved
  frame in its own slab (`frame_slot`); I did not read `hal.frame_init` to
  confirm that no HAL writes to the user stack.
- **W5. A device row is never a buffer** (unchanged: `translate_user`
  skips it).
- **The windows go:** `rw_base`, `rw_top`, `ro_base` and `ro_top` on the
  process body, `PlacedImage`'s fields and their accumulation in
  `place_image`, `window_holds`, the window parameters of
  `start_process`, and their assignment in `process_create`. They do not
  survive as a fast path: `translate_user` walks at most
  `hal.GRANT_ROW_BUDGET` rows and has to, for the execute test. `region_top`
  stays (`free_user_va` places mappings above it), as does `region_base`
  if anything else reads it.

Holes (c) opens, each with a sequence:
- **WH1. A shared row changed during a call.** A maps allocation X, which B
  also maps; A sends a body from X while B rewrites it. Each copy door reads
  its bytes once into kernel memory, so A sends some mix of the two, and no
  check is made on bytes read again later. I did not audit every caller for
  a second read; the doors' callers I read copy into a record on the
  kernel's frame. Not new in kind: today another thread of A can rewrite the
  buffer, and on the identity profiles a launcher can map A's destination
  into B.
- **WH2. A buffer spanning two rows.** On arm64 `free_user_va` can place
  two allocations adjacently, and a program treating them as one array
  passes a buffer across the seam: `BadBuffer`, since a row is the unit of
  one translation (`translate_user`). Kept and documented.
- **WH3. A different row at the same address at the wake.** A parks with a
  record in X's row; A's other thread unmaps X and maps Y, which
  `free_user_va` places at the same address (arm64 recycles addresses, as
  its doc says; on the identity profiles only X itself can come back
  there). The late door's re-check passes and the record is written into Y.
  Isolation holds: the kernel writes only where A's grant permits writing at
  that moment. It is a program bug, documented; a per-row generation would
  close it and is not recommended.
- **WH4. The stats row becomes a source.** It is a read-only row `map`
  installed over the kernel's counter page (`StatsRegionMap`); a process may
  now send from it. Harmless: the process can read it itself. As a
  destination it is refused by W3.

**Cost of (c):** kernel about +50 / −80 (`process.saw`: the two checks and
`translate_user`; `loader.saw`: `PlacedImage`; `dispatch.saw`:
`process_create`, `thread_create`; `start_process`); spec §2.2's copy-door
paragraph, §2.7's stack sentence and §5.6's copy-in window, about 40 lines.
Cases:
- `late_door_buffer_gone` is rebuilt on an allocation mapped into the child
  and runs on all three profiles, arm64 included. Its exec-row trials
  become arm64's alias trial for W2, since R6 refuses the overlapping rows
  on the identity profiles. `kill_late_door` likewise (unread).
- **New:** a source and a destination in an allocation (taken, every
  profile); a destination in a read-only row (`BadBuffer`); the arm64 alias
  (`BadBuffer`); a thread whose stack is an allocation (it runs).
- **Unchanged by pattern:** `umode_bad_wait_buffer` (address zero, in no
  row), the execute cases (`copy_from_code_refused`,
  `wx_segment_not_buffer`), and `process_walk_gate`'s cursor in read-only
  memory (still `BadBuffer`, now by W3 rather than the window). I found no
  case that expects a buffer in a row `map` installed to be refused, but
  found it by pattern only.

**What each answer leaves for SO-90's machinery:**
- (a): the re-checks become assertions, the disposals go, and the doom
  path goes unless `kill` uses it.
- (b): all of it stays, unreachable and untested.
- (c): all of it stays, reached by ordinary use and tested on every
  profile.

**The minimum each way** (estimates, against main):
- M-no (a): about +430 / −250, or −400 if the doom path goes;
  `late_door_buffer_gone` removed; 4 new cases.
- M-leave (b): about +415 / −150; the late-door case removed or parked;
  SO-90's code kept untested.
- M-wide (c): about +465 / −230; the late-door case rebuilt on all three
  profiles; about 8 new cases.
- M-yes (`slice`, with (b) and the route kept): about +580 / −150; six
  slice cases.

**Recommendation: (c) in the minimum,** as step 3 of 11.6, before the split
of an allocation is removed (step 4), so the late-door case is rebuilt on an
allocation before its split-based route disappears; there is no interval
without it and no code thrown away. It is what the pool model needs: a
process that allocates from its own pool can pass that memory to a system
call and run a thread on it, and every profile agrees. `slice` is not
needed under (c) either.

If (c) is deferred: (b) now and (c) next is sound in behaviour, since no
state reaches the late doors, so their code cannot misfire. But it leaves
that code untested for the interval, loses the case meanwhile, and rests on
an unstated invariant. (a) now and (c) next is worse: (a) removes the
recovery code that (c) then has to restore. So (b)-then-(c) is preferable to
(a)-then-(c), and (c) now to both.

Under (c), Q12 dissolves (an allocation's row is a buffer), and R6's refusal
matters more: with one covering row per address, `translate_user` and
`executable_at` agree with the PMP.
