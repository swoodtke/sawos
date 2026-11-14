# riscv32 kernel HAL — the seam

What the SOS kernel needs from its architecture, and nothing more.
`hal/arm64/kernel/` implements the same list for Profile B; the kernel
above it does not change, and `tools/sos_runner.py` checks that by scanning
`kernel/core/` for architecture names and failing the run if it finds one.

The seam has two halves. The Saw half (`lib.saw`) is the module the kernel
imports as `hal` — it is the surface, and it is where the arch-free vocabulary
above meets this machine's. The native half (`boot.S`, `trap.S`, `sink.c`) is
what Saw cannot express: a trap vector, a privilege transition, a CSR write
whose operand must be an assembly-time immediate, and a linker symbol.

Each half is split again along the board line, and this document describes the
seam rather than the files. The surface below is reached as `hal.<name>`.
Everything true of riscv32 rather than of QEMU `virt` — the trap frame, PMP, the
cause decoding, the syscall accessors, the linker seams, the trap entry, the
`ecall` stub — lives once in `hal/riscv32-common/` and is shared with
`hal/riscv32-esp32c3`; `lib.saw` here re-exports it with a `public import`, so
`kcore`'s view of `hal` is one flat module. The **Where** column of the
native-half table below says which file a symbol lives in. See
`../../riscv32-common/README.md` for the rule that decides.

Almost everything is Saw: the NS16550A write loop, the finisher write that
stops the machine, and all of the PMP region arithmetic. The `sink.c`
functions are each an instruction with no Saw spelling, or a linker symbol's
address, and each states its reason in the table below. The interrupt-related
ones, `sos_mie_write` and `sos_wait_for_irq`, are a single line each: the timer
here is memory-mapped and so is the interrupt controller, so the counter
arithmetic, the comparator write order, the priorities, the claim and the
completion are all Saw.

A thread context is a frame the kernel owns, built by `frame_init` in Saw. The
privilege transition is four instructions and a branch into the trap entry's
own restore path.

## The Saw surface (`lib.saw`) — what `kcore` may use

| Name | Contract |
|---|---|
| `arch_name() -> String` | What the boot banner says this kernel is running on. |
| `arch_tag() -> UInt8` | This profile's `sosimg` arch tag, read out of `imgformat`'s registry rather than spelled again. An image carrying another one is refused by the loader, not jumped into. |
| `console_byte(b)` | Put one byte on the kernel's console. Byte-at-a-time on purpose: the arch-free half formats, this half places. |
| `exit_pass()` / `exit_fail(code)` | Stop the machine, zero / non-zero. Never return. A zero `code` is promoted so a failing exit never reads as a passing one. |
| `payload_start()` / `payload_end()` | Bounds of the appended root image. Equal when there is none. |
| `region_table_start()` / `region_table_end()` | Bounds of the build-emitted boot region table. Equal when there is none, which is zero regions and the ordinary state of an image that appends no child images. |
| `PROT_GRAIN: UInt` | Protection granularity — what a region bound is rounded up to. |
| `MAX_ROOT_SEGMENTS: UInt` | How many segments a root image may ask for, i.e. the grant budget minus the stack. |
| `GRANT_ROW_BUDGET: UInt` | **How many protection rows one process's domain may hold** — what `MemoryOp.Map` checks a target's `grant_count` against. **On this profile it is the physical wall**: a row is a numbered hardware region and there are only `PMP_REGIONS` of them, so a map meeting it is a map the machine cannot install. The target's `Mapping` quota is clamped to its free rows, so the answer a caller sees is `QuotaExceeded` and the wall itself is a kernel stop. Profile B publishes the same name meaning a policy cap, which is why the kernel reads the HAL's number rather than a shared constant. |
| `RAM_BASE` / `RAM_TOP` | Where RAM is on this board, and how much of it there is — the window `map_target_ok` judges against. Stated rather than derived: nothing on this profile publishes it (the linker script knows where the kernel is, not where memory ends). |
| `map_target_ok(base, top) -> Bool` | **May `[base, top)` be installed as a RAM row in a user domain?** The point of the predicate is the altitude it moves a refusal to: the loader path answers an unrepresentable grant by stopping the machine, which is right for a memory map the build got wrong and wrong for a range a process chose. A map asks first and faults with `BadArg`. Here an entry can name any address, so this accepts everything in RAM — not vacuous: it is what refuses a device window through the RAM door and a region whose bounds have wrapped. |
| `device_window_ok(base, len) -> Bool` | **May `[base, base + len)` be installed as a device row?** — this profile's granularity rule on caller-chosen numbers, checked at `IoMemoryOp.Carve` and again at its `Map`. Here: `len` a power of two, at least `PROT_GRAIN`, and `base` a multiple of it — the NAPOT shape, because a device window costs one entry rather than a TOR pair. |
| `ROOT_LOAD_BASE` / `ROOT_REGION_TOP` / `ROOT_STACK_LEN` | Root's region in this board's memory map. |
| `phys_to_virt(pa) -> UInt` / `virt_to_phys(va) -> UInt` | **The linear-map seam, and here it is the identity** (`rv32core`). The kernel's discipline is that addresses-as-data stay physical everywhere and only the moment of kernel dereference converts — so `kernel/core` names these at every site where a stored physical address becomes a load or a store. This machine has no MMU: a physical address is what the kernel loads through, both bodies return their argument, and every one of those calls folds away to nothing. Profile B's bodies are an OR and an AND with a fixed offset, and the kernel carries no tier conditional either way. An Sv32 kernel would inherit the identity too: this board's RAM at 0x8000_0000 already sits in the upper 2 GiB, so such a kernel never moves. |
| `prot_reset()` | Revoke every grant. User mode then reaches nothing. |
| `map_source_ok(base, top) -> Bool` | **The same body as `map_target_ok`, and that is this profile's answer to placement.** The other profile splits one question into two because it has two addresses — which input addresses a domain may use, which output frames it may reach. Here there is one address, so the two predicates are deliberately identical rather than one forwarding to the other, and each states its own question where a reader of either finds it. |
| `image_link_base(dest_base) -> UInt` | `dest_base`. **Where it loads.** An image's link base and its destination frames are one number on a machine that does not translate, and a linker script that said otherwise would produce a program that runs nowhere. This is why this board keeps one script per resident image — `root.ld`, `child.ld`, `child2.ld` and `child3.ld` — while arm64 has one script whatever the process count. |
| `map_place(cursor, pa, device) -> UInt` | `pa`. The cursor is the translating tier's instrument and is ignored; the kernel keeps one per process on both tiers because it is arch-free bookkeeping. `map` takes no VA hint on any tier: the address is the answer, and on this machine it is an answer with no freedom in it. |
| `prot_region(idx, base, top, perms)` | Stage grant `idx` as `[base, top)` with `perms`, a mask in the `imgformat` `SegFlag` vocabulary (R = 1, W = 2, X = 4). Fed the row's physical range by `load_domain`'s replay, which on this tier is also its user range — PMP filters addresses the processor actually issues. |
| `prot_device(idx, base, len)` | Stage grant `idx` as a device window: read/write, never executable, with whatever memory type this machine needs for MMIO. `len` is a power of two and `base` is aligned to it — which `device_window_ok` above is the callable form of, so a caller-chosen window is refused before it reaches here. |
| `DEVICE_GRANT_BASE` / `DEVICE_GRANT_LEN` | The one window this board offers a process — the console UART's page. The build mirrors this pair when it publishes a device row in the region table: the kernel mints an `IoMemory` for it and a driver obtains it. The older path, where an image declares a window and this pair authorizes it, is dormant but present — see §2.5. |
| `prot_commit()` | Publish the staged set. Separate so a half-programmed set is never live. |
| `is_syscall(cause) -> Bool` | Did this trap come from a syscall instruction, or is it a fault? |
| `is_interrupt(cause) -> Bool` | Is this trap an interrupt instead of either? Asked first, because an interrupt is not the running program's business and its instruction has not run. |
| `cause_tag(cause) -> String` | A short symbolic name for a raw trap cause; unmodelled codes get their own name rather than an arm. |
| `trap_pc(frame) -> UInt` | Where the trapped context resumes: the faulting instruction on a fault, the interrupted one on an interrupt. |
| `syscall_handle/op/arg0/arg1/arg2(frame) -> UInt` | The §5.7 argument registers, by role rather than by name. Three argument slots, because `Process.ThreadCreate` takes an entry, a stack and an argument. |
| `syscall_return(frame, status, value)` | Place the (status, value) pair where the caller reads them and step the saved PC past the trapping instruction — which is a no-op on a profile whose trap already points past it. |
| `UNMAPPED_PROBE: UInt` | An address the kernel cannot reach here. The harness's kernel-fault case reads it; it is per-target because "unmapped" is. |

**The syscall return carries a (status, value) pair and nothing wider.** An op
whose answer does not fit a word (`Waiter.Wait` is the first) takes a buffer
argument, and the kernel copies a record out into memory the caller supplies.
The HAL is not involved in that: a copy-out is a store to an address, and the
check that makes it safe (`copy_out` in `kcore`, the only door) names no
machine.

## Thread contexts

**The trap frame is a thread's context**, which is what makes a context switch
cost nothing extra here: the kernel reserves one frame per thread, the trap
entry saves into the running thread's, and `ktrap` returns the frame to resume.
Handing back a different one is the switch. The three rows below are the whole
of what the kernel needs to know about a context; everything else — the layout,
the register roles, the mode the frame resumes in — stays this file's.

| Name | Contract |
|---|---|
| `FRAME_BYTES: Int` | Bytes in one saved context: 128 here; on Profile B the trap frame plus the thread's FP/SIMD area. The kernel asserts its per-thread stride against it, so a frame that outgrew the stride is a build error. |
| `frame_init(frame, entry, stack_top, arg0)` | Build a fresh user context: the entry as the resume address, the stack pointer, `arg0` in the first argument register, and every other word zeroed — including the return-address register, so a thread entry that returns faults instead of running on. |
| `fp_trap(frame, cause) -> Bool` | Claim a trap as a thread's first use of floating point or SIMD, giving the thread its own state and answering true so the kernel resumes the same frame; false for any other trap. Always false here: there is no FPU. |
| `fp_switch_out(frame)` / `fp_switch_in(frame)` | Save the outgoing thread's FP/SIMD state, and install the incoming thread's, at a switch. Empty here. |
| `resume_frame(frame) -> Never` | Enter user mode in a saved context. The first entry only; every one after it is `ktrap` returning a frame to the trap entry's restore path. |

There is no `enter_user`: a context built in assembly and entered once has no
storage a second thread could have. The loader and the harness kernels go
through `kcore.start_process`, which reifies a program as a Process with one
Thread and resumes its frame.

## Interrupts

The same list again, for the interrupt half of the seam; `hal/arm64/kernel/`
implements every row.

| Name | Contract |
|---|---|
| `IRQ_NONE: UInt` | What a claim answers when the controller had nothing to give. Zero on both profiles, and never a real line on either. |
| `IRQ_TIMER: UInt` | The line the periodic timer arrives on. |
| `intc_init()` | Bring the interrupt controller up with every line masked. |
| `irq_unmask(line)` / `irq_mask(line)` | Let a line interrupt the kernel, or stop it. Masking is spec §9's mask-on-fire; the Interrupt object's ack is what unmasks. |
| `irq_claim(cause) -> UInt` | Which line is being serviced, `IRQ_NONE` if none. Takes the cause because one profile answers the timer half out of it. |
| `irq_poll() -> UInt` | The same question with no trap behind it — the idle path's half of the seam. Here it also has to check the timer by hand, because on this profile the timer is not one of the controller's sources; there it is `irq_claim` under a second name. |
| `wait_for_irq()` | Park the core until an interrupt is pending. Both machines wake from this whether or not the current privilege level would take one, which is what the idle path needs — interrupts stay masked in kernel mode (see "Interrupts are masked in the kernel" below), so the kernel notices by polling rather than by trapping. |
| `irq_line_valid(line) -> Bool` | Is this a line the board wires? What `System.InterruptBind` checks a caller's number against. The timer's line is excluded arch-free by the kernel instead, because "the tick is not for rent" is a policy rather than a fact about the board. |
| `irq_complete(line)` | End of service for a line. |
| `timer_now_ns() -> UInt64` | This machine's monotonic time in nanoseconds since the counter started. What a `Clock.now()` reads and what every deadline is measured against — one time source, so the two cannot disagree. |
| `timer_set_deadline_ns(at_ns)` | Program the one comparator to interrupt at an absolute deadline. On both profiles this is also what lowers the timer's line, which is why the fire path calls it rather than acknowledging something. `UInt64.max` is the kernel's "never". Enables the timer interrupt every time — idempotent, and what lets a kernel that armed no tick still serve a Timer armed later. |
| `timer_pending() -> Bool` | Has the timer come due without a fire having been taken for it? The masking case's evidence. |
| `irq_raise_selftest_line() -> UInt` | Raise a line from software, for bring-up, and say which. Per-board because what a board can interrupt itself with is: here a real device, on Profile B a software-generated line. |

`irq_raise_selftest_line` stays although the echo driver now takes a real
device line round the claim / mask / complete cycle on both profiles, because
two claims are only reachable through it. It raises a line in the kernel, so the
transcript reads "raised, entered user mode, only then serviced", which is the
masked-in-kernel property for a device line where the timer case makes the same
claim for a timer; and the line it raises is bound to no Interrupt object, which
is the one path where `on_external_irq` reports and leaves the line masked.
Retiring it would delete both.

What the kernel provides above this is one funnel with two ways in. `ktrap`
decides interrupt / fault / syscall in that order and hands an interrupt to
`service_irq`; the idle path polls and hands one to `idle_poll`. Both meet at
`deliver_line`, which rearms the timer or masks a device line, runs the hook
that owns it, and completes. The two hooks — the timer tick and a device line —
are where the scheduler and the Interrupt object land.

`console_byte`, `exit_fail` and the `sos_rt_write` seam are thin over `sosrt`:
the poll-and-place (`ConsoleSink.write_byte`), the panic path's write loop
(`console_write`, generic and monomorphized here) and the exit-status promotion
(`abort_status`) live there, once, for both profiles. What stays here is the
device — `can_write` reads LSR bit 5, `put` stores to THR — and the mechanism
that stops the machine.

## The native half (`boot.S`, `trap.S`, `sink.c`)

The **Where** column names the file: `boot.S` is this board's, `trap.S` and
`sink.c` are the shared riscv32 ones in `hal/riscv32-common/kernel/`.

| Symbol | Where | Contract | Why not Saw |
|---|---|---|---|
| `_start` | boot.S (this board) | Reset entry. Sets up the stack, clears the mode witness, installs the trap vector, zeroes `.bss`, calls `kmain`. Never returns. | `csrw`, and a stack pointer before any compiled code can run. |
| `trap_entry` | trap.S (shared) | Machine trap vector. Saves the U-mode context into the running thread's 32-word frame, calls `ktrap(frame, cause, tval)` on the kernel stack, and resumes the frame `ktrap` returns — which need not be the one it was called with, and that is the context switch. A trap taken in kernel mode goes to `kernel_fault` instead. | `csrrw` on `mscratch` as the mode witness, register saves, `mret`. |
| `kernel_fault` | boot.S (this board) | A trap the kernel itself took. Writes the finisher with `mcause` in the code bits and stops the machine. Never returns, never hangs. | `csrr mcause` plus the finisher store, in the one path that must work with no assumptions about kernel state. |
| `sos_resume_frame(frame)` | trap.S (shared) | Enter user mode in a saved context, behind `resume_frame`. Selects U-mode as the `mret` target with the global interrupt enable left clear, then branches into the restore path above rather than repeating it. | `csrc` on `mstatus`, and a `mret` the restore path owns. |
| `sos_pmpaddr_write(index, value)` | sink.c (shared) | Place a word in `pmpaddr<index>`, `index` 0..15. | The CSR number is an assembly-time immediate, so an indexed write is a switch. Sixteen arms is every entry the part implements, so the budget above it is `lib.saw`'s decision alone and there is no further widening available. What a region means is Saw. |
| `sos_mie_write(mask)` | sink.c (shared) | Place a word in `mie` — which classes of interrupt may reach this hart. | `csrw` names its CSR. Which classes, and the shadow the mask is staged in, are Saw. Nothing here writes the global enable (see "Interrupts are masked in the kernel" below). |
| `sos_pmpcfg_write(w0, w1, w2, w3)` | sink.c (shared) | Publish all four config registers together — entries 0-3, 4-7, 8-11, 12-15. | Same: `csrw pmpcfg0` names its register. The config words are staged in Saw, in four shadow variables rather than an array, because a `static` is not an array element. |
| `sos_payload_start()` / `sos_payload_end()` | sink.c (shared) | Bounds of the appended payload. | A linker symbol's address, which Saw cannot name. |
| `sos_region_table_start()` / `sos_region_table_end()` | sink.c (shared) | Bounds of the `.regions` section — the boot region table. The table's blob rows carry bases the linker resolved when it placed the generated stub, so no per-child symbol has to be nameable here. | Same reason. |
| `sos_wait_for_irq()` | sink.c (shared) | Park the core until an interrupt is pending, behind `wait_for_irq`. | `wfi` is an instruction. |
| `virt.ld` | this board | Places the image at this board's RAM base, first section first, and bounds the appended payload on page boundaries at both ends. The rounding is a speed property under emulation rather than a protection one: a PMP region covering part of a page defeats the emulator's per-page translation cache, and the cost under emulation is large enough to matter. | Not a program. |

`sos_rt_write` (check-free by construction so the panic path cannot re-enter
it), `sos_rt_abort` (it is `exit_fail` under the seam's name), and the PMP
arithmetic (`prot_reset` / `prot_region` / `prot_commit` over the two register
writers above) are Saw in `lib.saw`.

## Required of the kernel

- `kmain()` — the Saw entry `_start` calls.
- `ktrap(frame, cause, tval) -> UInt` — the Saw handler `trap_entry` calls. May
  rewrite the frame; a returning syscall goes back through `syscall_return`,
  which is what knows whether the saved PC needs advancing. It returns the
  frame to resume: the same one for an ordinary return, another thread's for a
  switch. The trap entry does not know which happened and must not care.

## Which altitude is supported for whom

Nothing in this directory is an application interface. It is the kernel's own
platform layer: the kernel calls it, and a process never can (every symbol here
lives in M-mode code a process holds no grant for). The three-altitude question
belongs to the user seam — see `hal/riscv32/user/ABI.md`.

The one thing worth stating in both places: the op numbers the trap handler
dispatches on come from `kernel/abi/`, which the exported `sos` module
imports too. The dispatch and the wrappers are the two halves of one contract,
and they are compiled from one definition so they cannot skew.

## What is riscv32-specific here, and why

- **`mscratch` as the mode witness** (0 in the kernel, the running thread's
  frame address in user mode) — one `csrrw` plus one branch separates a syscall
  from a kernel bug. arm64 has separate exception vectors per source EL and
  needs no equivalent. The witness is also how a switch takes effect: the
  restore path re-arms it from the frame it resumed, so the next trap saves into
  the thread that is actually running. Profile B does the same thing through
  `SP_EL1`.
- **A device window is one NAPOT entry, not a TOR pair.** PMP encodes a
  naturally aligned power-of-two region as a single address word whose trailing
  ones give its size, so the console's page costs one entry where a bounded
  region costs two. NAPOT's smallest region is eight bytes, so a window of four
  is one NA4 entry instead, matching exactly those bytes. The budget is
  `PMP_REGIONS` regions, which uses every entry the part implements, so there
  is no further widening — and the NAPOT shape is
  a rule a caller must satisfy, since `carve` lets a process choose a window's
  bounds (`device_window_ok`). The window is page-aligned and page-sized for a
  speed reason rather than a protection one, and it is the same one `virt.ld` is
  rounded for: a byte-tight region puts every access on the emulator's slow
  path, and a driver's register touch is the hottest thing in an interrupt path.
  This profile's device space needs no memory-type bit — it is uncached because
  of where it is — which is why the seam has a device call at all: Profile B has
  to say so in a descriptor.
- **The 32-word trap frame**, word `i` holding `x<i>` and word 0 holding the
  saved PC. `TrapFrame` in `lib.saw` is the same layout declared as a struct;
  the two are one description and must move together. The frame is the
  thread's context: the kernel reserves one per thread, and this file reserves
  none.
- **`ecall` is 4 bytes and `mepc` points at it**, so `syscall_return` advances
  the saved PC. Profile B's `ELR` already points past the `svc`.
- **PMP as TOR pairs.** Region granularity, the reserved write-without-read
  encoding, and the "no match means deny for user mode" default are all
  RISC-V's. Profile B replaces this whole mechanism with page tables. The
  staging is Saw on both profiles — descriptors and config words are data, and
  only publishing them is an instruction.
- **The SiFive test finisher** as the failure channel — QEMU `virt`, not real
  hardware. It is an ordinary MMIO store, so `exit_pass`/`exit_fail` are Saw
  through the MMIO driver idiom; an ESP32-P4 build replaces them with a
  reset. This profile has no `noreturn` C leaf.
- **`UNMAPPED_PROBE` is address 0.** The kernel runs with no translation, so
  nothing is there. On a profile whose kernel runs with an MMU on, address 0 is
  the device window and reading it succeeds.
- **Interrupts are masked in the kernel: `mstatus.MIE` is a bit this kernel
  never sets.** An interrupt reaches M-mode only with `mstatus.MIE` set, and
  reaches a lower privilege mode whatever that bit says — so leaving it clear
  forever is exactly "interrupts are taken from user mode only", with no
  masking in the trap path and no epilogue to get wrong. `mie` still selects
  which classes are reachable, and that is the one interrupt register this HAL
  writes. Should one arrive in kernel mode anyway, the mode witness sends it to
  `kernel_fault`, which stops the machine with the cause in its status: a
  diagnosed bug, never a silent reentry.
- **The timer is not one of the interrupt controller's sources**, and that is
  the difference in the seam a reader trips over first. It is a core-local
  comparator with an interrupt class of its own, so `IRQ_TIMER` here is a number
  one past the controller's last source rather than a line — a `static_assert`
  holds the two apart — and `irq_complete` has nothing to do for it. Profile B
  has the timer on its controller and does the ordinary end-of-interrupt.
- **The timer is memory-mapped, so all of it is Saw**, including the two things
  a split 64-bit counter needs on a 32-bit machine: a carry-safe read (the high
  half either side of the low one) and a write order that never leaves the
  comparator naming a deadline in the past (low half to all-ones first).
- **One comparator, two customers.** The scheduler tick and the Timer objects
  share `mtimecmp`, and the sharing is the kernel's arithmetic, not this HAL's:
  the seam is `timer_now_ns()` + `timer_set_deadline_ns(at)` — a counter read
  and an absolute deadline — and `kernel/core/` programs the earliest deadline
  it owes anybody. What this machine does with such a deadline: converts
  nanoseconds to counter units at the board's 10 MHz timebase (100 ns a unit,
  which divides exactly), rounding up so a deadline never fires early, and
  writes the comparator through the split-write order above. The kernel's
  "never" deadline (`UInt64.max`) saturates to a counter value this machine
  never reaches, which is also what keeps the idle poll quiet when nothing is
  due.
- **A line is masked by priority, not by its enable bit.** This controller
  ignores a completion for a source that is not enabled for the context, so
  masking by disabling between claim and complete would leave the source in
  service forever. Priority 0 is the architecture's "never wins" and is the same
  mask with none of that.
- **`irq_raise_selftest_line` makes the console interrupt.** There is no
  software-trigger register here, so the bring-up path uses the one device this
  kernel already owns: its transmit-empty interrupt asserts as soon as it is
  enabled and needs no input. The kernel masks the line while servicing it,
  which is what stops the still-asserting device re-firing.
