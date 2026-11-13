# hal/xtensa-esp32s3 — the Xtensa HAL and the ESP32-S3 board

SOS boots here, on the `Flat` protection tier. `make sos-smoke-esp32s3` runs
the board smoke against Espressif's QEMU (`-machine esp32s3`); it needs a
sawlang whose `sawc` targets `xtensa-esp-unknown-elf`, and the Espressif
toolchain under `~/.espressif` (that QEMU, and esp-clang with the `ld.lld`
beside it, which can link Xtensa). It is not part of `make sos-test`.

There is one Xtensa board, so the architecture and the board share one
directory, as `hal/arm64/` does.

```
kernel/boot.S       the direct-boot entry, the reset path out of the ROM's
                    register window, the vector table, the trap entry and
                    the restore path (`sos_resume_frame`)
kernel/sink.c       the special-register leaves (INTENABLE, INTERRUPT,
                    INTCLEAR), `simcall` SYS_exit, the linker-symbol
                    accessors, and `kernel_fault`
kernel/lib.saw      the `hal` module: the trap frame and its accessors, the
                    cause decoding, the Flat protection seam, UART0,
                    SYSTIMER, the interrupt matrix, the memory map
kernel/esp32s3.ld   the flash layout across the two bus windows, IRAM for the
                    vectors, DRAM for the kernel's data
user/syscall.c      the `syscall` stub every root and child links
user/root.ld        root at 0x403A_0000      user/child.ld  child at 0x403B_8000
ABI.md              every board fact, with the probe that produced it; §13 is
                    the process layout
```

**Read `ABI.md` before changing anything here.** It is the record of what
this emulator does, probe by probe; its §11 lists where silicon may differ,
and none of this directory has run on silicon.

The things most likely to surprise someone arriving here:

- **Everything is call0.** The ROM enters the image windowed (`callx8`);
  `_start` resets the register window and turns window exceptions off, and
  nothing after it is windowed. The compiler's objects are call0 with no FPU
  (the triple's default features), and so is every C and assembly file here
  (`-Xclang -target-feature -Xclang -windowed`, the same for `-fp`). No call0
  libgcc, compiler-rt or ROM helper exists, so no image may link one.
- **There is no privilege.** PS.UM selects which vector a trap takes, not what
  code may do: user code can write every special register (ABI.md §5e). So the
  tier is `Flat`: `prot_isolated()` answers false and every `prot_*` call
  records nothing in hardware, while the kernel's checks on the syscall path
  hold as on every tier. The vector is the mode witness: user syscalls,
  interrupts and faults reach the user vector, and the kernel's own traps, with
  PS.UM = 0, reach the kernel or double vector and stop the machine through
  `kernel_fault`. Two things a process can do the tier cannot deny: a `break`
  in user mode reaches the debug vector and stops the machine the same way
  (`vector=0x00000280`; compiled code traps with `ill`, so only hand-written
  assembly gets there), and `rsil` raises the process's own INTLEVEL, which
  turns the tick off for as long as it runs.
- **Flash is two windows.** Code executes in place at 0x4200_0000 (IBUS);
  read-only data is read at 0x3C00_0000 (DBUS). `esp32s3.ld` puts everything
  fetched in the first 2 MiB of flash, with the load images of `.data` and the
  vectors, and the read-only data read in place in the second; `boot.S` copies
  `.data` and the vectors out of flash through the data bus.
  Literal pools sit below their code (`l32r` reaches only backwards).
- **SRAM is one array twice on silicon.** IRAM and DRAM are separate on the
  emulator; on silicon IRAM 0x4037_8000 + n is DRAM 0x3FC8_8000 + n. The
  vectors, the kernel's data and the process regions are laid out so that no
  two are twins (`esp32s3.ld` has the map).
- **Interrupts are level-1 and level-triggered.** SYSTIMER TARGET0 (matrix
  source 57) is CPU interrupt 1. A device line is given one of nine CPU
  interrupts when it is unmasked and keeps it while masked; an unmask that
  finds none unassigned takes one back from a masked line, so at most nine
  lines are unmasked at once, never a count of lines ever bound. The mask is
  INTENABLE, not the matrix: this emulator moves a CPU interrupt's level only
  when a source changes level, not when a route does (ABI.md §6c).
  The kernel runs at INTLEVEL 15 from `_start` on (ABI.md §7f has the probe
  and the stress run), so interrupts are taken from user mode only, and
  `wait_for_irq()` is empty because `waiti` would take the interrupt in
  kernel mode (ABI.md §6d): the idle loop spins on `irq_poll`, which reads
  SYSTIMER and the INTERRUPT register.
- **The scheduler tick preempts processes here.** This emulator delivers the
  timer's level-1 interrupt to user mode, so the shipping kernel's tick takes
  the processor from a process that never traps; the smoke's `tick_spinner`
  shows it. Every other case's image carries the "no tick" boot flag, as on
  the gate.
- **The machine stops through semihosting.** `exit_pass`/`exit_fail` print the
  verdict line (`SOS-S3: halt pass` / `halt fail code=0x...`) and then
  `simcall` SYS_exit, so the harness asserts the exit status as well as the
  transcript. The emulator runs with `-semihosting`; without it the kernel
  halts after the line.

- **Processes run from IRAM at fixed link addresses.** Nothing translates, so
  root links at `ROOT_LOAD_BASE` and a resident child at the destination the
  runner's region table names, one linker script per resident image as on
  riscv32; the region map is in `kernel/lib.saw` above `RAM_BASE`.

The smoke's cases are in `_esp32s3_smoke` in `tools/sos_runner.py`; `--case`
narrows it. The kernel-only ones are the gate's harness kernels of the same
names on hand-assembled payloads from `tests/xtensa/`, and `irq_takeback`,
which drives this HAL's CPU interrupt table directly. The process ones are
the gate's own cases, named there, and two of the board's own:
`irq_line_cycle` (more lines bound over a life than there are CPU
interrupts) and `frame_hold` (every register of the trap frame held across
ticks, in `tests/frame-hold/`). Their packages carry a
`[sos.xtensa-esp-unknown-elf]` section naming `user/root.ld` or
`user/child.ld` and, as native sources, `user/syscall.c` and
`rt/common_c/support.c`, so the same programs run on this board. Blade builds
them with Espressif's clang and `ld.lld` (`SOS_CLANG`, `SOS_LD`) and tags them
with the image format's Xtensa tag, which `arch_tag()` answers.

`support.c` is still linked for the arena accessors. Its word-atomic routines
are not reached here: the compiler lowers word atomics to inline `s32c1i`, so
nothing references them and `--gc-sections` drops them.
