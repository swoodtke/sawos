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

- ~~sysapi split~~ **BUILT Aug 29 — branch parked** [user-requested
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
- `event-wake` / `event-consume-wake` rewritten to ONE HANDLE EACH,
  now that `MINT_OP` exists [#4 finding 5; design 3 finding 2]. They
  share an Event handle by ADDRESS through a parked `UnsafePointer`
  because no op could mint a second one; a mint makes the shape those
  programs always wanted writable. AUTHORIZED AS BACKLOG by the lead
  and deliberately NOT done in unit 3 — it moves shipped transcript
  rows, so it needs a unit that authorizes them by name
- M4 seeds — pipes + PipeReplyHandle IPC (select-with-timeout via
  Timer), IOMMU driver + critical processes, priorities/§7 bands,
  SMP + IntrSpinLock (after channels; unit 1.5's point map is its
  conversion guide), FP in userspace, vDSO true-mapping [sawlang#232
  "Explicitly out"]
