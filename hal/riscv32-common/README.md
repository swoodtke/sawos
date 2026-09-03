# hal/riscv32-common — the shared riscv32 HAL (design 23)

**Nothing in this directory knows what board it is on.** It is the half of a
riscv32 kernel HAL that is true of the ISA — the trap frame, the cause decoding,
the syscall accessors, PMP, the privilege transition, the linker seams — and it
is compiled into `hal/riscv32` (QEMU `virt`) and `hal/riscv32-esp32c3` alike.

```
kernel/lib.saw     the `rv32core` MODULE: trap frame + FRAME_BYTES, the PMP
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

## The seam did NOT move

`kernel/core` imports the module named **`hal`**, and the runner still maps
`hal=<board>/kernel` per build. This directory is a SECOND module, `rv32core`,
added as one more `--module-path` on riscv32 builds; each board's `hal` module
`public import`s from it and re-exports what `kcore` consumes. So `kcore`'s view
of `hal` is byte-for-byte what it was, and nothing above the HAL can tell that
its trap-frame accessors and its UART now come from two files.

Concretely, a board's `lib.saw` opens with two import lines and they mean
different things:

```saw
public import rv32core.{ arch_tag, cause_tag, ... }   // re-exported AS `hal`
import rv32core.{INTERRUPT_FLAG, InterruptCause, WORD_BITS}  // the board's own
```

The first is the seam; the second is what the board needs and `kcore` does not.

## Which side does a declaration belong on?

**Ask what would change if the board changed.** A number or a routine that would
be the same on any RV32 M/U part with PMP belongs here; anything that names a
device, an address map or a register belongs to the board. Worked cases, because
several are less obvious than they look:

- `device_window_ok` is SHARED (it is PMP's NAPOT rule) while `map_target_ok` is
  per-board (it is where RAM is). Same file, opposite answers.
- The interrupt-CLASS shadow and `irq_class_enable` are PER BOARD, even though
  the two tag constants are identical: on `virt` the tags ARE `mie` bit
  positions and the shadow is what gets written, while the C3's matrix gates
  everything through MEIE and makes them software tags. Identical values, two
  different registers.
- `IRQ_TIMER` is shared (a number the seam promises) but the `static_assert`
  that it cannot collide with a real source is per board (it names the board's
  own source count).
- `boot.S` splits: `_start`, `kernel_fault` and the stack reservation are the
  board's; everything from `trap_entry` down is here.

## The `extern` block is split BY CALLER

An `extern "C"` declaration carries no visibility and cannot be re-exported
(tracker SL-6), so there is no way to declare `sink.c`'s surface once and share
it. It is split instead: `rv32core` declares the seven leaves it calls, and each
board declares the ones IT calls (`sos_mie_write`, and `sos_wait_for_irq` on
`virt` — the C3 deliberately does not call it and therefore does not name it).
The two halves are disjoint, which is what keeps SL-6's one-owner rule satisfied
with no shared declaration to own. `sink.c` itself defines all nine; the linker's
`--gc-sections` drops whichever a board does not reach.

## The origin-note convention is RETIRED for these files

Design 20 built `hal/riscv32-esp32c3` as a sibling COPY under a rule that each
copied file names its origin and the pairs are kept in step until a named later
pass. This is that pass. The files above have no origin — they ARE the original,
once — and nothing is left to keep in step. What the C3 directory still holds is
board code, marked `BOARD (C3)` where it sits, which was always the point of
those markers.

Adding a third riscv32 board means: a new `hal/riscv32-<board>/` with its own
`boot.S`, linker scripts, and a `lib.saw` whose two import lines are copied
verbatim from either sibling; a `hal_native` / `hal_modules` / `hal_asm` triple
in the runner's arch table; and nothing here.

## arm64 is untouched

There is one arm64 board in this tree, so its HAL is whole — its own `sink.c`,
its own `boot.S`, no shared module beside its `hal`. Design 23's ruling named the
riscv HALs; a second arm64 board is when the same question gets asked again.
