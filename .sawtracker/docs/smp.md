# Using the ESP32-S3's second core: a design

Revision 3, Nov 26. A design only: nothing here is built until the owner and
the reviewer have read it. Written against main at `76460836`, with SO-148's
inheritance lock (`.p4` r6, approved, in the merge queue) taken as landed.
Revisions 1 and 2 are kept in scratch.

**Changes in revision 3**, from the owner's rulings (SO-170):

- **Nothing may rely on the limits of the first stage.** Placing a process on
  one core is policy, not a guarantee: no kernel, runtime or driver code may
  assume two threads of one process never run at once. §3 says what each
  mechanism promises on full SMP, and what changes so it holds.
- **The first stage is trimmed** to: core 1 up on the S3 and two harts on
  riscv32 in the gate; one big lock and one shared kernel stack; per-core
  ready lists, tick and idle, and the reschedule IPI; processes placed by
  their launcher; core 0 serving every Timer and every device line; the
  panic stop. Everything else is the "later" list (§11).
- **One shared kernel stack, and a small entry stack per core** (the owner's
  proposal). It holds against the code, with two changes it needs: the idle
  loop leaves `pick_next`'s call chain, and a teardown whose process is
  running on the other core is never waited for with frames live (§2).

Markers. **[read F:L]** is taken from that file and those lines (paths from
the sawos root; `IDF/` is ESP-IDF tag v5.4.1, `EQ/` is espressif/qemu tag
`esp-develop-9.2.2-20250817`, the build installed here, both fetched into
scratch; `ROM` is the mask ROM's ELF, `esp32s3_rev0_rom.elf` from
esp-rom-elfs 20241011, by address; `PS:` is the tracker doc
`SO:priority-scheduler`). **[probed]** is something I ran. **UNVERIFIED** is
neither.

---

## 0. The first stage

- **One kernel image on both cores, under one big kernel lock.** Every
  kernel entry takes it; the user-return and the idle sleep give it back.
- **One full kernel stack**, used only by the lock holder, and **an entry
  stack per core** for saving the user context, spinning for the lock,
  idling and stopping (§2).
- **Per core:** ready lists (the eight levels and the bitmap), the running
  thread, the tick, idle, and a reschedule IPI.
- **Placement is a launcher's policy:** `ProcessOp.Core(core)` between the
  create and the start; unset, a child runs on its launcher's core, so an
  image that places nothing is today's machine with core 1 idle. In this
  stage every thread of a process runs on the process's core, but nothing
  relies on it (§3).
- **Core 0 serves every Timer and every device line;** a wake for a thread on
  core 1 is an IPI.
- **The panic stops the other core first** (§6).

What it gives the projects: separate programs on separate cores.

---

## 1. Locking

**One lock**, a ticket (`next`, `serving`) in kernel DRAM, taken at every
kernel entry and given back at every exit. The kernel is not preemptible,
its sections are short but for a child's load, a teardown and a console
line, and the object graph is one web (one wake walks a Waiter, an
attachment, a thread, a ready list and a process's memory [read
kernel/core/preempt.saw:11-14]). One lock turns each `unsafe static var`'s
"uniprocessor" argument [read kernel/core/threads.saw:252-253,
kernel/core/irq.saw:110-120] into "under the kernel lock" and nothing else.
Its cost is that a core waits for the other core's longest section; that is
measured later (§11). A preemption point keeps the lock: it delivers this
core's lines and asserts nothing about another core's syscall [read
spec.md:4633-4635].

**Per core, written by its own core only:** `CURRENT_THREAD`, `SLICE_LEFT`,
`SLICE_ENDED`, `PENDING_FREE`, `LAST_PROT_PROCESS` [read
kernel/core/threads.saw], the tick's `NEXT_TICK_NS` [read
kernel/core/time.saw], `RUNNING_PROCESS` (§2.3), and the HAL's per-core
register shadows (the S3's `INTENABLE_SHADOW`, `LINE_CPU_INT` [read
hal/xtensa-esp32s3/kernel/lib.saw]; riscv's PMP shadows).
**Per core, written by either under the lock:** `READY_HEAD`, `READY_TAIL`,
`READY_BITS`, `NEED_RESCHED`. **Shared, under the lock:** every slab, the
pending-death lists, the stats region, the Timer comparator and its shadows,
the console. `IN_DELIVERY` [read kernel/core/irq.saw:162-168] stays one
flag: only the holder delivers.

**`IntrSpinLock` grows** [read spec.md:4739-4760]: a failed acquire spins
when the other core holds it and still panics when this core does (the word
holds the holder's core plus one); the spin polls the stop word (§6). The
kernel lock is not closure-scoped, because it is given back on paths that do
not return to the taker (`hal.resume_frame` is `Never`); the save-and-mask
half is the identity, since the kernel always runs masked [read
spec.md:4008-4009, 4617-4622].

**Atomics.** The S3's word atomic is `s32c1i`, inline in compiled code [read
hal/xtensa-esp32s3/ABI.md:269-270], working under `ATOMCTL` 0x15 on DRAM on
silicon [read ABI.md:1380-1410]. The ROM's reset handler writes 0x15 on both
CPUs [read ROM 0x4000_0484-0x4000_048C]. Two cores contending `s32c1i` on
internal SRAM is what IDF's own spinlock does [read
IDF/components/esp_hw_support/include/spinlock.h:123,132]; on PSRAM IDF
refuses the native compare-and-swap [read
IDF/components/esp_hw_support/cpu.c:203-218], and two cores on one PSRAM word
are unproven (board stage P8 is one core [read ABI.md:1925-1930]), so a word
two cores contend stays in SRAM until a board stage shows otherwise.
Ordering is `memw` on the S3, `fence` on riscv [read hal/riscv32/board.toml:13,
`rv32imac`].

---

## 2. One full kernel stack, an entry stack per core

### 2.1 The shape

Each HAL's trap entry saves the user context into the thread's frame with no
stack (the frame address is the save area: `EXCSAVE1` on the S3 [read
hal/xtensa-esp32s3/kernel/boot.S:49-56,585-650], `mscratch` on riscv [read
hal/riscv32-common/kernel/trap.S:50-108], `SP_EL1` on arm64 [read
hal/arm64/kernel/boot.S:295-311]), then loads the one kernel stack and calls
`ktrap` [read boot.S:664; trap.S:112]. The new entry, after the save:

1. switch to this core's entry stack; clear this core's `RUNNING_PROCESS`
   (§2.3);
2. take the lock, spinning on the entry stack, polling the stop word;
3. switch to the full stack (`_stack_top`, as today) and call `ktrap`;
4. `ktrap` returns a frame, or the idle sentinel (§2.2), or the retry
   sentinel (§2.3);
5. for a frame: set `RUNNING_PROCESS`, give the lock back, restore the frame,
   return to user mode.

Step 5 gives the lock back before the restore reads the frame, which is safe
because nothing but a thread's own core writes a running thread's frame: a
blocked thread's result is written by its wake, and a running one is never
blocked [read kernel/core/dispatch.saw:5577-5586]. The full stack is used by
nothing after `ktrap` returns, so it is empty at every release.

`resume_after_death` and `start_process` enter user mode through
`hal.resume_frame` with frames still on the full stack [read
kernel/core/sched.saw:343-347; kernel/core/process.saw:1540-1542]; those
frames are abandoned there today, and the next holder starts the full stack
from its top, so they stay abandoned. The restore path gives the lock back
in all three ways in.

At boot core 0 holds the lock from `kmain` on the full stack, which is
today's boot stack [read boot.S:183,308], until root's first entry. Core 1
starts on its entry stack, spins for the lock, and gets it at that first
release.

### 2.2 The idle loop leaves `pick_next`

Today the idle loop runs deep in `ktrap` → `pick_next` → `take_next` →
`idle_until_runnable` → `idle_once` [read kernel/core/sched.saw:209-330], and
`resume_after_death` reaches it too. Under the proposal that wait is a release
with frames live, so it moves:

- `take_next` with nothing ready makes the deadlock check (§4) and returns
  no thread; `pick_next` then marks the core idle and `ktrap` returns the
  idle sentinel. `resume_after_death` does the same.
- The entry stub gives the lock back and runs the idle loop on the entry
  stack: `hal.wait_for_irq()`, then take the lock, switch to the full stack
  and call `kidle()`: the poll, `drain_deaths`, the deadlock check and
  `take_next`; it returns a frame or the idle sentinel again.
- `wait_for_irq` runs without the lock, so it may touch only this core's
  registers and HAL state. The S3's reads its comparator's raw state and
  sleeps in `waiti` [read hal/xtensa-esp32s3/kernel/lib.saw:1361-1372]; the
  interrupt that ends the sleep returns to the poll using no stack [read
  ABI.md:914-925]. riscv's and arm64's are `wfi`.

### 2.3 No wait for the other core with frames live

The waits the design has, each settled:

- **A teardown of a process whose thread is running on the other core**
  (`Kill`, and a doomed process drained by `drain_deaths` [read
  kernel/core/sched.saw:1413-1497]). Each core publishes `RUNNING_PROCESS`,
  the process whose user code it is executing: set under the lock just
  before the release that returns to user mode, cleared by the entry stub
  after the save, before the spin. A teardown may proceed whenever no core's
  `RUNNING_PROCESS` names the process. Otherwise:
  - `drain_deaths` leaves that process doomed and IPIs the core; it is ended
    at the next drain after that core's entry.
  - `Kill` must answer `Ok` "once the teardown is done" [read
    spec.md:4437-4438]. It IPIs the core and answers the retry sentinel,
    writing nothing. The stub gives the lock back, spins on the entry stack
    until that core's `RUNNING_PROCESS` no longer names the process (or the
    stop word is set), takes the lock, and calls `ktrap` again on the same
    frame with the trap not counted a second time (the `syscalls` column stays
    exact [read spec.md:2203-2206]). `dispatch` reads its arguments from the
    frame [read kernel/core/dispatch.saw:5570-5574], so the op decides again
    from scratch, and finds the target gone if someone else ended it.
  - **Two cores killing each other's processes do not deadlock:** a core in
    the retry spin has cleared its `RUNNING_PROCESS`, so the other's kill
    proceeds; the first core then finds its own thread dead (below).
- **A core's current thread can die while the core waits for the lock** (a
  teardown made by the other core after this core cleared `RUNNING_PROCESS`).
  So every lock acquisition, in `ktrap`, the retry and `kidle`, first asks
  whether its current thread is still alive, and if not runs
  `resume_after_death`'s choice instead of the trap. The dead thread's slot
  is not freed while it is any core's current thread: the teardown leaves it
  for that core's `PENDING_FREE`, as `exit_thread` already does for its own
  core [read kernel/core/threads.saw `PENDING_FREE`].
- **Bring-up:** core 0 waits for core 1's up word while holding the lock;
  core 1 writes the word before it spins, so nothing deadlocks and nothing is
  released with frames live.
- **The stop** waits for an acknowledgement; it is §6's.

Nothing else waits for the other core: an IPI is sent, never answered.

### 2.4 Preemption points

They keep the lock and stay on the full stack: a point polls this core's
lines through the same delivery [read kernel/core/irq.saw idle and preempt
paths]; a delivery may wake a thread on the other core, which is an IPI and
no wait.

### 2.5 The entry stubs, per architecture

- **S3 (call0).** The kernel runs with `PS.WOE` = 0 and the whole register
  file saved into the frame before `PS` drops `EXCM` [read boot.S:585-660;
  ABI.md:1258-1300], so changing `a1` raises no window exception and spills
  nothing: the switch is one `rsr a1, misc0` for the entry stack and today's
  `l32r a1, .Lkstack` for the full one. The only windowed code the kernel
  runs is a ROM call through `sos_windowed_call`, before the loader [read
  ABI.md:1317-1340], which is before core 1 starts, on the boot stack. The
  user Alloca path runs on the frame, not a stack [read boot.S:545-556].
  `vec_fatal` loads the kernel stack today [read boot.S:451-457]; it loads
  this core's entry stack instead, since the other core may hold the full
  one (§6).
- **riscv32.** The save uses the frame as `sp` and `mscratch` as the mode
  witness [read trap.S:50-108]; the entry stack's top is found by `mhartid`
  in a table, as there is one scratch register. A trap during the spin has
  the witness at zero and goes to `kernel_fault` [read trap.S:56-62], which
  needs no stack.
- **arm64** is later (§11); `TPIDR_EL1` would hold the entry stack.

### 2.6 The entry stack's size

On it run: the lock spin and the retry spin (leaves, no frames worth
counting), the idle loop with `hal.wait_for_irq` (a few frames of Saw and a
C leaf), and the first half of a stop (§6: the claim, the signal, the bounded
wait, the hold, a few frames reading the clock). Nothing nests on it: every
interrupt is masked there, the S3's idle wake and stop NMI use no stack, and
a fault there goes to `vec_fatal` or `kernel_fault`, which restart the entry
stack from its top. **1 KiB per core**, held by U0's paint extended to the
entry stacks; the worst case is the stop's first half on top of a spin's
frame, which the restart removes.

---

## 3. What holds on full SMP

Placement on one core is the first stage's policy. The spec says so, and
says that threads of one process may run at once (§9). Each mechanism, held to
that:

- **The section word is preemption deferral, never mutual exclusion.** Its
  rule, "while the depth is nonzero no thread of the process runs but the
  holder" [read spec.md:4123-4136], becomes: while the depth is nonzero the
  scheduler does not *start* a sibling in the holder's place; a sibling
  already running on another core runs on. The spec says data a section
  covers is protected by a real lock as well. The runtime already does that:
  its heap windows are `SpinLock` windows inside the section [read
  rt/common/src/lib.saw:371-387; PS:844-847]. What changes is the comment's
  claim that no in-process contention is left to spin on, which is true only
  on one core. `SectionHeld` keeps its meaning.
- **The inheritance lock, two threads of one process on two cores.** Its
  no-lost-wake argument today is "the kernel reads `owner` and parks in one
  trap with interrupts masked" [read PS:673-678], which another core
  executing the give does not respect: the waiter's core reads `owner` = H;
  H's core swaps `owner` to 0 and loads `waiters`, still 0; the waiter writes
  `waiters` and parks, and no one wakes it. It becomes futex-like:
  - **`LOCK_WAIT_OP`** writes `waiters` nonzero first, fences, then reads
    `owner`: zero answers `Ok` without parking (step 2's answer, decided
    later); otherwise it parks naming the holder it read. All of it is under
    the big lock.
  - **The give** is a compare-and-swap of `owner` to 0 followed by a load of
    `waiters`, sequentially consistent (a fence between them where the
    target's compare-and-swap does not order a later load).
  - Either the waiter's store comes before the giver's load, so the giver
    traps to `LOCK_WAKE_OP`, which needs the big lock and so finds the waiter
    parked; or the giver's load came first, so its swap did too, and the
    waiter's read finds `owner` changed.
  - **Step 4's `Running` holder** [read PS:630-634] is today always the
    caller; on two cores it can be a sibling running elsewhere, and it is
    raised like a ready one.
  - `LOCK_WAKE_OP`'s write of `waiters` to zero stays under the big lock, as
    every kernel write of it is; only the user's load races.
- **The runtime and drivers.** Code written for one core, found by reading
  for the assumption:
  - `drivers/pktring`: "the kernel is uniprocessor and every shared access is
    volatile, so the peer sees the stores in program order" [read
    drivers/pktring/src/lib.saw:42-45]. Two processes on two cores share a
    ring even in this stage, so the head and tail advances become release
    stores and their reads acquire loads.
  - `kernel/sysapi/src/floor.saw`'s `SYSTEM_HANDLE` rests on "spec §7's
    uniprocessor argument" [read kernel/sysapi/src/floor.saw:1530-1533]; it
    is written before any second thread exists, and the kernel lock orders
    that write before the new thread runs on either core. The comment
    changes.
  - `rt/common_c/support.c`'s plain atomics serve only targets with no atomic
    instruction and say SMP voids them [read rt/common_c/support.c:7-11,
    21-28]; no two-core board uses them.
  - The audit is U4 (§10): every comment in `rt/`, `drivers/` and the kernel
    that argues from one core.
- **The kernel** keeps a thread's core in its slot, set from its process at
  its start, and the ready lists key on the thread's core, not the process's,
  so thread placement later changes the setter and nothing else.

---

## 4. The scheduler

- **Per-core lists**: today's eight lists and bitmap, one set per core;
  `ready_push`, `ready_pop`, `ready_remove`, `ready_rotate` and
  `ready_remove_process` take the thread's core [read
  kernel/core/threads.saw:385-540]. Strict priority holds per core.
- **Preemption on wake** compares against the target core's running thread
  [read kernel/core/threads.saw:385-404]. For the other core, when the thread
  is above that core's running level or the core is idle, the waker raises
  that core's `NEED_RESCHED` and sends its IPI; the core switches at its own
  user-return. The IPI's service only clears it.
- **The tick is per core**: one period [read kernel/core/threads.saw:327], each
  core's own `NEXT_TICK_NS`, charging only its own running thread [read
  kernel/core/irq.saw:224-245]. Core 0's is armed before root's first entry
  as today, core 1's when it first enters its scheduler, both only when the
  image asks for a tick.
- **Idle per core** (§2.2). **Deadlock is machine-wide**: every core's lists
  empty, no core running a thread, no bound line and no armed Timer [read
  kernel/core/sched.saw:291-301 for today's one-core form; spec.md:4054-4063].
- **Kill and doom across cores**: §2.3.
- **Domain edits** on riscv's replay tier: an edit to a process installed on
  the other core marks that core's domain stale and IPIs it [read
  kernel/core/process.saw:809].
- **The latency record** carries across: the claim is stamped where the line
  is taken, the stamp at the woken thread's return to user mode on its own
  core [read kernel/core/irq.saw:273-284; kernel/core/sched.saw:173-199]; one
  clock serves both (SYSTIMER's UNIT0, riscv's `mtime`).

---

## 5. Interrupts, Timers and the IPI

- **Every device line and every Timer is core 0's.** Lines are routed in the
  PRO map only, as today; a fire for a driver on core 1 is a cross-core wake.
  Core 0's comparator (SYSTIMER TARGET0, source 57 [read ABI.md:766-770];
  riscv hart 0's `mtimecmp` [read hal/riscv32/kernel/lib.saw:145]) carries
  core 0's tick and the whole Timer schedule, today's funnel [read
  kernel/core/time.saw:455-480]. A core-1 process arming a Timer reprograms
  it under the lock (SYSTIMER and the CLINT are memory-mapped).
- **Core 1's tick**: SYSTIMER TARGET1 (source 58 [read
  IDF/components/soc/esp32s3/include/soc/interrupts.h:73-75]), IDF's own
  per-core tick alarm [read IDF/components/esp_hw_support/include/
  esp_private/systimer.h:14-16]; the emulator models three comparators [read
  EQ/include/hw/timer/esp_systimer.h:56,66; EQ/hw/xtensa/esp32s3.c:740-742].
  riscv hart 1's `mtimecmp` at `+8`, UNVERIFIED.
- **The S3's IPIs** are `FROM_CPU_INTR0..3`, sources 79 to 82 (counted from
  [read IDF/components/soc/esp32s3/include/soc/interrupts.h:17-101], which
  gives UART0 27 and SYSTIMER 57 as probed), raised and lowered by bit 0 of
  `0x600C_0030 + 4n` [read IDF/components/soc/esp32s3/register/soc/
  system_reg.h:681-708], wired in the emulator [read EQ/hw/xtensa/
  esp32s3.c:698-703]. 79 and 80, in the PRO and APP maps (the APP map is at
  `+0x800` [read ABI.md:760-762]), at a level-1 CPU interrupt, reschedule
  core 0 and core 1; 81 and 82 at CPU interrupt 14, the NMI, stop them.
  Routing a source to 14 took the NMI vector at once [read ABI.md:780]; that
  the APP CPU takes it at `INTLEVEL` 15 is UNVERIFIED (§12's probe).
- **riscv**: each hart's CLINT `msip`; no NMI.

---

## 6. The panic stop

Every machine stop (`fatal`, `fatal_kernel`, `fatal_deadlock`, `fatal_image`
[read kernel/core/diag.saw:150-206], a kernel panic, root's end and kill, the
S3's `kernel_fault` [read hal/xtensa-esp32s3/kernel/sink.c:207-211]) stops
the other core before it prints or takes a device back; SO-165's console
reclaim comes after [read .leadwork/SO-165/design.md:446-450].

1. **Claim** the stop word by compare-and-swap; a loser acknowledges and
   parks.
2. **Signal**: on the S3 the other core's NMI. Its vector, `vec_fatal` today
   [read boot.S:425-427], writes the acknowledgement with `EPC7`, the stopped
   core's PC, and loops, using no stack. On riscv the reschedule IPI with the
   stop word set; the target acknowledges at its next entry, idle poll,
   preemption point or spin.
3. **Wait**, bounded by the clock (one millisecond).
4. **Hold**, on the S3: the RTC software stall [read
   IDF/components/hal/esp32s3/include/hal/cpu_utility_ll.h:29-48], as IDF's
   panic does [read IDF/components/esp_system/port/panic_handler.c:181-189];
   a core 1 that did not acknowledge is also held in reset (`RESETING`, and
   the `SW_APPCPU_RST` pulse [read IDF/components/soc/esp32s3/register/soc/
   rtc_cntl_reg.h:137-139]). The emulator's stall is a no-op [read
   EQ/hw/xtensa/esp32s3.c:226-228]; its `SW_APPCPU_RST` resets core 1 to the
   ROM [read EQ/hw/misc/esp32s3_rtc_cntl.c:91-94].
5. **Print**, on the full stack.

**With the shared stack.** A core that stops while holding the lock is on the
full stack already and keeps it. One that stops without it (a fault in its
spin or idle, a stop raised there) runs steps 1 to 4 on its entry stack, then
takes the full stack from its top, abandoning whatever frames the stopped
core had there. **What the stop may read** after that: its own registers and
its own thread's frame; the stop and acknowledgement words, the stopped
core's PC among them; and the constants of its line. Not the kernel's slabs
or lists, which the stopped core may have left mid-update, and not the
abandoned frames. Every stop line already prints only its own cause and
registers [read kernel/core/diag.saw:150-206].

---

## 7. Bring-up

**Order in `kmain`**: `clock_init` (the flash switch inside), the banner,
`memory_init`, `intc_init`, **`start_cores`**, `load_root_and_enter` [read
kernel/main.saw:22-52]. Core 1 starts after the flash switch and the PSRAM
bring-up, so the switch's cache freeze needs nothing from it; the switch and
the PSRAM bring-up stop the machine if core 1 is out of reset.

**The S3**, core 0, in IDF's order [read
IDF/components/esp_system/port/cpu_start.c:293,307-311,322]:

1. core 1's cache buses opened to core 0's mask, from IRAM with both caches
   frozen by the flash switch's freeze [read
   IDF/components/hal/esp32s3/include/hal/cache_ll.h:559-579;
   cpu_start.c:365-372]: the two cores share one instruction and one data
   cache, each through its own bus;
2. core 1 unstalled; `SYSTEM_CORE_1_CONTROL_0`: `CLKGATE_EN` set, `RUNSTALL`
   cleared, `RESETING` set then cleared [read system_reg.h:15-30], which on
   this part is what releases the core (`SOC_APPCPU_HAS_CLOCK_GATING_BUG`
   [read IDF/components/soc/esp32s3/include/soc/soc_caps.h:91]);
3. the entry written to `0x600C_0004`: the ROM's `main` on `PRID` 0xABAB
   polls it and `callx8`s it [read ROM 0x4004_3A2F-0x4004_3A43; ABI.md:246-249],
   and `ets_set_appcpu_boot_addr` is one store to it [read ROM
   0x4004_3664-0x4004_366F]; the emulator keeps that word and nothing of
   `CONTROL_0` [read EQ/hw/xtensa/esp32s3_clk.c:55-57,88-90];
4. a bounded wait for core 1's up word; none stops the boot, `SOS: core 1
   did not start`.

**Core 1's entry**, in IRAM: PS `0x0F`, then `WINDOWSTART` and `WINDOWBASE`
as core 0's `_start` [read ABI.md:221-224]; `VECBASE` to the one table;
`MISC0` and `a1` to its entry stack; `INTENABLE` 0; every APP map register
to 6; the entry word cleared, so a reset leaves it in the ROM's loop; then
`secondary_main(1)`: its IPI and tick sources, the up word, the spin for
the lock, its tick, and idle. It prints `SOS: core 1 up`, a boot line.

**riscv32 `virt`**, `-bios none`: every hart enters the ELF entry (UNVERIFIED,
§12); a hart other than 0 parks in `wfi` with only `MSIE` set until core 0's
release, then `secondary_main`.

---

## 8. The API and the image

- **`ProcessOp.Core(core)`**, between the create and the start, on
  `ProcessRight.BandMap`. Answers in order: no right is `AccessDenied`; a
  child not `Created` is the `BadState` fault; a core the board does not have
  is the `BadArg` fault. The last call wins; unset, the launcher's core. No
  per-thread core in this stage.
- **`CORE_OP`**, a no-handle op as `THREAD_ID_OP` is [read PS:603-606],
  answers the core the caller is running on at that trap. It grants nothing;
  under later thread placement it stays true for the moment it answers.
- **The builder**: `board.toml` `cores` (2 on both S3 boards and riscv32, 1
  on riscv32-flat, arm64 and the C3), held to the HAL's `CORES`;
  `process.toml` `core = N`, refused past the board's `cores`;
  `bootplan.Child.core`; the stock root's `child.core(...)` after
  `band_map` [read PS:943-951].

---

## 9. Spec text that changes

- §7: "Uniprocessor kernel in v1" [read spec.md:4206-4208] becomes the
  model: one kernel, a lock, per-core lists, tick and idle, the IPI, the
  machine-wide deadlock; **placement is a launcher's policy, and threads of
  one process may run at once**; nothing above relies on them not.
- §7's section word: preemption deferral, not mutual exclusion; data it
  covers is under a real lock too (§3).
- §7's inheritance lock: the wait's order and the give's ordering (§3).
- §8: kill and doom across cores (§2.3). §9: the IPI and the stop. §9b: the
  lock. §2.6: core 0's comparator carries every Timer, each core its tick.
  §2.7: `ProcessOp.Core`, `CORE_OP`. §11: the later list. §12: root's core
  and the plan's step.
- CLAUDE.md: riscv32's two harts, and the cross-core construction rule
  (§10.1).

---

## 10. Gate, tests and units

**The gate.** riscv32 runs every case on two harts (`-smp 2`,
`-accel tcg,thread=multi`; whether multi-threaded TCG is this QEMU's default
is UNVERIFIED). Existing cases place nothing, so their transcripts do not
change. riscv32-flat stays on one hart, keeping a one-core build on the gate.
The S3 smoke runs the SMP cases with the emulator's two CPUs [read
EQ/hw/xtensa/esp32s3.c:956-957].

**10.1 Construction.** A cross-core ordering is made true by waiting on what
the other side does (a word in a shared Memory, an Event, a death), never by
the clock; spinners run with the tick off, so on one core a case that needs
both to progress times out.

| case | shows |
|---|---|
| `smp_up` | `SOS: core 1 up`, before the handover |
| `core_place` | children placed on 0 and 1 read their core with `CORE_OP`; an unplaced one reads its launcher's |
| `core_refused` | `ProcessOp.Core`'s refusals in order |
| `core_parallel` | two children on two cores, tick off, each spins until the other's counter moves |
| `core_wake` | a core-1 child's `High` thread, parked on an Event, preempts its own `Normal` spinner when root signals: the IPI; and the same with core 1 idle |
| `core_timer` | a core-1 child's Timer fires through core 0's comparator and wakes it |
| `core_kill` | root kills a core-1 child spinning in user mode: `Ok`, `Killed` (the retry); and two children on two cores killing each other's siblings: both kills answer, nothing deadlocks |
| `core_deadlock`, `core_not_deadlock` | one deadlock line when both cores block; none while core 1 still runs |
| `core_stop` | a harness kernel stops from core 0 with core 1 spinning masked, and from core 1 without the lock; the acknowledgement and the line |
| `core_lock_churn` | both cores hammer shared objects for a fixed count; every count exact (a net for a missing lock, never a flaky pass) |
| `core_section`, `core_lock`, `pktring` | SO-148's section and lock cases and a ring between processes on two cores |

**Board stages** (U3): core 1 comes up (its handover state, its cache buses,
a core-1 process in place from flash); the stop holds it (a word core 1
increments stays put after the stall and after the reset hold).

**Units.**

```
U0  S  Sonnet  The full kernel stack's depth: paint at boot, report the
               deepest word after the heaviest paths, per profile; the
               entry stacks painted too. First, independent.
U1  L  Opus    The lock, the shared stack and entry stacks, per-core state
               with CORES = 1 on every board: the stubs, the idle loop out
               of pick_next, RUNNING_PROCESS, the dead-current check, the
               retry, the stop word; spec §7, §9b. No transcript changes.
               After SO-148's lock and runtime-locks units.
U2  L  Opus    riscv32 on two harts: release, msip IPI, per-hart tick, the
               machine-wide deadlock, kill and doom across cores, the
               riscv stop, ProcessOp.Core, CORE_OP, the builder's `core`
               and the stock root's step; §10's cases; spec §2.6, §2.7, §8,
               §9, §12. After U1.
U3  L  Opus    The S3: core 1's start, entry and entry stack, the FROM_CPU
               IPIs, TARGET1, the NMI stop, stall and reset hold, the switch
               and PSRAM guards; the smoke's cases; ABI.md's second-core
               section; the two board stages. After U0 and U2.
U4  M  Opus    Full-SMP soundness: the section's spec text, the inheritance
               lock's wait order and give ordering, pktring's release and
               acquire, and the audit of one-core comments in rt/, drivers/
               and the kernel. Beside U2; before U3's cases.
```

---

## 11. Later

Not designed now; each gets a design when it is next.

- Per-core Timers, a Timer firing on its owner's core (revision 2's §4.3 has
  a sketch, in scratch).
- Device lines routed to their driver's core.
- Measuring the lock and the cross-core Timer path on the board, and what
  result would justify splitting the lock.
- Per-core stats rows.
- arm64 on two cores.
- Placing threads, not just processes, and other placement policies.
- A grant cap on which cores a launcher may place on.

---

## 12. What was checked

- **ESP-IDF v5.4.1** and **Espressif QEMU** files, fetched into scratch, and
  the **mask ROM** disassembled, as cited above.
- **Probes**, built in scratch (`smp/probe/`): `s3core1.S` (the APP CPU
  started through `0x600C_0004`, its registers printed, source 80 raised at
  its CPU interrupt 2 and source 81 at its NMI while it spins at `INTLEVEL`
  15, then reset through `SW_APPCPU_RST` and started again) and `rv2.S` (two
  harts under `-smp 2 -bios none`, and `msip` ending hart 1's `wfi` with
  `MIE` clear). **Neither has run:** another run held the suite lock every
  time this work checked (pids 38850, 86081, then 63667, last at 18:09). Each
  is a few seconds of QEMU; the UNVERIFIED marks pointing here are what they
  settle.

---

## 13. Questions for the owner

None new from revision 3. The coordinator holds revision 2's three (the
budgets, when per-core Timers are built, the lock counters), which the trim
moves to the later list.
