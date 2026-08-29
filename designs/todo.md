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

- M3 unit 3 — give(handle, tag:) [sawlang#232 Aug-16 launch-flow
  ruling, #4 — designs/004-give.md, authored Aug 29]: give as
  unbind-and-rebind rights-verbatim (2.75 composes: giver's word goes
  stale), tags the only cross-process vocabulary, per-process boot
  queues, start(boot_tag:) kernel-resolved a0, give-before-start the
  frozen barrier, child Process handle re-ruled full-set+Transfer
  (supervise OR donate — the second-handle question is 5.5's). No
  authorized transcript changes. DISPATCHED Aug 29.
  **BUILT Aug 29 — branch parked for review.** Gate: 120/120 across
  riscv32 + arm64 (60 cases each) against a 108 baseline at the merge
  base; the 108 pre-existing rows byte-identical in name, verdict and
  ORDER, six new rows appended per architecture. Landed as briefed
  except for ONE DEVIATION the lead must rule on, recorded in full in
  the brief's As-built: **the donation of a child's own Process handle
  happens AT the start barrier (`BootTagForm.Donate`), not as a
  separate `give`**, because `Start` is an op on the very handle being
  moved — a launcher that gave it away first has nothing left to start
  the child with, so D-3's give-then-start sequence cannot be written.
  Everything else about it is an ordinary give (same fault set, same
  unbind-and-rebind, same rights verbatim, same tagged record), and
  the alternative — a SECOND handle onto one process — is design 3's
  finding-2 re-mint question, which is unit 5.5's. Two smaller items
  for the lead: one new kernel report line (`SOS: process exit:
  code=…`, child-only, unreachable for every pre-existing case) exists
  because a donated child has no other voice; and `FaultReason
  .DuplicateKey`'s text still names attachments, unchanged because
  `event_dupkey` asserts it and no expectation edit was authorized
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
