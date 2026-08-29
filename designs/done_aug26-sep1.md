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
