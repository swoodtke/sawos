# SO-93: every kernel access to a process's memory

## Reading this after the copy doors were widened

This audit describes the kernel as SO-93 left it, when each copy door asked
a window (a range of addresses on the process body) before it asked the
rows. SO-92's widening removed the windows, so where the tables below say
"window", `rw_base`, `rw_top`, `ro_base`, `ro_top`, `region_base`,
`window_holds`, `meets_execute` or `PlacedImage`, read this instead:

- **One rule, decided in one place.** `translate_user` is the buffer rule
  for both directions (spec §2.2). A buffer lies whole in one non-device
  row that grants the direction's permission (read for a source, write for
  a destination), and no row of the process that permits execute grants any
  of its bytes. Any row serves: a segment the loader recorded, the stack
  grant, or a row `map` installed.
- **Execute is asked of the bytes, not the address.** `grants_execute`
  (which replaced `meets_execute`) compares each executable row's physical
  range with the buffer's bytes, so a writable view of bytes that are
  executable at another address is refused on a translating profile.
- **The doors are alignment and then that rule.** `copy_in_check` and
  `copy_out_check` ask nothing else. The findings' fixes stand: the write
  permission is read (F2), and the late doors ask again at the wake, the
  buffer before any other state (`wake_call_reply`, `resume_call_room`).
- **A thread's stack top** is asked the destination test for the word below
  it (`stack_top_ok`), in place of a test against the region.
- **Not yet true, until the overlap rule lands:** where two rows of one
  process cover one address on a profile whose hardware lets the first
  matching region decide, a read-write row recorded after a read-only one
  is a destination the process cannot store to itself. Spec §2.2 marks the
  two sentences this touches.

The cases that hold the rule's edges are `buffer_rule` and `buffer_alias`;
the late doors are held by `late_door_buffer_gone`, `kill_late_door`,
`late_door_order` and `read_only_row_not_destination` on every profile that
runs them.


Audit of sawos main at `246c5768` (rebased onto `5e948ec9`; the two commits
between are tracker-only). Every site where the kernel reads or writes a
process's memory, keeps an address, a length or a translation that came from
one, or reads memory a process may write, with its class and verdict.

## Headline

- **48 sites** in the table below, **five findings**: three fixed in the kernel
  (F1, F2, F4: first commit), one in the HAL (F3, the NAPOT window: second
  commit), and one that fails closed and is left for the owner (F5).
- **Worst:** a process with two threads makes the kernel copy as many bytes of
  its choosing as one of its own rows holds into a 128-byte slot of the
  kernel's exchange slab (F1). One thread parks a
  fused `Call` on a full ring; a sibling rewrites the args record's length;
  the resume re-read the record and trusted the length. Reachable on every
  profile, with nothing but a pipe; the run stopped all three machines with
  `panic at slab.saw:166: Slab.[]: index out of range: -1515870812`, the
  slab's next-link overwritten with the child's own pattern (`0xA5A5A5A4`).
- **The one that matters most for widening:** F2. No door read a row's write
  permission; the writable window stood in for it. On the identity profiles a
  read-only `map` inside the window was a destination, and once the window no
  longer narrows, so would every read-only row on every profile, the kernel's
  own stats region (mapped read-only into any process that asks) among them.
  F4 is the second: an image is memory, and the loader read its segment table
  again after validating it, across preemption points at which a delivery can
  write into it.

## Findings

| # | Severity | What | Profiles reachable today | Fix | Case |
|---|---|---|---|---|---|
| F1 | kernel memory written with process-chosen bytes; machine stops | `resume_call_room` re-reads a room-parked call's args record and uses its `len` unchecked to size the body copy into a 128-byte exchange slot | riscv32, arm64, riscv32-flat, ESP32-C3, ESP32-S3 (core code) | the resume holds the re-read record to `check_send`'s length rule: past `PIPE_BODY_BYTES` dooms the process with `BadArg` before any copy | `room_record_rewritten` (3 profiles) |
| F4 | kernel memory written or read at a process-chosen address; machine stops | `place_image` re-reads the image's segment records (and `process_create` its entry) after `validate_image`, across `long_copy`/`long_zero` preemption points where a delivered wait record can land on the image | riscv32, riscv32-flat (an image inside a parked process's copy-out window); every profile after widening | `validate_image` copies the header and table into an `ImageTable` once; placement and the entry read only that copy | `image_written_while_placed` (riscv32, flat) |
| F2 | a process has the kernel write memory it may only read | `copy_out_check`/`translate_user` never read a row's write permission | riscv32, flat (a read-only `map` in the window); every profile through an image linked with read-only data above its data | `translate_user` takes the permission the direction needs; a row that does not grant it is no candidate | `read_only_row_not_destination` (riscv32, flat), `read_only_segment_not_destination` (3 profiles) |
| F3 | a process reaches device registers outside its window | a 4-byte device window is programmed as a NAPOT entry (`napot_bound(base, 4)`), whose size comes from the base's trailing ones: 8 bytes at least, 8 KiB at offset `0xFFC` | riscv32 virt, ESP32-C3 (shared `rv32core.prot_device`) | `prot_device` programs a 4-byte window as one NA4 entry | `device_window_four_bytes` (riscv32), second commit |
| F5 | none (fails closed) | arm64's `prot_update` revoke clears every page of the removed row without asking whether another row covers it (two device rows over one page) | arm64 | not fixed: owner question Q3 | none |

Read, not run, and not a finding: `executable_at` answers for any executable
row while PMP lets the lowest-numbered covering row decide, so an entry can
pass the check and fault at its first fetch. It fails closed (the thread's
process faults); see Q2.

## Probes on the unfixed kernel

All under the pinned v0.23.0 compiler, on main as rebased.

F1, `room_record_rewritten` before the fix, every profile:

    SOS: console handover
    panic at slab.saw:166: Slab.[]: index out of range: -1515870812 (len 64)

(arm64: `-6510615555426900572`, the same pattern read as a 64-bit index.)

F2, `read_only_row_not_destination` before the fix (riscv32 and flat): the
child's `pipe_create` into the read-only piece is taken.

    SOS rodest: read-write piece, record: taken
    SOS: process exit: code=0x00000007 process=0x00000001
    SOS rodest: the child ended with status 65543

F4, `image_written_while_placed` before the fix (riscv32, first attempt):

    SOS: console handover
    panic at loader.saw:523: cast to UInt out of range: 8808893709

`8808893709` is `0x2_0D0D_0D0D`: the wait record's key and its `Timer` tag,
read as the second segment's load address after validation had passed it.
The panic is the narrowing cast; aimed at the record's other words the same
delivery chooses a segment's `file_off` (a copy from any physical address into
the new child) or its length (a zero-fill past the destination).

F3 was run by the reviewer (their write-up on the issue): a 4-byte carve at
`0xFFC` of the console page, mapped, reads `0x1000_1000`, the next device.

## How completeness was established

1. **Greps over `kernel/`, `hal/` and `rt/`** (counts include comments):
   `copy_out_check` 23, `copy_in_check` 10, `copy_out(` 15, `copy_in(` 10,
   `translate_user` 14, `meets_execute` 2, `executable_at` 3, `UnsafeMemory`
   74, `UnsafePointer` 119, `phys_to_virt` 34, `copy_bytes` 8, `long_copy` 9,
   `long_zero` 10, `store_words` 0, `write_result` 13, `frame_slot` 24, plus
   `wait_buf|call_src|call_len` (every kept user address), `remove_grant|
   clear_domain|record_grant|install_row(` (every row change), and
   `preempt_point|long_copy|long_zero` (every preemption point). Each non-comment
   hit is a row below or is kernel-only memory (named in the table's last
   section).
2. **The op walk** (`kernel/abi/src/ops.saw`, every op of every kind, and the
   three universal ops): what each op touches is in the table "Ops". Every op
   not listed there answers in registers only.
3. **Every dereference of a stored address**: `UnsafeMemory` constructions are
   in `mem.saw` (the two byte movers), `loader.saw` (`header_at`, `seg_at`),
   `process.saw` (the boot region table), `slab.saw`, `threads.saw`
   (`frame_slot`), `stats.saw`, and the HAL (frames, FP areas, page tables, MMIO
   registers). The byte movers' callers are the two doors, the strided movers,
   and nothing else.

## The doors

| Door | File | Permission asked before | Asked now | Leans on a window? |
|---|---|---|---|---|
| `copy_out_check` / `copy_out` | `process.saw` | alignment; `rw_base..rw_top`; a non-device covering row; no executable row over any byte | the same, and the covering row must grant write | before: yes, for write (F2). Now: no; the window only narrows |
| `copy_in_check` / `copy_in` | `process.saw` | alignment; region or in-place read-only window; a non-device covering row; no executable row | the same, and the covering row must grant read | no: every row grants read (image segments must be readable or executable, `map_access` refuses write without read, executable rows are refused anyway); the check is now explicit |
| late doors (`wake_one_waiter`, `wake_call_reply`, `resume_call_room`) | `refs.saw` | the early door again, at the wake | unchanged, so they inherit the permission | no |
| `executable_at` (thread entry) | `process.saw` | any non-device executable row covering the entry | unchanged | no (Q2) |

## Ops

| Op | Memory touched | Door | Class | Verdict |
|---|---|---|---|---|
| `SystemOp.ProcessNext` | reads the cursor doubleword, writes the next pid back | `copy_in`, `copy_out_check` then `copy_out` in one step | 1, 4 | sound: read once, checked for write before anything is minted, written in the same step |
| `SystemOp.ProcessByPid` | reads a pid doubleword | `copy_in` | 1 | sound |
| `SystemOp.StatsRegionMap` | writes the row index; installs a read-only row over kernel memory | `copy_out`; `install_row` | 1, 6 | sound; the row is read-only, so after F2 no door can write through it |
| `SystemOp.ProcessCreate` | reads the image's header, table and bytes; writes the destination | loader (`validate_image`, `place_image`), not a copy door | 1, 4, 7 | F4, fixed; the bytes copied are contents only |
| `SystemOp.SlabDonate` | zeroes the donated region with preemption points | `long_zero` | 1 | sound: `maps == 0` and `refs == 1`, so no row reaches the bytes and no delivery can write them |
| `ProcessOp.BootHandleNext` | writes a record | `copy_out` | 1 | sound |
| `ProcessOp.PipeCreate` | writes a record | `copy_out_check` before minting, `copy_out` after, one step | 1 | sound |
| `ProcessOp.Stats` | writes a record | `copy_out` | 1 | sound |
| `ProcessOp.ThreadCreate` | none: entry and stack go into the new frame | `executable_at`, region bounds | 5 | sound; Q2 |
| `ClockOp.Now` | writes a record | `copy_out` | 1 | sound |
| `TimerOp.Arm` | reads a record | `copy_in`, once | 1, 4 | sound |
| `WaiterOp.Wait` | writes a record now or at a wake | `copy_out_check`; `deliver_attachment` now, or `block_on_wait` keeps the VA and `wake_one_waiter` asks again | 1, 2, 3 | sound |
| `PipeInletOp.Post` | reads the args record, then the body | `send_args` copies the record once; `copy_in` of `len <= 128` from that copy | 1, 4 | sound |
| `PipeInletOp.Call` | reads the record and body, writes the reply, possibly at a resume and a wake | early checks of all three; the resume re-reads the record | 2, 3, 4 | F1 (the resume), fixed; the rest sound |
| `PipeOutletOp.Take` | writes the take record and body | `copy_out_check`, then two `copy_out`s in one step | 1 | sound |
| `PipeReplyOp.Resolve` | writes the reply record now, or at a wake | `copy_out_check`; `park_resolve` keeps the VA, `wake_call_reply` asks again | 1, 2, 3 | sound |
| `PipeRequestOp.Reply` | reads the record and body | `send_args`, `copy_in` | 1, 4 | sound |
| `PipeRequestOp.ReplyWait` | reads one record; writes a wait record now or at a wake | `copy_in` once; `check_send` on the copy; `copy_out_check`; then `Wait`'s paths | 1, 2, 4 | sound |
| `MemoryOp.Map`, `IoMemoryOp.Map` | adds a row | `install_row` -> `record_grant` | 6 | sound |
| `MappingOp.Unmap` | removes a row | `remove_grant` | 6 | sound: record and hardware change together, no preemption point; late doors re-translate |
| every other op (`DebugPrint`, `Shutdown`, `ProcessSelf`, `ClockGet`, `TierGet`, `InterruptBind`, `ThreadSelf`, `Exit`, `GetStatus`, `EventCreate`, `WaiterCreate`, `Start`, `Give`, `RestrictRights`, `Ceiling`, `ThreadOp.*`, `EventOp.*`, `WaiterOp.Add/Remove`, `InterruptOp.Ack`, `ClockOp.TimerCreate`, `TimerOp.Disarm`, `MemoryOp.Split`, `IoMemoryOp.Carve`, `MappingOp.Base`, `PipeReplyOp.Ready`, `PipeRequestOp.Sender`, `RELEASE_OP`, `MINT_OP`, `INFO_OP`) | registers only (`Join`'s result and `Receive`'s word are register values) | none | — | sound |

## Every site

Classes: 1 one step, 2 late door, 3 kept address, 4 double fetch, 5
permission, 6 row change, 7 shared memory.

| # | File | Function | Touched (user address, from) | Dir | Class | Verdict |
|---|---|---|---|---|---|---|
| 1 | process.saw | `translate_user` | any, from its caller | r/w | 5 | F2 fixed: takes `need` (read or write) and skips a row without it; refuses any range an executable row meets; device rows never candidates |
| 2 | process.saw | `copy_out_check` | destination | w | 1, 5 | sound after F2; the window is now narrowing only |
| 3 | process.saw | `copy_out` | destination, translated again in the same step as its check | w | 1 | sound: `copy_bytes` takes no preemption point |
| 4 | process.saw | `copy_in_check` | source | r | 1, 5 | sound; read now asked explicitly |
| 5 | process.saw | `copy_in` | source | r | 1 | sound |
| 6 | process.saw | `executable_at` | thread entry (register value, not touched) | x | 5 | sound, fails closed; Q2 |
| 7 | dispatch.saw | `read_pid` | `arg0` | r | 1, 4 | sound: one copy |
| 8 | dispatch.saw | `process_next` | `arg0` | r, w | 1, 4 | sound |
| 9 | dispatch.saw | `stats_region_map` | `arg0` | w | 1 | sound |
| 10 | dispatch.saw | `boot_handle_next` | `arg0` | w | 1 | sound |
| 11 | dispatch.saw | `process_stats` | `arg0` | w | 1 | sound |
| 12 | dispatch.saw | `clock_now` | `arg0` | w | 1 | sound |
| 13 | dispatch.saw | `timer_arm` | `arg0` | r | 1, 4 | sound: one copy, fields used from it |
| 14 | dispatch.saw | `waiter_wait` | `arg0` | w | 1, 2 | sound: checked at the call; ready path delivers in the same step; park keeps the VA (site 33) |
| 15 | dispatch.saw | `pipe_create` | `arg0` | w | 1 | sound: checked before the mint, written in the same step |
| 16 | dispatch.saw | `send_args` / `check_send` | args record, `arg0` | r | 1, 4 | sound: one copy, every field judged on it |
| 17 | dispatch.saw | `pipe_post` | body at `args.body`, `args.len` from the copy | r | 1 | sound |
| 18 | dispatch.saw | `pipe_call` | record, body, reply buffer | r, w | 1, 2 | sound at the call; body copied in the same step when the ring has room |
| 19 | dispatch.saw | `pipe_take` | `arg0` | w | 1 | sound |
| 20 | dispatch.saw | `pipe_resolve` | `arg0` | w | 1, 2 | sound |
| 21 | dispatch.saw | `park_resolve` | keeps `dst` | — | 3 | sound: a VA, re-translated at the wake |
| 22 | dispatch.saw | `pipe_reply_status` | body at `args.body` | r | 1 | sound: `len` from a validated copy |
| 23 | dispatch.saw | `pipe_reply_wait` | record, `rec_dst` | r, w | 1, 4 | sound |
| 24 | dispatch.saw | `thread_create` | entry, stack top (registers) | — | 5 | sound: the kernel never dereferences the stack |
| 25 | dispatch.saw | `process_create` | image (a `Memory`), destination | r, w | 4, 7 | F4 fixed: entry and table read from `ImageTable` |
| 26 | dispatch.saw | `slab_donate` | the donated region | w | 1 | sound (no row reaches it) |
| 27 | dispatch.saw | `install_row` | adds a row | — | 6 | sound |
| 28 | dispatch.saw | `MappingOp.Unmap` | removes a row | — | 6 | sound |
| 29 | refs.saw | `deliver_wait_record` | `dst` | w | 1 | sound: every caller asked `copy_out_check` for the whole record in the same step; the refusal arm is a kernel bug |
| 30 | refs.saw | `deliver_attachment` | `dst` | w | 1 | sound |
| 31 | refs.saw | `deliver_take` | `dst` | w | 1 | sound |
| 32 | refs.saw | `wake_one_waiter` | `THREADS[t].wait_buf` | w | 2, 3 | sound: asks again, dooms on refusal |
| 33 | waitables.saw | `block_on_wait` | keeps `dst` | — | 3 | sound: a VA |
| 34 | refs.saw | `wake_call_reply` | `wait_buf` | w | 2, 3 | sound; its body length is now bounded on every path (F1) |
| 35 | waitables.saw | `block_on_call_room`, `tie_call_reply`, `clear_pending_call` | keep `src`, `len`, `dst` | — | 3 | sound: VAs and a length the call checked for equality |
| 36 | refs.saw | `resume_call_room` | record at `call_src`, body | r | 2, 3, 4 | F1 fixed: the re-read record is one copy, held to `check_send`'s length rule; handle words re-asked in `hold_staged` |
| 37 | refs.saw | `hold_staged`, `release_carried` | handle words of a copy | — | 4 | sound: act on the caller's copy and re-validate every word |
| 38 | loader.saw | `validate_image` | image header and table | r | 4, 7 | F4 fixed: read once into `ImageTable` |
| 39 | loader.saw | `place_image` | image bytes, destination | r, w | 1, 7 | sound after F4: reads records from the table; bytes are contents |
| 40 | preempt.saw | `long_copy`, `long_zero` | as their callers | r, w | 1 | the only preemption points; see "Preemption points" |
| 41 | process.saw | `record_grant` | appends a row | — | 6 | sound |
| 42 | process.saw | `remove_grant` | removes a row: hardware revoked, record compacted, region dropped last | — | 6 | sound |
| 43 | process.saw | `clear_domain` | every row, at teardown | — | 6 | sound |
| 44 | process.saw | `rows_move_to_piece` | a row's `region` field | — | 6 | sound: range and permission unchanged |
| 45 | hal (all) | `frame_init`, `syscall_return`, trap save/restore, FP areas | the thread's frame, kernel memory (static arena or donated thread extent) | w | — | sound: the frame address is kernel state (`mscratch`, `sp_el1`, the S3's save area), never a user number; donated extents have `maps == 0` |
| 46 | riscv32-common | `prot_device` / `napot_bound` | the device row's range in PMP | — | 5 | F3 fixed (second commit) |
| 47 | arm64 | `prot_update` (revoke) | pages of the removed row | — | 6 | F5, fails closed, Q3 |
| 48 | process.saw | `mint_boot_regions` | the region table in the kernel image | r | — | sound: written by the build, read before user mode |

Kernel-only memory reached by `UnsafeMemory` and not listed: `slab.saw`
(extents), `stats.saw` (the stats region; a process holds at most a read-only
row over it), `threads.saw` (frames), the HALs' page tables and MMIO.

## Class answers

**1. One step.** The only preemption points are `irq.preempt_point`, called
from `preempt.long_copy` and `preempt.long_zero`, called from
`loader.place_image` (both) and `dispatch.slab_donate` (`long_zero`). A point
delivers interrupts and runs no other thread (no switch happens until
`pick_next`), so no row can go and no thread of any process can run between
two strides. No copy door crosses a point: the doors use `copy_bytes` and move
records bounded by `WaitBuffer`. A delivery at a point does write: it hands a
parked thread its wait record through `copy_out`, which asks its full check.
What that nested write can meet: the image being placed (F4: it was re-read;
now only its contents are), the destination being placed (contents only; a
process that maps a launcher's destination writable already has write
authority over those bytes), a donated slab extent (impossible: no row reaches
it). No kernel structure the outer copy holds a pointer into can move: the
copies hold the table on the kernel stack and two `Memory` regions that no
delivery can split, free or donate (deliveries reach `unref_waitable`, never a
Memory's count; deaths are paid at the switch point after the syscall).

**2. Late doors.** Every op that parks and later touches memory: `Waiter.Wait`
and `ReplyWait`'s wait leg (the record, `wake_one_waiter`), a fused `Call`'s
reply and a parked `Resolve`'s (`wake_call_reply`), a `Call` parked for room
(record and body, `resume_call_room`). `Join`'s result and `Receive`'s word are
registers; a Timer's, an Interrupt's, an Event's, a death's and a pipe level's
answers are all wait records through `wake_one_waiter`; the boot-handle drain
never parks; revocation writes a status register only. So SO-90 found all of
them; F1 was a hole in the third one's re-validation.

**3. Kept addresses.** `ThreadSlot.wait_buf`, `call_src`, `call_len`: user
VAs and a length, re-translated by the doors at every use. No physical address,
translated pointer or row index from a process's memory is kept across a switch
point (`MappingSlot.row` is the kernel's index into its own record, kept in
step by `compact_mapping_rows`). Per the owner's ruling, a row unmapped and
another mapped at the same addresses is judged as it is at the wake;
`read_only_row_not_destination` pins both halves (read-only: `BadBuffer`;
read-write: delivered into the new row).

**4. Double fetch.** Every record is copied once into a kernel local:
`read_pid`, `timer_arm`, `send_args`, `pipe_reply_wait`. The two that were read
twice: a room-parked call's record (by design; the second reading was not held
to the length rule: F1) and an image's header and table (F4).

**5. Permission.** See "The doors". Lead 1 confirmed (F2). For a source, no
row without read can exist today, so the new read check refuses nothing; it is
there so the door does not lean on that.

**6. What removes or changes a row.** `MappingOp.Unmap` (`remove_grant`), a
teardown (`clear_domain`; a kill reaches it through the teardown), `Split`'s
`rows_move_to_piece` (the `region` field only). Rows are added by
`record_grant` from the loader and `install_row`. Nothing changes a row's range
or permission in place. **The rule for a future remap** (in spec §2.2): to
every door it must be an unmap followed by a map. The grant record and the
hardware change together with no preemption point between; nothing caches a
row's permission or translation across a switch point; the next door to look,
late doors included, judges the new row.

**7. Shared memory.** Records are copied before use (class 4). Bodies are
copied as bytes and never interpreted, so a body torn by a concurrent writer is
still just bytes. The image is now read once (F4). The region table is boot
data. No door depends on contents staying still between its check and its use:
the doors check addresses and rows, not contents.

## The two leads

- **Write permission (lead 1): confirmed** (F2), on riscv32 and flat by a
  read-only `map` in the window, on all three profiles by an image linked with
  read-only data above its data. Fixed in `translate_user`.
- **4-byte NAPOT window (lead 2): confirmed by reading** `napot_bound` and
  `prot_device`, and by the reviewer's run (F3). `napot_bound(base, 4)` is
  `base >> 2`, which NAPOT reads as at least 8 bytes and more for each
  trailing one. `napot_bound` has one caller, `prot_device`; its other inputs
  (a board's own window, any carve of 8 bytes or more) are powers of two of at
  least 8 aligned to their length (`device_window_ok`), which NAPOT encodes
  exactly. The ESP32-C3 shares the code. The ESP32-S3 and the flat profile
  program nothing.

## What bears on widening

- **F2's fix is a precondition.** Without the write check, every read-only row
  a process holds would become a destination once the window stops narrowing:
  a read-only `map` of shared memory, a read-only segment, and the kernel's own
  stats region, whose row `SystemOp.StatsRegionMap` installs read-only over
  kernel memory in any process with the right.
- **F4's fix is a precondition.** Any `Memory` a process can map writable
  becomes a place a wait record can land, an image included.
- With both in, no door's correctness leans on a window: the windows only
  narrow. Every row a widened door would accept is judged by its own
  permission, no executable row covers it, and it is not a device row.
- Still true after widening: a mapped row can go while a thread is parked;
  every late door asks again (SO-90), and a remap is that same event.

## Fixes, cases and mutants

| Finding | Fix (function) | Case | Mutant | Fails at |
|---|---|---|---|---|
| F1 | `refs.resume_call_room`: `args.len > PIPE_BODY_BYTES` dooms with `BadArg` right after the record's copy | `room_record_rewritten` | the check made `false &&` | all three profiles: `panic at slab.saw:166` instead of "length past a slot: the process ended with BadArg" |
| F2 | `process.translate_user` takes `need`; `copy_out_check`/`copy_out` pass write, `copy_in_check`/`copy_in` read | `read_only_row_not_destination`, `read_only_segment_not_destination` | `(true \|\| (row.perms & need) == need)` | riscv32, flat: "read-only piece, record: the process ended with BadBuffer"; all three: "a record into the read-only data: BadBuffer" |
| F4 | `loader.ImageTable`; `validate_image` fills it; `place_image`, the boot door and `process_create` read only it | `image_written_while_placed` | `place_image` reads each record from the image again | 10 of 10 runs (5 on riscv32, 5 on flat): `panic at loader.saw:560: cast to UInt out of range` |
| F3 | `rv32core.prot_device`: a `PROT_GRAIN`-byte window is one NA4 entry | `device_window_four_bytes` (riscv32) | the NA4 branch made `false &&` | "SOS devfour: UNREACHABLE: the read landed" instead of "SOS: fault " |

F3's fix is NA4 rather than refusing windows under eight bytes, as the lead
preferred: it is one branch in the one function that programs a device row,
it keeps one-register windows, and both riscv32 boards have a four-byte PMP
grain (`PROT_GRAIN` is 4 on both), which is when NA4 is selectable.

## Runs (pinned v0.23.0, suite lock taken and released per run)

| State | Suite | C3 smoke | S3 smoke | Rows vs baseline |
|---|---|---|---|---|
| main (baseline) | 696 passed | 6 | 36 | — |
| first commit | 706 passed | 6 | 36 | +10 new rows, nothing else moved |
| both commits | 707 passed | 6 | 36 | +1 more (`device_window_four_bytes`) |

The runner's diff against main adds lines only: no existing entry changed.

## Questions for the owner

- **Q1. Overlapping rows of one process with different permissions.** On PMP
  the lowest-numbered covering row decides what the process may do; the doors
  now take any covering row that grants the permission (write through an RW row
  even where an R row recorded first denies the process's own store). That is
  the authority the process holds, so I built it that way; it belongs with
  SO-92's question of what an overlap means.
- **Q2. `executable_at` vs the hardware.** Same overlap question for execute:
  the entry check accepts any executable row; PMP may fault the first fetch.
  Fails closed. Should the check agree with the hardware, or should overlaps
  be refused?
- **Q3. arm64 revoke over a shared page (F5).** Two device rows over one page
  (an `IoMemory` mapped twice into one process): unmapping one clears the
  page, the other row stays in the record and stops working. Fails closed.
  Refuse a device row that meets another, or re-install the survivors after a
  revoke?
- **Q4. The parked call's record.** It is read twice by design (the thread slot
  keeps only its address). I held the second reading to every rule of the
  first. The alternative, copying the record at the call into the exchange or
  thread, would make the resume act on what the call validated.
