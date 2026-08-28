# Design 162 — SOS M1b: arm64 EL1 parity + the HAL extraction

**Status: APPROVED (user, Aug 7: "approve the M1b recommendations -
dispatch when M1 integrates") — all three decisions below are
ratified as recommended. Dispatches the moment M1 lands on main.
Process pin: like every early SOS branch, M1b PARKS for user review
before integrating — the compiler-side DF fixes it produces
cherry-pick to main immediately, the sos/ work waits.**

## Goal

The M1 feature set — boot → kernel init → load the root sosimg →
System object ops (`debug_print`, `shutdown`) over the typed-handle /
`SosStatus` / scoped-rights ABI → clean shutdown, all existing QEMU
tests — running identically on **qemu-system-aarch64 `-M virt` at
EL1/EL0**, with ONE arch-free kernel core and per-arch HAL packages.
The deliverable is as much the SEAM as the port: after M1b, adding an
arch means writing a HAL, not editing the kernel.

## Units

1. **HAL seam extraction (riscv32 stays green throughout).** Audit
   the kernel for arch residue and move it behind a defined HAL
   surface under `sos/hal/<arch>/kernel/`: boot entry + early init,
   trap/exception vector install, syscall REGISTER convention
   (extract args → the arch-free `(handle, op, args)` dispatch,
   return status/value), memory-protection primitive (riscv32: the
   existing PMP code), console/debug byte output, shutdown mechanism
   (riscv32: sifive_test), and the linker script + any remaining
   boot asm. `kcore` compiles against the HAL interface only —
   enforced by building it per-arch with no arch names in its source.
   The user-side stub (`ecall` today) extracts the same way under
   `sos/hal/<arch>/user/` for the kernel-owned `sos` module.
2. **The arm64 HAL.** `boot.S` (EL1 entry, stack, BSS zerofill —
   design 149's .bss makes the regions free), exception vectors
   (sync exception → SVC → syscall path), `virt.ld` for `-M virt`
   memory map, PL011 UART for console, PSCI or the QEMU exit device
   for shutdown, and the memory-protection parity piece (decision 2
   below). No GIC bring-up beyond masking — M1 never enables
   interrupts on either arch (the M2 boundary, same as riscv32's
   never-set MIE).
3. **Width-cleanliness.** riscv32 is 32-bit, arm64 is 64-bit, and
   `Int` is platform-width: audit `kcore` + `imgformat` + `sosabi`
   for word-size assumptions. Wire fields are already explicit
   fixed-width via the typed views (design 112's idiom) — the audit
   proves it and fixes what it finds. `sosimg` images stay
   arch-tagged; a wrong-arch image is a clean load error.
4. **Harness.** `tools/sos_runner.py` grows the second target:
   both QEMU binaries, the same test set per arch, serial-output
   assertions unchanged; `make sos-test` runs BOTH arches and the
   battery treats either failing as red. Blade target layout already
   supports per-target build dirs (design 143 / M1's two-target
   layout).
5. **Compiler gaps as product.** aarch64 freestanding
   (`--target aarch64-...-none` triple, section placement, any
   target-feature needs) — every gap the port hits is a DF-162x
   finding fixed-or-filed, same as every port so far. (The 148/149
   work — const generics, .bss, SpinLock's target-atomics check —
   all have arm64 answers to verify: LSE/`+a`-equivalent atomics
   exist at EL1, so `SpinLock` should just work; prove it.)

## Decisions (all three RATIFIED as recommended [user, Aug 7])

1. **arm64 syscall register convention** — [ratified] `svc #0`,
   `x0` = handle, `x8` = op (the Linux-style op register keeps
   x0-x5 clean for args), args `x1-x5`, returns `x0` = status
   (`SosStatus` tag), `x1` = value/handle. Mirrors the riscv shape
   one-to-one; recorded in spec §5.7 beside the riscv column.
2. **Memory protection at EL1** — [ratified] a minimal STATIC
   identity-mapped MMU setup (block mappings, kernel RWX under EL1,
   the root image's region EL0-accessible per its SegFlags-validated
   permissions) as the PMP parity. The alternative — MMU off — runs
   but abandons M1's protection story on arm64. No dynamic mapping,
   no ASIDs, no TLB games beyond the mandatory: Mapping objects stay
   M2.
3. **QEMU machine/CPU** — [ratified] `-M virt -cpu cortex-a53`
   (ubiquitous, EL1 well-exercised, LSE atomics present).

## Gates

Per-unit commits, full battery each (suite zero xfails, lexdiff,
astdiff, irdet --all, bootstrap, gmgate) + `sos_runner` BOTH arches.
Final state: one kcore, two HALs, all SOS tests green twice. Branch
PARKS for user review.

## Explicitly out

SMP/secondary cores, interrupts and the GIC (M2, with IntrSpinLock's
implementation per §9b), devices beyond UART/console, scheduling
beyond M1's, Mapping/AddressSpace objects, big-endian anything.
