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

- M3 unit 1.5 — kernel interruptibility [sawlang#232 pin 1, #1 —
  designs/001-kernel-interruptibility.md]: DONE Aug 28, branch PARKED
  for user review. Landed: `irq.preempt_point()` beside `idle_poll`
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
- M3 unit 2 — CreateProcess [sawlang#232]: two-phase inert-until-started
  lifecycle, image copy born with its points
- M3 unit 2.75 — handle lifecycle [sawlang#232]
- M3 unit 3 — give(handle, tag:) [sawlang#232]
- M3 unit 4 — Memory/IoMemory [sawlang#232]
- M3 unit 5 — quotas [sawlang#232]
- M3 unit 5.5 — death notifications [sawlang#232]
- M3 unit 6 — the money shot [sawlang#232]

## [BACKLOG] — filed, not scheduled

- sawlang#238 unit 6 remainder — CI cold-fetch acceptance + negative
  tests PEND sawlang becoming public at the pinned sha (a82e06f4);
  dispatches from the sawlang side [sawlang#238]
- First `sawlang.pin` bump — expected after sawlang design 218 unit 1.5
  (monomorphization) lands; bump version + sha TOGETHER [sawlang#238
  D-b2]
- M4 seeds — pipes + PipeReplyHandle IPC (select-with-timeout via
  Timer), IOMMU driver + critical processes, priorities/§7 bands,
  SMP + IntrSpinLock (after channels; unit 1.5's point map is its
  conversion guide), FP in userspace, vDSO true-mapping [sawlang#232
  "Explicitly out"]
