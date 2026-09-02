# SawOS design 19 — M5 SEED: the three-tier memory story

Status: SEED, Sep 2 2026 — user-ruled direction, recorded ahead of the
M5 scoping session it will anchor (the design-10 pattern). Not a unit
brief; nothing dispatches from this document.

## The ruling (user, Sep 2)

ONE SET OF IDIOMS — alloc → map — over THREE PROTECTION TIERS. The
same program runs on all three; what differs is what the hardware can
deny, and that difference is ADVERTISED, never silently faked.

### Tier 1 — MMU (true mapping)

Per-process address spaces: page tables, VA placement on `map`, many
mappings, `satp`/`TTBR0` + ASID switching riding the EXISTING
`load_domain` seam (`run_thread`'s lazy same-process skip is already
the right shape — design 2 D-5's marked point, occupied since M2).
vDSO true-mapping falls out. The arm64 virt profile — today an
identity map used as an MPU — is the natural first climb.

### Tier 2 — MPU (the ruled floor: 16 SLOTS MINIMUM)

Physical regions, identity addresses (a mapping's address is the
ANSWER, not a choice — programs treat it as data, which the tree's
programs already do), mapping count priced by the design-7 quota
machinery. The floor arithmetic: 16 slots − ~5 fixed (kernel guard,
ROM, flash, a peripheral window, deny-all) ≈ 11 live, so
`DEFAULT_QUOTA_MAPPINGS` prices at 6-8. TOR-shaped (arbitrary-bounds)
PMP regions cost 2 entries where NAPOT costs 1 — held HAL-INTERNAL
with a conservative quota, so a program's budget never varies by
allocation luck (open cell for the sketch to ratify).

Verified hardware (Sep 2, ESP-IDF sources): **ESP32-P4** — 32 PMP
entries on shipping silicon (rev ≥ v3.0; pmpcfg0-7 complete), 16 PMA
regions besides, 128-BYTE granularity — comfortably above the floor;
the natural first hardware target, and an ISA match (riscv32 M+U,
PMP at `load_domain` — a board port, not a protection-model port).
PMSAv8-M parts at 16 clear the floor; 8-entry parts fall to tier 3.

### Tier 3 — FLAT (sub-floor MPU, ESP32-S3-class, and none at all)

The important bits are mapped ONCE AT BOOT; everyone gets free
access. `map`'s contract is "make this region accessible with at
least these rights" — on flat hardware that is TRIVIALLY TRUE, so the
call SUCCEEDS HONESTLY as a no-op (not ignored: the contract holds).
What the tier lacks is DENIAL, and that is a PLATFORM ATTRIBUTE — a
tier word advertised in the boot info / on System (`Isolated` /
`Flat`) — never a per-call lie. A program that relies on isolation
checks the tier once and refuses to run. `unmap`'s revocation half is
the one call whose enforcement the tier word explicitly disclaims.

**The kernel boundary survives wherever the hardware offers ANY
line** (the S3's WORLD0/1 gives exactly one): handles stay checked,
authority stays unforgeable through the API, quotas and lifetimes
stay real. The degradation is precise and sayable: the capability
model's AUTHORITY enforcement survives intact; MEMORY isolation
between peers does not.

## What holds across all three

- alloc → map → unmap, one surface; `give(keep:)` and the quota
  machinery unchanged.
- `load_domain` is the one seam: region reprogram (MPU), table+ASID
  switch (MMU), no-op (flat).
- One story, one test — sorted by tier: the isolation proofs
  (`process-isolation`, `map-wx-refused`, `map-exec-gated`) are
  Isolated-tier cases; the object-model suite (pipes, waiters, stats,
  quotas) runs identically everywhere.

## Testing targets for the flat tier

The flat tier is a PROFILE, not an architecture: its first test
target is the EXISTING QEMU virt boards under a flat build (PMP left
open / permission window left wide, `load_domain` a no-op, tier word
Flat) — zero new toolchain, and the tier's semantics (map trivially
true, isolation proofs excluded, kernel boundary kept) are fully
exercisable there. MCU-class flavor later, in preference order:
Espressif's QEMU fork's `esp32c3` machine (riscv32, 400 KB SRAM —
NOTE: RV32IMC, no A extension, so a `-a` build; single-core makes it
survivable but SpinLock is refused there by the language) before
`esp32s3` (Xtensa: a whole new sawc backend — the big lift, not a
board port); upstream `sifive_e` exists but its 16 KiB DTIM is below
our image sizes. An ARM-M (`mps2-*`) port is a further tier-2/3
candidate if ARMv8-M ever earns a HAL.

## Adjacent seeds this composes with

The growable-slab donation op (010 ruling 10's M5 seed); the shared
stats region (design 16's seed — wants tier-aware mapping); the
MAX_PROCESSES/PMP-budget question unit 5 forces; design 11's pending
§11 rescope. The M5 sketch takes all of these plus this document as
its agenda.
