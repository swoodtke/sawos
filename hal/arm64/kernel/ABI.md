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

## The Saw surface (`lib.saw`)

Identical in name and meaning to the riscv32 HAL's — that table is not repeated
here. What follows is only what this profile does differently, and why.

## The native half

| Symbol | Contract |
|---|---|
| `_start` | Reset entry. Stack, `VBAR_EL1`, `CPACR_EL1.FPEN`, `.bss` zerofill, `sos_mmu_init`, `kmain`. Never returns. |
| `_vectors` | The 16-entry EL1 vector table. Only "lower EL, AArch64, synchronous" is a user trap; every other entry is a kernel bug or an interrupt M1 never enables, and all of them land on `kernel_fault_entry`. |
| `user_trap_entry` | Builds the 34-doubleword frame on the kernel stack, calls `ktrap(frame, ESR, FAR)`, restores, `eret`. |
| `kernel_fault_entry` / `sos_kernel_fault` | A trap the kernel itself took. Reports the exception class and stops the machine with it as the status. Never returns, never hangs; a fault while reporting exits immediately rather than looping. |
| `sos_enter_user(entry, stack_top, boot_handle)` | `ELR_EL1` / `SP_EL0` / `SPSR_EL1` = EL0t with interrupts masked, every register but x0 zeroed, `eret`. |
| `sos_rt_write(ptr, len)` | PL011 output. Called by `sos/rt/common_c/support.c`. |
| `sos_platform_exit(code)` / `sos_rt_abort(code)` | Stop the machine through semihosting `SYS_EXIT`. |
| `sos_mmu_init()` | Build the static identity map and turn the MMU on. Called by `_start` after `.bss` is zeroed, because the tables live there. |
| `sos_prot_reset/region/commit` | The page-attribute editing behind `prot_*`. |
| `sos_payload_start()` / `sos_payload_end()` | The linker symbols Saw cannot name. |
| `virt.ld` | Places the image at RAM base 0x4000_0000 and bounds the appended payload on PAGE boundaries — protection granularity here is the page. |

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
- **FP/SIMD must be enabled before any compiled code runs.** `CPACR_EL1.FPEN`
  traps Advanced SIMD at EL1 out of reset, and LLVM vectorizes ordinary loops in
  both the C and the Saw halves, so the first table-filling loop faulted before
  this line existed. FP state is NOT saved across a trap (the frame holds
  x0-x30 and three system registers); with one user thread and no preemption
  nothing can observe that, and M2's context switch is where it stops being
  true.
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
- **No GIC.** M1 enables no interrupt sources on either profile; the vector
  table routes IRQ/FIQ to the kernel-bug path so an unexpected one is a
  diagnostic rather than a silent return. The GIC arrives with M2.
