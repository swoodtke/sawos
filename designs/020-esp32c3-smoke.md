# SawOS design 20 — ESP32-C3 board HAL, smoke only

User-ruled scope (Sep 2, the concurrent-pair session): BRINGUP
(boot -> transcript, trap entry, timer tick) plus MEMORY CONFIG (PMP
over the real C3 map, one isolation smoke) — and NOTHING ELSE. This
is design 19's MCU-class flavor made real on the machine-local
Espressif QEMU; the C3 is the FAMILY PROXY (the P4, the first
hardware target, has no QEMU machine — it arrives later as a board
variant reusing these peripheral drivers). NON-GATING: never part of
`make sos-test`, never CI — the QEMU is machine-local
(memory: espressif-qemu-tools.md). Runs concurrent with design 22;
unit 5 (design 21) follows both.

## The pre-carved seams (the stub pass, `ec9e56e`)

- `tools/sos_runner.py` has `--board` with a marked EMPTY esp32c3
  section — everything board-specific lands THERE and only there;
  the virt tables above it belong to other units.
- `Makefile` has `sos-smoke-esp32c3` refusing with a pointer; it
  becomes real.
- `hal/riscv32-esp32c3/README.md` states THE SIBLING-COPY RULE: this
  HAL is its own directory with its OWN copies of whatever it needs
  from `hal/riscv32` — NO restructure of `hal/riscv32` (unit 5 owns
  that directory exclusively); shared-core dedup is a named later
  pass once both land. The README is replaced by a real one when
  filled.

## The board facts (constraints, all ruled or verified)

- QEMU: `~/.espressif/tools/qemu-riscv32/esp_develop_9.2.2_20250817/
  qemu/bin/qemu-system-riscv32 -machine esp32c3`. THE EMULATOR IS
  THE ORACLE for the memory map: derive addresses from
  `info mtree` (monitor) on the actual machine and record them in
  the HAL's ABI.md — cross-check against the C3 TRM where possible,
  but what the smoke target must match is the QEMU model.
- DIRECT BOOT: magic words at flash offset 0, the bundled ROM jumps
  in — NO esptool image format yet (that is a hardware-bringup
  concern, out of scope). The agent verifies the exact magic/entry
  layout empirically against the local QEMU.
- Espressif UART, NOT 16550 — a new console sink (write-byte +
  status poll is all the smoke needs).
- Interrupt matrix + SYSTIMER, NOT CLINT/PLIC: route one SYSTIMER
  alarm through the matrix to the CPU line; the trap entry stays
  standard RISC-V CSRs.
- RV32IMC — NO A EXTENSION: atomics via `rt/common_c/support.c`
  libcalls, irq-off; `SpinLock` is language-refused there;
  single-core makes it sound. The no-A build spelling (sawc target
  flag vs esp-clang override through the toolchain resolver) is the
  agent's FIRST PROBE — record the answer in ABI.md and the
  As-built.
- C3 linker script(s), sibling-copied and reshaped: 400 KB SRAM.
  For SMOKE the ruled default is COPY-TO-SRAM (a tiny flash stub
  copies the image and jumps): XIP placement is design 19's later
  C3/P4-port consideration, explicitly out of scope here.
- PMP: 16 entries (design 19 tier 2's ruled floor met exactly) over
  the REAL C3 map — SRAM, flash window, peripheral space.

## Review points (the API gate's subject — user reviews BEFORE dispatch)

There is no kernel/abi or sysapi surface in this unit; what is
reviewable is the harness and HAL shape:

1. **The smoke case list, smallest that proves the ruling's two
   halves**:
   - `boot`: kernel boots to its transcript banner on the Espressif
     UART — proves direct boot, linker layout, console sink.
   - `timer`: a SYSTIMER alarm arrives through the interrupt matrix
     and ticks N times — proves trap entry + interrupt routing.
   - `isolation`: one PMP proof — a user-mode touch of a
     kernel-owned address faults and is reported — proves the PMP
     program over the real map. Reuse `process-isolation`'s entry
     if its payload proves map-portable; otherwise a minimal
     C3-local case, the agent argues which in the As-built.
2. **The harness shape**: `--board esp32c3` fills the stub section
   only; report printed in case-definition order, same transcript
   discipline as the gate (diffable, deterministic rows); exit
   nonzero on any failure. `make sos-smoke-esp32c3` = exactly that
   invocation. It builds kernel + payloads for riscv32imc via the
   standard toolchain resolver.
3. **HAL layout, the sibling copy**: `hal/riscv32-esp32c3/kernel/`
   (boot.S, trap entry, board sinks — UART/SYSTIMER/matrix, PMP
   over the C3 map, `esp32c3.ld`) + `user/` (ecall stub + link
   script) + `ABI.md` recording the map, the boot protocol, and the
   no-A build spelling. Copies are LICENSED and expected; each
   copied file notes its origin at the top so the later dedup pass
   can find the pairs.

## Out of scope (all named, none negotiable without the user)

- XIP placement, esptool image format, real hardware, the P4
  variant, ESP32-S3/Xtensa.
- Any restructure of `hal/riscv32` (unit 5 owns it); the
  shared-core dedup pass.
- Gating: this target never joins `sos-test` or CI. The GATE for
  this unit's own commit is still `make sos-test` on virt both
  arches — the smoke must not move it.
- New kernel objects, ops, rights, or sysapi surface. If bringup
  seems to need one, STOP and park.

## The proof

`make sos-smoke-esp32c3` runs its cases and prints a diffable
transcript; the As-built records it verbatim as the board's oracle
transcript (the sawlang#238 unit-0 tradition), plus `make sos-test`
unmoved on virt/both-arches.

---

# As-built — PARKED (Sep 2 2026)

**The unit did not build the HAL. It stopped at two blockers, both of
which need a user ruling, and neither of which can be routed around
without deviating from a reviewed point.** Nothing under `kernel/`,
`user/`, `tools/sos_runner.py` or the `Makefile` was touched: the
`--board esp32c3` stub still refuses, `make sos-smoke-esp32c3` still
points at this brief, and the pre-carved seams are exactly as the stub
pass left them.

What DID land is the half of the unit the brief called "derive it from
the emulator and record it": `hal/riscv32-esp32c3/ABI.md`, the board's
verified facts — memory map, boot protocol, no-A build spelling, and
the probed semantics of the UART, SYSTIMER, interrupt matrix and PMP.
A re-dispatch after the ruling starts from measurements, not from a
datasheet.

## Blocker A — the kernel does not fit in SRAM, so COPY-TO-SRAM is impossible

The brief rules: *"For SMOKE the ruled default is COPY-TO-SRAM (a tiny
flash stub copies the image and jumps): XIP placement is design 19's
later C3/P4-port consideration, explicitly out of scope here."*

The arithmetic refuses it. Measured with `llvm-size` on the baseline
commit's riscv32 virt build — the same kernel module a C3 build
compiles, and a C3 build is slightly LARGER, because no-A turns atomics
into `rt/common_c/support.c` libcalls:

```
.text                 360,624 B
.rodata                27,004 B
.data                  14,224 B
                     ----------
LOADABLE              401,852 B  =  392.4 KiB
.bss                  152,176 B  =  148.6 KiB
                     ----------
kernel alone          554,028 B  =  541.0 KiB

+ .payload (root)      36,864 B   (process_isolation's root image)
+ .regions                 56 B
+ .childimg             2,336 B
                     ----------
one real case         593,284 B  =  579.4 KiB

ESP32-C3 SRAM         409,600 B  =  400.0 KiB   (esp32c3.iram, per info mtree)
```

**The kernel's loadable image alone is 98.1% of the part's entire
RAM**, before one byte of `.bss`. The `.bss` is 64 KiB of sosrt's
`ARENA`, 64 KiB of `boot.S`'s kernel stack, and ~21 KiB of object
tables. Only the stack is mine to shrink under the sibling-copy rule
(`ARENA` is `rt/`, the tables are `kernel/`, and both are read-only to
this unit); taking it to 8 KiB leaves 496,684 B = 485 KiB, still
**76 KiB over**, with nothing left for a root region, a child region or
the pool. There is no arrangement of copy-to-SRAM that fits.

Levers examined and rejected:

- **Size optimization.** `sawc` has no `-Os`/`-Oz`; its only
  optimization flag is `-O0`, which *disables* passes. Filed as SL-18.
- **Trimming `.bss`.** `MAX_PROCESSES` and the table sizes are unit 5's
  question in `kernel/`, off-limits here, and even zeroing all ~21 KiB
  of tables does not close a 76 KiB gap.
- **Leaving `.rodata`/`.payload` in the flash DBUS window.** Saves
  27 KiB + 37 KiB and is still hopeless, because `.text` alone
  (352 KiB) plus `.bss` (149 KiB) is 501 KiB. `.text` is the term that
  has to move.
- **More RAM.** There is none. `esp32c3.iram` (400 KiB) plus
  `esp32c3.rtcram` (8 KiB) is the whole writable map. The
  `esp-rgb-vram` region at `0x2000_0000` is QEMU's display extension,
  not C3 silicon, and counting it would be a fiction.

The only layout that fits is **text-in-place in the flash IBUS window
at `0x4200_0000`, data/bss copied to SRAM** — precisely design 19's
addendum ("XIP is the family's execution model … the sosimg loader
wants an XIP placement mode: text-in-place at the flash-mapped
address, data/bss copied to SRAM"), and precisely what this brief puts
out of scope. So the ruling this unit needs is: **does design 20 adopt
XIP text placement for the C3 smoke, or does the C3 smoke wait for the
code-size work?** Either is a user call; picking one silently is not.

Nothing about the copy-to-SRAM MECHANISM is wrong — the stub was
built, booted, and used to run every probe below from SRAM
(`ABI.md` §2a). It is the payload that is too big, not the method.

## Blocker B — this QEMU delivers no peripheral interrupts, so the `timer` case cannot be shown

Reviewed point 1 rules the `timer` case as *"a SYSTIMER alarm arrives
through the interrupt matrix and ticks N times — proves trap entry +
interrupt routing."*

The two halves of that sentence come apart on this emulator. The alarm
fires; it never arrives.

- SYSTIMER counts and alarms correctly: the UPDATE/VALUE_VALID
  handshake works, `UNIT0_VALUE_LO` advances, and arming TARGET0 sets
  `INT_RAW` bit 0 **and** `INT_ST` bit 0, which `INT_CLR` then clears.
- TIMG0 T0 does the same, as a cross-check.
- The interrupt matrix register file is present and correctly laid out.
  The mask ROM's own footprint proves the source numbering: it leaves
  `0x600C_2054` reading `5`, i.e. source index 21 (UART0) routed to CPU
  interrupt 5.
- **And nothing reaches the core.** With both timers asserting, every
  source index 0..63 mapped to CPU interrupt 7, `CPU_INT_ENABLE` bit 7
  set, `CPU_INT_PRI_7 = 1`, `CPU_INT_THRESH = 1`, `mie = 0xFFFFFFFE`
  and `mstatus.MIE = 1`: `CPU_INT_EIP_STATUS` reads `0x00000000`,
  **`mip` reads `0x00000000`**, and zero traps are taken. Scanned one
  source at a time (all 64) and all-at-once; same answer both ways.

`mip` reading zero is decisive — it is the CPU's own pending register,
so this is not a mask, priority or threshold error in the probe.
Espressif QEMU 9.2.2's `esp32c3` machine models the interrupt matrix as
a register file and wires no source to the core.

So the `timer` case as reviewed is not achievable here. The nearby
shapes, all of which are deviations from the reviewed point and so are
NOT taken:

- a POLLED clock case (SYSTIMER counter + `INT_RAW`, no interrupt) —
  proves the timer and the map, drops "trap entry + interrupt routing",
  which is the half the case exists for;
- an `ecall`/fault-driven trap-entry case — the trap ENTRY path does
  work (see the isolation probe), but that is the `isolation` case's
  proof, not a second one;
- upgrading the QEMU fork or patching in the wiring — out of scope and
  not this repo's to do.

The ruling this needs: **does the `timer` case become a polled-clock
case, does it drop until the emulator wires the matrix, or does the
board smoke target a different QEMU?**

## The isolation case: which entry, argued

Reviewed point 1 asks the agent to argue whether `process-isolation`'s
entry is map-portable or whether a minimal C3-local case is needed.

**It is NOT portable, and a C3-local case would be needed** — but the
reason is entirely blocker A, and the mechanism it would rest on is
verified working.

- Not portable because every address in the case is a virt address.
  `process-isolation` runs a root at `0x8020_0000` and a child at
  `0x8024_0000` (`hal/riscv32/user/root.ld`, `child.ld`, and
  `ARCHES`'s `root_entry` / `child_region_base`), and its payload
  pokes a kernel-owned address chosen from that map. None of
  `0x8000_0000`+ exists on the C3, whose only RAM is
  `0x4037_C000-0x403D_FFFF`. The case would need its own link scripts
  and its own poke address regardless — which is exactly the "minimal
  C3-local case" the brief anticipates.
- But the *shape* transfers exactly, and that is the good news:
  probed directly on the emulator, one TOR pair granting R/W/X over
  `[0x403C_0000, 0x403C_1000)`, then `mret` to U-mode —

  ```
  granted window, ecall from U-mode        -> mcause = 0x00000008
  U-mode load of 0x4038_0000 (not granted) -> mcause = 0x00000005
                                              mtval  = 0x40380000
                                              mepc   = the faulting instruction
  ```

  Default-deny, the TOR encoding, the fault cause, `mtval` and `mepc`
  all behave exactly as `hal/riscv32`'s PMP code already assumes, and
  the C3 implements all 16 `pmpaddr` entries and all four `pmpcfg`
  words — design 19 tier 2's ruled floor met exactly, with no headroom.
  So the PMP half of this unit needs no rethinking; it needs a kernel
  that fits.

## The board facts that landed

Full detail in `hal/riscv32-esp32c3/ABI.md`. The headlines:

**Memory map** (from the monitor's `info mtree` on the running
machine, cross-checked by probing):

```
0x3C00_0000-0x3C7F_FFFF  flash DBUS window (romd, 8M)
0x3FC8_0000-0x3FCD_FFFF  SRAM via DRAM window (alias of IRAM+0x4000)
0x3FF0_0000-0x3FF1_FFFF  mask ROM via DROM window
0x4000_0000-0x4005_FFFF  mask ROM, 384K
0x4037_C000-0x403D_FFFF  internal SRAM, 400K   <-- the only RAM
0x4200_0000-0x427F_FFFF  flash IBUS window (romd, 8M)
0x5000_0000-0x5000_1FFF  RTC RAM, 8K
0x6000_0000  UART0     0x6001_F000  TIMG0     0x6002_3000  SYSTIMER
0x6001_0000  UART1     0x6002_0000  TIMG1     0x600C_2000  INT MATRIX
0x600C_0000  SOC CLK   0x600C_4000  CACHE     0x6004_3000  USB-SERIAL-JTAG
```

The SRAM alias was verified rather than assumed: a store to IRAM
`0x4038_0000` reads back at DRAM `0x3FC8_0000`, and IRAM
`0x4037_C000-0x4037_FFFF` (16 KiB) has no DRAM alias at all.

**Boot protocol — DIRECT BOOT, read out of the mask ROM and then
confirmed by booting:** flash words at offset `0x00` and `0x04` must
BOTH be `0xAEDB041D`; the ROM maps flash offset 0 at `0x4200_0000`
(IBUS) and `0x3C00_0000` (DBUS), then `jalr`s to **`0x4200_0008`** —
it CALLS the image, with `ra` pointing at a ROM path that prints
`Direct boot returned`, so an image that returns is diagnosed rather
than hanging. No esptool format, no header, no checksum: the two magic
words are the whole protocol. Flash attaches as
`-drive file=<img>,if=mtd,format=raw`.

**The no-A build spelling** (the brief's FIRST PROBE):

```
sawc :  --target riscv32-unknown-none-elf --target-features +m,+c
        (the virt profile's +m,+a,+c minus +a — the triple names the
         architecture, --target-features names the extensions)

clang:  ~/.espressif/tools/esp-clang/esp-20.1.1_20250829/esp-clang/bin/clang
        --target=riscv32-esp-unknown-elf
        -march=rv32imc_zicsr_zifencei
        -mabi=ilp32
```

`_zifencei` is not decoration: `-march=rv32imc_zicsr` warns
`no multilib found matching flags [-Wmissing-multilib]` and prints the
bundled set, in which `rv32imc_zicsr_zifencei` is the exact C3 row.

**And the emulator will not police it.** This QEMU's esp32c3 CPU
reports `misa = 0x401411AD` — RV32 with **A C D F H I M S U**. It
advertises the A extension, hardware float, the hypervisor extension
and S-mode, none of which exist on C3 silicon (M and U only, RV32IMC).
An accidental `+a` build runs green here and fails on the part, so the
no-A discipline is a build invariant, never something the smoke target
can be relied on to catch. That is worth carrying into whatever this
unit becomes.

## The oracle transcripts

There is no smoke transcript to record, because there is no smoke
target: the harness stub was deliberately left refusing. What follows
is what the brief's "the emulator is the oracle" clause actually
produced — the probe runs the board facts were read from, verbatim,
each preceded by the QEMU stderr line and the mask ROM's own banner.
Every probe is a direct-boot flash image whose payload the copy-to-SRAM
stub moved to `0x4038_0000`.

### Probe 1 — direct boot, CPU identity, PMP count, SRAM extents, UART, SYSTIMER

```
ESP-ROM:esp32c3-api1-20210207
Build:Feb  7 2021
rst:0x1 (POWERON),boot:0x8 (SPI_FAST_FLASH_BOOT)

C3PROBE begin
misa      = 0x401411ad
  ext:ACDFHIMSU
mvendorid = 0x00000000
marchid   = 0x00000000
mimpid    = 0x00000000
mhartid   = 0x00000000
pmpaddr implemented mask = 0x0000ffff
pmp entries = 16
pmpcfg0..3 = 0x0f0f0f0f 0x0f0f0f0f 0x0f0f0f0f 0x0f0f0f0f  traps=0
iram[0x4037c000] = 0xc0ffee01
iram[0x403dfffc] = 0xc0ffee02
dram[0x3fc80000] (alias of iram 0x40380000) = 0xa5a5a5a5
........................................................................................................................................................................................................
uart status after 200-byte burst = 0x00000000
uart clkdiv = 0x0030015b
systimer DATE = 0x00000000
systimer CONF (reset) = 0x46000000
systimer unit0 lo #1 = 0x0003fc10
systimer unit0 lo #2 = 0x0003ff70
systimer advanced = 864
systimer INT_RAW = 0x00000001
systimer INT_ST  = 0x00000001
intmtx 0x100.. baseline: 0x00000000 0x00000000 0x00000000 0x00000000 0x00000000 0x00000000 0x00000000 0x00000000
found systimer source index = NONE
C3PROBE end
```

(The 200 dots are the UART burst; the status register read immediately
after reads zero, and all 200 bytes arrived — QEMU drains the FIFO
synchronously and never reports a TX count.)

### Probe 2 — the interrupt matrix, per-source scan with two timers asserting

```
ESP-ROM:esp32c3-api1-20210207
Build:Feb  7 2021
rst:0x1 (POWERON),boot:0x8 (SPI_FAST_FLASH_BOOT)

C3PROBE5 begin
systimer INT_RAW = 0x00000001
timg0    INT_RAW = 0x00000001
eip with nothing mapped = 0x00000000
per-source scan:
  src 0 (?) eip=0x00000000
  src 1 (?) eip=0x00000000
  src 2 (?) eip=0x00000000
  src 3 (?) eip=0x00000000
  src 4 (?) eip=0x00000000
  src 5 (?) eip=0x00000000
  src 6 (?) eip=0x00000000
  src 7 (?) eip=0x00000000
  src 8 (?) eip=0x00000000
  src 9 (?) eip=0x00000000
  src 10 (?) eip=0x00000000
  src 11 (?) eip=0x00000000
  src 12 (?) eip=0x00000000
  src 13 (?) eip=0x00000000
  src 14 (?) eip=0x00000000
  src 15 (?) eip=0x00000000
  src 16 (?) eip=0x00000000
  src 17 (?) eip=0x00000000
  src 18 (?) eip=0x00000000
  src 19 (?) eip=0x00000000
  src 20 (?) eip=0x00000000
  src 21 (?) eip=0x00000000
  src 22 (?) eip=0x00000000
  src 23 (?) eip=0x00000000
  src 24 (?) eip=0x00000000
  src 25 (?) eip=0x00000000
  src 26 (?) eip=0x00000000
  src 27 (?) eip=0x00000000
  src 28 (?) eip=0x00000000
  src 29 (?) eip=0x00000000
  src 30 (?) eip=0x00000000
  src 31 (?) eip=0x00000000
  src 32 (?) eip=0x00000000
  src 33 (?) eip=0x00000000
  src 34 (?) eip=0x00000000
  src 35 (?) eip=0x00000000
  src 36 (?) eip=0x00000000
  src 37 (?) eip=0x00000000
  src 38 (?) eip=0x00000000
  src 39 (?) eip=0x00000000
  src 40 (?) eip=0x00000000
  src 41 (?) eip=0x00000000
  src 42 (?) eip=0x00000000
  src 43 (?) eip=0x00000000
  src 44 (?) eip=0x00000000
  src 45 (?) eip=0x00000000
  src 46 (?) eip=0x00000000
  src 47 (?) eip=0x00000000
  src 48 (?) eip=0x00000000
  src 49 (?) eip=0x00000000
  src 50 (?) eip=0x00000000
  src 51 (?) eip=0x00000000
  src 52 (?) eip=0x00000000
  src 53 (?) eip=0x00000000
  src 54 (?) eip=0x00000000
  src 55 (?) eip=0x00000000
  src 56 (?) eip=0x00000000
  src 57 (?) eip=0x00000000
  src 58 (?) eip=0x00000000
  src 59 (?) eip=0x00000000
  src 60 (?) eip=0x00000000
  src 61 (?) eip=0x00000000
  src 62 (?) eip=0x00000000
  src 63 (?) eip=0x00000000
C3PROBE5 end
```

A companion run with `mie = 0xFFFFFFFE` and `mstatus.MIE = 1` while
both timers asserted reported `ticks = 0`, `exceptions = 0`,
`mip = 0x00000000`.

### Probe 3 — PMP and U-mode isolation

```
ESP-ROM:esp32c3-api1-20210207
Build:Feb  7 2021
rst:0x1 (POWERON),boot:0x8 (SPI_FAST_FLASH_BOOT)

C3PROBE6 begin
user blob at 0x403c0000 = 0x403805b7
pmpcfg0  = 0x00000f00
pmpaddr0 = 0x100f0000
pmpaddr1 = 0x100f0400
back in M-mode
mcause = 0x00000005
mtval  = 0x40380000
mepc   = 0x403c0004
VERDICT: load access fault (PMP denied)
VERDICT: mtval is the denied address
VERDICT: mepc is the faulting instruction
granted-region run mcause = 0x00000008
VERDICT: ecall from U-mode (region was reachable)
C3PROBE6 end
```

## Deviations

None. The unit stopped at the first reviewed point it could not
implement as written, and implemented nothing in its place.

## Findings

1. **The C3 is a tier-2 part at the floor with no room for this
   kernel.** Design 19 sizes tier 2 by PMP slots and the C3 meets that
   floor exactly (16 entries). What design 19 did not price is RAM:
   400 KiB against a 392 KiB loadable kernel image. The tier taxonomy
   is about what the hardware can DENY; this unit found that the
   binding constraint on the family's proxy is what the hardware can
   HOLD. Worth carrying into the M5 sketch beside the tier table.
2. **XIP is not a placement preference for this family, it is the
   entry price.** Design 19's addendum already says so; this unit
   measured it. Any ESP32 port needs the sosimg loader's XIP placement
   mode before it needs anything else.
3. **The emulator is a weaker oracle than the brief assumed, in two
   specific ways.** It over-reports the ISA (`misa` advertises A, F, D,
   H and S-mode, none on real C3 silicon), so it cannot police the no-A
   discipline; and it under-implements the SoC (no interrupt source is
   wired to the core), so it cannot demonstrate interrupt routing. Both
   are worth stating before a P4 or S3 port trusts a green smoke run.
4. **The UART status poll is unexercisable here.** QEMU drains the TX
   FIFO synchronously and `UART_STATUS` reads 0 even mid-burst, so a
   `TXFIFO_CNT` poll written against the wrong bit field would pass on
   the emulator and hang on hardware. The console sink's poll is
   hardware-only correctness with no test.
5. **`sawc` has no size-optimization level** — only `-O0`, which
   disables passes. On an MCU target that is the difference between a
   port and a park. Filed as SL-18.
