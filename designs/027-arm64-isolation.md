# SawOS design 27 — M5 unit 1: arm64 translation-as-isolation

**Status: BRIEF, Sep 3 2026 — dispatches under `designs/025` (RULED,
the M5 plan of record): D-1 step one + D-2's seam edit pair + D-3's
static pools, on arm64-virt only.** riscv32 is UNTOUCHED in behavior
this unit — its HAL absorbs the seam rename mechanically and keeps
replaying PMP exactly as today.

## The one-sentence goal

Every process gets its OWN persistent translation-table set; a
process switch becomes a TTBR0+ASID write instead of a ~1.6k-store
window rewrite; a process's tables map ONLY its own grants, so peer
isolation is by ABSENCE of a translation rather than by permission
bits in a shared window — with VAs identity throughout (§5.5
survives verbatim, D-1 step one) and the console transcript
BYTE-IDENTICAL on both architectures.

## What exists (verified at `56ad00e`)

- One table set built at boot, shared by all processes:
  identity 2 MiB EL1-only blocks over RAM; a 4 MiB grant window
  (`0x4000_0000..0x4040_0000`) of level-3 pages; a device level-3
  window for the console page (`hal/arm64/kernel/lib.saw`).
- `load_domain(p)` (kernel/core/process.saw) = `hal.prot_reset()` +
  replay `p`'s grant rows through `hal.prot_region`/`prot_device` +
  `hal.prot_commit()`; full-window reset + TLB flush per switch
  (cost recorded at design 2 D-5). `run_thread` skips equal domains;
  `domain_changed(p)` reloads when the live domain's record is
  edited (the LIVE-DOMAIN RULE); `remove_grant` compacts by index.
- No ASIDs anywhere.

## D-1 — the seam, as ruled (025 D-2/D-3)

The kernel-side grant RECORD stays authoritative (teardown,
compaction, and the quota walks read it; nothing about
`GrantRow`/`remove_grant`/`compact_mapping_rows` changes shape).
What changes is WHEN hardware learns about it:

- **The seam grows an edit pair** — spelled by this unit, proposed
  `hal.prot_install(p, idx, row)` / `hal.prot_remove(p, idx, row)`
  — called wherever the record changes (install_row, remove_grant,
  and teardown's clears), for ANY process, current or not.
- **`load_domain` becomes a switch**: on arm64, write TTBR0_EL1
  (table base + ASID) and nothing else — no descriptor stores, no
  full flush. On riscv32 the function keeps its exact current body
  (reset + replay + commit IS the MPU's switch, per the ruling:
  replay-at-switch stays MPU-only).
- **The live-domain rule becomes TLB maintenance** on arm64: an
  edit to the CURRENT domain's tables is followed by
  invalidate-by-VA/ASID (or by-ASID; the agent picks the narrower
  one that is provably sufficient and says why at the site). An
  edit to a NON-current domain needs only the table store — its
  ASID's stale entries are handled at (a) nothing, if entries are
  ASID-tagged and the edit added; (b) invalidate-by-ASID at the
  edit, if it removed. Removal invalidates eagerly: an unmap that
  waited would be a revocation that did not revoke (the same
  sentence `domain_changed` carries today).
- **How the two HALs split**: the riscv32 HAL implements the edit
  pair as a NO-OP (its replay at switch reads the record; the
  live-domain reload path stays, now expressed through the seam the
  same way). The exact seam spelling that keeps both HALs honest
  without a tier conditional in kernel/core is this unit's main
  design surface — bring the shape, record it in the As-built.

## D-2 — the table sets (D-3's static pools)

- **Per-process sets from a static HAL pool**: MAX_PROCESSES ×
  (L1 + L2 + grant L3s + device L3) — same species of constant as
  today's single set, same 4 MiB grant span, same device window.
  Order-of-magnitude ~20 KiB/process, ~60 KiB total at
  MAX_PROCESSES 3; the .bss growth is expected and recorded in the
  As-built (the measured-RAM section of 025 is the context: virt
  has room, MPU boards are untouched).
- **Kernel placement is HAL-internal (ruled)**: the EL1 identity
  blocks appear in EVERY set (the simple v1), or TTBR1 carries them
  once — the agent's call, invisible above the HAL. NOTE the
  cross-process copy funnels rely on it: pipe/give copies touch the
  OTHER process's RAM through EL1 mappings while the CURRENT set is
  loaded, so whatever placement is chosen must keep all-RAM EL1
  reachability in every configuration. Say so at the definition.
- **ASID = process slot index** (proposed; 16-bit field, 3 slots —
  no allocator needed). Slot REUSE must invalidate the ASID's TLB
  entries: hang it on `clear_domain`/teardown, where
  `LAST_PROT_PROCESS` is already invalidated today.
- **Boot**: root's set is built where the shared set is built today;
  a created process's set starts EMPTY-of-grants (EL1 halves
  present) and fills through the edit pair as the loader installs
  rows. The boot door's ordering (design 2: `load_domain(ROOT)`
  before entering user mode) is unchanged.

## The transcript hazard, named

Under the shared window, a stray user access faults as a PERMISSION
fault; under per-process tables the same access is a TRANSLATION
fault (the descriptor is absent, not deny-marked). The trap decode
must map both ESR classes onto the SAME §5.7 fault reason it
reports today, or fault-case rows move. The isolation proofs
(`process-isolation` et al.) and every fault case are the witnesses;
byte-identical is the requirement, and a row that moves is a
finding to bring back, not to authorize locally (unit 2 holds the
only move authorization, and it is for placement, not for this).

## Gate

`make sos-test`, suite lock protocol, transcript diffed against a
pre-change baseline run in the same worktree: BYTE-IDENTICAL, both
architectures, the three documented timing rows excepted. The
riscv32 half of the diff proves the seam rename cost nothing; the
arm64 half proves the mechanism swap is invisible, which is the
design-23 tradition this unit inherits.

## Out of scope

VA placement, link-base collapse, `map` answering an address (unit
2 — its transcript-move authorization does NOT cover this unit);
riscv Sv32 (unit 3); the tier word (unit 4); any allocator/donation
work (units 5–6); spec.md prose beyond doc comments at edited
declarations (unit 8's sweep owns the sections).

## Recording duties

As-built in this file: the seam spelling as landed, the table-pool
arithmetic and measured .bss delta, the ASID/TLB-maintenance
choices with their reasons, the fault-class mapping, gate evidence
(case counts, both arches, byte-identical or the finding). New SL
entries to `designs/todo.md` [SAWLANG] in the established format if
the language bites. Close the tracker entry in place; the lead
moves it at integration.
