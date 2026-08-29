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
  `give` is the answer to). OPEN: nothing
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
