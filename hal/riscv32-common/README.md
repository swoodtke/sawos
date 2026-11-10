# hal/riscv32-common — the shared riscv32 HAL

Nothing in this directory knows what board it is on. It is the half of a
riscv32 kernel HAL that is true of the ISA — the trap frame, the cause decoding,
the syscall accessors, PMP, the privilege transition, the linker seams — and it
is compiled into `hal/riscv32` (QEMU `virt`) and `hal/riscv32-esp32c3` alike.

```
kernel/lib.saw     the `rv32core` module: trap frame + FRAME_BYTES, the PMP
                   staging apparatus (TOR pairs, cfg packing, NAPOT, the grant
                   budget), frame_init/resume_frame, the payload + region-table
                   seams, the cause/syscall decoding, arch_tag, IRQ_NONE/
                   IRQ_TIMER
kernel/trap.S      trap_entry (the 32-word save/restore and the mode witness)
                   and sos_resume_frame
kernel/sink.c      the CSR and linker-symbol leaves — the C that exists because
                   a `csrw` names its CSR in the instruction
user/syscall.c     the `ecall` stub every riscv32 root and child links
```

## The seam

`kernel/core` imports the module named **`hal`**, and the runner maps
`hal=<board>/kernel` per build. This directory is a second module, `rv32core`,
added as one more `--module-path` on riscv32 builds; each board's `hal` module
`public import`s from it and re-exports what `kcore` consumes. So `kcore` sees
one flat `hal`, and nothing above the HAL can tell that its trap-frame accessors
and its UART come from two files.

A board's `lib.saw` opens with two import lines and they mean different things:

```saw
public import rv32core.{ arch_tag, cause_tag, ... }   // re-exported as `hal`
import rv32core.{INTERRUPT_FLAG, InterruptCause, WORD_BITS}  // the board's own
```

The first is the seam; the second is what the board needs and `kcore` does not.

## Which side does a declaration belong on?

Ask what would change if the board changed. A number or a routine that would be
the same on any RV32 M/U part with PMP belongs here; anything that names a
device, an address map or a register belongs to the board. Worked cases, because
several are less obvious than they look:

- `device_window_ok` is shared (it is PMP's NAPOT rule) while `map_target_ok` is
  per-board (it is where RAM is). Same file, opposite answers.
- The interrupt-class shadow and `irq_class_enable` are per board, even though
  the two tag constants are identical: on `virt` the tags are `mie` bit
  positions and the shadow is what gets written, while the C3's matrix gates
  everything through MEIE and makes them software tags. Identical values, two
  different registers.
- `IRQ_TIMER` is shared (a number the seam promises) but the `static_assert`
  that it cannot collide with a real source is per board (it names the board's
  own source count).
- `boot.S` splits: `_start`, `kernel_fault` and the stack reservation are the
  board's; everything from `trap_entry` down is here.

## The `extern` block is split by caller

An `extern "C"` declaration carries no visibility and cannot be re-exported, so
there is no way to declare `sink.c`'s surface once and share it. It is split
instead: `rv32core` declares the leaves it calls, and each board declares
the ones it calls (`sos_mie_write`, and `sos_wait_for_irq` on `virt` — the C3
deliberately does not call it and therefore does not name it). The two halves
are disjoint, so each declaration has exactly one owner. `trap.S` (the resume
leaf) and `sink.c` (the CSR and linker-symbol leaves) define all of them; the
linker's `--gc-sections` drops whichever a board does not reach.

## No FPU

Both riscv32 boards are FPU-less (`rv32imac` on `virt`, `rv32imc` on the C3), so
`fp_trap`, `fp_switch_out` and `fp_switch_in` here do nothing and no thread has
floating-point state. A board with an FPU follows the arm64 rule instead: the
kernel itself executes no FP instruction, the FPU is disabled for user mode by
default, a thread's first use traps and loads that thread's own (zeroed)
state, and a switch saves and loads state for the threads that have used it.
Those three seams are where it lands, with the save area beside the frame in
the thread's slot and counted in `FRAME_BYTES`.

## Adding a board

Adding a third riscv32 board means: a new `hal/riscv32-<board>/` with its own
`boot.S`, linker scripts, and a `lib.saw` whose two import lines are copied
verbatim from either sibling; a `hal_native` / `hal_modules` / `hal_asm` triple
in the runner's arch table; and nothing here. Board code in the C3 directory is
marked `BOARD (C3)` where it sits.

## arm64

There is one arm64 board in this tree, so its HAL is whole — its own `sink.c`,
its own `boot.S`, no shared module beside its `hal`. A second arm64 board is
when the question of an arm64 shared half gets asked.
