# SawOS design 29 — M5 unit 1.5: the higher-half kernel + the linmap seam

**Status: BRIEF, Sep 3 2026 — dispatches under `designs/025` (RULED;
this unit is its ruling 10, user-proposed and user-ruled the same
day).** arm64 only in behavior; every other HAL grows two identity
inline bodies. **After unit 1** (it edits the same tables and boot
path) — the dispatching lead rebases this brief's premises onto
unit 1's As-built, including the seam-collapse rework
(`prot_switch`/`prot_update`/`prot_clear`). **Before unit 2**, which
spends the low half this unit frees.

## The one-sentence goal

The arm64 kernel relinks into the high half under TTBR1, with a
LINEAR MAP of all RAM (Normal, PXN) and the board's usable MMIO
(Device, PXN) at a fixed offset, so TTBR0 becomes purely the user's
register — and the kernel's phys↔virt conversion is one HAL seam
pair that is bit arithmetic on MMU machines and the bare address on
MPU machines, with the console transcript BYTE-IDENTICAL.

## The ruled discipline (the whole design, in three sentences)

**Addresses-as-data stay PHYSICAL everywhere.** `GrantRow`s, sosimg
records, boot-handle records, region tables, Memory/IoMemory
capabilities, fault report words — none change meaning; a physical
address remains the one name for a location that crosses any API.
**Only the moment of kernel DEREFERENCE converts**, through
`hal.phys_to_virt(pa) -> UInt` / `hal.virt_to_phys(va) -> UInt` —
on arm64 an OR / AND-mask with the linmap offset; on riscv32 (both
boards) identity, compiling to nothing. `kernel/core` stays
arch-free: no tier conditional, no second address vocabulary.

## What it buys (recorded so the As-built can check itself)

1. TTBR0 is 100% user: the low half stops being shared territory,
   which is what unit 2's placement spends.
2. The copy funnels' unit-2 shape exists ahead of need: resolve a
   user VA to a PA through the target's grant records, dereference
   through the linmap.
3. Kernel table content exists ONCE (TTBR1's) instead of per-set;
   the per-process sets shrink to the user windows.
4. The linmap is PXN end to end and the kernel executes only its own
   high-half text — a W^X improvement over the identity blocks.

## Design points

- **The offset**: pick the canonical high-half base the configured
  VA size gives (TCR.T1SZ's choice is this unit's); the constant is
  HAL-internal, named once. Pure OR/AND must hold — assert at build
  time that RAM and the MMIO ranges fit under it.
- **Two attribute flavors, one arithmetic**: RAM maps Normal
  cacheable; each MMIO range the board publishes maps Device at the
  same offset. The HAL owns its board map; nothing above the seam
  knows which flavor a PA gets.
- **TTBR1 setup**: TCR.EPD1 opens; TTBR1 tables are built once at
  boot and never switched; kernel text/rodata/data/bss relink to
  high VAs (linker script + boot.S: MMU-off entry at physical,
  enable, jump high — keep the relocation window minimal and
  documented). `VBAR_EL1`, kernel SP, and every kernel-held pointer
  come out of the relink naturally.
- **The per-process sets lose their kernel blocks**: user sets carry
  only the user windows; EL0 translations stay `nG`/ASID-tagged
  exactly as unit 1 left them. The unit-1 finding that "window pages
  must stay EL1-mapped because kernel text sits inside the window"
  DISSOLVES — kernel access now rides TTBR1's linmap, so a
  non-granted user page may become genuinely ABSENT. If making it
  absent flips a fault from permission-class to translation-class
  anywhere, remember the gate does NOT witness fault class (unit 1
  finding 3) — normalize in the decode anyway, because unit 3 will
  light that assertion.
- **The sweep**: every kernel-side `UnsafeMemory`/`UnsafePointer`
  constructed from a stored physical address routes through
  `phys_to_virt` at the construction site. Bounded and greppable;
  the As-built lists the sites swept. The USER-facing funnels
  (copy-in/copy-out) convert the PA they already resolve; user VAs
  are still identity this unit (placement is unit 2's).
- **riscv32**: both HALs gain the two identity inlines and NOTHING
  else. (The Sv32 climb is PUNTED to backlog — 025 ruling 11 — but
  the note stands for whoever picks it up: virt's RAM at
  0x8000_0000 already sits in the upper 2 GiB, so an Sv32 kernel
  never moves and the seam stays identity there.)

## Gate

`make sos-test`, suite lock protocol, transcript diffed against a
pre-change baseline from the same worktree: BYTE-IDENTICAL, both
architectures, timing rows excepted. No user-visible address is a
kernel address — every address in the gated transcript is a
user-space address, which is what makes the ambition realistic. The
riscv32 half of the diff proves the seam's identity bodies cost
nothing.

## Out of scope

VA placement and the link-base collapse (unit 2); Sv32 (punted to
backlog, 025 ruling 11); any allocator/donation work; spec.md prose
beyond doc comments
(unit 8 sweeps; §5.5's user-address identity law is UNTOUCHED by
this unit — kernel-internal addressing was never its subject, say
so in a doc comment if a reader could wonder).

## Recording duties

As-built here: the offset and T1SZ choice, the linker/boot.S shape,
the set-shrink arithmetic (.bss delta, expected NEGATIVE), the sweep
site list, the fault-class note's outcome, gate evidence (hash,
both arches). SL entries to [SAWLANG] if the language bites. Close
the tracker entry in place; the lead moves it at integration.
