# SawOS design 35 — M5 unit 4: the tier word + the flat profile

**Status: BRIEF, Sep 4 2026 — dispatches under `designs/025` (RULED):
D-4 as ruled, plus design 19's flat-tier testing target.** The tier
track's last rung (1 → 1.5 → 2 → 4 complete with this).

## The one-sentence goal

The platform advertises which protection tier it is — `Isolated` /
`Flat`, TWO words only (MMU vs MPU is not advertised, ruled) — via
a `SystemOp` getter; a FLAT BUILD PROFILE of one virt board joins
the gate as the tier-3 demonstration; and the runner learns
tier-sorted case lists so the isolation proofs run only where
isolation is claimed.

## The tier word (D-4 as ruled)

- **The op**: a `SystemOp` getter beside `ClockGet`'s shape (name
  the agent's — `TierGet` or similar), answering one word; a
  `SystemRight` bit for it in the root set (attenuable like any).
  Enum in `sosabi` (`Isolated` / `Flat`), decoded exactly.
- **The "boot info" half of the ruling**: the ruled surface said
  "getter + boot info". SCOUT what the tree's boot-info carrier
  actually is — if no natural record exists (the boot handle set
  carries handles, not platform facts), land the OP as the checkable
  surface and record the finding that the boot-info half has no
  carrier yet; do not invent a record for it.
- **Semantics per design 19, carried whole**: `Flat` means `map`'s
  contract ("make accessible with at least these rights") is
  TRIVIALLY TRUE and succeeds honestly; what the word disclaims is
  DENIAL — `unmap`'s revocation half, and peer isolation. The
  kernel boundary (handles, rights, quotas, lifetimes) is NOT
  disclaimed anywhere. A program that relies on isolation checks
  the word once and refuses to run.
- Both current HALs answer `Isolated`. The flat profile answers
  `Flat`.

## The flat profile (design 19's ruled first target)

A BUILD PROFILE of the riscv32 virt board — zero new toolchain:
PMP left open (or the permission window wide), the protection calls
no-ops, the tier word `Flat`. Concretely post-design-27: a HAL
variant whose `prot_*` bodies are empty, `PROT_REPLAY_AT_SWITCH`
false (nothing to replay), `prot_switch` a no-op. The agent picks
the packaging (a third HAL directory vs a build flag on the
existing board) — bias toward whatever keeps `hal/riscv32-common`
the one arch home (design 23's consolidation is not to be undone).

**It JOINS `make sos-test` as a third profile run** — that is what
"one story, one test" means, and it changes the gate's shape
permanently: the report grows a flat section whose rows are ALL NEW.

## Tier-sorted case lists

Runner case metadata grows a tier requirement: the isolation proofs
(`process_isolation`, `map_wx_refused`, `map_exec_gated`, and scout
for others whose assertion IS denial — e.g. fault-on-unmapped
cases) are Isolated-only; the object-model suite (pipes, waiters,
stats, quotas, donation) runs identically everywhere. Every
excluded case is excluded BY NAME with the reason in the case
definition — no silent skips (the no-silent-caps doctrine).
`map`-succeeds-honestly cases run on flat and PASS — that is the
tier's contract witnessed.

## Authorized transcript motion

- The flat profile's ENTIRE section: all new rows.
- The runner's report framing where a third profile forces it
  (totals line, any per-profile banners).
- NOTHING in the existing riscv32/arm64 sections moves — those two
  profiles' rows stay byte-identical against the current baseline
  (246/246, hash `c823c1b5…`), and that is the fence proving the
  tier word and case-sorting cost the isolated tiers nothing.

## Out of scope

Any MCU-class flat target (Espressif QEMU is design 19's later
preference, not this unit); the ESP32-C3 board (stays tier 2,
non-gating smoke); spec.md prose beyond doc comments (unit 8 owns
§5b/§2's tier table); per-case timing rows doctrine (unchanged).

## Recording duties

As-built here: the word's spelling and surface, the boot-info
scout's answer, the profile packaging chosen and why, the excluded
case list with reasons, the new gate shape (runs/cases/hash), SL
entries if the language bites (SL-24 is the current highest —
verify against todo.md before numbering). Close the tracker entry
in place.
