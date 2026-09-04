# SawOS design 33 — M5 unit 2: placement (the address becomes the kernel's answer)

**Status: BRIEF, Sep 3 2026 — dispatches under `designs/025` (RULED):
D-1 step two, arm64 ONLY.** riscv32 keeps identity linking and its
`child*.ld` deliberately (ruling 11: "identity is the tier-2 answer"
stays a living demonstration). Builds on units 1 + 1.5 as merged:
the three-name seam, per-process TTBR0 sets, the high-half kernel
over the linmap, the whole low half now user territory.

## The one-sentence goal

On arm64, a user address is the KERNEL'S ANSWER: every user image
links at ONE canonical base and loads wherever physical frames are
(the allocator's answer), `map` returns a kernel-chosen VA (no hint
— ruled), the per-child `.ld` scripts and the runner's base
arithmetic collapse on that arch, and §5.5's identity law re-scopes
to the tiers that keep it.

## The structural change, named honestly

Until now a grant row's `base` was one number serving two meanings
(VA = PA). Placement splits them: **a `GrantRow` carries a user VA
range AND the physical base it translates to** (addresses-as-data
stay physical everywhere else — Memory capabilities, the region
table, sosimg records are untouched). Consequences, each a section
of the work:

1. **The seam**: `prot_update(p, base, top, perms, device)` grows a
   `pa` argument (or the row itself — the agent picks the spelling
   that keeps the MPU tier's identity implementation trivial: there
   `pa == base` always, assert it). The arm64 body installs
   descriptors whose output address is `pa`, input range the VA.
2. **The copy funnels**: resolving a user VA to a PA walks the
   TARGET's grant rows (bounded: `MAX_GRANT_ROWS`), then
   dereferences through the linmap — the shape 029 said would exist
   ahead of need, now real. A VA that resolves to no row is the
   fault it always was.
3. **The loader**: `place_image` copies segments to PHYSICAL frames
   and installs rows mapping the canonical VA to them. Physical
   placement comes from the existing region/pool machinery — the
   allocator composes here, deliberately.
4. **`map`**: chooses the VA (a per-process cursor above the image
   region is sufficient v1 policy — record the policy at the site);
   answers it in the op's existing return shape. IoMemory likewise.
   The `Mapping` object records the row it owns, unchanged in
   meaning.
5. **Link bases**: ONE arm64 user base (keep `0x4020_0000` — the
   canonical number every process now sees). `child.ld`/`child2.ld`
   (arm64) collapse into `root.ld`'s base or one shared script; the
   runner's arm64 base arithmetic (`child_region_base` etc.)
   simplifies to match. riscv32's scripts and constants DO NOT MOVE.

## §5.5 and §2.5, at doc-comment level only

The identity law's re-scope ("an address is the same number in every
process" becomes the TIER-2 answer; on tier 1 an address is
per-process and kernel-chosen) and §2.5's "shared at its own
address" amendment are UNIT 8's prose. This unit updates doc
comments at edited declarations and the two ABI.md files, nothing
else in spec.md.

## Authorized transcript motion (025 ruling 9 — the exact list)

Verified against the 0.5.0 baseline (495 lines, hash `801a0f98…`):
the gated report contains NO raw addresses — only case rows and
image sizes — and the runner's address expectations are
TEMPLATE CONSTANTS (`root_entry`, `child_region_base`, `pool_base`,
arm64 block, `tools/sos_runner.py` ~line 613).

- AUTHORIZED: every **aarch64** `.sosimg` size row (relinking at one
  base may move any of them); the runner's arm64 constants block and
  any console rows templated on it; case rows only if a case is
  RENAMED (do not).
- NOT AUTHORIZED: any **riscv32** row of any kind — that half of the
  diff must be BYTE-IDENTICAL, and it is the proof the tier split
  is real. Anything else moving on arm64 beyond the size rows and
  templated address rows is a finding.

## The proof

- Existing suite: every case now runs with per-process, kernel-
  chosen addresses on arm64 — the suite IS the placement proof.
- One new case, `map_placed` (or a leg on an existing map case):
  two processes map the same region and receive DIFFERENT VAs; both
  read the shared bytes — "shared at per-process VAs", witnessed.
- `share_double_map` (identity-shared today) is the case most
  likely to need retargeting — its rows are covered by the
  authorization above only if the change is address-template or
  size; a semantic retarget must be named in the As-built.

## Out of scope

riscv32 anything; the tier word (unit 4); vDSO true-mapping (M6+);
spec.md prose (unit 8); COW/overcommit (never in v1); guard pages
(record as a refinement if the VA policy makes them cheap).

## Recording duties

As-built here: the GrantRow/seam spelling as landed, the VA policy,
the funnel-resolution shape, the collapsed-vs-kept linker scripts,
the enumerated row diff (before/after for every moved row class),
gate evidence (riscv32 byte-identical + arm64 motion within
authorization). SL entries if the language bites. Close the tracker
entry in place.
