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
| `cause_tag(cause) -> String` | A short symbolic name for a raw trap cause; unmodelled codes get their own name rather than an arm. |
| `fault_pc(frame) -> UInt` | Where the trapped instruction was, for a fault line. |
| `syscall_handle/op/arg0(frame) -> UInt` | The §5.7 argument registers, by role rather than by name. |
| `syscall_return(frame, status, value)` | Place the (status, value) pair where the caller reads them and step the saved PC past the trapping instruction — which is a no-op on a profile whose trap already points past it. |
| `UNMAPPED_PROBE: UInt` | An address the KERNEL cannot reach here. The harness's kernel-fault case reads it; it is per-target because "unmapped" is. |

## The native half (`boot.S`, `sink.c`)

| Symbol | Contract |
|---|---|
| `_start` | Reset entry. Sets up the stack, clears the mode witness, installs the trap vector, zeroes `.bss`, calls `kmain`. Never returns. |
| `trap_entry` | Machine trap vector. Saves the U-mode context into a 32-word frame, calls `ktrap(frame, cause, tval)` on the kernel stack, restores, `mret`. A trap taken in kernel mode goes to `kernel_fault` instead. |
| `kernel_fault` | A trap the kernel itself took, i.e. a kernel bug. Reports the cause through the platform's failure channel and stops the machine. Never returns, never hangs. |
| `sos_enter_user(entry, stack_top, boot_handle)` | The privilege transition behind `enter_user`. |
| `sos_rt_write(ptr, len)` | Write bytes to the kernel's console. Called by `sos/rt/common_c/support.c`. |
| `sos_rt_abort(code)` | Stop the machine with a non-zero status. Never returns. |
| `sos_pmp_reset/region/commit` | The PMP programming behind `prot_*`. |
| `sos_payload_start()` / `sos_payload_end()` | The linker symbols Saw cannot name. |
| `virt.ld` | Places the image at this board's RAM base, first section first, and bounds the appended payload. |

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
  RISC-V's. Profile B replaces this whole mechanism with page tables.
- **The SiFive test finisher** as the failure channel — QEMU `virt`, not real
  hardware. A P4 build replaces `sos_rt_abort` with a reset.
- **`UNMAPPED_PROBE` is address 0.** The kernel runs with no translation, so
  nothing is there. On a profile whose kernel runs with an MMU on, address 0 is
  the device window and reading it succeeds.
