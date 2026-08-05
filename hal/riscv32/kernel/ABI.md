# riscv32 kernel HAL — the seam

What the SOS kernel needs from its architecture, and nothing more. M1b (design
79) adds `sos/hal/arm64/kernel/` implementing this same list; the kernel above
it does not change.

## Provided to the kernel

| Symbol | Contract |
|---|---|
| `_start` | Reset entry. Sets up the stack, clears the mode witness, installs the trap vector, zeroes `.bss`, calls `kmain`. Never returns. |
| `trap_entry` | Machine trap vector. Saves the U-mode context into a 32-word frame, calls `ktrap(frame, cause, tval)` on the kernel stack, restores, `mret`. A trap taken in kernel mode goes to `kernel_fault` instead. |
| `kernel_fault` | A trap the kernel itself took, i.e. a kernel bug. Reports the cause through the platform's failure channel and stops the machine. Never returns, never hangs. |
| `sos_enter_user(entry, stack_top, boot_handle)` | Drop to user mode at `entry`, with `stack_top` as the stack pointer and `boot_handle` in the first argument register. Zeroes every other register. Never returns. |
| `sos_rt_write(ptr, len)` | Write bytes to the kernel's console. Called by `sos/rt/common_c/support.c`. |
| `sos_rt_abort(code)` | Stop the machine with a non-zero status. Never returns. |
| `sos_pmp_reset()` | Revoke every region. User mode then reaches nothing. |
| `sos_pmp_region(idx, base, top, perm)` | Stage region `idx` as `[base, top)` with `perm` (bit 0 R, bit 1 W, bit 2 X). |
| `sos_pmp_commit()` | Publish the staged set. Separate so a half-programmed set is never live. |
| `sos_payload_start()` / `sos_payload_end()` | Bounds of the appended root image. Equal when there is none. Saw cannot name a linker symbol. |

## Required of the kernel

- `kmain()` — the Saw entry `_start` calls.
- `ktrap(frame, cause, tval)` — the Saw handler `trap_entry` calls. May rewrite
  the frame; a returning syscall advances the saved PC past the trapping
  instruction itself.

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
  arm64 has separate exception vectors per level and needs no equivalent.
- **The 32-word trap frame**, word `i` holding `x<i>` and word 0 holding the
  saved PC. `TrapFrame` in `sos/kernel/core/lib.saw` is the same layout
  declared as a struct; the two are one description and must move together.
- **PMP as TOR pairs.** Region granularity, the reserved write-without-read
  encoding, and the "no match means deny for user mode" default are all
  RISC-V's. Profile B replaces this whole file with page tables.
- **The SiFive test finisher** as the failure channel — QEMU `virt`, not real
  hardware. A P4 build replaces `sos_rt_abort` with a reset.
