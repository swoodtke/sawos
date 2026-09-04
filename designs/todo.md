# SawOS — Open Work Tracker

OPEN WORK ONLY, plus the two pointer sections directly below. Landed
work lives in `designs/NNN-*.md` + done files + git history.
Conventions (inherited from sawlang's tracker, ruled Aug 18): cite
source designs in [brackets] — sawlang briefs as `sawlang#NNN` (copies
in `designs/sawlang/`), sawos-native briefs as `#N` starting at 1;
VERIFY items need a probe before being treated as real work.

TRACKER FLOW. An IMPLEMENTING AGENT closes its entry IN PLACE here —
the status line plus what landed — and never touches a done file. The
LEAD does the moving, at INTEGRATION and only after review/approval: an
entry with nothing open left inside it is cut VERBATIM into the current
week's done file, in the same pass that accepts the work. An entry with
ANY open item stays here WHOLE — a partially closed entry is never
split. Done files are VERBATIM moves, never rewritten; the first done
file starts when the first entry closes.

[QUEUE] and [BACKLOG] are POINTERS ONLY — one line per item, naming the
entry below or the brief that carries it, never restating either.

## [QUEUE] — scheduled, in order (user-approved)

- M5: the memory milestone — `designs/025` (RULED Sep 3, the plan of
  record; amended same day: unit 1.5 higher-half+linmap ruled IN,
  unit 3 Sv32 punted to backlog): tier climbs 1→1.5→2→4 in order;
  allocator track 5→6→6b arch-free, may pipeline in a parallel
  worktree; stats region 7; docs sweep 8 closes; arena+kernel-stack
  unit slots at convenience. Unit briefs dispatch from the sketch
  (027 unit 1 MERGED; 028 unit 5 MERGED; 029 unit 1.5 + 032 unit 6
  next to dispatch).
  **UNIT 1 BUILT (`designs/027`, Sep 3):** arm64 translation-as-isolation
  — per-process persistent table sets from a static HAL pool
  (`PROT_DOMAIN_SLOTS` 3 × 24 KiB, `.bss` +48 KiB measured), the switch
  collapsed from ~1.6k descriptor stores to one TTBR0+ASID write, ASID =
  process slot, the seam grown by THREE names (`prot_switch`,
  `prot_update`, `prot_clear`) beside the unchanged
  `PROT_REPLAY_AT_SWITCH` and MPU replay quartet, hung on the record's
  own funnels (`record_grant`/`remove_grant`/`clear_domain`). **The seam
  was USER-RULED from five names to three before merge (Sep 3):**
  `prot_update` absorbs the install/remove pair with `perms == 0`
  meaning revoke — a real domain value, not a mode flag, and safe
  because BOTH grant doors already refuse a permitting-nothing row;
  `prot_clear` stays separate as domain RETIREMENT (the fail-closed
  whole-set mask, and the one lifecycle anchor for ASID-reuse
  invalidation). Collapsing the replay quartet too was considered and
  DECLINED (it touches the staged publish contract for no behavioral
  gain). Gate 232/232 both arches and RE-GATED 232/232 after the rework,
  transcript BYTE-IDENTICAL both times and hashing to the same
  `3f6dde15` design 24 recorded. riscv32 unchanged (two no-op bodies).
  No SL entry owed. **REBASED onto design 28 (`8f080c3`) and re-gated:
  117 cases, 234/234, transcript hashing to `d37d2db` — the same hash
  028's As-built records for main's own run, so the combined state adds
  no rows.** The contact zone (`record_grant`/`remove_grant`/
  `clear_domain`) resolved on ORDER: the row is read once and both
  consumers share that read, and the hardware revoke precedes
  `unref_region_row` because 028's "stopped granting before the range
  can be freed" argument rests on the row leaving the RECORD, which
  stops nothing once the tables persist. INTEGRATED (lead, Sep 3, `215ba09`,
  after design 28's `8f080c3`). Units 1.5→2→4 and 6→6b→7 are BUILT and
  **unit 8 (the docs sweep) is the only one still open**, so this entry stays
  whole; its As-built findings ride there, one a warning for
  unit 3 (the gate does not witness fault CLASS — see 027).
  **UNIT 6 BUILT (`designs/032`, Sep 3):** slab donation —
  `SystemOp.SlabDonate(kind, memory)` on its own `SystemRight.SlabDonate`
  (minted in the root set of necessity), consuming, permanent, with the static
  array as extent 0 so the kernel boots with no donation. NINE kinds converted
  (Events, Waiters, Interrupts, Attachments, Timers, Memories, IoMemories,
  Mappings, FreeRanges — design 28's named first customer) through ONE generic
  `Slab<T, const N: Int>` in the new `kcore.slab`; the kind vocabulary is a
  dedicated `SlabKind` in `sosabi.ops` rather than `ObjType`, which is the wrong
  set in both directions (it holds undonatable kinds and MISSES the free-range
  nodes, which have no `ObjType` at all). **THE `borrows` ACCESSOR IS WHY THIS
  WAS SMALL: `EVENTS[i]` keeps its exact spelling, so ~370 access sites did not
  move** and the whole risk sat in the TWELVE loop bounds that had to become
  `capacity()`. **THREE KINDS EXCLUDED BY NAME, each with its reason** — PIPES
  (ten satellite arrays on a DERIVED index), THREADS (one satellite, the frame
  arena; excluded on RISK to the context-switch path and named as the cheapest
  next increment), CLOCKS (not a slab). Processes stay unit 6b. Gate: baseline
  234/234 at `b613c5a` hashing to the same `d37d2db` main records, then
  **238/238, 119 cases**; every pre-existing case line BYTE-IDENTICAL both
  arches and the normalised transcripts diffing to ZERO. **ONE unanticipated
  motion, reported not hidden: 119 riscv32 images grew a uniform +40 bytes**
  (arm64: 0) because the new floor `@export` links into every image as all ~40
  of its neighbours do. TWO SL ENTRIES FILED (SL-21 struct-typed `static` refused
  as a repeat-literal value; SL-22 a `static` initializer cannot wrap after `=`).
  **THREE THINGS THE LEAD SHOULD SEE**: (a) design 25 D-6's "a handle word's
  index bits span the chain" is FALSE for this kernel — a handle names a
  per-process TABLE ROW and the row names the slot in a full `Int`, so
  `HANDLE_INDEX_BITS` bounds `MAX_HANDLES` and not slab capacity, and the brief's
  requested assert was replaced with two honest ones; (b) the brief's refusal
  word `BadState` is a `FaultReason` here, not a `SosStatus`, so the safety
  condition ENDS the caller — which matches the doctrine and is what the negative
  case asserts; (c) the `maps == 0` half of the condition is implemented but
  UNTESTED (it wants a live mapping, a bigger case), named as a finding.
  **UNIT 6a BUILT (`designs/034`, Sep 3):** the satellite slabs — `SlabKind` grows
  `Threads = 9` and `Pipes = 10`, so BOTH kinds unit 6 excluded now convert and the
  user's three motivating examples are down to processes alone (6b). **TWO KINDS,
  TWO SHAPES, and the difference is the substance.** PIPES: the ten satellite
  arrays become the FIELDS of one `Exchange` record, and `EXCHANGES` is then a
  plain chain in lockstep with `PIPES` — lockstep proven by ARITHMETIC rather than
  by a shared table, since `x = c * PIPE_INFLIGHT + i` is uniform across the chain,
  so extent `e` of one holds exactly the exchanges extent `e` of the other maps to.
  Every access site is a mechanical rename on the SAME flat index (82 of them), so
  the rendezvous paths keep their ONE bounds check and gain no division — the
  fatten-`PipeSlot` alternative was declined for exactly that. THREADS: the frame
  arena cannot become a field, because one HAL's user-return loads the frame
  address INTO THE STACK POINTER (`mov sp, x0`) so a frame must carry its 16-byte
  alignment, and a fattened `ThreadSlot` is 592 on arm64 (fine) and 552 on riscv32
  (8 mod 16, misaligned ON THE PROFILE THAT DOES NOT CHECK) — so the extent table
  grew a COLUMN, `Slab.ext_side`, read through the same new `locate` walk the slot
  lookup uses, which is what makes "slot i's frame and slot i's storage cannot
  disagree" structural. **THE SWITCH PATH'S FLOOR ARM IS BYTE-FOR-BYTE THE OLD
  FUNCTION** behind one compare against a constant, which is why 238 pre-existing
  rows are byte-identical; the donated arm is a <=4-iteration walk with an O(1)
  body and design 1's verdict is unchanged. ONE NEW FUNNEL, named and justified
  (`frame_slot`'s `phys_to_virt`); `EXCHANGES` deliberately adds none, since an
  exchange is a slab slot and reaches donated memory through `extent_addr` like
  every other. **BOTH OF UNIT 6's GAPS CLOSED**: `slab_donate_mapped` (+ its
  `child-donor`) proves the `maps == 0` leg with the CHILD as donor — the refusal
  is a fault, so the donor dies and only a survivor can show the region was not
  absorbed, which root does by reading a witness byte back through the very
  mapping that caused the refusal; and `slab_donate_free_nodes` witnesses design
  28's leak path end to end. Gate: baseline 238/238 at `340e4c5` hashing to the
  same `801a0f98` the pin bump records, then **245/245**, and the normalised diff
  is FIVE HUNKS OF NEW ROWS — **not one pre-existing case line or IMAGE SIZE
  moved**, because this unit adds no floor `@export` and so design 32's +40 bytes
  per riscv32 image does not recur. TWO SL ENTRIES FILED (SL-23 `&var slab[i].field`
  is not addressable though the same field is inside a `&var self` method; SL-24 a
  bare literal does not adopt a platform `UInt` at a call argument).
  **THREE THINGS THE LEAD SHOULD SEE**: (a) **design 32's finding 6 named the wrong
  wall** — exhausting the free-range nodes is blocked by `MAX_HANDLES` (16) and by
  `pool_cut` being FIRST FIT, not by `MAX_MEMORIES`, so donating Memories alone
  would not have helped; what made the case reachable is that a DONATED region
  holds bytes out of a pool costing neither a handle nor a slot, which is the op
  under test supplying its own test fixture; (b) that case is **riscv32 ONLY** and
  the arithmetic is in the record — first-fit forces strictly growing cuts, the
  step is one `hal.PROT_GRAIN`, and at the other profile's 4096 the 32 holes alone
  want 2.1 MiB against a 256 KiB pool that cannot grow, so it is impossible there
  rather than merely unwritten; (c) the brief's expected facade motion did NOT
  happen and did not need to — `SlabKind` is a NAME already on both facade lines
  since unit 6, and a CASE is not a name.
  **REBASED ONTO 033 (Sep 4, 6a lands second).** One textual conflict, pure
  adjacency (both units append cases to `sos_runner.py`; 033's `map_placed` kept
  first so its ordinal holds); `dispatch.saw` auto-merged despite both units
  editing it. **THE REAL CONTACT WAS NOT A CONFLICT AT ALL AND IS WORTH THE
  LEAD'S ATTENTION**: 033 collapsed the arm64 user linker scripts into one
  `user.ld`, and this unit's FIVE NEW packages named the old `root.ld`/`child.ld`
  — git had nothing to conflict with, because those files are new on one side and
  untouched on the other, so the rebase was clean and the arm64 build then failed
  at the LINK step. A new file naming a removed one is invisible to a three-way
  merge; a clean rebase is not evidence a rebase is done. COMPOSITION CONFIRMED
  rather than assumed: this unit dereferences donated memory only through
  `hal.phys_to_virt` (029's KERNEL-side seam, untouched by 033, which split the
  USER side), `MEMORIES[].base` is still physical as the donate path needs, and
  `thread-donate` on arm64 — a thread whose FRAME is donated memory reached
  through the linmap, under an image 033 now places at frames — is the witness.
  Re-gate: main `4cb149c` 239/239 (120 cases) then **246/246 (123 cases)**, the
  same five hunks of new rows, and proven PER ARCH since the two units'
  authorizations differ by profile — with 6a's rows removed the riscv32 half is
  BYTE-IDENTICAL to main's and the arm64 half is too but for the total line.
  **UNIT 6b BUILT (`designs/036`, Sep 4) — THE ALLOCATOR TRACK'S LAST RUNG, and
  the last of the user's three motivating kinds.** `SlabKind` grows
  `Processes = 11`; `ProcessSlot` absorbs the FOUR tables a process index used to
  key outside it (the handle table, the boot set with its count and cursor, the
  two quota rows), so `PROCESSES` becomes a plain `Slab` chain and a deployment
  that donates runs MORE CONCURRENT PROCESSES than the compiled floor.
  **THE REAL WORK WAS THE MODULE ORDER, NOT THE FIELD LAYOUT.** Those four
  storages lived in `kcore.objects`, which sits ABOVE `kcore.process` — and the
  quota ledger's own header already said that was why §12's promised "field on
  the process slot" had been kept one module down. Making them fields therefore
  meant moving either the FUNCTIONS down (out of the module whose 2,400 other
  lines call them) or the RECORD down below both; this unit took the second, so
  the new `kcore.pslot` holds the record and its field types and does no work at
  all, and **not one function moved between modules**. That is `kcore`'s own
  altitude rule applied to STORAGE rather than to code. The fattening is BYTE FOR
  BYTE FREE (4,296 bytes of per-process storage on arm64 before and after, no
  padding introduced); what it costs is a SECTION — `.bss` -> `.data`, because
  `GrantRow.region`'s `NO_MEMORY` is `-1` so design 149's zerofill does not apply
  — which is invisible to the gate (the transcript's image column is the
  userspace sosimg, not the kernel ELF) and is filed as a seed with the
  plus-one encoding that would fix it. ~16 access sites unlearned their multiply;
  THREE were rewritten rather than renamed, each a place-window question now that
  `PROCESSES[...]` is an accessor (a `&&` with two windows on one root, a
  three-subscript read/mark/return, and the quota comparison) — design 34's SL-17
  lesson applied, and two shapes I expected to be refused turned out documented
  as legal. **BOTH HALVES OF THE PRICED arm64 CONTACT LANDED.** The wall is
  enforced by `process_slots_usable()` = `min(capacity, hal.PROT_DOMAIN_SLOTS)`,
  which `alloc_process` scans to — one bound carrying both "a donated slot must be
  visible" and "a slot no tier can isolate must not be handed out" — so a create
  past it is `NoResource`, advertised rather than faulted, and `limits`' assert
  re-scopes to the FLOOR in its wording. And `PROT_DOMAIN_SLOTS` goes **3 -> 5,
  +48 KiB of `.bss`**, verified at `0x22000`. Gate: baseline `ebf76d0` 246/246
  hashing to the same `c823c1b5` the brief records, then **248/248 (124 cases)**,
  and the normalised diff is **TEN NEW ROWS AND THE TOTAL LINE — with this unit's
  rows removed the transcripts are IDENTICAL at 515 lines, ONE hunk**, so no
  pre-existing case row and no image size moved on either arch. NO SL ENTRY OWED
  (highest remains SL-24).
  **THREE THINGS THE LEAD SHOULD SEE**: (a) **the brief's "per-process sets SHRANK
  at unit 1.5" premise is FALSE and design 29's own As-built already recorded it**
  — a translation table is a page, so removing entries frees descriptors and never
  a table, and a set is 3072 descriptors (24 KiB) before 1.5 and after; the brief
  derived a "pool under ~64 KiB of .bss" budget from it that is UNSATISFIABLE as
  written (the pool is already 88 KiB at three slots), so it was re-read as
  "keep the ADDED .bss under ~64 KiB", which is plainly what was meant, and the
  re-measurement is in the record so the next raise starts from a true number;
  (b) **the wall is WITNESSED but by a measured PROBE rather than a gated case** —
  with `PROT_DOMAIN_SLOTS` temporarily back at 3 the donation succeeds (the slab
  grows by ~91 slots) and the create still answers `NoResource`, which also proves
  the raise load-bearing, since without it an arm64 process donation would buy
  exactly nothing; it is not gated because the number is per-tier (riscv32
  publishes 256, and witnessing that would want 256 identity-placed images and
  ~24 MiB) and because a case asserting 5 would pin this unit's own arbitrary pool
  size rather than a behaviour; (c) the case needed TWO design-arounds worth
  knowing — the donated region must be a FRESH CUT and not a split's remainder (a
  region that has lent bytes out is a pool root with `out_bytes != 0` and donating
  one FAULTS), and there are THREE echo-child packages plus a new
  `hal/riscv32/user/child3.ld` purely because riscv32 places by identity, so three
  concurrent children need three load addresses while arm64 serves all three from
  one `user.ld`.
  **THE 1.5 SEAM IS CLOSED (029 rebased second, Sep 3) — AND IT WAS TWO SITES,
  NOT ONE.** This entry used to say `Slab.extent_addr` was the single line to
  flip. The READ funnel was indeed one line, but `slab_donate`'s `long_zero` in
  `dispatch.saw` — the write that zeroes a donated extent so its slots start
  `Free` — dereferences the same raw physical base BEFORE the extent is in the
  table, so it never passes through the funnel. Both convert now; see 029's
  finding 6 for the fault that found it and why riscv32 could not.
  **UNIT 4 BUILT (`designs/035`, Sep 4):** the tier word + the flat profile —
  and the tier track's last rung is complete (1 → 1.5 → 2 → 4).
  `SystemOp.TierGet` (op 6) on `SystemRight.TierGet` (bit 14, in the root set)
  answers a `ProtectionTier` — `Isolated`/`Flat`, TWO WORDS per D-4 — and
  `System.tier()` decodes it exactly, panicking on a skew in
  `decode_boot_handle`'s idiom. It is **the first op on any object that answers
  a plain fact about the machine**: no state read, no resource spent, no
  argument, cannot fail. **THE BOOT-INFO HALF OF D-4 HAS NO CARRIER AND NONE WAS
  INVENTED** — `sosabi.boot` holds only the boot-handle record ("exactly the
  givable kinds", its tag word contractually "handed back unread"), design 232
  ruled a bootinfo SECTION out by name (the vDSO discipline's whole point), and
  `kernel/sysapi` depends on no HAL so a build constant cannot see the tier
  either; the scout's evidence is in the As-built §2.
  `hal/riscv32-flat/` is **250 lines of facade, not a fork**: the runner maps
  the virt board's own directory a SECOND time as `rv32virt=`, so the profile
  re-exports the arch half from `rv32core` and the board half from the board and
  shares `boot.S`, `virt.ld`, all three user scripts, `sink.c`, `trap.S`,
  `syscall.c`, the `tests/riscv32` payloads and every `.sosimg`. **Design 23's
  consolidation is untouched** — neither `riscv32-common` nor `hal/riscv32/`
  gained a copy of anything. Four runner keys carry it (`hal_board`,
  `tests_arch`, `build_tag`, `banner_arch`), each defaulting to what the two
  existing entries already had. A build FLAG was not an option: this toolchain
  has no conditional compilation at all, so a module swap is the mechanism the
  language gives.
  **ONE SL ENTRY, AND IT RESHAPED THE SEAM (SL-25): a module-level `static` does
  not carry module identity the way a free function does**, so two modules in
  one compilation unit cannot both declare one — reported at the KERNEL ENTRY, a
  file naming neither module, while `prot_reset` and its six siblings are
  declared in both and coexist fine. The consequence outlives this unit: **a HAL
  seam value a build profile may override cannot be a `static`**, and
  `PROT_REPLAY_AT_SWITCH` had been one since design 27, so the flat profile
  could not have existed without finding it. Both tier facts became functions
  (`prot_replay_at_switch()`, `prot_isolated()`) and moved from `rv32core` to the
  BOARDS while they were being changed — `riscv32-common`'s own split rule
  applied correctly, since two profiles of one architecture disagree about both.
  Both still fold. **The sharper version is filed and UNMET: a profile that had
  to override a SIZE has no spelling at all**, because a `static_assert` operand
  needs a compile-time constant and a function is not one.
  PMP is OPENED, not left alone (RISC-V default-denies, so an empty seam would
  deny root its first instruction): one TOR entry over `[0, 4 GiB)` RWX, in
  `rv32core.prot_open_all()` because PMP is architectural and because an
  `extern "C"` leaf has one owner (SL-6) — the flat profile declares no externs.
  Published from BOTH `prot_commit` and `prot_switch`, the two seam calls that
  survive `prot_replay_at_switch()` being false (the pre-M2 harness kernels
  program the seam directly and end at the former; `load_domain` calls the
  latter unconditionally), so no new HAL hook and no `kernel/core` edit.
  **FOUR CASES EXCLUDED BY NAME WITH REASONS, printed, no silent skips**:
  `process_isolation`, `map_unmap`, `umode_access_fault`,
  `root_server_oversteps` — derived by grepping every `expect_out` for a
  HARDWARE fault string rather than by reading names. **THE BRIEF'S EXAMPLE LIST
  WAS WRONG IN TWO PLACES AND THE CORRECTION IS DOCTRINAL**: `map_wx_refused`
  and `map_exec_gated` are KERNEL refusals (the W^X word check and the
  `MapExecute` gate, both in `dispatch.saw`), the tier word disclaims nothing
  about the kernel boundary, so both RUN ON FLAT AND PASS — a better proof of
  design 19's "the AUTHORITY enforcement survives intact" than any case written
  for it. `iomemory_carve` likewise still refuses, through a `device_window_ok`
  the profile re-exports unchanged.
  Gate: baseline RE-RUN at the base commit and reproducing the brief's stated
  `c823c1b5…` exactly (514 lines, 246/246), then **366/366 across three
  profiles (123 + 123 + 120), 764 lines, `0588f8b7…`** — and **THE FENCE HELD**:
  the header block and BOTH isolated sections hash identically before and after
  (`0a2eb417…` riscv32, `f4324849…` arm64), so the only motion is the flat
  profile's own 250-line section and the totals line. `sos-smoke-esp32c3` 3/3
  besides (this unit edits the C3 HAL).
  **FOUR THINGS THE LEAD SHOULD SEE**: (a) SL-25's unmet sharper version, above,
  is a real constraint on any future tier-2 board wanting a per-PROFILE
  `GRANT_ROW_BUDGET`; (b) **the `tier_word` case is FLAT-ONLY IN THE GATE purely
  because of the byte-identical fence** — adding it to the other two lists moves
  every `[i/N]` row in both — so the ISOLATED answer is PROBE-witnessed instead
  (all three profiles run and pass it; the widening was reverted), and promoting
  it is one line for unit 8, which will hold the authorization anyway;
  (c) **the `@export`ed C floor stub was DELIBERATELY NOT SHIPPED**, because it
  broke the fence — design 32's +24/+40-bytes-per-riscv32-image finding
  recurring, measured both ways here, mechanism confirmed in the emitted IR
  (every `@export` lands in `llvm.used`, a linker GC root) — so `System.tier()`
  is the whole surface and the stub is one line to add when rows may move; the
  number worth carrying is the TOTAL, ~60 stubs every process links whether it
  calls them or not, which is the kind of constant that decides an MCU port
  (**making the C altitude opt-in per package is filed, not built**);
  (d) two ABI hygiene fixes taken in passing, both compile-time only — the
  SystemOp bound assert named case 4 while the top was 5 (design 32 added a case
  without re-pointing it; it names `TierGet` now), and `SystemRight.SlabDonate`
  had no `>= (1 << 8)` assert though the rule beside it says one per case.
  `spec.md` untouched per the brief — §5b/§2's tier table is unit 8's, which now
  has a built word and a built profile to describe.
  **AND ONE PRE-EXISTING FLAKE OBSERVED, NOT CAUSED AND NOT FIXED**: the
  confirming gate run came back 365/366 on `thread_preempt`, then passed 5/5 in
  isolation and reproduced the green transcript byte for byte. **CLAUDE.md lists
  that case's A/B interleave among the rows "that no assertion reads" — an
  assertion DOES read it** (`"AB", "BA", "AB"` as ordered substrings, which is a
  good claim well argued in the case's own comment). What breaks is adjacency:
  the kernel's `SOS: timer tick` lines print into the same stream, and a tick
  landing between the `B` and the `A` leaves no adjacent `"BA"` on a run whose
  interleave (`A×7, B, A, B×7`) demonstrated all three crossings. Fix for
  whoever takes it: match the interleave against the letters-only projection
  (strip `SOS:` lines), and amend CLAUDE.md in the same pass — the interleave is
  asserted; the tick COUNT and the `interrupts=` column are the unasserted ones.
  This unit's only relation to it is that a third profile makes a run longer,
  which shifts where a tick lands.
  **UNIT 7 BUILT (`designs/037`, Sep 4) — THE MILESTONE'S OWN DEMO.** The three
  trap columns LEAVE `ProcessSlot` for a kernel-owned, grain-aligned region a
  process maps READ-ONLY, and `ProcessOp.Stats` reads that same storage — so v1
  is a compatibility floor BY CONSTRUCTION rather than by synchronization, and
  the kernel-private `ProcessStatsRecord` is gone because the op's record and
  the region's row are now one published declaration (`sosabi.StatsRow`) with
  nothing left to pin. `SystemOp.StatsRegionMap` (op 7) on
  `SystemRight.StatsRegionMap` (bit 15, root set, ABSENT from the child mask)
  installs the row into the CALLER and answers a `Mapping` in `map`'s own shape.
  **NO `Memory` IS MINTED — access without possession, deliberately**: the page
  is kernel storage, so a Memory handle would carry `Split` and would make it
  donatable, and neither is an operation that could mean anything over the page
  the kernel counts traps into. ONE right gates it, not two, because the op
  installs into the caller's own domain and names no other process.
  **THE CAPACITY DECISION CAME OUT EXACT, AND OPTION (a) WAS NOT AVAILABLE.**
  The brief offered a floor-sized page with a donated second one as a follow-on;
  scouting killed it, because once the columns RELOCATE a slot with no row has
  nowhere to count, so a floor-sized region would silently stop counting exactly
  the donated slots unit 6b exists to create. The other option turned out not
  merely bigger but EXACT: `alloc_process` scans to
  `min(capacity, hal.PROT_DOMAIN_SLOTS)`, so that constant is a CEILING on the
  slot index the machine can ever hand out — donation raises the capacity and
  cannot raise it — and a region with `PROT_DOMAIN_SLOTS` rows can never be
  outgrown. **The follow-on the brief was willing to accept is not owed at all.**
  Measured: **12288 bytes of `.bss` on riscv32 (256 rows), 8192 on arm64 (5)**,
  zerofill so the image carries none of it; `PROCESSES` shrinks 2504 → 2420 and
  4408 → 4336 bytes of `.data` — **the two deltas differ because riscv32 also
  recovered tail padding** the three doublewords were forcing on an otherwise
  4-aligned struct. TEARING IS DOCUMENTED IN THREE PLACES and the seqlock is
  recorded as the refinement and NOT built.
  **ONE DEVIATION FROM THE BRIEF, and it closed a hole rather than opening
  one**: the op also returns the caller's own ROW INDEX, in a one-word copy-out
  record. Rows are indexed by process SLOT and slot numbering is kernel-internal,
  so the brief's `(va, Mapping)` alone hands a caller a table it cannot find
  itself in — the case could only have asserted "its own row" by hard-coding 0.
  The `Mapping` still comes back in the value register and the address still
  comes from `MappingOp.Base`, so `map`'s shape is intact. Gate: baseline 369/369
  reproduced at `a64d359`, then **374/374 (126 + 126 + 122)**, and **the
  normalised diff is NINE HUNKS, every one an addition — not one pre-existing
  case row and not one image size moved on any profile.** No new floor
  `@export` (unit 4's finding stands). No SL entry owed; highest remains SL-24.
  **THREE THINGS THE LEAD SHOULD SEE**: (a) **the kernel's own address is not
  the frame's, and only the translating profile could witness it** — the first
  arm64 run took an ADDRESS SIZE fault (`0x92000003`, DFSC `0b000011`) because
  the grant was handed the kernel's linear-map address instead of the physical
  one; `hal.virt_to_phys` is the seam and is identity on riscv32, which passed
  throughout, so a unit that hands kernel storage to hardware must be exercised
  on arm64 before it is believed; (b) **a row index is not a name** — the op
  answers only the CALLER's row, so the child reads row 0 on the strength of
  root's assertion in the same transcript, and a general `top` wants either a
  per-row identity column or an op resolving a Process handle to its row (filed,
  not built, nothing in M5 consumes it); (c) `hal.PROT_DOMAIN_SLOTS` now sizes a
  SECOND thing, so raising it costs 24 bytes of `.bss` per slot on top of the
  domain pool's own cost — cheap, but no longer free, and an arena-sizing unit
  should re-measure rather than reuse a pre-unit-7 number. **The NON-GATING C3
  board compiles this region too and gets riscv32's 256 rows / 12288 bytes of
  `.bss` on a part with a few hundred KiB of DRAM** — checked to build clean,
  deliberately not retuned (no authorization to move a board knob), and the fix
  when it bites is that `PROT_DOMAIN_SLOTS` is a BOARD number which now sizes
  the domain pool and this region together. Filed for design 20's port.
  **UNIT 1.5 BUILT (`designs/029`, Sep 3):** the higher-half kernel +
  the linmap seam. The arm64 kernel LINKS at
  `physical + 0xFFFF_FF80_0000_0000` and loads at its physical
  addresses unchanged (`virt.ld`: one VMA cursor plus a per-section
  `AT(ADDR(.x) - LINMAP_OFFSET)`, and `ENTRY(_start_phys)` because QEMU
  sets the reset PC from `e_entry` with the MMU off). `TCR_EL1.EPD1`
  opens and `T1SZ` = `T0SZ` = 25 — a 39-bit VA on both halves, chosen
  to MATCH so a kernel address and its physical twin share a level-1
  index, which is what makes the seam a pure OR/AND and lets one
  two-descriptor boot table serve TTBR0 and TTBR1 at once. TTBR1 holds
  a LINEAR MAP (3 tables, 12 KiB, built once): 2 MiB Device blocks over
  the device gigabyte and 2 MiB Normal blocks over the 128 MiB of real
  RAM, PXN throughout **except RAM block 0**, which holds the kernel
  image and must execute — bounded by the `_bss_end <= ROOT_LOAD_BASE`
  assert that already existed, so the linker ceiling and the carve-out
  are one number. Per-process sets lose their kernel blocks entirely:
  TTBR0 is the process's two windows and nothing else, and a
  non-granted page is now genuinely ABSENT. `hal.phys_to_virt` /
  `virt_to_phys` land on every HAL (identity on both riscv32 boards,
  folding away to nothing); **15 kernel-side sites swept**, and the
  As-built lists the 4 deliberately NOT swept — an address from a Saw
  reference is already a kernel address, which is exactly why
  `copy_out` converts `dst` and not `src`. The boot window is
  hand-written assembly because a high-linked kernel cannot call
  compiled code before the MMU is on; the TTBR1 swap runs from the
  identity map (Linux's `idmap_cpu_replace_ttbr1` shape). No value is
  spelled twice: `boot.S` reads MAIR/TCR from `sink.c` `const`s through
  a masked pointer, MAIR is checked against `sos_mair_value()` every
  boot, and the offset's two spellings (`virt.ld`, `lib.saw`) are
  compared at boot by `linmap_offset_probe`.
  **Gate 117 cases, 234/234 both arches, transcript BYTE-IDENTICAL and
  hashing to `d37d2db` — the same hash main's own run records**, so the
  unit moves no row.
  THREE FINDINGS, the first against the brief:
  (1) **the `.bss` delta is `+0x4000` (+16 KiB), POSITIVE where the
  brief expected NEGATIVE** — the sets did lose their kernel content,
  but that content was ENTRIES inside tables the user windows still
  need, and a translation table is a PAGE, so a set is the same 3072
  descriptors it was; the delta is exactly the linmap's 3 tables plus
  the 4 KiB boot table, and the honest win is "+12 KiB instead of
  +36 KiB", not a shrink;
  (2) **the fault class DID flip** — `ESR` `0x9200004F` (DFSC 0x0F,
  permission) → `0x92000047` (DFSC 0x07, translation), same EC and
  direction bit, so `cause_tag` renders identically; checked by PROBE
  because the gate cannot witness a class (027 finding 3), and the
  normalization is now stated at `abort_direction` as a decision;
  (3) **PXN is not end to end** — one 2 MiB block is EL1 RWX, and
  per-section W^X inside it (3 linker symbols + a level 3) is a named,
  unspent refinement. No SL entry owed.
  **REBASED onto design 32 (`d919aae`) and re-gated — 1.5 was the
  SECOND REBASER, so the coordination seam is DISCHARGED here.** One
  conflicting file, `designs/todo.md`, and it is pure adjacency (two
  units appending to this entry; both kept). No code file conflicted:
  032 touched no HAL file and 1.5 touched no allocator file.
  **THE SEAM WAS TWO SITES, NOT THE ONE BOTH UNITS WROTE DOWN** —
  finding 6, and the one thing the lead should carry forward.
  `Slab.extent_addr` (the READ funnel, `kernel/core/slab.saw`) was
  anticipated by everyone; `slab_donate`'s `long_zero` (`dispatch.saw`),
  which zeroes an extent before installing it so its slots start `Free`,
  was not — it lives in another file, reaches the extent by its raw
  physical base, and runs BEFORE the extent is in the table, so it never
  passes through the funnel. Both now convert; the base stays PHYSICAL
  in the extent table either way. **A TEST CAUGHT IT, NOT A REVIEW**:
  with only the funnel flipped, `slab_donate` failed on arm64 with
  `ec=0x25 elr=0xffffff804000abdc far=0x40280000` — a kernel data abort
  whose ELR is high and whose FAR is the raw extent base, the exact
  shape of a missed conversion — while riscv32 passed at every stage,
  because the seam is the identity there. The general lesson outlives
  this pair: a "one access funnel" claim covers READS, and memory must
  be initialized before it can be read.
  **Combined-state gate: 119 cases, 238/238 both arches, 495 lines,
  hashing to `13b3214c` — exactly the hash 032's As-built records for
  its own 238-run on main**, so the combined tree reproduces main's
  transcript byte for byte and 1.5 adds no row on top of 032's. The
  slab-donate cases now execute over 1.5's linear map on arm64, which
  makes their passing the seam-flip's own witness. Sweep is now
  **17 sites**.

  **UNIT 2 BUILT (`designs/033`, Sep 3):** PLACEMENT — on arm64 a user
  address is the kernel's answer. `GrantRow` grew ONE field (`pa`); the
  seam grew `image_link_base(dest_base)` and
  `map_place(cursor, pa, device)` and split `map_target_ok` into a
  VIRTUAL half (the 4 MiB window, body unchanged) plus a new
  `map_source_ok` PHYSICAL half (RAM) — **and there is NO tier
  conditional anywhere, because the MPU tier's answer to both new
  functions is the IDENTITY** and folds away exactly as design 29's
  `phys_to_virt` does. `prot_update` grew `pa`; the MPU tier ASSERTS
  `pa == base` there, the funnel every row passes through as it is
  recorded. `LoadRegion` grew `link_base` and `place_image` now copies to
  `dest.phys_of(va)` while recording `(va, va_top, pa)`; every address on
  a `ProcessSlot` is now a USER address. The copy funnels resolve through
  `translate_user` (walk the target's rows, `row.pa + (va - row.base)`,
  whole range in ONE row) and then `phys_to_virt` — the shape 029 said
  would exist ahead of need. **`MappingOp.Base` + `MappingRight.Base` +
  `Mapping.base()`** is the op the unit owed: once the kernel chooses,
  nothing else can tell a program where its memory is.
  **THE VA POLICY IS NOT THE BRIEF'S, AND THE SUITE IS WHAT REFUSED IT.**
  The brief proposed a monotonic per-process cursor; `mapping_slot_free`
  (map/unmap thirteen times, then expect the fourteenth mapping of a
  re-split range at the address the thirteenth wrote) and `memory_split`
  (`base` and `base + CHUNK` through two consecutive splits) both assert
  that a recycled range comes back at a USABLE address, which a cursor
  that only rises cannot give. What landed is `free_user_va`: FIRST FIT
  at or above the target's own `region_top`, over the rows it already
  has, device rows skipped — no new `ProcessSlot` field, since it is
  derived from the record. Cost named at the site: an address freed by
  `unmap` may be reused by a later mapping of different bytes. Its useful
  consequence is what carried the unit — one link base and one region
  size mean **every process's FIRST mapping lands at the same number**
  (0x4024_0000), so `share_double_map` needed NO retargeting against the
  brief's expectation that it was the likeliest case to.
  **LINKER SCRIPTS: arm64's THREE COLLAPSED INTO ONE** (`user.ld`;
  `root.ld`/`child.ld`/`child2.ld` deleted, **121 `Saw.toml` files**
  repointed), **riscv32's THREE KEPT** (ruling 11) — one directory
  against the other IS the tier split, in the tree, gated every run.
  The runner's arm64 constants did NOT need simplifying, they needed
  DISAMBIGUATING: `child_region_base` and `pool_base` are PHYSICAL and no
  frame moved, and `root_entry` is unmoved because `USER_IMAGE_BASE` was
  given `ROOT_LOAD_BASE`'s value on purpose, so root's `entry=` row stays.
  **Gate: baseline 238/238 119 cases 495 lines `801a0f98` (the brief's
  stated baseline, reproduced exactly) -> 239/239, 120 cases, 498 lines,
  `2238afc8`. THE riscv32 HALF IS BYTE-IDENTICAL — 250 lines, hash
  `1ca29323` both sides, a diff of ZERO lines.** The arm64 motion is
  enumerated and totals 120 removed / 123 added: 119 case rows
  RENUMBERED (payload byte-identical, `[i/119]` -> `[i/120]`, the case
  appended at the END so no index moved), 1 new row `map_placed`, 2 new
  aarch64 size rows (the new packages), the total line, and **ZERO
  existing size rows moved on EITHER arch** — the brief authorized aarch64
  size motion and none was needed, because `user.ld` page-pads.
  **EIGHT FINDINGS**, the load-bearing ones: (1) the policy deviation
  above; (3) **an `@export` costs every image on every architecture —
  measured at +24 bytes per riscv32 image**, which is why the per-op C
  wrapper `sos_mapping_base` is OMITTED and recorded as owed (032 saw the
  same mechanism from the other side at +40); (5) **`sos_test_pool_base()`
  now returns a VIRTUAL address on arm64 under a name that says
  otherwise** — ten test packages read it, all ten compile for BOTH
  arches, and a byte-identical riscv32 gate leaves the per-triple `native`
  slot as the ONLY arm64-only lever, so **migrating those ten onto
  `Mapping.base()` is OWED and needs a unit authorized to move riscv32
  size rows**; (6) `map_basics`'s header now describes a machine arm64 is
  not (its double map is two addresses, not an alias) and is owed with
  (5); (7) `process_isolation` now faults for a STRICTLY BETTER reason —
  the child cannot reach root's region at all rather than being refused
  at it, same observable. (4) is design 27's finding 2b met verbatim: the
  arch-free lint refused the build over the word `arm64` in a
  `kernel/core` DOC COMMENT. **No SL entry owed** — the language did not
  bite once.

- M6 (after M5): the storage milestone — seed `designs/030` (user-
  ruled Sep 3): flash-first block driver, RO archive fs + a simple
  tmpfs (the write path, same protocol), userspace loader, the
  namespace as the protocol, and a simple UART shell (ls/read/write
  builtins — poke at a running system). Scoping session at M5's
  close.
- M7 (after M6): the POSIX compatibility layer, toybox first — seed
  `designs/031` (user-ruled Sep 3; the posix_spawn hinge). Scoping
  session at M6's close.

## [BACKLOG] — filed, not scheduled

- riscv32 Sv32 tier-1 climb — PUNTED from M5 [`designs/025` ruling
  11, user, Sep 3]: 32-bit VA scarcity makes placement a genuinely
  different design (careful fitting vs 64-bit's space-for-tables
  trade); no in-tree hardware target wants Sv32 (C3/P4 are M+U); a
  future riscv MMU target is likelier rv64/Sv39, inheriting the
  64-bit shape. Revisit trigger: a real S-mode riscv target earning
  a HAL. Design 029 carries the Sv32-stays-identity linmap note for
  whoever picks it up.

- **`PipeRequestRight.Reply` HAS NO TEST ANY MORE** [#22 As-built
  finding 1, Sep 2]. Ruling 12(c) took `Mint` out of
  `pipe_request_rights()`, and the only way this tree ever produced a
  `PipeRequest` handle WITHOUT `Reply` was `pipe-no-reply` minting an
  attenuated sibling — so that spelling died with the bit and the case
  retargeted to the one-name rule instead. The gate itself is still in
  `pipe_request_op_decoded` and is now reachable ONLY through a `give`
  keep mask into another process (`Take` mints the full set; a
  message-carried handle carries the sender's entry rights verbatim),
  which is a two-process case: root posts, takes the obligation, gives
  it to a child with `keep = Transfer | Wait`, and the child's `reply`
  faults with `AccessDenied`. It wants a new child package (~60 lines,
  `child-narrow`'s shape) and one runner entry. THE SAME HOLE EXISTS AT
  `PipeReplyRight.Resolve` for the same reason and would be covered by
  the same case shape at the other end. Filed rather than done because
  design 22's scope was the discipline, not the coverage the discipline
  displaced.

- `Process` and `System` in messages — a sysapi MODULE-SPLIT ruling
  [#18 As-built finding 1, Sep 2]: `PipeHandle` ships six cases
  (`Memory`, `IoMemory`, `PipeInlet`, `PipeOutlet`, `PipeReply`,
  `PipeRequest`) and the kernel refuses the other two AT THE SENDER in
  `msg_kind_of`. The obstacle is module ORDER and not doctrine —
  `sos.pipe` sits below `sos.system` and `sos.process` (both name pipe
  types in their `give` and factory surfaces), so a case holding a
  `Process` wrapper is a cycle, DF-232e's shape. The scouted path: split
  the wrapper STRUCTS into a leaf module both layers import and leave
  the METHODS where they are, which Saw's extensions make mechanical
  since a type's declaration and its methods need not share a file. It
  moves every handle wrapper in the package, which is why it wants a
  lead ruling before it is written; what it buys is a launcher able to
  hand a child a Process or System handle AFTER `start`, which no
  in-tree program needs today. The kernel side is one line in
  `msg_kind_of` when it comes

- LAZY HANDLE DECODE in `PipeMsg` — the message vocabulary's image cost
  [#18 As-built §12, Sep 2]: unit 4's transcript accounting measured
  +26.7% (riscv32) / +22.6% (arm64) across every image that names the
  pipe or wait surface, and the cause is the TYPED value rather than the
  wire record — `PipeMsg` grew from `{len, bytes}` to
  `{len, [PipeHandle?; 4], bytes}`, and a `PipeHandle` is an enum of six
  `NoCopy` wrappers, so every take/resolve/wait caller moves a bigger
  value and links drop glue for four optional six-way enums. It lands
  hardest where it is least deserved: `tests/event-wake` waits on an
  Event, touches no pipe, and grew 5,584 B / 8,192 B, because
  `decode_wait` (4,934 B on riscv32), `wait_message` (962 B) and
  `decode_handle` (568 B) link into EVERY image that calls `wait`
  whether or not its attachments could produce a message. The lever:
  keep the kind bytes and the handle WORDS in `PipeMsg` and construct a
  wrapper only when the receiver asks for a slot
  (`msg.take_handle(0) -> PipeHandle?`), which moves the six-way
  construction out of `decode_wait` into a function only a
  handle-reading program links; the drop then walks words rather than
  matching enums. No kernel and no ABI consequence — it is entirely
  inside `sos.pipe`. NOT done in unit 4 because it rewrites the
  RECEIVING VOCABULARY that unit's §API was user-reviewed on, and the
  ergonomics trade (a match on an optional field becomes a call then a
  match) is a taste question the lead should answer

  **DISPOSITION (user, Sep 2): deliberately UNSCHEDULED** — the M5
  namespace makes handle-receipt universal (open() answers
  capabilities in messages), and XIP on ESP32-class parts lands the
  code tax on flash, not SRAM. Revisit trigger: a genuinely
  tight-SRAM tier target; if it fires, prefer sawc-side drop-glue
  dedup BEFORE this API rewrite. **[RIDER, Sep 3, the design-25
  session: the "M5 namespace" premise is STALE — the namespace was
  ruled OUT of M5 (designs/025 seed 3). The disposition itself
  stands on its other two legs (XIP + the revisit trigger).]**
- buffered `debug_print` — a length-taking form [#16 As-built finding,
  Sep 1]: today the seam traps once per BYTE, so a test's prose
  outweighs its object ops ~10:1 in the syscall column
  (`pipe_oneshot`: ~5 traps of exchange, ~300 of description). A
  buffered form taking (addr, len) is the obvious answer and an ABI
  change with no consumer yet; filed for the unit that first wants
  the column quiet. The interrupt column measures preemption pressure
  on ONE process, not machine load — restate wherever the
  shared-region `top` seed (#16) gets built.

- transfer-funnel dissolve helpers [SL-3 remainder, SL-16 closure
  finding, Sep 2]: the by-value-PARAMETER funnels still disarm by hand
  (`Process.give(memory:)`, `give(system:)`, and the four `Waiter.give`
  overloads in `kernel/sysapi/src/pipe.saw`), because design 260's
  `consumes` is a RECEIVER-position effect and those take their wrapper
  as an argument. Each body is the same four lines: bind the moved-in
  value, read the word, write `NO_HANDLE`, call the attach. A private
  `consumes` helper on each wrapper —
  `func dissolve(&var self) consumes -> UInt { self.handle }`, legal
  because it is declared in the type's own module — collapses that to
  `let word = (move owned).dissolve()` and retires the last sentinel
  writes outside the deinits. NOT DONE at SL-16's closure by
  instruction: that sweep converted RECEIVERS, and this is a second
  shape with its own review surface — it puts a teardown-substituting
  method on every handle wrapper in the package, which is a taste
  question the lead should answer before it is written six times.
  `PipeReply.resolve` is NOT a candidate and never will be: its
  consumption is CONDITIONAL, which is the one shape the effect cannot
  express

- tools/sosimg_dump.py — landed Aug 29 (user-requested dev tool, this
  line is its capture): dumps sosimg v3 headers/segments and raw v2
  region tables; kept in step with imgformat + process.saw by hand —
  a format bump edits it too
- sos_runner `--case NAME` — landed Aug 30 (user-requested dev
  convenience, this line is its capture): run named cases only
  (repeatable / comma-separated, `-`/`_` interchangeable, did-you-mean
  on unknowns); the filter narrows the package builds too. NEVER the
  gate — the gate stays every case on every architecture

- tests idiom sweep — landed Aug 31 (user-ruled, this line is its
  capture): 290 bind-or-bail match-on-Result sites across 55 test
  packages became the inline try/catch guard form (CLAUDE.md carries
  the ruling); 75 statement-position sites stay `match` on SL-12's
  ICE, 25 fold-shaped sites (Err supplies a value) left as a possible
  follow-on, 12 real-work + 4 negative-test matches stay by design
- sawlang#238 unit 6 remainder — CI cold-fetch acceptance + negative
  tests PEND sawlang becoming public at the pinned sha (a82e06f4);
  dispatches from the sawlang side [sawlang#238]
- `event-wake` / `event-consume-wake` rewritten to ONE HANDLE EACH,
  now that `MINT_OP` exists [#4 finding 5; design 3 finding 2]. They
  share an Event handle by ADDRESS through a parked `UnsafePointer`
  because no op could mint a second one; a mint makes the shape those
  programs always wanted writable. AUTHORIZED AS BACKLOG by the lead
  and deliberately NOT done in unit 3 — it moves shipped transcript
  rows, so it needs a unit that authorizes them by name. **CARRIED past
  unit 4 unchanged** [#6]: that unit's authorization named exactly two
  cases (the uart-echo pair), so the same reason applied again
- `SegFlag.Device` RETIREMENT — a PIN-BUMP EVENT, sawlang-side [#6 D-6].
  M3 unit 4 migrated both driver packages to obtained-not-declared, so
  NO IMAGE IN THIS TREE declares a device window any more and the path
  is DORMANT. What survives is `imgformat`'s `SegFlag.Device` case, the
  emitter that writes it (`blade/sosimg.saw` — SAWLANG-SIDE, which is
  why this is a pin bump and not an edit here), and the in-tree
  consumers it feeds: `kcore.loader`'s `check_device_grant`,
  `check_segment`'s device branch, `place_image`'s device arm, and
  `validate_image`'s `allow_device` parameter (still `true` for the
  boot door, `false` for `process_create`). Retiring it is: delete the
  flag and the manifest key sawlang-side, bump `sawlang.pin`'s two
  lines together, then delete the four in-tree consumers and the
  `device: Bool` field on `GrantRow` becomes the only device signal
  (set by `IoMemoryOp.Map`). Nothing about the kernel's device MAPPING
  changes — `prot_device` and `GrantRow.device` are unit 4's path and
  are unaffected
- M4 seeds — pipes + PipeReplyHandle IPC (select-with-timeout via
  Timer), IOMMU driver + critical processes, priorities/§7 bands,
  SMP + IntrSpinLock (after channels; unit 1.5's point map is its
  conversion guide), FP in userspace, vDSO true-mapping [sawlang#232
  "Explicitly out"]

## [SAWLANG] — deficiencies met building sawos, tracked here for upstream resolution
One entry per issue, resolution-sufficient: the symptom verbatim, the probe/site, the workaround in-tree, and what upstream resolution looks like. The user references these from sawlang; entries close here when a pin bump delivers the fix.

- SL-1 — NO RUNTIME-INSTALLABLE FUNCTION HOOK (freestanding): a mutable static holding a function address is refused (``static `DELIVER` must be initialized by a compile-time constant``, probed against pinned sawc a82e06f4 during design 1). Design 186's static-init rule as specified, but the capability gap is real: freestanding code cannot install a boot-time dispatch hook at all (FuncPointer statics are immutable, unsafe static var requires const init, no Option-of-FuncPointer zero form). Workaround: design 1 restructured module altitudes instead (kcore.preempt). Resolution: a ruled story for late-bound function slots — a zero/None-initializable FuncPointer form, or a blessed once-set static tier.
- SL-2 — PLATFORM-WIDTH STATIC DOES NOT ADOPT A CONST EXPRESSION: `static M: UInt = (1 << BITS) - 1` over `static BITS: Int` is refused; DF-240a's adoption reaches FIXED-WIDTH slots only, and platform `UInt` is not one (design 3, kernel/abi HANDLE_INDEX_MASK — the `as UInt` workaround is written at the definition with this note). Resolution: widen const adoption to platform-integer slots, or rule the asymmetry permanent and document it beside DF-240a. **FIX IN FLIGHT (user, sawlang design 257 / DF-282a, Aug 31): closes at the NEXT pin bump (0.3.0). At closure, sweep the `as UInt` workaround at `HANDLE_INDEX_MASK` and any sibling sites the entry names.** **CLOSED (third pin bump, sawlang 0.3.0 @ `66e353da`, Sep 1):** design 257's const-adoption ladder reaches platform-integer slots; the `as UInt` and its recorded rationale at `HANDLE_INDEX_MASK` are swept (the entry named no sibling sites, and a tree grep confirmed none), and the derivation now folds unadorned.
- SL-3 — NO CONSUME-WITHOUT-DEINIT (mem::forget equivalent): a NoCopy value whose deinit must NOT run after its payload's ownership left by other means (a kernel moved the handle word) has no language spelling; sawos uses the NO_HANDLE-sentinel-in-the-field idiom, disarm-before-syscall, recorded as the transfer-funnel contract (design 3 D-5, design 4 funnels). Livable; a `forget`/dissolve construct would retire the sentinel pattern. Low urgency, filed for the record. **PARTLY ANSWERED (fourth pin bump, sawlang 0.4.0 @ `46eebb36`, Sep 2), and the remainder is named.** Design 260's `consumes` IS the dissolve construct for one position: a consuming body substitutes for the custom `deinit` body, so a method that hands the word to the kernel simply does not run the release, and SL-16's closure retired the sentinel at `PipeRequest.reply` and `PipeRequest.reply_wait` on exactly that mechanism. WHAT IT DOES NOT REACH, and why the sentinel is still in the tree: (a) `consumes` is RECEIVER-position only, so every funnel taking its wrapper as a by-value PARAMETER — `Process.give(memory:)`, `give(system:)`, the four `Waiter.give` overloads — still disarms by hand; the in-tree answer is a private `consumes` dissolve helper per wrapper, which is the backlog item above and needs no language change. (b) A funnel whose consumption is CONDITIONAL — `PipeReply.resolve`, which consumes on two paths out of three — cannot use the effect at all, because `consumes` consumes on every path. That third shape was not priced when this entry was filed and is the one that keeps the sentinel a permanent idiom here rather than a migration.
- SL-4 — AMBIGUITY DIAGNOSTIC ANCHORS AT 1:1 WITH `<unknown>` MODULE: ``ambiguous struct `Thread`: defined in both `<unknown>` and `sos.thread` `` reports at line 1:1 of an arbitrary file — the location and the `<unknown>` are both noise (design 5, the std.task Thread<T> prelude collision). Resolution: real span + real module name on the ambiguity path.
- SL-5 — DECLARED-VS-IMPORTED NAME PRECEDENCE ASYMMETRY AGAINST THE PRELUDE: a locally DECLARED type name beats a prelude generic of the same name; a selectively IMPORTED one ties with it and errors (design 5: `struct Thread` won in-file, `import sos.thread.{Thread}` lost to std.task's `Thread<T>` — worked around with the qualifier at five sites). Resolution: rule which precedence is intended and make the two paths agree.
- SL-6 — `extern "C"` DECLARATIONS ARE PRIVATE-BY-CONSTRUCTION AND UNSHAREABLE: an extern decl carries no visibility modifier, and two sibling modules declaring one symbol is a hard ambiguity at the importer (probed, design 5) — which forces a package's entire extern surface into one bottom module. Resolution: visibility on extern blocks, or a ruled one-owner-module convention documented upstream.
- SL-7 — EXTENSION-METHOD LOOKUP DOES NOT FOLLOW FACADE RE-EXPORTS (designs 142/229 by design): a type's extension methods must live in its declaring module, so mutually-referential types cannot be split across files even behind a facade (design 5 finding 1 — System/Process/BootHandle are one 724-line module with three banners). Resolution is a language design question, named in the finding: an internal/forward-declaration tier, or lookup that follows `public import`. **SECOND SITE, design 6**: it also decides METHOD PLACEMENT, not just file layout. `Memory.map(into: &Process)` is unwritable — `BootHandle` carries a `Memory?` and an `IoMemory?`, so both region modules sit below `sos.system` and a `map` written in either naming `Process` is the DF-232e cycle. The funnels became `Process.map(memory:access:)` / `Process.map(iomemory:)` instead. That reads well here (it matches the `give` overloads), but the constraint chose it rather than the design. **THIRD SITE, design 8 — and this one MOVED A SPELLING THE BRIEF HAD RULED.** Design 8's ruled surface is `Waiter.add(process:, key:)`, the fourth member of the overload list `sos.waiter` already holds; it is unwritable, because `waiter` sits below `system` (its three `add` overloads read the waitables' handle fields, which is what put it above THEM) and `Process` is declared in `system` with `System` and `BootHandle`, all three mutually referential. So the surface is `Process.attach(waiter:, key:)` — design 6's flip applied verbatim — with a comment in `waiter.saw` where the fourth overload would have gone. The alternative was rejected on the same finding's other half: an `extension Waiter` written in `system.saw` IS legal under the orphan rule, but design 142 scopes extension-method LOOKUP to the declaring module plus the caller's DIRECT imports, so every consumer would have owed an `import sos.system` whose purpose nothing on the page explains. Resolution unchanged and now three-sited: an internal/forward-declaration tier, or lookup that follows `public import`. **THIRD SITE RE-LITIGATED AND UPHELD (lead, Aug 30):** a sawlang-side analysis (relayed by the user) proposed restoring `Waiter.add(process:)` via `extension Waiter` in the Process-declaring module, arguing the overload is visible to exactly the files that hold a Process. Verified against LANGUAGE_SPEC (extension scoping §142; re-export: "it does not widen extension scope") and REFUTED for this package: every consumer takes `Waiter` and `Process` through the `sos` facade's `public import`, never by importing `sos.system` directly, so the overload would be invisible at every real call site — and the same analysis's "move all typed overloads into the waitables' own modules" would break the three EXISTING `add` overloads the same way (they are visible today only because they are `sos.waiter`'s inherent API). A facade-placed extension fails separately: the body needs `process.handle`, deliberately module-private to `sos.system` (the no-raw-word surface guarantee). `Process.attach` stands. The proposal's one durable yield: the upstream resolution is now CONFIRMED to be "extension lookup that follows `public import`" — the other language-side fixes do not help a facade package. **FIX IN FLIGHT (user, sawlang-side, Aug 30):** extension lookup will follow `public import`; the user is implementing it and the seed edits (the `extension Waiter` in `sos.system`, the respelled test call sites) sit UNCOMMITTED in the working tree awaiting the pin bump that delivers it. The respell entry below is the capture; this entry closes when that bump lands. **CLOSED (Aug 30) — THE FIX LANDED: sawlang 0.2.0 @ `3f15d2ee`, delivered by the FIRST PIN BUMP.** Extension lookup follows `public import` now; the respell executed with the bump (`Waiter.add(process:, key:)` via `extension Waiter` in `system.saw`, funnel `public(package)`, `Process.attach` retired, three call sites respelled, facade header + design 8 As-built rider written). The first two sites' placements stand — the fix moves methods, not declarations — so the file-layout half of this entry is RESOLVED-BY-TOOL for methods and MOOT for declarations.
- SL-8 — DF-172d STILL BITES (already filed upstream as DF-172d; listed here as a cross-reference, not a new issue): unbracketed binary expressions do not wrap across lines; hit again in designs 2 and 4. Resolution tracked in sawlang's own DF. **THIRD SITE, design 6**: `if (a & X) != 0\n && (b & Y) == 0 {` in `kcore.dispatch`'s `map_access`, fixed by parenthesising the whole condition. **FOURTH AND FIFTH SITES, design 21** (`tests/svc-uart-*`), and the second of them is a shape the earlier sites had not shown: an `if` CONDITION carrying a three-term sum (`traps == answered + 2 * discarded + 1`) is the familiar one, fixed by hoisting the arithmetic into a parenthesised `let`; but a wrapped ASSIGNMENT (`window_discarded =\n    window_discarded + 1`, split only because the arm was deeply indented) fails as `Unexpected token: NEWLINE` at the LINE BELOW, and its diagnostic points into the continuation rather than at the assignment that ran out of room. It bites hardest where indentation is deepest, which is exactly where a line is most likely to need wrapping; the in-tree fix was the compound form (`window_discarded += 1`), which fits.
- SL-9 — A LONE RAW-BACKED ENUM CASE DOES NOT ADOPT A FIXED-WIDTH SLOT, THOUGH A COMBINATION OF THEM DOES: ``static `RO` has type `UInt32` but its initializer has type `MapAccess` `` (design 6, `tests/map-basics`, pinned sawc a82e06f4). `static RW: UInt32 = MapAccess.Read | MapAccess.Write` COMPILES and folds to 3 — DF-240a's flag-enum rule — while `static RO: UInt32 = MapAccess.Read` at the same slot is refused, so ONE bit needs an `as UInt32` projection and TWO bits do not. Minimal example: `enum E: UInt32 { case A = 1, case B = 2 }` then `static X: UInt32 = E.A | E.B` (ok) beside `static Y: UInt32 = E.A` (error). Workaround in-tree: write `MapAccess.Read as UInt32`, with the asymmetry noted at the line. Resolution: let a bare case adopt a fixed-width slot exactly as a combination does (the value is as constant either way), or rule the asymmetry permanent and say so beside DF-240a — the current state teaches that adding a second flag REMOVES a cast, which is backwards. **FIX IN FLIGHT (user, sawlang design 257 / DF-282b, Aug 31): closes at the NEXT pin bump (0.3.0). At closure, sweep the recorded workaround sites.** **CLOSED (third pin bump, sawlang 0.3.0 @ `66e353da`, Sep 1):** a lone raw-backed case now adopts a fixed-width slot; `map-basics`' `RO` lost its `as UInt32` and the recorded comment, and `pipe-no-post`'s keep-mask comment dropped its stale lone-case clause (that mask was policy, not workaround — its value is untouched).
- SL-11 — AN EXTENSION-METHOD OVERLOAD SET BINDS ONLY ITS FIRST MEMBER WHEN THE RECEIVER TYPE'S NAME IS NOT IMPORTED: ``missing argument for parameter `entry` `` anchored at the first LABEL of the call (design 12, `tests/waiter-revoked`, sawlang 0.2.0 @ `3f15d2ee`). `import sos.{System, SosStatus, ...}` — everything the file names EXCEPT `Process` — makes `proc.thread_create(stack_top:, arg:)` unresolvable, because only the FIRST of the two `thread_create` overloads declared in `extension Process` is bound and the arity mismatch is then reported against it; adding `Process` to the same import list makes both members visible and the call compile. Nothing in the file writes the word `Process`, so the import is pure ceremony that only the diagnostic asks for — and the diagnostic points at an argument, not at an import. **THIS IS DF-242b'S RULE AT THE OTHER DECLARATION KIND.** Design 249 ruled that an import binds the WHOLE overload set a free-function name stands for, and DF-242b fixed the bare form that used to bind the first member only; extension METHODS were not swept with it, and a method is not imported by name at all — it is reached through design 142's extension neighbourhood — so the set that gets bound is decided by whatever put the receiver type in scope. Minimal repro, three files, no facade needed (a direct import fails identically, so this is NOT SL-7's re-export question):
  ```saw
  // dep.saw
  public struct Crate { public n: Int }
  extension Crate {
      public func make(&self, entry: Int, top: Int) -> Int { entry + top }
      public func make(&self, top: Int) -> Int { self.make(entry: 7, top: top) }
  }
  public func hand() -> Crate { Crate(n: 1) }

  // main.saw
  import dep.{hand}                 // error: missing argument for parameter `entry`
  import dep.{Crate, hand}          // compiles, prints 8
  func main() { let c = hand()  print("{}", c.make(top: 1)) }
  ```
  Workaround in-tree: import the receiver type by name, with the reason written at the import (`tests/waiter-revoked/src/main.saw`). The tree had not met this before because every other root server imports `Process` and `Thread` anyway — a program that only ever HOLDS a value of a type, never names it, is the shape that hits it. Resolution: bind the whole overload set for an extension method exactly as design 249 binds one for a free function, keyed on the receiver type rather than on which of its names an import happened to mention; failing that, a diagnostic that names the unbound sibling and the import that would bind it, since the current one sends the reader to the argument list. **FIX IN FLIGHT (user, sawlang design 256 / DF-280a, Aug 31): closes at the NEXT pin bump (0.3.0). At closure, drop the ceremony `Process` import in `tests/waiter-revoked` and re-verify the three-file repro resolves.** **CLOSED (third pin bump, sawlang 0.3.0 @ `66e353da`, Sep 1):** design 256's identity-keyed resolver binds the whole overload set for a resolved receiver; the ceremony `Process` import and its comment are dropped from `tests/waiter-revoked`, and the minimal repro re-verified under sawc 0.3.0 — `import dep.{hand}` alone compiles and prints 8.
- SL-13 — A `&var [T; N]` PARAMETER'S ELEMENTS ARE NOT ASSIGNABLE, THOUGH THE SAME ARRAY REACHED THROUGH A `&var STRUCT` IS: ``error: cannot assign to element of immutable array `a` `` with ``hint: consider using `var` instead of `let` to make it mutable`` (design 14, `tests/pipe-oneshot`, sawc 0.3.0 @ `87063387`). A fixed-size array borrowed DIRECTLY is treated as immutable at an indexed write; borrowed as a field of a struct it is mutable, so the referent's mutability is being decided by how it was reached rather than by the sigil. The hint compounds it — there is no `let` anywhere in the program, and the parameter already says `&var`, so it sends the reader to a declaration that does not exist. LANGUAGE_SPEC's own DF-232a list puts "a `&var`/`self` referent" among the assignment targets, which is what made this look like a bug rather than a rule. Minimal repro, one file, hosted:
  ```saw
  func bump(a: &var [UInt8; 4]) {
      a[0] = 9                       // error: cannot assign to element of
  }                                  //        immutable array `a`

  struct Crate { n: Int, xs: [UInt8; 4] }
  func set_nested(b: &var Crate) {
      b.xs[0] = 9                    // compiles, and runs
  }
  ```
  Workaround in-tree: the array is not lent mutably at all — a message buffer is filled by the CALLER and passed `&`, and the two helpers that wanted to write one take the byte as a parameter instead (`tests/pipe-oneshot`). That is livable here because the arrays are small and caller-owned; a helper that genuinely fills a caller's buffer (the shape `read_into` has) has no spelling today short of wrapping the array in a one-field struct. Resolution: make an indexed write through a `&var [T; N]` legal, exactly as it is through a `&var` struct field — or, if the restriction is deliberate, say so in LANGUAGE_SPEC beside DF-232a's list and give the diagnostic a hint that names the wrapper-struct workaround instead of a `let` the program does not have.
  **SECOND SITE, design 21, AND IT SHAPED A PUBLIC API RATHER THAN A HELPER.** `tests/uartproto` is the /dev/uart0 protocol's message builder, and its whole job is to fill a `[UInt8; PIPE_BODY_BYTES]` a caller then hands to `post`/`send` — the shape this entry says has no spelling. The wrapper-struct workaround is what shipped: `public struct UartBody { public bytes: [UInt8; PIPE_BODY_BYTES] }`, with `&buf.bytes` at every send site. It reads acceptably and the reason is written at the declaration, but note what the restriction decided: a builder that could have been three free functions over a borrowed array is a TYPE, and every consumer of the protocol now names it. That is the first time this deficiency has reached a package's published surface rather than a private helper's signature.
- SL-12 — STATEMENT-POSITION `try ... catch` ON A `Result<Void, E>` CALL IS AN INTERNAL COMPILER ERROR: ``internal compiler error at src/main.saw:257:5 (TryExpr): 'NoneType' object has no attribute 'type'`` (the Aug-31 idiom sweep, sawlang 0.2.0 @ `3f15d2ee`). `try give(...) catch { ... }` as a bare STATEMENT — and the `let _ =` spelling identically — dies in sawc when the callee's Ok type is `Void`; the same catch with a non-Void Ok compiles and runs, so it is the Void, not the discard. Probe: any `Result<Void, SosStatus>` op (`give`/`start`/`waiter.add`/`timer.arm`/`interrupt.ack`/`waiter.remove`/`mapping.unmap`) under a statement-position inline catch. In-tree workaround: statement-position checks stay `match { case Ok(_) -> {}, case Err(e) -> ... }` — 75 such sites deliberately kept at the Aug-31 sweep, and CLAUDE.md's idiom ruling carries the caveat until the fix ships in a pin. Resolution: the inline catch's lowering handles a Void Ok payload (nothing to bind is not nothing to type); the guard form should be legal at statement position exactly as the binding form is. **CLOSED (second pin bump, sawlang 0.2.1 @ `8ffc5809`, Aug 31):** DF-281a fixed it fix-on-discovery; the statement-position guard form is legal, CLAUDE.md's caveat is lifted, and the 75 kept `match` sites are a queued conversion pass.
- SL-14 — A `move` INSIDE A **DIVERGING** INLINE CATCH RETIRES THE BINDING ON THE FALL-THROUGH PATH TOO: ``error: use of moved variable `owned` `` with ``hint: value was moved at line 14 and can no longer be used`` (design 15, `kernel/sysapi/src/waiter.saw`, sawc 0.3.0 @ `87063387`). The catch block ENDS IN A `return`, so its `move` runs only on the error path and the binding is provably still live below the `try` — but the move checker records the move unconditionally and refuses the next use. It is the exact shape ruling 11(c)'s "a refused give HANDS THE OBJECT BACK" wants: consume on the error path, disarm on the success path. Minimal repro, one file, hosted:
  ```saw
  struct Res { n: Int }
  extension Res: NoCopy {}

  func might(x: Int) -> Result<Void, Int> {
      if x == 0 { return 1 }
      return
  }

  func hand(r: Res, x: Int) -> Result<Void, (Int, Res)> {
      var owned = move r
      try might(x) catch {
          let back: (Int, Res) = (error, move owned)
          return move back          // the catch DIVERGES
      }
      owned.n = 0                   // error: use of moved variable `owned`
      return
  }
  ```
  Workaround in-tree: the eight `Waiter.give` overloads disarm BEFORE the syscall (the transfer-funnel contract verbatim) and RE-MINT a fresh wrapper around the same word on the error path, which the kernel's own rule licenses — a refused add consumes nothing. That reads well enough that it is what shipped, and it is arguably stronger (no path can leave two owners of one word), so this is filed for the language rather than as a blocker. Resolution: the move checker should follow the catch block's divergence, exactly as design 228 makes a diverging catch satisfy any expected type — the two halves of "a diverging catch is an exit" should agree. Failing that, a diagnostic that says the move is on a path that cannot reach the use, rather than one that points at a line the fall-through never executes.
- SL-15 — A BARE INTEGER LITERAL DOES NOT ADOPT A `UInt` PARAMETER WHEN THE METHOD IS OVERLOADED, THOUGH THE SAME CALL ON A SINGLE-CANDIDATE FUNCTION ADOPTS: ``error: argument 1 expects `UInt` but got `Int` `` with the three-conversion hint (design 15, `tests/pipe-send-blocking`, sawc 0.3.0 @ `87063387`). Every candidate in the set declares the parameter `UInt` and the candidates differ by ARITY, so there is exactly one that could match and nothing about the literal is ambiguous — but adoption does not run and the call is refused. LANGUAGE_SPEC's rule is that a bare literal adopts a fixed-width or platform EXPECTED type at a parameter, and its documented ambiguity carve-out is about two candidates of DIFFERENT integer types (`h(Int)` vs `h(Int8)`), which is not this. Minimal repro, one file, hosted:
  ```saw
  struct Bag { n: Int }
  extension Bag {
      func put(&self, len: UInt) -> UInt { len }
      func put(&self, len: UInt, extra: Int) -> UInt { len + (extra as UInt) }
  }
  func solo(len: UInt) -> UInt { len }

  func main() {
      let b = Bag(n: 1)
      print("{}", solo(len: 1))     // adopts: one candidate
      print("{}", b.put(len: 1))    // error: argument 1 expects `UInt` but got `Int`
  }
  ```
  It bites wherever a ratified surface is overloaded on presence rather than on type — §2.1's `send(msg)` beside `send(msg, timeout:)` is exactly that shape, and the two differ only in trailing arguments. Workaround in-tree: name the number (`static TIMEOUT_MSG_LEN: UInt = 1`), with the reason written at the declaration; a suffix is not available for platform `UInt`, which is what makes the workaround a static rather than a one-character fix. Resolution: run literal adoption per CANDIDATE during overload resolution and let a set whose members agree on the parameter's type resolve it, keeping the refusal for the genuinely ambiguous case the spec already documents. **IT COST A NAMING IN M4 UNIT 4 (design 18, Sep 2), which is worth recording because the cost is now shipped surface.** Adding a handle-carrying `post(body:len:handles:)` beside `post(body:len:)` broke 38 `len: <literal>` call sites across the suite that had compiled for three units — every one of them a candidate set agreeing on `UInt` and differing by arity, this entry's exact shape. Rather than name 38 numbers, the handle-carrying variants ship as DISTINCT NAMES: `post_with`, `send_with`, `reply_with`, `reply_wait_with`, with the reason written at the definitions. They read honestly enough that this is not urgent, but collapsing them into overloads is a mechanical sweep the day this closes, and until then a ratified surface is being named around a resolution bug.

- SL-16 — THERE IS NO CONSUMING (`self`) METHOD RECEIVER, SO A TRANSFER FUNNEL CANNOT BE A METHOD ON THE THING IT CONSUMES: ``Parse error at 8:20: 'self' must be a reference: use '&self' or '&var self'`` (design 17, `kernel/sysapi/src/pipe.saw`, sawc 0.3.0 @ `87063387`). Saw has `&self` and `&var self` and nothing else — a by-value receiver is refused AT THE PARSER, before any ownership question is asked — so an op whose whole contract is "this call consumes the receiver" has to be spelled either as a `&var self` method that disarms a sentinel (what the tree does, and what `PipeRequest.reply`, `PipeReply.resolve` and every other funnel already do) or as a method on some OTHER receiver taking the value by move (`Waiter.give`, `Process.give`). Neither spelling can be checked at compile time: a caller may call a disarmed wrapper again, and only the kernel's handle generations catch it. Minimal repro, one file, hosted:
  ```saw
  struct Owned { w: Int }
  extension Owned: NoCopy { func deinit(&var self) { } }
  extension Owned {
      func spend(self, n: Int) -> Int {     // Parse error: 'self' must be a reference
          var owned = move self
          owned.w + n
      }
  }
  ```
  LANGUAGE_SPEC is ambivalent about it, which is what made this look like a spelling problem rather than a rule: the Gotchas section says "a bare `self` is likewise rejected", while the concurrency section's capture rules say "A CONSUMING `self` receiver (no `&`) is an owned binding and captures by value as usual" — a sentence about a form the parser does not accept. It bit design 17 because the unit's reviewed §API spells `reply_wait(self, …)`, and the two things the user ruled — the op lives ON THE REQUEST, and the receiver is consumed — are jointly unwritable today; the placement won, and the consume is the sentinel discipline. Workaround in-tree: `&var self` + disarm before the syscall, which is the transfer-funnel contract this tree already documents at `sos.floor`. Resolution: either allow a by-value receiver (which would make single-use a COMPILE error at every funnel in this kernel, rather than a runtime fault the generations diagnose), or strike the concurrency section's sentence and say once, in one place, that a consuming receiver is spelled as a by-value PARAMETER on another type. **FIX IN FLIGHT (user, Sep 1): sawlang grows a `consumes` effect — closes at the FOURTH pin bump.** The ruled syntax: `func f(&var self, ...) consumes { ... }` on the definition (the effect slot, receiver unchanged — exclusivity is the entry requirement, consumption the exit); call sites spell `(move obj).f(...)` (moves stay written — stronger than Rust's invisible-at-the-call consumption). THE SEMANTICS, RULED (user, Sep 1, second round): a `consumes` body SUBSTITUTES for the custom deinit body — the teardown pipeline today is custom-deinit-body -> synthesized member drops, and a consumed path is consumes-body -> synthesized drops FOR THE NON-MOVED MEMBERS; the custom deinit body never runs on that path. MULTIPLE fields may be moved out (partial moves stay banned everywhere outside a consumes body); no restriction on hand-written-deinit types is needed, because the substitution model IS the answer — the custom body is simply not on the consumed path. One note left open upstream: the foreign-module fence (a consumes method on a type with a custom deinit suppresses teardown the author wrote — declaring-module-only, or lean on field privacy). At closure: `reply`/`resolve`/`reply_wait` definitions gain `consumes`, their call sites gain `(move ...)`, THE NO_HANDLE SENTINEL RETIRES AT CONVERTED SITES (the consumes body makes the syscall; the handle word's synthesized drop is a no-op; no release fires), and the compile-time use-after-consume error this entry names becomes real. `Waiter.give` is untouched (by-value PARAMETERS were always legal; the gap was receiver-position only). **CLOSED (fourth pin bump, sawlang 0.4.0 @ `46eebb36`, Sep 2):** design 260 landed `consumes` and the conversion sweep executed. WHAT CONVERTED: `PipeRequest.reply` and `PipeRequest.reply_wait` — the two ops that consume the obligation on EVERY path they can take (`reply` on `Ok` and on `PeerClosed`; `reply_wait` on `Ok` and on both legs of `ReplyWaitError`) — now carry `consumes` in the effect slot beside an unchanged `&var self`, and their 22 call sites across 13 test packages — 20 `reply`, 2 `reply_wait` — spell `(move obligation).reply(...)`. There are no sysapi-internal callers of either: a tree grep found every one of the 22 in `tests/`, so the sweep never touched `kernel/`, `root/` or `rt/`. THE SENTINEL RETIRED AT BOTH: `reply_body` and `reply_wait_body` no longer take the wrapper by reference and no longer write `PipeRequestHandle(NO_HANDLE)` — they take the WORD, which is the shape `post_body`/`call_body`/`take_into` beside them already had, so the file's helper surface is uniform again. The consuming body substitutes for the custom `deinit` body on the consumed path, the handle field's synthesized drop is a no-op, and no release fires; the custom `deinit` STAYS, because an unconsumed obligation still has to release. WHAT THE COMPILE ERROR NOW CATCHES, both witnessed by probe against the pinned sawc: a second use is ``error: use of moved variable `obligation` `` + ``hint: value was already moved at line 76``, and a bare call is ``error: `reply` consumes its receiver — write `(move obligation).reply()` `` + a hint naming the moved-from binding and the `var` revival. `resolve` DID NOT CONVERT, and the argument is this file's own documented departure read forward: a `resolve` that answers `Ok(None)` consumed NOTHING, so it consumes on two paths out of three, and `consumes` consumes on every path — spelling it that way would spend a live claim at the first empty poll, which is the one thing a poll loop must survive. Its contract is consume-on-success-or-terminal-failure and the effect cannot express it, so `PipeReply.resolve` keeps `&var self` and the disarm decided by the ANSWER. Splitting it into a non-consuming poll plus a consuming resolve was considered and DECLINED: it gives one op two typed doors for a distinction the kernel already draws in the status, and §2.1's ratified surface is one `resolve`. NEGATIVE-TEST CONSEQUENCE, one case and not the two the sweep expected: `pipe-no-reply` uses its receiver ONCE (its fault is a RIGHTS refusal, not a double use) and needed only the `(move ...)`; `pipe-dead-claim` double-uses `resolve`, which did not convert, so its proof is untouched and it is now the ONLY place in the suite that can show the ledger's `BadHandle` from Saw. The case that broke is `pipe-reply-wait-dead`, which used `reply` and then `reply_wait` through one binding — a use-after-move now. A minted sibling cannot restore that proof (a sibling's entry SURVIVES the original's consume, so it answers `PeerClosed`, not `BadHandle`) and the raw altitude cannot be reached from a test without either widening the facade's floor re-export or giving a userspace package a `sosabi` dependency for the record layouts, which is the vDSO wall. So the case was RETARGETED to the property its own error enum owes and nothing in the suite exercised: the REPLY LEG firing — a client that abandoned its claim, answered through the fused op, against a Waiter with NOTHING attached, so a park would have been `every thread blocked` and reaching `done` is the proof that no park happened. That is design 17's "one dead client cannot stall a server loop" sentence, executed. Its three transcript rows moved and the case went from a fault case to a clean-exit one; the kernel-side `BadHandle` is untouched and still stands for a raw-altitude caller. GIVE-INTERNALS NOTE (recorded, deliberately not built): the four `Waiter.give` overloads and the other transfer funnels still disarm by hand, because `consumes` is a RECEIVER-position effect and they take their wrapper as a by-value PARAMETER. A private `consumes` dissolve helper on each wrapper (`(move owned).dissolve()` answering the word) would retire those too — SL-3's remainder, filed as a backlog item above.
- SL-17 — `move` OUT OF A **PLACE**-MATCH ARM COMPILES AND DOUBLE-DROPS: no diagnostic at all, and the moved-from payload's `deinit` RUNS A SECOND TIME when the matched-on value dies (design 18, `kernel/sysapi/src/pipe.saw`, sawc 0.4.0 @ `46eebb36`). Matching a `&var` place and writing `move o` in an arm yields a working value AND leaves the enum believing it still owns the payload; for a handle wrapper that is a DOUBLE RELEASE of a live capability, which the kernel's generations turn into a `BadHandle` fault in whichever unrelated code next holds that table slot. Probed with a drop counter, hosted, three files — the move variant drops TWICE, the same arm without the move drops ONCE, and the design-around drops once:
  ```saw
  unsafe static var DROPS: Int = 0
  struct Owned { w: Int }
  extension Owned: NoCopy { func deinit(&var self) unsafe { DROPS = DROPS + 1 } }
  enum Slot { case Empty, case Full(o: Owned) }
  extension Slot: NoCopy {}

  func take_it(s: &var Slot) -> Owned {      // compiles clean
      match s {
          case Empty -> { Owned(w: 0) },
          case Full(o) -> { move o },        // <-- the payload is NOT retired
      }
  }

  func main() unsafe {
      var s = Slot.Full(o: Owned(w: 7))
      var got = take_it(&var s)
      let _ = move got                       // drops=1  (correct so far)
      let _ = move s                         // drops=2  <-- the double drop
  }
  ```
  Control (`case Full(o) -> { o.w }`, no move): drops=1. Design-around (take the whole enum BY VALUE — `func take_it(s: Slot)`, `var owned = move s`, then match the OWNED LOCAL): drops=1, so an owned-local match consumes its payload correctly and only the PLACE form is unsound.
  **A SECOND FACE, FOUND IN THE SAME UNIT'S REWORK (Sep 2) AND NOT A `move` PROBLEM AT ALL: a NESTED place-match double-drops with no `move` written anywhere.** Match a place, bind a NoCopy payload, then make THAT BINDING the scrutinee of a second match and read a plain field of the inner payload — the intermediate binding is materialized and dropped at the arm's end while the outer place still owns it. Probed with the same drop counter: one read-only `peek` costs one spurious drop, two peeks cost two. It bites for real: `reply_len(r: &WaitResult)` written as ``match r.what { case Reply(outcome) -> match outcome { case Data(msg) -> msg.len, … } }`` released the outlet a reply was carrying, and the case that then spent it faulted `bad handle`. What does NOT materialize, probed alongside: BORROWING the arm binding onward — passing it as a `&` argument (`inner_w(&inner)`) or using it as a `&self` receiver (`inner.w()`) — both cost zero drops. So the in-tree discipline is: a place-match arm binding may be borrowed onward, never re-matched; where a nested read is wanted, the inner type publishes a `&self` accessor (`ReplyDelivery.len()`/`.byte(i)`/`.arrived()` exist for exactly that, and say so).
  **RESOLUTION: REFUSAL, RULED (user, Sep 2).** Option (a) — you cannot move a field or payload out of a BORROWED reference, uniformly; the place-match arm was a missing check rather than a missing semantics. The partial-move-tracking alternative was weighed and REJECTED: partial-move-through-a-borrow stays banned everywhere, so marking the payload moved-from and skipping the enclosing drop would carve out a special case the rest of the language does not have. `take()`, `swap_out` and the owned-local match remain the outs. ONE BOUNDARY, because the two rules meet: design 260's `consumes` carve-out is the licensed exception — a `consumes` body moves its own fields out because the effect marks the receiver DYING-OWNED rather than borrowed — so the refusal and the carve-out compose rather than conflict. The nested-place face wants its own answer in the same ruling: an arm binding is a borrow, so re-matching it must borrow too rather than materialize.
  **AND THE DESIGN-AROUND IS RETIRED, BY A SECOND USER RULING THE SAME DAY.** The paragraph that used to sit here said design 18's §API shape — a payload enum owning its wrappers — was unwritable; it was not. What SL-17 forbids is the INVALID construction, and the LICENSED one already exists: `WaitPayload` carries `Reply(outcome: ReplyDelivery)` and `Message(msg: PipeMsg, request: PipeRequest)`, `WaitResult` is `{key, what}` again, and extraction goes through a `consumes` accessor (`WaitResult.open(&var self) consumes -> (UInt, WaitPayload)`, an unconditional multi-field move-out) whose result is matched OWNED — the path this entry's own probe verified sound. The `message`/`request` optional fields are gone. Probed before it was written: the tuple-of-moved-fields return compiles under sawc 0.4.0 and drops each payload exactly once, so the ruled fallback (optionals inside the case, extracted with `Optional.take()`) was not needed and the type invariant is kept rather than traded away.
- SL-18 — THERE IS NO SIZE-OPTIMIZATION LEVEL: `sawc --help` offers exactly one optimization flag, ``-O0  Disable optimization passes (emit raw codegen output for debugging)``, and nothing that ASKS for smaller code — no `-O1/-O2/-Os/-Oz`, no per-build tradeoff of speed for image size (design 20, sawc 0.4.0 @ `46eebb36`). Probe: `sawc --help`, plus `llvm-size` on the riscv32 kernel image — `.text` 360,624 B, `.rodata` 27,004 B, `.data` 14,224 B, `.bss` 152,176 B. It is filed from sawos rather than as a nicety because on an MCU-class target it is the difference between a port and a park: design 20's ESP32-C3 unit parked on a 392.4 KiB loadable image against 400 KiB of on-chip SRAM, and the ONE build-side lever that could have closed a gap that size does not exist. The shape of the `.text`, measured with `llvm-nm --print-size --size-sort`, is a long tail with a heavy shoulder rather than a hot spot: the largest single function is 35,134 B (`end_process`), the next four are 23,294 / 19,764 / 17,400 / 16,642 B, and the top twenty together are 209,470 B — 58% of `.text` — with the remaining 42% spread over hundreds of mid-size functions. So no excision closes the gap and the lever wanted is whole-program codegen policy. Workaround in-tree: NONE — the alternatives are all source-side (sawos's own lazy-decode lever, filed in [BACKLOG], and the ESP32 family's XIP execution model, which moves the tax to flash rather than removing it). Resolution shape: an optimization-level flag that reaches LLVM's `optsize`/`minsize` function attributes and the matching pass pipeline, selectable per build so a freestanding MCU profile can ask for it without changing what the hosted profile does; the useful reporting companion is a per-module `.text` attribution the caller can diff across a change, since "which module grew" is currently only answerable with `llvm-nm --size-sort` on the linked image. **FIX IN FLIGHT (user, Sep 2, relayed): sawlang size work queued after its current run — probe bundle (llvm-size --format=sysv, llvm-nm --size-sort tail-40, --emit-bt-table: 138 B, zero frames) sent upstream; closes at a future pin bump. At closure: re-measure the C3 fit and record whether copy-to-SRAM becomes viable — the XIP ruling stands regardless (it is the family's execution model, not a size workaround). LANDED UPSTREAM Sep 3 (sawlang design 263, HEAD 04b0258d): ~67% reduction measured by the user on process_isolation.elf — codegen quality (addressed elements, scalar field loads), NOT an -O flag, so this entry's literal resolution (a size-optimization LEVEL) may narrow rather than close; decide at adoption. ADOPTED at the fifth pin bump (sawlang 0.5.0 @ `6269266d`, Sep 3 evening): suite re-baselined 238/238 both arches (495 lines, hash `801a0f98fd521978…`), every image-size row moved as expected. THE MEASUREMENTS: riscv32 kernel `.text` 360,624 → 105,406 B (−71%, and that is WITH M5's additions — per-process tables, the allocator, slab chains, the linmap); `.rodata` 27,004→24,084; `.bss` ~unchanged. sosimg shrink is smaller (~10%, e.g. process-isolation 33,728→30,352 B) because a sosimg is packed loadable segments, not the ELF. C3 RE-MEASURE: loadable ≈142 KiB + .bss ≈147 KiB ≈ 289 KiB against 400 KiB SRAM — COPY-TO-SRAM BECOMES VIABLE (~110 KiB headroom before root images), though XIP stands as the family's execution model (design 20's ruling, unchanged); the real board re-measure belongs to whoever next runs `sos-smoke-esp32c3`. **THE ENTRY NARROWS RATHER THAN CLOSES: sawc 0.5.0 still offers only `-O0` — the literal resolution (a selectable size-optimization LEVEL reaching optsize/minsize) remains open; what closed is the URGENCY, since the codegen-quality work (261/263 et al.) removed the port-vs-park stakes.**
- SL-19 — A `u32`-SUFFIXED SHIFT STILL FOLDS IN THE SIGNED PLATFORM DOMAIN, so bit 31 of a `UInt32` cannot be written as a shift on a 32-bit target: ``constant expression -2147483648 does not fit in `UInt32` (range 0..=4294967295)``, anchored at the `<<` (design 20, `hal/riscv32-esp32c3/kernel/lib.saw`, sawc 0.4.0 @ `46eebb36`, target riscv32). `static SYSTIMER_CLK_EN: UInt32 = 1 << 31` is refused, which design 185's documented gotcha predicts and licenses — the fold is in the signed platform-`Int` domain, and on riscv32 `1 << 31` is `Int.min`. WHAT IS NOT PREDICTED is that the EXACT-TYPED spelling behaves identically: `1u32 << 31` is refused with the same message at the same column, even though a suffixed literal is documented as exact-typed and `UInt32` has 0x8000_0000 comfortably in range. So the suffix — the one lever LANGUAGE_SPEC offers for "no expected type reaches this subexpression" — does not reach the constant folder, and there is no spelling of the house style's bit-flag-as-a-shift rule that works at the top bit of a fixed-width unsigned slot on a 32-bit target. Probe: the two `static` lines above, either alone, on riscv32; both compile on a 64-bit host, which is what makes this easy to miss (the fold has 64 bits of room there and `1 << 31` is a comfortable positive). Workaround in-tree: write the literal — `static SYSTIMER_CLK_EN: UInt32 = 0x8000_0000` — with the reason at the line, while every other bit in the same file stays a shift, so the file is inconsistent exactly where the language forces it to be. Resolution shape: fold a suffixed operand in ITS OWN declared domain (the suffix is a type ascription, and `1u32 << 31` has an unambiguous `UInt32` answer), or — the wider fix — fold a const shift in the domain of the SLOT it is landing in, which is what DF-243a already does for a mixed binop's operand and what DF-240a did for the enum-flag combination. Either would let the bit-flag style hold at bit 31 as it does at bit 30.
- SL-20 — A GLOB IMPORT DOES NOT BIND A `type` ALIAS, THOUGH THE SELECTIVE FORM DOES: ``` `Handle` is not defined in `probe` `` with ``hint: available: SIZE, Tag, consume`` `` (design 26, the `sosabi` split, sawc 0.4.0 @ `46eebb36`). A module declaring `public type Handle = UInt` beside a `public static`, a `public enum` and a `public func` publishes three of those four through `import m.*` and silently omits the ALIAS — the hint enumerates the survivors, which is what makes the omission legible once you are already looking at it. It bites hardest through a FACADE, because `public import m.*` drops the alias from the re-exporting module's surface too and the failure then lands in a third file that names neither: splitting `kernel/abi/src/lib.saw` into leaves behind a glob facade turned all fifteen handle aliases into name-only types at the importer — ``argument `handle` expects `UInt` but got `MemoryHandle` `` and ``undefined function `MemoryHandle` `` in `kernel/sysapi/src/memory.saw`, a file that was not edited — so the alias lost BOTH halves of design 144's asymmetry at once: the implicit flow TO `UInt` and the explicit back-construction. Minimal repro, two files plus an entry, hosted, no facade needed (the plain glob fails identically, so this is not SL-7's re-export question):
  ```saw
  // pkg/leaf.saw
  public type Handle = UInt
  public enum Tag: UInt8 { case A = 0, case B = 1 }
  public static SIZE: Int = 8
  public func consume(h: UInt) -> UInt { h }

  // main.saw
  import pkg.leaf.*                        // error: undefined function `Handle`
  import pkg.leaf.{Handle, consume}        // compiles, prints 7
  func main() { print("{}", consume(Handle(7))) }
  ```
  Workaround in-tree: name every re-exported symbol — `kernel/abi/src/lib.saw`'s facade lists all 138 (generated from the leaves' public declarations, so the surface is provably the pre-split one), with the reason written in its header. The selective form carries an alias WHOLE, verified by probe in every position the split needed: construction, annotation, struct field, parameter, return, and the implicit widening to the underlying, two hops from the declaring module. Resolution: make a glob bind every public declaration KIND, aliases included — a `type` is a declaration like any other and nothing in design 150 or 229 says otherwise; failing that, a diagnostic AT THE GLOB naming what it declined to bind, since the current one fires at a use site in a module that may not import the alias's declarer and cannot see that a glob was involved.
- SL-21 — A STRUCT-TYPED `static` MAY NOT BE A REPEAT LITERAL'S VALUE, THOUGH AN INTEGER-TYPED ONE MAY, AND THOUGH THE SAME STRUCT LITERAL WRITTEN INLINE COMPILES: ``static `C` must be initialized by a compile-time constant`` (design 32, `kernel/core/waitables.saw`, sawc 0.4.0 @ `46eebb36`). The hint that comes with it lists "an earlier module `static`" among the things a constant expression may name, which is what makes this look like a bug rather than a rule — the entry is a `static` and it IS earlier. Minimal repro, one file, hosted:
  ```saw
  struct Slot { a: Int, b: Int }
  static N: Int = 4
  static ZERO_INT: Int = 0
  static ZERO_SLOT: Slot = Slot(a: 0, b: 0)

  unsafe static var A: [Int; N] = [ZERO_INT; N]          // compiles
  unsafe static var B: [Slot; N] = [Slot(a: 0, b: 0); N] // compiles
  unsafe static var C: [Slot; N] = [ZERO_SLOT; N]        // error
  ```
  So the repeat literal's VALUE position accepts a struct literal and an integer static but not a struct static, and the three are equally constant. It bites where a slab's zero element is wanted by name: design 32 converts nine kernel slabs whose initializers each repeat a zero slot, and naming that slot once per kind (`static ZERO_EVENT: EventSlot = ...`) is exactly what the size-in-one-place idiom would suggest. Workaround in-tree: write the struct literal INLINE inside each repeat, which is what the arrays already did before the conversion, so nothing regressed — but the nine initializers now repeat their field lists in the one place a name would have read better. Resolution: let a repeat literal's value name a `static` of any constant-constructible type, exactly as it names an integer one — or, if the restriction is deliberate, say which types the value position admits and make the hint stop advertising "an earlier module `static`" for a case it does not accept.
- SL-22 — A `static` DECLARATION'S INITIALIZER CANNOT WRAP AFTER THE `=`, WHICH FORCES A LONG GENERIC TYPE ONTO ONE OVER-LENGTH LINE: ``Parse error at 289:72: Unexpected token: NEWLINE`` (design 32, `kernel/core/waitables.saw`, sawc 0.4.0 @ `46eebb36`). A newline ends a statement, and neither spelling of the wrap is available, so a declaration whose type must be written TWICE — which is every generic `static`, since constructors do not infer type arguments (design 93/105) — has no legal way to fit a line budget:
  ```saw
  unsafe static var EVENTS: Slab<EventSlot, MAX_EVENTS> =
      Slab<EventSlot, MAX_EVENTS>(...)      // error: Unexpected token: NEWLINE
  unsafe static var EVENTS: Slab<EventSlot, MAX_EVENTS>
      = Slab<EventSlot, MAX_EVENTS>(...)    // same, at the other break
  ```
  Only the argument LIST wraps, because it is inside `(`/`)` (design 129), so the head `NAME: T<...> = T<...>(` must be one line. In-tree that is 100–118 characters on nine declarations against a file that otherwise holds ~80, which is the whole cost — the code is correct and reads fine, it simply cannot be formatted. This is DF-172d's shape (unbracketed expressions do not wrap) at a declaration rather than at a binary operator, and a `type` alias is not the way out: an alias over a struct is a DISTINCT type whose back-conversion takes one argument, not the memberwise initializer. Workaround in-tree: accept the long lines, noted at the block. Resolution: allow a break after `=` in a declaration whose right-hand side is unambiguously incomplete, which is the same judgement the bracket rule already makes — or make constructor type arguments infer from the annotation, which removes the second spelling and the problem with it.

- SL-23 — A `borrows` ACCESSOR'S LENT PLACE HAS NO ADDRESS: `&var slab[i].field` IS REFUSED THOUGH THE SAME FIELD IS ADDRESSABLE INSIDE A `&var self` METHOD: ``can only take reference to a variable, field, or array element`` with ``hint: references require an addressable location`` (design 34, `kernel/core/objects.saw`, sawc 0.5.0). Design 146's whole claim for a place accessor is that it lends the element WHERE IT SITS, and design 130's one sanctioned crossing into the unsafe tier is `(&x) as UnsafePointer<T>` — but the two do not compose, so a kernel that needs the ADDRESS of a slot's field (a message body handed to a byte mover) cannot ask the accessor for it:
  ```saw
  // EXCHANGES: Slab<Exchange, N>, Exchange { body: [UInt8; 128], ... }
  ((&var EXCHANGES[x].body) as UnsafePointer<UInt8>) as UInt   // error
  ```
  The workaround is a method on the ELEMENT type, and it works because design 261 passes every receiver by pointer, so the receiver's own field is an ordinary addressable location:
  ```saw
  extension Exchange {
      public(package) func body_addr(&var self) unsafe -> UInt {
          ((&var self.body) as UnsafePointer<UInt8>) as UInt   // fine
      }
  }
  EXCHANGES[x].body_addr()
  ```
  So the capability exists and only the SPELLING is missing — which is what makes this a wart rather than a wall: one extra declaration per element type that needs it, and the address still comes out of the slot the accessor lent. It bit exactly once in-tree (the pipe body arena becoming a field of a slab slot) and the workaround is arguably the better code, since it names the operation. Resolution: let a `&`/`&var` of a lent place's field take the address the window already refers to, on the ordinary unsafe-tier terms — the window's extent is the expression, which is exactly as long as the existing crossing's.

- SL-24 — A BARE INTEGER LITERAL DOES NOT ADOPT A PLATFORM `UInt` AT A CALL ARGUMENT, THOUGH IT DOES AT A `static` AND AT EVERY FIXED WIDTH: ``argument 2 expects `UInt` but got `Int` `` with the three-conversion hint (design 34, `tests/thread-donate`, `tests/slab-donate-free-nodes`, sawc 0.5.0). Design 257 §1 put the platform pair on the adoption list and the entry in the saw-lang digest names a `static` slot; an ARGUMENT is on the bare-literal list for every fixed width, so the gap is the intersection of the two:
  ```saw
  static N: UInt = 0
  proc.thread_create(stack_top: t, arg: 0)   // error: expects `UInt`, got `Int`
  proc.thread_create(stack_top: t, arg: N)   // fine
  SlabKind.from(raw: 0)                      // same, on a UInt-backed enum
  ```
  Low severity and the workaround is what the house style wants anyway (design 153: name the constant, do not write a magic number), so both in-tree sites became named `static`s with a comment pointing here. It is worth filing because the ASYMMETRY is the surprising part — `arg: 0` at a `UInt32` parameter compiles and at a `UInt` one does not, and nothing at the call site says which width a parameter is. Resolution: extend design 257 §1's slot list to call arguments at the platform pair, which is where every other width already adopts.
- SL-25 — A MODULE-LEVEL `static` DOES NOT CARRY MODULE IDENTITY, THOUGH A FREE FUNCTION DOES, SO TWO MODULES IN ONE COMPILATION UNIT CANNOT BOTH DECLARE ONE: ``ambiguous static `X`: defined in both `core` and `top` `` with ``hint: rename one definition, or import `X` from a single module``, anchored at the ENTRY FILE's first line (design 35, `hal/riscv32-flat/kernel/lib.saw`, sawc 0.5.0). Design 249 ruled that a free function is identified by (defining module, name) and that two modules may declare the same one; design 144 ruled the same for types. A `static` was never swept with them, and it is the one module-level declaration kind that still reserves its name PROGRAM-WIDE. Minimal repro, three files, hosted — note that `h` is declared in both modules and draws no complaint, which is the whole asymmetry:
  ```saw
  // core/lib.saw
  public static X: Bool = true
  public func h() -> Int { 1 }
  public func only_core() -> Int { 7 }

  // top/lib.saw — a PLAIN import, nothing re-exported
  import core.{only_core}
  public static X: Bool = false      // <- the error is reported for this
  public func h() -> Int { 2 }       // <- ...and never for this
  public func board() -> Int { only_core() }

  // entry.saw — names `core` nowhere
  import top
  func main() -> Int { if top.X { top.h() + top.board() } else { 0 } }
  ```
  **THE DIAGNOSTIC IS WHAT MAKES IT EXPENSIVE**, twice over. It is anchored at `entry.saw:1:1` — a file that imports one module and mentions neither the name nor the other module — so it points at the importer rather than at either declaration, and the hint's second half ("import `X` from a single module") describes a fix the entry file cannot make, since it is not importing `X` from anywhere. And the ambiguity is reported whether or not anything ever READS the name, so a module can be broken by a static it does not use, added to a module it does not import.
  **WHERE IT BIT, and it is the interesting half:** a BUILD PROFILE is exactly a module that re-implements part of another module's seam. SOS's flat profile (design 35) re-exports the riscv32 virt board's device half and answers differently about protection, and `PROT_REPLAY_AT_SWITCH` — a `public static Bool` in the shared arch module since design 27 — could therefore not be overridden by it AT ALL. `prot_reset` and its six siblings ARE declared in both modules and coexist, which is what made the failure read as a spelling problem for an hour. **In-tree resolution: the two seam values a profile must override became FUNCTIONS** (`prot_replay_at_switch()`, `prot_isolated()`), which is livable — a leaf returning a literal still inlines and folds, so `load_domain`'s replay loop still compiles away where it is dead — and which yields a rule worth stating on its own: **a HAL constant that a build profile may need to override cannot be a `static`.** The other HAL constants (`PROT_GRAIN`, `GRANT_ROW_BUDGET`, `FRAME_BYTES`, the memory map) stay `static`s because nothing overrides them; they are declared once and re-exported, which is the shape that works — and which is also why this went unnoticed for eight designs' worth of HAL work.
  What it does NOT reach: a `static_assert` operand, an array length, a const generic argument. Those want a compile-time constant, and a function is not one — so a profile that had to override a SIZE rather than a flag would have no spelling at all today, which is the sharper version of this entry and is filed here rather than met.
  Resolution shape: give a module-level `static` the identity a free function has (design 249's rule at the fourth declaration kind), so `top.X` and `core.X` are two statics and each importer resolves its own. Failing that, the diagnostic should anchor on the two DECLARATIONS and say which modules they are in, rather than on an entry file that names neither.
