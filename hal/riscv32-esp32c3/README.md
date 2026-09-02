# hal/riscv32-esp32c3 — placeholder (design 20 fills this)

THE SIBLING-COPY RULE (user, Sep 2): this board HAL is built as its
own directory with its OWN copies of whatever it needs from
`hal/riscv32` — NO restructure of `hal/riscv32` into shared-core +
board, because unit 5 owns that directory exclusively (its
MAX_PROCESSES/PMP-budget work edits `hal/riscv32/kernel/lib.saw`).
The shared-core dedup is a NAMED LATER PASS once both have landed.

Scope when filled (design 20, smoke-only by ruling): direct-boot
minimal image on Espressif QEMU's `esp32c3` machine — Espressif UART
(not 16550), interrupt matrix + SYSTIMER (not CLINT/PLIC), the C3
memory map, RV32IMC (no A extension: atomics via support.c libcalls;
single core), 16 PMP entries over the real map. NON-GATING.
