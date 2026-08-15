# arm64 kernel HAL — the seam

What the SOS kernel needs from its architecture, and nothing more. This
directory implements the same list `sos/hal/riscv32/kernel/ABI.md` states, for
Profile B (spec §5b): kernel at EL1, processes at EL0, QEMU `virt` with
`-cpu cortex-a53`. The kernel above it does not change, and
`tools/sos_runner.py` proves that by scanning `sos/kernel/core/` for
architecture names and failing the run if it finds one.

The seam has two halves. The SAW half (`lib.saw`) is the module the kernel
imports as `hal`. The NATIVE half (`boot.S`, `sink.c`) is what Saw cannot
express: a vector table, an `eret`, a system-register write, a semihosting
call, and a linker symbol.

Design 172 moved the line between them. `sink.c` was 304 lines and is 130:
the PL011 write loop, the page-table construction, the grant editing, the
kernel-fault report and its hex formatting are all Saw now. What is left is
four functions, and each states its own reason at the top of its section — an
instruction with no Saw spelling, or a linker symbol's address (DF-172a).

Design 178's interrupt work added four more, one LINE each: this profile's timer
is system registers, so reading its frequency, reading and writing its control
and setting its countdown cannot be anything but instructions. Everything above
them — the period arithmetic, the tick policy, and the whole interrupt
controller, which is memory-mapped — is Saw. C code lines: 47 to 51.

## The Saw surface (`lib.saw`)

Identical in name and meaning to the riscv32 HAL's — that table is not repeated
here, and neither is the interrupt half design 178 added beside it (the timer,
the controller, and the one funnel above them). What follows is only what this
profile does differently, and why.

The design 172 review round changed how much of that surface this file writes,
not the surface: the poll-and-place, the panic path's write LOOP and the
exit-status promotion are `sosrt`'s, once, for both profiles. What stays here is
the DEVICE, and the PL011's polarity is the reason this is a trait rather than a
shared function — readiness is FR bit 5 CLEAR (transmit FIFO not full), the
opposite sense to the 16550's LSR bit 5 SET, and `put` stores a `UInt32` to DR
rather than a byte to THR.

## The native half

| Symbol | Where | Contract | Why not Saw |
|---|---|---|---|
| `_start` | boot.S | Reset entry. Stack, `VBAR_EL1`, `CPACR_EL1.FPEN`, `.bss` zerofill, `sos_mmu_init`, `kmain`. Never returns. | Instructions: `msr`, and a stack pointer before any compiled code can run. |
| `_vectors` | boot.S | The 16-entry EL1 vector table. TWO entries are a user trap now — "lower EL, AArch64, synchronous" and, since design 178, "lower EL, AArch64, IRQ"; every other entry is a kernel bug (an interrupt among them, see D2 below) and lands on `kernel_fault_entry`. | A vector table is placement + branches at fixed 0x80 strides. |
| `user_trap_entry` | boot.S | Builds the 34-doubleword frame on the kernel stack, calls `ktrap(frame, ESR, FAR)`, restores, `eret`. | Register saves and `eret`. |
| `user_irq_entry` | boot.S | The same frame, then `ktrap(frame, IRQ_CAUSE, 0)`, then the same return path — the save is a macro and the restore is shared code, so the two entries cannot drift. | Register saves and `eret`. |
| `kernel_fault_entry` | boot.S | Reads `ESR`/`ELR`/`FAR` and calls the SAW `sos_kernel_fault` with them. | `mrs` names a register at assembly time. The REPORT is Saw (design 172 unit 3). |
| `sos_enter_user(entry, stack_top, boot_handle)` | boot.S | `ELR_EL1` / `SP_EL0` / `SPSR_EL1` = EL0t with interrupts masked, every register but x0 zeroed, `eret`. | `msr` + `eret` + a register file the caller must not be able to leave anything in. |
| `sos_platform_exit(code)` | sink.c | Stop the machine through semihosting `SYS_EXIT`. | `hlt #0xf000` with the call number and parameter block pinned in x0/x1. |
| `sos_mmu_init()` | sink.c | Ask `lib.saw` for a finished identity map, then turn the MMU on. Called by `_start` after `.bss` is zeroed, because the tables live there. | `msr`/`mrs` to four system registers plus `dsb`/`isb`. The MAP is Saw (design 172 unit 1). |
| `sos_prot_commit()` | sink.c | Publish the staged grant set. | `dsb`/`isb` barriers and a `tlbi`. The DESCRIPTORS are Saw. |
| `sos_timer_freq()` / `sos_timer_ctl_read()` / `sos_timer_ctl_write(v)` / `sos_timer_set_countdown(n)` | sink.c | The core's physical timer: its frequency, its control register (enabled / masked / fired), and the down-counter whose expiry raises the line. | `mrs`/`msr` name a system register at assembly time. One instruction each; the period arithmetic and the tick policy are Saw. |
| `sos_payload_start()` / `sos_payload_end()` | sink.c | Bounds of the appended payload. | A linker symbol's ADDRESS, which Saw cannot name — DF-172a. |
| `virt.ld` | — | Places the image at RAM base 0x4000_0000 and bounds the appended payload on PAGE boundaries — protection granularity here is the page. | Not a program. |

Moved to `lib.saw` by design 172, and no longer C: `sos_rt_write` (unit 4, and
now check-free by construction so the panic path cannot re-enter it),
`sos_rt_abort` (unit 4), `sos_kernel_fault` with its `put_str`/`put_hex` (unit
3), `sos_prot_reset` / `sos_prot_region` and the whole page-table build (unit
1), which reaches C only as `sos_page_tables_build` and `sos_mair_value`.

## Required of the kernel

- `kmain()` — the Saw entry `_start` calls.
- `ktrap(frame, cause, tval)` — the Saw handler `user_trap_entry` calls. `cause`
  is `ESR_EL1`, `tval` is `FAR_EL1`.

## What is arm64-specific here, and why

- **No mode witness.** RISC-V needs `mscratch` (0 in the kernel, `&_trapframe`
  in user mode) to tell a syscall from a kernel bug. Here the hardware picks a
  different vector per source EL, so the question is answered by which of
  sixteen entries ran. That also means the frame is built on `SP_EL1` with no
  window in which the kernel runs on the user's stack.
- **`svc` traps with `ELR_EL1` already past the instruction**, so
  `syscall_return` advances nothing. Doing what Profile A does here would skip
  the instruction AFTER a syscall — which is exactly why "how far to resume" is
  a HAL decision rather than a kernel one.
- **FP/SIMD must be enabled before any compiled code runs, and the reason is
  now ONLY the C.** `CPACR_EL1.FPEN` traps Advanced SIMD at EL1 out of reset,
  and LLVM reaches for `q` registers to move a struct, so the first
  table-filling loop faulted before this line existed. Design 172 unit 7 made
  the freestanding profile imply `-neon,-fp-armv8` on aarch64, which took the
  Saw half from five SIMD instructions to zero — so the boot line is no longer
  there for Saw's sake. It stays because `sos/rt/common_c/support.c` is
  PERMANENTLY C (its `memcpy` is the loop-idiom self-recursion case) and
  compiles to 16 SIMD references at `-O2`. Removing it needs
  `-mgeneral-regs-only` on every aarch64 C compile, which means a Blade manifest
  key for per-target C flags — see DF-172c. FP state is NOT saved across a trap
  (the frame holds x0-x30 and three system registers); with one user thread and
  no preemption nothing can observe that, and M2's context switch is where it
  stops being true.
- **Semihosting, not PSCI, for shutdown.** PSCI `SYSTEM_OFF` over the HVC
  conduit works on `-M virt` but always exits the emulator with status 0, and
  this harness asserts on exit STATUS — one case encodes its entire verdict in
  the number. Semihosting `SYS_EXIT` carries a subcode QEMU exits with, which is
  the SiFive finisher's shape. It needs `-semihosting` on the QEMU command line.
- **A static identity map instead of PMP** (design 162 decision 2). One map,
  built once, whose only mutable part is the EL0 permission bits of the pages a
  root image was granted:

  | Level | Covers | State |
  |---|---|---|
  | 1 `[0]` | 0x0000_0000, 1 GiB block | device, EL1 RW, never executable |
  | 1 `[1]` | 0x4000_0000, 1 GiB | table -> level 2 |
  | 1 rest | everything above 2 GiB | INVALID — which is what makes `UNMAPPED_PROBE` fault |
  | 2 `[0]`, `[1]` | the first 4 MiB of RAM | tables -> level 3: the GRANT WINDOW |
  | 2 rest | RAM above 4 MiB | 2 MiB blocks, EL1 RW, no EL0 |
  | 3 | 1024 pages | reset: EL1 read/write/execute, no EL0. Granted: EL0 RO or RW, `PXN` always |

  EL0 default-deny is the same property PMP gives for free: a page EL0 was never
  granted is a translation fault. The kernel's own pages staying EL1-RWX is the
  arm64 spelling of Profile A's unconstrained M-mode, not a claim about kernel
  W^X — neither profile has that yet. The window covers the kernel image as well
  as the root region because the harness's unit-A cases grant a payload that
  executes IN PLACE inside it.

  A grant installs exactly what the image asked for, and `PXN` on top: the
  kernel must not execute user memory even by accident, which is a guarantee
  Profile A cannot make.
- **Table walks are cacheable and inner-shareable**, so a descriptor written
  with the MMU on is visible to the walker without cache maintenance and
  `prot_commit` only has to order and flush the TLB. The tables are built
  BEFORE the MMU comes on, where a real board would want a data-cache clean
  first; QEMU does not model caches, and a board port is where that stops being
  free.
- **The interrupt controller is v2, and it is PINNED** (design 178 M2 unit 1).
  `tools/sos_runner.py` passes `gic-version=2` rather than taking the machine's
  default: this HAL programs a v2 distributor and CPU interface, and a newer
  emulator changing its default would swap the hardware under a kernel with no
  way to say so. A v3 port is system registers instead of the CPU-interface
  window and is a HAL change, not a kernel one.
- **The timer IS a controller line here** (a per-core one, 30), which is the
  seam difference a reader trips over: Profile A's timer is a core-local
  comparator with an interrupt class of its own and never reaches its
  controller, so its `IRQ_TIMER` is a stand-in number and its `irq_complete`
  does nothing for the timer. Here every tick goes round the ordinary
  claim/complete cycle, which is also why THIS profile exercises that cycle
  without the selftest line and Profile A does not.
- **D2 is masking plus a vector.** The kernel runs with interrupts masked at
  its own exception level throughout — the hardware masks on every exception
  entry, and the boot path never unmasks — and the user-mode return is where
  they come back on: `SPSR_EL0T` leaves the I bit CLEAR (it was set until
  design 178), and a returning trap restores the user's own saved state. Should
  an interrupt arrive at kernel level anyway, the four current-EL vectors send
  it to `kernel_fault_entry`: a diagnosed stop, never a silent reentry.
- **The interrupt entry passes a cause the syndrome register cannot hold.** An
  interrupt has its own vector and no syndrome, but the kernel's handler takes
  one cause word for every trap, so `user_irq_entry` passes `1 << 32` — this
  architecture's syndrome is 32 bits, so no fault or syscall can look like it.
  `boot.S`'s `IRQ_CAUSE_HI` and `lib.saw`'s `IRQ_CAUSE` are one description and
  must move together, exactly as the frame size is.
- **`irq_raise_selftest_line` raises a software-generated line**, because this
  controller has a register for exactly that. Profile A has none and has to make
  a real device interrupt instead — the same seam, answered by what each board
  can do.
