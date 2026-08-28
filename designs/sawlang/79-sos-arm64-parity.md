# Design 79 — SOS M1b: arm64 EL1 boot parity + HAL extraction (DECIDED Jul 31)

Profile B's first step (spec §5b): the SAME milestone as design 78 —
boot to a loaded root server with UART output through the
(status,value) syscall ABI — on **qemu-system-aarch64 -M virt**,
kernel at EL1, root server at EL0. Lands BEFORE any object-model work
so the HAL boundary is proven two-arch first.

## Scope
1. **Compiler: freestanding aarch64 target** — hosted aarch64 codegen
   exists (the dev host); verify/enable the freestanding path
   (triple aarch64-unknown-none, no libc, platform Int = i64 — the
   design-47 machinery's 64-bit case). Fix gaps on discovery; DF-ledger
   anything large.
2. **HAL extraction**: refactor design 78's kernel so ALL
   arch-specific code sits behind `--module-path hal=sos/hal/<target>`
   (spec §5b): boot shim (asm: EL1 entry, stack, bss, MMU off v1),
   exception vectors + trap frame (EL0 `svc` → the shared syscall
   dispatcher; same (status,value) ABI: x8 number, x0-x5 args,
   x0=status/x1=value — the AAPCS-adjacent mirror of the riscv32
   convention, pinned, veto-able), context enter-EL0 (`eret`), UART
   (PL011 on virt), timer stub. The shared kernel core (sosimg loader,
   syscall table, panic path) must compile UNCHANGED for both HALs —
   that is the acceptance criterion of the extraction.
3. **Protection v1 on arm64**: MMU ON with a minimal static identity
   map (4KB granule, kernel RWX-appropriately + device window +
   root-server region user-accessible) — enough to fence EL0 from
   kernel memory (the profile's floor; real per-process AddressSpaces
   are the M3 object-model work). If identity-map setup proves heavy,
   the honest v1 floor is EL0/EL1 privilege separation alone with the
   MMU identity-mapped permissively — report which landed.
4. **sosimg**: same fixed-width format (78); emitter grows the arch
   field; loader shared.
5. **QEMU smoke**: sos/test_boot.py grows an arm64 target (same
   transcript assertions); CI job runs BOTH profiles (qemu packages
   on ubuntu; degrade honestly if unavailable).
6. Docs: spec §5b status; tracker (design 79 landed; freestanding-
   aarch64 compiler notes); build.py both targets.

## Hazards
- The compiler work (item 1) may dominate — apply fix-on-discovery;
  keep hosted suite + bootstrap green per commit (the oracle, as
  always).
- Do NOT let arch details leak above the HAL line — the shared-core-
  compiles-unchanged criterion is the review gate.
- Trap-frame/context asm is per-arch by nature; keep it minimal and
  documented.

Bars: full suite + blade/libs + bootstrap green per commit; zero
xfails; interruption-safe per-unit commits + tracker notes; standing
policy applies. Load the saw-lang skill before writing Saw.
