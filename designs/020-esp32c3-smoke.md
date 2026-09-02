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
