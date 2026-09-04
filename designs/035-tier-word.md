# SawOS design 35 — M5 unit 4: the tier word + the flat profile

**Status: BUILT, Sep 4 2026 (As-built at the end) — dispatched under
`designs/025` (RULED): D-4 as ruled, plus design 19's flat-tier testing
target.** The tier track's last rung (1 → 1.5 → 2 → 4 complete with this).
Gate 366/366 across three profiles; the riscv32 and arm64 sections
byte-identical to the pre-unit baseline. SL-25 filed.

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

---

# As-built (agent, Sep 4 2026)

**GATE GREEN AT 366/366 ACROSS THREE PROFILES (123 + 123 + 120), AND THE FENCE
HELD: the riscv32 and arm64 sections are BYTE-IDENTICAL to the pre-unit
baseline, hash for hash.** The only motion is the flat profile's own 250-line
section and the totals line, both authorized by the brief. The C3 board smoke
passes 3/3 besides (this unit edits its HAL).

| | baseline `ebf76d0` | after |
|---|---|---|
| transcript | 514 lines, `c823c1b5cab6977e…` | 764 lines, `0588f8b7186cecd5…` |
| header block (8 lines) | — | IDENTICAL |
| riscv32 section (251 lines) | `0a2eb41724708e49` | `0a2eb41724708e49` |
| arm64 section (252 lines) | `f4324849ddf7af6f` | `f4324849ddf7af6f` |
| riscv32-flat section | — | 250 lines, all new |
| totals | `246 passed across riscv32 + arm64` | `366 passed across riscv32 + arm64 + riscv32-flat` |

The baseline was RE-RUN at the base commit in this worktree rather than taken on
trust, and it reproduced the brief's stated `c823c1b5…` exactly.

## 1. The word's surface

| layer | what landed |
|---|---|
| `sosabi.ops` | `SystemOp.TierGet = 6`; `public enum ProtectionTier: UInt { Isolated = 0, Flat = 1 }` |
| `sosabi.rights` | `SystemRight.TierGet = 1 << 14`, in `root_system_rights()` |
| `kcore.dispatch` | the `TierGet` arm — a rights check and `op_value(protection_tier() as UInt)` |
| `sos.system` | `System.tier() -> Result<ProtectionTier, SosStatus>` |
| HAL | `prot_isolated() -> Bool` on all four profiles |

`TierGet` is **the first op on any object that answers a plain fact about the
machine.** Every other op either performs an act or mints a handle; this one
reads no state, spends no resource, takes no argument and cannot fail. That is
also why the dispatch arm needed no helper: the body IS the rights check and the
word.

`System.tier()` decodes exactly and PANICS on a word it cannot name, in
`decode_boot_handle`'s exact idiom and for its exact reason — the kernel wrote
it out of the same declaration this function reads, so a value that does not
decode is a kernel/`sos` skew rather than a runtime condition.

**Two words, and the brief's own example list needed correcting.** The brief
named `map_wx_refused` and `map_exec_gated` among the isolation proofs. They are
NOT: both refusals are raised in `kernel/core/dispatch.saw` (the W^X word check
and the `MemoryRight.MapExecute` gate) and are argument checks, not hardware
denials. The tier word disclaims nothing about the kernel boundary, so both run
on flat and PASS — verified in the gate. See §4.

## 2. The boot-info half: SCOUTED, and there is NO CARRIER

Recorded as the brief asked, with the evidence rather than the conclusion alone:

1. **`sosabi.boot` carries handles only** — three words (tag, kind, handle) and
   a `BootHandleKind` defined as "EXACTLY THE GIVABLE KINDS". No fact column, no
   header, no reserved word. The tag word is explicitly "the giver's own word,
   handed back unread", so putting a platform fact there would violate its own
   stated contract.
2. **A bootinfo SECTION was ruled out by name**, in `designs/sawlang/232`:
   "DELIVERY IS THE RETRIEVAL OP, NOT A BOOTINFO SECTION — a bootinfo section
   written into the address space would freeze a struct layout in raw memory as
   permanent ABI … (seL4's BootInfo frame is the cautionary precedent; Zircon
   went message-based for this reason)."
3. **A build-time constant cannot see the tier.** `kernel/sysapi/Saw.toml`
   depends on `sosabi` and `sosrt` and no HAL, so the `PIPE_BODY_BYTES`-style
   "build define re-exported through `sos`" carrier is structurally unavailable.
4. **No existing op advertises a platform fact.** A repo-wide grep for
   `boot info|bootinfo|boot_info|platform_info|machine_info|sysinfo` returns
   design prose and zero code. The only place the kernel names its platform is
   the boot banner, which goes to the console and never to userspace.

**So the op is the whole surface, and no record was invented.** D-4's ruled
"getter + boot info" is satisfied by its checkable half; the other half has
nothing to attach to and should be re-opened only if a boot-info carrier is ever
built for some other reason.

## 3. The profile packaging, and the two seam changes it forced

`hal/riscv32-flat/` is **250 lines of facade, not a fork.** The runner maps the
virt board's own directory a SECOND time, as `rv32virt=hal/riscv32/kernel`, so
the profile re-exports the arch half from `rv32core` and the board half from the
board. A Saw module's name comes from its `--module-path` mapping, so one
directory is the board's `hal` in one build and a component of somebody else's
`hal` in another.

**Design 23's consolidation is untouched:** `riscv32-common` is still the one
arch home, `hal/riscv32/` is still the one virt board, and neither gained a copy
of anything. Shared with the board and NOT duplicated: `boot.S`, `virt.ld`, the
three user linker scripts, `sink.c`, `trap.S`, `syscall.c`, the
`tests/riscv32/*.S` payloads, and every root/child `.sosimg` (same triple, so
userspace is byte-identical — nothing about a process image knows the tier).

Four runner keys carry it, each defaulting to what the two existing entries
already had, so neither of them changes: `hal_board`, `tests_arch`,
`build_tag` (two profiles on one triple must not relink each other's objects —
the hazard the esp32c3 section already documents), `banner_arch`.

**A build flag was not an option, and that is a fact rather than a preference:**
this toolchain has no conditional compilation at all — no `--define`, no `cfg` —
and the only build-side switches in the runner are `--target-features` and
`--module-path`. A module swap is the mechanism the language gives.

### 3a. `prot_replay_at_switch()` and `prot_isolated()` are FUNCTIONS — SL-25

The unit's real discovery, filed as **SL-25**: **a module-level `static` does not
carry module identity the way a free function does**, so two modules in one
compilation unit cannot both declare one — the collision is reported at the
KERNEL ENTRY, a file that names neither module. Probed down to three files;
`prot_reset` and its six siblings ARE declared in both `rv32core` and the flat
profile and coexist perfectly, which is exactly the asymmetry and what made the
failure read as a spelling problem.

The consequence is bigger than this unit: **a build profile is precisely a
module that overrides part of another's seam, so a HAL seam value a profile may
need to override CANNOT be a `static`.** `PROT_REPLAY_AT_SWITCH` had been a
`public static Bool` in the shared arch module since design 27 and was
therefore un-overridable — the flat profile could not have existed without
finding this.

Both became zero-argument functions, and both **moved from `rv32core` to the
BOARDS** while they were being changed, which is `riscv32-common`'s own split
rule applied correctly ("ask what would change if the board changed"): two
profiles of one architecture disagree about both, so neither was ever an
architectural fact. `arm64` follows suit. Both still fold — a leaf returning a
literal inlines — so `load_domain`'s replay loop still compiles away where it is
dead.

The other HAL constants (`PROT_GRAIN`, `GRANT_ROW_BUDGET`, `FRAME_BYTES`, the
memory map) stay `static`s because nothing overrides them. **The sharper version
of SL-25, filed and not met: a profile that had to override a SIZE rather than a
flag has no spelling at all today**, because a `static_assert` operand or an
array length needs a compile-time constant and a function is not one.

### 3b. PMP is OPENED, and `prot_open_all()` is the ARCH's

"PMP left open" cannot mean "write nothing": RISC-V gives default-DENY for free,
so at reset (every `pmpcfg` byte zero, A=OFF) a profile that merely emptied its
`prot_*` bodies would deny root its own first instruction. The flat profile
therefore takes one affirmative action — a single TOR entry over `[0, 4 GiB)`,
RWX — and that is the only hardware it touches.

The routine lives in `rv32core` beside `prot_reset`, whose exact opposite it is.
Two reasons: PMP is architectural (the body would be identical on any RV32 M/U
part with PMP), and the CSR writers are `extern "C"` leaves — SL-6 — so two
modules declaring one C symbol collide across the compilation unit. **The flat
profile declares no externs at all.**

It is published from BOTH `prot_commit` and `prot_switch`, which are the two
seam calls that survive `prot_replay_at_switch()` being false on the paths that
matter: `load_domain` calls `prot_switch` unconditionally (the real kernel's
path), and the pre-M2 harness kernels under `tests/` program the seam DIRECTLY
and end at `prot_commit`. Covering both means no new HAL hook, no `kernel/core`
edit, and no "have we opened it yet" state to get wrong.

## 4. The excluded cases — four, by name, with reasons

The runner grew a `tier` key defaulting to "every tier". **Four cases carry one;
every other case in the table runs on all three profiles.** The exclusion block
is skipped entirely when empty, which is what keeps the isolated sections
byte-identical.

```
  tier flat: 4 isolation proofs excluded (this platform cannot deny, and says so)
    - umode_access_fault: a U-mode store into kernel .text must FAULT; a flat
      platform permits it and the line never appears
    - root_server_oversteps: root reaching into kernel memory must FAULT; a flat
      platform lets the store land
    - process_isolation: peer isolation is the property the Flat tier disclaims
      BY NAME (design 19) — the child's poke would land
    - map_unmap: unmap's revocation half is the other property Flat disclaims by
      name — the touch after unmap would succeed
```

The list was derived by grepping every case's `expect_out` for a HARDWARE fault
string (`SOS: fault `, `fault store-access-fault`, `data abort`) rather than by
reading names, which is what caught that the brief's two examples do not belong
and that nothing else does. Each of the four is engineered to fail BY OMISSION
on flat — the expected line simply never appears — so none would have failed
loudly.

**What runs on flat and passes, which is the tier's contract witnessed:**
`map_basics`, `map_into_child`, `share_double_map`, `memory_recycle`,
`mapping_slot_free`, `quota_vs_wall` (map succeeds honestly); `map_wx_refused`
and `map_exec_gated` (kernel-side refusals, not tiered); `iomemory_carve` (whose
refusal goes through `device_window_ok`, which the flat profile re-exports
UNCHANGED — the tier disclaims hardware denial, not the kernel's argument
checking); and the whole object-model suite. The five riscv32-only `arches`
lists were widened to name the flat profile, so it runs 119 of riscv32's 123.

## 5. `tier_word`, and the one thing the gate does not witness

The new case reads the word, asserts it is STABLE across two asks (a platform
fact does not change; an implementation sampling mutable state would be a
different op), and then asks through a System sibling minted WITHOUT `TierGet` —
which faults, because **rights are not tiered.** That third assertion is the
positive statement of what `Flat` does not disclaim, and it is asserted ON THE
FLAT PROFILE, where it means the most.

**IT IS FLAT-ONLY IN THE GATE, and that is the fence rather than a claim about
the case.** Adding it to the riscv32 and arm64 lists would change `N` in every
`[i/N]` row in both sections. So the ISOLATED answer is witnessed by a PROBE
instead, run in this worktree by temporarily widening `arches` to all three:

```
riscv32       [1/1] ✓ tier_word        (asserts "SOS tier: Isolated")
arm64         [1/1] ✓ tier_word        (asserts "SOS tier: Isolated")
riscv32-flat  [1/1] ✓ tier_word        (asserts "SOS tier: Flat")
ALL SOS TESTS PASSED (3 passed across riscv32 + arm64 + riscv32-flat)
```

The widening was reverted. **Promoting the case to all three profiles is a
one-line change** and is owed to whichever unit next holds an authorization to
move those rows — unit 8, the M5 docs sweep, is the natural home. The
`{tier}` substitution already makes it profile-correct on all three.

## 6. The finding that cost the fence, and was paid rather than argued

The unit initially shipped an `@export`ed C floor stub (`sos_system_tier_get`)
beside the other six System ops. **It broke the fence: all 126 riscv32 sosimgs
grew by 24 or 40 bytes and every image-size row in the riscv32 section moved.**
arm64 showed 0 — its segments are page-granular, so small growth hides in
padding, which is why this is only ever visible on the profile whose
`PROT_GRAIN` is 4.

This is **design 32's finding recurring**, and its As-built named the mechanism
exactly ("the new floor `@export` links into every image as all ~40 of its
neighbours do"); design 34 confirmed the converse. The mechanism is visible in
the emitted IR: every `@export` lands in `llvm.used`, which is a linker GC root.

Unit 4 is the first unit with a REASON not to pay it, so it did not: the stub
was removed, measured both ways (with it, the rows move; without it, the section
hashes to the baseline exactly), and the reasoning is recorded at the site in
`floor.saw`. The typed `System.tier()` is the whole surface meanwhile, which the
vDSO discipline is comfortable with. **Adding the stub back is one line** for
whichever unit holds transcript authorization.

The number worth carrying forward is not this op's 24 bytes but the total: the
floor exports about sixty stubs on those terms, and a process that calls none of
them carries all of them. On the MCU-class targets designs 19 and 20 aim at that
is the kind of constant that decides a port. **Making the C altitude opt-in per
package is a real unit; it is filed here, not built.**

## 7. Two ABI hygiene fixes, taken in passing

Both are one-line consistency repairs in files this unit was already editing,
and neither changes behaviour (`static_assert`s are compile-time):

- **`sosabi.ops`'s SystemOp bound assert named `ProcessCreate` (case 4)** while
  the table's top was `SlabDonate` (5). The rule stated three lines above it is
  "one assert per table, ON ITS HIGHEST CASE"; design 32 added a case without
  re-pointing it. It names `TierGet` now.
- **`SystemRight.SlabDonate` had no `>= (1 << 8)` assert**, though the rule
  beside it says "one assert per kind-specific case". Backfilled beside the new
  one — the repetition IS the check, and a hole in it checks nothing.

## 7a. A PRE-EXISTING FLAKE THIS UNIT OBSERVED — `thread_preempt`

Not caused by this unit and not fixed by it, but it fired once during the
confirming run (365/366) and it contradicts something CLAUDE.md says, so it is
recorded rather than shrugged at.

CLAUDE.md lists `thread_preempt`'s A/B interleave among the timing-dependent
rows "that no assertion reads". **An assertion does read it.** The case asserts
`"AB", "BA", "AB"` as ordered substrings, deliberately and with a good comment
explaining why direction-changes are the robust claim (whoever goes first, three
crossings happened).

What the comment does not anticipate is that the substring match needs the
letters to be **adjacent in the console stream**, and the kernel's own
`SOS: timer tick …` lines are printed into that same stream. The failing run:

```
AAAAAAASOS: timer tick 0x00000002 at 0x80200a5e
BSOS: timer tick 0x00000003 at 0x80200956
SOS: timer tick 0x00000004 at 0x80200a5e
ABBBBBBB
```

The interleave is `A×7, B, A, B×7` — three crossings, exactly what the case
means to prove — but a tick line sits between the `B` and the `A`, so no
adjacent `"BA"` exists and the match fails on a run that demonstrated the
property perfectly.

Re-run five times in isolation: 5/5 pass. The full gate then reproduced the
green transcript byte for byte (`0588f8b7…` twice). So it is a genuine flake
with a low rate, and this unit's only relation to it is making a gate run longer
— a third profile changes host timing, which is exactly the kind of thing that
shifts where a tick lands.

**The fix, for whoever takes it:** match the interleave against the letters-only
projection of the output (strip lines beginning `SOS:`) rather than against the
raw stream. That preserves the direction-change claim exactly and removes the
dependence on where the kernel's own diagnostics land. CLAUDE.md's sentence
wants amending in the same pass: the interleave IS asserted; what is unasserted
is the tick COUNT and the `interrupts=` column.

## 8. What the lead should see

1. **SL-25 is the substantial finding**, and its consequence outlives this unit:
   a HAL seam value a build profile may override cannot be a `static`. The
   sharper version — a profile overriding a SIZE has no spelling at all — is
   filed and unmet, and it is a real constraint on any future tier-2 board that
   wants a different `GRANT_ROW_BUDGET` by profile rather than by board.
2. **The brief's isolation-proof list was wrong in two places**, and the
   correction is doctrinally load-bearing rather than clerical:
   `map_wx_refused` and `map_exec_gated` are KERNEL refusals, and the tier word
   disclaims nothing about the kernel boundary. They run on flat and pass, which
   is a better proof of design 19's "the capability model's AUTHORITY
   enforcement survives intact" than any case written for the purpose.
3. **The tier word's Isolated answer is probe-witnessed, not gate-witnessed**
   (§5), purely because of the byte-identical fence. One line to fix when rows
   may move.
4. **The C floor stub is deliberately absent** (§6) and one line to add.
5. **A pre-existing `thread_preempt` flake fired once** (§7a) — the A/B
   interleave IS asserted, against CLAUDE.md's claim, and a kernel tick line
   landing between two letters breaks the substring match. Not this unit's,
   not fixed here, and the one-line fix is named.
6. `spec.md` is untouched, per the brief's out-of-scope list — §5b/§2's tier
   table belongs to unit 8, which now has a built word and a built profile to
   describe.
