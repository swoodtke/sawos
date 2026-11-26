# Using the ESP32-S3's second core: a design

Revision 1, Nov 26. A design only: nothing here is built until the owner and
the reviewer have read it. Written against main at `76460836`, with SO-148's
inheritance lock (`.p4` r6, approved, in the merge queue) taken as landed.

Markers. **[read F:L]** is taken from that file and those lines (paths from
the sawos root; `ROM ELF` is the mask ROM's ELF, by address; `IDF/` is ESP-IDF tag v5.4.1, `EQ/` is espressif/qemu tag
`esp-develop-9.2.2-20250817`, the build installed here, both fetched into
scratch; `PS:` is the tracker doc `SO:priority-scheduler`,
`.sawtracker/docs/priority-scheduler.md`). **[probed]** is something I ran;
§13 says how. **UNVERIFIED** is neither.

---

## 0. Verdict

**SMP with process affinity.** One kernel image runs on both cores under one
big kernel lock. Each process is placed on one core by its launcher, between
the create and the start, as the band map is, and never moves; every thread
of the process runs there. Each core has its own ready lists, tick, idle and
IPI line. A launcher that places nothing gets today's machine: every process
on core 0, core 1 idle.

The deciding fact is the section word. It is a ceiling enforced by *not
scheduling* a sibling, entered by a plain store with no trap [read
spec.md:4123-4136]; a sibling already running on the other core is not
stopped by a store, so the moment one process's threads run on two cores at
once the section is no lock at all, and the runtime's heaps, which U5 moves
into the section [read PS:842-848], corrupt. The inheritance lock's no-lost-
wake argument also rests on the process not running while the kernel reads
the owner word [read PS:673-678]. Confining a process to one core keeps both
exactly as specified, and keeps every PSRAM atomic contended from one core,
which is the only case the board has shown [read
hal/xtensa-esp32s3/ABI.md:1929-1930]. A process whose threads span cores is
a later, additive unit (§11, U8).

It gives a user program: a core to itself (the radio alone on core 1, its
realtime levels competing with nothing of core 0's), and two processes
running at once. It does not give one process's threads in parallel.

---

## 1. The model

| | Full SMP | Asymmetric | **Affinity: process** | Affinity: thread |
|---|---|---|---|---|
| What runs where | any thread, any core | core 1: pinned threads, a restricted loop | a process's threads on its core | a thread on its core |
| Kernel change | shared ready structure, global pick of the two highest, migration, every lock | a second kernel personality, or syscall forwarding | per-core ready lists, one lock, IPIs, remote kill | as process, plus every in-process cross-core case |
| Section word | broken (§0) | n/a on core 1 or broken | **unchanged** | broken for a spanning process |
| Inheritance lock | needs a re-check after publishing `waiters` | as full | **unchanged** | needs the re-check |
| PSRAM atomics | two-core `s32c1i` on PSRAM, unproven; IDF refuses it [read IDF/components/esp_hw_support/cpu.c:203-218] | as full | **one core per word** | as full |
| RT isolation | none unless pinned | best if core 1 makes no syscall, worse than affinity if it does (forwarding is two IPIs plus core 0's kernel) | good: a core of its own; bounded by core 0's longest kernel section (§2) | good |
| Throughput | best (balancing) | none for general work | across processes | within a process |
| Risk | highest: every ordering in the suite changes meaning | medium, and a spec surface nobody else uses | **lowest that gives both cores** | medium |

**Asymmetric is dropped.** A radio's realtime work waits on interrupts and
pipes, so its threads make syscalls. Forwarding them to core 0 puts core 0's
kernel and two IPIs inside every realtime path; running a restricted kernel
on core 1 is a second kernel to specify and test. Affinity gives the same
"core 1 is the radio's" with one kernel: place the radio there alone.

**Full SMP is dropped for now.** It costs everything thread affinity costs and
adds migration and a global pick, for load balancing that the box's fixed set
of processes does not need.

**Fit for the S3.** The kernel code is shared, the vector table is shared
(each core points `VECBASE` at it), and per-core state is a few hundred
bytes. The real cost is a second kernel stack: `KERNEL_STACK_SIZE` is 64 KiB
of a 144 KiB kernel DRAM [read hal/xtensa-esp32s3/kernel/esp32s3.ld:92,106],
and nothing measures how much of it is used (U0). In-place flash adds
contention, not work: both cores fetch through one shared cache (§5), so core
0's misses can evict core 1's lines, which only a board can measure.

---

## 2. Locking

### 2.1 What needs a lock

Every mutable kernel global is an `unsafe static var` whose comment argues
"uniprocessor, interrupts taken from user mode only" [read
kernel/core/threads.saw:252-253, kernel/core/irq.saw:110-120]. Under process
affinity they split three ways.

**One per core, written by its own core only** (an array indexed by
`hal.core_id()`): `CURRENT_THREAD`, `SLICE_LEFT`, `SLICE_ENDED`,
`PENDING_FREE`, `LAST_PROT_PROCESS` [read kernel/core/threads.saw], `IN_DELIVERY`
[read kernel/core/irq.saw:162-168], the tick's `NEXT_TICK_NS` [read
kernel/core/time.saw], and the HAL's per-core registers' shadows: the S3's `INTENABLE_SHADOW` and `LINE_CPU_INT` [read
hal/xtensa-esp32s3/kernel/lib.saw], arm64's `EL0_FP_ON` and `CURRENT_SET`,
riscv's `PMPCFG*` shadows. The other core reads `CURRENT_THREAD` only under
the lock (§3.4, §3.5).

**One per core, written by either core** (under the lock): `READY_HEAD`,
`READY_TAIL`, `READY_BITS` and `NEED_RESCHED`. A wake made on core 0 readies a
thread on core 1's lists and raises core 1's flag.

**Shared** (under the lock): every slab (`THREADS`, `PROCESSES`,
`PROCESS_RECORDS`, `HANDLE_BLOCKS`, the memory, pipe, exchange, event,
waiter, interrupt, attachment, clock and timer slabs), `NEXT_PID`,
`UNSTARTED_PENDING`, `DOOM_PENDING`, `STATS_STORAGE`, `TIMER_TICKS`,
`SPURIOUS_IRQS`, the Timers' comparator and its shadows (`PROGRAMMED_NS`,
the S3's `TIMER_PROGRAMMED`), and the console.

Boot-only state (`IMAGE_QUOTA_*`, the PSRAM records) is written before core 1
starts and read after, so it needs none.

### 2.2 One lock

**A big kernel lock, taken at every kernel entry and given back at every
exit.** Entry is the trap entry from user mode (`ktrap`'s top [read
kernel/core/dispatch.saw:5535]) and the secondary core's first entry; exit is
`ktrap`'s return after `pick_next` and `stamp_interrupt_latency`, and
`resume_after_death`'s `hal.resume_frame` [read
kernel/core/sched.saw:343-347]. The idle wait gives it back around the sleep
and takes it again to poll (§3.5).

Why one, and not finer:

- The kernel is not preemptible and its sections are short except three
  known ones (the loader's placement, a teardown, a console line), so one
  lock costs little throughput on two cores.
- The object graph is one web. A single wake runs `notify_ready` →
  `wake_one_waiter` → `deliver_attachment` → `copy_out` and touches a Waiter,
  an attachment, a thread, a ready list and a process's memory in one walk
  [read kernel/core/preempt.saw:11-14]. Finer locks need an order across that
  web and a re-audit of every serialization comment; one lock turns each
  comment's "uniprocessor" into "under the kernel lock" and nothing else.
- What it costs is latency on the other core: a realtime thread on core 1
  waits, at its next kernel entry, for the longest section core 0 can be in.
  The latency record (§3.2) measures it, and the per-core `lock_waits`
  column (§8.3) shows contention. If they show it matters, the first split is
  the scheduler's per-core state out of the big lock.

**A preemption point does not give the lock back.** A point asserts that no
in-flight invariant spans state the IRQ path touches [read spec.md:4633-4635],
which is weaker than "no syscall on the other core can see a half-built
object": a `process_create` mid-load is invisible to an interrupt and visible
to a kill. Points stay what they are: delivery of this core's lines.

### 2.3 How `IntrSpinLock` grows

§9b specifies `lock` as save-and-mask, then compare-and-swap, panicking on a
failed acquire because on one core that is reentrancy [read
spec.md:4739-4750]. It grows three ways:

- **A failed acquire spins when the holder is the other core**, and panics
  only when the holder is this core. The word holds the holder's core plus
  one, so reentrancy stays a loud stop.
- **A ticket, not a test-and-set.** Two words, `next` and `serving`; a taker
  fetch-adds `next` and spins until `serving` reaches its ticket. Two cores
  cannot starve each other.
- **The spin polls the stop word** (§7), so a core spinning masked still
  answers a stop on a board with no NMI.

The big lock is that type over `()`, but it is not closure-scoped, because it
is released on paths that do not return to the taker (`hal.resume_frame` is
`Never`): `kcore` gets `kernel_enter()` and `kernel_leave()` around it. The
save-and-mask half is the identity in the kernel, which always runs masked
[read spec.md:4008-4009, 4617-4622]; it matters for a future lock taken from a context
that is not.

### 2.4 What the atomics allow

- **The S3.** `s32c1i` is the core's one word atomic, the compiler inlines it
  [read hal/xtensa-esp32s3/ABI.md:269-270], it works under `ATOMCTL` 0x15 on
  DRAM on silicon [read hal/xtensa-esp32s3/ABI.md:1380-1410], and there is no
  `L32EX/S32EX` [read IDF/components/xtensa/esp32s3/include/xtensa/config/core-isa.h:71,79].
  Two cores contending `s32c1i` on internal SRAM is what IDF's own spinlock
  does [read IDF/components/esp_hw_support/include/spinlock.h:123,132], so
  the kernel's lock words live in kernel DRAM. On PSRAM, IDF never uses the
  native compare-and-swap and takes an interrupts-off lock in internal RAM
  instead [read IDF/components/esp_hw_support/cpu.c:203-218]. Under process
  affinity a user lock word in PSRAM is contended from one core only, which
  board stage P8 showed exact [read hal/xtensa-esp32s3/ABI.md:1925-1930];
  two cores on one PSRAM word stays unproven and is a spanning process's
  problem (U8). Ordering: `memw` after a releasing store, and `L32AI`/`S32RI`
  are present [read core-isa.h:78].
- **`ATOMCTL` on core 1 is the ROM's 0x15, as on core 0.** Core 0 inherits
  it [read hal/xtensa-esp32s3/ABI.md:1390-1400]; the ROM's reset handler,
  which both CPUs run, writes 21 to `ATOMCTL` and its own table to `VECBASE`
  before either reaches `main` [read ROM ELF `esp32s3_rev0_rom.elf`
  (esp-rom-elfs 20241011), 0x4000_0484-0x4000_048C]. The emulator reads only
  bits [1:0] [read ABI.md:1398-1400], so only a board shows the part's.
- **riscv32** is `rv32imac` [read hal/riscv32/board.toml:13]: `amoadd.w`
  and `lr/sc`. **arm64** is a Cortex-A53 [read tools/sos_runner.py:1216],
  ARMv8.0, so exclusives and no LSE; the compiler chooses.
- `rt/common_c/support.c`'s plain read-modify-write atomics are for targets
  with no atomic instruction only, and say SMP voids them [read
  rt/common_c/support.c:7-11,21-28]; no SMP board uses them.

---

## 3. The scheduler

### 3.1 Per-core ready structures

Each core owns eight lists and a one-byte bitmap: today's `READY_*`, one per
core. A thread's core is its process's, read when it is pushed, so
`ready_push`, `ready_pop`, `ready_remove`, `ready_rotate` and
`ready_remove_process` take the core from the thread and the code is
otherwise today's [read kernel/core/threads.saw:385-540]. Nothing is shared
because nothing migrates; strict priority holds per core, and a level-7
thread on core 1 holds back nothing on core 0, which is the point of putting
it there.

### 3.2 What carries across cores unchanged

- **The eight levels, head-and-tail placement, round-robin**: per core, as
  built.
- **The section word**: a process is on one core, so "no thread of the
  process runs but the holder" [read spec.md:4123-4136] stays a statement
  about one core's scheduler, and `section_keeps`/`section_displaces` [read
  kernel/core/sched.saw:437-508] read that core's lists.
- **The inheritance lock**: holder and waiters are one process's threads, so
  one core's; the raise moves a thread between that core's lists
  (`ready_move`), and the give-and-wait argument [read PS:673-678] holds
  because none of the process's user code runs while its core is in the
  trap.
- **The latency record**: the claim is stamped on the core that took the line
  and the stamp at the woken thread's first return to user mode on its own
  core [read kernel/core/irq.saw:273-284, sched.saw:173-199]. The interval now
  includes an IPI when the two differ, which is what it should measure. Both
  ends read one clock: the S3's SYSTIMER unit is one counter, riscv's `mtime`
  is one register; arm64's virtual counter is architecturally common to all
  cores, UNVERIFIED under QEMU.

### 3.3 Preemption on wake, across cores

`ready_push` compares against the **target core's** running thread [read
kernel/core/threads.saw:385-404 for today's compare]. When the target is this
core, today's rule. When it is the other core and the thread is above that
core's running level, or that core is idle, the waker raises the target's
`NEED_RESCHED` under the lock and sends the target its reschedule IPI. The
target takes the IPI from user mode (or it ends the idle sleep), enters the
kernel, waits for the lock if the waker still holds it, and switches at its
own user-return boundary, where every switch happens. The IPI's service does
nothing but clear the source: the flag was raised by the sender. An IPI that
finds the target already in the kernel costs one extra trap at that core's
return, counted (§8.3).

### 3.4 Kill, start and domain edits across cores

- **Start.** `ProcessOp.Start` and `ThreadOp.Start` push onto the child's core
  and may IPI it: §3.3.
- **Kill.** A kill answers `Ok` "once the teardown is done" [read
  spec.md:4437-4438]. When a victim's thread is running on the other core in
  user mode, the killer marks the process doomed, IPIs that core, gives the
  lock back and spins until that core has left the victim (its
  `CURRENT_THREAD` names no thread of the process), then takes the lock and
  tears down as today. The other core leaves at its next entry: the IPI's
  trap finds its current process doomed and picks another thread. The spin is
  bounded by one kernel section and an IPI; it polls the stop word.
- **Faults and exits** happen on the process's own core and stay local.
- **Domain edits.** On riscv's replay tier, an edit to a process installed on
  the other core marks that core's installed domain stale and IPIs it, and
  that core reloads at its user-return (today's `domain_changed` reloads only
  the running process [read kernel/core/process.saw:809]). arm64's per-ASID
  flush is already broadcast, `tlbi aside1is` with `dsb ish` [read
  hal/arm64/kernel/sink.c:219-222]. The S3 is `Flat` and loads no domain.

### 3.5 Idle and deadlock

**A core idles alone.** A core whose lists are empty gives the lock back and
sleeps in its HAL's `wait_for_irq`; its tick, its IPI and any line routed to
it end the sleep, and it takes the lock again to poll and to recheck its
lists [read kernel/core/sched.saw:321-330, irq.saw idle path]. On the S3 the
IPI is a level-1 CPU interrupt like every other the kernel enables, so
`waiti 0` lets it in [read hal/xtensa-esp32s3/ABI.md:903-913].

**Deadlock is machine-wide.** Today a core that finds nothing runnable and no
external wake source stops the machine [read kernel/core/sched.saw:291-301].
Under two cores "nothing runnable here" is not deadlock while the other core
runs a thread that can wake one. The predicate, read under the lock: every
core's lists are empty, **no core is running a thread**, and there is no
bound line or armed Timer. A core that finds its own lists empty but the
other core running idles; the last core to block makes the check and reports.
The tick still does not count [read spec.md:4054-4063].

### 3.6 The tick

Each core has its own tick at `TICK_PERIOD_US`, charging its own running
thread's slice [read kernel/core/irq.saw:224-245]. Timers stay on core 0's
comparator (§4.3).

---

## 4. Interrupts

### 4.1 The S3's matrix

Each CPU has its own map register per source: the PRO CPU's at `0x600C_2000 +
4*source`, the APP CPU's at `+0x800` [read hal/xtensa-esp32s3/ABI.md:760-762];
the emulator keeps a map per CPU and drives every CPU whose map routes the
source [read EQ/hw/xtensa/esp32s3_intc.c:27-41]. Each core has its own 32 CPU
interrupts and its own `INTENABLE`, so the HAL's table of CPU interrupts a
line may be given (`DEVICE_CPU_INTS`, nine of them [read ABI.md:855-857])
becomes one table per core.

### 4.2 Which core takes a line, and who decides

**The kernel decides: a line goes to the core of the process whose Waiter it
is attached to.** It is routed to the binder's core at `InterruptBind` and
moved, masked, when an attachment to a Waiter is made from another core. The
driver that will be woken is then on the core that takes the fire, so a
device interrupt crosses no core in the steady state; a fire before the
attach marks the Interrupt pending and wakes nobody, wherever it lands. A new
HAL seam, `irq_route(line, core)`: on the S3, the old core's map register to
6 and a CPU interrupt from the new core's table; on riscv, the PLIC enable
bit in the other hart's context; on arm64, the SPI's `ITARGETSR` byte [read
hal/arm64/kernel/lib.saw:2010,2089].

The move is made masked because the emulator does not lower a CPU interrupt
when its source is routed away [read ABI.md:803-850]; the S3 HAL's
quiet-entry take-back already handles that, per core.

### 4.3 The tick on each core

- **S3:** core 0 keeps SYSTIMER TARGET0 (source 57 [read ABI.md:766-770]) for
  its tick and every Timer; core 1 takes TARGET1 (source 58, the next entry
  [read IDF/components/soc/esp32s3/include/soc/interrupts.h:73-75]) for its
  tick only. One counter, UNIT0, serves both. Whether the emulator models
  TARGET1 is UNVERIFIED.
- **riscv:** each hart's own `mtimecmp` (the CLINT's compare array the HAL
  already addresses [read hal/riscv32/kernel/lib.saw:145]).
- **arm64:** each core's own physical timer (a banked PPI).

Timers on core 0 only: a Timer fire for a core-1 process is a cross-core wake
(§3.3), one IPI. A core-1 process that arms a Timer reprograms core 0's
comparator under the lock: SYSTIMER and the CLINT are memory-mapped, so either
core can write them; arm64's timer is a system register of its own core, so
there it takes an IPI (U7). Serving each process's Timers on its own core's comparator
would save that IPI and split the Timer schedule per core; it is Q8.

### 4.4 The IPIs the S3 offers

`FROM_CPU_INTR0` to `3` are sources 79 to 82 (counted from the source table
[read IDF/components/soc/esp32s3/include/soc/interrupts.h:17-101]; the same
count gives UART0 27 and SYSTIMER 57, as probed [read ABI.md:762-770]). Each is
raised by writing bit 0 of `SYSTEM_CPU_INTR_FROM_CPU_n_REG`, `0x600C_0030 +
4n`, and lowered by writing 0 [read
IDF/components/soc/esp32s3/register/soc/system_reg.h:681-708]; they are
level sources. The emulator wires all four into the matrix from the SYSTEM
block's model [read EQ/hw/xtensa/esp32s3.c:698-703;
EQ/hw/xtensa/esp32s3_clk.c:26-42,64-67].

| source | routed in | to | use |
|---|---|---|---|
| 79 | PRO map | a level-1 CPU int | reschedule core 0 |
| 80 | APP map | a level-1 CPU int | reschedule core 1 |
| 81 | PRO map | CPU int 14, the NMI | stop core 0 |
| 82 | APP map | CPU int 14, the NMI | stop core 1 |

Routing a source to CPU interrupt 14 took the NMI vector at once [read
ABI.md:780]. The NMI is level 7 [read core-isa.h:371,417], above
`PS.INTLEVEL` 15's reach, so a core masked in the kernel, or spinning on the
lock, still takes it. That the APP CPU takes it at `INTLEVEL` 15 is
UNVERIFIED: §13's probe checks it and has not run.

**riscv:** each hart's CLINT `msip` word, a machine software interrupt.
That `wfi` ends on it with only `mie.MSIE` set and `mstatus.MIE` clear is the
privileged architecture's rule, UNVERIFIED here (§13's probe). No NMI. **arm64:**
GICv2 SGIs through `GICD_SGIR`, which the HAL already writes [read
hal/arm64/kernel/lib.saw:2011,2113]; no NMI on GICv2.

---

## 5. Memory and protection

- **PMS.** The S3 profile is `Flat`: the core has no privilege and the kernel
  does not program the PMS [read ABI.md:660-676]. Nothing per core to load.
  The PMS's per-core registers matter only to a future isolated S3 tier and
  were not read.
- **What a switch on core 1 loads.** What core 0's does: the frame, all 64
  address registers and `WINDOWBASE`/`WINDOWSTART` through the restore path,
  `EXCSAVE1` set to the frame [read hal/xtensa-esp32s3/kernel/boot.S:49-56].
  `CPENABLE` stays 0. What core 1 sets once, at bring-up: `VECBASE` (the
  same table), `INTENABLE`, and **its kernel stack top in
  `MISC0`**: the trap entry loads the stack from a literal today [read
  boot.S:316,456,664], which names one stack; `rsr a1, misc0` names each
  core's. The S3 has four `MISC` registers [read core-isa.h:83]. riscv keeps
  it in a second scratch word indexed by `mhartid`, arm64 in `TPIDR_EL1`.
- **The caches are shared.** The S3 has one instruction cache and one data
  cache; each core has its own bus into each, opened or shut by
  `EXTMEM_{I,D}CACHE_SHUT_CORE{0,1}_BUS` [read
  IDF/components/hal/esp32s3/include/hal/cache_ll.h:559-579]. So there is no
  coherence to maintain between the cores' views of flash, PSRAM or each
  other's stores; there is contention. IDF suspends both caches once for a
  flash operation [read IDF/components/spi_flash/cache_utils.c:365-372].
- **Core 1's buses.** IDF opens core 1's buses to match core 0's, with the
  cache disabled around the write [read
  IDF/components/esp_system/port/cpu_start.c:365-372]. Whether the ROM's direct
  boot leaves them open is UNVERIFIED; the bring-up copies core 0's mask
  regardless, from an IRAM routine that freezes both caches around the write
  with the flash switch's own freeze and unfreeze (§6).
- **`sos_flash_switch` and its freeze.** It runs once, at boot, from
  `clock_init` [read kernel/main.saw:26; hal/xtensa-esp32s3/keys/flash-dio80/
  lib.saw:6-9], before `memory_init` and long before core 1 exists. So its
  freeze needs nothing from core 1, and the rule that keeps it so is an
  order: **core 1 starts after `memory_init`, and the switch and the PSRAM
  bring-up stop the machine if core 1 is out of reset** (a new stop code,
  checked by reading `SYSTEM_CORE_1_CONTROL_0`). A freeze at run time, which
  nothing does today (no image writes flash), would need IDF's shape: the
  other core parked in IRAM with interrupts masked until the operation ends
  [read IDF/components/spi_flash/cache_utils.c:114-136,150-210]; here every
  user instruction is in flash, so the park would be a kernel IRAM loop
  reached by the stop NMI with a resume. Not built.
- **PSRAM.** Behind the shared data cache; one core per contended word (§2.4).

---

## 6. Bring-up

### 6.1 Order

`kmain`: `clock_init` (the flash switch inside), the banner, `memory_init`,
`intc_init`, **`start_cores`**, `load_root_and_enter` [read
kernel/main.saw:22-52]. Core 1 is up before root runs, so a child placed on
it can start; it prints `SOS: core 1 up` before the handover, a boot line.

### 6.2 The S3

Core 0, from IRAM with both caches frozen by the flash switch's freeze and
unfreeze: open core 1's cache buses to core 0's mask. Then, in IDF's order [read
IDF/components/esp_system/port/cpu_start.c:293,307-311,322]:

1. clear any software stall of core 1 [read
   IDF/components/hal/esp32s3/include/hal/cpu_utility_ll.h:50-65];
2. `SYSTEM_CORE_1_CONTROL_0` (`0x600C_0000`): set `CLKGATE_EN` (bit 1),
   clear `RUNSTALL` (bit 0), set then clear `RESETING` (bit 2) [read
   system_reg.h:15-30]; on silicon core 1 leaves reset held in reset with its
   clock gated [read ABI.md:1841-1845], and the part's clock-gate bit does
   not hold the core by itself (`SOC_APPCPU_HAS_CLOCK_GATING_BUG` [read
   IDF/components/soc/esp32s3/include/soc/soc_caps.h:91; cpu_start.c:637-642]);
3. write the entry to `SYSTEM_CORE_1_CONTROL_1` (`0x600C_0004`). The ROM's
   `main`, on `PRID` 0xABAB, polls that word and `callx8`s it [read ROM ELF
   0x4004_3A2F-0x4004_3A43; ABI.md:246-249], and `ets_set_appcpu_boot_addr`
   is one store to it [read ROM ELF 0x4004_3664-0x4004_366F]. The ROM's reset
   handler also takes a word with bit 31 set as a `call0` entry before it sets
   `VECBASE` or `ATOMCTL` [read ROM ELF 0x4000_045A-0x4000_0472]; an IRAM
   address has bit 31 clear, so the kernel takes the `main` path. The
   emulator's SYSTEM model keeps exactly that one word and nothing of
   `CONTROL_0` [read EQ/hw/xtensa/esp32s3_clk.c:55-57,88-90];
4. wait, bounded by the clock, for core 1's up word; none is a boot stop,
   `SOS: core 1 did not start`, with core 1 put back in reset.

Core 1's entry, `sos_core1_entry` in `boot.S`'s IRAM section, entered by the
ROM's `callx8` with a windowed PS as core 0's handover is [read
ABI.md:182-206]: PS = `0x0F` first, then `WINDOWSTART`/`WINDOWBASE` (core 0's
own sequence [read ABI.md:221-224]); `VECBASE`; `MISC0` and
`a1` to core 1's stack; `INTENABLE` 0; every APP map register to 6, as IDF
clears its APP matrix [read cpu_start.c:250-251]; the entry word cleared, so
a later reset leaves the ROM polling rather than re-entering (the emulator's
reset returns core 1 to the ROM [read EQ/hw/misc/esp32s3_rtc_cntl.c:91-94;
EQ/hw/xtensa/esp32s3.c:218-222]). Then it calls `kcore`'s
`secondary_main(1)`.

### 6.3 What core 1 does before the scheduler takes it

`secondary_main`: its HAL half (`intc_init_core`: its reschedule and stop
sources, its tick source), its per-core state, the up word, then
`kernel_enter`, its tick armed if the image's tick is, and the idle loop.
It has no thread until a launcher places one; idle on its own lists, it is
not a deadlock while core 0 runs (§3.5).

### 6.4 riscv32 and arm64

- **riscv32 `virt`**, `-bios none`: every hart enters the ELF entry,
  UNVERIFIED (§13's probe). `_start` reads `mhartid`; a hart other than 0 enables only `MSIE`,
  parks in `wfi` until core 0's release word, then takes its own stack and
  calls `secondary_main`.
- **arm64 `virt`**: secondary cores start powered off and are started by PSCI
  `CPU_ON`, which QEMU provides when it boots a kernel directly; UNVERIFIED for
  this boot path (the HAL stops through semihosting, not PSCI [read
  hal/arm64/kernel/lib.saw:327-329]). Its unit is later (U7).

---

## 7. Panic

**Every machine stop stops the other core before it prints or takes a device
back.** The stops are `fatal`, `fatal_kernel`, `fatal_deadlock`,
`fatal_image` [read kernel/core/diag.saw:150-206], a kernel panic, root's end
and kill, and the HALs' C stops (the S3's `kernel_fault` [read
hal/xtensa-esp32s3/kernel/sink.c:207-211]). SO-165's `console_reclaim` runs
after this, not before [read .leadwork/SO-165/design.md:446-450,1046-1048].
The stop never takes the kernel lock: the core being stopped may hold it.

1. **Claim.** Compare-and-swap the stop word (kernel DRAM) from 0 to this
   core plus one. A core that loses is the one being stopped: it writes
   its acknowledgement and parks, masked.
2. **Signal.** Raise the other core's stop source. On the S3 that is the NMI
   (§4.4), which neither `INTLEVEL` 15 nor a spin masks. The NMI vector, today
   `vec_fatal` [read boot.S:425-427], becomes: if the stop word names another core
   than this one, write this core's acknowledgement, `memw`, and loop; otherwise fatal
   as today. It is a few IRAM instructions touching no kernel state. On
   riscv and arm64, which have no NMI here, the signal is the reschedule IPI
   with the stop word set, and the acknowledgement is made at the target's
   next trap entry, idle poll, preemption point or lock-spin iteration, all
   of which read the stop word; every kernel section is bounded, so only a
   kernel loop bug misses it.
3. **Wait** for the acknowledgement, bounded by the clock
   (`STOP_ACK_NS`, one millisecond), not a loop count, because the clock key
   changes the CPU's speed.
4. **Hold.** On the S3 the stopped core is also stalled by the RTC software
   stall, which needs no cooperation: `0x86` split across
   `RTC_CNTL_OPTIONS0` and `RTC_CNTL_SW_CPU_STALL` [read
   cpu_utility_ll.h:29-48; rtc_cntl_reg.h:150-153,2175-2184], what IDF's panic
   does to every other core [read
   IDF/components/esp_system/port/panic_handler.c:181-189]. A core 1 that did
   not acknowledge is held in reset as well (`RESETING` set, and the
   write-only `SW_APPCPU_RST` pulse [read rtc_cntl_reg.h:137-139]); a core 0
   that did not can only be stalled, since resetting the PRO CPU restarts the
   chip. On the emulator the stall is a no-op [read
   EQ/hw/xtensa/esp32s3.c:226-228] and `CONTROL_0` is not modelled, while
   `SW_APPCPU_RST` does reset core 1 back to the ROM [read
   EQ/hw/misc/esp32s3_rtc_cntl.c:91-94], where the cleared entry word keeps
   it. On riscv and arm64 under QEMU there is no per-core hold.
5. **Print.** Reclaim the console and write the stop's line; a core that did
   not acknowledge adds `SOS: core N did not stop`.

Two cores stopping at once are ordered by step 1. An acknowledged core is
stalled rather than reset so a debugger can still read it (Q6).

---

## 8. The API and the image

### 8.1 A process asks; its launcher grants

- **`ProcessOp.Core(core, cores)`**, between the create and the start, on
  `ProcessRight.BandMap`: placement is the same authority as priority, how
  and where a launcher's child runs, and a new right bit buys nothing (Q3).
  `core` is where the child's threads run; `cores` is the set the child may
  in turn grant, which must hold `core`. The last call wins. Answers, in
  order: no right is `AccessDenied`; a child that is not `Created` is the
  `BadState` fault; a core the board does not have, a `cores` without
  `core`, or a set not inside the caller's own is the `BadArg` fault and sets
  nothing. Unset, a child runs on its launcher's core and may grant only it.
  Root runs on core 0 and may grant every core.
- **No per-thread core.** `ThreadOp.Start` keeps its band only.
- **`CORE_OP`**, a no-handle op as `THREAD_ID_OP` is [read PS:603-606],
  answers the caller's core. It grants nothing, the launcher chose it, and a
  program needs it for per-core data and the tests need it to observe
  placement.

### 8.2 The declaration and the builder

- `board.toml` gains `cores` (2 on both S3 boards, 1 on the C3 and the flat
  profile, 2 on riscv32, 1 on arm64 until U7), held to the HAL's `CORES`
  constant by `sosimage board` as the other numbers are [CLAUDE.md, "`sosimage
  board` holds a board file ... to the HAL's numbers"].
- `process.toml` gains `core = N` (absent: the launcher's core, which for a
  stock-root child is core 0). The builder refuses a
  core the board does not have, naming the board's `cores`, in `check-tree`
  too. A child that launches and places children declares `cores = [...]`.
  An image description has no key that overrides either, as for `bands`
  [read PS:907-920].
- `bootplan.Child` gains `core` and `cores`; the stock root calls
  `child.core(...)` after `band_map`, before `restrict`, and stops on a
  refusal as every step does (`root: core NAME: STATUS`) [read PS:943-951].
- The rights table gains nothing (Q3 as proposed).

### 8.3 What `Stats` reports per core

The process record is unchanged: its `interrupts` column is still charged to
the process current at trap entry, on whichever core trapped [read
spec.md:2212-2219]. The stats region gains a block after the process rows, a
row per core, its header's version moving:

| column | moves with | asserted |
|---|---|---|
| `interrupts` taken through `ktrap` on this core | the host | never |
| `ipis` received | the program, in a case built so each wake IPIs | yes, as a lower bound only |
| `lock_waits`, entries that found the lock held | the host | never |
| `idle_sleeps` | the host | never |

`ProcessOp.Stats` stays per process; the core rows are read through the
region, which a `top` already maps.

---

## 9. The other profiles

- **riscv32 runs two harts, every case** (`-smp 2`). Every existing case
  places nothing, so its processes run on core 0 and its transcript does not
  change; the second hart idles, and every case then also checks the
  machine-wide deadlock predicate and that a silent core 1 stays silent. The
  SMP cases run here too. Whether this QEMU runs the two harts on two host
  threads (multi-threaded TCG) by default is UNVERIFIED; the profile passes
  `-accel tcg,thread=multi` so races are real ones.
- **riscv32-flat stays one hart**, `cores = 1`. It keeps a one-core kernel
  build on the gate, so a `CORES = 1` regression is caught, and the flat
  profile's exclusions are about the tier, not the core count.
- **arm64 stays one core** until U7 (PSCI, SGIs, GIC routing).
- **The ESP32-S3 smoke** runs the SMP cases on the emulator with two cores
  (the machine always has two [read EQ/hw/xtensa/esp32s3.c:956-957]).
  Whether Espressif's Xtensa emulator runs its two CPUs on two host threads
  is UNVERIFIED; if not, it shows the protocol and not the races.
- **The C3** is single-core; `cores = 1`.

---

## 10. Tests

### 10.1 Construction

Each cross-core ordering is made true by waiting on what the other side does:
a word in a shared Memory the other core writes, an Event, a death. No case
reads the clock to order cores. Spinners run with the image's tick off, so on
one core a case that needs both to progress cannot pass (it times out),
which is what makes "both ran at once" a construction and not a timing.

### 10.2 Cases on riscv32 and the S3 smoke

| case | shows |
|---|---|
| `smp_up` | `SOS: core 1 up` at boot, before the handover |
| `core_place` | children placed on 0 and 1 read their core with `CORE_OP`; a child placed with no call reads its launcher's |
| `core_refused` | each `ProcessOp.Core` refusal, in order; a started child's is `BadState` |
| `core_parallel` | two children on two cores, tick off, each spins until it sees the other's counter move: both progress or the case times out |
| `core_wake_preempt` | a core-1 child's spinner at `Normal`, tick off; root signals an Event a `High` thread of that child waits on; the waiter runs (it writes a word root waits for) though the spinner never traps: the IPI preempts |
| `core_wake_idle` | core 1 idle; root's signal wakes a core-1 waiter |
| `core_pipe_call` | a client on core 0 calls a server on core 1 and back; core 1's `ipis` at least one |
| `core_kill_running` | root kills a core-1 child spinning in user mode, tick off: `Ok`, status `Killed`, the eviction path |
| `core_deadlock` | both cores' threads block, nothing bound or armed: one deadlock line |
| `core_not_deadlock` | core 0's threads all block while core 1 spins and then signals: no deadlock |
| `core_irq_route` | a driver on core 1 attaches its line; the fire is delivered on core 1 (`CORE_OP` in the driver, the line's latency record counts it) |
| `core_section`, `core_lock` | SO-148's section and lock cases with the child placed on core 1, transcripts unchanged |
| `core_stop` | a harness kernel stops the machine from core 0 with core 1 spinning masked in the kernel, and prints the acknowledgement it read before the stop's line |
| `core_stop_unanswered` | the same with core 1's stop source unrouted (a harness seam): `SOS: core 1 did not stop`, then the line |
| `core_lock_churn` | both cores hammer shared objects (Event ping-pong, pipe calls, create-and-kill churn) a fixed number of rounds; every count exact |

`core_lock_churn` never fails on correct code; a removed `kernel_enter` on
one path fails it only when the race lands, so it is a net, not a proof.
`ipis` is asserted as a lower bound only, since an IPI that finds its core
already in the kernel still counts.

### 10.3 Only the board

The RTC stall and the reset hold actually stopping core 1; core 1's ROM
handover (PS, its cache buses); the clock-gating bug;
`s32c1i` between cores on SRAM under real concurrency; cache contention, and
what it does to a core-1 realtime thread's latency record when core 0 runs
flash-heavy code. Board stages in U5.

---

## 11. Units

```
U0  S  Sonnet  The kernel stack's depth: paint the stack at boot, a harness
               kernel reports the deepest word after the suite's heaviest
               paths, per profile; KERNEL_STACK_SIZE set from it. First,
               independent; it decides whether a second S3 stack fits.
U1  L  Opus    Per-core state and the kernel lock with CORES = 1 everywhere:
               hal.core_id() and hal.CORES, the per-core arrays, the ticket
               lock and kernel_enter/kernel_leave, the stop word and
               stop_machine's one-core form; IntrSpinLock's growth; spec §7,
               §9b, §11. No transcript changes. After SO-148 U3 and U5.
U2  L  Opus    riscv32 on two harts: the parked hart and its release, the
               CLINT IPI, a tick per hart, PLIC routing, cross-core wake,
               kill and domain reload, the machine-wide deadlock, the
               poll-based stop, ProcessOp.Core, CORE_OP, the floor and
               sysapi; the riscv32 profile at -smp 2; §10's cases; spec §2.7,
               §7, §8, §9, §12. After U1.
U3  S  Sonnet  The builder and the stock root: board `cores`, `core` and
               `cores` in process.toml and their refusals, bootplan,
               root's step. After U2's op, beside U4.
U4  S  Sonnet  The per-core stats rows. After U2.
U5  L  Opus    The S3: core 1's entry and stack (MISC0), its cache buses in
               the frozen window, IDF's start sequence, per-core HAL state,
               the FROM_CPU IPIs, TARGET1, irq_route, the NMI stop, stall and
               reset hold, the switch and PSRAM guards; the smoke's cases;
               ABI.md's second-core section rewritten; board stages. After
               U0 and U2.
U6  S  Sonnet  SO-165's console reclaim placed after the stop, if it lands
               first; otherwise SO-165 lands on top of U1's stop.
U7  M  Opus    Later: arm64 on two cores (PSCI CPU_ON, SGIs, ITARGETSR).
U8  L  Opus    Later, on a need: a process spanning cores (thread
               placement), with the section refused for it and the lock's
               wait re-checking the owner after publishing `waiters`.
```

Order: U0 beside U1; then U2; then U3 with U4; then U5. What must land first:
SO-148's U3 and U5 (U1 restates their serialization comments), and nothing
of SO-165, which only needs the stop to exist before its reclaim runs.

**Spec text that changes.**

- §7: "Uniprocessor kernel in v1" [read spec.md:4206-4208] becomes the model:
  one kernel, a core per process, per-core lists, tick and idle, preemption
  on wake across cores by IPI, the machine-wide deadlock, and the stop rule.
- §8: kill across cores (eviction before teardown); `ProcessOp.Core`.
- §9: the IPI line, which core takes a line, the stop NMI; "interrupts
  masked in the kernel" holds per core.
- §9b: "Not built, and correctly so" [read spec.md:4752-4760] becomes the
  big lock, the ticket, the cross-core spin.
- §2.7: the op, its answers, the default; `CORE_OP`; the region's core rows.
- §11: the not-built line goes; spanning processes and arm64 SMP are listed.
- §12: root's core and the plan's step. CLAUDE.md's timing section gains the
  cross-core construction rule (§10.1) and the riscv32 profile's two harts.

---

## 12. What was not settled here

- Whether the kernel lock's latency is acceptable for the radio is a
  measurement (the latency record on the board with core 0 loaded), not a
  design question.
- The PMS on the S3, which this design does not need.

---

## 13. What was checked

- **ESP-IDF v5.4.1**, fetched from GitHub into scratch:
  `esp_system/port/cpu_start.c`, `esp_system/port/panic_handler.c`,
  `esp_hw_support/cpu.c`, `esp_hw_support/include/spinlock.h`,
  `hal/esp32s3/include/hal/cpu_utility_ll.h`, `hal/esp32s3/include/hal/
  cache_ll.h`, `spi_flash/cache_utils.c`, `soc/esp32s3/register/soc/
  {system_reg,rtc_cntl_reg,extmem_reg}.h`, `soc/esp32s3/include/soc/
  {interrupts,soc_caps}.h`, `xtensa/esp32s3/include/xtensa/config/core-isa.h`.
- **Espressif QEMU** at the installed build's tag: `hw/xtensa/esp32s3.c`,
  `esp32s3_intc.c`, `esp32s3_clk.c`, `hw/misc/esp32s3_rtc_cntl.c`.
- **Probes**, built in scratch (`smp/probe/`): `s3core1.S`, a flash image
  that starts the APP CPU through `0x600C_0004`, prints its `PRID`,
  `ATOMCTL`, `VECBASE`, `PS` and `INTENABLE`, raises source 80 at its CPU
  interrupt 2 and source 81 at its NMI while it spins at `INTLEVEL` 15, then
  resets it through `SW_APPCPU_RST` and gives it the entry again; `rv2.S`,
  two harts under `-smp 2 -bios none` and a CLINT `msip` ending hart 1's
  `wfi` with `MIE` clear. **Neither has run**: the suite lock was held by
  another run (pids 38850, then 86081) for the whole of this work, so no
  claim here is marked [probed]. Each is a single QEMU boot of a few seconds
  under the lock; the files are in scratch for the lead to run, and every
  UNVERIFIED pointing at §13 is what they settle.
- **The mask ROM**, disassembled from `esp32s3_rev0_rom.elf` (esp-rom-elfs
  20241011): the reset handler (0x4000_045A-0x4000_048C), `main`'s APP-CPU
  loop (0x4004_3A2F-0x4004_3A43) and `ets_set_appcpu_boot_addr`
  (0x4004_3664-0x4004_366F).

---

## 14. Questions for the owner

1. **What do the projects need from the second core?** Throughput across
   programs (two processes at once), isolation of realtime work (the radio on
   a core of its own), or one program's threads in parallel? Process affinity
   gives the first two; the third is U8, which costs the section word for
   that process and needs two-core atomics on PSRAM proven on the board.
2. **Is the big lock's latency acceptable for now?** A core-1 realtime thread
   waits, at its next kernel entry, for the longest kernel section core 0 can
   be in (a child's load, a teardown, a console line). The design measures it
   rather than designing it away; splitting the scheduler's state out of the
   lock is the first step if it is too long.
3. **Placement on `ProcessRight.BandMap`, or a right of its own?** The design
   reuses `BandMap` (where and how urgently a child runs are one launcher
   authority). A separate `Place` bit lets a launcher grant priority without
   placement.
4. **The second kernel stack on the S3.** 64 KiB per core does not sit
   comfortably in 144 KiB of kernel DRAM. U0 measures the real depth first;
   is a smaller stack per core, sized from that measurement with a margin, the
   right lever, or should the kernel DRAM grow at the processes' expense?
5. **The gate's riscv32 profile on two harts for every case**, as proposed
   (no new profile, every transcript unchanged, riscv32-flat keeps a one-hart
   kernel), or a separate two-hart profile (more gate time, the existing one
   untouched)?
6. **The stopped core: stall it (registers kept for a debugger) and reset it
   only when it does not answer**, as proposed, or always hold core 1 in
   reset?
7. **arm64 SMP**: later (U7), as proposed, or with riscv32 so two
   architectures catch SMP bugs on the gate?
8. **Timers**: every Timer on core 0's comparator (one IPI per fire for a
   core-1 process, as proposed), or each core serving its own processes'
   Timers (no IPI, a split schedule)? A radio whose timers drive its
   realtime work would want the second.
9. **The default core.** Unplaced children run on their launcher's core, so
   an image that says nothing is today's machine with core 1 idle. Should the
   stock root instead spread undeclared children, or is placement always the
   declaration's?
