# riscv32 kernel HAL — the seam

What the SOS kernel needs from its architecture, and nothing more.
`sos/hal/arm64/kernel/` implements the same list for Profile B; the kernel
above it does not change, and `tools/sos_runner.py` proves that by scanning
`sos/kernel/core/` for architecture names and failing the run if it finds one.

The seam has two halves. The SAW half (`lib.saw`) is the module the kernel
imports as `hal` — it is the surface, and it is where the arch-free vocabulary
above meets this machine's. The NATIVE half (`boot.S`, `sink.c`) is what Saw
cannot express: a trap vector, a privilege transition, a CSR write whose
operand must be an assembly-time immediate, and a linker symbol.

Design 172 moved the line between them. `sink.c` was 135 lines and is 65: the
NS16550A write loop, the finisher write that stops the machine, and all of the
PMP region arithmetic are Saw now. What is left is four functions, and each
states its own reason at the top of its section.

Design 178's interrupt work added a fifth, and it is ONE LINE (`sos_mie_write`)
— the timer here is memory-mapped and so is the interrupt controller, so the
counter arithmetic, the comparator write order, the priorities, the claim and
the completion are all Saw. C code lines: 22 to 23.

Design 178's THREAD work moved the line again, in the other direction from
usual: `boot.S` went from 176 code lines to 134. The privilege transition used
to build a user context by zeroing twenty-nine registers in assembly; a context
is now a FRAME the kernel owns, built by `frame_init` in Saw, and the transition
is four instructions and a branch into the trap entry's own restore path.

## The Saw surface (`lib.saw`) — what `kcore` may use

| Name | Contract |
|---|---|
| `arch_name() -> String` | What the boot banner says this kernel is running on. |
| `arch_tag() -> UInt8` | This profile's `sosimg` arch tag, read out of `imgformat`'s registry rather than spelled again. An image carrying another one is refused by the loader, not jumped into. |
| `console_byte(b)` | Put one byte on the kernel's console. Byte-at-a-time on purpose: the arch-free half formats, this half places. |
| `exit_pass()` / `exit_fail(code)` | Stop the machine, zero / non-zero. Never return. A zero `code` is promoted so a failing exit never reads as a passing one. |
| `payload_start()` / `payload_end()` | Bounds of the appended root image. Equal when there is none. |
| `PROT_GRAIN: UInt` | Protection granularity — what a region bound is rounded up to. |
| `MAX_ROOT_SEGMENTS: UInt` | How many segments a root image may ask for, i.e. the grant budget minus the stack. |
| `ROOT_LOAD_BASE` / `ROOT_REGION_TOP` / `ROOT_STACK_LEN` | Root's region in this board's memory map. |
| `prot_reset()` | Revoke every grant. User mode then reaches nothing. |
| `prot_region(idx, base, top, perms)` | Stage grant `idx` as `[base, top)` with `perms`, a mask in the `imgformat` `SegFlag` vocabulary (R = 1, W = 2, X = 4). |
| `prot_device(idx, base, len)` | Stage grant `idx` as a DEVICE WINDOW: read/write, never executable, with whatever memory type this machine needs for MMIO. `len` is a power of two and `base` is aligned to it. |
| `DEVICE_GRANT_BASE` / `DEVICE_GRANT_LEN` | The ONE window this board offers a process — the console UART's page. The image DECLARES a window and this pair AUTHORIZES it; anything else is a refused image. |
| `prot_commit()` | Publish the staged set. Separate so a half-programmed set is never live. |
| `is_syscall(cause) -> Bool` | Did this trap come from a syscall instruction, or is it a fault? |
| `is_interrupt(cause) -> Bool` | Is this trap an interrupt instead of either? Asked FIRST, because an interrupt is not the running program's business and its instruction has not run. |
| `cause_tag(cause) -> String` | A short symbolic name for a raw trap cause; unmodelled codes get their own name rather than an arm. |
| `trap_pc(frame) -> UInt` | Where the trapped context resumes: the faulting instruction on a fault, the interrupted one on an interrupt. (Was `fault_pc`; the value never was fault-specific.) |
| `syscall_handle/op/arg0/arg1/arg2(frame) -> UInt` | The §5.7 argument registers, by role rather than by name. Three argument slots since design 178 M2 unit 2: `Process.CreateThread` takes an entry, a stack and an argument. |
| `syscall_return(frame, status, value)` | Place the (status, value) pair where the caller reads them and step the saved PC past the trapping instruction — which is a no-op on a profile whose trap already points past it. |
| `UNMAPPED_PROBE: UInt` | An address the KERNEL cannot reach here. The harness's kernel-fault case reads it; it is per-target because "unmapped" is. |

**THE SYSCALL RETURN CARRIES A (status, value) PAIR AND NOTHING WIDER**, and
that is a deliberate floor rather than a limit nobody hit. Design 178 M2 unit 3
first added a `syscall_return_pair` so `Waiter.Wait` could answer with two
values; the ruling that replaced it (rider 3) took the answer OUT of the
registers entirely — the kernel now COPIES OUT a record into memory the caller
supplies, which is SOS's first kernel-writes-userspace path. The HAL is not
involved in that: a copy-out is a store to an address, and the check that makes
it safe (`copy_out` in `kcore`, the only door) names no machine. So this seam
has exactly the shape it had in M1, and the ops whose answers do not fit a word
grow a buffer argument instead of a register.

## Thread contexts (design 178 M2 unit 2)

THE TRAP FRAME IS A THREAD'S CONTEXT, and that is what makes a context switch
cost nothing extra here: the kernel reserves one frame per thread, the trap
entry saves into the RUNNING thread's, and `ktrap` returns the frame to resume.
Handing back a different one IS the switch. The three rows below are the whole
of what the kernel needs to know about a context; everything else — the layout,
the register roles, the mode the frame resumes in — stays this file's.

| Name | Contract |
|---|---|
| `FRAME_BYTES: Int` | Bytes in one saved context (128 here, 272 on Profile B). The kernel asserts its per-thread stride against it, so a frame that outgrew the stride is a build error. |
| `frame_init(frame, entry, stack_top, arg0)` | Build a fresh user context: the entry as the resume address, the stack pointer, `arg0` in the first argument register, and every other word ZEROED — including the return-address register, so a thread entry that returns faults instead of running on. |
| `resume_frame(frame) -> Never` | Enter user mode in a saved context. The FIRST entry only; every one after it is `ktrap` returning a frame to the trap entry's restore path. |

`enter_user` is GONE. It was the one thing above that a Thread object made
redundant: a context built in assembly, entered once, with no storage a second
thread could have had. Its callers — the loader and four harness kernels — now
go through `kcore.start_process`, which reifies a program as a Process with one
Thread and resumes its frame.

## Interrupts (design 178 M2 unit 1)

The same list again, for the half of the seam that arrived with M2. It is stated
separately only because it is new; `sos/hal/arm64/kernel/` implements every row.

| Name | Contract |
|---|---|
| `IRQ_NONE: UInt` | What a claim answers when the controller had nothing to give. Zero on both profiles, and never a real line on either. |
| `IRQ_TIMER: UInt` | The line the periodic timer arrives on. |
| `intc_init()` | Bring the interrupt controller up with every line masked. |
| `irq_unmask(line)` / `irq_mask(line)` | Let a line interrupt the kernel, or stop it. Masking is spec §9's mask-on-fire; the Interrupt object's ack will be what unmasks. |
| `irq_claim(cause) -> UInt` | Which line is being serviced, `IRQ_NONE` if none. Takes the cause because one profile answers the timer half out of it. |
| `irq_poll() -> UInt` | The same question with NO trap behind it — the idle path's half of the seam (design 178 M2 unit 4). Here it also has to check the timer by hand, because on this profile the timer is not one of the controller's sources; there it is `irq_claim` under a second name. |
| `wait_for_irq()` | Park the core until an interrupt is PENDING. Both machines wake from this whether or not the current privilege level would take one, which is exactly what the idle path needs — D2 keeps them masked in kernel mode forever, so the kernel notices by polling rather than by trapping. |
| `irq_line_valid(line) -> Bool` | Is this a line the BOARD wires? What `Process.CreateInterrupt` checks a caller's number against. The TIMER's line is excluded arch-free by the kernel instead, because "the tick is not for rent" is a policy rather than a fact about the board. |
| `irq_complete(line)` | End of service for a line. |
| `timer_start(period_us)` | Arm a periodic tick and make it reachable. |
| `timer_rearm()` | Schedule the next tick. On both profiles this is also what LOWERS the timer's line, which is why the tick path calls it rather than acknowledging something. |
| `timer_pending() -> Bool` | Has the timer come due without a tick having been taken for it? The masking case's evidence. |
| `irq_raise_selftest_line() -> UInt` | Raise a line from software, for bring-up, and say which. Per-board because what a board can interrupt itself with is: here a real device, on Profile B a software-generated line. |

**`irq_raise_selftest_line` STAYS, and unit 1 expected it not to** (design 178 M2
unit 4). The role it was built for is genuinely covered now — the echo driver
takes a real device line round the claim / mask / complete cycle on both
profiles — but two claims are only reachable through it. It raises a line IN THE
KERNEL, so the transcript reads "raised, entered user mode, only then serviced",
which is D2 for a DEVICE line where the timer case makes the same claim for a
timer; and the line it raises is bound to NO Interrupt object, which is the one
path where `on_external_irq` reports and leaves the line masked. Retiring it
would delete both.

WHAT THE KERNEL PROVIDES ABOVE THIS: one funnel with TWO WAYS IN. `ktrap`
decides interrupt / fault / syscall in that order and hands an interrupt to
`service_irq`; the idle path polls and hands one to `idle_poll`. Both meet at
`deliver_line`, which rearms the timer or masks a device line, runs the hook
that owns it, and completes. The two hooks — the timer tick and a device line —
are where the scheduler and the Interrupt object land.

This surface is unchanged by the design 172 review round; what changed is how
much of it this file writes. `console_byte`, `exit_fail` and the `sos_rt_write`
seam are now thin over `sosrt`: the poll-and-place (`ConsoleSink.write_byte`),
the panic path's write LOOP (`console_write`, generic and monomorphized here)
and the exit-status promotion (`abort_status`) live there, once, for both
profiles. What stays here is the DEVICE — `can_write` reads LSR bit 5, `put`
stores to THR — and the mechanism that stops the machine.

## The native half (`boot.S`, `sink.c`)

| Symbol | Where | Contract | Why not Saw |
|---|---|---|---|
| `_start` | boot.S | Reset entry. Sets up the stack, clears the mode witness, installs the trap vector, zeroes `.bss`, calls `kmain`. Never returns. | `csrw`, and a stack pointer before any compiled code can run. |
| `trap_entry` | boot.S | Machine trap vector. Saves the U-mode context into the RUNNING THREAD'S 32-word frame, calls `ktrap(frame, cause, tval)` on the kernel stack, and resumes the frame `ktrap` RETURNS — which need not be the one it was called with, and that is the context switch. A trap taken in kernel mode goes to `kernel_fault` instead. | `csrrw` on `mscratch` as the mode witness, register saves, `mret`. |
| `kernel_fault` | boot.S | A trap the kernel itself took. Writes the finisher with `mcause` in the code bits and stops the machine. Never returns, never hangs. | `csrr mcause` plus the finisher store, in the one path that must work with no assumptions about kernel state. |
| `sos_resume_frame(frame)` | boot.S | Enter user mode in a saved context, behind `resume_frame`. Selects U-mode as the `mret` target with the global interrupt enable left clear (D2), then branches into the restore path above rather than repeating it. | `csrc` on `mstatus`, and a `mret` the restore path owns. |
| `sos_pmpaddr_write(index, value)` | sink.c | Place a word in `pmpaddr<index>`. | The CSR NUMBER is an assembly-time immediate, so an indexed write is a switch. What a region MEANS is Saw (design 172 unit 1). |
| `sos_mie_write(mask)` | sink.c | Place a word in `mie` — which CLASSES of interrupt may reach this hart. | `csrw` names its CSR. WHICH classes, and the shadow the mask is staged in, are Saw. Note what is absent: nothing here writes the GLOBAL enable, and that absence is design 178's D2. |
| `sos_pmpcfg_write(lo, hi)` | sink.c | Publish both config registers together. | Same: `csrw pmpcfg0` names its register. The config words are STAGED in Saw. |
| `sos_payload_start()` / `sos_payload_end()` | sink.c | Bounds of the appended payload. | A linker symbol's ADDRESS, which Saw cannot name — DF-172a. |
| `sos_wait_for_irq()` | sink.c | Park the core until an interrupt is pending, behind `wait_for_irq`. | `wfi` is an INSTRUCTION. One line, and it is the whole of design 178 M2 unit 4's native delta on this profile. |
| `virt.ld` | — | Places the image at this board's RAM base, first section first, and bounds the appended payload — on PAGE boundaries at both ends since design 178, which is a speed property under emulation rather than a protection one (DF-178b: a PMP region covering part of a page defeats the emulator's per-page translation cache, and the same user-mode loop measured 62.6s before the round-up and 0.03s after). | Not a program. |

Moved to `lib.saw` by design 172, and no longer C: `sos_rt_write` (unit 4, and
now check-free by construction so the panic path cannot re-enter it),
`sos_rt_abort` (unit 4 — it is `exit_fail` under the seam's name), and
`sos_pmp_reset` / `sos_pmp_region` / `sos_pmp_commit`, whose arithmetic became
`prot_reset` / `prot_region` / `prot_commit` over the two register writers above
(unit 1).

## Required of the kernel

- `kmain()` — the Saw entry `_start` calls.
- `ktrap(frame, cause, tval) -> UInt` — the Saw handler `trap_entry` calls. May
  rewrite the frame; a returning syscall goes back through `syscall_return`,
  which is what knows whether the saved PC needs advancing. It RETURNS THE FRAME
  TO RESUME (design 178 M2 unit 2): the same one for an ordinary return, another
  thread's for a switch. The trap entry does not know which happened and must
  not care.

## Which altitude is supported for whom

Nothing in this directory is an application interface. It is the kernel's own
platform layer: the kernel calls it, and a process never can (every symbol here
lives in M-mode code a process holds no grant for). The three-altitude question
belongs to the USER seam — see `sos/hal/riscv32/user/ABI.md`.

The one thing worth stating in both places: the op NUMBERS the trap handler
dispatches on come from `sos/kernel/abi/`, which the exported `sos` module
imports too. The dispatch and the wrappers are the two halves of one contract,
and they are compiled from one definition so they cannot skew.

## What is riscv32-specific here, and why

- **`mscratch` as the mode witness** (0 in the kernel, the RUNNING THREAD'S
  FRAME ADDRESS in user mode) — one `csrrw` plus one branch separates a syscall
  from a kernel bug. arm64 has separate exception vectors per source EL and
  needs no equivalent. Since design 178 M2 unit 2 the witness is also how a
  switch takes effect: the restore path re-arms it from the frame it resumed, so
  the next trap saves into the thread that is actually running. Profile B does
  the same thing through `SP_EL1`.
- **A DEVICE WINDOW IS ONE NAPOT ENTRY, not a TOR pair** (design 178 M2 unit 4).
  PMP encodes a naturally aligned power-of-two region as a single address word
  whose trailing ones give its size, so the console's page costs one entry where
  a bounded region costs two — which is what keeps the four-region budget intact
  once an image asks for segments, a stack AND a window. The window is
  page-aligned and page-sized for a SPEED reason rather than a protection one,
  and it is the same one `virt.ld` is rounded for: DF-178b measured a
  byte-tight region putting every access on the emulator's slow path, and a
  driver's register touch is the hottest thing in an interrupt path. This
  profile's device space needs no memory-type bit — it is uncached because of
  where it IS — which is why the seam has a device call at all: Profile B has to
  say so in a descriptor.
- **The 32-word trap frame**, word `i` holding `x<i>` and word 0 holding the
  saved PC. `TrapFrame` in `lib.saw` is the same layout declared as a struct;
  the two are one description and must move together. The FRAME IS THE THREAD'S
  CONTEXT — the kernel reserves one per thread, this file no longer reserves any.
- **`ecall` is 4 bytes and `mepc` points AT it**, so `syscall_return` advances
  the saved PC. Profile B's `ELR` already points past the `svc`.
- **PMP as TOR pairs.** Region granularity, the reserved write-without-read
  encoding, and the "no match means deny for user mode" default are all
  RISC-V's. Profile B replaces this whole mechanism with page tables. The
  STAGING is Saw on both profiles since design 172 unit 1 — descriptors and
  config words are data, and only publishing them is an instruction.
- **The SiFive test finisher** as the failure channel — QEMU `virt`, not real
  hardware. It is an ordinary MMIO store, so `exit_pass`/`exit_fail` are Saw
  through the design-112 driver idiom; a P4 build replaces them with a reset.
  One consequence worth knowing: this profile has no `noreturn` C leaf left,
  which is what makes DF-172e bite here and not on Profile B.
- **`UNMAPPED_PROBE` is address 0.** The kernel runs with no translation, so
  nothing is there. On a profile whose kernel runs with an MMU on, address 0 is
  the device window and reading it succeeds.
- **D2 IS A BIT THIS KERNEL NEVER SETS.** An interrupt reaches M-mode only with
  `mstatus.MIE` set, and reaches a LOWER privilege mode whatever that bit says —
  so leaving it clear forever is exactly design 178's "interrupts are taken from
  user mode only", with no masking in the trap path and no epilogue to get
  wrong. `mie` still selects which CLASSES are reachable, and that is the one
  interrupt register this HAL writes. Should one arrive in kernel mode anyway,
  the mode witness sends it to `kernel_fault`, which stops the machine with the
  cause in its status: a diagnosed bug, never a silent reentry.
- **The timer is NOT one of the interrupt controller's sources**, and that is
  the difference in the seam a reader trips over first. It is a core-local
  comparator with an interrupt class of its own, so `IRQ_TIMER` here is a number
  one past the controller's last source rather than a line — a `static_assert`
  holds the two apart — and `irq_complete` has nothing to do for it. Profile B
  has the timer ON its controller and does the ordinary end-of-interrupt.
- **The timer is memory-mapped, so all of it is Saw**, including the two things
  a split 64-bit counter needs on a 32-bit machine: a carry-safe read (the high
  half either side of the low one) and a write order that never leaves the
  comparator naming a deadline in the past (low half to all-ones first).
- **A line is masked by PRIORITY, not by its enable bit.** This controller
  ignores a completion for a source that is not enabled for the context, so
  masking by disabling between claim and complete would leave the source in
  service forever. Priority 0 is the architecture's "never wins" and is the same
  mask with none of that.
- **`irq_raise_selftest_line` makes the CONSOLE interrupt.** There is no
  software-trigger register here, so the bring-up path uses the one device this
  kernel already owns: its transmit-empty interrupt asserts as soon as it is
  enabled and needs no input. The kernel masks the line while servicing it,
  which is what stops the still-asserting device re-firing.
