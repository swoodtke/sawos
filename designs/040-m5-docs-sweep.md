# SawOS design 40 — M5 unit 8: the docs sweep (M5 CLOSES)

**Status: BUILT Sep 5 2026 — brief-cum-As-built in one file, the design-24
precedent, because the PILE IS THE BRIEF.** Dispatched under `designs/025`
(RULED): unit 8, "docs sweep, M5 closes", the design-11/24 tradition. Base
`53382d1` (the K1-backlog commit after design 39's integration). Gate
**382/382 = 129 + 129 + 124** across riscv32 / arm64 / riscv32-flat.

**THIS UNIT HOLDS ROW AUTHORIZATION.** M5 queued row-moving work here for
three units running (design 35 §5 and §8.3, design 33's findings 5/6, the
tracker's unit-8 pile), so transcript rows MAY move — and every moved row is
enumerated in §2 with its cause. The diff is the deliverable, not the word
"green".

## The pile (from `designs/todo.md`, collected at the Sep-5 tracker sweep)

1. **spec.md** — §5.5 re-scope; §2.5 amendments; §5.9 / design 11 F2 CLOSE
   (built by unit 5); the §5b/§2 tier table (unit 4 built the word and the
   profile); design 36's located stale capacity prose; §5c's `support.c`
   reason count (three permanent reasons since design 38); and design 39
   §7's fourth item, §5.7's vDSO discipline qualified for C.
2. **The two user ABI.md files** — design 39 §7's other three items: the
   stale "three scripts" count, "Required of a process" grown from ONE
   bullet (the entry register) to FIVE — that bullet plus the four facts
   design 39 had to read out of `kernel/core/` — and the altitude table's
   typed-C row.
3. **Gate hygiene, the row-moving half** — promote `tier_word` to all three
   profiles; FIX the `thread_preempt` flake; correct CLAUDE.md's timing-rows
   paragraph in the same pass.
4. **Code comments** — `ThreadState`'s stale pre-M3 generations comment;
   design 16's "recorded future shape" paragraph.
5. **CLOSE THE MILESTONE** — this file, M5's closing note in `designs/025`,
   and the tracker entries closed in place.
6. **CANDIDATE, the unit's own scope call** — design 33 findings 5/6, the
   ten `sos_test_pool_base()` packages onto `Mapping.base()`. **DECLINED and
   re-filed; see §7.**

## What this unit does NOT do

No kernel behaviour changes. Comments, docs, spec text, and harness
hygiene only — the flake fix changes what the harness MATCHES, not what the
kernel prints, and the `tier_word` promotion adds runs of an existing case.
Two `static_assert` re-pointings are taken (§6); a `static_assert` is
compile-time and no instruction moves for one. No op, right, kind, status,
slab, quota or seam is touched.

---

# As-built (agent, Sep 5 2026)

## 1. THE M5-CLOSES STATEMENT

**M5 IS COMPLETE AT THIS UNIT'S INTEGRATION.** Every rung of `designs/025`'s
ladder as ruled and as amended has landed and is recorded:

| rung | design | what it was |
|---|---|---|
| unit 1 | `027` | arm64 translation-as-isolation — per-process persistent table sets, ASIDs, identity VAs |
| unit 1.5 | `029` | the higher-half kernel + the `phys_to_virt`/`virt_to_phys` linmap seam |
| unit 2 | `033` | PLACEMENT — on arm64 the address is the kernel's answer; one link base; `Mapping.base()` |
| unit 3 | — | riscv32 Sv32: **PUNTED TO BACKLOG** (design 25 ruling 11). The rung vacates; the numbering stands |
| unit 4 | `035` | the tier word (`SystemOp.TierGet`, `Isolated`/`Flat`) + the flat BUILD PROFILE as a third gate run |
| unit 5 | `028` | the real allocator — pool returns to root, coalescing, byte accounting; §5.9 answered |
| unit 6 | `032` | slab donation — `SystemOp.SlabDonate`, the extent-chain slab |
| unit 6a | `034` | the satellite slabs — threads and pipes donate |
| unit 6b | `036` | processes donate — the slot absorbs its tables; the domain-pool wall priced |
| unit 7 | `037` | the shared stats region — a userspace `top` that costs no syscalls |
| unit 8 | `040` | this file |

Three riders touched no ladder rung and are recorded with it: the `sosabi`
module split (`026`, housekeeping, byte-identical), the arena + kernel-stack
sizing unit (`038`, seed 4, slotted at convenience), and the C-leg probe
(`039`, user-ruled Sep 4 — one freestanding C process end to end, which
found four of this sweep's doc items by being the first program that needed
them). Seven pin bumps ran across the milestone, the last taking sawlang to
**0.8.0 @ `449d2485`** with `-Oz` on all freestanding builds.

**The gate went 238 → 382 across M5**, and its SHAPE changed once: unit 4
made it three profiles rather than two architectures, which is design 19's
"one story, one test" executed. The lead moves on this statement at
integration; M6's anchor is `designs/030-m6-storage-seed.md` and nothing in
this sweep touches it beyond that pointer.

## 2. GATE EVIDENCE, AND THE ENUMERATED ROW DIFF

| | baseline (`53382d1`, re-run here) | after |
|---|---|---|
| totals | **380 = 128 + 128 + 124** | **382 = 129 + 129 + 124** |
| transcript | 808 lines, `fc247c62…` | 812 lines, `6db3008f…` |
| header block | — | **IDENTICAL** |
| riscv32-flat section | — | **IDENTICAL** (totals line excluded — it is the report's, not the section's) |

The baseline was RE-RUN at the base commit in this worktree before a
character was edited, rather than taken on trust. **And the after-run was RUN
TWICE — once on the working tree and once on the four committed commits —
reproducing `6db3008f…` byte for byte.** That second run is worth its line
here for one reason beyond diligence: `thread_preempt` is the case this unit
repaired, and a flake fix is worth exactly as much as the number of times the
case has been observed green since. Three full-gate runs in this worktree,
three passes.

**EVERY MOVED ROW, CLASSIFIED. There are four classes and nothing else.**

| class | riscv32 | arm64 | riscv32-flat | cause |
|---|---|---|---|---|
| case rows whose **DENOMINATOR** moved `[i/128]` → `[i/129]`, payload identical | 128 | 128 | 0 | `tier_word` joins the two isolated profiles' case lists |
| …of which the **INDEX** also moved +1 | 4 | 4 | 0 | `tier_word` is not last in the table: `stats_region` 125→126, `stats_region_ro` 126→127, `arena_small` 127→128, `c_hello` 128→129 |
| **NEW case row** `[125/129] ✓ tier_word` | 1 | 1 | 0 | the promotion itself |
| **NEW `.sosimg` size row** for `tests/tier-word` | 1 (10,296 B) | 1 (12,360 B) | 0 | the case's package now builds for those two triples |
| existing size row whose **VALUE** moved | **0** | **0** | **0** | — |
| the totals line | — | — | — | `380 passed` → `382 passed`, one pair |
| **anything else** | **0** | **0** | **0** | — |

Three things that half of that table is there to prove:

- **NOT ONE `.sosimg` VALUE MOVED, on any profile.** The unit added no
  `@export`, no `static`, no declaration and no code — design 35 §6 and
  design 33 finding 3 both measured what an added exported symbol costs
  every image on the 4-byte-granular profile, and this sweep pays none of
  it. The two new size rows are the tier-word package appearing where it
  did not build before, not an existing image changing size.
- **THE FLAT SECTION IS BYTE-IDENTICAL**, which is the fence the other
  direction: promoting a case to two profiles must not disturb the third,
  and the case's own ordinal there (`[121/124]`) is where design 35 left it.
- **THE FLAKE FIX MOVED NOTHING, and could not have.** The report prints
  case rows, image sizes and totals; a guest console line reaches the screen
  only when a case goes red. So a change to what the harness MATCHES is
  invisible in a green transcript by construction — which is why it is in
  this unit's diff and not in its row table.

## 3. THE GATE-HYGIENE HALF

### 3a. `tier_word` is promoted, and what that buys

One key deleted (`"arches": ["riscv32-flat"]`). Design 35 §5 recorded the
case as flat-only *purely* as a transcript fence and witnessed the Isolated
answer with a probe run once in a worktree; it is a gate row on all three
profiles now, run every time. `{tier}` substitutes `Isolated` on the two
protected profiles and `Flat` on the third, so ONE case checks the runner's
tier table against the HAL that implements it, everywhere.

The third assertion is the one worth having on the isolated profiles: a
System sibling minted WITHOUT `TierGet` faults, because **rights are not
tiered**. On the flat profile that is the positive statement of what the
tier word does not disclaim; on the other two it is the same statement about
a machine that can also deny. Same row, same reason, three machines.

The four shifted ordinals are the cost of leaving the case where its own
unit put it rather than moving it to the end of the table. Paid once,
enumerated above.

### 3b. The `thread_preempt` flake — AND IT HAD TWO CAUSES, NOT ONE

Design 35 §7a diagnosed one cause and prescribed one fix. **The prescribed
fix alone does not fix the run design 35 recorded**, and finding that out is
this unit's sharpest catch.

The failing interleave was `A×7, B, A, B×7` — sixteen letters, exactly three
crossings, exactly what the case means to prove. The case asserts `"AB"`,
`"BA"`, `"AB"` as ordered substrings.

1. **The kernel's narration splits the letters.** `SOS: timer tick …` lines
   go into the same serial stream the workers write letters into, and one
   landed between the `B` and the `A`, so no adjacent `"BA"` existed. That
   is design 35's diagnosis and it is correct.
2. **AND THE ORDERED MATCHER CANNOT SEE THREE CONSECUTIVE CROSSINGS.**
   Strip the narration and the projection is `AAAAAAABABBBBBBB`; the
   crossings are at offsets 6, 7 and 8 and they OVERLAP. `_check` advances
   `cursor = at + len(want)` (design 158's ordered matching), so finding
   `"AB"` at 6 puts the cursor at 8 and steps straight over the `"BA"` at 7.
   The case's real claim was therefore "five-ish crossings, arranged so as
   not to overlap" rather than the three it says, and its minimum was
   unmatchable.

Both halves are fixed, as two independent per-case keys, each documented at
the helper and at the case:

- **`strip_kernel_lines`** — match against the LETTERS-ONLY PROJECTION,
  `SOS: [^\r\n]*(?:\r?\n)?` removed. The removal is exact rather than
  approximate: the kernel writes a diagnostic from inside a trap handler,
  start to newline, with no preemption point in it, so `SOS: …\n` always
  appears WHOLE and never has a user byte inside it — it is the USER's
  stream that gets split, which is the thing being repaired. Nothing this
  case expects begins `SOS: `, which is the whole rule for using the key.
- **`overlapping_matches`** — the cursor advances by ONE character, which is
  what "three DIRECTION CHANGES" means when it is written as adjacent pairs.
  The default stays non-overlapping and is right for every case asserting
  LINES; this is the only case in the table asserting adjacent pairs.

Verified against design 35's recorded transcript before the gate ran: raw
stream FAILS (the flake as it fired), strip-only FAILS (design 35's
prescription), strip + overlapping PASSES — and the negative control
`AAAAAAAABBBBBBBB`, a run with no preemption at all, still FAILS, which is
the claim's other end and the thing a looser matcher would have thrown away.

### 3c. CLAUDE.md's timing-rows paragraph, corrected

It listed `thread_preempt`'s A/B interleave among the rows "no assertion
reads". **An assertion does read it** — that is design 35 §7a's correction
of this document, and the paragraph now says so, names the two keys, and
lists the genuinely unasserted things: the tick COUNT, `thread_preempt`'s
and `process_stats`' `interrupts=` columns, and `timer_interval`'s `fires=`
counters. One further precision, because it changes how the sentence should
be read: **none of them appears in the transcript at all on a passing run.**
The report prints case rows, image sizes and totals; the console reaches the
screen only on a red row. They are timing-dependent CONSOLE OUTPUT, not
timing-dependent transcript rows, and "anything moving in a transcript diff
is a real finding" is therefore unconditional.

## 4. spec.md, section by section

**§5.5 — RE-SCOPED (+31 lines).** The item the whole document cites when it
says "§5.5" is §5's item 5 (P4 memory-hardware realities), and its reach was
what changed rather than its content: *"an address is the same number in
every process"* was written there as a fact about SOS and is now a fact
about a TIER. Three bullets, in the order a reader needs them — tier 1's
address is the kernel's answer (`hal.USER_IMAGE_BASE`, `map` places, no VA
hint on any tier); tiers 2 and 3 keep identity and therefore keep a linker
script per resident image; and the kernel above the seam never learned which
it is on, because a `GrantRow` carries both halves and the MPU tier asserts
them equal. Addresses-as-DATA stay physical everywhere they are stored.

**§5.9 — CLOSED, AND BUILT RATHER THAN RE-WORDED A THIRD TIME (+38 lines).**
The sub-detail open since M1 and restated at M3's and M4's closes. Four
answers, each traced to where it landed: the count lives on the region's own
slab slot and there are TWO of them (`refs` + `maps`, release at the second
zero — §2.5's quoted invariant executing as written); there is NO pool-side
region descriptor, because a pool is not a fourth object kind and every
region carries a `root` back-reference instead; byte accounting IS that
ledger (`out_bytes` beside `pool_free_bytes`), the pool's business and not a
per-process authority, with quotas staying object-counted; and `IoMemory` is
untouched, which is this item's own carve-out discharged where the mechanism
lives. **Design 11's F2 nod closes with it** — the pin had "a caller, a
neighbouring mechanism, and still no home", and it has a home.

**§11's pin bullet — RE-HEADED (+10/−9).** "Orchestrator pins STILL OPEN,
restated at M3's close" was a milestone behind at M4's close (design 24's F2,
which deliberately left it alone because the pin's own wording was the parked
question's subject). The pin is answered, so the header is now "ONE STILL
OPEN after M5, and it is no longer the memory one" — what remains under it is
the §7 priority map plus root's bootstrap band map, unchanged.

**§2.5 — THREE AMENDMENTS (+34 lines).** (a) The quoted refcount clause is
now EXECUTED, word for word, with the `maps`-counts-ROWS-not-OBJECTS
reasoning at the site and return-to-root, coalescing, first fit and the
infallible-release `dropped` counter beside it. (b) *"at multiple virtual
locations"* — the Jul-29 clause narrowed for four milestones — is TRUE AS
WRITTEN on the tier that translates, and the narrowing survives as the other
tiers' answer; what is portable across the split is that a mapping's address
is the kernel's answer and is treated as data. (c) The
dropped-without-unmap stance changed its SUBJECT and not its truth: the ROW
is still leaked to the target's teardown, the BYTES are not.

**§2 table — FOUR ROWS (+2,900 chars).** `MemoryObject`: the range comes
back since unit 5. `Mapping`: it had "no virtual half" for four milestones
and a grant row carries both halves now; TWO ops, `Base` beside `Unmap`, on
a separate right because locating a row and revoking one are different
authorities. `Process`: the three stale capacity clauses design 36 located
(the `MAX_PROCESSES × MAX_HANDLES` scan bound, "bounds CONCURRENT processes
again", and "every per-process table was already indexed"), each corrected in
place, plus a new paragraph making `MAX_PROCESSES` a FLOOR with
`process_slots_usable()` = `min(capacity, hal.PROT_DOMAIN_SLOTS)` the running
bound and the domain-pool wall stated as the honest half. `System`: the three
M5 ops it grew — `SlabDonate`, `TierGet`, `StatsRegionMap` — each on its own
right, each here because this is the only machine-wide object; "Later
candidates: info queries" is what `TierGet` turned out to be.

**§2's `Process` row, design 16's "recorded future shape" — BUILT.** The
paragraph described a shared read-only counter region as a thing parked
beside the M5 growable-pool seed; unit 7 built it, and the paragraph now says
what is true: `SystemOp.StatsRegionMap` on its own right, the op-shaped v1
SURVIVING unchanged (which is what the sentence was claiming), and the three
things a reader of the region must know — no `Memory` handle is minted,
`rows` is a capacity and not a census, and **a row index is not a name**.
That last is design 37's recorded follow-on, carried here rather than left in
an As-built: a general `top` wants either a per-row identity column or an op
resolving a Process handle to its row, and nothing in M5 consumed it.

**§5b.1 — NEW SUBSECTION (+52 lines): the protection tier and the word the
platform advertises.** Two tables — tier × (what the hardware does, the
address story, the word) and target × (tier, in the gate?, notes) — carrying
design 25's tier table updated to what is BUILT, with the flat build profile
as a gate run rather than a proposal. Then the three rulings the tables need:
TWO WORDS and not three (MMU-vs-MPU is not advertised, because a third word
would invite tier-sniffing); the word disclaims DENIAL and the kernel
boundary is not tiered anywhere, which the gate demonstrates by running the
object-model suite unchanged on all three and excluding exactly four cases
BY NAME; and the tier-2 floor, shape ratified and number deferred.

**§5c — THE THIRD REASON (+22 lines).** `support.c` was "reason 2, and reason
2 only"; design 38 made a package's arena size a linker-script knob and
`sosrt` has to read the bounds back, so `sos_arena_base`/`sos_arena_size` are
**reason 3** — the same wall `sink.c` meets for `.payload`, answered the same
way. Reason 3's count is corrected with it: TEN accessor bodies across three
files, not "four, two per profile" (four per kernel profile since the region
table, plus the arena pair). The closing sentence is corrected from "every one
of them is reason 1 or reason 2" and keeps its real point: reason 3 is still
the only open LANGUAGE gap.

**§5.7 — THE vDSO DISCIPLINE, QUALIFIED (+24 lines).** Design 39 §7's fourth
item. The discipline holds for Saw and does not yet hold for C, and the
reason is structural: the typed-C altitude is real and linked, but its
symbols are COMPILED FROM the `sos` Saw module, which depends on `sosrt` — so
reaching them makes an image a Saw image with a C `main` in it. The probe is
the first genuinely non-Saw process this tree has had and it could not use
the row written for it; it hardcoded three op numbers out of KERNEL-INTERNAL
`kernel/abi/`. **So for a C image an op number IS ABI today.** Nothing is
withdrawn — the claim is scoped to the altitude that can honour it, and what
is missing is named (design 31 part 1: an `sos.h` this tree owns, and a way
to link the floor without the runtime behind it).

**Two more, from the consistency grep** (design 11's method): §8's
`alloc_process` pend and §11's M4-recap "one number (`MAX_PROCESSES` = 3)",
both design 36's located sites, corrected to floor-not-ceiling; and §2.2's
`MAX_ATTACHMENTS` bound, which gains the one-clause rider `kcore.limits`
already carries (`Attachments` is a donatable kind, so that number is a floor
too).

## 5. The two user ABI.md files

**The "three scripts" count — SIX SITES, not four.** Design 39 §7 named
three in `hal/riscv32/user/ABI.md` plus the arm64 file's tier-split
paragraph; the grep found **two more** the probe had not looked at —
`hal/riscv32/kernel/ABI.md`'s `image_link_base` row and
`hal/riscv32-common/kernel/lib.saw`'s identity-costs comment — both listing
`root/child/child2`. All six corrected, and each now states the SHAPE rather
than the number, because the number is what went stale: **on a tier that does
not translate, a linker script per resident image; on the translating tier,
one script whatever the process count.** Design 36 needed a third child alive
at once and that is why `child3.ld` exists.

**"Required of a process" — one bullet becomes five, on both profiles.** The
section a crt0 author reads was the entry register and nothing else, so
design 39's probe read the other four facts out of `kernel/core/dispatch.saw`,
`kernel/core/loader.saw` and spec §8 instead. Written down now, each verified
against the code rather than against the probe's table: the STACK POINTER is
preloaded with the region's `link_top()` (`frame_init`); `.bss` is already
zero (the loader zero-fills each segment's `mem_len` tail past `file_len`);
`.data` is already in place (segments copied to their LINK addresses, nothing
to relocate); and **the link register is ZERO, deliberately** — `frame_init`
zeroes the whole trap frame before writing its four fields, so falling off the
end of `_start` FAULTS and a failed bootstrap reads `Faulted` in the
launcher's `get_status` instead of hanging until a case times out. Four of the
five are things a crt0 here does NOT have to write, which is the point of
saying them; the fifth is the way out, which is the process's own job.

**The typed-C altitude row — the catch recorded where a reader meets it.** The
riscv32 file carries the whole finding (compiled from a Saw module, `sosrt`
behind it, ~8 KiB of runtime and a 64 KiB arena for three calls, what a real C
program does instead, and that design 31 part 1's deliverable is a header and
a linkable object rather than more `@export`s); the arm64 file states it in
three sentences and points there, which is how those two documents already
divide every other shared fact. DF-172i's note is kept and extended rather
than replaced.

## 6. Code comments, and an ABI assert audit that found two holes

**`ThreadState`'s docstring** ended *"…which is also why handles carry no
generation yet"*, which has been false since M3: handle words have carried an
index/generation split since design 3 D-1. Rewritten to say what is true —
the slot returns at process teardown and at no earlier moment, `end_process`
holds the one loop that writes `Free`, a Thread carries no reference count ON
PURPOSE (design 7 D-1), and `RELEASE_OP` destroys a Thread HANDLE and never a
thread — with the retired sentence quoted so the correction is legible.

**AND THE AUDIT FOUND TWO STALE ABI ASSERTS, both design 33's, both the
species design 35 §7 fixed one unit earlier.** `kernel/abi/src/ops.saw`
states the rule in as many words — *"one assert per table, ON ITS HIGHEST
CASE"* — and carries a NOTE, added by design 35, saying re-pointing it is a
one-line duty its own existence makes unmissable. It was missed anyway, three
units later, in the same milestone:

- `static_assert((MappingOp.Unmap as UInt) < MINT_OP)` tested case **0**
  while the table's top was `Base` (**1**). A bound tested against a case
  that is not the highest is a check that would pass while the table walked
  past `MINT_OP`. Re-pointed at `Base`.
- `MappingRight.Base` (`1 << 9`) had **no `>= (1 << 8)` assert**, though the
  rule beside it is one per kind-specific case. Backfilled — the repetition
  IS the check, and a hole in it checks nothing.

Both are compile-time and neither changes an instruction. The audit was
mechanical rather than by eye (a script over every `*Op` and `*Right` enum
against its asserts), which is why it caught the second one; every other
table and every other kind-specific bit is correct.

**`hal/riscv32-common/kernel/lib.saw`**'s identity-costs comment gained
`child3.ld` and the shape-not-number rewording (§5).

**README.md** — three catches, design 24's README pass repeated: the opener
said the system runs on two architectures where the gate is three PROFILES
(and the tier story is now the paragraph a reader arrives on); the gate line
said "116 cases per architecture, 232 runs", the M4 number; and the layout
list had no `hal/riscv32-flat/`. **CLAUDE.md**'s repo map had the same gap
and its testing block the same stale shape; both fixed in the flake-fix pass.

## 7. THE `pool_base` CANDIDATE: DECLINED, and re-filed by name

Design 33 findings 5 and 6 — migrate the packages reading
`sos_test_pool_base()` onto `Mapping.base()`, and fix `map_basics`' header —
were left to "the unit's own scope". **DECLINED**, for four reasons, of which
the second is the one that decided it. (The count has drifted, which is worth
one line: design 33 said TEN packages and there are **ELEVEN** callers today
— `slab-donate-mapped` joined during the donation track. `map-placed` names
the function in a comment only, and is the one case already written against
`Mapping.base()`.)

1. **It is a code migration, not a docs or harness edit.** This unit's own
   constraint is comments, docs, spec text and harness hygiene; rewriting how
   eleven programs learn an address is program behaviour, and it belongs in
   a unit whose brief says so.
2. **IT IS NOT A MECHANICAL SUBSTITUTION, and the C file's own comment
   understates that.** Several sites read the base where no `Mapping` exists
   to ask: `map-wx-refused` needs the address to attempt a refused map,
   `child-touch` pokes an address in a CHILD that holds no Mapping at all,
   and `memory-recycle` does arithmetic with the pool LENGTH (which
   `Mapping.base()` has no twin for). Each of those needs its own answer, and
   two of them need a new way to learn an address. That is design work.
3. **It would spoil this unit's diff.** ~24 image-size rows across three
   profiles would move in the same transcript as the `tier_word` promotion's
   denominator change, and the enumeration in §2 — which is what this unit
   exists to produce — would stop being readable as two causes.
4. **The authorization argument is answerable.** The rows it needs are
   riscv32 and flat image-size rows, and ANY unit that edits a shared test
   package moves those; it does not need a rare standing authorization, only
   its own brief naming them. So declining costs the migration a wait, not a
   window.

Both are re-filed to `[BACKLOG]` in `designs/todo.md`, together, as one entry
naming the shape of the work.

## 8. Findings for the lead

1. **DESIGN 35's PRESCRIBED FLAKE FIX WAS INSUFFICIENT, and the second cause
   is a property of the matcher rather than of the kernel** (§3b). The A/B
   interleave's stated minimum — three crossings — was unmatchable by design
   158's ordered non-overlapping scan whenever those crossings were
   consecutive, which is exactly the shape the failing run had. Worth the
   lead's attention because it is a class: **any case asserting ADJACENT
   PAIRS rather than lines has the same latent gap**, and `thread_preempt` is
   the only one in the table today.
2. **TWO STALE ABI ASSERTS, from design 33, of the species design 35 fixed
   one unit earlier and left a note about** (§6). The note did not prevent
   the recurrence; a mechanical audit did. If the lead wants that permanent,
   it is a runner lint (`every *Op enum's assert names its highest case`)
   rather than a comment, and it would be small — recorded, not built,
   because a new gate check is not a docs sweep's to add.
3. **`hal/riscv32/kernel/ABI.md` and `hal/riscv32-common/kernel/lib.saw`
   carried the same stale script count design 39 found in the user ABI.md**
   (§5). The probe looked where it worked; the sweep grepped. Nothing else
   in `hal/` or `kernel/` names the old count.
4. **spec.md's §11 pin header is current for the first time since M3** —
   design 24's F2 flagged it deliberately and left it, because the pin's
   wording was the parked question's own subject. The pin is answered, so
   re-heading it costs nothing now.
5. **The `pool_base` migration is DECLINED and re-filed** (§7), with the
   argument that it never needed this unit's authorization in the first
   place.
6. **Design 37's row-identity follow-on is now IN THE SPEC**, not only in an
   As-built: §2's `Process` row states that a row index is not a name and
   what a general `top` would need. Recorded, not built.

## 9. SL entries

**None owed.** This unit wrote prose and two `static_assert` operands. The
only `.saw` edits are inside existing `///` and `//` blocks with their
documented declarations unmoved, plus two assert lines; nothing about the
language bit, and no construct was reached for that did not exist. The
highest entry remains **SL-27**.

## 10. The independent no-motion proof (design 11's tradition)

`git diff` over the COMPILED sources under `kernel/`, `hal/` and `rt/` (the
`.saw`, `.c`, `.S` and `.ld` files — four files touched in all) filtered to
non-comment lines is **FOUR LINES**: the re-pointed `MappingOp` assert, and
the backfilled `MappingRight.Base` assert with its message. Both are
compile-time, and both point a bound at the case the file's own stated rule
says it should name. Every other changed line in a compiled file is a `//` or
`///` line, and every doc block still documents the declaration it documented
before, so
design 121's placement rules were never in play. That is the second,
independent reason nothing in the transcript could have moved for anything in
§4, §5 or §6 — and the four classes in §2 are all attributable to the one
runner key that was deleted.
