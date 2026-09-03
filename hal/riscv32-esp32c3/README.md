# hal/riscv32-esp32c3 — the ESP32-C3 board HAL (design 20)

SOS boots here. `make sos-smoke-esp32c3` is NON-GATING by ruling — never
part of `make sos-test`, never CI, and it needs the machine-local
Espressif toolchain under `~/.espressif` (QEMU with the `esp32c3`
machine, and esp-clang for the no-A multilib row).

**This directory is the BOARD half only, since design 23.** Everything
architectural — the trap entry, the trap frame, the cause decoding, the
syscall accessors, PMP, the linker seams, the `ecall` stub — lives once in
`../riscv32-common/` and is shared with `hal/riscv32`. Read that directory's
README for the split rule and for what the two import lines at the top of
`kernel/lib.saw` mean.

```
kernel/boot.S       XIP .data copy, and a console-printing kernel_fault —
                    this part has no sifive_test finisher to fail through
kernel/lib.saw      Espressif UART, SYSTIMER, the interrupt matrix, the
                    C3 memory map, PMP budget over it; re-exports the
                    shared `rv32core` as `hal`
kernel/esp32c3.ld   the XIP layout; its `.magic` section IS the
                    direct-boot protocol
user/root.ld        root at 0x403A_6000    user/child.ld  child at 0x403C_5000
ABI.md              EVERY address, with the probe that produced it

  ../riscv32-common/kernel/trap.S    trap entry + M->U transition, SHARED
  ../riscv32-common/kernel/sink.c    the CSR and linker-symbol leaves, SHARED
  ../riscv32-common/kernel/lib.saw   the `rv32core` module, SHARED
  ../riscv32-common/user/syscall.c   the ecall stub, SHARED
```

**Read `ABI.md` before changing anything here.** It is not a summary of
the datasheet; it is the record of what this emulator actually does,
probe by probe, and three of its sections describe places where the
emulator and the silicon DISAGREE (the UART's FIFO count, the interrupt
matrix's `mip` behaviour, the CPU's advertised ISA). Two of those cost
this unit a park.

The four things most likely to surprise someone arriving here:

- **The kernel's text is not in RAM.** The image is ~445 KiB and the
  part has 400 KiB of SRAM, so `.text`/`.rodata` execute in place in the
  flash window at 0x4200_0000 and only `.data` is copied. User-ruled;
  design 19's addendum already named XIP the family's execution model.
- **The exit status carries nothing.** There is no finisher and no
  shutdown door, so the machine never exits: the kernel prints
  `SOS-C3: halt pass` / `halt fail code=0x...` and halts, and the
  harness reads the verdict off the transcript.
- **`mie` bit 11 is the interrupt gate, not the bit `mcause` reports.**
  The matrix drives MACHINE EXTERNAL; `mcause` carries the matrix's own
  line number. `TIMER_CPU_INT` is 7 so a tick's cause word matches the
  standard machine-timer encoding and the arch-generic decoding above
  this HAL keeps working.
- **`wait_for_irq()` is deliberately empty.** This emulator's matrix
  never raises `mip`, so `wfi` cannot wake on it; the idle loop spins
  and `irq_poll` reads the SYSTIMER latch. On real silicon `wfi` does
  wake, and the shared `sink.c`'s `sos_wait_for_irq` is left in place for
  that day. This board's `lib.saw` therefore declares no `extern` for it —
  design 23 splits the `extern` block by CALLER, and a board that never
  calls a leaf never names it.

THE SIBLING-COPY RULE IS RETIRED (design 23, Sep 2) — its named later
pass has run. How this directory was built: design 20 made it its own
directory with its OWN copies of whatever it needed from `hal/riscv32`,
with no restructure of that directory, because unit 5 owned it
exclusively; each copied file named its origin at the top, everything
ARCHITECTURAL in them was the sibling's and untouched, and the dedup was
a NAMED LATER PASS. **That pass is design 23, and the copies are gone**:
the trap entry, the 32-word frame layout, the resume path, the PMP
staging, the cause decoding, the syscall accessors and the `ecall` stub
are one copy each in `../riscv32-common/`, the origin notes are struck,
and there is nothing left to keep in step. What survives unchanged is the
OTHER half of the rule: every board difference is marked `BOARD (C3)`
where it sits, and that is now the whole of what this directory contains.

Test packages live in `tests/c3-{timer,isolation,child-poke}/` rather
than reusing the virt ones, because a package names its linker script BY
TARGET TRIPLE and the C3 shares `riscv32-unknown-none-elf` with virt —
so a package can name one script or the other and not both. Each also
restates `march`/`mabi`/`target-features` in its manifest: blade's
default for a riscv32 triple is the virt/P4 baseline `+m,+a,+c`, and
this part has NO A extension.
