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
