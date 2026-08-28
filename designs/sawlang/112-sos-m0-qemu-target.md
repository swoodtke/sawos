# Design 112 — SOS M0: QEMU riscv32 target in the build (queued Aug 4)

First SOS milestone (App-2, sos/spec.md §5b Profile A): Saw code
running bare-metal under QEMU, with a mechanical test loop the kernel
briefs can build on. Covers tracker items F7 (boot shim remainder),
F8 (linker scripts), F9 (QEMU smoke + CI). This is the tracker's
"UART blink" milestone.

## Facts established (lead scouting, Aug 4)

- sawc already takes `--target <triple>` + `--freestanding`;
  freestanding emits an UNLINKED object and rejects hosted-only std
  imports — linking is the caller's job by design.
- llvmlite in the venv emits riscv32 and aarch64 (probed).
- Host has `qemu-system-riscv32` 11.0.3 and `ld.lld` 22.1.8 (brew).
- The freestanding profile already externs the runtime seams
  (codegen core.py:105) — no dependency on design 113; the kernel
  supplies its own seam implementations or avoids them.

## Decisions (user, Aug 4)

- **QEMU is a HOST PREREQUISITE, not Blade-managed.** Blade resolves
  Saw source packages; emulators/toolchains are host tools like the
  venv. The harness probes for the qemu binary and fails with an
  install hint (`brew install qemu` / `apt install qemu-system-misc`).
  Blade enters the SOS build story later, at the sosimg build-target
  stage (spec §5 boot protocol) — NOT in this brief.
- **riscv32 only.** arm64 EL1 parity + HAL extraction is the next
  milestone (M1b, spec §5b), its own brief.

## Target (pinned, veto-able)

- Machine: `qemu-system-riscv32 -M virt -nographic -bios none
  -kernel sos.elf` — M-mode direct entry, no OpenSBI.
- Triple `riscv32-unknown-none-elf`; ISA `rv32imac_zicsr`, ABI ilp32
  (verify llvmlite accepts the feature string; adjust pin if not).
- Memory map: RAM at 0x8000_0000 (link there); NS16550A UART at
  0x1000_0000 (byte registers: THR +0, LSR +5, TX-empty bit 5 —
  poll-and-write, no interrupts in M0); `sifive_test` exit device at
  0x0010_0000 (write 0x5555 = exit 0; 0x3333 | code<<16 = exit code).
- Stack: 64 KiB in .bss, set in the boot shim (pin, veto-able).

## Scope

1. `sos/kernel/` skeleton: `boot.S` (set sp, zero .bss, call the
   exported Saw `kmain`, hang on return), a minimal trap stub that
   writes a fail code to sifive_test (mcause in the code bits —
   faults must FAIL the test run, never hang it), `virt.ld` linker
   script (entry at 0x8000_0000, .text/.rodata/.data/.bss, keep
   `@section` placements working).
2. `main.saw`: `@export` kmain; UART driver over
   `UnsafeMemory<_, Device>` (the design-46 machinery, first real
   bare-metal use); print a banner; exit 0 via sifive_test.
   Freestanding panic path: panic seam → UART message → sifive_test
   fail exit (verify what the freestanding panic seam expects and
   wire it).
3. Build wiring: compile .saw with
   `--freestanding --target riscv32-unknown-none-elf`; assemble
   boot.S (`clang --target=riscv32... -c`); link with
   `ld.lld -T virt.ld`. Encapsulated in the harness, not hand-run.
4. Harness: `tools/sos_runner.py` + `make sos-test` — build, run
   qemu with a hard timeout (10 s), capture UART stdout, assert
   expected banner AND qemu exit status (sifive_test makes the exit
   code real). Probe for qemu/ld.lld up front with install hints.
   Separate from test_runner.py (different execution model); same
   pass/fail reporting style. Test programs live under `sos/tests/`
   (M0: the boot smoke test; kernel briefs add more).
5. CI: new job (ubuntu: `qemu-system-misc` + lld) running
   `make sos-test`. macOS CI job optional — skip if brew qemu is
   slow/flaky in CI; ubuntu coverage suffices.
6. Docs: CLAUDE.md repo map line for sos/ gains the kernel skeleton
   + `make sos-test`; sos/spec.md roadmap notes M0 done; tracker
   F7/F8/F9 closed (F7's compiler surface was already done —
   design 58). LANGUAGE_SPEC/skill untouched (no language change).

## Non-goals (M0)

Interrupts, timers, scheduler, kernel objects/handles/syscalls,
sosimg, arm64, P4 hardware, SMP. The deliverable is: `make sos-test`
green means "sawc-built code boots, prints, and exits cleanly under
QEMU" — the substrate every kernel brief tests against.

## Discovery duty

First bare-metal use of the freestanding profile WILL surface rough
edges (missing seam, datalayout wrinkle, `@section`/linker friction,
i32-word gaps). Standing rules apply: fix user-facing bugs on
discovery if unambiguous, otherwise tracker-flag with a repro — do
not scope-creep the brief. Specifically watch for the tracker's
two-suspend embedding claim if any suspending shapes get written.

Bars: full suite zero xfails + bootstrap green per commit (compiler
untouched, but the bar stands); `make sos-test` green; per-unit
commits; linear history; no attribution trailers; foreground suites;
interruption-safe. SEQUENCING: may run CONCURRENT with design 113 in
a separate worktree (disjoint trees — sos/ + tools/ + Makefile/CI vs
sawc/); design 114 must NOT run concurrently with this (both are
fine, they don't overlap — the constraint is 113 vs 114).
