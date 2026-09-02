# hal/riscv32-esp32c3 — placeholder (design 20 PARKED here)

STATUS (Sep 2 2026): still a placeholder. Design 20 was flown and
PARKED before writing `kernel/` or `user/` — two blockers, both
needing a user ruling, argued in `designs/020-esp32c3-smoke.md`:

- **A. The kernel does not fit.** Its loadable image is 392 KiB
  against the C3's 400 KiB of SRAM, before a byte of the 149 KiB
  `.bss`. The ruled COPY-TO-SRAM boot mode is arithmetically
  impossible for this kernel; only XIP text placement fits, and the
  brief puts XIP explicitly out of scope.
- **B. Espressif QEMU 9.2.2's `esp32c3` machine delivers no
  peripheral interrupts.** SYSTIMER and TIMG0 alarms fire in their own
  status registers, but the interrupt matrix is a register file with
  nothing wired to the core (`mip` stays 0 with every source mapped and
  `mie` wide open), so the ruled `timer` smoke case cannot be shown.

WHAT DID LAND: `ABI.md` beside this file — the memory map, the
direct-boot protocol, the no-A build spelling, and the probed
semantics of the UART, SYSTIMER, interrupt matrix and PMP, all derived
from the running emulator. A re-dispatch after the ruling starts from
verified board facts rather than from a datasheet.

THE SIBLING-COPY RULE (user, Sep 2) — UNCHANGED, and it still governs
whatever fills this directory: this board HAL is built as its own
directory with its OWN copies of whatever it needs from `hal/riscv32`
— NO restructure of `hal/riscv32` into shared-core + board, because
unit 5 owns that directory exclusively (its MAX_PROCESSES/PMP-budget
work edits `hal/riscv32/kernel/lib.saw`). The shared-core dedup is a
NAMED LATER PASS once both have landed. Each copied file notes its
origin at the top so the dedup pass can find the pairs.

Scope when filled (design 20, smoke-only by ruling): direct-boot
minimal image on Espressif QEMU's `esp32c3` machine — Espressif UART
(not 16550), interrupt matrix + SYSTIMER (not CLINT/PLIC), the C3
memory map, RV32IMC (no A extension: atomics via support.c libcalls;
single core), 16 PMP entries over the real map. NON-GATING.
