# `hal/xtensa-esp32s3-r8` — the ESP32-S3 with 8 MiB of octal PSRAM

The `esp32s3-r8` board: the ESP32-S3 part whose package holds 8 MiB of AP
Memory's octal PSRAM beside its flash, as on an N8R8 module. It is the
`esp32s3` board's machine and HAL with the PSRAM described, so it is a
facade, not a fork, as `hal/riscv32-flat/` is over `hal/riscv32/`.

```
kernel/lib.saw   the `hal` module for this board: the S3 HAL re-exported
                 as `s3hal`, and the five names the PSRAM changes
board.toml       the board description, the S3's with the PSRAM's range
README.md        this file
```

Everything else is the S3 HAL's, `hal/xtensa-esp32s3/`: `boot.S`, `sink.c`,
the PSRAM's command layer `mspi.c`, the linker scripts, the clock and stall
keys, and `ABI.md`, whose §16 and §17 are the PSRAM's. The runner's
`ESP32S3_R8_ARCH` and the board's `modules` map `hal` here and `s3hal` to the
S3's `kernel/`.

## What changes

- **`memory_init`** brings the PSRAM up at every boot, after `clock_init`
  and the banner and before anything is loaded: its eFuse codes, the clock,
  the chip's identity and mode register, SPI0's PSRAM side and the cache's
  map, from IRAM in one call (S0 to S12); the read-back test over all
  8 MiB (S14); and only then the PSRAM published (S15). A board described
  as having PSRAM never boots on without it: any check that disagrees with
  the description stops the machine with a line naming what it read and
  what the description says (ABI.md §16c, §17).
- **`psram_bytes`** answers 0 until S15 and 8 MiB after.
- **`map_target_ok`, `map_source_ok`, `segment_bus_ok`** take the PSRAM,
  `PSRAM_BASE` (0x3D00_0000) for `psram_bytes()`, as process RAM beside the
  SRAM range once it is published, and refuse a range that reaches into
  both. Code is still used only in place in flash.

## The description

HAL data, since the kernel needs it at boot: the size, 8 MiB, which sets
both the eFuse's size code and the chip's density; the clock the bus is
brought up at, which is the image's clock key's (each key's module states
the words it leaves on the part and its memory-SPI core clock); and the
divider that runs the bus at 20 MHz from that clock, 2 at `xtal40` and 4
on the PLL, and never less than 2: `rom`, whose core clock is 20 MHz, runs
it at 10 MHz, since on the part a bus at the core clock itself read the
mode registers shifted (ABI.md §17b). A board image carries the part's
clock words and an image for the emulator the emulator's: the two machines
answer the clock check differently, and the part must match its
description exactly.

Any clock change is `clock_init`'s, and nothing after `memory_init` writes
SYSTEM_SYSCLK_CONF, SYSTEM_CPU_PER_CONF or SPI_MEM_CORE_CLK_SEL, since the
PSRAM's bus moves with them. The runner's `check_clock_registers` holds the
S3 HALs' sources to it.

## RAM

Root's region, the default process range and the default pool stay in
SRAM, since root's region is fixed there. The PSRAM's range states word
atomics, which the runtime's own statics in every process's region and a
heap's pool need, on board stage P8's result: `s32c1i` increments raced by
preempted threads on PSRAM words, with the data cache evicting and filling
their lines, kept every count (ABI.md §17g). So an image may place a
process, or a heap's pool, in the PSRAM, unless it declares
`ram = "fast"` (ABI.md §17e): the smoke's `stock_heap_psram` runs a data
heap there beside a default heap in the SRAM. A buffer there is slower to
the kernel's copy doors, which read it with interrupts masked (ABI.md §11,
stage P9). Once published the kernel takes the PSRAM as
process RAM, so a launcher may also give a process a destination or a pool
there, as the smoke's hand-assembled child and pool do; a process's code and
read-only data are in flash, in place, as on the S3.

The board's `EXTMEM` device is the caches' control and counters; the
stress gives it to its root to read the data cache's PSRAM misses.
