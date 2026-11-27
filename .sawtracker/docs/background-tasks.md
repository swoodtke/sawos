# SO-141 design: sosrt runs Saw's background tasks

Claims are marked **[read F:L]** (read in the tree at that place), **[probed]**
(a compile I ran; sources under
`/private/tmp/claude-501/-Users-shawn-Projects-sawos/34c0d76b-723c-482d-939f-716d368f6e5c/scratchpad/so141/`)
or **UNVERIFIED**. `ABI` is sawlang v0.24.5's `runtime/ABI.md` and `LS` its
`LANGUAGE_SPEC.md`, both in `.toolchains/cb66b632…/`. No QEMU probe was run.

## 0. What the probes settled first

- With both entries declared, a sawos `main` program that calls `Task.spawn`
  compiles against the real tree (`sosmain`, `sos`, `sosrt`, …, freestanding,
  `--runtime`, riscv32). The only `__saw_rt_*` symbols left undefined are
  the eight seams of the executor set: `thread_executor`,
  `set_thread_executor`, `clock_monotonic_nanos`, `sleep_ns`, and
  `reactor_create`, `_poll`, `_wake` and `_destroy` **[probed: b/main.saw]**.
  So the issue's "fails at link" is exactly those eight.
- Adding a channel adds `lock_acquire` and `_release` **[probed: a/chan2.saw]**.
  A `Channel.receive` in thread context also reaches std's thread gate, the
  eight `__saw_rt_pthread_*` seams **[probed: a/chan.saw]**. Those are not
  in the set this unit builds (Q4).
- Without the declarations, `Task.spawn` is refused with `spawn.freestanding`
  **[probed: a/nodecl.saw]**.
- One module may declare both entries while another declares only the drive,
  and it compiles **[probed: c/]**.
- A `sync` function may call `__saw_background_close` **[probed: a/probe.saw]**.
  A `sync` body cannot join a task (`effect.sync`), but it may call a
  `blocking` helper that does **[probed: a/syncspawn.saw]**. A
  `thread_create(run:)` entry is `sync`
  **[read kernel/sysapi/src/system.saw:920]**, so a thread that joins tasks
  does it in a `blocking` helper.

Two of the issue's premises are stale:

- **arm64's `TPIDR_EL0` already travels with each thread.** No kernel change is
  needed. Every switch saves it into the thread's FP area at
  `THREAD_REGISTER_OFFSET` (536) and loads the incoming thread's, which is zero
  for a new thread **[read hal/arm64/kernel/lib.saw:136-147, 738-743,
  800-829]**. riscv32 and riscv32-flat save `tp` in the trap frame
  **[read hal/riscv32-common/kernel/trap.S:67,128;
  hal/riscv32-common/kernel/lib.saw:87]**. The S3 saves `THREADPTR` there
  too **[read hal/xtensa-esp32s3/kernel/boot.S:620-621, 690-691]**.
- **The thread register is already taken.** It holds the thread's lock-owner
  identity: the address of its `ThreadBlock`, or an odd number. That word is
  written once and must never change **[read kernel/sysapi/src/rt.saw:175-214]**.
  So it cannot also be the executor word.

## 1. The executor word

**Where it lives: a new field of `ThreadBlock`.** The thread register names
the block, and `thread_executor` is one register read and one load. It is the
hottest seam (std reads it on nearly every task operation
**[read std/taskgroup.saw:1309, 2026, 4362 and others]**), so it has to be
cheap and must not trap or take a lock.

The alternatives:

- The thread register itself is taken (above).
- A table keyed by thread ID needs a lock or a hash on every read, and a bound.

ABI asks only that the word be per thread and never freed by the runtime
**[read ABI:951-973]**. std's record outlives the thread in std's registry,
and the block lives as long as its thread.

`ThreadBlock` grows from `{thread, id}` to `{thread, id, executor, record}`.
`record` points at the thread's runtime record (§2). `StartRecord`, which
starts with the block, grows by two words, so the "four words" in
`thread_create(run:)`'s documentation becomes six
**[read rt.saw:282-285, 333-343; system.saw:905]**.

**The kernel change per HAL:** none (§0).

**Which threads have a word:**

| thread | block | executor word |
|---|---|---|
| root's first thread, and any `main` program's main thread | in `sosmain._start`'s frame **[read rt/main/src/lib.saw:131-132]** (root uses `sosmain` **[read root/src/main.saw:71]**) | yes |
| a `thread_create(run:)` thread | head of its start record **[read rt.saw:407-408]** | yes |
| a `thread_create(entry:)` / `(stack_top:arg:)` thread, an `@export _start` program's main thread | none, unless its code calls the public `sos.rt.enter_thread_block` with a block it keeps for the thread's life | no |

The bodies:

- `thread_executor`: reads the register. A zero or odd word answers 0.
  Otherwise it answers `block.executor`.
- `set_thread_executor`: stores into the block and answers 0. With no block
  it answers `-1`.

On `-1`, std panics with "executor: the runtime cannot keep this thread's
executor record (no thread-specific word)" **[read std/taskgroup.saw:1314-1316]**.
So a spawn on a thread without a block is a clean panic, never a record shared
with another thread, which ABI forbids **[read ABI:970-973]** (Q6).

## 2. The seams

### The thread's runtime record

The record is per thread and runtime-owned. It holds:

- `next`, the registry link;
- `claimed`;
- the poll set: a Waiter `W`, a Timer `T` and an Event `E`, with `T` and `E`
  attached to `W` persistently under the record's two keys;
- the bridge set: a Timer `Tb` and an Event `Eb` (§3);
- `parked`, the handle of the Event the thread is parked with right now, or 0;
- `seen`, the last wake generation this thread acted on;
- the lock pair (§2, the lock seams) and its depth.

**Where records live.** Records are allocated through the runtime's own
allocator (`rt_alloc`, called as a function) and pushed with a compare-and-swap
onto a process-wide list that only ever grows. Like std's own records, they are
never freed **[read std/taskgroup.saw:1323-1330]**. So a stale pointer from
another thread always lands in a valid record.

**How a thread gets one.** The thread claims a record at its first blocking
poll, sleep, bridged wait or lock acquire: it compare-and-swaps `claimed` on a
free record, or makes a new one, and keeps the record's address in
`block.record`. The kernel objects are made lazily: the poll set at the first
blocking poll or sleep, the bridge set at the first bridged wait.

**When it is released.** A record goes back to the free list at the thread's
close (§4). Its kernel objects stay with it for the next thread. If the thread
ended holding a runtime lock (lock depth above 0), the record is never
released.

**Its quota:**

- The poll set costs 1 Waiter, 1 Event, 1 Timer and 3 Handle rows.
- The bridge set costs 1 Event, 1 Timer and 2 Handle rows more.
- Per process, two handles are minted once and kept: a Process sibling
  (`SystemOp.ProcessSelf` on the parked System handle, `SYSTEM_HANDLE`
  **[read floor.saw:1585]**) to create Waiters and Events, and a Monotonic
  Clock (`SystemOp.ClockGet`). Each is published by compare-and-swap, and a
  thread that loses the race releases its spare.
- Root's rows are unlimited **[read spec.md:2055]**. A child's defaults are
  Waiters 2, Events 4, Timers 4 and Handles 12
  **[read kernel/core/limits.saw:506-544]**, so two task-running threads use
  both default Waiters (Q1).

**What it needs.** The rights it spends (`ProcessSelf`, `ClockGet`,
`EventCreate`, `WaiterCreate`, and `Read`, `TimerCreate`, `Arm`, `Wait`,
`Attach`, `Signal`) are read before they are spent, as `sosmain` does. A
missing right, a quota refusal, or no System handle at all is a panic from the
seam that names the missing piece. A panic is the runtime's own code, not the
program's.

### The bodies

Every op below is a raw floor op on handle words, as the growth hook is
written **[read rt.saw:613-627]**. None calls a typed wrapper, because
wrappers drive (§3).

- **`thread_executor`, `set_thread_executor`:** §1. No trap.
- **`clock_monotonic_nanos`:** `ClockOp.Now` on the process's Monotonic Clock
  (`sos_clock_now`). It answers nanoseconds, a 64-bit copy-out record,
  doubleword `CLOCK_NOW_TIME_SLOT` **[read clock.saw:52-60, 103-110;
  floor.saw:815-827]**. That is one trap, plus `ClockGet` on the first call.
  It is the kernel's own unit, so nothing is scaled.
- **`sleep_ns(ns)`:**
  - Zero returns at once.
  - On a thread with a record, it arms `T` for `ns` and waits on `W` until a
    record with `T`'s key arrives.
  - An `E` record that arrives first is a reactor wake. The sleep does not end
    on it, but sets `seen` to "none" so the next poll returns at once, and
    waits again. The wake is not lost, and the sleep is not cut short
    **[read ABI:237-253]**.
  - A thread with no record makes a temporary Waiter and a Timer it gives to
    the Waiter, waits, and releases the Waiter.
  - Whether the kernel takes an `after_ns` near `u64::MAX` is
    **UNVERIFIED**. If it refuses, the sleep is made in chunks.
- **`reactor_create(r_out)`:** stores the address of one static `REACTOR`
  (a wake generation, `Atomic<Int>`) and answers 0. It makes no kernel object.
  Every thread's objects are its own.
- **`reactor_destroy`:** nothing. The loser of std's publish race destroys
  the same static it got **[read std/taskgroup.saw:3218-3236]**.
- **`reactor_poll(r, timeout_ms, count_out)`:**
  1. `g = REACTOR.gen`. If `g != rec.seen`, set `seen = g`, store a count of
     1 and answer 0. On a thread with no record, `seen` is taken as "none".
  2. A `timeout_ms` of 0 stores a count of 0 and answers 0, with no trap. This
     is the drive's per-turn poll **[read ABI:1491-1493]**.
  3. Otherwise claim the record and its poll set. Arm `T` for
     `timeout_ms × 10^6` ns when the timeout is positive. A negative timeout
     leaves `T` disarmed.
  4. Set `parked = E`, then read `gen` again. If it changed, set `parked = 0`,
     store a count of 1 and answer 0.
  5. Call `sos_waiter_wait(W)`, then set `parked = 0` and
     `seen = REACTOR.gen`, and disarm `T`.
  6. Store a count of 1 for an `E` record and 0 for a `T` record, and answer 0.

  A failed wait (`Revoked`) is a broken instance, and the seam panics, naming
  the status. A negative timeout with nothing that can ever wake parks for
  good. When that leaves the whole machine with nothing runnable, the kernel's
  deadlock line reports it **[read spec.md:4091-4111]**. ABI's
  single-threaded panic does not apply, because another thread may wake the
  poll (Q5).
- **`reactor_wake(r)`:** `REACTOR.gen.fetch_add(1)`. Then, for every record
  on the list whose `parked` is nonzero, `sos_event_signal(parked, 1)`.
  - This is the broadcast ABI asks for **[read ABI:995-1004, 1086-1095]**.
    Every parked poller and every bridged waiter returns.
  - A thread that is not parked meets the new generation at its next poll, so
    a wake with nothing parked is not lost.
  - The waker needs no block and no record, so it can be any thread,
    including one the executor knows nothing about.
  - Signalling never parks and never fails for room
    **[read event.saw:59-66]**.
  - `parked` and `gen` are a Dekker pair, all sequentially consistent: the
    poller stores `parked` and then reads `gen`, and the waker writes `gen` and
    then reads `parked`. So at least one of them sees the other. An Event left
    holding bits makes one later poll return early, which ABI allows.
- **`lock_acquire(state)`, `lock_release(state)`:** one word, with zero meaning
  free **[read ABI:1267-1288]**.
  - **The word.** While the lock is held, the word holds the holder's record
    address. On a thread with no block it holds the holder's odd identity
    instead (`rt_lock_owner`).
  - **Acquire.** Compare-and-swap the word from 0 to `me`. On success, bump
    `rec.depth`. When depth goes from 0 to 1, take the record's own
    inheritance pair with the thread's ID. Nobody else ever takes that pair,
    so this never waits.
  - **Contention.** On failure, read the holder `h`:
    - An even `h` is a record: `LOCK_WAIT_OP` on `h`'s pair. The kernel
      raises the holder to the waiter's level and answers once the holder has
      given back every runtime lock it holds. Then the waiter swaps again.
    - An odd `h` is a holder with no block: yield and swap again.
  - **Release.** Compare-and-swap the word from `me` to 0 and drop the depth.
    At zero, give the pair back (`LOCK_WAKE_OP` if its waiters flag is set).
  - **Why a pair in the record.**
    - Inheritance comes from spec §7's own lock, so a waiter above a preempted
      holder parks instead of spinning on it **[read spec.md:4237-4302]**.
    - A stale `h` is always valid memory, because records are never freed.
    - The `Mutex` user's window may park (a print), which a section-guarded
      spin would turn into a `SectionHeld` fault **[read spec.md:4185-4192]**.
  - **Reuse.** A reused record hands a waiter the wrong thread's progress to
    wait for. That is spurious but it ends, and the waiter swaps again.
  - **The hazards, stated, not fixed:**
    - A holder with no block is spun on, like the arena when no section word
      is registered **[read rt/common/src/lib.saw:380-384]**.
    - A holder that exits holding a lock leaves it held for good. `Revoked`
      from the wait panics, naming the lock, as the output lock's wait does.
- **The no-program-code rule** **[read ABI:93-102]**. Every body above makes
  floor ops (the `sos_syscall*` C stubs), atomics and `rt_alloc`, and nothing
  else. None calls a typed wrapper, a drive or a callback. `rt_alloc` takes
  only `SpinLock`s, never this seam, so `lock_acquire` cannot recurse.

**The kernel image keeps none of this** (§6). The bodies live in a new `sos`
module, `sos.exec`, not in `sosrt`.

## 3. The drive

**The entries live in `sos.floor`.** It declares both entries in one
`extern "C"` block, beside the syscall externs, and wraps them as
`public(package) background_drive()` and `background_close()`. The floor is
compiled into every process. The claim is then the program's, decided at the
link **[read ABI:1456-1466]**, and every wrapper reaches the drive without an
import cycle. Only `sos.exec` (`end_thread_tasks`, §4) calls the close.

**The rule.** A seam never drives; only the typed wrappers do
**[read ABI:1534-1538]**.

**Every wrapper is already counted.** Every `sos` wrapper ends at
`sos_syscall1`/`sos_syscall3`, the user HAL's C stubs, which are externs with
no body in the program and not seams **[read floor.saw:28-35]**. So the
compiler's window fence already counts each one as running tasks
**[read LS:11145-11160]**, and adding the drive refuses nothing new there.

**How a wrapper drives.** A wrapper calls `background_drive()` before it parks
and reads the answer **[read ABI:1523-1531]**:

- `-1`: it parks as it does today.
- `0`: it drives again. A bridged wrapper first makes a zero-length bridged
  wait, so a ready user event is still served between turns and a task that
  never parks cannot starve it.
- `INT64_MAX`: the wait must end on a reactor wake.
- `D`: the wait must end by the monotonic instant `D`. The Timer is armed for
  `D` minus the clock's reading, floored at 0.

**The bridge.** A bridged wait parks on the waited-on Waiter with the thread's
bridge set attached, so either one ends it:

1. `Waiter.add` the bridge Timer `Tb` and Event `Eb` to the caller's Waiter,
   persistent, under the record's two runtime keys.
2. Arm `Tb` by the answer.
3. Set `parked = Eb` and check `gen`.
4. Wait.
5. Set `parked = 0`.
6. Remove both keys, unless the wait answered `Revoked`, which releases them.
7. Disarm `Tb`.

**What the wait's answer means:**

- A user key returns the record to the caller.
- One of the thread's own keys means it drives again.
- Another thread's runtime key reached the wrong thread: several threads park
  on one Waiter, and a delivery wakes one of them **[read waiter.saw:72-75]**.
  The receiver forwards it by signalling the owner's `Eb` (named by the key),
  then drives again.

**The keys.** Runtime keys have the top bit of the word set and encode the
record's index and which object fired. A user key equal to one is the kernel's
key-reuse termination **[read waiter.saw:118-121]**. So the top half of the key
space becomes the runtime's on any Waiter a thread with live tasks waits on
(spec text, Q3).

**When the bridge cannot be attached.** A caller's Waiter without `Attach`
answers the add with a status (**UNVERIFIED**: `AccessDenied`, as other ops
answer). Then the wrapper falls back to the unbounded form.

**Cost.** A bridged wait costs 6 traps instead of 1: 2 adds, the arm, the wait,
2 removes, and a clock read for a deadline. That is paid only while tasks are
live.

**Per wait:**

| wait | drives? | bound |
|---|---|---|
| `Waiter.wait` | yes | the bridge on the caller's Waiter |
| `PipeRequest.reply_wait(_with)` | yes | with tasks live, split into `reply` (never parks) and a bridged `Waiter.wait`. The error legs keep their meaning: `Reply(s)` from the reply, `Wait(s)` from the wait |
| `PipeInlet.send(_with)` (the fused Call) | yes, until the answer is not 0 | none: it parks on the reply inside the kernel, with no Waiter to bound (Q2) |
| `PipeReply.resolve` | yes, until not 0 | none. Attaching the claim to the thread's own Waiter could meet a claim the caller already attached, which the kernel answers by ending the process **[read waiter.saw:72-75]** (Q2) |
| `Thread.join` | yes, until not 0 | none: a Thread is no Waiter-attachable kind **[read waiter.saw; system.saw, `add(process:)`]** (Q2) |
| `Lock.take`'s park | **no** | the thread may hold another `sos.Lock` its tasks take, and a re-take is the kernel's `BadState` fault **[read spec.md:4269-4271]**. A lock's hold is short |
| the output lock's park (`rt_output_lock`) | **no**: it is a seam | tasks wait while the holder finishes one line |
| the console's Call on a full ring, and `console_sync` (inside `rt_write` and `rt_abort`) | **no**: seams | tasks wait for the driver |
| a pool heap's growth (inside `rt_alloc`) | **no**: a seam | it never parks: every op answers at once **[read rt/common/src/lib.saw:780-787]** |
| `reactor_poll`, `sleep_ns`, `lock_acquire` | **no**: seams | std's own sweep around them runs the tasks |

**Nesting.** A wrapper reached inside a task's turn makes a nested drive,
which skips the tasks further up the stack and never answers `-1`
**[read ABI:1538-1543]**. So a task in `Waiter.wait` always bridges. Each
level of nesting holds a turn's frames on the thread's stack, which matters on
the S3's stacks.

**Two hazards for the spec text:**

- A thread that holds a `sos.Lock`, or is inside a `Section`, across a driving
  wrapper runs its tasks there. A task that takes the same lock ends the
  process. Parking inside a section already does
  **[read spec.md:4185-4192]**.
- A wrapper's drive answering `0` forever (a task that never parks) keeps an
  unbridged wrapper from reaching its op, as any cooperative thread would.

## 4. The close

`sos.exec` gains a public `end_thread_tasks()`. It calls
`__saw_background_close()` and then gives the thread's runtime record back.
It is called:

- **`sosmain._start`**, after `__saw_main()` returns and before `flush`/`end`
  **[read rt/main/src/lib.saw:142-145]**. `__saw_main` has already closed what
  `main` spawned **[read ABI:1414-1416, 1476-1478]**, so this close is almost
  always empty. ABI still names this point **[read ABI:1556-1559]**, and it
  gives the record back.
- **`sos.rt.run_thread`**, after `run(record.arg)` returns and before
  `sos_thread_exit` **[read rt.saw:407-420]**. An abandoned record spawned
  nothing and skips it.
- **An `@export _start` program** owns its threads' ends. It calls
  `sos.exec.end_thread_tasks()` as each thread's last Saw code. A thread that
  ends without it abandons its tasks with the thread, as `Process.exit` does.

**Why `Process.exit` doesn't close.**

- The kernel's teardown frees every object and the whole region at once
  **[read system.saw, `exit`]**, so nothing a close would release outlives it.
- A close runs program code: cancelled tasks finish through their own code.
- A close can wait without bound: a task that ignores its cancel, or one
  parked in a join, holds it open **[read ABI:1549-1553; LS:12406-12413]**.
- `exit` is often called inside a task's turn, where a close panics
  **[read std/taskgroup.saw:4178-4180]**.

`Thread.exit` doesn't close either, for the last two reasons. A thread that
exits that way leaves its std record and its runtime record claimed.

## 5. Threads

**Each thread drives its own group, and nothing assumes one thread per process
or one core** (the owner's SMP rule):

- The executor word is read and written only by its own thread.
- The record list grows by compare-and-swap and is read with atomic loads.
- `claimed`, `parked`, `seen` and `gen` are atomics with sequentially
  consistent ordering where they pair up (the wake Dekker in §2).
- The two process-wide handles are published by compare-and-swap.
- The lock seam is a compare-and-swap on the word plus a kernel park.
- The forwarding in a bridged wait assumes only that the kernel hands a ready
  record to some parked thread **[read waiter.saw:72-75]**. Which thread is
  **UNVERIFIED**, and nothing here depends on it.
- Nothing relies on the uniprocessor kernel **[read spec.md:4349-4352]**.

**`reactor_wake` from another thread:** it bumps the generation and signals
the Event of every thread parked in a poll or a bridged wait. Each such wait
returns, and that thread's executor looks again. A thread between parks sees
the generation at its next poll. The waker may be any thread, one with no
block included, since it touches no record of its own.

There are no user-mode interrupt handlers in SOS: an Interrupt is a waitable.
So ABI's "never from a handler" rule **[read ABI:1482-1485]** has no site.

## 6. The kernel image

The kernel compiles `kcore`, `sosrt`, `sosabi`, `imgformat` and `hal`, and never
`sos` **[read tools/sosimage/src/boardcheck.saw:58-62]**. The declarations
(`sos.floor`) and the executor set (`sos.exec`) are both in `sos`, so a kernel
declares neither entry, and a `Task.spawn` there is refused
(`spawn.freestanding`, **[probed: a/nodecl.saw]**).

`sosrt` takes none of this for a second reason too: the kernel harness entry
`tests/taskdump.saw` exports its own reactor, clock and executor-word stubs
**[read tests/taskdump.saw:34-125]**, and a second export of the same symbols
from `sosrt` would collide.

**The guard** is a kernel-entry case, `kernel_spawn_refused`: a kernel source
with one `Task.spawn`, and `expect_build_error: "spawn.freestanding"`.
Whether `_expected_build_failure` routes a kernel compile's failure the way it
routes a package's is **UNVERIFIED** **[read tools/sos_runner.py:1538-1550]**;
the unit adds the routing if not. Mutant: move the declarations into `sosrt`,
and the case goes red (the kernel compiles).

## 7. Cases

Every case runs as root, or as a stock root's child where noted, on riscv32,
arm64 and riscv32-flat. Each package carries a `[sos.xtensa-esp-unknown-elf]`
section and joins the S3 smoke. None reads the clock to decide what it prints,
except one one-sided floor.

1. **`task_spawn_join`** — `main` spawns `work(21)`, joins it and prints
   `joined 42`. **Mutant:** `set_thread_executor` answers `-1`. std panics
   (§1), and the transcript has the panic line in place of `joined 42`.
2. **`task_sleep_floor`** — the floor is read on the kernel's clock, beside the
   runtime's.
   - **Steps:** `main` reads `Clock.now()` (the typed op), spawns a task that
     calls `sleep(Duration.ms(20))`, joins it, and reads `Clock.now()` again.
   - **Prints:** `slept at least 20 ms: true`.
   - **Why it is safe:** a stalled host only raises the elapsed time.
   - **Why the kernel's clock:** std compares the deadline with the runtime's
     clock, so a poll that returns early cannot break the floor. Reading the
     kernel's clock is what pins the seam's units.
   - **Mutant:** `clock_monotonic_nanos` answers the reading shifted left by
     10 bits. Time then runs 1024 times fast, the task wakes after about
     20 µs, and the case prints `false`.
3. **`task_channel_wake`** — task A receives on an empty channel; task B,
   spawned after A, prints `sending` and sends 7; A prints `got 7`.
   - **Order:** a drive's turn resumes tasks in slot order (sawlang's
     `background_turn_*` cases), so A parks before B runs
     (**UNVERIFIED** for the sweep inside a thread-context join; the lines
     `a parks`, `sending`, `got 7` are asserted in order, which fails loudly if
     it is not).
   - **Mutant:** `lock_release` leaves the word set. The next acquire finds
     its own record as the holder and waits on its own pair, which is the
     kernel's `BadState` fault, so the case goes red.
4. **`task_close_at_thread_end`** — the close runs at a thread's end.
   - **Steps:** a `thread_create(run:)` thread spawns a detached task that
     loops `while not cancelled() { yield_now() }`, then prints `bg cancelled`.
     The thread's `run` returns at once, and `main` joins the thread and prints
     `thread joined`.
   - **Order:** the task's only turns are inside the close, which runs before
     the thread exits, so the order is fixed.
   - **Mutant:** remove `end_thread_tasks()` from `run_thread`. No
     `bg cancelled` appears.
5. **`task_two_threads`** — two threads, each with its own group.
   - **Steps:**
     1. `main` spawns task M, which receives on channel `c1` and prints
        `main: m=<v>`, with -1 for an `Err`.
     2. `main` starts thread T and joins it.
     3. T's `blocking` helper spawns X (`work(21)`), joins it and prints
        `t: x=42`. Then `run` returns, and the close runs.
     4. `main` sends 7 on `c1` and joins M.
   - **Expect:** `t: x=42`, then `main: m=7`.
   - **Mutant:** keep the executor word in one static for every thread (or a
     HAL that stops switching the thread register). T then shares `main`'s
     record, and T's close cancels M, so the line reads `main: m=-1`.
6. **`task_cross_thread_wake`** — a wake from another thread ends a poll.
   - **Steps:**
     1. Thread T's helper spawns R, which receives on channel `c` and prints
        `r got 9`.
     2. T joins R, so T's sweep parks in a poll with a negative timeout.
     3. `main` yields until `sos_exec_parked_polls()` is at least 1. That is a
        count of blocking polls entered, exported as `sos_arena_contended` is
        **[read rt/common/src/lib.saw:395-401]**.
     4. `main` sends 9 and joins T.
   - **Order:** by construction. `main` sends only after T has entered the
     poll, and the generation check covers the gap before its kernel wait.
   - **Mutant:** `reactor_wake` does nothing. T stays parked, `main` parks in
     the join, and with no Timer armed the kernel prints its deadlock line
     **[read spec.md:4091-4111]**.
7. **`task_bridge_wait`** — `Waiter.wait` drives, and its bridge arms the
   deadline.
   - **Steps:** `main` makes an Event and a Waiter and attaches the Event.
     It spawns a task that sleeps 5 ms and then signals the Event. Then
     `main` calls `waiter.wait()` and prints `woken by the task`.
   - **Order:** nothing else signals the Event.
   - **Mutant:** `Waiter.wait` doesn't drive, or the bridge leaves `Tb`
     disarmed. Either way `main` parks with no armed Timer, and the kernel
     prints its deadlock line.
8. **`kernel_spawn_refused`** — §6.
9. **Every profile, and the S3** — cases 1–7 run on all three gate profiles
   and in the S3 smoke. Case 5 is the one that pins each HAL's per-thread
   thread register.

`sleep_ns` is reached by std's deadlock walk naps
**[read std/taskgroup.saw:1155, 1201, 3161]**, and no case above pins it
alone. A mutant that returns at once would pass every case. A case that pins
it would need a thread-context `sleep` reaching the seam; whether one exists
outside an executor is **UNVERIFIED** (std's `sleep` is a task park,
**[read std/task.saw:62-69]**).

## 8. Units

| unit | what | size | model | after |
|---|---|---|---|---|
| U0 | the spec text below | S | Opus | — |
| U1 | `sos.floor`'s declarations and the drive/close wrappers; `sos.exec`: the record list, the eight seams, `end_thread_tasks`, `sos_exec_parked_polls`. `ThreadBlock` grows and moves to `sos.exec`, which sits between `waiter` and `pipe` in the facade's order **[read kernel/sysapi/src/lib.saw:77-109]**. Close calls in `run_thread` and `sosmain`; the doc updates (StartRecord's size, `sosrt`'s "Not implemented here" list). Cases 1, 2, 4, 5, 6 and 8 | L (about 450 lines of Saw, 5 test packages) | Opus | U0 |
| U2 | the lock seams over the record's pair; case 3 | M (about 150 lines) | Sonnet | U1 |
| U3 | the wrapper drives: the bridge in `Waiter.wait` and `reply_wait`, and drive-until-not-0 in `send`, `resolve` and `join`; case 7 | M (about 250 lines) | Opus | U1 |

U2 and U3 touch different files (`sos.exec`'s lock section versus `pipe.saw`
and `thread.saw`), so they can run at once.

**Spec text (U0):**

- **§10** gets a built section before HandlerGroup, "The task executor". It
  states:
  - the record and what it costs in quota;
  - which threads can run tasks;
  - the reactor's generation and wake;
  - the wrappers that drive and how each bounds its wait, as the table
    in §3;
  - the reserved key half;
  - the two hazards;
  - the close points, and why `Process.exit` and `Thread.exit` don't close.
- **§7, "The runtime's locks"** gains the one-word lock: holder record, the
  inheritance pair, and the spin for a holder with no block.
- **§8** gets one sentence at a thread's end: the runtime closes a `main`
  thread's and a `run:` thread's tasks; an `@export _start` program calls
  `end_thread_tasks`.
- **§11:** the thread gate seams (Q4), and the waits that cannot be bounded
  (Q2).

## 9. Questions for the owner

1. **Quotas.** A thread that runs tasks costs a child 1 Waiter, 1 Event,
   1 Timer and 3 handles, plus 1 Event, 1 Timer and 2 handles once it makes a
   bridged wait, and 2 handles per process. The default Waiter quota is 2.
   Raise the child defaults, or leave it to launchers?
2. **Waits with no bound.** `Thread.join`, `PipeReply.resolve` and
   `PipeInlet.send` cannot bound their waits, so tasks with deadlines are late
   until the wait ends. Accept that? The alternatives:
   - (a) with tasks live, split `send` into a post and a bridged wait on the
     fresh claim, which costs a claim handle;
   - (b) make a Thread's exit a Waiter-attachable level (a kernel change), so
     `join` bridges.
3. **Reserved keys.** May the runtime reserve keys with the top bit set on a
   Waiter that a thread with live tasks waits on? Without it, `Waiter.wait`
   cannot bound its wait.
4. **The thread gate.** A `Channel.receive` (and a `TaskGroup(threads:)`) in
   thread context reaches std's thread gate, the `__saw_rt_pthread_*` seams,
   and still fails at link. File it as a follow-up?
5. **Deadlock reports.** A process whose tasks all wait on nothing that can
   come parks quietly in its poll. The kernel's deadlock line reports it only
   when the whole machine stops. Is that enough, or should the runtime count
   its parked threads and report?
6. **Threads with no block.** A spawn on a thread with no block panics with
   std's text ("no thread-specific word"). Keep that, or have
   `set_thread_executor` panic with a sawos message naming
   `enter_thread_block`?
