# SO-105: per-process limits for larger images (design)

For the owner and the reviewer, before anything is built. Written against
main at `6ca48b20`. Nothing in the repository was changed. One probe
compile ran in a scratch directory, outside the tree (§3.3).

A bare section sign (§3) is this document's; the spec's sections are
written "spec §2.7".

Sources: the issue and its comment (the networking design's requirements);
the owner's rulings of Nov 14 (a design constrained by a policy limit
raises the limit; limits are per board, and an image adjusts within its
board); SO-95's design (`images-and-packaging`, §6.2 to §6.4 and §7) and
SO-103's (`networking`, §4.4, §4.6.1 and requirement 4.1); spec §2.7, §2.8,
§2.9, §3, §7, §8, §11 and §12; `kernel/core/{pslot,objects,limits,areas,
slab,dispatch,sched,process,loader}.saw`; `kernel/abi/src/{types,ops,boot,
rights}.saw`; `kernel/sysapi/src/{bootset,system,floor}.saw`;
`rt/main/src/lib.saw`; `root/src/main.saw`; `tools/sosimage/src/
{declaration,board,imagefile,plan,limits,kernel,tree,layout,product}.saw`;
each `hal/*/board.toml`; the runner's quota and slab-block cases; sawlang's
`libs/imgformat` at the pin.

---

## 0. Verdict

1. **Most of what the issue lists is built.** A pool per process as its
   RAM limit is built: a declaration's heaps, each a pool the stock root
   gives to that process alone, and `memory` records bound to an image's
   pools. The quota door and declared rows are built. A declaration's
   System rights are built. Two things are not: rights ceilings per kind
   from a declaration (the stock root never narrows a child's ceilings),
   and a sum of the children's declared rows against the image's slabs.
   And the handle table is still two `MAX_HANDLES` arrays in every body.
2. **The handle table: the body's table plus handle blocks from a new
   slab.** This is the lead's first reading, with three corrections the
   code forces. The body keeps `MAX_HANDLES` entries (32), not sixteen. A
   block extends the boot set as well as the table, because root's rows
   pass 32 in a networking image. Root, which has no launcher to raise its
   row, takes its blocks from the image's slab block at boot.
3. **Not a larger `MAX_HANDLES`, per board or per image.** On the ESP32-S3
   a 64-entry table costs 1 KiB in every body: 8 KiB for the box's eight
   processes and 8 or 9 KiB for a networking image's eight or nine,
   against the kernel range's 21.9 KiB of slack (SO-95's measurement),
   which the networking image already overruns. Blocks cost the networking
   image about 1.75 KiB and the box 224 bytes. Kernel SRAM on the S3 is
   the hardware wall the owner's ruling excepts. On riscv32 and arm64
   either would do, and one mechanism on every board is simpler than two.
4. **Below index 32 the lookup is today's.** Above it, one bound, one link
   and one slab access. Every walk of a table (the lowest-free mint, the
   delivery's pre-flight, the teardown) is at most 128 entries, four times
   today's.
5. **A child's blocks are attached by `QuotaSet`,** before it starts. A
   machine with too few answers `NoResource` and sets nothing. They stay
   until the teardown. A create never takes one.
6. **The builder derives both numbers:** the blocks an image's kernel
   holds, from its processes' `Handle` rows and root's need, and root's
   table, from the most the stock root holds at any step of its plan. An
   image author states `handles = 64` in a declaration and nothing else.
7. **Ceilings come from the declaration:** an optional `[ceilings]` table,
   complete when present, which the stock root applies before the start,
   and which every record of the kind is given within.
8. **The declared rows are summed** into the image's slab totals, as the
   builder already derives records and attachments.
9. **Placed elsewhere:** a stack length per process (its own issue: the
   kernel reads it from the image header, a sawlang change to `imgformat`);
   the priority map (the scheduler unit K3, with a recommendation);
   sub-pools (SO-146).
10. **Five units** (§7): two kernel units for Opus, three for Sonnet.

---

## 1. What exists

### 1.1 Rights

- **A declaration's System rights: built.** A `system` record's `rights`
  names the System rights the child keeps, by the four names of the launch
  convention (`read_rights`, `child_rights()`); a record with
  none keeps the image's `[system] rights` (`system_rights_or`,
  `console_rights()` in an image that names a console). The plan carries
  them by name (`bootplan.RightName`) and the stock root gives the child a
  System sibling masked to them (`root/src/main.saw`, `system_mask` and
  `give`).
- **Per-kind ceilings: the kernel's half is built, the image's is not.**
  The kernel keeps a ceiling per `HandleKind` in the body
  (`ProcessBody.ceilings`), intersects every mint with it (`mint_capped`),
  inherits it at create with an optional ceiling source, and narrows it
  through `ProcessOp.RestrictRights`. The stock root calls no
  `restrict_rights`, so every stock child's ceilings are root's, all ones
  (`start_process`).
- **Given records carry root's rights.** `give` takes a keep mask for every
  kind (`kernel/sysapi/src/system.saw`), and the stock root uses one for
  the System sibling (`system_mask`), for heap pools (`HEAP_POOL_KEEP`) and
  for inlets (`CLIENT_INLET`). A window, a line, an outlet and a `memory`
  record are given with every right root holds (`give_row`, `give`).

So SO-103's requirement 3, "rights narrowed to the declaration", is not
built: neither what the radio mints nor what it is given is narrowed to
its table of rights.

### 1.2 The RAM a process may allocate

Built, with no kernel change. A process allocates only from a pool it
holds, so its limit is the pools it is given.

- A declaration's `[heap.NAME] len` is a pool the builder makes a row for
  (`Bindings.heaps`), placed in the range the image's `heap-ram` names,
  and the stock root gives it to that process alone under `HEAP_TAG` plus
  the heap's slot, keeping nothing (`Source.Heap`).
- A `memory` record is bound to one of the image's `[[pool]]`s, and a pool
  bound twice is refused (`sosimage.imagefile`).
- The `Memory` row bounds how many allocations a process holds at once.

The radio's PSRAM pool is `[heap.default] len = 0xA000` in its declaration
and `heap-ram = { default = "psram" }` on its image entry. SO-103's
requirement 2 is met. A launcher that makes children at run time and wants
to hand each a budget needs `Pool.split`, which is SO-146's.

### 1.3 Counted limits

- **The kernel's door.** `ProcessOp.QuotaSet` and `QuotaGet`
  (`process_quota_set`, `process_quota_get` in `kcore.dispatch`), on a
  `Created` child only, a cap and not a budget, every row bounded. An
  image's quota defaults ride its slab block (`check_quota_defaults`,
  `set_image_quota_defaults` in `kcore.areas`) and a create installs them
  (`install_quota_limits`).
- **The formats.** A declaration's `[quota]` states nine rows; an image's
  process entry raises any of them and never lowers one; a board's
  `[limits.default]` and an image's `[limits]` state slab totals and quota
  defaults (`sosimage.board`, `LimitsSpec`).
- **The builder's checks.** The handle floor (`check_handles`); each row
  against the board's kernel (`hold_quota` in `sosimage.tree`) and against
  the image (`check_child` in `sosimage.plan`); the derivations of records,
  threads, attachments and the Mapping slab (`plan_limits`); and root's own
  objects of five kinds, step by step, against the image's slabs
  (`root_steps`, `check_plan`).
- **The stock root** sets every declared row before its first give
  (`set_quota`).

Not built: nothing sums the children's rows against the image's slabs. An
image whose processes declare six, four and three threads boots with
whatever its board's threads total is, and the thread create that finds
the slab full answers `NoResource` at run time, inside a running driver.

### 1.4 The handle table

- **Storage.** `ProcessBody.handles: [HandleEntry; MAX_HANDLES]` and
  `ProcessBody.boot_handles: [BootHandleSlot; MAX_HANDLES]`
  (`kcore.pslot`). `MAX_HANDLES` is `hal.MAX_HANDLES`, 32 on every board.
  An index costs 32 bytes on the 32-bit boards (an entry 16, a boot slot
  16) and 56 on arm64 (24 and 32). Of the S3's 1436-byte body (SO-95's
  figure), 1024 bytes are these two arrays.
- **The word.** `(generation << HANDLE_INDEX_BITS) | (index + 1)`, with an
  8-bit index field (`sosabi`), so a table holds at most 255 entries.
- **The code that touches it.** `lookup_handle`, `mint_handle`,
  `table_room` and `unbind_handle` (`kcore.objects`); the close-all and the
  bound-entry check of `teardown_process` (`kcore.sched`); the boot set's
  `boot_handle_room`, `boot_tag_used`, `boot_handle_take`,
  `queue_boot_handle`, `boot_handle_cursor` and `clear_boot_handles`
  (`kcore.objects`), and `boot_handle_next` (`kcore.dispatch`). Every other
  site resolves through `lookup_handle` and releases through
  `unbind_handle`.
- **Every bound that reads `MAX_HANDLES`.** A `Handle` limit past it
  answers `NoResource` (`process_quota_set`); an image's `Handle` default
  is below it (`check_quota_defaults`, `plan_limits`); root's three first
  handles and its rows fit it (`mint_boot_regions`' stops, `plan_limits`);
  the stock root's steps fit it (`root_steps`); a declared `handles` row
  and handle floor fit it (`hold_quota`, `check_child`); it holds the
  floors' rows beside five handles and is at most `BOOT_SET_CAPACITY`
  (`kcore.limits`); and the userspace `BootSet` holds `BOOT_SET_CAPACITY`,
  32, records in `_start`'s frame (`sos.bootset`, `rt/main`).
- **Root.** Root's rows are unlimited, so its table is what refuses it.

### 1.5 The stack and the priority map

- **The stack.** One length on every board, `hal.ROOT_STACK_LEN` (16 KiB):
  the kernel grants the top of a destination as the first thread's stack
  (`process_create`, `LoadRegion.stack_len`), and the builder writes the
  same length into each process's script (`SOS_STACK_LEN`,
  `process_script`).
- **The priority map.** The sosimg header carries a band map Blade writes
  (`SosimgHeader.prio_map`); the loader prints root's and nothing stores
  it. Spec §7 specifies the scheduler that would use it, not built.

---

## 2. What is left

1. A handle table past 32 for one process, the radio's 64 and a networking
   root's 48 or so, with no other body growing (§3).
2. The builder's side of it: the blocks an image holds, root's table, and
   the bounds that read `MAX_HANDLES` today (§4.1).
3. Ceilings and given rights narrowed to a declaration (§4.2).
4. The children's declared rows summed into the image's slab totals
   (§4.3).
5. Placing the stack and the priority map (§5).

---

## 3. The handle table

### 3.1 What is asked, and what it costs where

- **The need** (SO-103 §4.4 and §4.6.1). The radio holds 58 handles
  steadily and 60 at peak and declares 64 (48 for Wi-Fi alone). Root of a
  networking image holds about 48. Every other process holds 32 or fewer;
  the box's drivers declare 18 (GPIO), 15 (PWM) and 9 (console).
- **Root's boot set.** A networking image's region table has about 36 to
  40 rows: two per child (seven or eight children), the radio's ten
  windows and the box's devices, the ROM range and a pool per heap. Root
  queues one boot record per row, so root's boot set passes 32 as well as
  its table.
- **The S3's kernel SRAM** (SO-95 §6.3, SO-103 §4.6.1). The kernel range
  has 21.9 KiB of slack; the box's linked areas take 12.0 KiB of it; a
  networking image adds about 15.0 KiB (Wi-Fi) or 24.1 KiB (with BLE),
  1.5 KiB of it handle storage past 32, which leaves it 5.1 or 14.2 KiB
  over until SO-103's unit K6 shrinks the kernel stack (16 KiB is the
  hope).

### 3.2 The alternatives

**(a) A larger `MAX_HANDLES` per board.** The owner's general ruling: a
policy limit is raised, per board.

- The change is a HAL number per board and `BOOT_SET_CAPACITY` with it.
- On riscv32 and arm64 it costs nothing that matters: their kernels have
  1.5 MiB or more of slack.
- On the S3 every body grows by 32 x 32 bytes, 1 KiB. Put in place of
  SO-103's 1.5 KiB of handle storage, the Wi-Fi image's linked areas come
  to 27.0 - 1.5 + 8.0 = 33.5 KiB (eight bodies) and the BLE image's to
  36.1 - 1.5 + 9.0 = 43.6 KiB (nine), against 21.9 KiB of slack today and
  37.9 KiB if K6 frees all 16 KiB. The BLE image does not fit even then,
  and the box alone would pay 8 KiB for a need it does not have.
- Decoupling the boot set from the table would halve the cost and does not
  work: root's boot set must hold its rows, which pass 32.

Rejected. The S3's internal SRAM is a hardware fact, and a policy number
that every body pays for runs into it.

**(b) A `MAX_HANDLES` per image.** The same per-body cost as (a) in a
networking image, which is the image that needs it. It also needs the
kernel compiled with an image's number, where SO-95 §6.2 ruled that the
compiled numbers are the board's and an image raises a machine through
linked areas. Rejected.

**(c) A table sized per process at its create, from a slab of
variable-size runs.** The table leaves the body: a run of entries in a
kernel arena, its base and length in the body.

- Every lookup goes through the arena, for every index.
- The arena needs a run allocator (first fit, coalescing), and fragments:
  a raise can be refused with enough entries free.
- `process_create` takes no numbers (spec §12), so the run is the `Handle`
  default's at the create, and a `QuotaSet` that raises the row allocates
  a new run, copies the entries the launcher's gives already bound, and
  frees the old one. A create gains a new refusal.
- Root still needs its length from the image.
- What it buys: a small process pays for what it uses. A heartbeat holding
  eight handles would save 24 x 32 bytes, 768 bytes, of S3 SRAM.

Rejected on the owner's preference for the simpler design. The saving for
small processes is reachable later from (d), by lowering a board's
`MAX_HANDLES` (§5.4).

**(d) The body's table plus handle blocks.** Recommended (§3.3). Rejected
variants of it, each tested against the code:

- *Sixteen entries in the body* (the lead's first reading). Every board's
  `[limits.default]` gives a child a `Handle` row of 28, the GPIO driver
  declares 18, and `kcore.limits` asserts that root's table holds the
  floors' 24 rows beside five handles. With sixteen, root, the GPIO driver
  and every child at its board's default would take blocks, and a create
  would have to attach them. The body keeps `MAX_HANDLES`.
- *Blocks attached by the mint that finds the body's table full.* A
  running process within its row would then meet `NoResource` from the
  machine, where today a child meets its own budget and never the table's
  (spec §2.9, `DEFAULT_QUOTA_HANDLES`' comment), and the builder could not
  size the slab from the image.
- *An op that sets the table's length.* Two numbers that must agree, a new
  op and a new right; the `Handle` row already bounds the table and
  already answers `NoResource` past it.
- *Blocks chained through a link in each block.* Saves 20 bytes a body and
  costs a walk on every lookup above 32.
- *A block of 32 entries.* Three links a body instead of six, but root's
  48 would take a whole 32-entry block: the networking image pays about
  400 bytes more.

### 3.3 The recommendation

**Storage.**

- `HANDLE_BLOCK` = 16 entries, a power of two, and `MAX_TABLE_BLOCKS` = 6,
  both `kcore.limits`' and the same on every board. The longest table is
  `MAX_HANDLES + MAX_TABLE_BLOCKS * HANDLE_BLOCK`, 128 on every board
  today, asserted to fit the index field.
- A block is `HandleBlock { owner: Int, entries: [HandleEntry;
  HANDLE_BLOCK], boot: [BootHandleSlot; HANDLE_BLOCK] }`, `owner` the body
  plus one and 0 for free: 516 bytes on the 32-bit boards, 904 on arm64.
- The blocks are a slab, `HANDLE_BLOCKS: Slab<HandleBlock,
  MAX_HANDLE_BLOCKS>`, with a floor of zero, so a kernel whose image raises
  nothing pays only the slab's extent table. A zero-length extent 0
  compiled in a probe (a `Slab`-shaped generic with a `[T; 0]` inline array
  and the lending accessor, `sawc -Oz --freestanding` for riscv32 at the
  pin); it was not linked or run. If the kernel build refuses it, the floor
  is one block, 516 bytes on the S3.
- A new `SlabKind.HandleBlocks` (13) gives the blocks an image's linked
  area and a run-time donation through the existing doors: one arm each in
  `slab_geometry`, `slab_install` and `boot_capacity` (`kcore.areas`).
- `ProcessBody` gains `block_count: Int` and `blocks: [Int;
  MAX_TABLE_BLOCKS]`, each a block's slot plus one. A process's table is
  `MAX_HANDLES + HANDLE_BLOCK * block_count` entries, and its boot set as
  many records. 28 bytes a body on the 32-bit boards, 56 on arm64. A
  zeroed body, a donated one included, has no blocks.

**Addressing.** Index `i` names:

- `PROCESSES[b].handles[i]` when `i < MAX_HANDLES`, which is today's path;
- otherwise, below the table's length, entry `j` of block `k`, where
  `k = (i - MAX_HANDLES) / HANDLE_BLOCK` and `j` is the remainder:
  `HANDLE_BLOCKS[blocks[k] - 1].entries[j]`;
- otherwise nothing.

Two helpers in `kcore.objects` know the split, one reading an entry and
one writing it, and two more do the same for a boot record. The four table
functions, the teardown's two walks and the seven boot-set functions go
through them, and nothing else indexes `handles` or `boot_handles`.
`handle_word_index`'s answer for a word that names nothing becomes a
number past the longest table, where today it is `MAX_HANDLES`.

**The hot path.** A lookup of an index below `MAX_HANDLES` runs today's
instructions: the comparison that today answers `Unbound` now branches to
the block path. Above it: a load and compare of `block_count`, a load of
the link, and a slab access, which with a floor of zero takes `locate`'s
first iteration and a multiply. The lowest-free mint walks the body's
entries and then each block's sixteen, resolving a block's base once;
`table_room` and the teardown walk the whole table. No walk exceeds the
longest table, 128 entries with interrupts masked, four times today's;
`kcore.limits`' header names an order of magnitude as the point to
re-check, and this stays under it.

**A child's blocks.** In `process_quota_set`, for `QuotaKind.Handle`,
after the answers decided today and in place of the `MAX_HANDLES` bound:

1. A limit past the longest table answers `NoResource`.
2. The blocks the limit needs are `ceil((limit - MAX_HANDLES) /
   HANDLE_BLOCK)`, none for a limit at or below `MAX_HANDLES`. When that is
   more than the child holds, the free blocks are counted (a walk bounded
   by the slab's capacity) and too few answer `NoResource`, attaching
   nothing and setting nothing.
3. Otherwise the first free blocks in slot order are taken, each owned by
   the child's body and linked in order, and then the row is set.

A row set lower keeps the blocks: the table stays at least as long as the
row and the quota refuses first. Keeping them is also what keeps a slot's
generation rising through a process's life: a block detached and another
attached later at the same indexes could let a word bound in the first
resolve in the second at an equal generation. A create attaches nothing,
because an image's `Handle` default stays below `MAX_HANDLES`
(`check_quota_defaults`, unchanged).

Nobody's row is charged for a block. It is the child's for its life, and
what bounds blocks together is their slab, as with every kind a `QuotaSet`
raises: a launcher can give a child a `Handle` row above 32 only if its
own row is above 32, and only root's is by default.

**Root's blocks.** Root has no launcher and an unlimited row, so its blocks
come from its image.

- The slab block's header has a reserved word today (`SlabBlockHeader` in
  `kcore.areas`); it becomes root's block count. The region table goes to
  version 6, since a kernel at version 5 ignores the word.
- `install_image_slabs` refuses, after the quota defaults and before
  anything is installed, a count past `MAX_TABLE_BLOCKS` and a count past
  the `HandleBlocks` capacity at boot, each by `fatal_image`, and keeps the
  count.
- `start_process` attaches them after `claim_root` and before the first
  mint, so root's System, Process and Thread still land at words 1, 2 and
  3, and its rows fill the longer table and boot set. An attach that fails
  there is `fatal_kernel`, since the boot checked it.
- `mint_boot_regions`' two stops ("no boot-record room", "no handle table
  room for the boot regions") read root's table as it now is.
- An image with no slab block gives root `MAX_HANDLES`, as today.

**The teardown.** Step 2's close-all walks the whole table, the blocks'
entries included, and step 4's bound-entry check covers them. Then each
block's owner is cleared, its link and `block_count` with it, beside
`clear_boot_handles`. A freed block keeps its entries' generations, as a
reused body does.

**Exhaustion, every way.**

- A child at its row: `QuotaExceeded`, as today.
- A child within its row: never a full table, since the table is at least
  the row.
- Root at its table's end: `NoResource`, as today, at a table its image
  may have made longer.
- `QuotaSet` past the longest table, or short of blocks: `NoResource`,
  nothing set and nothing attached.
- The boot: root's blocks past either bound stop it, naming the bound.
- A launcher that raises rows at run time: a donation of `HandleBlocks`
  (`SystemOp.SlabDonate`) adds blocks for the next `QuotaSet`.

**The boot set's userspace side.** The kernel's boot set grows with the
table, so a networking root's 40 records are queued. `BOOT_SET_CAPACITY`
(`sosabi`), the records a `BootSet` holds, goes from 32 to 64: 768 bytes in
`_start`'s frame on the 32-bit boards, 384 more than today, in every
program written with `main`. `kcore.limits` keeps `MAX_HANDLES <=
BOOT_SET_CAPACITY`, so a process with no blocks always drains into a
`BootSet`. A process whose boot set the builder would make longer than
`BOOT_SET_CAPACITY` is refused (§4.1); a hand-written launcher that gives
more has its child drain with `BootHandleNext`, or meets `BootSet`'s
existing panic, which names the capacity.

**The record the builder reads.** `sos_image_limits` goes to version 2: the
`HandleBlocks` floor among the floors, `HANDLE_BLOCK`, `MAX_TABLE_BLOCKS`,
a block's bytes and `BOOT_SET_CAPACITY`. The bytes are a word of their own
because `sosimage.kernel` sizes every other kind's slot from its slab's
symbol divided by its floor, and a floor of zero leaves nothing to divide.

### 3.4 What it costs, per profile

| profile | a body grows by | a block |
|---|---|---|
| riscv32, riscv32-flat, ESP32-S3 | 28 B | 516 B |
| arm64 | 56 B | 904 B |

On the S3 the box pays 8 x 28 = 224 bytes, and a networking image with
eight bodies 224 + 3 x 516 = 1772 bytes: the radio's two blocks (64) and
root's one (48). SO-103 priced this at 1.5 KiB; the difference is the
links in every body. A board-wide 64 would be 8 KiB on the same image.

- **riscv32** (isolated, PMP, copied): blocks are kernel memory in the slab
  region, like every area. No protection row changes.
- **arm64** (translated): a block is reached through the slab's one access
  funnel, `extent_addr`, which converts with `hal.phys_to_virt` as it does
  for every donated or linked extent.
- **riscv32-flat**: identical to riscv32. No case below asserts a hardware
  denial, so the profile excludes none.
- **ESP32-S3**: the area lands in internal DRAM between `_sos_slabs_start`
  and `_sos_slabs_end`. The kernel's code grows by the helpers and the
  attach; U1 reports the S3 kernel's `.text` and `.bss` before and after.
- **ESP32-C3**: nothing builds for it; the same code compiles there when it
  does.

### 3.5 Kernel changes, by file

- `kernel/abi/src/ops.saw`: `SlabKind.HandleBlocks = 13`.
  `kernel/abi/src/boot.saw`: `BOOT_SET_CAPACITY` 64, and its comment.
- `kernel/core/limits.saw`: `HANDLE_BLOCK`, `MAX_TABLE_BLOCKS`,
  `LONGEST_TABLE`, `MAX_HANDLE_BLOCKS` (0); asserts that `HANDLE_BLOCK` is
  a power of two, that `LONGEST_TABLE` fits `HANDLE_INDEX_MASK`, and that
  `MAX_SLAB_CAPACITY >= LONGEST_TABLE * MAX_PROCESSES` (today's assert
  reads `MAX_HANDLES`); the body's cost in `MAX_PROCESSES`' comment.
- `kernel/core/pslot.saw`: `HandleBlock`, `HANDLE_BLOCKS`, and the body's
  two fields.
- `kernel/core/objects.saw`: the entry and boot-record helpers;
  `lookup_handle`, `mint_handle`, `table_room`, `unbind_handle` and the
  boot-set functions through them; `attach_handle_blocks` and
  `free_handle_blocks`; the `static_assert` on the index field reads
  `LONGEST_TABLE`.
- `kernel/core/dispatch.saw`: `process_quota_set`'s `Handle` arm;
  `boot_handle_next` through the helper.
- `kernel/core/sched.saw`: `teardown_process`' two walks and the free.
- `kernel/core/areas.saw`: the three `HandleBlocks` arms; the header's
  root-blocks word and its two stops; `IMAGE_LIMITS` version 2.
- `kernel/core/process.saw`: `start_process` attaches root's blocks;
  `region_table` accepts version 6.
- `kernel/sysapi`: `SlabKind`'s new case in `sos`.
- Each HAL's `ABI.md` line for `MAX_HANDLES` and its `lib.saw` comment:
  "the entries a body holds", not "the length of a process's table".

### 3.6 Spec changes

**§2.7, quota rows, "The bound".** Replace the `Handle` clause:

> For every kind but `Mapping`, a limit is at most the caller's own limit
> for that kind, and a `Handle` limit is at most the longest handle table,
> `MAX_HANDLES` + `MAX_TABLE_BLOCKS` × `HANDLE_BLOCK` (§3).

and add a bullet after it:

> - **A `Handle` limit above `MAX_HANDLES` lengthens the table.** The set
>   attaches to the child, before it sets the row, as many handle blocks as
>   the limit needs beyond the body's `MAX_HANDLES` entries, from the
>   `HandleBlocks` slab (§2.8), so a child's table is never shorter than
>   its row. The blocks stay until the child's teardown, whatever its row
>   is set to afterwards. Nobody's row is charged for them.

**§2.7, "Its answers".** The machine's answers become: "a `Mapping` limit
above the child's free grant rows, a `Handle` limit above the longest
table, and a `Handle` limit needing more handle blocks than the slab has
free each answer `NoResource`, and set and attach nothing."

**§2.7, the body and its teardown.** The body holds "the handle table's
first `MAX_HANDLES` entries and its links to the handle blocks it holds".
The teardown frees the blocks with the body, and a walk of a table is
bounded by the longest table.

**§2.8, the machine's limits.** Replace "The handle table's length is a
board's number too":

> - **The handle table's length.** `MAX_HANDLES` is set in the HAL: the
>   entries every body holds, at least five more than `MAX_MEMORIES` +
>   `MAX_IOMEMORIES`, so root's table holds a region table filling those
>   two floors beside its first handles (§12). A process's table is longer
>   by the handle blocks it holds (§3), from the `HandleBlocks` slab, whose
>   floor is zero: an image raises it with an area, and a donation adds to
>   it. `HANDLE_BLOCK` and `MAX_TABLE_BLOCKS` are the kernel's, the same on
>   every board. The build asserts that the longest table, `MAX_HANDLES` +
>   `MAX_TABLE_BLOCKS` × `HANDLE_BLOCK`, fits the handle word's index field.

**§3, the handle table.** Add a property:

> - **Where an entry lives.** A table is the `MAX_HANDLES` entries of its
>   process's body and, for a process whose `Handle` row was set above that
>   (§2.7) or for root when its image gives it blocks (§12), up to
>   `MAX_TABLE_BLOCKS` handle blocks of `HANDLE_BLOCK` entries, in the
>   order they were attached: index `MAX_HANDLES` is the first entry of the
>   first block. A table never shrinks while its process lives. A block
>   also holds the boot-set records of its positions, so a process's boot
>   set is as long as its table. A freed block keeps its entries'
>   generations, as a body does.

**§8, kill's cost.** "bounded by the slabs' capacities and the longest
table".

**§11, the plan.** The reserved list loses "a handle table sized per
process" and "rights ceilings narrowed to what a program declares" (U4);
"a pool per process as its memory limit" moves to what is built (§1.2);
"a stack length per process" stays, naming its issue.

**§12.** Four changes:

- the rows bound: "one whose rows root's handle table cannot hold beside
  its first handles", where root's table is `MAX_HANDLES` and the blocks
  its image gives it;
- the slab block: "The table is at version 6", and the header is "a magic
  word, the number of areas, one quota default for each `QuotaKind`, the
  number of handle blocks root's table takes, then one entry per area";
  two stops join the list: root's blocks more than `MAX_TABLE_BLOCKS`, and
  more than the `HandleBlocks` slab holds at boot; and "root's blocks are
  attached before its first handle is minted";
- the stock root's plan: "one at whose steps root would hold more handles
  than the longest table", since the builder sizes root's table to the
  most it holds (§4.1);
- the launch convention: ceilings and given rights (§4.2).

---

## 4. The image and declaration side

### 4.1 Handles

**Declarations and images.** Nothing new to write. The radio states
`[quota] handles = 64`; an image's process entry may raise it. The bounds
that read `MAX_HANDLES` read the longest table instead: a declared row or
handle floor past it is refused for the board (`hold_quota`), and an image
row past it for the image (`check_child`). An image's `Handle` default
stays below `MAX_HANDLES`.

**Two `[limits]` keys**, in a board's `[limits.default]`, an image's
`[limits]` and a test image's JSON alike, one spelling in all three:

- `handle-blocks`: the blocks the machine boots with, a slab kind's total
  like `records`;
- `root-handles`: root's table, rounded up to a whole block.

Both are derived for an image whose root is the stock root, and an image
may state either above its derivation and never below it (SO-95's rule
6.4). For a hand-written root the builder has no plan to derive from: the
image states them, and with none root's table is `MAX_HANDLES` and the
machine holds no blocks.

**What `plan_limits` derives and checks.**

1. Root's table: `MAX_HANDLES`; for the stock root the most `root_steps`
   finds it holding at any step, rounded up to a block; a stated
   `root-handles` rounded up. Refused past the longest table, naming the
   step ("the stock root would hold 131 handles at `give radio record 12`,
   past the longest handle table (128)"), and a stated one below the
   derivation, naming both.
2. The blocks: root's, plus each child's for its `Handle` row (declared,
   or the image's). Refused when stated below that, and past
   `MAX_SLAB_CAPACITY` as every kind is.
3. Root's boot handles, three and one per row, against root's table where
   today they meet `MAX_HANDLES`.
4. Each boot set against `BOOT_SET_CAPACITY`: root's rows, and a child's
   records with the console's inlet and its heaps' pools.
5. The slab block carries root's block count and, when the blocks are
   raised, a `HandleBlocks` area of that many blocks' bytes.

For the networking image this derives three blocks and a root table of 48
with nothing stated by its author. Every refusal names the image, the
number and the bound, as `plan_limits`' do today.

`sosimage.kernel` reads record version 2 and refuses version 1 by name.
`SOS_IMAGE_BUILDER=python` refuses a case that states either key, by name,
as it refuses the other kinds of case only the builder makes.

### 4.2 Ceilings from the declaration

**The format.** A declaration gains an optional table, and every record
may carry `rights`, as the `system` record does today:

```toml
[ceilings]                     # optional; when present, complete
process = ["thread-create", "event-create", "waiter-create", "map",
           "exit", "boot-handles", "thread-self"]
thread = ["start", "control"]
event = ["signal", "receive", "wait"]
waiter = ["attach", "wait"]
clock = ["read", "timer-create"]
timer = ["arm", "wait"]
memory = ["map"]
mapping = ["unmap", "base"]
pipe-request = ["reply", "wait"]

[[boot]]
name = "mac"
kind = "window"
type = "esp32s3-wifi-mac"
tag = 3
rights = ["map"]               # optional: within the kind's ceiling
```

- **Names.** A key is a `HandleKind` in kebab case, `system` excepted:
  System rights stay on the `system` record, where the launch convention
  already bounds them. A value is a right of that kind, its enum case in
  kebab case (`kernel/abi/src/rights.saw`). An unknown kind, an unknown
  right and a right named twice are refused by name.
- **Complete when present.** With `[ceilings]`, a kind it does not list
  gets no rights; without it, the child keeps root's ceilings, as today.
  This is the System record's rule (an empty list gives none), and it
  means no existing declaration changes meaning.
- **Records.** A record's `rights` is its give's keep mask. With
  `[ceilings]`, a record with no `rights` is given with its kind's ceiling,
  and one with `rights` outside it is refused: a declaration says once what
  the program may hold of a kind. The kinds: a window is `iomemory`, a
  line `interrupt`, a serve `pipe-outlet`, a client `pipe-inlet`, and a
  `memory` record `pool`, the kind of the pool row it is bound to.
- **What the runtime needs.** The builder refuses a `[ceilings]` or a
  record's `rights` that withholds a right the program's runtime uses,
  naming the right and the use: for a program written with `main`, its
  Process handle's `boot-handles` and `thread-self` and its main Thread's
  `control` (`rt/main`'s start-up); for a heap, the Process handle's
  `map` and the pool's `HEAP_POOL_KEEP`; for the console's inlet, `post`.
  U4 takes the list from `rt/main` and `rt/common` and gives each entry a
  test.
- **The image cannot widen** a declaration's ceilings; no image key
  names them. An image narrowing them further is question 5.

**The plan and the stock root.** `bootplan`'s child gains a ceiling word
per `HandleKind` and each record a keep word; `root_arena` adds them. The
stock root, after the quota rows and before the first give, calls
`restrict_rights` once for each kind (a kind not narrowed is all ones) and
gives each record with its keep word. A kind the kernel cannot restrict is
`RestrictRights`' `BadArg` fault, which the builder's kind list makes
unreachable.

**Spec §12, the launch convention.** It gains: "It narrows each of the
child's rights ceilings to its declaration's `[ceilings]`, when it has
one, before any give, and gives each record with the record's rights, or
its kind's ceiling." The four System rights stay the bound on a `system`
record's `rights`.

The radio's table of rights (SO-103 §4.4) becomes its `[ceilings]` and
its records' `rights` with no further design.

### 4.3 The declared rows, summed

SO-95's rule 4.3 reads a declaration's `[quota]` as what the program
needs, and its §4 says "the boards a driver runs on are those with ...
limits that hold what it declares". So the image's slab totals derive
from them, beside the derivations `plan_limits` makes today:

- for each of `threads`, `events`, `waiters`, `timers`, `mappings`,
  `pipes`, `memories` and `iomemories`: at least root's own objects of the
  kind (those `root_steps` and `check_plan` count, root's main thread and
  its one Waiter), plus each child's row where its declaration or its
  image entry states one;
- a kind a child states no row for counts nothing, except threads, which
  count its main thread (today's "threads at least bodies");
- the `Memory` sum replaces `check_plan`'s count of heaps, which a child's
  `Memory` row already covers (`check_child`), so nothing counts twice;
- the Mapping slab is the larger of the sum and today's four per body.

An image that states a total below the sum is refused, naming the kind,
the sum and the image's total. A hand-written root has no plan and nothing
is derived for it, as today. Question 6 asks whether to derive or to
refuse; this design derives, because that is what every other total does.

---

## 5. Placed here, or elsewhere

### 5.1 A stack length per process: its own issue

Today every process's first stack is `hal.ROOT_STACK_LEN`, 16 KiB. The
radio is content with that (SO-103 §4.6.2); small processes on the S3 are
not, since a heartbeat's region carries 16 KiB of stack in SRAM.

- **It is not a quota.** The stack is the kernel's grant at the top of the
  destination, made at create, and a launcher supplies no numbers
  (spec §12). The length belongs in the image, as the entry does.
- **The mechanism.** The sosimg header's `reserved0` word, which
  `imgformat` keeps for a later version, becomes the stack length; the
  kernel's loader reads it at both doors with today's checks (whole grains,
  below the destination's top), and zero means `ROOT_STACK_LEN`; a
  declaration states `stack`, and the builder writes it into both the
  header and the process's `SOS_STACK_LEN`.
- **Why not here.** `imgformat` lives in sawlang, so it needs an SL issue
  and a pin bump, and nothing in networking waits on it.

Recommendation: file it now, after this issue's units.

### 5.2 The priority map: the scheduler unit's

What spec §7's scheduler wants per process is its band-to-level map, and
for the radio more levels than four bands give (SO-103's requirement
4.1). The map and its semantics are K3's design. One point bears on this
one: spec §7 says the launcher passes the map to `process_create`, which
breaks "root supplies no numbers" and a create's three arguments. The
shape this issue uses for every per-process number fits it: the image
header's map is the default, a `Created`-only `ProcessOp` beside
`QuotaSet` narrows it, the declaration states it and the plan carries it.
Recommendation: K3 adopts that shape; nothing is reserved here.

### 5.3 Sub-pools and revoke: SO-146

A pool per process is built for images (§1.2). A run-time launcher that
hands out budgets needs `Pool.split`, reserved in spec §2.5 and filed as
SO-146.

### 5.4 Lowering the S3's body table: not now

With blocks, `MAX_HANDLES` is a per-board trade between every body's cost
and how many processes take blocks. At 16 on the S3 a body would save
512 bytes (both arrays), 4 KiB across eight; root, the GPIO driver and any
child at the board's default of 28 would then take blocks, the board's
default would move, and `kcore.limits`' assert that a body's table holds
the floors' 24 rows beside five handles would have to become one about
root's table with its blocks. The net saving on the box is under 1 KiB.
Recommendation: not now; revisit if K6 does not free the kernel stack.

### 5.5 Grant rows

The radio's 18 rows are the S3 board's grant-row budget, a per-board
number (SO-103 asks for 20). Not this issue's.

---

## 6. Tests

The owner's standard: every check and stop has a case that goes red when
it is removed or inverted. Each kernel case runs on riscv32, arm64 and
riscv32-flat; those marked (S3) also join the S3 smoke. No case reads a
clock.

### 6.1 Kernel (U1 and U2)

| case | what it does | what goes red |
|---|---|---|
| `handle_blocks_quota` (S3) | image with two blocks; root sets a child's `Handle` to 64 and reads it back; the child fills its table to 64 with siblings of one Event, signals through the last word, and the next mint answers `QuotaExceeded` | the attach (the 33rd mint answers `NoResource`), the block path of `lookup_handle` (`BadHandle`), the row's bound |
| `handle_blocks_short` | one block; `Handle` 64 answers `NoResource` and the row still reads the default; then 48 is taken | the refusal, and an attach kept on refusal (48 would then find none free) |
| `handle_blocks_past_longest` | seven blocks; 129 refused, then 128 taken, on one child | the bound, moved or inverted (without it the seventh link overruns the body's array) |
| `handle_blocks_boundary` | a child fills index 31 and 32; releases both; the next mint takes 31; the released word for 32 faults `BadHandle` | lowest free across the split; a block's generation |
| `handle_blocks_kept` | two blocks; child A to 64, then to 40; child B to 48 answers `NoResource` | blocks detached on a lowered row |
| `handle_blocks_freed` | two blocks; A to 64, started, exits; B to 64 taken | the teardown's free |
| `handle_blocks_close_all` | a child holds, in a block, the only handle to a bound Interrupt and exits; root binds the line again | the close-all walking only the body (the bind faults `LineBound`) |
| `handle_blocks_room` | a child at 48 whose body entries are full takes a message carrying three handles, which land in its block | `table_room` counting only the body (the take answers `NoResource`) |
| `handle_blocks_boot_set` | a child at 48 is given 40 records, drains all 40 in order, and is started with the tag of record 36 | a boot set that does not extend (the 33rd give answers `NoResource`) |
| `handle_blocks_donate` | no area; 48 refused; a `HandleBlocks` donation; 48 taken | the donation arms |
| `root_blocks_rows` (S3) | hand-made slab block: Memories +25, root's blocks 1, 41 `Ram` rows; root drains 41 | root's attach at boot (the stop names row 29) |
| `slab_block_root_blocks_past_longest` | root's blocks 7, area of 7 | the first boot stop |
| `slab_block_root_blocks_past_capacity` | root's blocks 1, no area | the second boot stop |
| `region_table_old_version` | a hand-made table at version 5 | the version stop |

Two existing cases move: `quota_set_handles` (33 is taken with a block in
the image and refused without one), and `slab_area_unknown_kind`, whose
raw kind 13 becomes known and moves to 14. The version moves in three
places (`kcore.process`, `sosimage.regions` and the runner's
`REGION_TABLE_VERSION`); no case reaches the version stop today, so U2
adds `region_table_old_version`, a table at version 5. A transcript of a
case that raises the blocks gains `SOS: slab area kind=0x0000000d`.

### 6.2 Builder (U1 to U3, `blade test` in `tools/sosimage`)

- `sos_image_limits` version 2 read, version 1 refused; the slab block's
  bytes with root's block count.
- The blocks derived from rows; a stated `handle-blocks` below them
  refused; the derivation past `MAX_SLAB_CAPACITY` refused.
- Root's table derived from the plan's largest step; a plan past the
  longest table refused at its step; a stated `root-handles` below the
  derivation refused; root's rows against root's table.
- A child's `Handle` row at the longest table accepted, one past refused
  (`check_child`); a declared row and a handle floor past it refused
  (`hold_quota`).
- A boot set past `BOOT_SET_CAPACITY`, root's and a child's, refused.
- An S3 fixture shaped as the radio: a process declaring 64 handles and
  20 records in an image with the box, read clean, deriving three blocks
  and a root table of 48.
- Every existing message that names `MAX_HANDLES` (`stock_plan.saw`,
  `limits_plan.saw`, `console_image.saw`, `board_defaults.saw`) names the
  bound it now meets.

One image case, `stock_handles_wide` (S3): a stock-root image whose child
declares 48 handles and holds 40. It goes red if the derivation is
removed: root's `QuotaSet` answers `NoResource` and root stops, naming the
step.

### 6.3 Ceilings (U4)

- Builder: an unknown kind, an unknown right, a right twice, a `system`
  key, a record's rights outside its kind's ceiling, and each right the
  runtime needs withheld, each refused by name.
- `stock_ceilings`: a child reads its ceilings back (`ProcessOp.Ceiling`)
  and each equals its declaration's, an unlisted kind reading none; it
  makes an Event, whose ceiling withholds `signal`, and signals it, and
  root reports its end as an `AccessDenied` fault. Red if root does not
  narrow.
- `stock_record_rights`: a window given with `["map"]`; the child's carve
  of it faults `AccessDenied`. Red if the keep mask is not applied.
- A declaration without `[ceilings]` keeps today's behavior, which every
  existing stock case pins.

### 6.4 Summed rows (U5)

- Builder: each kind's derivation, a stated total below it refused, and
  the `Memory` sum counting heaps once.
- `stock_rows_summed`: two children declaring 6 and 5 threads on an image
  stating no threads total; each makes its threads at once and reports.
  Red if the sum is removed: a thread create answers `NoResource` at the
  board's total.

---

## 7. Units of work

Each lands as a sawtracker patch with its spec text, its tests and its
transcript diff.

- **U1. Handle blocks for children (L, Opus).** The slab kind, the block,
  the body's links, the split access in every table and boot-set function,
  the `QuotaSet` attach, the teardown, `BOOT_SET_CAPACITY` 64,
  `sos_image_limits` version 2 with the builder reading it, the
  `handle-blocks` key for test images, spec §2.7, §2.8, §3 and §8, and
  §6.1's cases but the three root ones.
- **U2. Root's blocks (M, Opus).** The header word, region table version 6,
  the two boot stops, the attach in `start_process`, the builder writing
  root's count, the `root-handles` key for test images, spec §12's slab
  block, the three root cases and the version case. After U1.
- **U3. The builder's derivations (M, Sonnet).** §4.1: root's table and
  the blocks derived, the bounds moved, the boot-set check, the board's and
  image's keys, the radio-shaped fixture, `stock_handles_wide`, spec §12's
  plan text. After U2.
- **U4. Ceilings from the declaration (M, Sonnet).** §4.2: the format, the
  builder's refusals, `bootplan` and `root_arena`, the stock root's
  narrowing and keep masks, spec §12's launch convention, §6.3's tests.
  Independent of U1 to U3, so it can run beside U1 within the two-agent
  limit.
- **U5. Summed rows (S, Sonnet).** §4.3 and §6.4. After U3, since both
  edit `plan_limits`.

SO-103's R4 (the Wi-Fi scan) needs U1 to U4. To file beside them: the
stack length per process (§5.1, with its SL issue), and a note on K3
(§5.2).

---

## 8. Questions for the owner

1. **Handle blocks, or a larger `MAX_HANDLES` per board?** Blocks cost the
   networking image about 1.75 KiB of S3 kernel SRAM and the box 224
   bytes; a board-wide 64 costs 8 to 9 KiB and does not fit the BLE image
   even after K6. *Recommendation: blocks, on every board.*
2. **The longest table: 128?** Six links a body (24 bytes on the S3); the
   radio declares 64 and may grow by three handles a library task; raising
   it later is an edit costing four bytes a body per block, up to 240.
   *Recommendation: 128.*
3. **The boot set grows with the table, and a `BootSet` holds 64?** Root's
   rows pass 32 in a networking image, so its boot set must. Every program
   written with `main` pays 384 more bytes of start-up stack; a process
   with a longer boot set is the builder's to refuse. *Recommendation:
   yes.*
4. **Blocks kept until the teardown when a row is set lower?** It keeps
   every generation rising within a process's life and costs nothing but a
   block a launcher chose to take. *Recommendation: yes.*
5. **`[ceilings]` optional, complete when present, and given records held
   within it; may an image narrow a process's ceilings further?** An image
   key would let an integrator distrust a package more than its author
   does, and nothing asks for it yet. *Recommendation: the declaration
   only, now; an image key when a product needs it.*
6. **Declared rows summed: derive the slab totals, or refuse an image that
   falls short?** Deriving is what records and attachments do, and a
   derivation that outgrows the S3's kernel range fails the link, naming
   the region. *Recommendation: derive.*
7. **A stack length per process as its own issue now?** It needs
   `imgformat`'s reserved word, a sawlang change, and saves up to 14 KiB of
   SRAM for each small process on the S3. *Recommendation: file it now,
   after U1 to U5.*
8. **Lower the S3's body table from 32 later?** Under 1 KiB saved on the
   box, and a board default that moves. *Recommendation: not now.*
