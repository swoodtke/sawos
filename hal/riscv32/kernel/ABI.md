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
| `prot_commit()` | Publish the staged set. Separate so a half-programmed set is never live. |
| `enter_user(entry, stack_top, boot_handle)` | Drop to user mode, `boot_handle` in the first argument register, every other register zeroed. Never returns. |
| `is_syscall(cause) -> Bool` | Did this trap come from a syscall instruction, or is it a fault? |
| `is_interrupt(cause) -> Bool` | Is this trap an interrupt instead of either? Asked FIRST, because an interrupt is not the running program's business and its instruction has not run. |
| `cause_tag(cause) -> String` | A short symbolic name for a raw trap cause; unmodelled codes get their own name rather than an arm. |
| `trap_pc(frame) -> UInt` | Where the trapped context resumes: the faulting instruction on a fault, the interrupted one on an interrupt. (Was `fault_pc`; the value never was fault-specific.) |
| `syscall_handle/op/arg0(frame) -> UInt` | The §5.7 argument registers, by role rather than by name. |
| `syscall_return(frame, status, value)` | Place the (status, value) pair where the caller reads them and step the saved PC past the trapping instruction — which is a no-op on a profile whose trap already points past it. |
| `UNMAPPED_PROBE: UInt` | An address the KERNEL cannot reach here. The harness's kernel-fault case reads it; it is per-target because "unmapped" is. |

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
| `irq_complete(line)` | End of service for a line. |
| `timer_start(period_us)` | Arm a periodic tick and make it reachable. |
| `timer_rearm()` | Schedule the next tick. On both profiles this is also what LOWERS the timer's line, which is why the tick path calls it rather than acknowledging something. |
| `timer_pending() -> Bool` | Has the timer come due without a tick having been taken for it? The masking case's evidence. |
| `irq_raise_selftest_line() -> UInt` | Raise a line from software, for bring-up, and say which. Per-board because what a board can interrupt itself with is: here a real device, on Profile B a software-generated line. The Interrupt object is what retires it. |

WHAT THE KERNEL PROVIDES ABOVE THIS: one funnel. `ktrap` decides interrupt /
fault / syscall in that order and hands an interrupt to `service_irq`, which
claims, runs the arch-free hook that owns the line, and completes. There is no
second entry point, and the two hooks — the timer tick and a device line — are
where the scheduler and the Interrupt object land.

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
| `trap_entry` | boot.S | Machine trap vector. Saves the U-mode context into a 32-word frame, calls `ktrap(frame, cause, tval)` on the kernel stack, restores, `mret`. A trap taken in kernel mode goes to `kernel_fault` instead. | `csrrw` on `mscratch` as the mode witness, register saves, `mret`. |
| `kernel_fault` | boot.S | A trap the kernel itself took. Writes the finisher with `mcause` in the code bits and stops the machine. Never returns, never hangs. | `csrr mcause` plus the finisher store, in the one path that must work with no assumptions about kernel state. |
| `sos_enter_user(entry, stack_top, boot_handle)` | boot.S | The privilege transition behind `enter_user`. | `csrw` to `mepc`/`mstatus`/`mscratch`, `mret`, and a register file the caller must not be able to leave anything in. |
| `sos_pmpaddr_write(index, value)` | sink.c | Place a word in `pmpaddr<index>`. | The CSR NUMBER is an assembly-time immediate, so an indexed write is a switch. What a region MEANS is Saw (design 172 unit 1). |
| `sos_mie_write(mask)` | sink.c | Place a word in `mie` — which CLASSES of interrupt may reach this hart. | `csrw` names its CSR. WHICH classes, and the shadow the mask is staged in, are Saw. Note what is absent: nothing here writes the GLOBAL enable, and that absence is design 178's D2. |
| `sos_pmpcfg_write(lo, hi)` | sink.c | Publish both config registers together. | Same: `csrw pmpcfg0` names its register. The config words are STAGED in Saw. |
| `sos_payload_start()` / `sos_payload_end()` | sink.c | Bounds of the appended payload. | A linker symbol's ADDRESS, which Saw cannot name — DF-172a. |
| `virt.ld` | — | Places the image at this board's RAM base, first section first, and bounds the appended payload. | Not a program. |

Moved to `lib.saw` by design 172, and no longer C: `sos_rt_write` (unit 4, and
now check-free by construction so the panic path cannot re-enter it),
`sos_rt_abort` (unit 4 — it is `exit_fail` under the seam's name), and
`sos_pmp_reset` / `sos_pmp_region` / `sos_pmp_commit`, whose arithmetic became
`prot_reset` / `prot_region` / `prot_commit` over the two register writers above
(unit 1).

## Required of the kernel

- `kmain()` — the Saw entry `_start` calls.
- `ktrap(frame, cause, tval)` — the Saw handler `trap_entry` calls. May rewrite
  the frame; a returning syscall goes back through `syscall_return`, which is
  what knows whether the saved PC needs advancing.

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

- **`mscratch` as the mode witness** (0 in the kernel, `&_trapframe` in user
  mode) — one `csrrw` plus one branch separates a syscall from a kernel bug.
  arm64 has separate exception vectors per source EL and needs no equivalent.
- **The 32-word trap frame**, word `i` holding `x<i>` and word 0 holding the
  saved PC. `TrapFrame` in `lib.saw` is the same layout declared as a struct;
  the two are one description and must move together.
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
