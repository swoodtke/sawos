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
