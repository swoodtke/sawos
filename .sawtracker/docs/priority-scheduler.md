# SO-148: the priority scheduler (design, revision 2)

Scope: build spec §7's priority scheduler, with what the networking design
(SO-103, `.leadwork/so103-design.md`) asks of it in §4.3: levels inside one
process, strict preemption on wake, a priority ceiling for critical sections,
and the latency diagnostic of requirement 4.2. Nothing is built from this
before the owner and the reviewer have read it.

Written against main at `734de56c`. Every claim about the kernel cites the
function it rests on. Units in flight that touch the same ground: SO-105's
per-process ceilings (in review: `process.toml` `[ceilings]`,
`tools/sosimage/src/rights.saw`, a `restrict` step in the stock root) and its
handle blocks (in progress: `SlabKind.HandleBlocks` = 13, no new op).
`MappingOp.Len` (3) is in. The op, right and fault numbers below are the next
free ones after all of them.

---

## Changes in revision 2

The owner's rulings of Nov 24 (`rulings1.md`) on revision 1:

- **8 system levels, not 16.** The ready bitmap is one byte and pick-next is
  one table load (§4). The radio's six levels fit 2 to 7 (§3.5).
- **8 bands: the four of spec §7 and four realtime bands, `Rt0` to `Rt3`**
  (`rt0` to `rt3` in TOML), replacing `Band4` to `Band7` (§3.1).
- **Two maps a declaration can ask for:** the simple map by default, spec's
  default extended to eight bands, or full access, the eight bands one to
  one onto the eight levels. `process.toml`'s request is now a mode,
  `bands = "simple" | "full"`, not a map (§3.2, §10).
- **A priority-inheritance lock for the runtime's in-process locks**, in
  place of revision 1's parked wait on an Event (Q11): `ThreadOp.LockWait`
  and `LockWake`, a lock word naming its holder by its own Thread handle,
  the holder boosted to its highest waiter's level until it gives the lock
  back. Weighed against the section word as a ceiling; the output lock
  takes the inheritance lock and the heaps take the section (§7, §8.3).
- **Spec §7's "no inheritance" stays for pipes**; §7.6 says why a lock
  inside one process is different.
- **Brought up to date with main** and the units in flight: the next free
  numbers (§12.7), `[ceilings]`'s new right name, the stock root's step
  order beside `restrict` (§10.4).
- Q3 to Q10 stand as accepted. Units (§15) and questions (§16) are redone.

---

## 1. Verdict

**Build it in six units, with one level behaving exactly as today.**

1. **Levels.** Eight system levels, a ready list per level and a one-byte
   ready bitmap; pick-next is one table load and a list pop, the same
   arch-free code on every profile.
2. **Bands.** Eight per process: `Background`, `Low`, `Normal`, `High` and
   `Rt0` to `Rt3`, resolved through the process's map when a thread starts.
   A launcher grants a child the simple map (`Background` at 0, every other
   band at 1) or, when its declaration asks, full access (band `b` at level
   `b`), and never more than it holds itself. A process still names only its
   own bands. The radio takes full access: its serve thread at `Normal` (2),
   its tasks at `High`, `Rt0` and `Rt1`, the Wi-Fi interrupt thread at `Rt2`
   and the BT one at `Rt3` (7).
3. **Preemption on wake** is a level compare in the one function every wake
   goes through. The switch stays at the user-return boundary.
4. **The ceiling is the section word**, as revision 1: no trap to enter or
   leave, one to leave when a sibling was held back, confined to its
   process, surviving its holder being off the processor.
5. **An inheritance lock inside one process.** A thread that finds a lock
   held parks in the kernel naming the lock; the kernel reads the holder out
   of the lock word and raises it to the waiter's level, along a chain of
   at most `LOCK_CHAIN_MAX` locks, until it gives the lock back. The
   uncontended path makes no syscall. The output lock uses it, because a
   print is long and can park, and a section around it would hold the
   radio's interrupt threads back for the whole print. The heaps use the
   section, because their windows are short, never park, and nest inside
   the radio libraries' own critical sections for free.
6. **No inheritance across pipes; the direct switch later.**

**Compatibility by construction.** Every existing thread starts at `Normal`
under the simple map, so every existing case runs at level 1, where the
rules are today's, and no case's expected text changes. The networking
design's latency estimate is short (§5.3); the latency record (§11)
measures it.

---

## 2. The scheduler as built

- **One FIFO.** `kcore.threads` holds `READY_HEAD`/`READY_TAIL` and one
  `link` per thread (`ThreadSlot.link`); `ready_push` appends and
  `ready_pop` takes the head. `ready_remove_process` unlinks a dying
  process's threads with a trailing-pointer walk.
- **One switch point.** `ktrap` (`kernel/core/dispatch.saw`) returns
  `pick_next(frame_addr)` on both its interrupt and its syscall paths, and the
  HAL's trap entry resumes whatever frame comes back (`hal/riscv32-common/
  kernel/trap.S`, `hal/arm64/kernel/boot.S`, `hal/xtensa-esp32s3/kernel/
  boot.S`). `pick_next` (`kernel/core/sched.saw`) drains owed deaths, returns
  the current frame when `stays_running` holds (`Running` and not
  `NEED_RESCHED`), otherwise requeues a still-running thread at the tail,
  saves FP state (`hal.fp_switch_out`), pops the head, idles or stops on
  deadlock when there is none, and installs the next through `run_thread`
  (domain reload, `hal.fp_switch_in`, a fresh slice). `resume_after_death`
  is the same tail after a teardown.
- **The flag.** `NEED_RESCHED` is raised by the tick when a slice ends
  (`on_timer_tick`, `kernel/core/irq.saw`), by `Yield`
  (`thread_op_decoded`), by every park (`block_on_wait`,
  `block_on_call_room`, `block_on_call_reply` in `kernel/core/waitables.saw`;
  `block_on_join` in `threads.saw`) and by `exit_thread`. A wake raises
  nothing: `revoke_blocked`, `wake_one_waiter`, `wake_call_reply`,
  `resume_call_room` (`refs.saw`), `exit_thread`, `invalidate_thread`,
  `process_start` and the `Start` arm of `thread_op_decoded` each set the
  thread `Runnable` and `ready_push` it.
- **The tick** charges `SLICE_LEFT` only when a thread is running
  (`a_thread_is_running`), with `SLICE_TICKS` = 1 and `TICK_PERIOD_US` =
  10 ms (`threads.saw`).
- **Idle and deadlock.** `idle_until_runnable` loops on `READY_HEAD == 0`,
  delivering through `idle_once` and asking `has_external_wake_source` after
  each turn; `fatal_deadlock` stops the machine.
- **Priorities.** The loader reads the header's `prio_map`
  (`ImageTable.prio_map`, `kernel/core/loader.saw`) for root, prints it
  (`SOS: root image ok ... prio=`) and stores nothing. No op carries a band.
  Blade emits the field from `[sos] priorities` (sawlang `LANGUAGE_SPEC.md`,
  the `[sos]` table: four bands, each 0 to 7, packed one byte each), and
  every manifest in the tree states the default,
  `{ background = 0, low = 1, normal = 1, high = 1 }`.
- **Thread creation.** `ProcessOp.ThreadCreate(entry, stack_top, arg)` uses
  all three argument registers (`thread_create`, `dispatch.saw`).
  `ThreadOp.Start` takes `arg0` and ignores it; the floor passes 0
  (`sos_thread_start`, `kernel/sysapi/src/floor.saw`).
- **The runtime's locks** (`rt/common/src/lib.saw`): the arena and each heap
  sit behind std's `SpinLock`, which spins without yielding; the output lock
  is a recursive owner-and-depth pair behind a `SpinLock`, whose waiter
  yields through `sos_rt_yield`. The owner identity is the thread's block
  address (`sos_rt_lock_owner`, `kernel/sysapi/src/rt.saw`), and the block
  holds the thread's own `Control` handle (`ThreadBlock`); a thread with no
  block gets an odd number and its yield does nothing (`tests/lock-wait`).
  Both comments say a higher waiter above a preempted holder waits for good
  under levels.

---

## 3. Levels and bands

### 3.1 Eight levels, eight bands

- **Levels: 8** (`SYSTEM_LEVELS`, `kcore.limits`), 0 the lowest, as spec §7
  has them.
- **Bands: 8 per process** (`BANDS`): `Background`, `Low`, `Normal`, `High`
  (spec §7's, wire tags 0 to 3) and `Rt0`, `Rt1`, `Rt2`, `Rt3` (tags 4 to
  7), the realtime bands, `Rt3` the highest.

**A process still never names a system level.** A band is a rank inside the
process; the map, which the launcher writes and the process cannot read,
says where each rank sits in the machine. Full access makes the map the
identity, but the process still asks for a band and the kernel still reads
its level from the map, so a launcher that grants the simple map to the same
program moves every band without a change to the program.

### 3.2 The two maps a launcher grants

```
band            Background Low Normal High Rt0 Rt1 Rt2 Rt3
simple (default)     0      1    1     1    1   1   1   1
full                 0      1    2     3    4   5   6   7
```

- **The simple map** is spec §7's default extended to eight bands: an
  application's bands are declarative, its threads at 0 or 1, and levels 2
  to 7 are headroom it cannot reach. A process no launcher maps has it.
- **Full access** is asked for in the declaration (`bands = "full"`, §10)
  and granted by the launcher; the builder checks the request and writes it
  into the stock root's plan, and the stock root sets it. It is still a
  declaration, never a level named at a syscall.
- **The kernel's op is general.** `ProcessOp.BandMap(map)` takes any map
  within the caller's cap (§3.3), so a hand-written launcher (a test root,
  a later launcher service) can grant other maps; the builder writes only
  the two.

### 3.3 Storage, root and the cap

- **Storage.** One `UInt32` per body, `ProcessBody.bands`: band `b`'s level
  in bits `4b` to `4b+2` of nibble `b` (the nibble's top bit is zero). The
  whole map is one register on every profile. Beside it,
  `ProcessBody.level_cap`, the highest level the process may grant.
- **Root's map** applies verbatim from its image header, as spec §12 says:
  `start_process` reads the four bytes `ImageTable.prio_map` (bands 0 to 3)
  and gives `Rt0` to `Rt3` `High`'s level. A byte at or above
  `SYSTEM_LEVELS` stops the boot through `fatal_image`, naming the band and
  the level (Blade refuses past 7, so only a hand-assembled image reaches
  it). Root's `level_cap` is 7: root is init, as its unlimited quota rows
  are, and it is what grants full access to anyone.
- **A child's map** is the simple map until its launcher sets it, between
  the create and the start:

  ```
  ProcessOp.BandMap = 16          arg0: the packed map
  ProcessRight.BandMap = 1 << 23, in process_rights()
  ```

  Its answers, in the order they are decided, on the pattern of `QuotaSet`
  (`process_quota_set`): a handle without the right is `AccessDenied`; a
  process that is not `Created` (started, or `Gone`, which was always
  started) is the `BadState` fault; a nibble with its top bit set is the
  `BadArg` fault; a level above the caller's `level_cap` is the `BadArg`
  fault and sets nothing; otherwise the map is stored, the child's
  `level_cap` becomes the map's highest level, and the op answers `Ok`. It
  may be made more than once before the start; the last wins. The map is
  immutable from the start on, as spec says.
- **The cap: a launcher grants at most what it holds.** That is
  `QuotaSet`'s rule for rows ("above the caller's own limit ... is a
  `BadArg` fault"). A child given the simple map holds level 1 and can grant
  no higher; one given full access holds 7.
- **The child's header field is not read** (Q3, accepted). The kernel never
  parses an image for policy and a launcher never parses one at all (spec
  §12), so a request in a child's header has no run-time reader. The
  request lives in the declaration (§10).

### 3.4 How a thread gets its level

**The band rides `ThreadOp.Start`.** `ThreadCreate` has no free register and
`Start`'s `arg0` is unused, and a thread's level matters from the moment it
is runnable, which is the start.

- `ThreadOp.Start(band)`: a `band` at or above `BANDS` is the `BadArg` fault,
  decided after the right and before the state checks. The level is read
  from the map of the process that owns the thread (`THREADS[t].process`),
  not the caller's, stored in `ThreadSlot.base_level` and in
  `ThreadSlot.level` (both `UInt8`), and never read from the map again: the
  hot path is numeric, as spec says. `level` is the level the scheduler uses;
  it differs from `base_level` only while the thread is raised by a lock
  (§7).
- **The first thread** of a process runs at its `Normal` band: root's in
  `start_process`, a child's in `process_start`. `ProcessOp.Start` keeps its
  arguments.
- **The `sos` module.** `enum Priority: UInt8 { Background = 0, Low = 1,
  Normal = 2, High = 3, Rt0 = 4, Rt1 = 5, Rt2 = 6, Rt3 = 7 }`, the wire tags
  fixed. `Thread.start()` starts at `Normal`; `Thread.start(band: Priority)`
  names one. The floor keeps `sos_thread_start(handle)`, now passing
  `Normal`, and adds `sos_thread_start_band(handle, band)` for C.

### 3.5 How the radio's six levels fit

Under full access, from SO-103 §4.3 requirement 4.1, with the ceiling taken
out of the level count (§6 makes it a word):

```
level  band     radio thread              beside it
  7    Rt3      BT interrupt thread
  6    Rt2      Wi-Fi interrupt thread
  5    Rt1      BT controller task
  4    Rt0      timer task (callbacks)
  3    High     Wi-Fi task                a full service's interrupt thread
  2    Normal   serve thread              svc-net, svc-ble, the console
                                          driver: full, serving at Normal
  1    Normal   (simple map)              applications
  0    Background
```

Interrupt threads sit above every task and BT's above Wi-Fi's, as masking
ordered them (SO-103 §4.3 points 2 and 3). The services that serve the
applications take full access and serve at `Normal`, level 2, at or above
their clients (spec §7's convention), and equal to the radio's serve thread,
which is a server to them. Another driver's interrupt thread fits at `High`
beside the Wi-Fi task, round-robin with it; a device that needs more is a
case for the launcher's general map (§3.2), not a ninth level. If the
libraries prove to need two tasks at one level (R2's log), the adapter maps
both to one band.

---

## 4. The ready structure and pick-next

```
READY_HEAD: [Int; SYSTEM_LEVELS]   slot + 1, 0 = empty   (kcore.threads)
READY_TAIL: [Int; SYSTEM_LEVELS]
READY_BITS: UInt8                  bit l set iff level l's list is nonempty
HIGHEST:    [UInt8; 256]           the highest set bit of a byte, .rodata
```

- **Push** (`ready_push(slot)`): level `l = THREADS[slot].level`; append to
  list `l`; set bit `l`. **Push at the head** (`ready_push_head`), used for
  a preempted thread (§5.2) and for a thread the section displaced (§6.3).
- **Pick** (`ready_pop`): `l = HIGHEST[READY_BITS]`; pop list `l`'s head;
  clear bit `l` if the list is now empty. `ready_peek` is the same without
  the pop.
- **Move** (`ready_move(slot, level)`): unlink a ready thread from its list
  (a walk of that one list) and push it at the tail of another; used only
  when a lock raises or restores a ready thread (§7.3).
- **Remove** (`ready_remove_process`): the existing trailing-pointer walk,
  once per nonempty level, clearing a bit whose list it empties. Each
  thread is on at most one list, so the bound is unchanged.
- **Empty** is `READY_BITS == 0`; `idle_until_runnable` tests that instead
  of `READY_HEAD == 0`.

**The cost on the hot path.** A syscall that reschedules nothing pays
nothing new: `stays_running` is unchanged. A wake adds two indexed loads,
a bit set and the level compare of §5. A switch adds one load from
`HIGHEST` and a bit clear when a list empties. Per thread two bytes, which
fit the padding beside `ThreadSlot.state` and `refs`; per body two words.
Kernel statics: 2 × 8 words of list heads and the 256-byte table.

**Every profile is the same code.** The ready structure lives in
`kcore.threads`, which is arch-free, and no HAL seam is added. riscv32 and
riscv32-flat (`rv32imac`) have no count-leading-zeros instruction and Saw
has no intrinsic for one, so the table serves every profile rather than a
per-HAL `clz` seam that arm64 (`clz`) and the ESP32-S3 (`nsau`) could
answer and riscv32 could not. On the ESP32-S3 the table is in `.drom` with
the kernel's other constants (`esp32s3.ld`).

---

## 5. Preemption on wake

### 5.1 Where

**Every wake goes through `ready_push`, so the compare goes there.** After
the append: if a thread is running (`a_thread_is_running`, moved from
`irq.saw` to `threads.saw` so both can reach it) and the pushed thread's
level is above the running thread's, set `NEED_RESCHED`. Nothing else
changes: `ktrap`'s tail calls `pick_next`, which already acts on the flag,
and the frame it returns is the switch. So:

- a wake made by a syscall (an Event signal, a post, a reply, a `Start`, a
  `LockWake`) switches at that syscall's return, after its result pair is
  written;
- a wake made by an interrupt taken from user mode switches at that
  interrupt's return (`ktrap`'s interrupt arm returns `pick_next`);
- a wake made at a preemption point switches when the operation that
  polled returns (§5.3);
- a wake made while the kernel idles has no running thread to compare
  against, and the idle loop's exit picks the highest level.

Equal levels never preempt: a wake of an equal or lower thread raises
nothing, which is today's rule ("a wake never preempts") restricted to the
level, and spec's "same-priority never preempts mid-slice". The same compare
runs when a lock lowers the running thread's level below a ready one (§7.3).

The requeue of the outgoing thread inside `pick_next` must not raise the
flag; it runs with the thread already `Runnable`, so the "a thread is
running" test is false there. Joiners woken by `exit_thread` meet an exiting
thread, likewise not running.

### 5.2 Where a preempted thread goes

**To the head of its level; a thread whose slice ended or that yielded goes
to the tail** (Q7, accepted). `pick_next` tells the two apart without a
second flag: an outgoing thread that is still `Running` was preempted
exactly when the highest ready level is above its own (`ready_peek`). Head
placement keeps a preempted thread ahead of the peers it was ahead of,
which is POSIX's `SCHED_FIFO` rule, and it keeps the section's holder at
the front (§6.3). A tick and a wake in the same trap read as a preemption;
either answer is round-robin-fair, and this one needs no state.

### 5.3 What a wake waits for

The kernel runs with interrupts masked and switches only at the user-return
boundary (spec §9: "a point interrupts latency, not atomicity"). So a
higher thread woken at a preemption point inside a long operation runs when
that operation returns, not one `PREEMPT_STRIDE` later. SO-103 §4.3 point 6
bounds a line's latency by "a trap, the delivery funnel and one switch, plus
at most one `PREEMPT_STRIDE` of a long kernel copy"; the true bound is a
trap, the funnel, the rest of the longest kernel operation another thread
can be in, and one switch. The long ones are a launcher's (a create's
placement, a kill's or a death's teardown, a donation), which at steady
state do not run, and a radio's own calls copy at most a message body. The
latency record (§11) measures what the board sees; nothing here changes the
rule.

---

## 6. The priority ceiling: the section word

### 6.1 Why a word, not a level or a syscall pair

SO-103 offered two shapes: raise to a ceiling level C by a syscall pair, or a
deferred-preemption word. The word wins on three counts. It costs no trap on
entry and none on exit unless a sibling was held back, where the pair costs
two traps per section and the libraries take several sections per frame. It
needs no level: the holder borrows the level of whichever sibling it holds
back, so the radio's map has six levels, not seven, which eight levels
need. And it gives exactly what masking gave the radio with respect to its
own state, nothing more: other processes run on.

### 6.2 The word and its registration

The word is a pair of machine words, word-aligned, in the process's own
memory:

```
word 0  depth     written by the process: nonzero = the section is held
word 1  pending   written by the kernel: nonzero = a sibling was held back
```

`ProcessOp.SectionWord(addr)` (17) registers it, on the caller's own Process
handle:

- **Gated by `ProcessRight.ThreadCreate`** (Q6, accepted). The word orders a
  process's own threads, and only a process that can make threads has
  several to order; it grants nothing beyond what the map already granted
  (§6.6).
- **Its answers, in order:** a handle without the right is `AccessDenied`; a
  handle naming another process is the `NotSelf` fault; an address not
  word-aligned is the `BadAlign` fault; a pair that does not lie in one row
  placed with the process's image and permitting write is the `BadArg`
  fault (a row `map` installed can be unmapped, an image row lives as long
  as the body, so only an image row is taken); a second registration is the
  `BadState` fault. Otherwise the kernel resolves the pair once
  (`translate_user`, then `hal.phys_to_virt`), keeps the kernel address in
  `ProcessBody.section_at`, writes `pending` to zero and answers `Ok`.
- The resolved address is the only pointer into a process's memory the
  kernel keeps. It is safe because the row cannot go before the body does
  (`remove_grant` is reached only from `unmap`, which removes `map` rows),
  and the body's free clears `section_at`. Image rows are recorded first, so
  `remove_grant`'s compaction never moves one; the body records how many
  there are (`ProcessBody.image_rows`, set by `place_image`).

### 6.3 The rules

Write `P` for a process with a registered word, "held" for `depth != 0`.

1. **While P's section is held, no thread of P runs but its holder.**
2. **The holder** is the thread of P that was running when the kernel last
   found the section held as that thread left the processor, or the running
   thread itself while it runs (`ProcessBody.section_holder`). Only a
   running thread can set the depth, and while it is set no sibling runs, so
   the holder is never ambiguous.
3. **Where the scheduler would run another thread of P, it runs the holder
   instead and sets `pending`.** Two places, both in `pick_next` and its
   tail in `resume_after_death`:
   - the outgoing thread is still `Running`, is P's, and P's section is
     held: if the highest ready thread (`ready_peek`) is P's, the holder
     stays on the processor and `pick_next` returns its frame;
   - the thread popped is P's, P's section is held, and the holder is
     another thread: the popped thread goes back to the head of its level,
     the holder is unlinked from its own level's list (a walk of that one
     list) and runs.
   Either way the holder runs at the level of the sibling it displaced,
   against every other process, so it finishes its section ahead of
   whatever that sibling would have been ahead of. That is the immediate
   ceiling, applied only when a sibling is actually waiting.
4. **Other processes are untouched.** The section never keeps a thread of
   another process off the processor and never changes which other
   process's thread the pick chooses.
5. **When the kernel finds P's section not held** at either place, it
   clears `pending` and forgets the holder.
6. **Leaving.** The process decrements the depth, and if it reached zero and
   `pending` is nonzero, yields. The yield traps once, the kernel finds the
   section not held, clears `pending`, and the sibling it held back, being
   higher or ahead, runs.
7. **A thread that parks, or ends itself with `ThreadOp.Exit`, while its
   process's section is held faults the process**, `FaultReason.SectionHeld`
   (15), the line `SOS: process fault: parked inside its critical section`
   (Q5, accepted). A park is decided in `ktrap` after the dispatch
   (`result.blocked` with the caller `Blocked`), which covers a `LockWait`
   that parks (§7); the exit in `thread_op_decoded`'s `Exit` arm, before
   `exit_thread`. A section that cannot end would hold P's other threads
   forever with no diagnostic. FreeRTOS treats a block inside a critical
   section as a bug too.

**The race at the leave is benign.** The kernel writes `pending` only while
the process is in a trap, and the process only reads it. A trap between the
process clearing the depth and reading `pending` finds the section not held,
switches as it would have, and clears `pending`; the process then reads
zero and does not yield. A trap between the read and the yield does the
same, and the yield that follows is a spurious one: the thread goes to the
tail of its level, which round-robin allows.

**Nesting** is the depth's: an increment and a decrement, each a plain load
and store. A sibling can only interleave a holder's increment by running,
which rule 1 forbids once the depth is nonzero, and a sibling that runs
before then leaves the depth as it found it.

### 6.4 Against interrupts and the tick

- **An interrupt delivered to a thread of P** masks its line (spec §9) and
  wakes that thread, which is above the holder, so `ready_push` raises the
  flag and rule 3 keeps the holder running with `pending` set. The holder
  leaves, yields, and the interrupt thread runs: a handler waits for the
  critical section, as under masking. The line stays masked until the
  interrupt thread acks it, so nothing is lost.
- **The tick** ends the holder's slice as any other's. If the highest ready
  thread is P's, the holder keeps the processor (rule 3); if it is another
  process's at the holder's level, round-robin goes on and the holder waits
  at the tail of its level with the section held, to be put back in front of
  P's threads by rule 3 when one of them is picked.
- **The holder off the processor.** A thread of another process at a higher
  level preempts the holder as it would anything; the holder goes to the
  head of its level with the section held. When that thread blocks, whatever
  thread of P is picked, the holder runs in its place (rule 3, the second
  arm). That is the reviewer's requirement: the ceiling survives its holder
  being off the processor, because it is P's and not the processor's.

### 6.5 What a section does not give

- Sections in two processes are independent; the radio's state is the
  radio's.
- It is not a lock between processes and not a mutex with waiters: a sibling
  is not "waiting for" the section, it is simply not run.
- `_ints_off(mask)` (SO-103 §4.3 point 4) stays a per-line flag in the
  radio's adapter; the kernel adds nothing.

### 6.6 Abuse

**A process that never clears the word harms only itself.** The holder runs
at most at the level of the highest sibling it displaces, a level the
launcher already granted P, and a thread of P at that level could spin there
anyway. Other processes keep their round-robin at that level and preempt it
from above. So no limit is set: no budget, no timeout, no kill. A supervisor
that wants a wedged process gone kills it (spec §8), as for any wedge.

### 6.7 The `sos` side

The runtime (`sosrt`) owns the one pair, a static in its `.bss`, so a
program and its runtime share one section. `sos.Section` offers `enter()`
and `leave()`; the floor exports `sos_section_enter()` and
`sos_section_leave()` for C, which is what the radio's adapter calls from
`_wifi_int_disable`, `_global_intr_disable` and the coexistence lock.
Registration is `Process.section_register()` on the caller's own handle,
and the runtime makes it itself the first time a program makes a thread
through `thread_create(run:)` (§8.3). `enter()` on an unregistered word
panics, naming the registration, rather than entering a section that holds
nothing back.

### 6.8 Cost

A process with no word pays one compare per switch decision
(`section_at == 0`). A process with one pays one load of the depth at each
decision that involves it, and a store of `pending` when it defers. Entering
and leaving are two plain stores and a load; the yield is one trap and only
when a sibling was held back.

---

## 7. The inheritance lock

### 7.1 What it is for

A lock that is held across something long, or across a park, cannot be a
section: a section forbids the park (rule 7) and holds back every sibling
for its whole length. Such a lock needs waiters, and with waiters at
several levels it needs inheritance, or a middle thread preempts a low
holder while a high waiter waits behind it. Parking on an Event (revision
1's answer) ends the spin but not that inversion. Inside one process the
holder is known and the levels are the process's own, so the kernel can
raise the holder to its highest waiter's level and lower it when it gives
the lock back: priority inheritance, kept to one process.

### 7.2 The lock word and the two ops

A lock is two machine words, word-aligned, in the process's memory:

```
word 0  owner     0 = free; else the holder's own Thread handle word
word 1  waiters   written by the kernel: nonzero = a thread is parked on it
```

- **The owner is named by its own Thread handle**, the `Control` handle a
  thread holds for its life (`ThreadBlock.thread`, released by its exit).
  It is the one name for a thread that a process holds and the kernel can
  resolve, and its generation makes a stale one name nothing (spec §3).
- **A thread with no handle of its own** (a `_start` thread with no block,
  `tests/lock-wait`) takes the lock with an anonymous owner, a word whose
  index byte is zero, which no handle word has (the "names nothing" byte,
  `kernel/abi/src/types.saw`). A waiter that meets one cannot be raised
  against it and yields, as today.

**Uncontended, no syscall.** Taking is one compare-and-swap of `owner` from 0
to the caller's word; giving back is one compare-and-swap from the caller's
word to 0, then one load of `waiters`. Only a give that finds `waiters`
nonzero traps.

**`ThreadOp.LockWait(addr)` (4)**, on the caller's own Thread handle, gated
by `ThreadRight.Control` as `Yield` is. Its answers, in the order they are
decided:

1. a handle without `Control` is `AccessDenied`; a handle naming another
   thread is the `NotSelf` fault;
2. an `addr` not word-aligned is the `BadAlign` fault; a pair the copy doors
   refuse (§2.2's rule: one writable, non-device, non-executable row) is
   their fault;
3. `owner` is 0: the lock was given back between the caller's swap and its
   trap; the op answers `Ok` without parking, and the caller swaps again;
4. `owner` is the handle word the call was made through: the `BadState`
   fault (the lock is not recursive in the kernel; the runtime counts its
   own recursion);
5. `owner`'s index byte is zero (an anonymous holder): `WouldBlock`, without
   parking;
6. `owner` resolved in the caller's table names a Thread of another process,
   or anything but a Thread: the `BadArg` fault (the word is corrupt);
7. `owner` names nothing, or a thread that has exited or is invalid: the
   holder exited holding the lock; `Revoked`, without parking;
8. the chain from the holder (§7.4) would close a cycle back to the caller
   or pass `LOCK_CHAIN_MAX` (4) locks: the `LockChain` fault (16);
9. otherwise the kernel writes `waiters` nonzero, records the lock and the
   holder in the caller's slot (`ThreadSlot.lock_at`, the user address;
   `ThreadSlot.lock_holder`, the slot), parks the caller, and raises the
   chain (§7.3). If the caller's process's section is held, the park is
   §6.3 rule 7's `SectionHeld`.

A parked waiter is answered `Ok` by a `LockWake` (and swaps again), or
`Revoked` when its holder exits (§7.5).

**`ThreadOp.LockWake(addr)` (5)**, on the caller's own Thread handle, gated
by `Control`: the same handle and address answers as `LockWait`'s 1 and 2.
Then it wakes every thread of the caller's process parked on `addr` (a scan
of the thread slab, as the room park's resume scans, `notify_room`), each
answered `Ok`, writes `waiters` to zero, lowers the caller (§7.3), and
answers `Ok`. The woken threads go onto their levels' lists in the order
they parked, so the highest runs first and takes the lock with its swap;
the others meet it held, wait again, and raise the new holder. Waking every
waiter rather than handing the lock to one keeps the kernel from tracking a
holder it did not see take the lock, which a hand-off would have to, and
with the handful of threads a process has the extra swaps are few.

**The give and the wait cannot lose a wake.** The kernel reads `owner` and
parks in one trap with interrupts masked. A give that swapped `owner` to 0
before the waiter's trap makes the wait answer at step 3; a give whose load
of `waiters` comes after the wait's park sees it nonzero and wakes it. The
give swaps first and reads second, so there is no third order.

### 7.3 Raising and lowering

- **A thread's level** (`ThreadSlot.level`) is the highest of its
  `base_level` and the `level` of every thread parked with `lock_holder`
  naming it.
- **Raising** happens at the wait: the holder's `level` becomes at least the
  waiter's; if the holder is itself parked on a lock, its holder is raised
  the same way, and so on along the chain (§7.4). A raised thread that is on
  a ready list moves to its new level's list (`ready_move`); a raised
  running thread keeps the processor, and a ready one now above the running
  thread raises `NEED_RESCHED` by §5's compare.
- **Lowering** happens at the give (`LockWake`) and when a waiter leaves for
  any other reason (its process dies, its holder exits): the holder's
  `level` is recomputed from its base and its remaining waiters (a scan of
  the thread slab for `lock_holder` naming it), and the change runs down the
  chain. A lowered running thread below a ready one is preempted at the
  give's return.
- **Only the level changes.** `base_level`, the band, the map and the cap do
  not, and a raise never exceeds the highest waiter's level, which is a
  level of the same process.

### 7.4 Nested locks and chains

- **A thread holding several locks** is raised by the waiters of all of
  them; giving one back drops only that lock's waiters from the recompute.
  The kernel keeps no per-lock record: each waiter names its holder, and the
  recompute reads them.
- **A chain**: the holder of the lock a thread waits on is itself parked on
  another lock, held by a third. The wait walks it, raising each holder, and
  stops at a holder that is not parked on a lock. `LOCK_CHAIN_MAX` bounds
  the walk at four holders: the runtime nests no inheritance lock inside
  another (§8.3), and FreeRTOS code nests mutexes rarely and shallowly. A
  wait that would pass it is the `LockChain` fault, as is one whose walk
  meets the caller, which is a deadlock the process made; neither is a
  status, because the program could have avoided both. The bound makes a
  wait cost at most four holders' raises and a lowering at most four
  recomputes, each a scan of the thread slab.

### 7.5 A holder that exits, faults or is killed

- **It faults, or its process is killed or exits:** every thread of the
  process goes with it, waiters included. Nothing is left to answer.
- **It exits by `ThreadOp.Exit` holding a lock with waiters:** each thread
  parked with `lock_holder` naming it is woken with `Revoked` (and lowered
  out of any chain), in `exit_thread` beside the joiners. A later
  `LockWait` on the same lock meets an `owner` that names a released handle
  and answers `Revoked` too (step 7). The kernel does not fault the exit:
  it cannot see an uncontended lock, and a rule it could keep only for
  contended ones would be half a rule. The runtime turns `Revoked` into a
  panic naming the lock ("a thread exited holding the output lock"), so the
  error is not hidden.
- **It parks elsewhere while holding** (the output lock across a console
  `Call`): it stays raised while parked, and is woken onto its raised
  level's list, which is the point: the print's call returns and the printer
  runs above the middle thread.

### 7.6 Why inheritance here and not across pipes

Spec §7 refuses inheritance through pipes because the reply obligation is
transferable: the thread that will answer a call is not known at the call
and can change while the caller waits, so there is no holder to raise, and
raising whatever process holds the obligation now would let a client lift
another process's thread above the levels that process's launcher granted.
Inside one process neither holds. The holder is named in the lock word by a
handle its own process holds, the kernel checks that it is a thread of the
caller's process, and the raise is bounded by a waiter's level from the same
map, so a process can lift its own threads no higher than its launcher
already let it run them. Spec §7's rule stays as it is, for pipes.

### 7.7 The `sos` side

`sos.Lock` (Saw) and `sos_lock_take(lock)` / `sos_lock_give(lock)` (the C
floor) over the two words and the two ops, through the calling thread's own
handle from its block. The runtime's output lock is one (§8.3). The radio's
adapter implements FreeRTOS mutexes with it: FreeRTOS's mutexes inherit
priority, and the adapter's `mutex_lock` then behaves as the libraries
expect.

### 7.8 Cost

Uncontended: two swaps and a load, no syscall, and no kernel state. A wait:
one trap, a resolve in the caller's table, a walk of at most four holders.
A contended give: one trap, a scan of the thread slab, a recompute along at
most four holders. Per thread: `base_level`, `lock_at`, `lock_holder`.

### 7.9 Section or inheritance lock, which for what

```
                       section (ceiling)           inheritance lock
a waiter's cost        none: it is not run         a trap to park, one to wake
uncontended cost       two stores, a load          two swaps, a load
who is held back       every sibling, whether or   only threads that want this
                       not it wants the lock       lock
may the holder park    no (SectionHeld)            yes, and stays raised
nests inside a         yes, a depth                no: a wait inside a section
  section                                          is SectionHeld
inheritance            the holder takes the        the holder rises to its
                       highest waiting sibling's   highest waiter's level
                       place
```

- **The heaps and the arena take the section.** Their windows are short and
  never park (`rt_alloc`, `rt_dealloc`, a heap's growth through `extend` and
  `map`, which answer without parking). The radio's libraries allocate from
  inside their own critical sections (IDF's heap takes its own critical
  section, a `portMUX` spinlock, for the same reason), and a section nests
  inside a section at the cost of a depth. An inheritance lock would make
  that a `SectionHeld` fault whenever another sibling held the heap.
- **The output lock takes the inheritance lock.** A print is long, and with
  a console record it calls the driver, which parks when the ring is full,
  so it cannot be a section at all; and a section around a print would hold
  the radio's interrupt threads back for the whole print. With the
  inheritance lock only a thread that prints waits behind a print, and the
  printer runs at the highest waiter's level, so a middle thread cannot
  starve it.
- **The rule for programs:** a section for short state that never parks, an
  inheritance lock for anything else. A print inside a section is legal
  while the output lock is free, and `SectionHeld` when it is not, since the
  wait would park; the radio's adapter logs from inside critical sections
  through the debug print, not through `print`, and interrupt threads do not
  print.

---

## 8. Compatibility

### 8.1 One level is today

At one level the new structure is the old one: one list, appended on a wake
and on a slice's end or a yield, popped from the head; no preemption,
because preemption needs a higher level; and `ready_remove_process`'s walk
preserves the order of the threads it leaves. The head placement of §5.2
never happens. A lock raises nobody, because every waiter's level is the
holder's.

### 8.2 Every existing case is at one level

- Every manifest in the tree states the default priorities, so root's
  `Normal` is level 1 and its first thread runs there.
- A child no launcher maps has the simple map, and its first thread starts
  at `Normal`, level 1.
- Every thread started through `Thread.start()` or `sos_thread_start` starts
  at `Normal`. The two wrappers change from passing 0 to passing `Normal`
  in the same unit that makes the kernel read the band; without that change
  every made thread would run at `Background`, level 0. U1 checks that no
  hand-assembled payload under `tests/<arch>/` starts a thread.
- The harness kernels that enter user mode with no Thread object have no
  current thread and never reach the ready lists.

So the cases that order by the FIFO hold unchanged: `thread_preempt`,
`preempt_tick`, `timer_masked_in_kernel`, `thread_basics`, `thread_run`
(`tests/threadrun`), `lock_owner`, `lock_wait`, `kill_runnable` and every
case whose comment says "a yield hands the processor to every thread ready
before it". **No case's expected text changes.** A launcher that starts a
child mapped above itself is now preempted at the start's return; no
existing case maps one.

### 8.3 The runtime's locks (U5)

- **The arena and the heaps** run each `SpinLock` window inside the section
  (§7.9). A sibling is not run while one is open, so there is no
  in-process contention left to spin on; the spin is reached only at one
  level, where a tick moves it, as today.
- **The output lock** becomes an inheritance lock: the owner word is the
  holder's own handle (or the anonymous word for a thread with no block),
  the recursion depth stays the runtime's and is touched only by the holder,
  so the `SpinLock` around today's owner-and-depth pair goes. A waiter with
  a block parks (`LockWait`); one that meets an anonymous holder, or has no
  block itself, yields as today (`tests/lock-wait` keeps its transcript).
  The panic sink takes the lock and never gives it back, as today. A
  `Revoked` wait panics, naming the output lock.
- **Registration.** The runtime registers its section word the first time
  the program makes a thread through `thread_create(run:)`. A
  single-threaded program makes no extra syscall, so no `Stats` reading in
  any case moves. A program that makes threads another way calls
  `Process.section_register()` itself; without it the heap windows run
  unguarded, and the runtime's comment keeps saying why.
- **At one level** the visible order of the output-lock cases is today's:
  in `threadrun`'s `main_holds`, the waiter parks where it yielded before,
  the main thread finishes its line, its give wakes the waiter onto the tail
  of level 1, and the main thread's `join` lets it run, which is where its
  yield used to bring it. U5 runs `thread_run`, `lock_owner` and
  `lock_wait` and holds their transcripts unchanged.
- **The runtime's needs** for SO-105's `[ceilings]` do not change: the
  section's registration spends `ThreadCreate`, which `thread_create(run:)`
  already needs, and the lock ops spend thread `Control`, which a `main`
  program's declared minimum already holds.

Until U5 lands, a process's threads at two levels must not contend on the
runtime's locks; the scheduler's own cases are written so they never do
(§14.1).

---

## 9. The tick, idle, deadlock and `Stats`

- **The tick** is unchanged: one-tick slices, charged to the running thread,
  re-armed from now (`on_timer_interrupt`). A slice's end requeues the
  thread at the tail of its own level (its raised level, while a lock
  raises it), and the pick takes the highest level, which is the thread
  again when it is alone there. Lower levels wait for higher ones to block:
  strict priority, no aging.
- **Idle** is an empty bitmap; the loop and its re-test are
  `idle_until_runnable`'s, on `READY_BITS`.
- **Deadlock** is unchanged: nothing ready at any level and no bound line or
  armed Timer. A thread starved by a higher spinner is runnable, so starving
  is not deadlock and is not reported. Threads parked on each other's locks
  in a cycle never get that far: the wait that would close the cycle is the
  `LockChain` fault (§7.4).
- **`Stats` gains no column** (Q10, accepted). A count of preemptions mixes
  causes the program determines with causes the machine determines, which
  is the single-total problem §2.7 splits the trap columns to avoid.
- **Which parked thread a readiness feeds** is unchanged: the Waiter's
  blocked list is fed from its head, the thread that parked last, whatever
  its level (`block_on_wait`, `wake_one_waiter`), and the room park's
  resume scans by slot (`notify_room`). Neither is level-ordered and spec
  §2.2 promises no order. The radio parks each thread on its own Waiter
  (SO-103 §4.3 point 5), so it needs none.

---

## 10. The image, the declaration, the builder and the stock root

### 10.1 Where a child's request lives

**In its declaration, `process.toml`** (Q3, accepted), as one key:

```toml
bands = "full"        # or "simple", the default when the key is absent
```

The value is a mode, not a map, so it does not reuse `[sos] priorities`, the
name Blade's manifest gives a map (one name for one kind of thing). The
header field stays root's band map.

The image grants what the declaration asks: an image description has no key
that changes a process's mode. A launcher policy that withholds full access
is a later need; the kernel's general op already allows it (§3.2).

### 10.2 What the builder refuses, by name

In `tools/sosimage/src/declaration.saw`, and so in `check-tree`:

- a `bands` value other than `simple` or `full`;
- a child package whose built header map is not the default, since a child's
  header is not read: `Saw.toml [sos] priorities is root's band map; a
  child asks for bands in its process.toml`;
- in a stock-root plan, a client whose `Normal` level is above its server's
  (a full-access client of a simple-map server), the convention that stands
  in for inheritance across pipes; a hand-written root is its own policy and
  is not checked.

### 10.3 The rights table

SO-105's builder table of every right (`tools/sosimage/src/rights.saw`, held
to `kernel/abi/src/rights.saw` by a builder test) gains `band-map` on the
`process` kind, so a declaration's `[ceilings]` may name it; a child that
does not launch has no use for it and need not.

### 10.4 The plan and the stock root

`bootplan.Child` gains `bands: UInt32`, the packed map the mode gives, and
`CHILD_WORDS` changes with it. The stock root sets it with
`child.band_map(c.bands)` after the quota rows, before SO-105's `restrict`
step and the gives, for a child whose map is not the simple one; a step
that fails stops the machine as every other step does
(`root: bands NAME: STATUS`). A full-access child is granted from root's
cap, which is the top.

`SOS_IMAGE_BUILDER=python` refuses a case whose plan grants full access, by
name, as it refuses the other kinds the builder alone builds.

---

## 11. The latency record (requirement 4.2)

**Always built, read by the line's holder** (Q9, accepted). The record costs
two clock reads per delivered fire and one compare per trap.

- **What it measures:** for a bound line, from the kernel's claim of a fire
  (`deliver_line`, stamping `InterruptSlot.fired_at` with
  `hal.timer_now_ns()`, the SYSTIMER on the ESP32-S3) to the first return
  to user mode of the thread the fire is delivered to, parked and woken by
  it or making the `Wait` that finds it pending (`deliver_attachment` for an
  Interrupt marks the thread, `ThreadSlot.latency_irq`; `ktrap`'s tail and
  `resume_after_death` stamp and clear it). That is trap, funnel, the rest of
  any kernel operation, the wait at the thread's level and the switch.
- **What it keeps,** per Interrupt: `fires` and `worst_ns` since the last
  read.
- **How it is read:** `InterruptOp.Latency(record)` (1), on
  `InterruptRight.Wait`, answering through a two-doubleword copy-out record
  (`fires`, `worst_ns`) and resetting both, so R3 reads one window per read.
  A refused record is the copy door's fault, as for every record.

---

## 12. Spec text that changes

### 12.1 §7, the first paragraph and the not-built block

Replace "**What is built.** The kernel schedules one round-robin FIFO — no
levels, no ready bitmap — on a one-tick slice." with:

> **What is built.** The kernel schedules by fixed priority over 8 system
> levels, 0 the lowest, round-robin within a level on a one-tick slice.

Replace "**Not built: priorities.** ... The rest of this section specifies
the scheduler that is not yet built." and its first four bullets (from
"Fixed-priority preemptive, 8 system levels" through "The map comes from
build-time metadata + launcher policy") with:

> - **A ready list per level and a one-byte ready bitmap.** Pick-next takes
>   the head of the highest nonempty level: a table load and a pop, the
>   same code on every profile. Idle is not a level: the idle loop runs
>   when the bitmap is empty.
> - **Round-robin within a level.** A thread whose slice ends or that
>   yields goes to the tail of its level. A thread preempted by a higher one
>   goes to the head of its level, so it resumes ahead of the peers it was
>   ahead of.
> - **Preemption on wake.** Readying a thread above the running thread's
>   level switches to it on the way out of the trap that readied it: at the
>   syscall's return, at the interrupt's return, or, for a wake made at a
>   preemption point, when the operation that polled returns. A wake at or
>   below the running level never preempts.
> - **Bands, not levels.** A process names its threads' priorities by band,
>   `enum Priority { Background, Low, Normal, High, Rt0, Rt1, Rt2, Rt3 }` in
>   the `sos` module, wire tags 0 to 7. `ThreadOp.Start` carries the band; a
>   tag past 7 is the `BadArg` fault. A process's first thread runs at
>   `Normal`. No syscall accepts a system level from the process that will
>   run at it, and no op tells a process its map.
> - **A band map per process, in the kernel.** Each process holds a map from
>   its eight bands to system levels, one nibble each in one word. A thread's
>   level is read from the map of the process it runs in when it is started
>   and stored plain. The simple map, every process's until its launcher
>   says otherwise, puts `Background` at 0 and every other band at 1; full
>   access puts band `b` at level `b`. Root's map is its image header's,
>   `Rt0` to `Rt3` at `High`'s level, and a level past 7 stops the boot. A
>   launcher sets a child's map with `ProcessOp.BandMap(map)`, on
>   `ProcessRight.BandMap`, between the create and the start: a child that
>   is not `Created` is the `BadState` fault, and a level above the
>   caller's own highest is the `BadArg` fault (root's highest is 7). The
>   map is immutable from the start.
> - **The request is the declaration's, the decision the launcher's.** A
>   child asks for full access in its declaration (`process.toml`'s
>   `bands = "full"`); the builder writes the map into the stock root's
>   plan, and the stock root sets it. A launcher never reads an image (§12),
>   so the kernel reads no child's header field, and the builder refuses a
>   child whose header states a map.
> - **The section word.** A process may register one pair of words in its
>   own memory with `ProcessOp.SectionWord(addr)` on
>   `ProcessRight.ThreadCreate`, through its own handle: a depth it writes
>   and a pending flag the kernel writes. While the depth is nonzero no
>   thread of the process runs but the holder, the one that was running when
>   the kernel last found it nonzero. Where the scheduler would run another
>   thread of the process it runs the holder instead, at that thread's level,
>   and sets the pending flag; the other thread keeps its place at the head
>   of its level. A process leaves by clearing the depth and, if the flag is
>   set, yielding; the kernel clears the flag when it finds the depth zero.
>   The section never holds back another process's thread. A thread that
>   parks, or exits with `ThreadOp.Exit`, while its process's depth is
>   nonzero faults the process (`SectionHeld`). The pair must be
>   word-aligned and lie in one writable row placed with the image; a
>   second registration is the `BadState` fault. It costs no trap to enter
>   or leave, and one trap to leave when a sibling was held back. It grants
>   nothing: the holder runs at most at a level the launcher gave the
>   process, so a process that never clears it starves only itself.
> - **Inheritance inside a process: the lock.** A lock is two words in a
>   process's memory: the holder's own Thread handle word, or 0, and a
>   waiters flag the kernel writes. Taking and giving back a free lock are
>   compare-and-swaps the process makes, with no syscall. A thread that
>   finds the lock held parks with `ThreadOp.LockWait(addr)` on its own
>   handle; the kernel resolves the holder in the caller's table and raises
>   it, and the holder of any lock it in turn waits on, to the waiter's level
>   until it gives the lock back. A holder that finds the flag set gives back
>   with `ThreadOp.LockWake(addr)`, which wakes every waiter and lowers the
>   holder to the highest of its base level and its remaining waiters'. A
>   free lock answers a wait at once; a holder that is the caller is the
>   `BadState` fault; an owner word that names a thread of another process,
>   or no thread, is the `BadArg` fault; a holder that has exited answers
>   `Revoked`, and so does every waiter parked on it when it exits; a wait
>   that would close a cycle of waits or pass four holders is the
>   `LockChain` fault. A thread is never raised above a level its own
>   process's map gives.

Keep "**Launching is a capability.**", "**The map is immutable after
creation.**" (with "each process sees only its 4 named bands" made "its
eight bands"), and "**Uniprocessor kernel in v1**" as they are. Replace
"**No priority inheritance.**" with:

> - **No priority inheritance across pipes.** Inheritance chains through
>   transferable PipeReplyHandles are ill-defined (the reply obligation
>   migrates, and the thread that will answer may be in any process). The
>   mitigations: (a) the convention that servers run at ≥ the max priority
>   of their clients (the launcher assigns both, and the image builder
>   refuses a stock-root image that breaks it); (b) preemption on wake,
>   which switches straight to a receiver a post or a reply readies above
>   the sender's level. Inside one process the lock above inherits, because
>   there the holder is named and the levels are the process's own.
>   **Not built:** the direct switch at an equal level, which would run such
>   a receiver ahead of its level's list.

### 12.2 §8

In "**Two phases, inert until started.**", after "`ProcessOp.Start` ... mints
the first thread at that recorded entry and makes it runnable": "at the
child's `Normal` band (§7)". In "**What is built.**", after "Thread
`Start`/`Join`/`Exit`/`Yield`": "; `Start` carries the thread's band (§7);
`LockWait` and `LockWake` are the in-process lock (§7)".

### 12.3 §2.7

Add `BandMap` and `SectionWord` to the op list and `BandMap` to the rights
list beside `QuotaSet`, with a paragraph:

> `BandMap(map)` sets the band map of a `Created` process (§7), on
> `ProcessRight.BandMap`. `SectionWord(addr)` registers the caller's section
> word (§7), on `ProcessRight.ThreadCreate` and only through the caller's
> own handle.

### 12.4 §9

After "**The wait answer carries the line**", add:

> - **Each line keeps its latency.** The kernel stamps each fire's claim
>   and the first return to user mode of the thread the fire is delivered
>   to, and keeps the count and the worst interval since the last read.
>   `InterruptOp.Latency` on `InterruptRight.Wait` answers both through a
>   copy-out record and resets them. A thread at a lower level than the one
>   running when the line fires waits for it, and the interval says so.

### 12.5 §11

Drop "**Open pin:** the §7 priority map ..." and "**Priorities and the band
map** (§7)" from the not-built list; add "**The direct switch** at an equal
level (§7)". In "**The plan.**" drop "priorities and the §7 band map".

### 12.6 §12

In the boot handle set: "Root's band map applies verbatim from its image
(§7, not built)" becomes "Root's band map applies verbatim from its image,
its highest level 7 (§7)". In "**The stock root's plan.**", "its quota
rows" becomes "its quota rows, its band map". In "**The launch
convention**", after "It sets the declared quota rows before any give": "and
the band map its declaration's `bands` gives, when that is not the simple
map".

### 12.7 The numbers

```
ProcessOp.BandMap       16      ProcessRight.BandMap    1 << 23
ProcessOp.SectionWord   17      FaultReason.SectionHeld 15
ThreadOp.LockWait        4      FaultReason.LockChain   16
ThreadOp.LockWake        5      InterruptOp.Latency      1
```

`ThreadOp`'s `static_assert` that every op lies below `PANIC_OP` gains the
two new ops.

---

## 13. `CLAUDE.md` text that changes

In the Testing section, replace "A wake never preempts the running thread
(only a tick, a yield, a block and a thread's exit request a reschedule)."
with:

> A wake preempts the running thread only when the woken thread's level is
> above it; at one level a wake never preempts (only a tick, a yield, a block
> and a thread's exit request a reschedule).

and "The ready list is one FIFO, so a yield returns once ..." with:

> There is one ready list per level, and every process is at one level
> unless its map says otherwise (every case's is, but the priority cases').
> At one level a yield returns once every thread that was runnable at the
> yield has run until it blocked, yielded or exited; a thread woken in those
> turns runs after the yielder, and one that yields hands back early. A
> thread at a lower level does not run while one above it is ready, so a
> yield never reaches it.

---

## 14. Tests

Every case is a gate case on riscv32, arm64 and riscv32-flat (none asserts a
hardware denial, so the flat profile excludes none), and the ones marked
**S3** join the ESP32-S3 smoke with a `[sos.xtensa-esp-unknown-elf]`
section. Every ordering is made true by construction: an image with the tick
off unless the rule is about the tick, a thread that cannot proceed until the
other side has done something observable, and no wait on the clock. Each
line names what goes red when the check is removed (R) or inverted (I).

### 14.1 Construction notes

- Root's map comes from its manifest; the cases' roots state
  `priorities = { background = 0, low = 1, normal = 2, high = 3 }`, so root's
  main thread is at level 2 with room below and above. A case that needs
  more levels in one process launches a child and grants it full access, or
  a map of its own, with `band_map`.
- Until U5, a case never lets a lower thread be preempted while it holds the
  output lock: a lower thread prints only when no higher thread can be woken
  before its print returns.
- A case prints inside a section only through the debug print, which never
  parks; a console record's call can park, which is `SectionHeld`.
- A spin "until the interrupts column has risen" reads `ProcessOp.Stats`
  before and in the loop: the kernel counts the tick that ended the slice,
  so the spin ends on a count the kernel keeps, and a stalled host only
  delays it.
- The lock cases use `sos.Lock` on a lock of their own, not the output lock,
  until §14.5.
- "Order" means the console lines' order; nothing prints a time.

### 14.2 Levels (U1)

```
case                     construction and expected      red when
level_start_preempts S3  main prints A, starts a thread  R: ready_push's
                         at High that prints B and       compare gone, B
                         exits, prints C: A B C          after C
level_lower_waits S3     main starts a thread at Low,    R: pick ignores
                         yields, prints Y, joins it;     levels (FIFO): L
                         the thread prints L: Y L        before Y
level_equal_no_preempt   main starts a thread at Normal  I: >= in the compare:
                         that prints B, prints A, joins: B before A
                         A B
level_preempted_head     main starts P at Normal (ready, I: preempted to the
                         not run), starts H at High,     tail: P before M
                         which prints H and exits; main
                         prints M, joins P, P prints P:
                         H M P
level_wake_event         H at High parks on a Waiter;    R: no preempt on a
                         main signals its Event and      signal's wake
                         prints A: H's line, then A
level_wake_irq S3        tick off; H at High parks on a  R: no preempt on an
                         one-shot Timer's Waiter; main   interrupt's wake:
                         spins on a word H sets, then    main spins to the
                         prints M: H's line, then M      harness timeout
level_tick_same_level    tick on; two threads at Normal  R: the tick's
                         each spin until the other has   requeue lost
                         printed (thread_preempt's
                         shape, at level 2)
level_tick_not_down      tick on; main at Normal starts  I: a slice's end
                         a Low thread, spins until its   picks across
                         interrupts column has risen     levels: L prints
                         (a tick ended its slice), then  before S
                         prints S and joins it; the
                         thread prints L: S L
level_owner_map          root (Normal 2, High 2) maps a  I: resolved through
                         child High to 3, starts a       the caller's map:
                         thread in it at High, prints R: no preempt, R first
                         the child's line, then R
level_simple_map         a child no one mapped starts a  R: the simple map
                         thread at Rt3, yields, prints   gone (Rt3 at 7): T
                         Y, joins it: Y, then the        before Y
                         thread's T (Rt3 is level 1)
level_full_map S3        root grants a child full        R: the identity
                         access; the child's main        map wrong: an
                         starts threads at Rt0 to Rt3    order out of rank
                         in rising order, each preempts
                         the last: Rt0 Rt1 Rt2 Rt3
                         start lines, each thread's
                         line at once
level_root_header        hand-assembled root image with  R: the boot check
                         a band at 8: fatal_image line   gone
                         naming band and level
level_kill_two_levels    kill a child with ready         R: the per-level
                         threads at two levels; root's   walk misses a
                         own two ready threads keep      level: a dead
                         their order: R1 R2              thread runs or the
                                                         bitmap lies
```

Refusals, one case each, each the fault line and teardown the dispatch
order gives (§3.3, §3.4): `band_map_started` (`BadState`),
`band_map_gone` (`BadState`), `band_map_denied` (`AccessDenied`, a handle
minted without the right), `band_map_nibble` (`BadArg`, a nibble of 8),
`band_map_above_cap` (`BadArg`: a child launcher given the simple map maps
its own child at 2; an inverted cap check passes it), `start_band_past`
(`BadArg`, band 8), and `band_map_twice` (the last map wins: the second
map's level is the one the order shows).

### 14.3 The section (U2)

```
case                       construction and expected     red when
section_defers_wake S3     main registers, enters,       R: rule 3's first
                           starts H at High: no switch;  arm: H first
                           main prints "held pending=1",
                           leaves (yields): H prints
section_pending_cleared    as above; after H exits,      R: rule 5: pending
                           main prints pending=0         still 1
section_other_process      root enters its section and   I: the section
                           starts a child's thread at a  holds back every
                           higher level: the child's     process: R first
                           line, then root's R
section_holder_away S3     tick off. P's holder A        R: rule 3's second
                           (Normal) enters and signals   arm: P's High
                           Q's high thread, which        thread runs before
                           preempts A, signals P's High  A's line
                           thread's Event and parks: A
                           prints "resumed held", leaves,
                           then P's High thread prints
section_tick_sibling       tick on. Holder at Normal     R: the tick bypasses
                           with a Normal sibling ready   the section: S
                           spins until its interrupts    before H
                           column has risen, prints H,
                           leaves; sibling prints S
section_irq_deferred S3    tick off. H at High parks on  R: rule 3: H first;
                           a one-shot Timer; main enters R: pending never
                           and spins until pending       set: main spins to
                           reads 1, prints D, leaves:    the timeout
                           D, then H
section_confined           tick on. A child at level 1   I: a held section
                           holds its section and spins   stops everyone:
                           forever; root at level 1      root never runs
                           waits for its "held" Event,   (timeout)
                           kills it, prints K
section_park_faults        a child enters and waits on   R: the park check:
                           an unsignalled Event: fault   a deadlock line
                           line SectionHeld; root reads  instead
                           Faulted, code 15
section_exit_faults        a child's second thread       R: the exit check
                           enters and exits: SectionHeld
section_nested             enter, enter, start H, leave: I: held read as
                           no yield, main prints N;      depth == 1: H runs
                           leave: H runs: N, then H      at the start
```

Registration refusals, one case each: `section_map_row` (`BadArg`, the
pair in a `map` row), `section_unaligned` (`BadAlign`), `section_twice`
(`BadState`), `section_not_self` (`NotSelf`), `section_denied`
(`AccessDenied`, a handle without `ThreadCreate`), and
`section_unregistered` (`Section.enter()` panics naming the registration).

### 14.4 The inheritance lock (U3)

Each case is a child that root gives a map of its own with `band_map`:
`Normal` at 7, so the main thread is above every worker and nothing it
starts or signals preempts it; `Background` at 0 for a driver thread; and
`Low` 1, `High` 3, `Rt0` 4, `Rt1` 5 for the workers. The driver steps the
workers: whatever it starts or signals runs until it parks, and the driver
runs again only then. When the scene is set the driver signals the main
thread, which readies the threads whose order is the check in one turn
(signals that cannot preempt it) and parks, so the pick sees them all at
once.

```
case                     construction and expected      red when
lock_inherit S3          tick off. Main starts L (Low), R: no raise: M
                         which takes the lock and       prints before L
                         signals main; main starts M    releases
                         (High), which prints M, and H
                         (Rt0), which waits on the
                         lock, and joins H. L prints
                         "L releases" and gives back;
                         H prints; M prints; L prints
                         "L after": L releases, H, M,
                         L after
lock_restore             as lock_inherit; the order of  R: no lowering at
                         H and "L after" is the check:  the give: L after
                         H first                        before H
lock_chain               the driver sets L holding A    R: no walk: N
                         and parked on an Event, M      (Rt0) prints
                         (High) holding B and waiting   before L releases
                         on A, H (Rt1) waiting on B;
                         main readies L and N (Rt0):
                         "L releases" before N's line
lock_two_locks           the driver sets L holding A    I: lowered to base
                         and B, parked; M (Rt0, 4)      at the first give:
                         waiting on A, H (Rt1, 5) on    X before "A given"
                         B; main readies L and X
                         (High, 3). L gives B back: H
                         runs; L, still raised to 4,
                         runs before X: "B given", H,
                         "A given", M, X
lock_free_wait           LockWait on a lock whose owner R: step 3: the
                         is 0: Ok at once, the syscalls wait parks on a
                         column moves by one            free lock to the
                                                        timeout
lock_holder_exits        L takes the lock and exits     R: exit_thread's
                         with H parked on it: H reads   wake: H parked to
                         Revoked; a later wait on the   the timeout
                         same lock reads Revoked
lock_cycle               A holds X waits Y, B holds Y   R: the walk's cycle
                         waits X: LockChain fault line  test: a deadlock
                                                        line instead
lock_chain_bound         a chain of five holders:       R: the bound
                         LockChain
lock_in_section          a thread enters its section    R: the park check
                         and meets the lock held:       for LockWait
                         SectionHeld
lock_uncontended         take and give 100 times; the   R: a syscall on
                         syscalls column moves by 0     the uncontended
                                                        path
lock_anon_owner          an owner word with a zero      R: step 5: BadArg
                         index byte: WouldBlock         or a park
```

Refusals, one case each: `lock_relock` (`BadState`), `lock_foreign_owner`
(`BadArg`, the owner a handle onto a child's thread), `lock_not_self`
(`NotSelf`), `lock_denied` (`AccessDenied`, a thread handle without
`Control`), `lock_unaligned` (`BadAlign`).

### 14.5 The runtime's locks (U5)

```
case                       construction and expected     red when
rt_arena_two_levels        a Low thread allocates in a   R: the arena window
                           loop; a Timer wakes a High    outside the section:
                           thread that allocates too;    High spins to the
                           tick off: both finish, totals timeout
                           printed
rt_output_inherit S3       tick off. A Low thread prints R: the output lock
                           through the console record    not an inheritance
                           with the ring kept full; a    lock: M spins and L
                           Normal thread M spins on a    never finishes
                           word; a High thread woken by  (timeout)
                           the driver prints and then
                           sets M's word: L's line, H's
                           line, M's line
rt_register_lazy           a program reads its syscalls  R: registration at
                           column around its first and   start-up: no +1 on
                           its second                    the first; I: every
                           `thread_create(run:)`: the    create registers:
                           first costs one call more     the second's
                           than the second               BadState fault
```

And `thread_run`, `lock_owner` and `lock_wait` keep their transcripts.

### 14.6 The builder and the stock root (U4)

Builder tests (`tools/sosimage/tests/`), each a refusal by name: a `bands`
value other than `simple` or `full`, a child with a non-default header map,
a full-access client of a simple-map server; and the bytes: `full` planned
as the identity map, `simple` as no `band_map` step. A gate case
`stock_full_bands`: an image description whose process declares
`bands = "full"` and starts an `Rt0` thread from its main: the thread's line
before its main's next line (R: root never calls `band_map`, the simple map
puts both at 1). The `check-tree` row: the tree's declarations read clean,
and `band-map` is in the rights table.

### 14.7 The latency record (U6)

The line is a UART's transmitter-empty source, which asserts as soon as it
is enabled with the transmitter empty (ns16550 `IER.ETBEI`, PL011 `TXIM`,
the S3 UART's `TXFIFO_EMPTY`), so each enable is exactly one fire; U6
confirms that on each emulator before a case relies on it.

```
case                     construction and expected      red when
latency_counts S3        enable, wait, ack, disable, 4  R: the stamp: fires
                         times; read: fires=4 and       0; R: the reset:
                         worst nonzero; read again:     second read fires=4
                         fires=0
latency_pending          fire while the driver is busy, R: the mark at a
                         then Wait finds it pending:    Wait that finds it
                         fires=1                        pending: fires 0
latency_denied           a handle without Wait:         R: the gate
                         AccessDenied
```

No case asserts a latency's value. "Worst nonzero" holds in the passing
direction alone: the interval contains at least a trap and a switch, more
than the coarsest clock's tick on any profile, and a stall only lengthens
it.

---

## 15. Units of work

```
U1  M  Opus    Levels: the ready structure and pick, preemption on wake,
               head and tail, ThreadOp.Start's band, ProcessOp.BandMap and
               its right, the simple map, root's header map and its boot
               check, `Priority`, `start(band:)`, the floor; spec §7, §8,
               §2.7, §11, §12 text; CLAUDE.md; §14.2. First.
U2  M  Opus    The section word: the op, rules 1 to 7, SectionHeld,
               `sos.Section` and the floor; spec text; §14.3. After U1.
U3  M  Opus    The inheritance lock: LockWait and LockWake, base and raised
               levels, the chain and its bound, the exit's Revoked,
               `ready_move`, `sos.Lock` and the floor; spec text; §14.4.
               After U2 (its park meets the section's rule 7).
U4  M  Sonnet  The builder and the stock root: `bands` in process.toml,
               the refusals, `band-map` in the rights table, `Child.bands`,
               root's `band_map` step; §14.6. After U1, beside U2.
U5  S  Sonnet  The runtime's locks: the heap and arena windows in the
               section, the output lock on `sos.Lock`, lazy registration;
               §14.5. After U3.
U6  S  Sonnet  The latency record; §14.7. After U1, beside U3.
U7  S  Opus    Later, not now: the direct switch at an equal level, with
               the audit of call-ordered cases.
```

At most two run at once: U1 alone, then U2 with U4, then U3 with U6, then
U5. U1 to U3 are Opus because each writes spec contracts; the rest follow
them. SO-103's R2c (the adapter's interrupt threads, its ceiling and its
mutexes) needs U1 to U3; its R4 and B-1 need U1 to U5. U4 lands after
SO-105's ceilings unit, whose rights table and `restrict` step it extends.

---

## 16. Questions for the owner

Q3 to Q10 of revision 1 stand as accepted. New:

1. **`bands = "simple" | "full"` in `process.toml`, a mode, rather than a
   map under the `priorities` key?** Recommended: yes; `priorities` is the
   name of a map in Blade's manifest, and this value is not one.
2. **The image grants the declaration's mode as asked, with no key to
   withhold full access?** Recommended: yes, for now; the kernel's general
   `BandMap` already lets a launcher withhold it when a launcher needs to.
3. **The kernel's `BandMap` stays general (any map within the caller's cap)
   while the builder writes only the simple map or the identity?**
   Recommended: yes; hand-written launchers and the cases need other maps,
   and the cap keeps it from granting more than the launcher holds.
4. **`LockWake` wakes every waiter, rather than handing the lock to the
   highest?** Recommended: yes; the highest still runs first, and the kernel
   need not track a holder it did not see take the lock.
5. **A holder that exits holding a lock answers its waiters `Revoked`, and
   the runtime panics on it, rather than the kernel faulting the exit?**
   Recommended: yes; the kernel sees only contended locks, and a fault kept
   for those alone would be half a rule.
6. **`LOCK_CHAIN_MAX` = 4, a longer chain or a cycle the `LockChain`
   fault?** Recommended: yes.
7. **The heaps and arena on the section, the output lock on the
   inheritance lock (§7.9)?** Recommended: yes.
8. **Export the lock to programs as `sos.Lock` and the C floor, and have the
   radio's adapter build FreeRTOS mutexes on it?** Recommended: yes;
   FreeRTOS's mutexes inherit, and the libraries were written against that.

---

## 17. What was checked

Read in full: spec §7, §8, §9, §9a, §9b, §11 and §12, §2.6, and §2.7's
`Stats`; SO-103 §1, §4.1 to §4.3, §5.1, §5.6, §9.3, §10, §11.2 and §12; the
brief; the owner's rulings (`rulings1.md`); SO-105's design (§0, §1.5, §3.5,
§5.2) and its U4 note. Read in the code at `734de56c`:
`kernel/core/threads.saw` whole, `kernel/core/sched.saw` (`pick_next`,
`run_thread`, `idle_until_runnable`, `resume_after_death`, `exit_thread`,
`invalidate_thread`), `kernel/core/irq.saw` (the tick, delivery, idle,
preemption point), `kernel/core/waitables.saw` (the parks),
`kernel/core/refs.saw` (every wake site), `kernel/core/dispatch.saw`
(`ktrap`, `thread_create`, `thread_op_decoded`, `process_start`, the
`QuotaSet` arm), `kernel/core/process.saw` (`translate_user`, the copy
doors, `start_process`), `kernel/core/loader.saw` (`prio_map`),
`kernel/core/pslot.saw` (`ProcessBody`, `GrantRow`),
`kernel/core/objects.saw` (the handle word), `kernel/abi/src/ops.saw`,
`rights.saw`, `status.saw`, `types.saw` (the handle word's split),
`kernel/sysapi/src/system.saw`, `floor.saw` and `rt.saw` (thread creation,
the lock owner, `ThreadBlock`), `rt/common/src/lib.saw` (the locks),
`root/src/main.saw` and `root/bootplan/src/lib.saw`,
`tools/sosimage/src/declaration.saw` and `imagefile.saw`,
`tests/threadrun/src/lib.saw`, `tests/lock-wait/src/main.saw`, and the
runner's ordering cases. Searched the in-flight diffs under `.leadwork/`
for op, right and fault numbers. Checked in the pinned sawlang checkout:
Blade's `[sos] priorities` table and range, and that std has no
count-leading-zeros. Nothing was built or run.
