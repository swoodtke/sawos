# Design 78 — SOS milestone 1: boot to root server on QEMU (DECIDED Jul 31)

App-2 begins. Goal: a Saw kernel that boots on QEMU riscv32 (`virt`
machine — the CI-able stage before ESP32-P4 hardware, F9), loads a
root server from a sosimg partition, enters it in U-mode, and proves
the syscall ABI with UART output driven from userspace. "Blink" =
periodic UART prints from the root server via a kernel syscall.

Decisions in force (sos/spec.md §5, user-ratified): strict-rendezvous
channels (not needed for M1), kernel-loads-root-server via the flat
sosimg format, (status,value) syscall ABI (a7 number, a0-a5 args,
a0=status/a1=value), objects/handles/rights model §2-§3. M1 pins
(veto-able): QEMU `virt` profile first (16550 UART at 0x1000_0000,
CLINT timer) — the P4 profile (PMP/APM specifics, its UART) is M2;
M1 isolation = PMP U-mode fencing of kernel memory only (coarse: one
kernel region + one root-server region + device window).

## Scope (kernel lives in sos/kernel/, userspace in sos/root/)
1. **Toolchain**: riscv32 freestanding compile is claimed working
   (design 47) — verify end-to-end: `--target riscv32` (or the
   existing flag spelling — probe sawc), `-c` object mode, no libc.
   Fix small gaps found (report). Linker script (F8) for the virt
   memory map; asm boot shim (F7): set sp, clear bss, jump to
   `@export("kmain")`. A build script `sos/build.py` (venv python +
   llvm tools + qemu invocation); Blade integration deferred.
2. **Kernel M1 core** (all Saw, freestanding): UART driver over
   `UnsafeMemory<_, Device>`; panic-to-UART; trap handler entry (asm
   stub saving regs → Saw handler): syscalls + fatal-fault report.
   Syscalls for M1: `debug_print(ptr, len)`, `yield()`, `exit()` —
   the ABI proof, not the object model (channels/handles are M2+).
   Static allocation only (slab over statics, design 42) — no Global.
3. **sosimg**: format constant in a shared Saw module; ALL header
   fields FIXED-WIDTH (UInt32 offsets/lens/flags, UInt64 where an
   address may be 64-bit — the format is shared with the arm64 MMU
   profile, design 79/spec §5b); kernel-side parser/loader (copy
   segments to their load addresses, PMP-fence, mret to entry in
   U-mode); emitter side in build.py (objcopy + header pack) v1.
   Structure code you write with the two-profile HAL split in mind
   (spec §5b): arch-specific bits (boot shim, trap entry, UART
   address) live under sos/hal/riscv32-virt/ so design 79 can add
   sos/hal/arm64-virt/ beside it without surgery.
4. **Root server M1**: a freestanding Saw program using the `sos`
   userspace module (syscall wrappers via inline ecall — probe how to
   emit ecall: an `@export`-adjacent intrinsic or asm shim per
   syscall; report the mechanism, an asm stub file is acceptable);
   loops: debug_print("blink N") + yield, N forever (bounded in the
   smoke test).
5. **QEMU smoke** (F9): `sos/test_boot.py` boots the image in
   qemu-system-riscv32 (if absent on the host: SKIP with a clear
   message and verify to the .elf/IR level — report which ran),
   asserts the UART transcript (kernel banner, root-server entry,
   3 blinks), times out bounded. Wire into CI as a separate job
   (allow-failure if qemu install is flaky; report).
6. Docs: sos/spec.md M1 status section; tracker (F7 wiring/F8/F9
   progress, design 78 landed); README one-line SOS mention.

## Hazards
- Cross-compilation gaps will be the real work — apply the standing
  fix-on-discovery policy to compiler bugs the kernel surfaces
  (riscv32 codegen, freestanding std subsetting, @export/linker
  interactions); DF-ledger anything deferred.
- Keep hosted suite/bootstrap green — kernel work must not perturb
  hosted codegen (the suite is the oracle; run per commit as always).
- No QEMU on the dev box is possible — degrade honestly per item 5.

Bars: full suite + blade/libs + bootstrap green per commit; zero
xfails. Interruption-safe per-unit commits + tracker notes. Standing
policy applies. Load the saw-lang skill before writing Saw.
