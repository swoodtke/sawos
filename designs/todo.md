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

## [BACKLOG] — filed, not scheduled

- M4 scoping — pipes [#10 — designs/010-m4-pipes.md, DRAFT Aug 30,
  awaiting user review]: §2.1 carried by reference; waiter revocation
  as unit 0 (the Aug-30 ruling — the free arm wakes parked threads,
  answering #7 finding 2 as "wake, don't count"); the role-split
  endpoint pair; a 7-unit ladder + decisions agenda
- tools/sosimg_dump.py — landed Aug 29 (user-requested dev tool, this
  line is its capture): dumps sosimg v3 headers/segments and raw v2
  region tables; kept in step with imgformat + process.saw by hand —
  a format bump edits it too
- sos_runner `--case NAME` — landed Aug 30 (user-requested dev
  convenience, this line is its capture): run named cases only
  (repeatable / comma-separated, `-`/`_` interchangeable, did-you-mean
  on unknowns); the filter narrows the package builds too. NEVER the
  gate — the gate stays every case on every architecture

- tests idiom sweep — landed Aug 31 (user-ruled, this line is its
  capture): 290 bind-or-bail match-on-Result sites across 55 test
  packages became the inline try/catch guard form (CLAUDE.md carries
  the ruling); 75 statement-position sites stay `match` on SL-12's
  ICE, 25 fold-shaped sites (Err supplies a value) left as a possible
  follow-on, 12 real-work + 4 negative-test matches stay by design
- sawlang#238 unit 6 remainder — CI cold-fetch acceptance + negative
  tests PEND sawlang becoming public at the pinned sha (a82e06f4);
  dispatches from the sawlang side [sawlang#238]
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
- SL-2 — PLATFORM-WIDTH STATIC DOES NOT ADOPT A CONST EXPRESSION: `static M: UInt = (1 << BITS) - 1` over `static BITS: Int` is refused; DF-240a's adoption reaches FIXED-WIDTH slots only, and platform `UInt` is not one (design 3, kernel/abi HANDLE_INDEX_MASK — the `as UInt` workaround is written at the definition with this note). Resolution: widen const adoption to platform-integer slots, or rule the asymmetry permanent and document it beside DF-240a. **FIX IN FLIGHT (user, sawlang design 257 / DF-282a, Aug 31): closes at the NEXT pin bump (0.3.0). At closure, sweep the `as UInt` workaround at `HANDLE_INDEX_MASK` and any sibling sites the entry names.**
- SL-3 — NO CONSUME-WITHOUT-DEINIT (mem::forget equivalent): a NoCopy value whose deinit must NOT run after its payload's ownership left by other means (a kernel moved the handle word) has no language spelling; sawos uses the NO_HANDLE-sentinel-in-the-field idiom, disarm-before-syscall, recorded as the transfer-funnel contract (design 3 D-5, design 4 funnels). Livable; a `forget`/dissolve construct would retire the sentinel pattern. Low urgency, filed for the record.
- SL-4 — AMBIGUITY DIAGNOSTIC ANCHORS AT 1:1 WITH `<unknown>` MODULE: ``ambiguous struct `Thread`: defined in both `<unknown>` and `sos.thread` `` reports at line 1:1 of an arbitrary file — the location and the `<unknown>` are both noise (design 5, the std.task Thread<T> prelude collision). Resolution: real span + real module name on the ambiguity path.
- SL-5 — DECLARED-VS-IMPORTED NAME PRECEDENCE ASYMMETRY AGAINST THE PRELUDE: a locally DECLARED type name beats a prelude generic of the same name; a selectively IMPORTED one ties with it and errors (design 5: `struct Thread` won in-file, `import sos.thread.{Thread}` lost to std.task's `Thread<T>` — worked around with the qualifier at five sites). Resolution: rule which precedence is intended and make the two paths agree.
- SL-6 — `extern "C"` DECLARATIONS ARE PRIVATE-BY-CONSTRUCTION AND UNSHAREABLE: an extern decl carries no visibility modifier, and two sibling modules declaring one symbol is a hard ambiguity at the importer (probed, design 5) — which forces a package's entire extern surface into one bottom module. Resolution: visibility on extern blocks, or a ruled one-owner-module convention documented upstream.
- SL-7 — EXTENSION-METHOD LOOKUP DOES NOT FOLLOW FACADE RE-EXPORTS (designs 142/229 by design): a type's extension methods must live in its declaring module, so mutually-referential types cannot be split across files even behind a facade (design 5 finding 1 — System/Process/BootHandle are one 724-line module with three banners). Resolution is a language design question, named in the finding: an internal/forward-declaration tier, or lookup that follows `public import`. **SECOND SITE, design 6**: it also decides METHOD PLACEMENT, not just file layout. `Memory.map(into: &Process)` is unwritable — `BootHandle` carries a `Memory?` and an `IoMemory?`, so both region modules sit below `sos.system` and a `map` written in either naming `Process` is the DF-232e cycle. The funnels became `Process.map(memory:access:)` / `Process.map(iomemory:)` instead. That reads well here (it matches the `give` overloads), but the constraint chose it rather than the design. **THIRD SITE, design 8 — and this one MOVED A SPELLING THE BRIEF HAD RULED.** Design 8's ruled surface is `Waiter.add(process:, key:)`, the fourth member of the overload list `sos.waiter` already holds; it is unwritable, because `waiter` sits below `system` (its three `add` overloads read the waitables' handle fields, which is what put it above THEM) and `Process` is declared in `system` with `System` and `BootHandle`, all three mutually referential. So the surface is `Process.attach(waiter:, key:)` — design 6's flip applied verbatim — with a comment in `waiter.saw` where the fourth overload would have gone. The alternative was rejected on the same finding's other half: an `extension Waiter` written in `system.saw` IS legal under the orphan rule, but design 142 scopes extension-method LOOKUP to the declaring module plus the caller's DIRECT imports, so every consumer would have owed an `import sos.system` whose purpose nothing on the page explains. Resolution unchanged and now three-sited: an internal/forward-declaration tier, or lookup that follows `public import`. **THIRD SITE RE-LITIGATED AND UPHELD (lead, Aug 30):** a sawlang-side analysis (relayed by the user) proposed restoring `Waiter.add(process:)` via `extension Waiter` in the Process-declaring module, arguing the overload is visible to exactly the files that hold a Process. Verified against LANGUAGE_SPEC (extension scoping §142; re-export: "it does not widen extension scope") and REFUTED for this package: every consumer takes `Waiter` and `Process` through the `sos` facade's `public import`, never by importing `sos.system` directly, so the overload would be invisible at every real call site — and the same analysis's "move all typed overloads into the waitables' own modules" would break the three EXISTING `add` overloads the same way (they are visible today only because they are `sos.waiter`'s inherent API). A facade-placed extension fails separately: the body needs `process.handle`, deliberately module-private to `sos.system` (the no-raw-word surface guarantee). `Process.attach` stands. The proposal's one durable yield: the upstream resolution is now CONFIRMED to be "extension lookup that follows `public import`" — the other language-side fixes do not help a facade package. **FIX IN FLIGHT (user, sawlang-side, Aug 30):** extension lookup will follow `public import`; the user is implementing it and the seed edits (the `extension Waiter` in `sos.system`, the respelled test call sites) sit UNCOMMITTED in the working tree awaiting the pin bump that delivers it. The respell entry below is the capture; this entry closes when that bump lands. **CLOSED (Aug 30) — THE FIX LANDED: sawlang 0.2.0 @ `3f15d2ee`, delivered by the FIRST PIN BUMP.** Extension lookup follows `public import` now; the respell executed with the bump (`Waiter.add(process:, key:)` via `extension Waiter` in `system.saw`, funnel `public(package)`, `Process.attach` retired, three call sites respelled, facade header + design 8 As-built rider written). The first two sites' placements stand — the fix moves methods, not declarations — so the file-layout half of this entry is RESOLVED-BY-TOOL for methods and MOOT for declarations.
- SL-8 — DF-172d STILL BITES (already filed upstream as DF-172d; listed here as a cross-reference, not a new issue): unbracketed binary expressions do not wrap across lines; hit again in designs 2 and 4. Resolution tracked in sawlang's own DF. **THIRD SITE, design 6**: `if (a & X) != 0\n && (b & Y) == 0 {` in `kcore.dispatch`'s `map_access`, fixed by parenthesising the whole condition.
- SL-9 — A LONE RAW-BACKED ENUM CASE DOES NOT ADOPT A FIXED-WIDTH SLOT, THOUGH A COMBINATION OF THEM DOES: ``static `RO` has type `UInt32` but its initializer has type `MapAccess` `` (design 6, `tests/map-basics`, pinned sawc a82e06f4). `static RW: UInt32 = MapAccess.Read | MapAccess.Write` COMPILES and folds to 3 — DF-240a's flag-enum rule — while `static RO: UInt32 = MapAccess.Read` at the same slot is refused, so ONE bit needs an `as UInt32` projection and TWO bits do not. Minimal example: `enum E: UInt32 { case A = 1, case B = 2 }` then `static X: UInt32 = E.A | E.B` (ok) beside `static Y: UInt32 = E.A` (error). Workaround in-tree: write `MapAccess.Read as UInt32`, with the asymmetry noted at the line. Resolution: let a bare case adopt a fixed-width slot exactly as a combination does (the value is as constant either way), or rule the asymmetry permanent and say so beside DF-240a — the current state teaches that adding a second flag REMOVES a cast, which is backwards. **FIX IN FLIGHT (user, sawlang design 257 / DF-282b, Aug 31): closes at the NEXT pin bump (0.3.0). At closure, sweep the recorded workaround sites.**
- SL-11 — AN EXTENSION-METHOD OVERLOAD SET BINDS ONLY ITS FIRST MEMBER WHEN THE RECEIVER TYPE'S NAME IS NOT IMPORTED: ``missing argument for parameter `entry` `` anchored at the first LABEL of the call (design 12, `tests/waiter-revoked`, sawlang 0.2.0 @ `3f15d2ee`). `import sos.{System, SosStatus, ...}` — everything the file names EXCEPT `Process` — makes `proc.thread_create(stack_top:, arg:)` unresolvable, because only the FIRST of the two `thread_create` overloads declared in `extension Process` is bound and the arity mismatch is then reported against it; adding `Process` to the same import list makes both members visible and the call compile. Nothing in the file writes the word `Process`, so the import is pure ceremony that only the diagnostic asks for — and the diagnostic points at an argument, not at an import. **THIS IS DF-242b'S RULE AT THE OTHER DECLARATION KIND.** Design 249 ruled that an import binds the WHOLE overload set a free-function name stands for, and DF-242b fixed the bare form that used to bind the first member only; extension METHODS were not swept with it, and a method is not imported by name at all — it is reached through design 142's extension neighbourhood — so the set that gets bound is decided by whatever put the receiver type in scope. Minimal repro, three files, no facade needed (a direct import fails identically, so this is NOT SL-7's re-export question):
  ```saw
  // dep.saw
  public struct Crate { public n: Int }
  extension Crate {
      public func make(&self, entry: Int, top: Int) -> Int { entry + top }
      public func make(&self, top: Int) -> Int { self.make(entry: 7, top: top) }
  }
  public func hand() -> Crate { Crate(n: 1) }

  // main.saw
  import dep.{hand}                 // error: missing argument for parameter `entry`
  import dep.{Crate, hand}          // compiles, prints 8
  func main() { let c = hand()  print("{}", c.make(top: 1)) }
  ```
  Workaround in-tree: import the receiver type by name, with the reason written at the import (`tests/waiter-revoked/src/main.saw`). The tree had not met this before because every other root server imports `Process` and `Thread` anyway — a program that only ever HOLDS a value of a type, never names it, is the shape that hits it. Resolution: bind the whole overload set for an extension method exactly as design 249 binds one for a free function, keyed on the receiver type rather than on which of its names an import happened to mention; failing that, a diagnostic that names the unbound sibling and the import that would bind it, since the current one sends the reader to the argument list. **FIX IN FLIGHT (user, sawlang design 256 / DF-280a, Aug 31): closes at the NEXT pin bump (0.3.0). At closure, drop the ceremony `Process` import in `tests/waiter-revoked` and re-verify the three-file repro resolves.**
- SL-12 — STATEMENT-POSITION `try ... catch` ON A `Result<Void, E>` CALL IS AN INTERNAL COMPILER ERROR: ``internal compiler error at src/main.saw:257:5 (TryExpr): 'NoneType' object has no attribute 'type'`` (the Aug-31 idiom sweep, sawlang 0.2.0 @ `3f15d2ee`). `try give(...) catch { ... }` as a bare STATEMENT — and the `let _ =` spelling identically — dies in sawc when the callee's Ok type is `Void`; the same catch with a non-Void Ok compiles and runs, so it is the Void, not the discard. Probe: any `Result<Void, SosStatus>` op (`give`/`start`/`waiter.add`/`timer.arm`/`interrupt.ack`/`waiter.remove`/`mapping.unmap`) under a statement-position inline catch. In-tree workaround: statement-position checks stay `match { case Ok(_) -> {}, case Err(e) -> ... }` — 75 such sites deliberately kept at the Aug-31 sweep, and CLAUDE.md's idiom ruling carries the caveat until the fix ships in a pin. Resolution: the inline catch's lowering handles a Void Ok payload (nothing to bind is not nothing to type); the guard form should be legal at statement position exactly as the binding form is. **CLOSED (second pin bump, sawlang 0.2.1 @ `8ffc5809`, Aug 31):** DF-281a fixed it fix-on-discovery; the statement-position guard form is legal, CLAUDE.md's caveat is lifted, and the 75 kept `match` sites are a queued conversion pass.
