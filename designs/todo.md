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
  **BUILT Aug 28, branch PARKED for user review.** All of D-1..D-7
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
