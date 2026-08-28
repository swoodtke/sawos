# Design 140 — SOS M1: riscv32 boot-to-root-server

STATUS: APPROVED (user, Aug 5; root-server design ratified as spec §12).
SOS-REVIEW POLICY APPLIES: the branch PARKS for user review — never
integrated to main by the lead without explicit user sign-off. May run
CONCURRENTLY with the compiler queue (disjoint tree) but NOT with design
136's re-spell sweep (it touches sos/*.saw and blade/) — dispatch after
136 integrates. Authoritative context: sos/spec.md (§1-§12 ratified
sections; §5b roadmap: this is M1, then M1b arm64 parity + HAL extraction
BEFORE the object model).

Orchestrator pins in this brief are VETO-ABLE at review; each is marked.

## Units

- **A. Trap entry, M/U split, PMP, minimal ecall.** Real `mtvec` handler
  (context save/restore in boot.S/rt.c — the HAL layer M1b will
  extract); M-mode kernel / U-mode root split; PMP regions locking the
  kernel range and granting root its load range + stack (pin: region
  layout recorded in virt.ld comments). Syscall dispatch per the decided
  ABI (§5.7: number in a7, args a0-a5, returns a0=status/a1=value).
  v1 syscall table (pin): `0 debug_putc(char)`, `1 exit(status)`. A
  U-mode fault (illegal access / bad ecall number) prints the cause tag
  and exits FAIL via sifive_test — never hangs (M0's discipline kept).
  M0's existing boot/banner/panic tests stay green throughout.

- **B. sosimg + Blade emit target + the kernel loader.** The §6 format,
  concretely (pin): magic `SOSI`, u16 version=1, u32 entry offset, u8
  segment count, segment table of `{flash_off: u32, load_addr: u32,
  len: u32, flags: u8}` (R/W/X bits), u32 priority-map field (§7) — all
  fixed-width little-endian (design-47 discipline; 32/64-bit
  interoperable). Blade gains an `emit = "sosimg"` build target reading
  the `[sos]` manifest section (priorities per §7) — Blade work in Saw,
  load the saw-lang skill. Kernel loader: the root image is an APPENDED
  BLOB after the kernel image with linker-symbol bounds (pin — simplest
  QEMU-virt shape; a real flash partition table is P4-hardware
  material), parse header, copy/place segments, set PMP per flags,
  enter U-mode at entry. Malformed image = FAIL exit with a cause tag.

- **C. Root server as a real second package + end-to-end test.**
  `sos/root/` is a separate Saw package with its own Saw.toml, built
  freestanding by Blade, emitted as sosimg. v1 behavior: print its own
  banner VIA THE debug_putc SYSCALL (proving U-mode + trap path — no
  direct UART access from root), then `exit(0)`. tools/sos_runner.py
  grows the two-image pipeline (build kernel, build root, append, boot)
  and asserts: kernel banner, root banner, clean exit; plus a
  fault-path test (a root image that touches a kernel address must FAIL
  with the cause tag, not hang). Update sos/spec.md §11 roadmap (M1
  DONE entry mirroring the M0 one) and the M0 note if the build shape
  changed.

## Constraints
Freestanding profile; keep every M0 test green at every commit; full
compiler gate battery before the final commit (suite/lexdiff/irdet/
astdiff/bootstrap/sos — the kernel work must not perturb the compiler);
venv python via absolute path; scratch under .build/scratch/;
no-workarounds policy — language pain in kernel/Blade code is exactly
what DF-findings exist for (record with repros, stop the unit if
blocked). Respect the design-130/136 unsafe model in all new Saw code
(slot spelling once 136 lands — rebase over it before starting).

## Exit criteria
`make sos-test` green with the new two-image tests; per-unit commits;
tracker: M1 section added under the SOS heading with the pins actually
taken; the branch left PARKED with a final report for user review.
