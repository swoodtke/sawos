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

The two scans are `process_slots_usable()` bodies times up to
`MAX_GRANT_ROWS` rows each, skipped when `maps == 0`; neither takes a
preemption point.

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
(`BadArg`), the handle (`BadHandle`), a region with bytes that
`hal.map_source_ok` refuses (`BadArg`), `refs != 1` (`BadState`),
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
2. `len == 0`, `offset % hal.PROT_GRAIN != 0`, `len % hal.PROT_GRAIN != 0`,
   `offset > m.len`, or `len > m.len - offset`: `BadArg` (fault), as carve
   faults a window outside its parent. The bounds are `m`'s own range, so a
   slice of a slice is never wider than the slice it came through.
3. Quota (`QuotaExceeded`), slab (`NoResource`), handle (`mint_refusal`),
   each with nothing changed.
4. The new slot: `{process: caller, base: m.base + offset, len, refs: 1,
   maps: 0, slices: 0, parent: backing(m)}`; then `backing(m).slices += 1`.
   `m` is not otherwise touched.

Consequences stated as rules:
- **R5a. Flattening.** A slice of a slice names the backing region as its
  parent. No slice is ever the parent of anything, so there is no chain.
- **R5b. A region in use may be sliced.** That is the point of the op.
- **R5c. A pool root may be sliced** (open question Q3). Its range is its
  unspent frontier, so the offset is relative to wherever the frontier is
  at the call. While the slice lives the root is in use and serves no split
  (R2): the pool is frozen. A piece returning while the root has a slice
  still folds into the frontier, which only moves `base` down, so no
  existing slice leaves the root's range.

**R6. Overlap is allowed.** Slices may overlap each other and anything else
on their backing. Two overlapping slices mapped into one process with
different permissions are the double map §2.5 already sanctions ("an RW row
here and an RX row there over one region"), which a holder of the parent
can make today by mapping the parent twice. The per-row W^X rule
(`map_access`), the execute gate (`MemoryRight.MapExecute` per handle), the
image-row rule (`meets_image_row`) and the any-covering-row rule for buffers
(`translate_user`, `meets_execute`) all apply unchanged.

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
region rule's `BadArg`. A fault, on the reasoning §2.8 already gives for
sibling references: the donor is the region's author (it cut the slices).

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
  `process_slots_usable()` × `MAX_GRANT_ROWS` rows with no preemption point
  (with donated bodies, up to 256 × 9 on riscv32, whose `PROT_DOMAIN_SLOTS`
  is 256); the new split does none.
- **Reasoning:** the release condition is local to one slot again; nothing
  about a region depends on scanning other processes' records.

## 7. Existing cases that split a region in use

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
that process's unmap and release).

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

## 9. Open questions for the owner

**Q1. What a split of a region in use answers.** `WouldBlock` ("not right
now — try again": true, and its doc already says "the peer's to clear rather
than the machine's"), `NoResource` (main's answer, whose text "out of kernel
objects" is false here), or a new status such as `InUse`. **Recommend
`WouldBlock`**, with the doc noting that nothing signals when the region
becomes idle.

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

**Q4. The name.** `slice` (the owner's word) or `carve` (§2.5 already defines
carve as "the parent stays whole", and the arguments are carve's). **Recommend
`slice`**: the lifetime differs (a slice pins its parent, a carve holds
nothing), and that is the one thing a program author must not assume.

**Q5. Whether "a slice pins and freezes its whole parent" is acceptable**
(N12), or slices should be bounded somehow (a per-backing limit, or slices
refused on a region that is still being allocated from). **Recommend
accepting it and documenting it**; the remedy is to give a split piece.

**Q6. Whether a slice of a process's own destination may be mapped into it**
(R18). **Recommend yes**: it is possible on main, it is the launcher's own
memory, and it keeps the late doors testable from a program.

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
