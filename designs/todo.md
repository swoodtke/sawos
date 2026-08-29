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
