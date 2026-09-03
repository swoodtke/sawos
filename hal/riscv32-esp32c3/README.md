# hal/riscv32-esp32c3 — the ESP32-C3 board HAL (design 20)

SOS boots here. `make sos-smoke-esp32c3` is NON-GATING by ruling — never
part of `make sos-test`, never CI, and it needs the machine-local
Espressif toolchain under `~/.espressif` (QEMU with the `esp32c3`
machine, and esp-clang for the no-A multilib row).

```
kernel/boot.S       XIP .data copy, trap entry, M->U transition, and a
                    console-printing kernel_fault — this part has no
                    sifive_test finisher to fail through
kernel/lib.saw      Espressif UART, SYSTIMER, the interrupt matrix, the
                    C3 memory map, PMP over it
kernel/sink.c       the CSR and linker-symbol leaves (unchanged)
kernel/esp32c3.ld   the XIP layout; its `.magic` section IS the
                    direct-boot protocol
user/root.ld        root at 0x403A_6000    user/child.ld  child at 0x403C_5000
user/syscall.c      the ecall stub (unchanged)
ABI.md              EVERY address, with the probe that produced it
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
  wake, and `sink.c`'s `sos_wait_for_irq` is left in place for that day.

THE SIBLING-COPY RULE (user, Sep 2) — how this directory was built and
what still governs it: this board HAL is its own directory with its OWN
copies of whatever it needs from `hal/riscv32`, with NO restructure of
`hal/riscv32` into shared-core + board, because unit 5 owned that
directory exclusively. **Each copied file names its origin at the top**,
and everything ARCHITECTURAL in them is the sibling's and untouched —
the trap entry, the 32-word frame layout, the resume path, the PMP
staging, the cause decoding, the syscall accessors. Every board
difference is marked `BOARD (C3)` where it sits. The shared-core dedup
is a NAMED LATER PASS, now that both have landed; keep the pairs in step
until it happens.

Test packages live in `tests/c3-{timer,isolation,child-poke}/` rather
than reusing the virt ones, because a package names its linker script BY
TARGET TRIPLE and the C3 shares `riscv32-unknown-none-elf` with virt —
so a package can name one script or the other and not both. Each also
restates `march`/`mabi`/`target-features` in its manifest: blade's
default for a riscv32 triple is the virt/P4 baseline `+m,+a,+c`, and
this part has NO A extension.
