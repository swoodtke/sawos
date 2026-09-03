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

- M4 unit 1 — the pair [#10 rulings 2/3/6, #13 —
  designs/013-the-pair.md, authored Aug 31]: PipeInlet/PipeOutlet as
  two counted kinds over one connection slab (two ref columns, both-
  zero frees), ProcessOp.PipeCreate via copy-out record, Post/Take
  data-only polling, PeerClosed=9 + WouldBlock=10, ring of
  PIPE_INFLIGHT × PIPE_BODY_BYTES slots as kcore.limits statics,
  give/boot-drain for both kinds. Everything polls; nothing parks.
  **CLOSED Aug 31 — LANDED.** Suite 170/170 (85 cases/arch, both
  profiles) from 160/160; whole-transcript diff bucketed in the
  design's As-built, and every one of the 160 pre-existing case rows is
  BYTE-IDENTICAL (the only movement in them is the `[n/80]` -> `[n/85]`
  denominator, plus image SIZES growing because every image links the
  `sos` facade and the facade now compiles `sos.pipe`). VALIDATED ON
  sawlang 0.2.1, which landed in the toolchain checkout before the
  first build, so baseline and gate used one compiler; the tree's pin
  still reads 0.2.0 and the bump is the lead's. What
  landed: two `ObjType` kinds over one `PIPES` slab row with two
  reference columns (the columns ARE the peer-gone state — a column
  can only fall, so no flag beside them), the staging ring as separate
  flat `.bss` storage rather than a slot field (2 KiB per connection
  would otherwise be a memset per slot write), `ProcessOp.PipeCreate`
  on a new `ProcessRight.PipeCreate` in the one Process default set,
  `Post`/`Take` through the existing copy-in/copy-out doors,
  `PeerClosed = 9` + `WouldBlock = 10`, `QuotaKind.Pipe` (creator-pays,
  ONE charge for the pair, credited at both-zero), give + boot drain
  for both kinds, typed `NoCopy` wrappers with `post`/`take`/`mint`,
  and the two `NotWaitable` arms unit 3 removes. FIVE cases across six
  packages: `pipe-basics`, `pipe-peer-gone`, `pipe-child` (+ the
  `child-post` child package), `pipe-no-post`, `pipe-oversized`.
  THREE DEVIATIONS, argued in the As-built: `PIPE_BODY_BYTES` lives in
  `sosabi` rather than `kcore.limits` (it is a size the two halves must
  agree on — `wait_record_bytes()`'s own reason — while `PIPE_INFLIGHT`
  is a slab dimension and stayed); no `pipe-not-waitable` case (the
  typed `Waiter.add` has no overload for either kind and the wrappers'
  words are package-private, so the refusal is a COMPILE error, which
  is why none of `NotWaitable`'s seven existing arms is tested either);
  and `Post` answers `Result<Bool, _>` so the would-block stays out of
  the error channel on both ops.
  FINDINGS carried forward, each written up in the design's As-built:
  (1) **CLOSE IS NOT NEEDED** — §11 has promised a `close` op "with
  Pipe in M4" since M3 unit 2.75, and closing an END is releasing the
  last handle onto it, which IS the column reaching zero; a `close`
  beside that would be a second route to one state, reachable with a
  sibling still live. §11 amended to say so. (2) A `PIPES` row's
  `process` field is the CHARGED process and the teardown sweep frees
  by it, so a creator dying while a peer holds an end frees the slot
  under that peer — the EXISTING Memory/IoMemory shape, unreachable
  while root dies last, and slightly sharper for pipes because a stale
  decrement onto a reused slot would also drop staged messages. The
  honest fix is ownership transfer at `give`, which wants a ruling.
  (3) `PipeCreate` mints into the process its HANDLE names and copies
  out to the CALLER; identical for every call today, and a
  cross-process create would hand back child-relative words exactly as
  `ThreadCreate` through a child's handle already does — named, not
  fixed, since it is the whole `*Create` family's receiver question.
  No new [SAWLANG] entry: nothing in this unit wanted a language
  feature that is not there, and SL-12's workaround held at every site
  INTEGRATED to main Aug 31 2026 (lead-reviewed, gate re-run
  170/170, fast-forward 15ac87f; validated on sawlang 0.2.1 —
  the pin bump rides this integration). Findings: close-not-
  needed (§11 amended); the teardown force-free hazard -> RULED
  as #10 ruling 10 (references govern lifetime, unit-2 rider)
- tests conversion pass — statement + fold try/catch [scheduled, queue
  1]: the 75 statement-position `match` sites kept at the Aug-31 sweep
  on SL-12's ICE become statement `try f() catch {…}` (legal since
  sawc 0.2.1), AND the 25 fold-shaped sites (`case Err(_) -> value`)
  become `try f() catch { value }` — the fold opt-in RULED IN by the
  user Sep 1 ("this new form is nicer / more readable"), extending the
  Aug-31 bind-or-bail idiom to bind-or-default. The 12 real-work + 4
  negative-test matches stay by design. Model on the Aug-31 sweep.
  Acceptance: per-case transcripts byte-identical except
  `entry=`/`epc=` address rows + the thread_preempt / timer_interval
  timing rows.
  **CLOSED (Sep 1, branch `worktree-agent-a1da569722b01aee8`):** 95
  sites converted across 33 test packages — 80 statement-position
  (`try f() catch { <bail> }`, the arm's bound `e` becoming the
  implicit `error`, every print string untouched) and 15 fold
  (`try f() catch { <value> }`). Control-flow only: same call, same
  order, same text. Census reconciled against the Aug-31 record, every
  delta from M4 unit 1 (the only work to land between the sweep and
  this pass): statement 75 -> 80, the five new ones all `pipe-child`;
  real-work keeps 12 -> 14 (`child-post`'s drain arm, `pipe-child`'s
  inlet give); negative-test keeps 4 -> 9 (`pipe-no-post`,
  `pipe-oversized`, `pipe-peer-gone` x3).
  **THE FOLD COUNT IS 15, NOT 25, AND THE 25 WAS AN OVER-COUNT.** The
  Aug-31 census keyed on the ERR arm being a bare value; that finds 26
  sites today (the extra one being `handle-drop-release`'s inner match,
  nested in its outer site's Ok arm and evidently counted with it). But
  the fold needs the OK arm too — the bound payload itself — and only
  15 have it. Of the other 11, ten are success PREDICATES
  (`case Ok(_) -> true, case Err(_) -> false`) whose Ok arm DISCARDS the
  payload for a different value, which bind-or-default cannot express,
  and one is `handle-drop-release`'s outer site, whose Ok arm is a block.
  All eleven are KEEPS under this entry's own "if an arm does real
  distinct work" clause. Keeps therefore total 33, not 16: 14 real-work,
  9 negative-test, 10 predicate. Two comments describing the old shape
  were retargeted (`event-basics`' "the arm exists to", `thread-basics`'
  DF-178e note); no other comment moved.
  Gate: `make sos-test` GREEN — `ALL SOS TESTS PASSED (170 passed across
  riscv32 + arm64)`, exit 0, 85 cases per arch. The runner asserts every
  transcript verbatim, so full green IS the byte-identical oracle this
  entry asks for; no expectation was touched. sawlang HEAD `23ce9b21`
  (the dispatch sha) at the gate.
  **REMAINDER FOR THE LEAD:** CLAUDE.md's idiom paragraph still ends
  "The 75 statement-position + 25 fold sites kept during the ICE era are
  a queued conversion pass" — stale now, and its `25` is the over-count
  above. One line, the lead's to write.
  INTEGRATED to main Sep 1 2026 (lead-reviewed, lead gate re-run
  170/170 under sawlang HEAD 87063387, fast-forward 721e2fe; the
  lead remainder executed in the integration commit — CLAUDE.md now
  records the tree as fully converted with 33 by-design keeps).
- sos_runner parallel `-j N` [scheduled, queue 1; user-acked Aug 31]:
  parallel builds + QEMU per case (**default 4 — user ruling Sep 1:
  the host has 4 performance cores vs 6 efficiency cores, and the
  parallelism tracks the P-cores**), per-case transcripts unchanged,
  deterministic report order. Measured ~33-45 MB RSS per QEMU, so
  memory is a non-issue on the 24GB host; cores are the constraint
  (a UTM VM + the sawlang-db peer session share them).
  Acceptance: per-case transcripts byte-identical vs a serial run.
  **CLOSED (Sep 1, branch `worktree-agent-afb1061980c6f31b7`):** `-j N`
  landed on `tools/sos_runner.py`, DEFAULT 4, so `make sos-test` is
  parallel with no argument written anywhere.
  SHAPE. The ARCHITECTURES STAY SEQUENTIAL — two at once would build one
  Blade package for two triples into one `.build/` tree, whose stale-image
  sweep and build stamp are shared even though the artifacts are not — and
  the pool runs INSIDE an arch: first the root/child package builds, then
  the per-case build+boot+judge, `jobs` at a time each. `_map_ordered`
  submits every item and awaits the futures in SUBMISSION order, so results
  stream out in case-definition order whatever the completion order; a
  worker returns its verdict and the lines it owns and prints NOTHING, and
  one thread walks the list. `-j 1` runs inline on the calling thread
  rather than through a one-worker pool, so the serial harness — the thing
  the parallel one is diffed against — takes no path the parallel one
  introduced. Threads, not processes: every unit of work is a
  `subprocess.run`, so the GIL is released throughout and a process pool
  would buy only a pickling constraint on the case table.
  THREE ISOLATION HAZARDS FOUND, ONE OF THEM REAL. (1) `_stitch_root_image`
  staged EVERY case's image over one `<build>/root.sosimg` and assembled one
  `rootimg.o`, because the committed `kernel/rootimg.S` names a fixed
  filename and so is the one artifact here a case cannot distinguish by
  name — two concurrent root-image cases would have booted each other's
  image, which is a wrong-but-PLAUSIBLE transcript and the worst failure a
  harness can have. Now a per-case staging directory the `-I` points at,
  plus a per-case `.rootimg.o`. (2) `_run_qemu`'s no-input path INHERITED
  the harness's stdin, and `-nographic` on a TTY sets raw mode and restores
  whatever it found — four QEMUs saving and restoring each other's termios
  leave the operator's shell in whichever state the last one to exit saw.
  Each boot now gets an empty pipe this process holds the write end of: no
  byte, no EOF, which is what an idle console always was, and the same in
  both modes. (3) `tc()` is a lazy global with a one-time note print, so it
  is resolved eagerly in `main` before any pool starts.
  AUDITED CLEAN: every other per-case artifact is already `<case>`-named;
  `_root_image`/`_child_images` are written on the main thread in the
  package phase, the table's only writer; nothing calls `os.chdir` or
  mutates `os.environ` (Blade's cwd and env are per-`subprocess` already);
  and the sawlang side is race-safe by construction and says so —
  `sawc/stdcache.py` publishes pid-temp + `os.replace` with a prune
  documented "safe to race", `rt_build.py` holds an `flock`.
  MEASURED, full suite, both arches, back to back on one machine under the
  suite lock: `-j 1` 1195s vs `-j 4` 457s — **2.6x**, 19m55s -> 7m37s.
  ACCEPTANCE MET. 170/170 green in BOTH modes (exit 0 both); the console
  report BYTE-IDENTICAL between them; the pairwise transcript diff 166
  identical / 4 differing, and the 4 are exactly the two known timing
  cases on both arches — `thread_preempt`'s A/B interleave and its
  `SOS: timer tick` rows, `timer_interval`'s `fires=` counters. No
  assertion reads either bucket: `thread_preempt` asserts `AB`/`BA`/`AB`
  and the join line, and `timer_interval`'s own comment states the
  periodic counts are deliberately unpinned because coalescing makes 1, 2
  or 3 all correct. Shown INHERENT rather than parallel-induced: two
  independent `-j 1` runs of riscv32 `thread_preempt` differ in the same
  row class. CLAUDE.md's Testing section records the default, `-j 1`, and
  the one row that moves in either mode.
  INTEGRATED to main Sep 1 2026 (lead-reviewed, lead gate re-run
  170/170 under the parallel default, sawlang HEAD 87063387 unchanged,
  fast-forward b42ae85). The CLAUDE.md Testing addendum accepted as a
  necessary deviation: the gate's default mode changed, so the two
  timing-row cases are named where the transcript tradition is stated.
- M4 unit 2 — the one-shot pair [#10 rulings 4/9(a)/10, §2.1 ratified,
  #14 — designs/014-one-shot-pair.md, authored Sep 1]:
  PipeReply/PipeRequest as two counted kinds over one RING SLOT (two
  more ref columns, D-1's no-third-slab), Post grows its claim return
  and Take grows the obligation beside the message, Resolve/Reply each
  CONSUMING the caller's entry, abandonment derived from the columns in
  both directions, transferable so §2.1's delegation example runs across
  a process boundary. **Plus ruling 10's rider**: the teardown's
  by-charged-process force-free arms for the counted kinds deleted and
  D-4's write-off activated. Everything still polls; nothing parks.
  **CLOSED Sep 1 — LANDED.** Suite 182/182 (91 cases/arch, both
  profiles) from 170/170; whole-transcript diff bucketed in the design's
  As-built, and every one of the 170 pre-existing case rows is
  BYTE-IDENTICAL in mark, name AND index — the only movement in them is
  the `[n/85]` -> `[n/91]` denominator, plus image SIZES (all 73 riscv32
  images by +64..+1168, five arm64 images by one page and 68 not at
  all). VALIDATED ON sawlang 0.3.0 at `87063387`, the pin's version, for
  both the baseline and the gate. **`pipe-basics`' ring-depth rows did
  NOT move** — the brief authorized a move with argument and none was
  needed: that case tells and discharges at every step, so its exchanges
  settle exactly where unit 1's slots freed. What landed: two `ObjType`
  kinds targeting an EXCHANGE (`connection * PIPE_INFLIGHT + ring slot`)
  with two reference columns, four `.bss` arrays beside `PIPE_LENS`, a
  four-state `ExchangeState`, `PipeSlot` trading `head`/`count` for a
  staged FIFO list (slots settle out of order, so occupancy is per-slot),
  `PipeReplyOp.Resolve` / `PipeRequestOp.Reply` on new rights enums,
  `Take` answering through a two-word record, give + boot drain for both
  kinds, typed `NoCopy` wrappers that disarm on consume, and two more
  `NotWaitable` arms unit 3 removes. SIX cases across seven packages:
  `pipe-oneshot`, `pipe-abandon`, `pipe-delegate` (+ the `child-reply`
  child package), `pipe-no-reply`, `pipe-big-reply`, `pipe-dead-claim`.
  SIX DEVIATIONS, argued in the As-built, of which two matter to a
  reader: `Take` answers through a copy-out record and the BODY buffer is
  now checked against the caller's real memory rather than against a cap
  argument (stricter, `BadBuffer` where it was `BadArg`); and the typed
  `resolve` disarms by the ANSWER rather than before the syscall, because
  a would-block consumes nothing.
  FINDINGS carried forward, each written up in the As-built: (1)
  **RULING 10 COULD NOT BE A PURE DELETION** — the close-all counts
  without freeing by design (7 D-5, so the counted sweeps' report numbers
  stay honest), so deleting the three silent sweeps outright would strand
  a `Live` slot at `refs == 0` that nothing can ever free; the sweeps'
  CONDITION changed from "charged to this process" to "named by nobody"
  and the free now runs each kind's own quota credit, which the old
  sweeps never did for another process's slot. (2) Mapping's sweep looks
  like the three and is NOT one — its handles carry no `Transfer`, so the
  hazard is structurally unreachable — left alone with the reason at the
  site. (3) **ROOT'S 16 KiB STACK GRANT IS A CEILING ON A TEST'S
  `_start`**, and the 64-bit profile meets it first: design 137 assembles
  every format-argument message in stack scratch, so 47 `print` sites in
  one function overflowed on arm64 and passed on riscv32. One phase per
  function is the fix and the shape unit 3's longer cases should start
  from. (4) SL-13 filed. (5) `MAX_HANDLES` (16), not `PIPE_INFLIGHT`
  (16), is what bounds a client holding its claims — proving the
  in-flight budget head-on will want two processes or a bigger table,
  which is also exactly what ruling 4's fused `Call` exists to make
  unnecessary. (6) The idiom held, fold shape included.
  INTEGRATED to main Sep 1 2026 (lead-reviewed, lead gate re-run
  182/182, sawlang HEAD 87063387 unchanged, fast-forward 2a522ba;
  finding 1's reap_unreferenced accepted as the faithful execution of
  ruling 10 against the stranding case a pure deletion reaches). The
  Sep-1 rulings — 11(a)-(d), the ReplyRecv reply-then-wait amendment,
  and the API review gate — land in designs/010 with this integration.
- **M4 unit 3 — waitability [#15 — designs/015-waitability.md]. CLOSED
  Sep 1 2026, awaiting the lead's move to a done file.** What landed:
  the four pipe arms (`designs/010` ruling 8's inlet room-to-post level
  among them), so §2.2's ratified waitable list CLOSES and the four
  `NotWaitable` refusal arms units 1 and 2 wrote by hand are gone; the
  wait record grows a body region and a REPLY delivery carries the
  answer's bytes (ruling 11(a)), so the wait IS the resolve and a
  multiplexed RPC is post + attach + wait; `WaiterOp.Add` grows a flags
  word for `AttachMode.OneShot` (ruling 9(b)) and the CONSUMING attach
  `Waiter.give` (ruling 11(c)), which is eight typed overloads, one per
  waitable kind; ruling 9(a)'s implicit detach at `Resolve`/`Reply`;
  peer-gone terminal levels at all four zero-arms, so nothing parked is
  silently doomed; and every piece §2.1's ratified blocking `send(msg)` /
  `send(msg, timeout:)` compose out of — post + attach + wait plus a
  Timer on the same Waiter. **THE COMPOSED-SEND SURFACE ITSELF IS STRUCK
  BY A USER RULING AT LEAD REVIEW (Sep 1)**: no `PipeClient`, no
  `SendOutcome`, because a wrapper type freezes a shape that cannot later
  be refactored into a simple kernel call, the manual composition stays
  user-writable with the shipped primitives, and a kernel-side timeout,
  if ever genuinely needed, will be added to the kernel directly, then,
  by ruling. §2.1's `send` surface therefore arrives with unit 3.5's
  `Call`; `tests/pipe-send-manual` is the composition written out and is
  what the "the primitives suffice" claim rests on. **ONE STRUCTURAL
  CHANGE**: `kcore.wake` merged into
  `kcore.refs` — a delivery now drops a counted reference and a dropped
  reference now delivers, so the two are one act and a module boundary
  between them would be the cycle DF-232e does not diagnose. Suite
  196/196 (98 cases/arch) from 182/182, seven new cases across eight new
  packages, no pre-existing case row moved. Docs: spec §2.1 (abandonment
  re-worded to the LAST REFERENCE; the send surface NOT marked built —
  the pieces are, the composition is the caller's, and the ratified
  surface arrives with `Call`), §2.2
  (the list closes, persistent-BY-DEFAULT, the record layout, the
  consuming attach), §5.7 (the Add flags growth), §2's Pipe and Waiter
  rows, the counted-kinds table's four attachment columns, §11's M4
  progress and quota note. As-built in #15; SL-14 and SL-15 filed below.
  INTEGRATED to main Sep 1 2026 (lead-reviewed, lead gate re-run twice
  — 196/196 at ee0f8a6 and again at cae4286 after the user's
  composed-send removal ruling — sawlang HEAD 87063387 unchanged
  through both, fast-forward cae4286). The Settled-state correction
  accepted (the brief's stale-entry sentence was unsound); the
  PipeClient wrapper struck by user ruling at review, recorded in
  designs/010 with this integration.
- **process stats [#16 — designs/016-process-stats.md]. CLOSED Sep 1
  2026, awaiting the lead's move to a done file.** What landed:
  `ProcessOp.Stats` on `ProcessRight.Stats` (a per-kind right named for
  its op, in the ONE default set, so self-inspection composes free),
  answering three `UInt64` columns through the copy-out funnel
  `Clock.Now` opened — DOUBLEWORD-indexed, because a count is a property
  of the quantity and not of the machine. The columns are split by the
  CAUSE CLASS `ktrap` already decides on, which is what makes any of
  them usable: `syscalls` is EXACT for a given program path, `interrupts`
  moves with the host and is printed, `faults` is exact where a test
  provokes one. **THE COUNTING SITE IS `ktrap`'s OWN THREE-WAY HEAD AND
  IT IS ARCH-FREE** — riscv32's one vector and arm64's two both land
  there, so no HAL file was touched, one increment per trap sits ahead of
  every dispatch, and there is no second site to keep in step. A BAD
  SYSCALL COUNTS AS A SYSCALL (the columns are keyed by how the machine
  entered the kernel, not by how the kernel felt about it — keying on the
  outcome would make the assertable column unpredictable); boot-door
  traps are unattributed and an interrupt taken at the idle poll or a
  preemption point is not a trap at all, both said at the site. Columns
  zero at BOTH creation doors, so a reclaimed slot inherits nothing, and
  they SURVIVE the death — which is the only shape in which the fault
  column is observable, since a faulting process cannot ask about
  itself. **ONE ADDITION BEYOND THE BRIEF, ARGUED**:
  `Process.mint(rights:)`, the one kind that had no typed mint, because
  the new bit exists to be WITHHELD and without the funnel it would be
  dead surface (`ProcessRight` joined the facade's re-export line with
  it, under the rule that line already states). PER-PROCESS ONLY as
  ruled; the shared-region future is recorded in the brief and NOT built.
  **THE SWEEP, five cases, every existing row byte-identical and the
  runner diff purely additive (zero removed lines)**: `pipe_send_manual`
  asserts the manual composition at 4 traps — post + attach + wait plus
  the closing read — which says a cross-process BLOCKING round trip
  costs three and a park is one `ecall` however long it parks;
  `pipe_oneshot` asserts the smallest complete exchange at 5;
  `process_isolation` and `death_fault` assert a dead child's columns as
  exact mirrors (`syscalls=0 faults=1` from the hardware,
  `syscalls=1 faults=0` from the syscall door); `thread_preempt` PRINTS
  the interrupt column and does not assert it. Every one of the five
  numbers was predicted from the counting rule before the case was run
  and every one was right first time. Dedicated proof `process-stats` +
  the silent `child-stats` (a talking child's count is a hash of its own
  prose): an exact self delta over a print-free window, a dead child's
  columns, the interrupt row, and a sibling minted without `Stats`
  faulting when it asks. Suite 198/198 (99 cases/arch) from 196/196; one
  new case, two new packages, no pre-existing case row moved — 196
  denominators changed and zero indices did. Docs: spec §2's Process row
  (op, right, record, counting and attribution rules, the per-process
  ruling and the recorded region future) and a §8-adjacent note on the
  status word's new neighbour; CLAUDE.md's timing-rows list names
  `process_stats`' `interrupts=` row beside `thread_preempt`'s and
  `timer_interval`'s; As-built in `designs/016-process-stats.md` with the
  census, the five deltas, the bucketed transcript accounting and four
  findings. **NO SL-N FILED** — nothing here met a sawlang deficiency.
  **ONE FINDING WORTH THE LEAD'S EYE AND DELIBERATELY NOT ACTED ON**:
  `debug_print` traps once per BYTE, so a root server's console prose
  outweighs its object ops roughly ten to one (`pipe_oneshot` spends ~5
  traps on the exchange it proves and ~300 describing it). A buffered
  `debug_print` taking a length is the obvious answer and is an ABI
  change with no consumer yet; unit 3.5's `Call` is the next thing that
  will want that argument made properly
  INTEGRATED to main Sep 1 2026 (lead-reviewed, lead gate re-run
  198/198, sawlang HEAD 87063387 unchanged, fast-forward fc9fd82).
  The Process.mint(rights:) addition accepted as the per-kind-on-
  demand answer the unit-1 parked mint finding anticipated; the
  buffered-debug_print finding seeded in the backlog.
- **CLOSED — M4 unit 3.5, the fused paths [#17 —
  designs/017-fused-paths.md; dispatched and landed Sep 1].** Built as
  briefed. `PipeInletOp.Call` (post-park-resolve behind one trap, gated
  by the existing `PipeInletRight.Post`, parks on the inlet's room level
  and never answers `WouldBlock`, no duration argument, mints no
  `PipeReplyHandle`) and `PipeRequestOp.ReplyWait` (on the REQUEST, the
  waiter REQUIRED, leg-tagged answer with `Wait` = 0, the request
  consumed on every path). The claim of a parked call is THREAD-TIED —
  `PIPE_CALLER` forward, `ThreadSlot.call_claim` back — and the wake
  dispatch grew ONE arm (`kcore.refs.notify_claim`), with the room level's
  own arm beside it because ruling 8's park has two states.
  `end_process` grew the orphaned-claim pass, ahead of the close-all.
  Typed surface: `PipeInlet.send(body:len:) -> Result<PipeMsg,
  SosStatus>` and `PipeRequest.reply_wait(body:len:waiter:) ->
  Result<WaitResult, ReplyWaitError>`. Suite 198/198 -> 210/210 (105
  cases/arch); six new cases, nine new packages.
  **ONE FORCED DEVIATION FROM THE REVIEWED §API, FOR THE USER TO
  RATIFY**: `reply_wait`'s reviewed CONSUMING RECEIVER (`self` by value)
  is not expressible — sawc 0.3.0 answers ``Parse error: 'self' must be
  a reference: use '&self' or '&var self'`` — so it landed as `&var self`
  + disarm-before-the-syscall, which is `PipeRequest.reply`'s own
  spelling of the same contract one method up and is observably
  identical (the request is consumed on every path, and a second use is
  the diagnosed `BadHandle` fault). Filed as SL-16. The alternative that
  would have kept a true by-value consume — moving the op off the request
  onto the Waiter, `Waiter.give`'s shape — was NOT taken, because the
  op's PLACEMENT is the more strongly ruled of the two ("THE OP LIVES ON
  THE REQUEST", user, Sep 1, restated three times in #10 ruling 4's
  amendment).
  INTEGRATED to main Sep 1 2026 (lead-reviewed, lead gate re-run
  210/210, sawlang HEAD 87063387 unchanged, fast-forward 5cda754).
  The receiver-spelling judgment RATIFIED BY EVENTS: the user is
  resolving SL-16 upstream with a `consumes` effect (fourth pin bump),
  so the landed `&var self` + disarm spelling is the ruled interim and
  converts at closure. The server arithmetic correction (3->2, unit
  4's delivery-as-take completes to 1) accepted — the brief's claim
  was the lead's error; the measured baseline stands.
- 1. M4 unit 4 — rendezvous + delivery-as-take [#10 rulings 5/11(d) +
  the keep-mask rider; design 18 to author]. THE PIN-BUMP DEPENDENCY IS
  DISCHARGED (Sep 2): the fourth bump landed `consumes` (sawlang 0.4.0 @
  `46eebb36`) and SL-16's conversion sweep closed with it, so the
  consuming surfaces are FINAL and unit 4's brief writes them as they
  now read — `(move request).reply(...)` and
  `(move request).reply_wait(...)` consume; `claim.resolve(...)` does
  not, by the argued exception in SL-16's closure. The APIs are
  complete; the ladder continues.
  **CLOSED Sep 2 (design 18 authored, §API user-reviewed, AMENDED at the
  implementing agent's STOP, and built; As-built in the brief).** What
  landed: handles in messages on all four submission ops through
  ARGUMENT RECORDS (six things do not fit three registers), with
  `PIPE_MSG_HANDLES = 4` in `sosabi` beside `PIPE_BODY_BYTES`; ruling
  5's rendezvous ledger — the staged word is a NOTE, the entry stays the
  sender's, `move_entry` moves it at the take or the delivery with the
  quota charge, and the batch is pre-flighted so a taker that cannot
  afford it refuses ATOMICALLY; ruling 11(d)'s delivery-as-take under
  its own `WaitTag.Message`, whose REFUSAL ARM (my design, argued in the
  As-built) is a wake with a status and no record — revocation's own
  shape — leaving the message staged and its level raised; the wait
  record's final layout (3 header words, the PACKED meta of one length
  byte plus four kind bytes, five handle words, then the body), which is
  SMALLER than unit 3's despite gaining handles because the standalone
  body-length word died; and agenda 7b's keep mask on `Give` AND on
  `SystemOp.ProcessSelf`, the latter closing spec §9's recorded finding.
  The decoded vocabulary moved to `sos.pipe`. Six new cases
  (`pipe_stale_attach`, `pipe_table_full`, `pipe_handles`,
  `pipe_delegate_msg`, `pipe_cq`, `give_keep_mask`), 105 → 111 per
  architecture. MEASURED: the completion-queue server serves eight round
  trips in 10 traps against `pipe-pingpong`'s 18, same client program —
  ONE trap per message, the ladder complete. Riders done: the two
  missing floor re-export seams, the `var`→`let` tidy in the touched
  files, `MAX_PROCESSES` unchanged at 2 with the two-pipe topology
  argued in the case header. TRANSCRIPT ACCOUNTING: 222/222 green on
  both arches, five buckets and no row outside them — all 100 moved
  image rows belong to packages that name the pipe or wait surface and
  every package that names none of it is unchanged, which is the whole
  argument; the growth is +26.7% / +22.6% and it is the TYPED message
  value rather than the wire record, filed in BACKLOG with its
  lazy-decode lever. Deviations and findings are in design 18's
  As-built; SL-17 filed below; `Process`/`System` in messages is the
  named follow-up, filed in BACKLOG.
  **RE-RULED AND REWORKED ON THE BRANCH BEFORE MERGE (user, Sep 2, at lead
  review): RULING 5 FLIPS TO TAKE-AT-POST.** A staged handle leaves the
  sender at the POST — each entry unbound, its reference held by the
  kernel in the ring note as a typed ref, the sender's quota row written
  off — and is minted into the receiver at the take or the delivery. The
  rationale, recorded because it post-dates the Aug-30 ruling: ruling
  10's orphan accounting and design 260's move-at-post semantics eroded
  it, sender-keeps contradicted the typed tier's own move story
  (`post_with` CONSUMES the wrapper), and the unit's own finding 5 — a
  fire-and-forget capability send racing the sender's exit — inverts into
  a GUARANTEE. What that cost: the stale-revalidation path is deleted
  (`None` in a slot means the sender left it empty and nothing else),
  three unwind arms became RELEASES (with `pipe_drop_staged` moving to
  `kcore.refs` and `exchange_settle` answering with its collected batch,
  which is collect-then-free spelled across a module boundary), and ONE
  refusal became terminal — a fused call's reply-handles are destroyed by
  a full table, because its claim is a parked thread with no second
  chance. `pipe-stale-attach` retargeted to **`pipe-send-exit`** (the
  child posts a capability and exits; root parks on the death, takes, and
  posts through what arrived — plus the sender's teardown count as the
  write-off), and `pipe-handles`' ledger rows INVERT. **AND A SECOND
  RULING THE SAME DAY: `WaitPayload`'s owning payloads are RESTORED** —
  `Reply(outcome: ReplyDelivery)` and `Message(msg:, request:)`, with
  extraction through a `consumes` accessor (`WaitResult.open`) and an
  owned match; the optional-field design-around is gone and the type
  proves what its docstring used to promise. Both re-rules are in design
  18's As-built §13; SL-17's entry below carries both amendments
  INTEGRATED to main Sep 2 2026 (lead-reviewed twice — the first park
  superseded by three user rulings at review; the rework gated
  222/222 at 8b1d43b, lead gate re-run green at 9b35478, sawlang HEAD
  46eebb36 unchanged throughout; fast-forward 9b35478). Take-at-post
  landed (ruling 5 re-ruled in #10, riding this integration);
  WaitPayload restored to the reviewed shape via the consumes
  accessor (primary construction, probed working); SL-17 amended with
  the ruled refusal AND the moveless second face; pipe-send-exit
  proves the capability-survives-sender guarantee with an asserted
  teardown count.

- 2. ~~design 22 — the one-shot discipline (M4 unit 4.5)~~ **CLOSED
  Sep 2 — all three parts of #10 ruling 12 landed, gate green on both
  arches (113 cases per arch, 226 passed).** (a) THE POLL SPLIT IS RETIRED:
  `take -> Result<(PipeMsg, PipeRequest), _>` and
  `post`/`post_with -> Result<PipeReply, _>`, `polled_value` retired,
  `polled_ok` survives as `ready()`'s decode, floor banner rewritten
  with the `Channel.try_receive` divergence recorded at the seam; ~60
  call sites swept, three genuine pollers keep a `WouldBlock` arm.
  (b) `PipeReplyOp.Resolve` PARKS when pending and consumes on every
  path, so `PipeReply.resolve` carries `consumes` and call sites spell
  `(move claim).resolve()`; new `PipeReplyOp.Ready = 1` gated on
  `PipeReplyRight.Wait`, typed `PipeReply.ready(&self)`. The park
  needed NO new wake or teardown machinery — `park_resolve` is
  `consume_entry` minus the unref, so `notify_claim`,
  `wake_call_reply` and `release_pending_calls` serve both parked
  shapes unchanged. (c) `Mint` left `pipe_reply_rights()` and
  `pipe_request_rights()`; enum bits and static_asserts untouched.
  New cases `pipe_resolve_park` (post+resolve = 2 traps against
  `Call`'s 1) and `pipe_resolve_orphan` (the `end_process` mirror at a
  handle-backed claim); `pipe_dead_claim` and `pipe_no_reply`
  retargeted. Docs: spec.md §2's Pipe row, §2.1's resolve line, the
  §5-era row and the `consumes`-funnel paragraph; design 13 D-2 and
  design 14's `Resolve`, `Mint` and consumed-handle notes carry
  re-rule pointers. As-built in `designs/022-one-shot-discipline.md`.
  ONE ITEM OPENED, filed in [BACKLOG]: the `PipeRequestRight.Reply`
  gate lost its only test.
  INTEGRATED to main Sep 2 2026 (lead-reviewed; the one deviation —
  mint-refusal at resolve consumes, on wake_call_reply's precedent —
  accepted on merits; lead gate re-run green 226/113-per-arch at
  3f664d7, sawlang HEAD 46eebb36 unchanged both ends; fast-forward
  3f664d7; the entry's gate line corrected by the lead from the
  agent's "114 cases, 228 assertions" overclaim to the gate's
  printed 113/226).

- 4. M4 unit 5 — the money shot [#10 agenda 7a; design 21
  (021-uart-service.md) USER-REVIEWED Sep 2 with the multi-client
  amendment (root retains a sibling inlet, two clients in
  deterministic phases) and MAX_PROCESSES RULED to 3].
  **CLOSED Sep 2 — BUILT AS BRIEFED, no deviation from the ruled
  surface.** Gate green on both arches: 116 cases each, 232 assertions.
  WHAT LANDED: `MAX_PROCESSES` 2 -> 3 (the whole kernel delta — one
  line, plus doc-block sweeps at `limits.saw`, three `sched.saw`
  invariant notes and spec §2's Process row); `tests/uartproto`, a
  LIBRARY package holding the /dev/uart0 protocol one declaration both
  halves compile against; `svc-uart-ns16550` / `svc-uart-pl011`, the
  M3 driver child grown a completion-queue server face (outlet given
  to its Waiter, IRQ beside it on the same Waiter, held obligations
  watched for abandonment); `svc-client`, a process that reads the
  console holding no device authority at all; `uart-service` (root
  wires both children and is a THIRD client through a minted sibling
  inlet), `uart-cancel`, `delegate-3p` + `child-forward` +
  `child-far`; `hal/{riscv32,arm64}/user/child2.ld`, because unit 5 is
  the first case in the tree with two children alive at once. FIVE new
  case entries, three reaching each arch (113 -> 116 per arch, 226 ->
  232). ROW CENSUS AT PEAK WIRING: root spends 3 protection rows and
  needs no more — `process_create` reads a child's image with kernel
  privilege and a launcher maps nothing — so the squeeze is the HANDLE
  table rather than the grant record; counted call by call, peak 11 of
  16 with the boot Memory wrappers released after the creates, 15
  without them. TRAP ACCOUNTING, measured at both ends and it moved the
  ladder's headline: one trap per message the server ANSWERS and TWO
  per message it DISCARDS, because a TELL owes no reply and the
  obligation it minted must still be released — the client pays the
  same two for the same reason, so a one-way message buys latency
  rather than syscalls. TWO EXISTING CASES ADAPTED, asserted rows
  unchanged: `process-reclaim` and `death-late-attach` each create one
  unstarted FILLER process, because both ask whether a slot is
  available and that question needs a full table. As-built in
  `designs/021-uart-service.md`. NO NEW SAWLANG DEFICIENCY: the two
  walls this unit met are SECOND SITES of SL-13 (a `&var [T; N]`
  parameter's elements are not assignable — the protocol's request
  buffer is a one-field struct because of it) and SL-8/DF-172d (a
  wrapped binary expression is two statements), both noted at their
  entries below.
  INTEGRATED to main Sep 2 2026 (lead-reviewed; built as briefed with
  no deviation from the ruled surface — the protocol's `Sync` and
  `Mark` ops accepted as within-scope elaborations, `Sync` being the
  ordering barrier finding 1's TELL-outlives-sender showed necessary;
  lead gate re-run green 232/116-per-arch at 8caa5a7, sawlang HEAD
  46eebb36 unchanged both ends; fast-forward 8caa5a7).

- 1. the stub pass — pre-carve the shared files for the concurrent
  pair [user-ruled Sep 2; lead's own, small, gated]: sos_runner
  `--board` arg + empty marked esp32c3 section, Makefile smoke-target
  stub, hal/riscv32-esp32c3/ placeholder (SIBLING-COPY rule: no
  hal/riscv32 restructure — unit 5 owns it), CLAUDE.md non-gating
  note, both queue entries below pre-written.
  **CLOSED Sep 2 (lead's own) — landed and gated at `ec9e56e`,
  222/222; both pre-carved seams were used exactly as drawn (design
  20's runner diff is one contiguous hunk inside the marked section,
  and no rebase conflict ever formed between the pair).**
  INTEGRATED with the pair's own commits (this line written at design
  20's integration, late Sep 2 2026).

- 3. design 20 — ESP32-C3 board HAL, SMOKE ONLY (bringup + memory
  config; direct boot; RV32IMC no-A; non-gating, machine-local QEMU)
  [#20, brief user-reviewed Sep 2]. **CLOSED — SOS BOOTS ON THE C3 AND
  `make sos-smoke-esp32c3` IS GREEN ON ALL THREE REVIEWED CASES**
  (boot / timer / isolation). As-built with the verbatim oracle
  transcripts: `designs/020-esp32c3-smoke.md`; every address with the
  probe that produced it: `hal/riscv32-esp32c3/ABI.md`.
  BOTH BLOCKERS CLOSED. (A) XIP as ruled: `.text` (413,622 B) and
  `.rodata` execute in place in the flash IBUS window at 0x4200_0000,
  `boot.S` copies only `.data` and zeroes `.bss` in SRAM;
  `.payload`/`.regions`/`.childimg` stay in flash too, since the kernel
  copies out of them. The 400 KiB divides 168/124/92/16 KiB across
  kernel, root, child and pool, every number MEASURED (the tightest is
  root at 99,392 of 110,592). (B) THE PARK'S FINDING WAS WRONG and the
  retry says so: the matrix IS wired, and what the first sweeps got
  wrong was `mie` — the matrix drives MACHINE EXTERNAL, so bit 11 is
  the gate while `mcause` reports the matrix's own CPU interrupt
  number. `TIMER_CPU_INT` is chosen as 7 so a C3 tick's cause word
  reads exactly like a standard machine-timer interrupt and the
  arch-generic decoding above the HAL is untouched. No fallback was
  needed: five deterministic ticks, taken as real interrupts.
  ONE REAL EMULATOR GAP remains and the HAL absorbs it: this matrix
  never raises `mip`, so `wfi` never wakes on it (probed level and edge
  alike, with `mip` reading 0 in the same breath as an interrupt being
  taken). `wait_for_irq()` is EMPTY here and the idle loop spins while
  `irq_poll()` reads the SYSTIMER latch — design 178 D2 untouched, no
  kernel change, and `sos_wait_for_irq` left in place because `wfi`
  does wake from the matrix on real silicon.
  WHAT LANDED: `hal/riscv32-esp32c3/{kernel,user}/` as a sibling copy
  (each file notes its origin); `tests/c3-{timer,isolation,child-poke}/`
  as NEW packages (a package names its linker script by TRIPLE and the
  C3 shares `riscv32-unknown-none-elf` with virt, so it can name one or
  the other and not both); the pre-carved board section of
  `tools/sos_runner.py` filled and nothing outside it; the Makefile
  target real. The isolation case is C3-LOCAL and the brief's question
  is answered NO: `child-poke` finds its target by rounding its own
  address to a 256 KiB grid, which is a fact about the virt map, and
  this board's regions are 124/92 KiB. It names a kernel-owned address
  instead — the stronger claim, and the one the brief actually asks for.
  THE NO-A BUILD SPELLING HAS THREE HALVES, and the third would have
  shipped a broken image in silence: sawc `--target-features +m,+c`;
  esp-clang `-march=rv32imc_zicsr_zifencei -mabi=ilp32`; and BLADE,
  whose default for any riscv32 triple is the virt/P4 baseline
  `+m,+a,+c`, so every C3 package restates march/mabi/target-features in
  its manifest. Nothing catches a miss — this emulator's CPU advertises
  A (`misa` 0x401411AD), so the wrong build runs green here and faults
  on the part.
  INTEGRATED to main Sep 2 2026 (lead-reviewed; the park-then-resume
  flight accepted — both blockers were user-ruled mid-flight (XIP in;
  interrupt retry, which overturned the park's own finding); fences
  verified (runner diff one contiguous hunk in the board section,
  nothing under kernel/, rt/, hal/riscv32/ or existing tests); lead
  gate re-run green 232/116-per-arch, virt byte-identical, sawlang
  HEAD 46eebb36 stable; SMOKE WITNESSED BY THE LEAD: 3/3 matching the
  As-built oracle transcript; fast-forward e00c2c3). LEAD FOOTNOTE:
  a smoke run WITHOUT SAWLANG_ROOT dies in toolchain.py's fetch path
  with a FileExistsError traceback instead of the documented
  three-way refusal — pre-existing resolver behavior, noted for a
  future toolchain.py pass.

- 2. design 23 — riscv32 HAL consolidation — **CLOSED (agent, Sep 2),
  awaiting review/merge** [brief authored Sep 2, review WAIVED by the
  user ("i don't see that needing my input"); branch
  `design/023-riscv-hal-consolidation`]. **BOTH GATES BYTE-IDENTICAL,
  image sizes included** — `make sos-test` 232/232 with a transcript
  that `diff`s empty against the `a88a602` baseline (every case verdict
  and all 232 `.sosimg` sizes), and `make sos-smoke-esp32c3` 3/3 with
  all three flash sizes matching design 20 §9 exactly and the three
  consoles replayed and diffed row by row, addresses included.
  WHAT LANDED: the mandatory C dedup, the substantial `.saw` split AND
  the optional `boot.S` split — nothing parked. `hal/riscv32-common/`
  now holds one copy each of `kernel/lib.saw` (the `rv32core` module:
  trap frame, PMP staging, frame_init/resume_frame, the payload and
  region-table seams, the cause and syscall decoding), `kernel/trap.S`
  (trap_entry + sos_resume_frame), `kernel/sink.c` and
  `user/syscall.c`; each board keeps its console, timer, interrupt
  controller, memory map, `_start`, `kernel_fault` and stack. 3737 →
  3100 lines across the eight touched files (−17%), one whole second
  copy of every shared file retired. THE `hal` SEAM DID NOT MOVE:
  `kcore` still imports `hal`, the runner still maps
  `hal=<board>/kernel`, and each board re-exports 28 names from
  `rv32core` with a `public import`. **SL-6 DID NOT FIRE** — the
  `extern` block is split by CALLER into disjoint sets, so no shared
  declaration needs an owner, and no new SL entry is owed. The
  load-bearing probe was proved by PERTURBATION: `GRANT_ROW_BUDGET`
  temporarily set to 10 in `rv32core` tripped `kcore`'s own
  `static_assert(hal.GRANT_ROW_BUDGET <= 9)` two module hops away, so a
  re-exported static still folds as a constant. Runner: build wiring
  only (`hal_native` / `hal_modules` / `hal_asm` per arch, plus the
  same three by hand in the `--board esp32c3` section) — no case,
  assertion or report line moved. Two repo-structure docs are LEFT FOR
  DESIGN 24 BY NAME and recorded in the As-built §8.4: `CLAUDE.md`'s
  repo map and `README.md` line 46 both describe the pre-23 layout, and
  design 24's §6 consistency grep already names both files. One
  ADDENDUM written into design 20 §9: its isolation oracle paste is
  missing a blank line after `root survived`, a transcription artifact
  the same section's own doubling note predicts — no behaviour delta.
  THEN:
  INTEGRATED to main Sep 3 2026, night run (lead-reviewed; fences
  verified — kernel/, rt/, hal/arm64 zero diff, the 121 tests/
  manifest edits are `native=` path repoints only; lead gate re-run
  green 232 byte-identical AND smoke re-witnessed 3/3 matching the
  oracle, image sizes unmoved; sawlang HEAD 46eebb36 stable;
  fast-forward ca8ca50). The perturbation proof for two-hop constant
  folding is the As-built's keeper.
