# SO-148: the priority scheduler (design, revision 1)

Scope: build spec §7's priority scheduler, with what the networking design
(SO-103, `.leadwork/so103-design.md`) asks of it in §4.3: levels inside one
process, strict preemption on wake, a priority ceiling for critical sections,
and the latency diagnostic of requirement 4.2. Nothing is built from this
before the owner and the reviewer have read it.

Every claim about the kernel cites the function it rests on. "The scheduler
as built" is `main` at the time of writing.

---

## 1. Verdict

**Build it in five units, with one level behaving exactly as today.**

1. **Levels.** Sixteen system levels, a ready list per level and a 16-bit
   ready bitmap; pick-next is a compare, a table load and a list pop, the
   same arch-free code on every profile. Eight bands per process (spec's
   four plus `Band4` to `Band7`), resolved through the process's map when a
   thread starts. A process still names only its own bands. Spec §7's eight
   levels and four bands are short of the radio's six levels above every
   application (§3).
2. **Preemption on wake** is a level compare in the one function every wake
   goes through, raising the flag the tick already raises. The switch stays
   where it is, at the user-return boundary at the end of the trap that made
   the wake.
3. **The ceiling is a section word**, two words in the process's own memory:
   a depth the process writes and a pending flag the kernel writes. While the
   depth is nonzero no thread of that process runs but the one that set it,
   which runs in the place of any sibling the scheduler would have chosen.
   Other processes are untouched. It costs no trap to enter or leave, one
   trap to leave when a sibling was held back, and survives its holder being
   off the processor. It is not a level, so the radio needs six levels, not
   seven.
4. **No inheritance; the direct switch later.** Preemption on wake already
   switches straight to a higher receiver. The direct switch at equal levels
   reorders a level's FIFO, which the suite relies on, and nothing in the
   networking design needs it.
5. **Compatibility by construction.** The default map puts every band but
   `Background` at one level and every existing thread starts at `Normal`, so
   every existing case runs at one level, where the rules are today's. No
   case changes. The runtime's locks are the exception that matters: they
   wait by yielding, which livelocks a higher thread behind a lower holder in
   the same process. A unit moves them onto the section word and a parked
   wait before any program runs threads at two levels (§8.3).

The networking design's latency estimate (§4.3, point 6) is short: a wake
made at a preemption point switches only when the whole kernel operation
ends, not after one stride (§5.3). The latency record (§11) measures it.

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
  sit behind std's `SpinLock`, which spins without yielding, and the output
  lock waits by yielding through `sos_rt_yield`. Both comments already say a
  higher spinner above a preempted holder spins for good under levels.

---

## 3. Levels and bands

### 3.1 How many levels, how many bands

The radio's needs, from SO-103 §4.3 requirement 4.1 with the ceiling taken
out of the level count (§6 makes it a word, not a level), from the top: the
BT interrupt thread, the Wi-Fi interrupt thread, the BT controller task, the
timer task, the Wi-Fi task, and the serve thread. That is six distinct
levels, all above every application, with the network and BLE hosts and the
console driver (servers of the applications, so at or above them) beside or
below the radio's serve thread.

- **Levels: 16** (`SYSTEM_LEVELS`, `kcore.limits`). With spec's eight the
  default map takes 0 and 1, and the radio's six fill 2 to 7 with its serve
  thread sharing level 2 with every other service and no room for another
  driver's interrupt thread above the radio's tasks. Sixteen costs one more
  compare at pick (§4) and 64 bytes of list heads. It is a policy limit, not
  a hardware wall.
- **Bands: 8 per process** (`BANDS`). The four named bands stay; bands 4 to 7
  are `Band4` to `Band7`. The radio maps `Normal` to its serve thread and
  `High`, `Band4` to `Band7` to the five above it. With four bands its three
  tasks would share one level and round-robin on the 10 ms slice, which puts
  the BT controller task (IDF's `configMAX_PRIORITIES - 2`) behind a Wi-Fi
  task for a whole slice. SO-103 left that to R2's log; eight bands make the
  log unnecessary for the design.

**A process still never names a system level.** A band is a rank inside the
process; the map, which the launcher writes and the process cannot read,
says where each rank sits in the machine. So eight bands change the size of
the map, not the rule, and spec §7's "processes never see or name system
levels" stands.

### 3.2 The map

- **Storage.** One `UInt32` per body, `ProcessBody.bands`: band `b`'s level
  in bits `4b` to `4b+3`. Sixteen levels fit a nibble, so the whole map is
  one register on every profile. Beside it, `ProcessBody.level_cap`, the
  highest level the process may grant (§3.3).
- **The default map** (a child no launcher mapped): `Background` 0, every
  other band 1. It is spec's default with bands 4 to 7 at `High`'s level.
- **Root's map** applies verbatim from its image header, as spec §12 says:
  `start_process` reads the four bytes `ImageTable.prio_map` (band 0 to 3)
  and gives bands 4 to 7 `High`'s level. A byte at or above `SYSTEM_LEVELS`
  stops the boot through `fatal_image`, naming the band and the level (Blade
  refuses past 7, so only a hand-assembled image reaches it). Root's
  `level_cap` is `SYSTEM_LEVELS - 1`: root is init, as its unlimited quota
  rows are.
- **A child's map** is the default until its launcher sets it with
  `ProcessOp.BandMap(map)`, between the create and the start:

  ```
  ProcessOp.BandMap = 16        arg0: the packed map
  ProcessRight.BandMap = 1 << 23, in process_rights()
  ```

  Its answers, in the order they are decided, on the pattern of `QuotaSet`
  (`process_quota_set`): a handle without the right is `AccessDenied`; a
  process that is not `Created` (started, or `Gone`, which was always
  started) is the `BadState` fault; a level above the caller's `level_cap` is
  the `BadArg` fault and sets nothing; otherwise the map is stored, the
  child's `level_cap` becomes the map's highest level, and the op answers
  `Ok`. It may be made more than once before the start; the last wins. The
  map is immutable from the start on, as spec says.
- **The child's header field is not read.** The kernel never parses an
  image for policy, and a launcher never parses one at all (spec §12), so a
  request in a child's header has no reader at run time. The request lives
  in the declaration (§10).

### 3.3 The cap

A launcher grants at most its own `level_cap`, the rule `QuotaSet` keeps for
quota rows ("above the caller's own limit ... is a `BadArg` fault"). Root's
cap is the top, so the stock root grants anything an image says. A process
root gave `SystemRight.ProcessCreate` to cannot launch a child above itself.

### 3.4 How a thread gets its level

**The band rides `ThreadOp.Start`.** `ThreadCreate` has no free register and
`Start`'s `arg0` is unused, and a thread's level matters from the moment it
is runnable, which is the start.

- `ThreadOp.Start(band)`: a `band` at or above `BANDS` is the `BadArg` fault,
  decided after the right and before the state checks. The level is read
  from the map of the process that owns the thread (`THREADS[t].process`),
  not the caller's, stored in `ThreadSlot.level` (a `UInt8`) and never read
  from the map again: the hot path is numeric, as spec says.
- **The first thread** of a process runs at its `Normal` band: root's in
  `start_process`, a child's in `process_start`. `ProcessOp.Start` keeps its
  arguments.
- **The `sos` module.** `enum Priority: UInt8 { Background = 0, Low = 1,
  Normal = 2, High = 3, Band4 = 4, Band5 = 5, Band6 = 6, Band7 = 7 }`, the
  wire tags fixed. `Thread.start()` starts at `Normal`;
  `Thread.start(band: Priority)` names one. The floor keeps
  `sos_thread_start(handle)`, now passing `Normal`, and adds
  `sos_thread_start_band(handle, band)` for C.

---

## 4. The ready structure and pick-next

```
READY_HEAD: [Int; SYSTEM_LEVELS]   slot + 1, 0 = empty   (kcore.threads)
READY_TAIL: [Int; SYSTEM_LEVELS]
READY_BITS: UInt16                 bit l set iff level l's list is nonempty
HIGHEST:    [UInt8; 256]           the highest set bit of a byte, .rodata
```

- **Push** (`ready_push(slot)`): level `l = THREADS[slot].level`; append to
  list `l`; set bit `l`. **Push at the head** (`ready_push_head`), used only
  for a preempted thread (§5.2) and for a thread the section displaced
  (§6.3).
- **Pick** (`ready_pop`): `b = READY_BITS`; `l = if b > 0xFF { 8 +
  HIGHEST[b >> 8] } else { HIGHEST[b] }`; pop list `l`'s head; clear bit `l`
  if the list is now empty. `ready_peek` is the same without the pop.
- **Remove** (`ready_remove_process`): the existing trailing-pointer walk,
  once per nonempty level, clearing a bit whose list it empties. Each
  thread is on at most one list, so the bound is unchanged.
- **Empty** is `READY_BITS == 0`; `idle_until_runnable` tests that instead
  of `READY_HEAD == 0`.

**The cost on the hot path.** A syscall that reschedules nothing pays
nothing new: `stays_running` is unchanged. A wake adds two indexed loads,
a bit set and the level compare of §5. A switch adds a compare, a load from
`HIGHEST`, and a bit clear when a list empties. Per thread one byte, which
fits the padding beside `ThreadSlot.state` and `refs`; per body two words.
Kernel statics: 2 × 16 words of list heads and the 256-byte table.

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

- a wake made by a syscall (an Event signal, a post, a reply, a `Start`)
  switches at that syscall's return, after its result pair is written;
- a wake made by an interrupt taken from user mode switches at that
  interrupt's return (`ktrap`'s interrupt arm returns `pick_next`);
- a wake made at a preemption point switches when the operation that
  polled returns (§5.3);
- a wake made while the kernel idles has no running thread to compare
  against, and the idle loop's exit picks the highest level.

Equal levels never preempt: a wake of an equal or lower thread raises
nothing, which is today's rule ("a wake never preempts") restricted to the
level, and spec's "same-priority never preempts mid-slice".

The requeue of the outgoing thread inside `pick_next` must not raise the
flag; it runs with the thread already `Runnable`, so the "a thread is
running" test is false there. Joiners woken by `exit_thread` meet an exiting
thread, likewise not running.

### 5.2 Where a preempted thread goes

**To the head of its level; a thread whose slice ended or that yielded goes
to the tail.** `pick_next` tells the two apart without a second flag: an
outgoing thread that is still `Running` was preempted exactly when the
highest ready level is above its own (`ready_peek`). Head placement keeps a
preempted thread ahead of the peers it was ahead of, which is POSIX's
`SCHED_FIFO` rule, and it keeps the section's holder at the front (§6.3).
A tick and a wake in the same trap read as a preemption; either answer is
round-robin-fair, and this one needs no state.

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
back, so the radio's map has six levels, not seven. And it gives exactly
what masking gave the radio with respect to its own state, nothing more:
other processes run on.

### 6.2 The word and its registration

The word is a pair of machine words, word-aligned, in the process's own
memory:

```
word 0  depth     written by the process: nonzero = the section is held
word 1  pending   written by the kernel: nonzero = a sibling was held back
```

`ProcessOp.SectionWord(addr)` (17) registers it, on the caller's own Process
handle:

- **Gated by `ProcessRight.ThreadCreate`.** The word orders a process's own
  threads, and only a process that can make threads has several to order;
  it grants nothing beyond what the map already granted (§6.6), so a right
  of its own would gate nothing new.
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
   (15), the line `SOS: process fault: parked inside its critical section`.
   A park is decided in `ktrap` after the dispatch (`result.blocked` with
   the caller `Blocked`); the exit in `thread_op_decoded`'s `Exit` arm,
   before `exit_thread`. A section that cannot end would hold P's other
   threads forever with no diagnostic. FreeRTOS treats a block inside a
   critical section as a bug too.

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
through `thread_create(run:)` (U3, §8.3). `enter()` on an unregistered word
panics, naming the registration, rather than entering a section that holds
nothing back.

### 6.8 Cost

A process with no word pays one compare per switch decision
(`section_at == 0`). A process with one pays one load of the depth at each
decision that involves it, and a store of `pending` when it defers. Entering
and leaving are two plain stores and a load; the yield is one trap and only
when a sibling was held back.

---

## 7. The direct switch and no inheritance

**No inheritance**, as spec says, and nothing to build for it. The
launcher's convention (a server at or above its clients' levels) is what
keeps inversion out, and the builder can check it for the stock root
(§10.4).

**The direct switch: later, as its own unit.** Spec's fast path switches
straight to a receiver a post or a reply readies. Above the sender's level,
preemption on wake (§5) already does that. At an equal level it means the
receiver runs ahead of its level's FIFO, which reorders exactly the queue
the suite's ordering cases rely on, and its benefit is latency at equal
level, which nothing in the networking design asks for. When it is built it
is one rule ("a fused `Call` that parks hands the processor to the server it
readied, ahead of its level"), its own cases, and a pass over every case
that orders by a call, which is a brief of its own.

---

## 8. Compatibility

### 8.1 One level is today

At one level the new structure is the old one: one list, appended on a wake
and on a slice's end or a yield, popped from the head; no preemption,
because preemption needs a higher level; and `ready_remove_process`'s walk
preserves the order of the threads it leaves. The head placement of §5.2
never happens.

### 8.2 Every existing case is at one level

- Every manifest in the tree states the default priorities, so root's
  `Normal` is level 1 and its first thread runs there.
- A child no launcher maps has the default map, and its first thread starts
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

### 8.3 What breaks at two levels: the runtime's locks

The comments in `rt/common/src/lib.saw` already say it: under levels, a
thread above a preempted holder of a runtime lock never lets the holder run.

- **The arena and the heaps** (std `SpinLock`, spinning): their windows make
  no call that parks (`rt_alloc`, `rt_dealloc`, the heap growth's
  `extend`/`map` answer without parking). So each window runs inside the
  section: a sibling is not run while one is open, there is no in-process
  contention left to spin on, and the spin is reached only at one level,
  where a tick moves it as today.
- **The output lock** is held across a whole `print`, which with a console
  record posts to the driver and calls when the ring is full: a park. It
  cannot be a section. Its wait becomes a park: a waiter counts itself in
  the lock's state, gives the `SpinLock` back and waits on a Waiter attached
  to an Event the runtime makes at the first contention; the last give of a
  contended lock signals the Event. A spurious wake re-checks and waits
  again. A refused Event or Waiter (a narrowed right, a quota) panics,
  naming it, instead of falling back to a yield that hangs.
- **Registration.** The runtime registers its section word the first time
  the program makes a thread through `thread_create(run:)`. A
  single-threaded program makes no extra syscall, so no `Stats` reading in
  any case moves. A program that makes threads another way calls
  `Process.section_register()` itself; without it the windows run unguarded
  and the runtime's comment keeps saying why.

Until U3 lands, a process's threads at two levels must not contend on the
runtime's locks; the scheduler's own cases are written so they never do
(§14.1, the construction notes).

---

## 9. The tick, idle, deadlock and `Stats`

- **The tick** is unchanged: one-tick slices, charged to the running thread,
  re-armed from now (`on_timer_interrupt`). A slice's end requeues the
  thread at the tail of its own level, and the pick takes the highest level,
  which is the thread again when it is alone there. Lower levels wait for
  higher ones to block: strict priority, no aging.
- **Idle** is an empty bitmap; the loop and its re-test are
  `idle_until_runnable`'s, on `READY_BITS`.
- **Deadlock** is unchanged: nothing ready at any level and no bound line or
  armed Timer. A thread starved by a higher spinner is runnable, so starving
  is not deadlock and is not reported.
- **`Stats` gains no column.** A count of preemptions mixes causes the
  program determines (a signal to a higher thread) with causes the machine
  determines (an interrupt's wake), which is the single-total problem
  §2.7 splits the trap columns to avoid; nothing could be asserted about it.
  The section's `pending` is already the process's own observable for the
  one count it might want. Per-thread stats stay not built.
- **Which parked thread a readiness feeds** is unchanged: the Waiter's
  blocked list is fed from its head, the thread that parked last, whatever
  its level (`block_on_wait`, `wake_one_waiter`), and the room park's
  resume scans by slot (`notify_room`). Neither is level-ordered and spec
  §2.2 promises no order. The radio parks each thread on its own Waiter
  (SO-103 §4.3 point 5), so it needs none.

---

## 10. The image, the declaration, the builder and the stock root

### 10.1 Where a child's request lives

**In its declaration, `process.toml`**, beside its quota and rights requests,
read by the builder, which is where a stock-root child's every other request
is read. Spec §7 has the launcher read the request out of the image, but
spec §12 has no launcher ever parse an image, so for a child the header field
has no run-time reader; the builder can read either, and the declaration is
already its source for the launcher's grants. The header field stays root's
band map.

```toml
# process.toml
priorities = { normal = 3, high = 4, band4 = 5, band5 = 6,
               band6 = 7, band7 = 8 }
```

The keys are `[sos] priorities`'s, plus `band4` to `band7`, one name for one
thing across the two formats. A band left out takes spec's default,
`background` 0 and `low`, `normal` and `high` 1, and each of bands 4 to 7
left out takes `high`'s level, as root's do.

### 10.2 What the image says

`[[process]] priorities = { ... }` in the image description overrides the
declaration band by band: the image is the launcher's policy, and may honor,
clamp or move the request, as spec says of a launcher.

### 10.3 What the builder refuses, by name

In `tools/sosimage/src/declaration.saw` and `imagefile.saw`, and so in
`check-tree`:

- a key that is not a band;
- a level at or above `SYSTEM_LEVELS`;
- a map whose levels fall from a lower band to a higher one (`low` above
  `normal`, say): the kernel would take it, but a process that ranks its
  bands cannot have meant it;
- a child package whose built header map is not the default, since a child's
  header is not read: `Saw.toml [sos] priorities is root's band map; state
  a child's in its process.toml`.

### 10.4 The plan and the stock root

`bootplan.Child` gains `bands: UInt32`, the packed map, and the plan's word
counts change with it (`CHILD_WORDS`). The stock root sets it with
`child.band_map(c.bands)` after the quota rows and before the gives, for a
child whose map is not the default. A step that fails stops the machine as
every other step does (`root: bands NAME: STATUS`). The builder also refuses
a plan in which a client's `Normal` level is above its server's, the
convention that stands in for inheritance; a hand-written root is its own
policy and is not checked.

`SOS_IMAGE_BUILDER=python` refuses a case whose plan carries a non-default
map, by name, as it refuses the other kinds the builder alone builds.

---

## 11. The latency record (requirement 4.2)

**Always built, read by the line's holder.** A separate diagnostic kernel
would measure a different kernel from the one that ships, and the record
costs two clock reads per delivered fire and one compare per trap.

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
- **How it is read:** `InterruptOp.Latency(record)` = 1, on
  `InterruptRight.Wait`, answering through a two-doubleword copy-out record
  (`fires`, `worst_ns`) and resetting both, so R3 reads one window per read.
  A refused record is the copy door's fault, as for every record.

R3 prints its lines' worst cases from that record. Nothing about latency is
concluded before it exists, as SO-103 says.

---

## 12. Spec text that changes

### 12.1 §7, the first paragraph and the not-built block

Replace "**What is built.** The kernel schedules one round-robin FIFO — no
levels, no ready bitmap — on a one-tick slice." with:

> **What is built.** The kernel schedules by fixed priority over
> `SYSTEM_LEVELS` (16) system levels, 0 the lowest, round-robin within a
> level on a one-tick slice.

Replace "**Not built: priorities.** ... The rest of this section specifies
the scheduler that is not yet built." and its first four bullets (from
"Fixed-priority preemptive, 8 system levels" through "The map comes from
build-time metadata + launcher policy") with:

> - **A ready list per level and a ready bitmap.** Pick-next takes the head
>   of the highest nonempty level: a compare, a table load and a pop, the
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
>   `enum Priority { Background, Low, Normal, High, Band4, Band5, Band6,
>   Band7 }` in the `sos` module, wire tags 0 to 7. `ThreadOp.Start` carries
>   the band; a tag past 7 is the `BadArg` fault. A process's first thread
>   runs at `Normal`. No syscall accepts a system level from the process
>   that will run at it, and no op tells a process its map.
> - **A band map per process, in the kernel.** Each process holds a map from
>   its eight bands to system levels, one nibble each in one word. A thread's
>   level is read from the map of the process it runs in when it is started
>   and stored plain. The default map is `Background` 0 and every other band
>   1. Root's map is its image header's, bands 4 to 7 at `High`'s level, and
>   a level past the top stops the boot. A child's is the default until its
>   launcher sets it with `ProcessOp.BandMap(map)`, on `ProcessRight.BandMap`,
>   between the create and the start: a child that is not `Created` is the
>   `BadState` fault, and a level above the caller's own highest is the
>   `BadArg` fault (root's highest is the top level). The map is immutable
>   from the start.
> - **The request is the declaration's, the decision the launcher's.** A
>   child asks for its map in its declaration (`process.toml`'s
>   `priorities`); the image may override it band by band; the builder
>   writes the result into the stock root's plan, and the stock root sets it.
>   A launcher never reads an image (§12), so the kernel reads no child's
>   header field, and the builder refuses a child whose header states a map.
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

Keep "**Launching is a capability.**", "**The map is immutable after
creation.**" (dropping "each process sees only its 4 named bands" for "its
eight bands"), "**No priority inheritance.**" with (b) rewritten:

> (b) preemption on wake, which switches straight to a receiver a post or a
> reply readies above the sender's level. **Not built:** the direct switch
> at an equal level, which would run such a receiver ahead of its level's
> list.

and "**Uniprocessor kernel in v1**" as they are. In the "**The tick does not
stop when the machine idles.**" bullet nothing changes.

### 12.2 §8

In "**Two phases, inert until started.**", after "`ProcessOp.Start` ... mints
the first thread at that recorded entry and makes it runnable": "at the
child's `Normal` band (§7)". In "**What is built.**", after "Thread
`Start`/`Join`/`Exit`/`Yield`": "; `Start` carries the thread's band (§7)".

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
its highest level the top (§7)". In "**The stock root's plan.**", "its quota
rows" becomes "its quota rows, its band map". In "**The launch
convention**", after "It sets the declared quota rows before any give": "and
the band map, when it is not the default".

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
  main thread is at level 2 with room below and above.
- Until U3, a case never lets a lower thread be preempted while it holds the
  output lock: a lower thread prints only when no higher thread can be woken
  before its print returns.
- A case prints inside a section only through the debug print, which never
  parks; a console record's call can park, which is `SectionHeld`.
- A spin "until the interrupts column has risen" reads `ProcessOp.Stats`
  before and in the loop: the kernel counts the tick that ended the slice,
  so the spin ends on a count the kernel keeps, and a stalled host only
  delays it.
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
level_default_map        a child no one mapped starts a  R: default map puts
                         thread at Background, yields,   Background at 1:
                         prints Y, joins it: Y, then     B before Y
                         the thread's B
level_root_header        hand-assembled root image with  R: the boot check
                         a band at 16: fatal_image line  gone
                         naming band and level
level_kill_two_levels    kill a child with ready         R: the per-level
                         threads at two levels; root's   walk misses a
                         own two ready threads keep      level: a dead
                         their order: R1 R2              thread runs or the
                                                         bitmap lies
```

Refusals, one case each, each the fault line and teardown the dispatch
order gives (§3.2, §3.4): `band_map_started` (`BadState`),
`band_map_gone` (`BadState`), `band_map_denied` (`AccessDenied`, a handle
minted without the right), `band_map_above_cap` (`BadArg`: a child
launcher whose own map tops at 1 maps its child at 2; inverted cap check
passes it), `start_band_past` (`BadArg`, band 8), and `band_map_twice`
(the last map wins: the second map's level is the one the order shows).

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

### 14.4 The runtime's locks (U3)

```
case                       construction and expected     red when
rt_arena_two_levels        a Low thread allocates in a   R: the arena window
                           loop; a Timer wakes a High    outside the section:
                           thread that allocates too;    High spins to the
                           tick off: both finish, totals timeout
                           printed
rt_output_two_levels S3    a Low thread prints through   R: the parked wait:
                           the console record with the   High spins to the
                           ring kept full; a High thread timeout
                           woken by the driver's reply
                           prints: whole lines, Low's
                           first
rt_register_lazy           a program reads its syscalls  R: registration at
                           column around its first and   start-up: no +1 on
                           its second                    the first; I: every
                           `thread_create(run:)`: the    create registers:
                           first costs one call more     the second's
                           than the second               BadState fault
```

### 14.5 The builder and the stock root (U4)

Builder tests (`tools/sosimage/tests/`), each a refusal by name: an unknown
band key, a level of 16, a falling map, an image override naming no band, a
child with a non-default header map, a client above its server; and the
bytes: a declared map and an override packed into `Child.bands`. A gate case
`stock_bands`: an image description whose process declares
`normal = 2, high = 3` and starts a High thread, with the stock root setting
the map: the High thread's line before its main's next line (R: root never
calls `band_map`, the default map puts both at 1). The `check-tree` row: the
tree's declarations read clean.

### 14.6 The latency record (U5)

The line is a UART's transmitter-empty source, which asserts as soon as it
is enabled with the transmitter empty (ns16550 `IER.ETBEI`, PL011 `TXIM`,
the S3 UART's `TXFIFO_EMPTY`), so each enable is exactly one fire; U5
confirms that on each emulator before a case relies on it.

```
case                     construction and expected      red when
latency_counts S3        enable, wait, ack, disable, 4  R: the stamp: fires
                         times; read: fires=4, and      0; R: the reset:
                         worst > 0 is printed only as   second read fires=4
                         "worst nonzero" (a stalled
                         host only raises it); read
                         again: fires=0
latency_pending          fire while the driver is busy, R: the mark at a
                         then Wait finds it pending:    Wait that finds it
                         fires=1                        pending: fires 0
latency_denied           a handle without Wait:         R: the gate
                         AccessDenied
```

No case asserts a latency's value. `worst > 0` holds in the passing
direction alone: the interval contains at least a trap and a switch, and a
stall can only lengthen it.

---

## 15. Units of work

```
U1  M  Opus    Levels: the ready structure and pick, preemption on wake,
               head and tail, ThreadOp.Start's band, ProcessOp.BandMap and
               its right, root's header map and its boot check, `Priority`,
               `start(band:)`, the floor; spec §7, §8, §2.7, §11, §12 text;
               CLAUDE.md; §14.2's cases. First.
U2  M  Opus    The section word: the op, rules 1 to 7, SectionHeld, the
               `sos.Section` API and the floor; spec text; §14.3. After U1.
U3  S  Sonnet  The runtime's locks: arena and heap windows in the section,
               the output lock's parked wait, lazy registration; §14.4.
               After U2.
U4  M  Sonnet  The builder and the stock root: process.toml `priorities`,
               the image override, the refusals, `Child.bands`, root's
               `band_map`, check-tree; §14.5. After U1, beside U2.
U5  S  Sonnet  The latency record; §14.6. After U1, beside U3.
U6  S  Opus    Later, not now: the direct switch at an equal level, with
               the audit of call-ordered cases (§7).
```

At most two run at once: U1 alone, then U2 with U4, then U3 with U5. U1 and
U2 are Opus because each writes spec contracts; the rest follow them.
SO-103's R2c (the adapter's interrupt threads and ceiling) needs U1 and U2;
its R4 and B-1 need U1 to U3.

---

## 16. Questions for the owner

1. **Sixteen system levels, not spec's eight?** The radio needs six above
   every application; eight leaves its serve thread sharing the services'
   level and no room above its tasks for any other driver. Recommended: yes.
2. **Eight bands per process (`Band4` to `Band7`) rather than the radio's
   tasks sharing a level?** Recommended: yes; the map stays the only
   priority surface, a process still names no system level.
3. **A child's request moves to `process.toml`, the header field is root's
   alone, and the builder refuses a child header that states a map?** The
   alternative keeps it in Saw.toml and needs a sawlang change (bands 4 to
   7 in the header's reserved word, levels to 15). Recommended: yes.
4. **The ceiling as the section word, not a syscall pair and not a level?**
   Recommended: yes (§6.1).
5. **Parking or exiting inside a section faults the process
   (`SectionHeld`)?** The alternative, letting the section lapse while the
   holder is parked, hides the bug and breaks rule 1 silently. Recommended:
   yes.
6. **The section word gated by `ThreadCreate`, with no right of its own?**
   Recommended: yes; it grants nothing the map did not.
7. **A preempted thread resumes at the head of its level?** Recommended:
   yes; at one level it never happens.
8. **The direct switch later, as its own unit?** Recommended: yes; it
   reorders a level's FIFO and nothing waits on it.
9. **The latency record always built, read and reset through the line's own
   Interrupt handle?** Recommended: yes; a diagnostic build would measure a
   kernel that does not ship.
10. **No new `Stats` column?** Recommended: none (§9).
11. **The runtime's output lock waits by parking on an Event and a Waiter it
    makes at the first contention, and panics if it cannot make them?**
    Recommended: yes; the yield it uses today hangs at two levels.
12. **The names `Band4` to `Band7` (`band4` to `band7` in TOML)?**
    Recommended: yes; a rank above `High` has no better name than its
    number, and the radio's adapter maps FreeRTOS priorities onto them by
    rank.

---

## 17. What was checked

Read in full: spec §7, §8, §9, §9a, §9b, §11 and §12, §2.6, and §2.7's
`Stats`; SO-103 §1, §4.1 to §4.3, §5.1, §5.6, §9.3, §10, §11.2 and §12; the
brief. Read in the code: `kernel/core/threads.saw` whole,
`kernel/core/sched.saw` (`pick_next`, `run_thread`, `idle_until_runnable`,
`resume_after_death`, `exit_thread`, `invalidate_thread`),
`kernel/core/irq.saw` (the tick, delivery, idle, preemption point),
`kernel/core/waitables.saw` (the parks), `kernel/core/refs.saw` (every wake
site), `kernel/core/dispatch.saw` (`ktrap`, `thread_create`,
`thread_op_decoded`, `process_start`, the `QuotaSet` arm),
`kernel/core/process.saw` (`translate_user`, the copy doors,
`start_process`), `kernel/core/loader.saw` (`prio_map`), `kernel/core/
pslot.saw` (`ProcessBody`, `GrantRow`), `kernel/abi/src/ops.saw`,
`rights.saw`, `status.saw`, `kernel/sysapi/src/system.saw` and `floor.saw`
(thread creation and start), `rt/common/src/lib.saw` (the locks),
`root/src/main.saw` and `root/bootplan/src/lib.saw`,
`tools/sosimage/src/declaration.saw` and `imagefile.saw` (schemas),
`tests/threadrun/src/lib.saw`, and the runner's ordering cases. Counted:
every `Saw.toml` in the tree with `priorities` states the default. Checked
in the pinned sawlang checkout: Blade's `[sos] priorities` table and range,
and that std has no count-leading-zeros. Nothing was built or run.
