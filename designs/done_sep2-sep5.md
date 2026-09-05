# SawOS — done (Sep 2 – Sep 5 2026)

Closed entries moved out of `designs/todo.md` at the Sep-5 tracker
sweep (the lead's move-at-integration flow, overdue by ten
integrations). Entries are VERBATIM as they last stood in the
tracker; each design's own As-built is the archival record, and the
commit history carries the gates.

ONE RULED DEVIATION from the tracker flow's "a partially closed entry
is never split": the M5 entry below still had unit 8 open inside it
and was split anyway — the closed unit digests moved here, and a fresh
compact M5 entry plus an explicit unit-8 pile replaced it in the
tracker. USER-APPROVED Sep 5 morning ("proceed as planned", against
the announced plan naming exactly this split); the entry had grown to
~570 lines and was drowning the tracker it lived in, which is the
condition the never-split rule did not foresee.

## [QUEUE] — M5 unit digests (all merged; unit 8 remains in todo.md)

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
  after design 28's `8f080c3`). Units 1.5→2→4, 6→6b→7 and the arena unit
  (`designs/038`) are BUILT and **unit 8 (the docs sweep) is the only one still
  open**, so this entry stays whole; its As-built findings ride there, one a
  warning for unit 3 (the gate does not witness fault CLASS — see 027).
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
  **THAT LAST CLAIM DOES NOT HOLD, AND THE ARENA UNIT FOUND IT (design 38,
  Sep 5): the C3 board smoke has been 0/3 since this unit landed.** It fails at
  the LINK — `section '.bss' will not fit in region 'SRAM': overflowed by
  10,416 bytes` — and the bisect is unambiguous: 3/3 at `a64d359`, 0/3 at
  `9026bf7` and at every commit after it, the pin bump included. The board had
  under 2 KiB of `.bss` slack and this region costs it 12,288. Design 38 did
  not retune the board (out of scope by its own brief) but it PROBED the fix
  and built two of the three levers: `ARENA_SIZE = 16K` in `esp32c3.ld` alone
  frees 48 KiB and takes the smoke back to 3/3 with 38,760 bytes of slack. See
  038 finding 1; the board's `ABI.md` and `esp32c3.ld` carry the corrected
  numbers.
  **ARENA UNIT BUILT (`designs/038`, Sep 5) — the small-board RAM levers, and
  the one unit of M5 that is about a NUMBER rather than a mechanism.** `sosrt`'s
  64 KiB ARENA leaves the SHARED runtime module and becomes a LINKER REGION:
  every user and kernel script `PROVIDE`s `ARENA_SIZE = 64K` and reserves
  `[_arena_start, _arena_end)` inside `.bss`, `rt/common_c/support.c` reads the
  pair back (Saw cannot name a linker symbol — `sink.c`'s wall, answered
  `sink.c`'s way, and the file's header gains a THIRD permanent reason), and
  `rt_alloc` keeps its cursor, its absolute-address alignment and its
  subtraction-form bound check line for line. **THE OVERRIDE IS TWO LINES AND
  NEEDED NO TOOLCHAIN CHANGE** — the brief's STOP condition did not fire: a
  package names a script of its own that assigns `ARENA_SIZE` and INCLUDEs the
  shared one, `PROVIDE` defines a symbol only when nothing else has, and
  `ld.lld` resolves an INCLUDE against the very working directory blade already
  runs it in, so the fragment spells its path exactly as the manifest would.
  THE KERNEL STACK becomes `KERNEL_STACK_SIZE` per board — **and the brief's
  premise that it was already a linker-script constant was WRONG**: it was a
  `.skip 0x10000` in each board's `boot.S`, which is the one file a board
  VARIANT (design 35's flat profile) inherits wholesale and cannot edit, so the
  knob was worth more than the brief thought. Both virt boards keep 64 KiB; the
  C3's is left at 64 KiB deliberately and noted there. Gate: baseline 374/374
  reproduced at `588ab67`, then **377/377 (127 + 127 + 123)**, and with the
  `[i/N]` denominator and the image-size column normalised out the diff is SIX
  HUNKS, every one an addition and every one the new case's own row — not one
  pre-existing case row moved on any profile. No SL entry owed (highest remains
  SL-24).
  **THREE THINGS THE LEAD SHOULD SEE**: (a) **the image-size column DOES move,
  and it is brought back rather than normalised** — every riscv32 image grows
  56–128 bytes (clustered at +96) while **133 of 134 arm64 images are
  byte-identical**, the exception being `pipe-pingpong`, whose `.text` sat
  inside 60 bytes of a page boundary and crossed it (+4096). The cause is
  measured, not guessed: `__saw_rt_alloc` reached a `static` and a `sizeof`
  that the compiler folded in as immediates and now makes two cross-TU calls
  (114 → 138 bytes), plus a 10-byte and a 22-byte accessor, plus one `.LCPI`
  entry, then the sosimg's 16-byte re-round of `.data`'s start. It is design
  32's finding from a different cause, and the only thing that would avoid it
  is LTO or a Saw spelling for a linker symbol — the toolchain change the brief
  forbade; (b) **the RAM side went the right way everywhere**: `.bss` did not
  grow in any image (it shrank 12–16 bytes on riscv32 from the removed static's
  own padding) and an 8 KiB package saves **57,344 bytes exactly** — 56 KiB,
  the arena difference and nothing else — which puts design 20's 67,600-byte C3
  child at 10,256; (c) `tests/arena-small` is **the first `Vector` anywhere in
  the SOS tree**, and it works freestanding with no ceremony, so std's
  allocating surface reaching this arena is now witnessed rather than assumed
  (its `Result<_, AllocError>` channel is dead here, because the allocator
  ABORTS rather than answering — the case asserts the abort's status, 65).
  ONE ITEM FOR UNIT 8: `spec.md`'s design-172 retrospective still says
  `support.c` is "reason 2, and reason 2 only"; there is a reason 3 now. Not
  edited, per the brief.
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

## [BACKLOG] — landed dev-tooling records

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

## [SAWLANG] — resolved upstream, adopted at pin bumps

- SL-2 — PLATFORM-WIDTH STATIC DOES NOT ADOPT A CONST EXPRESSION: `static M: UInt = (1 << BITS) - 1` over `static BITS: Int` is refused; DF-240a's adoption reaches FIXED-WIDTH slots only, and platform `UInt` is not one (design 3, kernel/abi HANDLE_INDEX_MASK — the `as UInt` workaround is written at the definition with this note). Resolution: widen const adoption to platform-integer slots, or rule the asymmetry permanent and document it beside DF-240a. **FIX IN FLIGHT (user, sawlang design 257 / DF-282a, Aug 31): closes at the NEXT pin bump (0.3.0). At closure, sweep the `as UInt` workaround at `HANDLE_INDEX_MASK` and any sibling sites the entry names.** **CLOSED (third pin bump, sawlang 0.3.0 @ `66e353da`, Sep 1):** design 257's const-adoption ladder reaches platform-integer slots; the `as UInt` and its recorded rationale at `HANDLE_INDEX_MASK` are swept (the entry named no sibling sites, and a tree grep confirmed none), and the derivation now folds unadorned.
- SL-7 — EXTENSION-METHOD LOOKUP DOES NOT FOLLOW FACADE RE-EXPORTS (designs 142/229 by design): a type's extension methods must live in its declaring module, so mutually-referential types cannot be split across files even behind a facade (design 5 finding 1 — System/Process/BootHandle are one 724-line module with three banners). Resolution is a language design question, named in the finding: an internal/forward-declaration tier, or lookup that follows `public import`. **SECOND SITE, design 6**: it also decides METHOD PLACEMENT, not just file layout. `Memory.map(into: &Process)` is unwritable — `BootHandle` carries a `Memory?` and an `IoMemory?`, so both region modules sit below `sos.system` and a `map` written in either naming `Process` is the DF-232e cycle. The funnels became `Process.map(memory:access:)` / `Process.map(iomemory:)` instead. That reads well here (it matches the `give` overloads), but the constraint chose it rather than the design. **THIRD SITE, design 8 — and this one MOVED A SPELLING THE BRIEF HAD RULED.** Design 8's ruled surface is `Waiter.add(process:, key:)`, the fourth member of the overload list `sos.waiter` already holds; it is unwritable, because `waiter` sits below `system` (its three `add` overloads read the waitables' handle fields, which is what put it above THEM) and `Process` is declared in `system` with `System` and `BootHandle`, all three mutually referential. So the surface is `Process.attach(waiter:, key:)` — design 6's flip applied verbatim — with a comment in `waiter.saw` where the fourth overload would have gone. The alternative was rejected on the same finding's other half: an `extension Waiter` written in `system.saw` IS legal under the orphan rule, but design 142 scopes extension-method LOOKUP to the declaring module plus the caller's DIRECT imports, so every consumer would have owed an `import sos.system` whose purpose nothing on the page explains. Resolution unchanged and now three-sited: an internal/forward-declaration tier, or lookup that follows `public import`. **THIRD SITE RE-LITIGATED AND UPHELD (lead, Aug 30):** a sawlang-side analysis (relayed by the user) proposed restoring `Waiter.add(process:)` via `extension Waiter` in the Process-declaring module, arguing the overload is visible to exactly the files that hold a Process. Verified against LANGUAGE_SPEC (extension scoping §142; re-export: "it does not widen extension scope") and REFUTED for this package: every consumer takes `Waiter` and `Process` through the `sos` facade's `public import`, never by importing `sos.system` directly, so the overload would be invisible at every real call site — and the same analysis's "move all typed overloads into the waitables' own modules" would break the three EXISTING `add` overloads the same way (they are visible today only because they are `sos.waiter`'s inherent API). A facade-placed extension fails separately: the body needs `process.handle`, deliberately module-private to `sos.system` (the no-raw-word surface guarantee). `Process.attach` stands. The proposal's one durable yield: the upstream resolution is now CONFIRMED to be "extension lookup that follows `public import`" — the other language-side fixes do not help a facade package. **FIX IN FLIGHT (user, sawlang-side, Aug 30):** extension lookup will follow `public import`; the user is implementing it and the seed edits (the `extension Waiter` in `sos.system`, the respelled test call sites) sit UNCOMMITTED in the working tree awaiting the pin bump that delivers it. The respell entry below is the capture; this entry closes when that bump lands. **CLOSED (Aug 30) — THE FIX LANDED: sawlang 0.2.0 @ `3f15d2ee`, delivered by the FIRST PIN BUMP.** Extension lookup follows `public import` now; the respell executed with the bump (`Waiter.add(process:, key:)` via `extension Waiter` in `system.saw`, funnel `public(package)`, `Process.attach` retired, three call sites respelled, facade header + design 8 As-built rider written). The first two sites' placements stand — the fix moves methods, not declarations — so the file-layout half of this entry is RESOLVED-BY-TOOL for methods and MOOT for declarations.
- SL-9 — A LONE RAW-BACKED ENUM CASE DOES NOT ADOPT A FIXED-WIDTH SLOT, THOUGH A COMBINATION OF THEM DOES: ``static `RO` has type `UInt32` but its initializer has type `MapAccess` `` (design 6, `tests/map-basics`, pinned sawc a82e06f4). `static RW: UInt32 = MapAccess.Read | MapAccess.Write` COMPILES and folds to 3 — DF-240a's flag-enum rule — while `static RO: UInt32 = MapAccess.Read` at the same slot is refused, so ONE bit needs an `as UInt32` projection and TWO bits do not. Minimal example: `enum E: UInt32 { case A = 1, case B = 2 }` then `static X: UInt32 = E.A | E.B` (ok) beside `static Y: UInt32 = E.A` (error). Workaround in-tree: write `MapAccess.Read as UInt32`, with the asymmetry noted at the line. Resolution: let a bare case adopt a fixed-width slot exactly as a combination does (the value is as constant either way), or rule the asymmetry permanent and say so beside DF-240a — the current state teaches that adding a second flag REMOVES a cast, which is backwards. **FIX IN FLIGHT (user, sawlang design 257 / DF-282b, Aug 31): closes at the NEXT pin bump (0.3.0). At closure, sweep the recorded workaround sites.** **CLOSED (third pin bump, sawlang 0.3.0 @ `66e353da`, Sep 1):** a lone raw-backed case now adopts a fixed-width slot; `map-basics`' `RO` lost its `as UInt32` and the recorded comment, and `pipe-no-post`'s keep-mask comment dropped its stale lone-case clause (that mask was policy, not workaround — its value is untouched).
- SL-12 — STATEMENT-POSITION `try ... catch` ON A `Result<Void, E>` CALL IS AN INTERNAL COMPILER ERROR: ``internal compiler error at src/main.saw:257:5 (TryExpr): 'NoneType' object has no attribute 'type'`` (the Aug-31 idiom sweep, sawlang 0.2.0 @ `3f15d2ee`). `try give(...) catch { ... }` as a bare STATEMENT — and the `let _ =` spelling identically — dies in sawc when the callee's Ok type is `Void`; the same catch with a non-Void Ok compiles and runs, so it is the Void, not the discard. Probe: any `Result<Void, SosStatus>` op (`give`/`start`/`waiter.add`/`timer.arm`/`interrupt.ack`/`waiter.remove`/`mapping.unmap`) under a statement-position inline catch. In-tree workaround: statement-position checks stay `match { case Ok(_) -> {}, case Err(e) -> ... }` — 75 such sites deliberately kept at the Aug-31 sweep, and CLAUDE.md's idiom ruling carries the caveat until the fix ships in a pin. Resolution: the inline catch's lowering handles a Void Ok payload (nothing to bind is not nothing to type); the guard form should be legal at statement position exactly as the binding form is. **CLOSED (second pin bump, sawlang 0.2.1 @ `8ffc5809`, Aug 31):** DF-281a fixed it fix-on-discovery; the statement-position guard form is legal, CLAUDE.md's caveat is lifted, and the 75 kept `match` sites are a queued conversion pass.
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
- SL-18 — THERE IS NO SIZE-OPTIMIZATION LEVEL: `sawc --help` offers exactly one optimization flag, ``-O0  Disable optimization passes (emit raw codegen output for debugging)``, and nothing that ASKS for smaller code — no `-O1/-O2/-Os/-Oz`, no per-build tradeoff of speed for image size (design 20, sawc 0.4.0 @ `46eebb36`). Probe: `sawc --help`, plus `llvm-size` on the riscv32 kernel image — `.text` 360,624 B, `.rodata` 27,004 B, `.data` 14,224 B, `.bss` 152,176 B. It is filed from sawos rather than as a nicety because on an MCU-class target it is the difference between a port and a park: design 20's ESP32-C3 unit parked on a 392.4 KiB loadable image against 400 KiB of on-chip SRAM, and the ONE build-side lever that could have closed a gap that size does not exist. The shape of the `.text`, measured with `llvm-nm --print-size --size-sort`, is a long tail with a heavy shoulder rather than a hot spot: the largest single function is 35,134 B (`end_process`), the next four are 23,294 / 19,764 / 17,400 / 16,642 B, and the top twenty together are 209,470 B — 58% of `.text` — with the remaining 42% spread over hundreds of mid-size functions. So no excision closes the gap and the lever wanted is whole-program codegen policy. Workaround in-tree: NONE — the alternatives are all source-side (sawos's own lazy-decode lever, filed in [BACKLOG], and the ESP32 family's XIP execution model, which moves the tax to flash rather than removing it). Resolution shape: an optimization-level flag that reaches LLVM's `optsize`/`minsize` function attributes and the matching pass pipeline, selectable per build so a freestanding MCU profile can ask for it without changing what the hosted profile does; the useful reporting companion is a per-module `.text` attribution the caller can diff across a change, since "which module grew" is currently only answerable with `llvm-nm --size-sort` on the linked image. **FIX IN FLIGHT (user, Sep 2, relayed): sawlang size work queued after its current run — probe bundle (llvm-size --format=sysv, llvm-nm --size-sort tail-40, --emit-bt-table: 138 B, zero frames) sent upstream; closes at a future pin bump. At closure: re-measure the C3 fit and record whether copy-to-SRAM becomes viable — the XIP ruling stands regardless (it is the family's execution model, not a size workaround). LANDED UPSTREAM Sep 3 (sawlang design 263, HEAD 04b0258d): ~67% reduction measured by the user on process_isolation.elf — codegen quality (addressed elements, scalar field loads), NOT an -O flag, so this entry's literal resolution (a size-optimization LEVEL) may narrow rather than close; decide at adoption. ADOPTED at the fifth pin bump (sawlang 0.5.0 @ `6269266d`, Sep 3 evening): suite re-baselined 238/238 both arches (495 lines, hash `801a0f98fd521978…`), every image-size row moved as expected. THE MEASUREMENTS: riscv32 kernel `.text` 360,624 → 105,406 B (−71%, and that is WITH M5's additions — per-process tables, the allocator, slab chains, the linmap); `.rodata` 27,004→24,084; `.bss` ~unchanged. sosimg shrink is smaller (~10%, e.g. process-isolation 33,728→30,352 B) because a sosimg is packed loadable segments, not the ELF. C3 RE-MEASURE: loadable ≈142 KiB + .bss ≈147 KiB ≈ 289 KiB against 400 KiB SRAM — COPY-TO-SRAM BECOMES VIABLE (~110 KiB headroom before root images), though XIP stands as the family's execution model (design 20's ruling, unchanged); the real board re-measure belongs to whoever next runs `sos-smoke-esp32c3`. **THE ENTRY NARROWS RATHER THAN CLOSES: sawc 0.5.0 still offers only `-O0` — the literal resolution (a selectable size-optimization LEVEL reaching optsize/minsize) remains open; what closed is the URGENCY, since the codegen-quality work (261/263 et al.) removed the port-vs-park stakes.** RE-PROBED at the sixth pin bump (sawlang 0.6.0 @ `545735ce`, Sep 4 evening): still only `-O0` — stayed open on the same narrowed terms; size at 0.6.0 was riscv32 kernel `.text` 119,638 B, `.rodata` 24,084→21,508 the perf batch's only size signal, wall flat under TCG. **CLOSED at the SEVENTH pin bump (sawlang 0.8.0 @ `449d2485`, Sep 5): design 265 lands `-O1/-O2/-Os/-Oz`, exactly the resolution shape this entry asked for** — a level set reaching optsize/minsize, selectable per invocation, with the machine outliner under `-Oz`. Adopted per the user ruling: -Oz on ALL freestanding builds (runner-driven kernel compiles + blade-built packages via the SAWC env value). MEASURED at adoption, same tree both sides: riscv32 kernel `.text` 121,596 → **76,808 B (−36.8%)**, loadable (text+rodata+data) 177,304 → 141,388 (−20.3%); `.rodata` +2,936 and `.data` +5,936 absorb outlined material; `.bss` unchanged; suite wall ~flat (~85 min). C3 RE-MEASURE: smoke 3/3, flash 147,016 / 167,496 / 169,672 B (was 177,752 / 206,424 / 212,776 — ~17-21% off), on top of the arena retune's 48 KiB of SRAM back. The reporting companion (per-module `.text` attribution) was not part of the ask's core and is not built; if it is ever wanted it files as its own entry.

## [QUEUE] — M5 closure entries (moved at M5's close, Sep 5)

- M5: the memory milestone — `designs/025`: **COMPLETE, Sep 5 2026.**
  Every rung landed behind an independent lead gate: units 1 (`027`),
  1.5 (`029`), 2 (`033`), 4 (`035`), 5 (`028`), 6 (`032`), 6a (`034`),
  6b (`036`), 7 (`037`), the arena unit (`038`) and unit 8 (`040`,
  the docs sweep that closes it). Unit 3 (riscv32 Sv32) VACATED to
  [BACKLOG] by ruling 11; two riders touched no rung — the `sosabi`
  split (`026`) and the C-leg probe (`039`). The full unit digests
  moved to `designs/done_sep2-sep5.md` at the Sep-5 tracker sweep;
  each design's own As-built is the archival record, and
  `designs/025`'s closing note ("M5 as it ran") is the milestone
  retrospective — the ladder as executed, the gate, and the four
  things the plan got wrong. **Gate at close: 382/382 = 129 + 129 +
  124** across riscv32 / arm64 / riscv32-flat (238 at the milestone's
  start; the SHAPE changed once, at unit 4, when the flat profile
  became a third run). sawlang pinned **0.8.0 @ `449d2485`** with
  **-Oz on all freestanding builds** (user-ruled; kernel `.text`
  76,808 B, −37% at adoption), C3 board smoke 3/3. **M6's anchor is
  `designs/030-m6-storage-seed.md`; its scoping session unlocks
  here.**
- THE C-LEG PROBE (`designs/039`, user-ruled Sep 4): **BUILT AND
  CLOSED.** `tests/c-child/` is a freestanding C image (main.c +
  crt0.c + a hand-rolled syscall.c, no `sos`, no `sosrt`, no arena)
  that `tests/c-hello/` spawns and asserts; case `c_hello` appended,
  gate 380/380 = 128 + 128 + 124. NO kernel, sysapi, ABI or toolchain
  change was needed and NO new runner build leg — a C package reaches
  Blade's sosimg emit through the same `[sos] native` line the HAL
  stubs use. Findings and their M7 sizing are the As-built in
  `designs/039`. **THE FOUR DOC ITEMS IT HANDED UNIT 8 ARE DONE**
  (`designs/040` §4–§5): spec §5.7's vDSO claim is qualified for C,
  both `user/ABI.md`s' "Required of a process" grew from one bullet to
  five, the typed-C altitude row now records that it is unreachable by
  an image not already linking `sos`, and the stale script count is
  corrected in SIX places — two more than the probe had looked at.
- M5 UNIT 8 — the docs sweep: **BUILT AND CLOSED (`designs/040`,
  Sep 5). M5 CLOSES WITH IT.** Gate **382/382 = 129 + 129 + 124**;
  every row that moved is enumerated in that design's §2 (four
  classes, all caused by one deleted runner key: 256 denominator
  changes, 8 of them index shifts, 2 new case rows, 2 new `.sosimg`
  size rows, the totals pair — and NOT ONE existing image size moved,
  on any profile; the flat section is byte-identical). The whole pile
  landed; `designs/040` §4–§6 is the section-by-section record, and
  three things in it are the lead's to see. **`tier_word` runs on all
  three profiles.** **THE `thread_preempt` FLAKE HAD TWO CAUSES, NOT
  ONE** — design 35's prescribed letters-only projection does NOT fix
  the run design 35 recorded, because design 158's ordered matcher
  advances past each whole match and that run's three crossings
  OVERLAP; both halves are per-case keys now (`strip_kernel_lines`,
  `overlapping_matches`), verified against the recorded transcript and
  against the no-preemption control, and CLAUDE.md's timing-rows
  paragraph is corrected. **TWO STALE ABI ASSERTS**, both design 33's,
  both the species design 35 §7 fixed one unit earlier, found by a
  mechanical audit and repaired. Design 37's row-identity follow-on is
  RECORDED in spec §2's `Process` row, not built. The `pool_base`
  CANDIDATE is DECLINED with reasons and re-filed to [BACKLOG] below.
