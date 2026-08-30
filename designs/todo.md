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

- M3 unit 5 — quotas [sawlang#232]
- M3 unit 5.5 — death notifications [sawlang#232]
- M3 unit 6 — the money shot [sawlang#232]

## [BACKLOG] — filed, not scheduled

- tools/sosimg_dump.py — landed Aug 29 (user-requested dev tool, this
  line is its capture): dumps sosimg v3 headers/segments and raw v2
  region tables; kept in step with imgformat + process.saw by hand —
  a format bump edits it too

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
  rows, so it needs a unit that authorizes them by name. **CARRIED past
  unit 4 unchanged** [#6]: that unit's authorization named exactly two
  cases (the uart-echo pair), so the same reason applied again
- `SegFlag.Device` RETIREMENT — a PIN-BUMP EVENT, sawlang-side [#6 D-6].
  M3 unit 4 migrated both driver packages to obtained-not-declared, so
  NO IMAGE IN THIS TREE declares a device window any more and the path
  is DORMANT. What survives is `imgformat`'s `SegFlag.Device` case, the
  emitter that writes it (`blade/sosimg.saw` — SAWLANG-SIDE, which is
  why this is a pin bump and not an edit here), and the in-tree
  consumers it feeds: `kcore.loader`'s `check_device_grant`,
  `check_segment`'s device branch, `place_image`'s device arm, and
  `validate_image`'s `allow_device` parameter (still `true` for the
  boot door, `false` for `process_create`). Retiring it is: delete the
  flag and the manifest key sawlang-side, bump `sawlang.pin`'s two
  lines together, then delete the four in-tree consumers and the
  `device: Bool` field on `GrantRow` becomes the only device signal
  (set by `IoMemoryOp.Map`). Nothing about the kernel's device MAPPING
  changes — `prot_device` and `GrantRow.device` are unit 4's path and
  are unaffected
- M4 seeds — pipes + PipeReplyHandle IPC (select-with-timeout via
  Timer), IOMMU driver + critical processes, priorities/§7 bands,
  SMP + IntrSpinLock (after channels; unit 1.5's point map is its
  conversion guide), FP in userspace, vDSO true-mapping [sawlang#232
  "Explicitly out"]

## [SAWLANG] — deficiencies met building sawos, tracked here for upstream resolution
One entry per issue, resolution-sufficient: the symptom verbatim, the probe/site, the workaround in-tree, and what upstream resolution looks like. The user references these from sawlang; entries close here when a pin bump delivers the fix.

- SL-1 — NO RUNTIME-INSTALLABLE FUNCTION HOOK (freestanding): a mutable static holding a function address is refused (``static `DELIVER` must be initialized by a compile-time constant``, probed against pinned sawc a82e06f4 during design 1). Design 186's static-init rule as specified, but the capability gap is real: freestanding code cannot install a boot-time dispatch hook at all (FuncPointer statics are immutable, unsafe static var requires const init, no Option-of-FuncPointer zero form). Workaround: design 1 restructured module altitudes instead (kcore.preempt). Resolution: a ruled story for late-bound function slots — a zero/None-initializable FuncPointer form, or a blessed once-set static tier.
- SL-2 — PLATFORM-WIDTH STATIC DOES NOT ADOPT A CONST EXPRESSION: `static M: UInt = (1 << BITS) - 1` over `static BITS: Int` is refused; DF-240a's adoption reaches FIXED-WIDTH slots only, and platform `UInt` is not one (design 3, kernel/abi HANDLE_INDEX_MASK — the `as UInt` workaround is written at the definition with this note). Resolution: widen const adoption to platform-integer slots, or rule the asymmetry permanent and document it beside DF-240a.
- SL-3 — NO CONSUME-WITHOUT-DEINIT (mem::forget equivalent): a NoCopy value whose deinit must NOT run after its payload's ownership left by other means (a kernel moved the handle word) has no language spelling; sawos uses the NO_HANDLE-sentinel-in-the-field idiom, disarm-before-syscall, recorded as the transfer-funnel contract (design 3 D-5, design 4 funnels). Livable; a `forget`/dissolve construct would retire the sentinel pattern. Low urgency, filed for the record.
- SL-4 — AMBIGUITY DIAGNOSTIC ANCHORS AT 1:1 WITH `<unknown>` MODULE: ``ambiguous struct `Thread`: defined in both `<unknown>` and `sos.thread` `` reports at line 1:1 of an arbitrary file — the location and the `<unknown>` are both noise (design 5, the std.task Thread<T> prelude collision). Resolution: real span + real module name on the ambiguity path.
- SL-5 — DECLARED-VS-IMPORTED NAME PRECEDENCE ASYMMETRY AGAINST THE PRELUDE: a locally DECLARED type name beats a prelude generic of the same name; a selectively IMPORTED one ties with it and errors (design 5: `struct Thread` won in-file, `import sos.thread.{Thread}` lost to std.task's `Thread<T>` — worked around with the qualifier at five sites). Resolution: rule which precedence is intended and make the two paths agree.
- SL-6 — `extern "C"` DECLARATIONS ARE PRIVATE-BY-CONSTRUCTION AND UNSHAREABLE: an extern decl carries no visibility modifier, and two sibling modules declaring one symbol is a hard ambiguity at the importer (probed, design 5) — which forces a package's entire extern surface into one bottom module. Resolution: visibility on extern blocks, or a ruled one-owner-module convention documented upstream.
- SL-7 — EXTENSION-METHOD LOOKUP DOES NOT FOLLOW FACADE RE-EXPORTS (designs 142/229 by design): a type's extension methods must live in its declaring module, so mutually-referential types cannot be split across files even behind a facade (design 5 finding 1 — System/Process/BootHandle are one 724-line module with three banners). Resolution is a language design question, named in the finding: an internal/forward-declaration tier, or lookup that follows `public import`. **SECOND SITE, design 6**: it also decides METHOD PLACEMENT, not just file layout. `Memory.map(into: &Process)` is unwritable — `BootHandle` carries a `Memory?` and an `IoMemory?`, so both region modules sit below `sos.system` and a `map` written in either naming `Process` is the DF-232e cycle. The funnels became `Process.map(memory:access:)` / `Process.map(iomemory:)` instead. That reads well here (it matches the `give` overloads), but the constraint chose it rather than the design.
- SL-8 — DF-172d STILL BITES (already filed upstream as DF-172d; listed here as a cross-reference, not a new issue): unbracketed binary expressions do not wrap across lines; hit again in designs 2 and 4. Resolution tracked in sawlang's own DF. **THIRD SITE, design 6**: `if (a & X) != 0\n && (b & Y) == 0 {` in `kcore.dispatch`'s `map_access`, fixed by parenthesising the whole condition.
- SL-9 — A LONE RAW-BACKED ENUM CASE DOES NOT ADOPT A FIXED-WIDTH SLOT, THOUGH A COMBINATION OF THEM DOES: ``static `RO` has type `UInt32` but its initializer has type `MapAccess` `` (design 6, `tests/map-basics`, pinned sawc a82e06f4). `static RW: UInt32 = MapAccess.Read | MapAccess.Write` COMPILES and folds to 3 — DF-240a's flag-enum rule — while `static RO: UInt32 = MapAccess.Read` at the same slot is refused, so ONE bit needs an `as UInt32` projection and TWO bits do not. Minimal example: `enum E: UInt32 { case A = 1, case B = 2 }` then `static X: UInt32 = E.A | E.B` (ok) beside `static Y: UInt32 = E.A` (error). Workaround in-tree: write `MapAccess.Read as UInt32`, with the asymmetry noted at the line. Resolution: let a bare case adopt a fixed-width slot exactly as a combination does (the value is as constant either way), or rule the asymmetry permanent and say so beside DF-240a — the current state teaches that adding a second flag REMOVES a cast, which is backwards.
