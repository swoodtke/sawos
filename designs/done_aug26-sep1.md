# SawOS — done, Aug 26 – Sep 1 2026

Verbatim moves from designs/todo.md at integration (the tracker flow
inherited from sawlang, stated in todo.md's header). This is the
repo's first done file; sawos was born Aug 28 (sawlang#238).

- M3 unit 1.5 — kernel interruptibility [sawlang#232 pin 1, #1 —
  designs/001-kernel-interruptibility.md]: DONE Aug 28, INTEGRATED to
  main same day (lead-reviewed, gate re-run 84/84, fast-forward
  dbd2211). Landed: `irq.preempt_point()` beside `idle_poll`
  (D-1, poll-and-deliver, no new HAL seam — `irq_poll` reused);
  `IN_DELIVERY` beside the counters with `deliver_line` split into a
  guarded entry and its body so the clear cannot be missed (D-2);
  `PREEMPT_STRIDE = 4096` and the strided long-op movers `long_copy` /
  `long_zero` in a NEW module `kcore.preempt` between `irq` and `sched`
  (D-3); `PREEMPT_PC = 1` minted distinct from `IDLE_PC` so a tick
  report says which poll delivered it; the placement audit as comments
  at all thirteen sites; spec §9 / §9b / §11 amended; two all-arch
  cases `preempt_tick` + `preempt_extirq` — 43 entries, 42/arch, 84
  runs, the 80 existing rows row-identical.
  FINDING (recorded in the brief's as-built, no workaround hidden): the
  points could NOT go inside `mem`'s byte loops as D-2 asked. A point
  delivers, and delivery reaches back down to those loops
  (`deliver_line` -> … -> `wake.notify_ready` -> `process.copy_out` ->
  `mem.copy_bytes`), so the loop would have to import `irq` from below
  it — the cycle DF-232e diagnoses. Nor can an installed `FuncPointer`
  hook route around it: a static initializer is a constant expression,
  so a mutable static holding a function address cannot be declared.
  Resolution keeps every piece single-sited — the loop in `mem`, the
  point in `irq`, the cadence in `kcore.preempt` — at the cost that a
  bulk copy issued from BELOW that module cannot take a point. Only the
  copy doors are there today (24-byte wait records, under one stride);
  §2.1's Pipe bodies are the length that will want one, and their
  callers sit above `kcore.preempt`, so the pointed door goes there in
  M4. Noted at `mem.saw`'s movers and in `kcore/lib.saw`'s module order

- M3 unit 2 — CreateProcess [sawlang#232, #2 —
  designs/002-create-process.md, authored + revised Aug 28]: THE
  LOADER STAYS IN THE KERNEL (user ruled Aug 28, superseding the
  same day's zero-copy ruling — split to userspace later if/when
  necessary): one loader, shared phases, `process_create(image:,
  memory:)` over two root-provided Memory regions, copy through
  unit 1.5's pointed movers, stack = kernel's grant at the
  destination's top (root's own pattern). Region table + minimal
  Memory kind (unit 4's first slice), boot_handle_next pulled
  forward (unit 3's iterator), protection reload at pick_next's
  marked point (forced both arches), end_process forks (root stops
  the machine, a child reschedules). DISPATCHED Aug 28.
  **BUILT Aug 28, INTEGRATED to main same day (lead-reviewed, gate
  re-run 94/94, fast-forward e18c1ef).** All of D-1..D-7
  landed as briefed; the brief's As-built has the detail. Counts:
  `SAWLANG_ROOT=… make sos-test` 94/94 across riscv32 + arm64, 47
  cases per architecture, the 84 pre-existing rows byte-identical in
  name, verdict and order. Five new all-arch cases
  (`process_lifecycle`, `process_isolation`, `process_badimage`,
  `process_doublestart`, `process_bootdrain`), two new child packages
  and five new root packages. ONE STRUCTURAL FINDING, recorded in the
  As-built and in `sched.saw`'s header: the teardown could not stay in
  `kcore.process`, because a child's death reschedules, a reschedule
  can idle, idling delivers, and delivery reaches back down into that
  module's own copy door — design 1's cycle at a second site, resolved
  the same way (an altitude split, nothing duplicated). Its visible
  cost is that the copy doors REPORT a bad buffer and their callers
  above the switch point terminate on it. TWO DEVIATIONS from the
  brief's letter, both argued in the As-built: `sos.Memory` is a plain
  handle wrapper rather than `NoCopy` (the owning tier is unit 2.75's
  whole subject, and a move-only field makes the ruled `{tag, kind,
  handle}` record unwritable), and the loader MOVED down the module
  order rather than staying last (its phases have to be reachable from
  the dispatch now that `process_create` is an op). Nothing left open
  inside the entry

- DESIGN 2 RIDER — `process_create` moves from Process to System (Aug-29
  user ruling; the record is the RIDER section at the end of
  `designs/002-create-process.md`) — DISPATCHED, BUILT, closed in place:
  `SystemOp.ProcessCreate = 4` on new `SystemRight.ProcessCreate = 1 << 11`
  minted in `root_system_rights()`, the Process op/right retired and
  `Start`/`BootHandles(Next)` renumbered down, `System.process_create` +
  `sos_system_process_create` in sysapi, four root packages and spec
  §2/§8/§12 amended. Gate re-run green, every case transcript unchanged.
  Nothing open; INTEGRATED to main Aug 29 (lead-reviewed, gate re-run 94/94, fast-forward 136cddd) [#2]

- ~~M3 unit 2.75 — handle lifecycle~~ **CLOSED Aug 29** [sawlang#232
  Aug-17 ruling, #3 — designs/003-handle-lifecycle.md]. All of D-1..D-6
  landed, both arches green (108 = 54 cases x 2). What is in the tree:
  a CONFIGURABLE split word (`HANDLE_INDEX_BITS`, default 8, in sosabi
  with mask/shift/asserts derived — the Aug-29 user note; gen 0 stamps
  nothing, so §12's 1/2/3 and every asm payload are untouched); one
  ungated universal `RELEASE_OP = 0xFFFF` intercepted between lookup
  and the kind match; mint-per-call on `ClockGet`/`ProcessSelf`/
  `ThreadSelf` with both `self_handle` caches and `clock_handle_of`
  DELETED; `Gone` process-slot reclaim on last release, closing unit
  2's `alloc_process` pend; DROP IS RELEASE — all nine wrappers
  NoCopy + deinit, no typed `release()`, `BootHandle` reshaped to
  `memory: Memory?` + `take_memory`. Seven new all-arch cases
  (handle_remint, handle_release_ungated/_nothing/_twice,
  handle_malformed_word, handle_drop_release, process_reclaim). D-6's
  authorized row set moved and NOTHING outside it; the per-case
  accounting table is in the brief's As-built. TWO FINDINGS recorded
  there (a platform-`UInt` const-adoption gap; no op mints a second
  handle onto a process's own Event/Waiter/Timer, which unit 3's
  `give` is the answer to). OPEN: nothing. INTEGRATED to main Aug 29 (lead-reviewed, gate re-run 108/108, fast-forward d3c9620)

- M3 unit 3 — give(handle, tag:) [sawlang#232 Aug-16 launch-flow
  ruling, #4 — designs/004-give.md, authored Aug 29]: give as
  unbind-and-rebind rights-verbatim, tags the only cross-process
  vocabulary, per-process boot queues, start(boot_tag:) kernel-resolved
  a0, give-before-start the frozen barrier. No authorized transcript
  changes. DISPATCHED Aug 29.
  **BUILT Aug 29, after FIVE USER RULINGS taken at review — branch
  parked.** The chain is recorded in the brief ("THE RULING CHAIN",
  amending D-3/D-4) and started from the implementation's finding that
  D-3's give-then-start sequence was not writable: `Start` is an op on
  the one handle a `give` would have moved. The answer went to the root:
  **`MINT_OP = 0xFFFE`**, a second universal op minting a SIBLING handle
  onto the same object with rights the source's INTERSECTED with a KEEP
  mask, gated on a new universal `Mint` bit. That closes design 3's
  finding 2, makes §3's attenuation something a HOLDER performs, and
  lets a launcher hand a child a MASKED SYSTEM HANDLE — so every process
  now bootstraps exactly as root does (§12 symmetry), a child can PRINT
  without owning a device, and a launcher keeps supervision while the
  child manages itself. `start(boot_tag:)`'s resolution CONSUMES its
  record. And **`Manage` is removed as a right everywhere** (specific
  rights for specific operations; `Mint` takes bit 1;
  `SystemRight.ProcessSelf`, `ProcessRight.ThreadSelf` and
  `ProcessRight.Give` replaced it), which collapsed the two Process
  default sets into one.
  Gate: 124/124 across riscv32 + arm64 (62 cases each) against a 108
  baseline at the merge base; the 108 pre-existing rows byte-identical
  in name, verdict and ORDER, eight new rows appended per architecture.
  FOR THE LEAD, three items: the uniform-`Mint`-in-every-default-set
  lean is recorded as a LEAN and is the user's to veto at integration; a
  handle cannot be narrowed below `Transfer` and still be given (so a
  receiver holds that bit — inert today, a ruling for the pipe unit);
  and `FaultReason.DuplicateKey`'s text still names attachments,
  unchanged because `event_dupkey` asserts it and no expectation edit
  was authorized. The `event-wake` / `event-consume-wake` rewrite that
  `MINT_OP` now makes possible is BACKLOG, deliberately not done here —
  it would move shipped rows
  INTEGRATED to main Aug 29 (lead-reviewed, gate re-run 124/124, fast-forward 355ca4b).

- ~~sysapi split~~ **BUILT Aug 29, INTEGRATED to main same day (lead-reviewed, gate re-run 124/124, empty transcript and symbol diffs, fast-forward b7b72a2)** [user-requested
  Aug 29, #5 — designs/005-sysapi-split.md]: kernel/sysapi/src/lib.saw
  becomes the kcore-style facade over per-object files, declared order,
  zero behavior change, every row byte-identical. DISPATCHED Aug 29.
  2,000 lines became ten modules plus the facade — floor, memory,
  thread, event, interrupt, timer, clock, waiter, system, rt, lib —
  with bodies moved by line range so every docstring and statement is
  byte-identical. Acceptance met in full: 124/124 both arches with the
  WHOLE transcript byte-identical to the merge base (no size movement
  to account for, build-info lines included), and the exported `sos_*`
  symbol list identical across four packages x two arches x every `.o`
  and `.elf`. FIVE FINDINGS in the brief's As-built, two of them
  structural: `System`/`Process`/`BootHandle` cannot be three files
  (each pair names the other, and a facade re-export widens no
  extension scope, so there is no `process.saw`), and a locally
  DECLARED type name beats the prelude where an IMPORTED one ties with
  it — `Thread` vs `std.task`'s `Thread<T>`, which is why `system.saw`
  writes `thread.Thread`. Also: the whole per-op C surface had to stay
  with the syscall externs (an `extern "C"` decl is private by
  construction and cannot be shared), `waiter` had to move above the
  three kinds it attaches, and the facade needs one plain
  `import sos.rt` because that module publishes nothing to re-export.
  OPEN: nothing. Closes in place; the lead moves it at integration

- ~~M3 unit 4 — Memory/IoMemory/Mapping~~ **CLOSED Aug 29, INTEGRATED to main same day (lead-reviewed incl. the Gone-slot fix and compaction, gate re-run 134/134, fast-forward b759005)** [sawlang#232
  §2.5, #6 — designs/006-memory.md]: a Mapping IS an installed grant
  row, and the unit is small because of it — the grant record unit 2
  built for the SCHEDULER is the record `map` edits. LANDED: three
  kinds (`ObjType.IoMemory = 10`, `Mapping = 11`) with `Split`/`Map`,
  `Carve`/`Map`, `Unmap`; `MemoryRight.Split|Map`, `IoMemoryRight`,
  `MappingRight`, `ProcessRight.Map` (two rights on two objects at a
  map); `MapAccess` as public API and access PER MAPPING, so
  double-mapping RO+RW is allowed; the LIVE-DOMAIN RULE
  (`domain_changed`, one helper, both ops) with unmap compacting the
  record and fixing up the Mapping slab's stored row indices; the PMP
  budget widened 4→8 TOR regions / 16 entries (`MAX_ROOT_SEGMENTS` 3→7,
  four cfg shadows, four-way `stage_cfg`, `sink.c` cases 8..15 and a
  four-word `sos_pmpcfg_write`); per-arch `GRANT_ROW_BUDGET` +
  `map_target_ok` + `device_window_ok` (a map outside a window is a
  caller-visible `BadArg`, not the HAL's kernel-bug stop); region table
  VERSION 2 with a kind column (24-byte rows, `len == 0` short-circuit
  kept ahead of every header read); the uart-echo pair migrated to
  obtained-not-declared, same bytes and same line number; five new
  proof cases + one child package. DEVIATIONS FROM THE BRIEF, both
  recorded with reasons in the As-built: unmap does NOT free the
  Mapping slot (a slab slot freed under a live handle is reachable
  through it when the next map reuses it — the aliasing generations
  cannot cover, since a generation lives in the handle ENTRY), and the
  map funnels are `Process.map(memory:access:)` /
  `Process.map(iomemory:)` rather than `Memory.map(into:)` (design 5
  finding 1 again — `BootHandle` carries both region kinds, so both
  modules are below `system` and a `map` naming `Process` there is a
  cycle). Free-on-last-ref stays explicitly unit 5's.
  GATE: 134 rows green on both arches (124 + 10 new, nothing removed).
  The 124 pre-existing, bucketed mechanically: 37 byte-identical, 82
  differing ONLY in an address the linker or the kernel chose (`entry=`,
  `epc=`/`elr=`, a tick's `at 0x…` — the `sos` module and the kernel
  both grew, so every root image's entry and the appended payload
  moved; `tval=` and every asserted field are unchanged), the 2
  AUTHORIZED uart-echo migrations accounted line by line, and 3 rows
  carrying values the harness deliberately does not assert
  (`thread_preempt`'s interleaving ×2, `timer_interval`'s coalesced
  fire count). Full account in the brief's As-built

- M3 unit 5 — quotas + the reference count [sawlang#232 unit 5 +
  agenda 8, the Aug-29 refcount conversation as D-1; #7 —
  designs/007-quotas.md, authored Aug 29]: per-slot refs (handles +
  attachments, per-kind matrix), synchronous free-on-zero with credit
  at the free site, creator-pays (mappings charge the TARGET — lead
  ruling), QuotaExceeded=7 as the policy answer distinct from
  NoResource, agenda-8's quota≤wall assert executable, orphan
  write-off degenerate-by-construction v1 (nothing gives upward),
  design 6's mapping-slot deviation resolved. Third authorized
  transcript movement (free-on-zero moves teardown counts; per-case
  accounting owed). DISPATCHED Aug 29.
  **CLOSED Aug 29 — BUILT. Gate: 144 passed across riscv32 + arm64
  (72 cases each), against a baseline of 134.** Every D landed; three
  shapes deviate from the brief's letter and each is argued in the
  As-built (the mapping row counts installed ROWS rather than Mapping
  objects, so agenda 8's assert is provable; root's rows are
  `QUOTA_UNLIMITED` rather than the slab sizes, so the machine keeps
  its own voice; the ledger is per-process storage in `kcore.objects`
  rather than a `ProcessSlot` field, because the handle row is charged
  inside `mint_handle`). **AND THE THIRD AUTHORIZED MOVEMENT TURNED
  OUT TO BE EMPTY**: NO teardown count moved, on either arch — the
  suite's pre-exit drops are all of Clock handles (exempt) or of kinds
  the teardown line has no column for. The one asserted line that moved
  is `memory_split`'s, whose claim design 6's finding 5 tied to this
  unit by name. New finding: D-4's "no orphan can exist in v1" is
  false for a Mapping charged to its TARGET, and the row-based
  accounting is what closes it. See designs/007-quotas.md's As-built
  INTEGRATED to main Aug 29 (lead-reviewed incl. the four deviations — all accepted, two of them corrections to the brief; gate re-run 144/144, fast-forward e293648).

- M3 unit 5.5 — death notifications [sawlang#232 agenda 9, #8 —
  designs/008-death-notifications.md, authored Aug 30]: Process
  becomes the fourth waitable (not a new kind), WaitTag.Process=3
  carrying the §8 status word, TERMINAL LEVEL (stays signaled,
  attach-after-death wakes, consume clears nothing), end_process
  notifies after status-record before the fork, the attachment joins
  the Process refcount row (design 7 composition), deadlock predicate
  deliberately untouched with the reasoning recorded. No authorized
  transcript changes. DISPATCHED Aug 30. **CLOSED Aug 30 — BUILT, gate
  green on both profiles.** All of D-1 landed as ruled: the five matrix
  arms, `waitable_slot`'s Process arm replacing its `NotWaitable`
  refusal, `notify_ready` in `end_process` after the status record with
  the wake-only-queues note at the site, the attachment counted in
  `ProcessSlot.refs`, and `has_external_wake_source` untouched with the
  three-case reasoning written at the predicate. Three cases —
  `death_notify` (no timer armed anywhere: the death is the only wake
  source), `death_fault` (the same park, `Faulted` instead of `Exited`,
  reusing `child-fault`), `death_late_attach` (`first=65541
  second=65541`, then `held=1 freed=1` — the attachment alone holds the
  dead slot until it is removed) — plus one new child, `child-bye`. Gate
  150 passed; of the 144 baseline rows, 103 byte-identical, 40
  address-only (`entry=` alone), 1 documented-nondeterministic
  (`thread_preempt`'s interleaving, which the case's own comment records
  as timing-dependent and which asserts direction changes, not a
  sequence), and ZERO authorized changes. THREE FINDINGS worth the lead's
  eye, all in the
  As-built: (1) SL-7's third site MOVED THE RULED SPELLING — the brief's
  `Waiter.add(process:, key:)` is the DF-232e cycle, so it is
  `Process.attach(waiter:, key:)` on design 6's precedent; (2)
  `kcore.waitables` now sits ABOVE `kcore.process`, the unit's one
  altitude change; (3) a Process attachment is the first CROSS-PROCESS
  attachment, so `end_process`'s attachment sweep had to start unhooking
  the far end
  INTEGRATED to main Aug 30 2026 (lead-reviewed, gate re-run
  150/150, fast-forward a3d5fc2)

- First `sawlang.pin` bump — expected after sawlang design 218 unit 1.5
  (monomorphization) lands; bump version + sha TOGETHER [sawlang#238
  D-b2]
  CLOSED Aug 30: the first bump happened for sawlang 0.2.0 @
  `3f15d2ee` (SL-7's fix — extension lookup follows `public
  import`), ahead of the 218/1.5 expectation. version + sha
  bumped together per D-b2

- `Waiter.add(process:, key:)` respell — a PIN-BUMP EVENT, sawlang-side
  [SL-7; #8 As-built finding 1; ruled by the user Aug 30]: once the
  sawlang fix (extension lookup follows `public import`) ships in a
  pin bump, design 8's ruled spelling becomes writable: `extension
  Waiter` in `sos.system` adds `add(process:, key:)` (it can read
  `process.handle` there — Process's module), the raw funnel
  `attach(target:, key:)` in `waiter.saw` widens to `public(package)`
  (NOT `public` — the no-forged-handles story rests on the raw form
  being unreachable from user code), `Process.attach` retires, the
  death tests respell, spec §2.2/§8 surface sentences + design 8
  As-built finding 1 amended (rider), SL-7 closes. Seed edits sit
  uncommitted in the lead's working tree (user, Aug 30). Transcript
  expectation: nothing beyond address-only
  CLOSED Aug 30 — EXECUTED with the first pin bump (sawlang
  0.2.0): everything in scope landed as written, except that no
  spec sentence names the method spelling, so no spec edit was
  owed. INTEGRATED to main with the bump commit (lead)

- M3 unit 6 — the money shot [sawlang#232, #9 —
  designs/009-driver-child.md, authored Aug 30]: child echo driver
  from config both arches + shared-memory double-map demo +
  MemoryRight.MapExecute exec gating (ruled Aug 30) + W|X-in-one-row
  refusal. **CLOSED Aug 30 — BUILT, and the M3 LADDER IS COMPLETE.**
  A DRIVER IS A CHILD PROCESS: root drains the console UART's register
  page as an `IoMemory`, `give`s it to a child (the new
  `Process.give(iomemory:)` funnel, over a `Transfer` bit
  `iomemory_rights()` has minted since unit 4), and the child maps it
  into itself, binds the line, enables the device and echoes the
  harness's bytes — both profiles, driver body byte-identical to the
  root-as-driver twins, which STAY unmoved. Beside it: SHARED MEMORY
  (one region, two address spaces, the launcher installing the child's
  row so the child holds no region object — `handles=2` in its teardown
  line is that claim counted), and EXECUTABLE AS A RIGHT
  (`MemoryRight.MapExecute = 1 << 10` gating `MapAccess.Execute` per
  handle, `memory_rights()` minting it of necessity since attenuation is
  monotonic, and `Write | Execute` in ONE row refused `BadArg` beside
  write-without-read). New surface: `Memory.mint(rights:)` and a
  `MemoryRight` re-export, which is where a launcher's memory policy is
  now written. Docs: spec §2.5 (the amendment + both new flows), §9's
  driver-child chapter, §11's M3 entry (the ladder complete) and the
  `MemoryObject`/`IoMemoryObject` rows, `memory_rights()`'s docstring.
  Gate: 158 passed across both profiles (79 cases each) — 150 baseline
  rows accounted (146 byte-identical, 1 address-only where the kernel's
  own trapping PC moved because `kcore.dispatch` grew, 3
  documented-nondeterministic: both `thread_preempt`s and
  `timer_interval`'s unasserted coalescing count) and 8 new. The
  authorized-with-cause bucket the brief anticipated for
  `memory_rights()` growing a bit is EMPTY: no program in the tree
  prints a Memory rights word. ONE DEVIATION, argued in the As-built: the exec-gate proof is TWO
  cases (`map-exec-gated`, `map-wx-refused`) rather than the brief's one
  with three arms, because two of those arms are FAULTS and a fault ends
  the process — each case's positive arm now motivates its own refusal.
  ONE FINDING for a later ruling: a launcher CANNOT withhold
  `ProcessRight.InterruptBind`/`Map` from a child, because those arrive
  on the handle `SystemOp.ProcessSelf` mints and a keep mask reaches only
  the child's SYSTEM handle — narrowing them wants `ProcessSelf` to take
  a mask of its own, which nothing in v1 needs
  INTEGRATED to main Aug 30 2026 (lead-reviewed, gate re-run
  158/158, fast-forward f336999) — M3's LADDER IS COMPLETE;
  unit 7 (docs sweep) is the milestone's close

- M3 unit 7 — the docs sweep — **CLOSED Aug 30** [sawlang#232 item 7, #11 —
  designs/011-m3-docs-sweep.md]: all six owed items landed. §3 REWRITTEN
  around NO AMPLIFICATION (the Jul-29 no-duplicate rule quoted and
  superseded clause by clause, the three facts that hold the invariant,
  the clock worked example, `MINT_OP`, and unit 6's exec gate as
  policy-by-mask); §2.5 aligned to the built names (`Memory`/`IoMemory`/
  `Mapping`, split/carve/map/give) with the two stale `§2.3` pointers
  fixed; §5.7 amended (rights-gated ops, boot set one handle wide at the
  register, the two copy funnels as the only user-memory doors); the
  DMA-TCB note placed at §2.5 with its two prerequisites; §11 refreshed
  whole (M3 ledger unit by unit, 79 cases/arch and 158 runs, M4 pointed at
  designs/010, a DMA/IOMMU row added, the pins restated). Consistency grep
  caught 26 stale sites — 16 in spec.md (one of them the 21 `sos/`-prefixed
  paths the Aug-28 flattening left behind) and 10 in doc comments, all
  fixed; the full list is in the As-built. Gate: 158/158, EVERY row
  byte-identical to the merge-base transcript. M3 CLOSES with this
  INTEGRATED to main Aug 30 2026 (lead-reviewed, gate re-run
  158/158, fast-forward f8bd8ae) — **M3 IS DONE**: units 0-7
  integrated, eleven object kinds, Pipe the one §2 row left,
  M4's plan of record is designs/010

- M4 unit 0 — waiter revocation [#10 ruling 1, #7 finding 2, #12 —
  designs/012-waiter-revocation.md, authored Aug 31]: SosStatus.Revoked
  appended; free_object's Waiter arm walks `blocked` and wakes every
  parked thread with it, no record written; the peer-gone doctrine's
  first instance. Existing rows unmoved; new case rows only.
  **CLOSED Aug 31 — LANDED AS BRIEFED.** `SosStatus.Revoked = 8`
  appended to `sosabi` with a `describe()` arm ("what this call was
  waiting for is gone"); `kcore.refs` gained `revoke_blocked`, called
  from `free_object`'s Waiter arm between the attachment cascade and the
  slot clear, writing ONLY the status register (the no-record contrast is
  written at the function and at §2.2). The legal-but-doomed paragraph is
  struck and design 7's As-built finding 2 carries a RIDER (the finding
  itself untouched); the reused-slot sentence survives, moved onto the
  literal it describes. Sysapi took no new surface — one docstring
  paragraph on `Waiter.wait`. One harness case, `waiter-revoked`, three
  threads on one Waiter proving all three claims (a revocation is
  delivered; the walk is the whole blocked list; the COUNT gates it, not
  the release). Gate 160/160 across both profiles with every pre-existing
  row byte-identical. One language deficiency met and filed: SL-11
  INTEGRATED to main Aug 31 2026 (lead-reviewed, gate re-run
  160/160, fast-forward a98b0cd)
