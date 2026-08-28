# Design 238 — The sawos Split: SOS Leaves the Language Repo

**Status: AUTHORED Aug 19 2026** from the coupling sweep recorded below (user:
"i want to move the sawos/sos code from this directory (sawlang) into ../sawos
— i want to retain the sawos tests and build system (blade?)"). **Queue slot:
BEFORE the M3 unit ladder** (user: "schedule it before more M3 sos work"), and
AFTER the sos riders batch — see "Scheduling" below.

**FOUR RULINGS, Aug 19 (same session):**
- **D-a RULED — imgformat moves to `libs/`.** "moving imgformat into libs is
  fine for now (make a note of the blade plugin idea since that is probably the
  way forward in the future)." The plugin is [BACKLOG], recorded as the
  probable future direction, not a dependency of this brief.
- **The downstream-CI gate is REPLACED by a standalone freestanding suite in
  sawlang** — "instead of running sawos from inside sawlang, we should create a
  stand-alone test suite which exercises the set of sawlang features needed for
  software like sawos." sawlang never checks out sawos. See "The gate,
  re-founded".
- **No shared suite lock across repos** — "they can run independently."
- **Toolchain discovery is `$PATH` first, pinned fetch second** — "the sawos
  repo should get for 'sawc' and 'blade' in the $PATH and if they don't exist,
  it should pull down swoodtke/sawlang from github and use that instead (at a
  fixed version/sha ideally so churn in sawlang doesn't break sawos)." Adopted;
  three sub-decisions it opens are D-b1..D-b3 below.

`../sawos` already exists as an empty directory. The repo this brief splits
FROM is `sawlang` (renamed from `claudes-lang`; stale absolute paths in older
briefs and tooling still say the old name).

## Why now, and not after M3

M3 is the largest planned expansion of `sos/` — six units from interruptibility
through Memory/IoMemory to quotas and death notifications [232]. Every M3 commit
written in-tree is a commit that has to be re-homed later, and the extraction
below is a `filter-repo` whose cost scales with the history it rewrites. The
split is cheapest at a quiet point in the tree, and the tree is quiet now: the
Aug-18 riders are the only sos work in flight.

The counter-argument — "split after M3, when the OS is real" — buys nothing.
Nothing in the units below gets easier with more sos code; two of them get
harder in proportion to it.

## The consumer sweep (obligation 2)

The boundary change here is a repo-level one, so the sweep asks: what, outside
`sos/`, depends on `sos/` being at that path — and what inside `sos/` depends on
anything outside it. Grep evidence, Aug 19:

- **Every package dependency under `sos/` resolves inside `sos/`.** The full
  set is `sosabi = ../abi`, `sos = ../kernel/sysapi` (1 manifest, `sos/root`),
  `sos = ../../kernel/sysapi` (19 manifests under `sos/tests/`), and
  `sosrt = ../../rt/common` — 20 sysapi consumers and 21 sosrt consumers in
  all. Nothing under `sos/` names `libs/`, `blade/`, or `sawc/` as a
  dependency. The tree is self-contained on the package axis.
- **Blade's SOS fixtures are self-contained.** `blade/tests/fixtures/sosapp`
  and `sosdefaults` declare their own `[sos]` manifests and reach into no part
  of `sos/`. Blade keeps exercising its sosimg emitter, its `[sos]` manifest
  parsing, and `sosimg_wire`/`sosimg_config` after the split, with no sawos
  checkout present.
- **FOUR crossings exist** (corrected Aug 19 — the first pass said two; the
  mechanism that hid the other two is recorded below):
  1. `blade/Saw.toml:10` — `imgformat = { path = "../sos/imgformat" }`.
  2. `tools/sos_runner.py` — module constants naming `sawc/sawc.py`, `blade/`,
     `libs/toml/src`, `libs/semver/src` and `sos/imgformat/src` off a computed
     `REPO_ROOT`, plus `sos/kernel`, `sos/tests`, `sos/hal`, `sos/rt/*`,
     `sos/root`.
  3. **`tools/blade_bootstrap.py:42-43`** — `IMGFORMAT_DIR = os.path.join(REPO,
     "sos", "imgformat")`, passed as `--module-path imgformat=…` at line 213.
     **This one GATES**: it is battery's `bootstrap` stage (`battery.sh:79`)
     and CI's blade job, so unit 2 must move it with the package or the stage
     goes red pointing at a deleted directory. `blade_bootstrap.py:44`'s
     `LIB_DIRS = [("toml", …), ("semver", …)]` is imgformat's natural home
     after the move.
  4. **`tools/framesizes.py:98-111`** — an entire `sos` measurement group
     (`--only sos`, `sos-kernel` and one target per `sos/tests/*.saw`). Design
     163's hand-run frame-size investigation: NO Makefile target, NO battery
     stage, NO CI job, so it degrades to "the sos group measures nothing"
     rather than reddening a gate. Unit 5 either moves the group to sawos or
     deletes it from sawlang — silently leaving a group that can never match
     is the one option the doctrine forbids.

  **The mechanism (obligation 4).** The first pass grepped for the literal
  string `sos/`, so every path BUILT from components —
  `os.path.join(REPO, "sos", …)` — was invisible to it, and the
  `sort -u` over `grep -hn` output collapsed by line NUMBER rather than by
  package, which is where the bogus "seven packages" count came from. Both
  misses are that one mechanism. The re-sweep that found crossings 3 and 4
  searched the CONSTRUCTION (`join([^)]*"sos"`) across every `.py` and `.sh`
  in the repo, and is the sweep of record; any later audit should repeat that
  form, not the string form.
- **sawc has no dependency on sos.** Its nine hits are comments plus one
  runtime-VARIANT name (`sos-hosted` in `runtime_abi.py`, `rt/ABI.md`) — a
  string in a link-time table, not a path. `--freestanding`,
  `--no-hidden-alloc`, `--runtime-provider` and the `sos-hosted` variant stay
  in sawc and are consumed from sawos as compiler flags, which is what they
  already are.
- **The history does NOT separate cleanly.** 97 commits touch `sos/`; the
  non-`sos/` paths they also touch include `blade/src/sosimg.saw`,
  `blade/Saw.toml`, `blade/tests/*`, eight `designs/*sos*.md`, `designs/todo.md`,
  `CLAUDE.md`, `.claude/skills/saw-lang/SKILL.md`, and a long tail of
  `examples/`. `filter-repo --path sos/` preserves the sos side of those
  commits and drops the rest — partial fidelity, not none. See D-d.

Conclusion the sweep supports: the split is a file move plus a path resolver,
with one genuine dependency decision (D-a) and one gate that must be rebuilt
(below). It is not a refactor of either tree. The correction does not change
that conclusion — crossings 3 and 4 are two more path constants, not new
coupling — but it does change unit 2's file list and unit 5's, which is why
the miss mattered.

## D-a — the imgformat cycle (RULED Aug 19: `libs/imgformat`)

`sos/imgformat` exists so the sosimg layout is written once: Blade WRITES the
images, the kernel core READS them, and the package is the shared authority that
stops the two from drifting (`sos/imgformat/src/lib.saw:1-5`, `sos/spec.md` §6).
Blade takes it as a path dependency and compiles it in; the kernel reaches the
same sources through `--module-path imgformat=…` (`sos_runner.py:74`).

If `imgformat` goes to sawos, **sawlang cannot build Blade without a sawos
checkout** — a repo-level cycle on the wrong axis. sawlang is the more
fundamental repo; its CI would need sawos cloned to build its own package
manager, and a sawos-side change could redden sawlang.

**RULED: `sos/imgformat` → `sawlang/libs/imgformat`.** `libs/` today
holds exactly Blade's dependency set (toml, semver); imgformat joins it
coherently, sawlang stays self-contained, and sawos depends downward only,
reaching the sources the same way it reaches sawc — through the D-b resolver.

The honest objection: an OS image format then lives in the language repo. That
is already true of the thing that is harder to move — `blade/src/sosimg.saw` is
the whole `[sos] emit = "sosimg"` target, ELF32→sosimg conversion included, and
it is Blade's code. The format package sitting beside its producer is the
consistent position, not a new compromise.

Alternatives considered and not recommended:

- **imgformat stays in sawos, Blade takes a git dependency on it.** Blade
  resolves git deps already (`src/git.saw`), so this builds — but it makes
  every sawlang Blade build network- and sawos-dependent, and pins a bootstrap
  cycle into the lockfile. Rejected on the same axis argument.
- **Blade grows an out-of-tree target plugin**, so `sosimg.saw` AND `imgformat`
  both live in sawos and Blade knows nothing about SOS. **This is the probable
  future direction** (user, Aug 19) and is filed to [BACKLOG] — but it is real
  design work on Blade's target model, and gating the split on it trades a
  two-day move for a two-week one. When it lands it SUPERSEDES this ruling:
  `libs/imgformat` goes back to sawos and `blade/src/sosimg.saw` follows it.
  Keeping imgformat a self-contained package with no sawlang-ward dependencies
  is what keeps that reversal cheap, and unit 2 must not add any.
- **Duplicate the layout with a drift test.** Refused: the duplication is
  precisely what the package was created to remove.

## The gate, re-founded (RULED Aug 19)

`CLAUDE.md` makes sos a COMPILER gate: a sawc change owes both the full suite
and `tools/sos_runner.py` on both arches, "the suite alone does not cover sos/",
and `battery.sh` carries `sos` as a stage. That is not ceremony — sos is the
only freestanding, no-hidden-alloc, cross-architecture, user-mode-crossing
exercise the codegen has.

The brief's first draft kept it by having sawlang CI check out sawos. **RULED
OUT.** sawlang never references sawos. Instead the coverage is re-founded as a
FIRST-CLASS sawlang suite that names the features directly.

**The diagnosis this ruling rests on:** sos is a SYSTEM test doing a UNIT
gate's job. When it goes red it says "the OS did not boot" — not "the
`--no-hidden-alloc` check regressed on enum init". Every feature below is
currently covered only incidentally, by a test that traverses it on the way to
something else. Naming them directly is more diagnostic AND cheaper to run.

**The feature inventory the new suite owns** — what `sos_runner` is really the
gate for:

- `--freestanding` codegen: no host runtime, no libc, no hidden dependencies.
- `--no-hidden-alloc`: the refusal surface, per construct.
- `--runtime-provider`: a package supplying the four frozen `__saw_rt_*` seams,
  with the signature check against `sawc/rt/ABI.md`.
- **Cross-target codegen for riscv32 AND aarch64** — a 32-bit target from a
  64-bit host is the live hazard here (platform-width `Int`, pointer size,
  struct layout, calling convention).
- Linking at a fixed load address with a custom linker script.
- `--module-path` module composition (the kcore/imgformat/sosrt shape).
- Blade's non-host target path: `blade build --target <triple>`.

**It must RUN, not just compile.** A suite that compiles and links freestanding
riscv32 proves the compiler emitted something, not that the something is
correct — calling convention, struct layout and trap-frame bugs are exactly
what sos catches today, and all three survive a clean link. So the cases stay
QEMU-executed; they are just tiny programs that print and exit rather than an
operating system.

**This is mostly kept code, not new code.** `sos_runner.py` splits at a seam
that already exists:

- GENERIC, stays in sawlang as the new suite's engine: `_run`, `_find_clang`,
  `_probe_tools`, `_run_qemu` (already arch-table-driven —
  `[qemu, *arch["qemu_args"], "-nographic", "-kernel", elf]`), `_check` and the
  transcript matcher (`_is_english_embedding`), and the `ARCHES` table.
- SOS-SPECIFIC, goes to sawos: `_build_blade`, `_build_root_image`,
  `_stitch_root_image`, `_root_image_path`, `_check_arch_free`, `_build_shared`,
  `expectations`, `arch_dirs`, and the `TEST_CASES` table
  (`sos_runner.py:334`).

**The engine is FORKED, not shared.** sawos's harness needs the generic half
too, and it cannot borrow sawlang's: the D-b resolver is scoped to sawc, blade,
imgformat and Blade's libs — it does not resolve Python modules — and in the
`$PATH` steady state there is no sawlang source tree on disk at all. So unit 5
COPIES the generic half into sawos rather than depending on it. This is the
same call as the boot stubs below and for the same reason: the two harnesses
diverge immediately (sawos's grows sosimg stitching and root-package builds;
sawlang's grows feature rows), and a shared engine across two repos with no
package relation between them would be a distribution problem invented to
avoid ~200 lines of duplication. Neither copy is authoritative after unit 5.

The boot stubs are the one duplication: sawlang's suite needs a minimal
`_start` (stack, call, exit device) per arch, derived from `sos/hal/<arch>/
kernel/boot.S` (231 and 270 lines today, nearly all of it kernel setup the
suite does not need). The two copies then diverge deliberately — a test stub is
not a kernel — so this is a fork, not a shared file, and the brief says so
rather than pretending a common ancestor should be maintained.

Spelling: `tools/freestanding_runner.py`, `make freestanding-test`, and a NEW
`freestanding` stage in `battery.sh`. It is a sawlang suite and takes sawlang's
suite lock like any other.

**The `sos` battery stage is NOT renamed — it is ADDED alongside and removed at
unit 5.** Renaming at unit 1 would leave units 2-4 with no runnable
`battery.sh suite sos` gate, which is exactly the code those units edit. Both
stages coexist from unit 1 to unit 5; `sos` goes away with `sos/`.

**And unit 1 adds a CI job.** `.github/workflows/ci.yml:152-176`'s `sos` job
runs `make sos-test` DIRECTLY, not through `battery.sh`, so nothing about the
battery stages touches CI. Without its own job the freestanding suite would run
only on a developer's laptop — and unit 5 deletes the `sos` job, so the net
would be no freestanding coverage in CI at all. That would gut the ordering
argument this whole section rests on. The new job clones the `sos` job's
toolchain install (clang, lld, qemu-system-misc, qemu-system-arm, llvmlite).

**Ordering consequence, load-bearing:** this suite lands BEFORE the extraction.
Once sawos pins a sawlang SHA, compiler churn cannot break sawos — but nobody
LEARNS that churn broke sawos until someone bumps the pin. The pin is only safe
because sawlang has its own coverage; if the suite slips, the pin becomes a
silent-rot machine. It is unit 1 for that reason.

## The suite lock (RULED Aug 19: not shared)

sawos and sawlang run independently — no cross-repo serialization. This costs
nothing to implement: the lock is not in any harness, it is an agent protocol in
`CLAUDE.md` (grep for `saw-suite-lock` in `tools/`, `test_runner.py` and the
Makefile returns nothing). Unit 7 narrows that paragraph to sawlang's own
suites and sawos's CLAUDE.md gets its own, with its own lock path.

The residual risk is CPU, not correctness — two heavy suites on one laptop is
slow, and the DF-182f history (loadavg >700) is what the protocol was written
against. Accepted: the repos are usually worked one at a time, and a wedged
laptop is visible in a way a silent state collision would not be.

## D-b — the toolchain resolver (RULED Aug 19: `$PATH`, then a pinned fetch)

"Every place sawos names a sawlang artifact" is a position-quantified rule, so
it gets one chokepoint rather than a hand-kept list of constants (obligation 1):
a single `sawos/tools/toolchain.py` whose docstring NAMES its entry points
(`sos_runner.py`, the Makefile, CI). It owns sawc, blade, imgformat, and
Blade's toml/semver — nothing else in sawos may compute a sawlang path.

Resolution order — **explicit intent first, then `$PATH`, then the fetch**:

1. **`SAWC` / `BLADE` / `SAWLANG_ROOT` env vars** — anything the operator
   named. `SAWC`/`BLADE` are how CI pins a prebuilt Blade (precedent:
   `sos_runner` already sets `env["SAWC"]` for Blade to read,
   `sos_runner.py:1322`). `SAWLANG_ROOT` is a local checkout, for working both
   repos at once against UNCOMMITTED compiler changes — the only way to test a
   sawc change against sawos before it is pushed, so the split must not make it
   impossible.
2. **`sawc` and `blade` on `$PATH`** — the developer fast path.
3. **Fetch `swoodtke/sawlang` at the pinned SHA** and bootstrap it, cached.
4. Otherwise a refusal naming all three.

`SAWLANG_ROOT` sits in group 1 rather than below `$PATH` deliberately. Below
it, a developer standing in a sawlang checkout with any `sawc` installed
globally would silently get the INSTALLED compiler instead of the one they are
editing — D-b2's failure mode, reachable from inside sawlang itself, and it
would break unit 4 (which lands the resolver in-repo with `SAWLANG_ROOT`
pointing at the repo). The user's ruling is preserved exactly where it applies:
for a sawos user who has set nothing, resolution is `$PATH` then fetch.

**D-b1/b2/b3 RULED Aug 24 (user): THE ABSOLUTE SIMPLEST THING, pre-1.0.**
The language has no real users yet, so perfect version fidelity is admirable
but unnecessary; before v1.0 the self-hosted story lands (the compiler is
COMPILED — see design 231), which makes all of this simpler, and the
machinery below is deliberately not built ahead of that.
- **D-b1**: `make install` writes `bin/` SHIMS (a `sawc` shim exec'ing the
  checkout's venv python against `sawc/sawc.py`, and the built `blade`
  binary copied/linked) onto a prefix (default `~/.local/bin`). No
  pyproject, no packaging — the two names exist, that is all step 2 needs.
- **D-b2**: OPTION (b) — `sawc --version` prints a plain semver from ONE
  source-of-truth constant; `sawlang.pin` records the version (step 2's
  check) and the SHA (step 3's fetch); the granularity asymmetry is
  ACCEPTED AND DOCUMENTED. Standing practice going forward: version bumps
  become rigorous (a brief that changes user-visible behavior bumps).
  A mismatch is still the loud refusal naming both — never a silent build.
- **D-b3**: the fetch path requires `python3` (clean refusal naming it if
  absent) and creates its own venv inside the cache entry with llvmlite —
  the ten-line step that keeps the fresh-machine promise; nothing fancier.

Three sub-decisions this opens — each a real gap, none blocking (RULED
above; the analysis below is the record):

- **D-b1 — there is no `sawc` binary to find (OPEN).** sawc is
  `python sawc/sawc.py` plus llvmlite: no `pyproject.toml`, no `setup.py`, no
  `bin/`, no console script. And `blade` is BUILT FROM SAW SOURCE BY sawc, so
  neither name exists on any `$PATH` today. Step 2 presupposes an install story
  sawlang does not have. It is small — a console-script entry point, or `bin/`
  shims plus `make install` — but it is a sawlang deliverable that must land
  before step 2 can ever hit, and it belongs to this brief (unit 3) or to a
  filed successor. Until then the resolver's step 2 is dead code.
- **D-b2 — a `$PATH`-found sawc is UNPINNED, which inverts the goal (OPEN).**
  The pin protects the FALLBACK path while the FAST path is whatever the
  developer happens to have installed — so the common case is the unpinned one,
  and a stale or bleeding-edge sawc silently produces a build sawos never
  tested. `sawc` has no `--version` today (grep: no version flag, no
  `__version__`). Proposal: sawc gains `--version`, `sawlang.pin` records what
  sawos requires, and the resolver VERIFIES what step 2 found — a mismatch is a
  loud refusal naming both, never a silent build. That makes `$PATH` a fast
  path rather than a hole. Doctrine: never hide errors.

  **The granularity asymmetry, stated rather than papered over.** Step 3
  fetches a SHA; step 2 can only compare whatever `--version` prints. A semver
  string cannot distinguish two commits that share it — the normal case for an
  unreleased compiler — so an installed sawc verified by version is a WEAKER
  guarantee than a fetched one verified by SHA. Two ways to close it, and the
  brief does not pick: (a) `sawc --version` also emits its build SHA when it
  can determine one, and `sawlang.pin` records the SHA, so both paths check the
  same thing and an installed sawc that cannot name its provenance is refused;
  or (b) sawlang adopts a real release discipline, the pin records a version
  for step 2 and a SHA for step 3, and the asymmetry is accepted and
  DOCUMENTED. (a) is stricter and probably right; (b) is cheaper and honest.
  Owed before unit 3 writes `--version`.
- **D-b3 — the fetch is a clone-and-bootstrap, not a download (OPEN).**
  Fetching at a SHA yields Python source; the resolver then needs llvmlite
  installed and a `blade` built by sawc — minutes, not seconds. So it caches by
  SHA (`~/.cache/sawos/toolchain/<sha>/`) and builds once, and a cache hit is
  the steady state. What remains open is the bootstrap's shape (does the
  resolver create a venv for llvmlite, or require one?), not its reachability:
  **`swoodtke/sawlang` is PUBLIC** (user, Aug 19), so unit 4 fetches over
  HTTPS with no credentials and the fresh-machine promise holds as stated.
  This repo's own `origin` is SSH (`git@github.com:swoodtke/sawlang.git`) —
  a developer-remote preference, not a constraint on the resolver.

CI note: locally, auto-fetch is the point. In CI prefer an explicit checkout
step so the SHA is visible in the log and cacheable — same resolver, cache
pre-warmed, no behavioral fork.

## Units

0. **The oracle.** BEFORE anything moves, capture the acceptance oracle:
   `make sos-test` on main, both arches, full console transcript saved beside
   this brief. Every later unit is diffed against that transcript rather than
   judged "green" — a split that quietly loses a test case would otherwise
   still pass.
1. **The freestanding suite** (`tools/freestanding_runner.py`,
   `make freestanding-test`). Split `sos_runner.py` at the seam named in the
   gate section: the generic engine stays and gains a case table of tiny
   QEMU-executed programs, one or more per feature in the inventory, both
   arches. Minimal boot stubs derived from `sos/hal/<arch>/kernel/boot.S`.
   Adds a `freestanding` battery stage ALONGSIDE `sos` (not a rename — units
   2-4 still need `sos` runnable) and a `freestanding` CI job cloning the `sos`
   job's toolchain install. **Lands FIRST and lands WHOLE** — every later unit
   removes sos coverage from this repo, and this is what replaces it. Gate: the
   new suite green locally AND in CI, plus `sos-test` still green (sos has not
   moved yet, so both run).
2. **imgformat relocates INSIDE sawlang** (`sos/imgformat` → `libs/imgformat`,
   D-a). One repo, one commit, and the file list is CROSSINGS 1 AND 3 TOGETHER:
   `blade/Saw.toml`, `blade/Saw.lock`, `sos_runner.py:74`,
   **`tools/blade_bootstrap.py:42-43` (moving imgformat into its `LIB_DIRS`)**,
   and the doc references in `sosimg.saw`, `imgformat/src/lib.saw`,
   `blade/Saw.toml`'s comment. Adds NO sawlang-ward dependency to the package
   (D-a's reversal clause). Gate: `battery.sh suite sos freestanding
   **bootstrap**` — the bootstrap stage is named explicitly because
   `blade_bootstrap.py` is the crossing the first sweep missed and the one this
   unit is most likely to break. **The de-risking unit** — after it no
   dependency surgery remains and the split is a move plus a resolver.
3. **The install story** (D-b1). sawlang gains a way to put `sawc` and `blade`
   on a `$PATH` — console-script entry point or `bin/` shims plus
   `make install` — and `sawc --version` (D-b2), whose string is what the pin
   is checked against. Without this unit the resolver's `$PATH` step can never
   hit. If it grows past a day's work it EXITS to its own brief rather than
   stretching this one; the resolver then ships with step 2 documented as
   pending.
4. **The resolver, still in-repo.** `tools/toolchain.py` lands with
   `SAWLANG_ROOT` defaulting to the repo itself; `sos_runner.py`'s five path
   constants become resolver calls. All four resolution steps implemented and
   unit-tested, including the version check and the refusal. Suite green proves
   the funnel before the move exercises it. SERIAL AFTER unit 2 — both edit
   `sos_runner.py:74`.
5. **The extraction.** `filter-repo --path sos/` into `../sawos` (D-d), minus
   imgformat. sawos gains `Makefile` (`sos-test`), `CLAUDE.md`, `.gitignore`,
   `.github/workflows/ci.yml`, `sawlang.pin`, and `tools/` (sos_runner +
   resolver + the FORKED generic engine). sawlang deletes `sos/`, its
   `sos-test` target, its `sos` battery stage, its `sos` CI job, and resolves
   crossing 4 — `framesizes.py`'s `sos` group either travels to sawos or is
   deleted here, never left pointing at nothing. sawlang now references sawos
   nowhere. Acceptance: the unit-0 transcript reproduced from sawos, both
   arches, once per resolution path AVAILABLE at that time — env override,
   `SAWLANG_ROOT` and cold fetch always; `$PATH` only if unit 3 landed rather
   than exiting (D-b1), and its absence is recorded in the landing note rather
   than silently skipped.
6. **sawos CI and the negative tests.** sawos's own workflow, pinned. Two
   deliberate negatives: no sawlang reachable at all fails with the D-b
   refusal naming all three steps, not a confusing toolchain error; and —
   CONDITIONAL on unit 3 having landed — a
   `$PATH` sawc whose version disagrees with the pin fails loudly (D-b2).
7. **Docs and tracker.** `CLAUDE.md`'s sos digest, its gate paragraph (sos is
   no longer the compiler's freestanding gate — `freestanding` is), and its
   suite-lock paragraph narrowed to sawlang. `TESTING.md`, `README.md` per
   design 125, `designs/INDEX.md`. D-c decided. sawos gets its own CLAUDE.md
   carrying the sos half, with its own lock path.

## Gates

Units 0-4 gate on `battery.sh suite sos freestanding bootstrap` in sawlang —
ordinary in-repo work, with sos still present and still green. All four stages
are named because this brief's edits reach all four: `suite` and `sos` as
usual, `freestanding` because unit 1 creates it and units 2-4 must not rot it,
`bootstrap` because `blade_bootstrap.py` is crossing 3. Unit 5's gate is the
unit-0 transcript, diffed, both arches, from sawos. Unit 6 gates on sawos CI
plus its negatives. **No compiler change lands in this brief**: if the split
surfaces a sawc defect, the unit STOPS and files a DF (agent conduct — no
coding around a compiler defect), and this brief does not absorb the fix.

The suite lock stays sawlang's own (see the ruling above); sawos gets a
separate path. Nothing in either harness implements it, so this is a docs
change in both repos, not code.

## Explicitly OUT of scope

- **Blade's target-plugin mechanism** (the eventual home for `sosimg.saw`).
  Filed to [BACKLOG]; not a dependency of this split.
- **Any change to the sosimg format, the boot protocol, or the syscall
  surface.** The bytes are identical across this brief; the unit-0 transcript
  is the proof.
- **Any M3 work.** The ladder resumes in sawos after unit 6.
- **Broadening the freestanding suite past the inventory.** Unit 1 covers what
  `sos_runner` gates today, not every freestanding feature imaginable. New rows
  are welcome later, as their own filings.
- **`blade/src/sosimg.saw`, `blade/tests/sosimg_*`, and the sos fixtures.**
  They stay in sawlang. Blade keeps testing its own emitter.
- **`sawc/rt/ABI.md`'s `sos-hosted` runtime variant** and the freestanding
  flags. Compiler surface, compiler repo.

## Decisions agenda

- **D-a — imgformat placement.** RULED Aug 19: `libs/imgformat`, superseded
  later by the Blade plugin ([BACKLOG]).
- **D-b — resolver order.** RULED Aug 19: `$PATH` first, pinned fetch as
  fallback. Three sub-decisions OPEN, all detailed above — **D-b1** the missing
  install story (no `sawc`/`blade` binary exists to find), **D-b2** the
  unpinned-`$PATH` inversion (needs `sawc --version` + a pin check), **D-b3**
  fetch mechanics (SHA-keyed cache + bootstrap shape; reachability settled —
  the repo is public, so HTTPS, no credentials).
- **D-c — the sos design briefs.** `designs/78, 79, 112, 140, 162, 172, 178,
  232` and the sos DF entries. Move to sawos / copy / stay archival in sawlang?
  Design numbering is repo-global and heavily cross-cited, so moving fragments
  the sequence; copying duplicates a record the tracker flow says must never be
  duplicated. Lead's suggestion: they STAY (archival, cited by number), sawos
  opens its own numbering at 1 and cites sawlang briefs as `sawlang#NNN`.
- **D-d — history.** `filter-repo --path sos/` (partial fidelity, per the
  sweep) versus a squashed import with a pointer to the sawlang SHA. filter-repo
  recommended: partial history beats none, and the dropped context is
  reconstructible from sawlang, which is not going away.
- **D-e — sawos layout.** Keep the `sos/` prefix inside sawos, or flatten
  (`sawos/kernel/`, `sawos/hal/`, …). Flattening rewrites every internal
  `../..` path dep and every doc cross-reference; keeping the prefix costs one
  redundant directory level. Recommendation: keep `sos/` for the split, flatten
  later if ever, as its own commit.
- **D-f — the saw-lang skill.** sawos writes Saw and needs the idiom skill.
  Copy into sawos's `.claude/skills/` (drifts) or leave the sawos worktree
  pointing at sawlang's (requires the checkout). Open.

## Aug-28 rulings (user) — the reorder to queue head

1. **THE ORDERING SUPERSESSION: 238 now runs BEFORE 218 unit 1.5**,
   reversing the Aug-24 ruling. Rationale (user): the goal of the split is
   INDEPENDENT DEVELOPMENT — sawos has seen little recent work and the user
   wants back to it; and "now that we have the baremetal tests" (unit 1's
   freestanding suite, landed, plus the Aug-21 gate amendment making it the
   compiler's cross-target gate) "the sos tests are less important for
   218/1.5". Accepted consequence: sawos's initial pin predates 1.5 and its
   first rigorous pin bump (D-b2 standing practice) lands after 1.5 does.
2. **The sos riders batch is SCHEDULED**, directly after the currently
   running agents (designs 252/253) and before unit 2 — the brief's
   settled-tree precondition holds.
3. **D-c RATIFIED** as recommended: sos design briefs STAY in sawlang
   (archival, cited by number); sawos opens its own numbering at 1 and
   cites `sawlang#NNN`. **D-d RATIFIED**: `filter-repo`, partial history.
4. **D-e RULED THE OTHER WAY: FLATTEN.** Everything under `sos/` moves to
   the ROOT of the new repo (`kernel/`, `hal/`, `rt/`, `root/`, `tests/`,
   …) — no `sos/` prefix survives. Unit 5's scope therefore INCLUDES the
   rewrite the agenda priced: every internal `../..` path dep (the
   re-sweep's 20 sysapi + 21 sosrt consumers lose one level), the ld/board
   paths, the Makefile, sos_runner's resolver-fed paths, and the doc
   cross-references — rewritten IN the extraction, verified against the
   unit-0 transcript like everything else.
5. **D-f RULED: the skill goes GLOBAL on this machine.** A user-level
   symlink `~/.claude/skills/saw-lang ->
   <sawlang>/.claude/skills/saw-lang` (created Aug 28) makes the ONE
   in-repo, version-controlled copy visible to every local project incl.
   sawos — zero drift. sawos's CLAUDE.md documents the dependency (the
   symlink dangles without a sawlang checkout; a machine without one
   re-creates it or copies the skill).
6. **Design 245 v1 (Scalar) WAITS until after the split** (user: sos does
   not depend on string/character manipulation). Its Aug-27 dispatch never
   landed and is presumed stale; re-dispatch is rescheduled post-238.

## Scheduling

AFTER queue item 7 (the sos riders batch — `clock_get` `type:`, abi enum
shifts, kcore re-narrowing), which are small in-tree edits already queued behind
DF-232f. Landing them first means the split moves a settled tree rather than
one with known-pending edits, and it keeps those riders on the simple in-repo
flow. BEFORE the M3 ladder, per the user.

Note the standing rule that SOS-side branches PARK for user review before
merging. This brief is SOS-side in blast radius but most of it is sawlang work:
units 1-4 and 7 follow the normal compiler-brief flow; units 5-6's sawos side
parks.

SERIALIZATION. Units 1, 2 and 4 all edit `tools/sos_runner.py` in overlapping
regions — unit 1 extracts its generic half, unit 2 rewrites `IMGFORMAT_DIR`
(line 74), unit 4 converts that same constant and four others into resolver
calls — so those three are STRICTLY SERIAL in that order. Only unit 3 (the
install story: `bin/` shims, `--version`, no `sos_runner.py` contact) is
genuinely parallelizable, and it may run in a worktree beside any of them.
Everything from unit 5 on is serial. (The first draft claimed 1, 2 and 3-4
were mutually independent; that was wrong on the file overlap.)
