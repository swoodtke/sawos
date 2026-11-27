# SO-141 design: sosrt runs Saw's background tasks

Revision 2.1. This is a design only: no tracked file changed. Claims are marked
three ways:

- **[read F:L]**: read in the tree at that file and those lines.
- **[probed]**: a compile I ran. The sources are under
  `/private/tmp/claude-501/-Users-shawn-Projects-sawos/34c0d76b-723c-482d-939f-716d368f6e5c/scratchpad/so141/`.
- **UNVERIFIED**: neither.

Abbreviations: `ABI` is sawlang v0.24.5's `runtime/ABI.md` and `LS` its
`LANGUAGE_SPEC.md`, in `.toolchains/cb66b632…/`. Main pins v0.24.5. `SMP` is
the tracker doc `SO:smp` r3. No QEMU probe was run: every item checked in this
revision was settled by reading.

## Changes in 2.1

The reviewer's t3 holds: a bounded `send` holds its claim as a handle, so a
thread that has live tasks spends one Handle row while that `send` waits.

- **The row is counted** in Q1's line below, in §4's state, and in S0's spec
  text, and the child default for Handles rises from 12 to 16.
- **The full table is handled in the kernel and the runtime.** A wakeable
  `Call` that finds no row for its claim answers `QuotaExceeded` or
  `NoResource`, with nothing staged and the carried handles still the caller's
  (§3). The runtime's `send` then falls back to a plain `Call`, which needs no
  row (§5). The fallback is honest: nothing was staged, so the message goes
  out once, the send still works, and the only thing lost is the bound. While
  it waits, that thread's tasks wait with it.
- **New cases.** K-4 gains a step that holds the kernel half, and a new case,
  R-10 `bounded_send_table_full`, holds the runtime half. Each has its
  mutants.

Nothing else changed.

## Changes in revision 2

- **Waits are bounded in the kernel, not the runtime (owner's ruling).**
  - **Kernel deadlines and wakes.** Every park the kernel offers reads a
    per-thread *wait word*: a monotonic deadline the thread writes into its
    own memory, registered once. A new no-handle op, `THREAD_WAKE_OP(id)`,
    ends a thread's wakeable park early, or latches if the thread is not
    parked. Two new statuses report the outcomes: `TimedOut` and `Woken`.
  - **A bare park.** A new no-handle op, `PARK_OP`, parks on nothing until
    the deadline or a wake. `sleep_ns` and `reactor_poll` are now that op.
  - **No kernel objects per thread.** The per-thread Waiter, Timer and Event
    of r1 are gone, and so is the r1 bridge (a second Timer and Event, the
    reserved top-bit keys and the cross-thread forwarding).
  - **Questions and comments that dissolve.** Q2 (waits with no bound) and
    Q3 (reserved keys) dissolve, and so does the reviewer's t1 (the reserved
    key half in `Waiter.add`): no key is reserved anywhere.
  - **New sections:** §2 (the kernel ops) and §3 (each park's deadline and
    wake). The runtime sections that followed in r1 shrink and are renumbered
    (§4–§8).
- **Every runtime wrapper bounds its wait in one trap.** The trap is the wait
  itself; writing the deadline is a memory store. That covers `Waiter.wait`,
  `PipeInlet.send`, `PipeReply.resolve`, `PipeRequest.reply_wait` and
  `Thread.join`. `reactor_wake` wakes each parked thread by its ID.
- **Q1, quotas (lead's call: raise the defaults; corrected in 2.1).** The
  executor costs a child Handle rows, and no other kernel object:
  - **one row per process**, for its Clock;
  - **one row per thread while that thread's bounded `send` waits**, for the
    claim a wakeable `Call` mints (§3). A bounded `Resolve` uses the claim
    handle the caller already holds, and no other wait spends a row.

  The child default for Handles therefore rises **from 12 to 16**, enough
  for the default four threads to each have a bounded `send` in flight.
  Every other default stays as it is
  **[read kernel/core/limits.saw:506-544]**. 16 is under every board's
  `MAX_HANDLES` of 32 **[read hal/riscv32/kernel/lib.saw:335;
  hal/arm64/kernel/lib.saw:568; hal/xtensa-esp32s3/kernel/lib.saw:159]**. The
  `quotahandles` case prints `limit=12` and moves to 16
  **[read tools/sos_runner.py:8091, 8110]**.

  A table that is full anyway costs only the bound (§5).
- **Q4 (lead's call), the thread gate, is a follow-up out of scope.** The
  missing seams are named in §9.
- **Q5 (lead's call), deadlock reports:** a process whose tasks all wait
  forever parks, and the kernel's deadlock line is enough.
- **Q6 (lead's call), threads with no block:** `set_thread_executor` on a
  thread with no block panics with a sawos message naming
  `enter_thread_block` (§4).
- **The reviewer's t2** is a case that pins `sleep_ns` directly (§8, R-7).
  ABI says nothing cuts `sleep_ns` short **[read ABI:249-253]**. So the
  mid-sleep wake ends the kernel park, but the sleep absorbs it and keeps it
  for the next poll; the sleep still lasts its whole delay. The case pins all
  three (Q3 below confirms the reading).
- **The units are reordered:** spec first, then the kernel's two units, then
  the runtime's three, which shrink (§9).
- **r1's UNVERIFIED items:**
  - **The runner routes a kernel compile's refusal to its case:** now read
    **[read tools/sos_runner.py:17364-17371, 18311-18331]**.
  - **Turn order in a sweep:** live slots are walked in ascending slot order
    **[read std/taskgroup.saw:438-443]**. That slots are handed out in spawn
    order on a fresh group stays UNVERIFIED, and the channel case asserts its
    order so a miss fails loudly.
  - **No longer apply:** `Waiter.add` without `Attach`, a Timer arm near
    `u64::MAX`, and which parked thread a Waiter delivery reaches. All three
    went with the bridge.

## 0. What the probes settled (unchanged from r1)

- **Exactly eight seams are missing.** With both background entries
  declared, a sawos `main` program that calls `Task.spawn` compiles against
  the tree, and only the eight executor seams stay undefined
  **[probed: b/main.saw]**.
- **A channel adds two more:** `lock_acquire` and `_release`
  **[probed: a/chan2.saw]**.
- **A thread-context `Channel.receive` adds the eight `__saw_rt_pthread_*`
  gate seams** **[probed: a/chan.saw]**.
- **Without the declarations, a spawn is refused** with
  `spawn.freestanding` **[probed: a/nodecl.saw]**.
- **Several modules may declare the drive**, as long as one declares both
  entries **[probed: c/]**.
- **A `sync` body cannot join a task, but it may call a `blocking` helper
  that does** **[probed: a/syncspawn.saw]**.
- **The executor word needs no HAL change.** arm64's `TPIDR_EL0` already
  travels with each thread **[read hal/arm64/kernel/lib.saw:136-147,
  800-829]**. riscv32's `tp` **[read hal/riscv32-common/kernel/trap.S:67,128]**
  and the S3's `THREADPTR` **[read hal/xtensa-esp32s3/kernel/boot.S:620-621,
  690-691]** are in the trap frame.
- **The thread register is taken** by the lock-owner identity
  **[read kernel/sysapi/src/rt.saw:175-214]**.

## 1. The executor word (unchanged from r1)

The executor word is a field of `ThreadBlock`, which the thread register
names: one register read and one load **[read rt.saw:282-285]**.

- **Who has a block:** the main thread of every `main` program, root's
  included, and every `thread_create(run:)` thread. Their blocks are in
  `sosmain._start`'s frame and in the start record
  **[read rt/main/src/lib.saw:131-132; rt.saw:407-408]**.
- **Who has none:** a raw `thread_create(entry:)` thread, and an
  `@export _start` program's main thread, unless it calls
  `enter_thread_block`.

`ThreadBlock` becomes `{thread, id, executor, record, wait}`. `wait` is the
thread's wait word (§2), one doubleword, eight-aligned. `StartRecord` grows to
match, so the "four words" in `thread_create(run:)`'s documentation changes
**[read system.saw:905]**. `enter_thread_block` registers the wait word with
`WAIT_WORD_OP`, which is one trap at each thread's start.

## 2. The kernel: deadlines and the wake

### The shape: a registered wait word, not a deadline argument per op

A thread registers one doubleword in its own memory with
`WAIT_WORD_OP(addr)`. The kernel reads it, through the copy-in door, at every
park the thread makes.

Why the word and not an argument on each op:

- **No op changes shape.** The ops are fixed arity
  **[read kernel/sysapi/src/floor.saw:28-35]**. An absolute 64-bit deadline
  is two registers on riscv32 and the S3, and `PipeInletOp.Call` already
  fills all three of its arguments (args, length, reply buffer)
  **[read floor.saw:1202-1215]**. A deadline argument would mean new wide
  forms of `Waiter.Wait`, `Call`, `Resolve`, `ReplyWait`, `Join` and
  `LOCK_WAIT`.
- **One trap per bounded wait.** The deadline is a store to the thread's own
  memory, and the op is the only trap. A "set the deadline" op would make two.
- **Every existing park is unchanged.** A word of 0 is today's park, bit for
  bit, so no existing caller, the C floor's included, sees a new status
  unless it wrote one.

### The word's values

The word holds a deadline on the Monotonic clock, in nanoseconds: the
machine counter `hal.timer_now_ns()`, the reading `ClockOp.Now` answers and
the time every Timer is armed in **[read kernel/core/time.saw:15-18, 425-430;
kernel/sysapi/src/clock.saw:52-60]**.

| word | the park is |
|---|---|
| `0` | **plain**: today's park. No deadline, and a wake does not end it; a wake latches for later |
| `UInt64.max` (`NEVER_NS`) | **wakeable**, with no deadline |
| any other | **wakeable**, with that deadline |

### The order at an op that would park

1. An op that can answer now answers as today. Readiness wins over every
   deadline: a ready attachment, an exited thread, a settled reply.
2. Otherwise, on a wakeable word:
   - a latched wake clears the latch and answers `Woken`, without parking;
   - otherwise, a deadline at or before now answers `TimedOut`, without
     parking;
   - otherwise the thread parks with the deadline recorded.
3. A plain word parks as today.

Neither at-once answer is a park, so it does not meet the section rule. A
park inside a held section still faults as today
**[read kernel/core/dispatch.saw:5874; spec.md:4185-4192]**.

### The statuses

There are two new `SosStatus` values. Neither is a fault, because the caller
could not have known: the clock or another thread decided.

- **`TimedOut = 12`**: "the wait's deadline came first". The park ended at
  its deadline, or the deadline had passed at the op. Nothing the op waits
  for was consumed; §3 gives what each op leaves.
- **`Woken = 13`**: "another thread woke this wait". The park was ended by
  `THREAD_WAKE_OP`, or a latched wake was waiting at the op. Nothing was
  consumed either.

Both are distinct from every existing status **[read
kernel/abi/src/status.saw:154-163]**:

- `WouldBlock` is a poll op's "not now", with no park.
- `Revoked` is "the thing waited on is gone".
- `PeerClosed` is the far end gone.

Numbers 12 and 13 are the next free ones.

### The three ops

All three are made with `NO_HANDLE` and continue the band below
`LOCK_WAKE_OP` **[read kernel/abi/src/ops.saw:168-195]**: `WAIT_WORD_OP`
`0xFFF8`, `THREAD_WAKE_OP` `0xFFF7` and `PARK_OP` `0xFFF6`. `OP_BAND_FLOOR`
moves to `PARK_OP` **[read ops.saw:1222]**. Op numbers are not ABI.

- **`WAIT_WORD_OP(addr)`** registers the calling thread's wait word, and 0
  unregisters it. A later registration replaces the earlier one.
  - An address that is not eight-aligned is the `BadAlign` fault.
  - A doubleword the copy-in door refuses now is that door's fault.
  - The kernel keeps only the address, and reads it again at each park. A
    word unmapped later is the door's fault at that park, as for any buffer
    **[read spec.md, §2.2 "Copy-out is checked"]**.
  - A thread with no word registered parks plain.
- **`THREAD_WAKE_OP(id)`** wakes the thread `id` names (spec §7's ID):
  - parked in a **wakeable** park: the park ends with `Woken`, and the
    thread is readied;
  - in a **plain** park, or not parked: the thread's latch is set, and its
    next wakeable park answers `Woken` at once.

  It answers `Ok`. The IDs it accepts are decoded as `LOCK_WAIT_OP` decodes
  an owner **[read spec.md:4269-4302]**:
  - a thread of the caller's own process that has run and not exited: woken;
  - one that has exited, or whose slot was freed: `Revoked`;
  - a thread of another live process, or any ID naming no thread there ever
    was: the `BadArg` fault.

  A thread may wake itself, which sets its own latch.

  **Who may wake whom:** a thread of the same process, by ID, with no handle
  and no right. This is `LOCK_WAKE_OP`'s precedent: it acts only inside the
  caller's process, whose threads already share every byte, and a thread with
  no Thread handle of its own (one with no block) must still be able to wake
  and be woken. The runtime's records keep each thread's ID (§5).
- **`PARK_OP`** parks the caller on nothing. It is always wakeable: a park on
  nothing that a wake could not end would end only at its deadline, or
  never. It takes the deadline from the wait word, where 0 or `NEVER_NS`
  means none. It answers `TimedOut` or `Woken`, never `Ok`.

### The latch

The latch is one flag per thread, in the kernel. It is cleared only when it
ends a wakeable park: at the op, which answers `Woken` without parking. A
wake that finds the thread parked wakeable ends that park directly and
leaves no latch. Plain parks neither read nor clear it, so the kernel's own
waits are never cut short:

- the runtime's lock waits and the output lock's wait are made with the
  word at 0 (§6);
- so is every park by code that never writes a word.

A slot's latch starts clear and is reset when the slot is reused.

### The kernel side

- **Four new `ThreadSlot` fields** **[read kernel/core/threads.saw:161-268]**:
  - `wait_word`: the registered address, or 0;
  - `wait_deadline`: the deadline while parked, or `NEVER_NS`;
  - `wakeable`;
  - `wake_latched`.
- **The schedule.** `reprogram_timer` takes the earliest parked deadline
  beside the tick and the Timers **[read time.saw:455-480, 327-339]**.
  `on_timer_interrupt` expires every parked thread whose deadline is at or
  before now, beside `expire_timers` **[read kernel/core/irq.saw:193-209]**.
  A scan of the thread slab costs what `earliest_timer_deadline`'s scan of
  the Timers costs.
- **The deadlock test.** `has_external_wake_source` also answers yes for a
  parked thread with a finite deadline **[read irq.saw:402-424]**. That
  thread will be readied by the clock, as an armed Timer's waiter is.

## 3. Each park's deadline and wake

On `TimedOut` and `Woken` the kernel takes the thread off whatever it is
parked on, as the op's own wake would, and answers the status.

| op | park **[read]** | what an early end leaves |
|---|---|---|
| `Waiter.Wait` | the Waiter's blocked list (`block_on_wait`, waitables.saw:821) | the thread is unlinked from the list; no record is written and nothing is consumed |
| `ThreadOp.Join` | the target's joiner list (`block_on_join`, threads.saw:785; dispatch.saw:2150-2172) | the thread is unlinked; the target is untouched, and a later join reads its code |
| `LOCK_WAIT_OP` | `lock_at`/`lock_holder` (dispatch.saw:2265-2291) | `lock_unpark` with the status, then `lock_recompute(holder)`, so the raise this waiter made is undone (sched.saw:585-612). The waiters flag stays set, and the holder's give traps once for nobody, which is harmless |
| `PARK_OP` | nothing | nothing |
| `PipeInletOp.Call`, room park | `call_pipe` (waitables.saw:853) | nothing was staged; the call fields are cleared, and the value register is `NO_HANDLE` |
| `PipeInletOp.Call`, reply park | the exchange's `caller` tie (waitables.saw:885) | below |
| `PipeReplyOp.Resolve` | the same tie (`park_resolve`, dispatch.saw:5160-5167) | below |
| `PipeRequestOp.ReplyWait` | its reply leg never parks, and its wait leg is a `Waiter.Wait` park (dispatch.saw:5439-5503) | as `Waiter.Wait`; the reply was delivered, and the value register reads `ReplyWaitLeg.Wait` |

**The fused Call's reply park, when the word is wakeable.** A plain `Call`
mints no claim handle: the parked thread is the claim **[read
threads.saw:205-225]**. A park that can end early has to give the claim back,
so a wakeable `Call` is a `Post` and a wakeable `Resolve` made in one trap:

- At the stage it mints the claim into the caller's table as a `PipeReply`
  handle, as `Post` does. The table is checked before anything is staged,
  whether the stage happens at the op or at the room park's resume
  (`table_room`, **[read kernel/core/objects.saw:607-622]**).
- **A table with no free row** (2.1) answers `QuotaExceeded` at the caller's
  Handle quota, or `NoResource` with the table full, as `Post` does.
  - Nothing is staged, and the carried handles stay the caller's: a
    transient refusal takes nothing, on `Post`'s terms
    **[read kernel/sysapi/src/pipe.saw:833-835]**.
  - From a room park's resume, the parked thread is answered the same way.
  - A plain `Call` on the same table goes through, since it mints no claim.
- It then parks on that claim as a wakeable `Resolve` does.
- On `TimedOut` or `Woken` the value register carries the claim's handle
  word. The caller holds the claim, and resolves it later.
- On a delivered reply the claim handle is spent, and nothing in the
  answer differs from a plain `Call`.

So a bounded `send` never loses a reply to a timeout. Its cost is the handle
row a `Post` costs, which is why a plain `Call` keeps no claim. The runtime
falls back to a plain `Call` on a full table (§5).

**A wakeable `Resolve` borrows its claim and spends it only at delivery.**
A plain `Resolve` unbinds the claim's handle at the park and hands its
reference to the thread **[read dispatch.saw:5160-5167]**. A wakeable one
instead takes a reference of its own for the tie and keeps the handle bound,
recording its index in the slot. The handle is the claim, so on `TimedOut`
or `Woken` the claim is still there under the same word.

- **Delivery** unbinds the handle if it still names the exchange (an index
  and generation check).
- **Another thread's `Resolve` on that handle while it is parked** is the
  `BadState` fault: a claim has one resolver.
- **Another thread's release** drops only the handle's reference. The parked
  thread's tie keeps the exchange alive, and delivery finds the handle gone
  and simply delivers.

**SMP** (the owner's rule: nothing assumes one core). Under SMP's first
stage, every kernel entry holds the one big lock **[read SMP:56-70]**.

- **The latch.** A wake's "set the latch, or end the park" and a park's
  "check the latch, then park" are two kernel sections under that lock, so
  they cannot interleave, on one core or two.
- **A cross-core wake.** A wake that readies a thread on the other core
  raises that core's `NEED_RESCHED` and sends its reschedule IPI, as every
  cross-core wake does **[read SMP:306-320]**.
- **Deadlines.** Expiry rides core 0's comparator with every Timer
  **[read SMP:336-345]**, so a core-1 thread's expiry is a cross-core wake
  and an IPI. When per-core Timers land (SMP §11), thread deadlines move with
  them.
- **The wait word** is read only by the owning thread's trap, on whichever
  core it runs.
- **Nothing more** of this design depends on there being one core.

## 4. The seams (the runtime, revised)

The bodies live in `sos.exec`, placed between `waiter` and `pipe` in the
facade's order **[read kernel/sysapi/src/lib.saw:77-109]**, and are written
over raw floor ops.

**The process-wide state:**

- one Monotonic Clock handle, got with `ClockGet` on the first clock read
  and published by compare-and-swap. With the claim of each bounded `send`
  in flight (one per thread at most), it is all the quota the executor costs
  (Q1, above);
- `REACTOR`, a static wake generation.

**The per-thread record:** each thread has one, never freed. It holds its
`id`, `parked`, `seen`, the lock pair and the depth. It keeps no kernel
object.

**The bodies:**

- **`thread_executor` / `set_thread_executor`:** the block's field. On a
  thread with no block, `set_thread_executor` panics with
  `"sos: this thread has no block; a thread that runs tasks enters one with
  sos.rt.enter_thread_block before it spawns"` (Q6). That is the runtime's
  own panic, not program code. `thread_executor` answers 0 there.
- **`clock_monotonic_nanos`:** `ClockOp.Now`, in nanoseconds, one trap
  **[read clock.saw:103-110; floor.saw:815-827]**.
- **`sleep_ns(ns)`:** zero returns at once.
  1. Compute `D = now + ns`, saturating to `NEVER_NS`.
  2. Set `parked = 1` and write `D` to the wait word.
  3. `PARK_OP` until it answers `TimedOut`. A `Woken` answer is a reactor
     wake: set `seen` to "none", so the next poll returns at once, and park
     again with the same `D`. Nothing cuts the sleep short
     **[read ABI:237-253]**.
  4. Write 0 to the word and set `parked = 0`.

  A thread with no block registers a word on its own stack for the sleep,
  parks, and unregisters it, for three traps more. It has no other word, so
  nothing is overwritten.
- **`reactor_create`:** stores `&REACTOR`; it makes nothing.
  **`reactor_destroy`:** nothing **[read std/taskgroup.saw:3218-3236]**.
- **`reactor_poll(r, timeout_ms, count_out)`:**
  1. If `gen != seen`, set `seen = gen` and answer a count of 1.
  2. A `timeout_ms` of 0 answers a count of 0, with no trap.
  3. Otherwise set `parked = 1`, then read `gen` again; if it moved, take the
     first step's answer.
  4. Write the word: `now + timeout_ms × 10^6`, or `NEVER_NS` for a negative
     timeout.
  5. `PARK_OP`.
  6. Write 0 to the word, set `parked = 0` and `seen = gen`.
  7. Answer a count of 1 for `Woken` and 0 for `TimedOut`.

  It makes two traps (the clock read and the park), and none for a zero
  timeout.
- **`reactor_wake(r)`:** `gen.fetch_add(1)`, then `THREAD_WAKE_OP(rec.id)`
  for every record whose `parked` is set. `Revoked` is ignored: that thread
  is gone. The trap is the thread-wake op, one per parked thread, made from
  any thread.
  - **The Dekker pair.** `parked` and `gen` pair up, sequentially
    consistent, as in r1. A thread that is not parked sees `gen` at its next
    poll.
  - **The latch.** A wake that races a thread's return from its park lands
    in the latch, so that thread's next wakeable park returns early, which
    ABI allows **[read ABI:1086-1095]**.
- **`lock_acquire` / `lock_release`:** unchanged from r1 (§6).
- **The no-program-code rule:** every body makes floor ops, atomics and
  `rt_alloc`, and nothing else **[read ABI:93-102]**.

## 5. The drive (revised)

`sos.floor` declares both background entries and wraps them as
`background_drive()` and `background_close()`.

**Every wrapper is already counted.** Every wrapper ends at the non-seam
`sos_syscall*` stubs, which the window fence already counts as running tasks
**[read floor.saw:28-35; LS:11145-11160]**.

**How a wrapper bounds its wait:**

1. Call `a = background_drive()`. On `-1`, make the op with the word at 0
   (plain, as today) and stop.
2. Otherwise write the word:
   - `1` (a past instant) for `0`, so a ready op answers and anything else
     answers `TimedOut` at once, and the user's event is still served once
     per turn;
   - `NEVER_NS` for `INT64_MAX`;
   - `a` itself for a deadline, since the drive's answer is already
     absolute monotonic nanoseconds **[read ABI:1523-1531]**.
3. Set `parked = 1` and check `gen`, as the poll does. Then make the op,
   which is the one trap, then write 0 to the word and set `parked = 0`.
4. On `TimedOut` or `Woken`, go back to step 1.
5. Anything else is the op's answer.

| wrapper | re-issued as |
|---|---|
| `Waiter.wait` | the same `Wait` |
| `PipeRequest.reply_wait(_with)` | after a `TimedOut` or `Woken` wait leg, a bounded `Waiter.wait`, because the reply was delivered |
| `PipeInlet.send(_with)` | a bounded `Resolve` of the claim handed back (§3); on a full table, the plain `Call` below |
| `PipeReply.resolve` | the same `Resolve` on the same word; the typed `consumes` wrapper spends the claim only at delivery |
| `Thread.join` | the same `Join` |

**`send` on a full table** (2.1). A wakeable `Call` answers `QuotaExceeded`
or `NoResource` with nothing staged and the carried handles still the
caller's (§3).

- **What the runtime does.** `send` then makes the same `Call` again with the
  word at 0: a plain `Call`, which mints no claim and goes through.
- **What it costs.** That one call is unbounded. The thread's tasks wait
  with it, as every thread's tasks waited before this design, and the next
  `send` tries the bounded form again.
- **Why the fallback is honest.**
  - Nothing was staged, so the message goes out exactly once.
  - The caller's `send` still works, and sees no status it would not have
    seen without tasks.
  - Only the bound is lost.
- **The count.** Each fallback adds one to `sos_exec_send_fallbacks()`, a
  count exported as `sos_arena_contended` is
  **[read rt/common/src/lib.saw:395-401]**, so a test or a program can see
  that it happened.
- **Why not return the status.** `send`'s callers today never see
  `QuotaExceeded` from a full table, since a plain `Call` takes no row.
  Returning it would make a program's sends fail because a task was spawned
  somewhere on the thread.

**Waits that do not drive:**

- `Lock.take`: the thread may hold another `sos.Lock` its tasks would take
  **[read spec.md:4269-4271]**. Its park is plain.
- The seams' parks: the output lock, the console's calls, the lock seam,
  heap growth. They are plain and never drive.

**Nesting and the two hazards (a held `sos.Lock` or `Section` across a
driving wrapper; a task that never parks) are as in r1.** The r1 table's
"none: unbounded" rows are gone.

## 6. The lock seams (unchanged from r1)

The word holds the holder's record address, or its odd identity on a thread
with no block.

- **Contention:** `LOCK_WAIT_OP` on the holder record's inheritance pair,
  with the wait word at 0, so the park is plain. A latched wake can never
  surface there as `Woken`.
- **Release:** gives the pair back at depth 0.
- **A holder with no block** is spun on.
- **`Revoked`** panics, naming the lock.

## 7. The close and the kernel image (unchanged from r1)

**The close.** `end_thread_tasks()` closes the group and gives the thread's
record back. It runs in `sosmain._start` after `__saw_main`, and in
`run_thread` after `run` returns; an `@export _start` program calls it
itself. `Process.exit` and `Thread.exit` do not close
**[read ABI:1545-1561; std/taskgroup.saw:4166-4180]**.

**The kernel image.** The kernel compiles `kcore`, `sosrt`, `sosabi`,
`imgformat` and `hal`, and never `sos` **[read
tools/sosimage/src/boardcheck.saw:58-62]**. So it declares neither entry, and
the `kernel_spawn_refused` case pins that **[read
tools/sos_runner.py:18311-18331]**.

## 8. Cases

### The kernel's cases

Each is a stock-root package that calls the floor ops directly. They run on
riscv32, arm64 and riscv32-flat, and in the S3 smoke. Every floor is a
one-sided reading on `Clock.now()`, and every order is fixed by construction,
using spec §7's rule that at one level a yield returns only once every
runnable thread has run until it blocked **[read CLAUDE.md, the timing
rule]**.

- **K-1 `wait_word_refusals`:** an odd address is the `BadAlign` fault; an
  address in no row is the door's fault; `THREAD_WAKE_OP` with a stock
  child's thread ID is the `BadArg` fault.
  **Mutant:** registration skips the alignment check, and the first
  half reads no fault.
- **K-2 `deadline_waiter`:** a Waiter with an Event nobody signals. The word
  is `now + 20 ms`, and `Wait` answers `TimedOut`, with at least 20 ms
  elapsed. Then:
  - a past deadline (word `1`) answers `TimedOut`;
  - with the Event signalled first, the same past word answers `Ok` with the
    record (readiness wins);
  - after the timeout the Event, signalled once, is still delivered by a
    plain `Wait` (nothing was consumed).

  **Mutants:** the readiness check placed after the deadline check makes the
  third line `TimedOut`; expiry that never unlinks the thread ends in the
  kernel's line for a stale list.
- **K-3 `deadline_join`, `deadline_lock`, `deadline_park`:** the same floor
  shape for `Join` (of a thread parked plain on an Event), `LOCK_WAIT` (a
  holder parked plain) and `PARK_OP`.
  - **`deadline_lock` also prints the holder's level** before and after the
    waiter's timeout, read as the priority cases read it: raised, then back
    to its base.
  - **`deadline_park`** runs alone in its image, with no tick: its only wake
    source is its own deadline.

  **Mutants:** without the `lock_recompute`, `deadline_lock`'s level stays
  raised; if `has_external_wake_source` ignores thread deadlines,
  `deadline_park` prints the deadlock line.
- **K-4 `deadline_pipe`:** a server child that takes and never replies. Then:
  1. a wakeable `Call` with `now + 20 ms` answers `TimedOut` with a claim
     handle;
  2. the server replies;
  3. a plain `Resolve` of that handle gets the reply;
  4. a wakeable `Resolve` that times out leaves the same handle, which a
     later plain `Resolve` resolves;
  5. a full ring with a wakeable `Call` answers `TimedOut` with `NO_HANDLE`
     and nothing staged: the server's later take finds only the messages
     before it;
  6. `ReplyWait` with a wakeable word answers the `Wait` leg's `TimedOut`.
  7. (2.1) The client child runs at its Handle quota. It mints siblings of
     one handle until a mint answers `QuotaExceeded`. Then:
     - a wakeable `Call` answers `QuotaExceeded`;
     - a plain `Call` of the same message gets its reply;
     - the server prints each message it took, and takes exactly one.

  **Mutants:**
  - A wakeable `Call` that mints no claim loses the reply: step 3 reads
    `PeerClosed`.
  - A wakeable `Resolve` that unbinds at the park makes step 4's handle a
    `BadHandle` fault.
  - A stage that runs before the table check stages the message and then
    refuses it, and the server prints two takes in step 7.
- **K-5 `thread_wake`:** thread T, at main's level.
  1. T parks wakeable in `Waiter.Wait`. Main yields (so T is parked), wakes
     T, and T prints `woken`.
  2. Main wakes T before T's next op. T's next wakeable `PARK_OP` answers
     `Woken` at once (the latch).
  3. T parks **plain** on an Event. Main wakes T, then signals the Event. T
     prints `ok` with the record, and T's next wakeable park answers `Woken`
     (the plain park kept the latch).
  4. A wake of an exited thread of the process answers `Revoked`.

  **Mutants:** a wake that ignores the plain or wakeable mode turns line 3
  into `woken`; no latch turns line 2 into a park until the harness timeout,
  and the case fails.
- **K-6 SMP:** none in this design. SMP's own `core_wake` case **[read
  SMP:500]** gains a row in that unit: a core-1 thread in a wakeable park,
  woken from core 0.

### The runtime's cases

These are r1's cases, each kept with the mutant r1 named for it.

- **R-1 `task_spawn_join`.**
- **R-2 `task_sleep_floor`:** a kernel-clock floor.
- **R-3 `task_channel_wake`:** the lock seams.
- **R-4 `task_close_at_thread_end`.**
- **R-5 `task_two_threads`:** the executor word, per thread and per HAL.
- **R-6 `task_cross_thread_wake`:** a wake from another thread ends a poll
  with a negative timeout. **Mutant:** a `reactor_wake` that does nothing
  ends in the deadlock line.
- **R-7 `sleep_ns_direct`** (t2): `main` declares
  `extern "C" { func __saw_rt_sleep_ns(ns: UInt64)
  func __saw_rt_reactor_poll(r: Int, timeout_ms: Int,
  count_out: UnsafePointer<Int>) -> Int }` and calls them in thread
  context, with no task.
  1. Thread W, at main's level, loops yielding until
     `sos_exec_parked_parks() ≥ 1` (a count of `PARK_OP`s entered, exported
     as `sos_arena_contended` is). It then calls `__saw_rt_reactor_wake` and
     exits.
  2. `main` reads `Clock.now()`, calls `__saw_rt_sleep_ns(50 ms)`, and reads
     the clock again. It prints `slept at least 50 ms: true` and
     `park woken: true` (from `sos_exec_parks_woken() ≥ 1`).
  3. `main` then calls `__saw_rt_reactor_poll(&REACTOR, -1, …)`, which
     returns at once because the wake was kept, and prints `wake kept`.

  The order is fixed by construction: W wakes only after main is counted
  parked.
  **Mutants:**
  - `sleep_ns` returning on `Woken` makes the first line `false`;
  - a `THREAD_WAKE_OP` that ends no park makes the second line `false`;
  - a `sleep_ns` that drops the wake (leaves `seen`) parks the poll forever,
    and the case ends in the deadlock line.
- **R-8 `task_bounded_waits`:** replaces r1's bridge case.
  1. `main` spawns a task that sleeps 5 ms and then signals an Event.
  2. `main` calls `waiter.wait()` on that Event and prints `woken by the
     task`.
  3. The same shape follows with `Thread.join` (a thread whose exit waits
     for the task's message) and `PipeInlet.send` (a server in the same
     process, served by the task).

  **Mutants:** a wrapper that skips the drive, or writes the word as 0,
  parks with nothing armed, and the case ends in the deadlock line.
- **R-9 `kernel_spawn_refused`.**
- **R-10 `bounded_send_table_full`** (2.1): `send` on a full table falls
  back and loses only the bound.
  - **Who plays which part.** Root is the server and prints each request it
    takes. A stock child is the client: it has a Handle quota of its own and
    one task live for the whole case (parked on a channel nobody sends to, so
    the drive answers `INT64_MAX` and every `send` takes the bounded path).
  - **Steps:**
    1. The child mints siblings of a handle until a mint answers
       `QuotaExceeded`.
    2. It calls `send(1)` and prints `reply 1`, then `fallbacks=1`, read from
       `sos_exec_send_fallbacks()`.
    3. It releases one sibling.
    4. It calls `send(2)` and prints `reply 2`, then `fallbacks=1`: with a
       row free, the bounded path served it.
  - **Expected:** root prints `took 1`, then `took 2`.
  - **Order:** fixed by construction. Each `send` returns only after root
    replied.
  - **Mutants:**
    - The runtime returns the status instead of falling back: the child
      prints `send: QuotaExceeded` in place of `reply 1`.
    - The fallback is taken on a table with room: step 4 reads
      `fallbacks=2`.
    - A wakeable `Call` that stages before its table check makes root print
      `took 1` twice.

Every runtime case also runs in the S3 smoke.

## 9. Units, spec text, and what is left out

| unit | what | size | model | after |
|---|---|---|---|---|
| S0 | spec text: the kernel's half (§2.2, §2.1, §5.7's ops and statuses, §2.6, §7, §8) and the runtime's half (§10's executor, §7's one-word lock, §8's thread end) | M | Opus | — |
| K1 | the Handle default (12 to 16, and `quotahandles`' line); the two statuses, the three ops, the `ThreadSlot` fields, the schedule and the deadlock test, wakeable `Waiter.Wait`, `Join`, `LOCK_WAIT` and `PARK`, and the floor exports (`sos_wait_word`, `sos_thread_wake`, `sos_park`); K-1, K-2, K-3, K-5 | L | Opus | S0 |
| K2 | wakeable `Call` (the room park, and the claim minted at the stage), the borrowing `Resolve`, and `ReplyWait`; K-4 | M | Opus | K1 |
| R1 | `sos.floor`'s declarations; `sos.exec`'s records, the eight seams over `PARK_OP` and `THREAD_WAKE_OP`, `end_thread_tasks`, the two counters, and the block's growth and registration; the close calls; R-1, R-2, R-4, R-5, R-6, R-7, R-9 | M (it was L) | Opus | K1 |
| R2 | the lock seams; R-3 | M | Sonnet | R1 |
| R3 | the wrapper drives (§5), and `send`'s fallback with its count; R-8, R-10 | S–M (it was M) | Sonnet | R1, K2 |

R2 and R3 touch different files and can run at once.

**Spec text:**

- **The kernel half (S0):**
  - §2.2 `Wait` and §2.1's `Call`, `Resolve` and `ReplyWait` each get their
    deadline and wake paragraph (§3's table).
  - §5.7 gets the three no-handle ops and the two statuses.
  - §2.1's `Call` says a wakeable `Call` mints its claim at the stage, so it
    spends one Handle row while it waits. With no row free it answers
    `QuotaExceeded` or `NoResource` with nothing staged, where a plain `Call`
    goes through.
  - §2.9 (quotas) gets the child Handle default of 16 (from 12).
  - §2.6 says deadlines are on the Monotonic clock.
  - §7 gets the wait word, the wake and its latch, the same-process rule, and
    a line in "Nothing runnable" for parked deadlines.
  - §8 says `Join`'s early end and that a slot's latch resets.
- **The runtime half:** §10's "The task executor", without r1's bridge or
  reserved keys, and the hazards. It also states the executor's quota: one
  Handle row per process for the Clock, and one per thread while that
  thread's bounded `send` waits, for the claim. On a full table, `send`
  falls back to a plain `Call` and loses only the bound. §11 lists the thread gate below.

**Left out, as decided:**

- **The thread gate** (Q4, a follow-up issue). A thread-context
  `Channel.receive` and a `TaskGroup(threads:)` still fail at link on
  `__saw_rt_pthread_mutex_init_default`, `_cond_init_default`,
  `_mutex_lock`, `_mutex_unlock`, `_mutex_destroy`, `_cond_wait`,
  `_cond_broadcast` and `_cond_destroy` **[probed: a/chan.saw]**. A
  `TaskGroup(threads:)` adds `__saw_rt_thread_spawn` and `_join` too
  **[read ABI:1130-1135]**.
- **Deadlock reports** (Q5): a process whose tasks all wait forever parks,
  and only the kernel's line reports it.

## 10. Questions for the owner

1. **A typed deadline for programs.** The kernel would offer a deadline and
   a wake on every wait, but only the runtime uses them here. Should `sos`
   also give programs a typed form, for example `Waiter.wait(until:
   Instant)` and `Thread.wake()`, now or as a follow-up?
2. **The cost of a bounded `send`.** A wakeable `Call` mints its claim at
   the stage (§3), so a bounded `send` costs one handle row while it waits.
   A full table refuses it before anything is staged, as `Post` does, and the
   runtime then falls back to a plain `Call` (2.1, §5). Accept that, or keep
   the `Call` reply park unbounded and let the runtime compose `Post` and
   `Resolve` itself? That costs the same row, in one more trap.
3. **The t2 reading.** ABI says nothing cuts `sleep_ns` short. R-7 pins that
   a mid-sleep wake ends the kernel park, is absorbed, is kept for the next
   poll, and does not shorten the sleep. If the reviewer meant the wake to
   end the sleep itself, that is an ABI change for sawlang, not a sawos
   choice.
