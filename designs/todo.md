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

- M5: the memory milestone — `designs/025` (RULED Sep 3, the plan of
  record): tier climbs units 1→4 in order; allocator track 5→6→6b
  arch-free, may pipeline in a parallel worktree; stats region 7;
  docs sweep 8 closes; arena+kernel-stack unit slots at convenience.
  Unit briefs dispatch from the sketch.

## [BACKLOG] — filed, not scheduled

- **`PipeRequestRight.Reply` HAS NO TEST ANY MORE** [#22 As-built
  finding 1, Sep 2]. Ruling 12(c) took `Mint` out of
  `pipe_request_rights()`, and the only way this tree ever produced a
  `PipeRequest` handle WITHOUT `Reply` was `pipe-no-reply` minting an
  attenuated sibling — so that spelling died with the bit and the case
  retargeted to the one-name rule instead. The gate itself is still in
  `pipe_request_op_decoded` and is now reachable ONLY through a `give`
  keep mask into another process (`Take` mints the full set; a
  message-carried handle carries the sender's entry rights verbatim),
  which is a two-process case: root posts, takes the obligation, gives
  it to a child with `keep = Transfer | Wait`, and the child's `reply`
  faults with `AccessDenied`. It wants a new child package (~60 lines,
  `child-narrow`'s shape) and one runner entry. THE SAME HOLE EXISTS AT
  `PipeReplyRight.Resolve` for the same reason and would be covered by
  the same case shape at the other end. Filed rather than done because
  design 22's scope was the discipline, not the coverage the discipline
  displaced.

- `Process` and `System` in messages — a sysapi MODULE-SPLIT ruling
  [#18 As-built finding 1, Sep 2]: `PipeHandle` ships six cases
  (`Memory`, `IoMemory`, `PipeInlet`, `PipeOutlet`, `PipeReply`,
  `PipeRequest`) and the kernel refuses the other two AT THE SENDER in
  `msg_kind_of`. The obstacle is module ORDER and not doctrine —
  `sos.pipe` sits below `sos.system` and `sos.process` (both name pipe
  types in their `give` and factory surfaces), so a case holding a
  `Process` wrapper is a cycle, DF-232e's shape. The scouted path: split
  the wrapper STRUCTS into a leaf module both layers import and leave
  the METHODS where they are, which Saw's extensions make mechanical
  since a type's declaration and its methods need not share a file. It
  moves every handle wrapper in the package, which is why it wants a
  lead ruling before it is written; what it buys is a launcher able to
  hand a child a Process or System handle AFTER `start`, which no
  in-tree program needs today. The kernel side is one line in
  `msg_kind_of` when it comes

- LAZY HANDLE DECODE in `PipeMsg` — the message vocabulary's image cost
  [#18 As-built §12, Sep 2]: unit 4's transcript accounting measured
  +26.7% (riscv32) / +22.6% (arm64) across every image that names the
  pipe or wait surface, and the cause is the TYPED value rather than the
  wire record — `PipeMsg` grew from `{len, bytes}` to
  `{len, [PipeHandle?; 4], bytes}`, and a `PipeHandle` is an enum of six
  `NoCopy` wrappers, so every take/resolve/wait caller moves a bigger
  value and links drop glue for four optional six-way enums. It lands
  hardest where it is least deserved: `tests/event-wake` waits on an
  Event, touches no pipe, and grew 5,584 B / 8,192 B, because
  `decode_wait` (4,934 B on riscv32), `wait_message` (962 B) and
  `decode_handle` (568 B) link into EVERY image that calls `wait`
  whether or not its attachments could produce a message. The lever:
  keep the kind bytes and the handle WORDS in `PipeMsg` and construct a
  wrapper only when the receiver asks for a slot
  (`msg.take_handle(0) -> PipeHandle?`), which moves the six-way
  construction out of `decode_wait` into a function only a
  handle-reading program links; the drop then walks words rather than
  matching enums. No kernel and no ABI consequence — it is entirely
  inside `sos.pipe`. NOT done in unit 4 because it rewrites the
  RECEIVING VOCABULARY that unit's §API was user-reviewed on, and the
  ergonomics trade (a match on an optional field becomes a call then a
  match) is a taste question the lead should answer

  **DISPOSITION (user, Sep 2): deliberately UNSCHEDULED** — the M5
  namespace makes handle-receipt universal (open() answers
  capabilities in messages), and XIP on ESP32-class parts lands the
  code tax on flash, not SRAM. Revisit trigger: a genuinely
  tight-SRAM tier target; if it fires, prefer sawc-side drop-glue
  dedup BEFORE this API rewrite. **[RIDER, Sep 3, the design-25
  session: the "M5 namespace" premise is STALE — the namespace was
  ruled OUT of M5 (designs/025 seed 3). The disposition itself
  stands on its other two legs (XIP + the revisit trigger).]**
- buffered `debug_print` — a length-taking form [#16 As-built finding,
  Sep 1]: today the seam traps once per BYTE, so a test's prose
  outweighs its object ops ~10:1 in the syscall column
  (`pipe_oneshot`: ~5 traps of exchange, ~300 of description). A
  buffered form taking (addr, len) is the obvious answer and an ABI
  change with no consumer yet; filed for the unit that first wants
  the column quiet. The interrupt column measures preemption pressure
  on ONE process, not machine load — restate wherever the
  shared-region `top` seed (#16) gets built.

- transfer-funnel dissolve helpers [SL-3 remainder, SL-16 closure
  finding, Sep 2]: the by-value-PARAMETER funnels still disarm by hand
  (`Process.give(memory:)`, `give(system:)`, and the four `Waiter.give`
  overloads in `kernel/sysapi/src/pipe.saw`), because design 260's
  `consumes` is a RECEIVER-position effect and those take their wrapper
  as an argument. Each body is the same four lines: bind the moved-in
  value, read the word, write `NO_HANDLE`, call the attach. A private
  `consumes` helper on each wrapper —
  `func dissolve(&var self) consumes -> UInt { self.handle }`, legal
  because it is declared in the type's own module — collapses that to
  `let word = (move owned).dissolve()` and retires the last sentinel
  writes outside the deinits. NOT DONE at SL-16's closure by
  instruction: that sweep converted RECEIVERS, and this is a second
  shape with its own review surface — it puts a teardown-substituting
  method on every handle wrapper in the package, which is a taste
  question the lead should answer before it is written six times.
  `PipeReply.resolve` is NOT a candidate and never will be: its
  consumption is CONDITIONAL, which is the one shape the effect cannot
  express

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
- SL-2 — PLATFORM-WIDTH STATIC DOES NOT ADOPT A CONST EXPRESSION: `static M: UInt = (1 << BITS) - 1` over `static BITS: Int` is refused; DF-240a's adoption reaches FIXED-WIDTH slots only, and platform `UInt` is not one (design 3, kernel/abi HANDLE_INDEX_MASK — the `as UInt` workaround is written at the definition with this note). Resolution: widen const adoption to platform-integer slots, or rule the asymmetry permanent and document it beside DF-240a. **FIX IN FLIGHT (user, sawlang design 257 / DF-282a, Aug 31): closes at the NEXT pin bump (0.3.0). At closure, sweep the `as UInt` workaround at `HANDLE_INDEX_MASK` and any sibling sites the entry names.** **CLOSED (third pin bump, sawlang 0.3.0 @ `66e353da`, Sep 1):** design 257's const-adoption ladder reaches platform-integer slots; the `as UInt` and its recorded rationale at `HANDLE_INDEX_MASK` are swept (the entry named no sibling sites, and a tree grep confirmed none), and the derivation now folds unadorned.
- SL-3 — NO CONSUME-WITHOUT-DEINIT (mem::forget equivalent): a NoCopy value whose deinit must NOT run after its payload's ownership left by other means (a kernel moved the handle word) has no language spelling; sawos uses the NO_HANDLE-sentinel-in-the-field idiom, disarm-before-syscall, recorded as the transfer-funnel contract (design 3 D-5, design 4 funnels). Livable; a `forget`/dissolve construct would retire the sentinel pattern. Low urgency, filed for the record. **PARTLY ANSWERED (fourth pin bump, sawlang 0.4.0 @ `46eebb36`, Sep 2), and the remainder is named.** Design 260's `consumes` IS the dissolve construct for one position: a consuming body substitutes for the custom `deinit` body, so a method that hands the word to the kernel simply does not run the release, and SL-16's closure retired the sentinel at `PipeRequest.reply` and `PipeRequest.reply_wait` on exactly that mechanism. WHAT IT DOES NOT REACH, and why the sentinel is still in the tree: (a) `consumes` is RECEIVER-position only, so every funnel taking its wrapper as a by-value PARAMETER — `Process.give(memory:)`, `give(system:)`, the four `Waiter.give` overloads — still disarms by hand; the in-tree answer is a private `consumes` dissolve helper per wrapper, which is the backlog item above and needs no language change. (b) A funnel whose consumption is CONDITIONAL — `PipeReply.resolve`, which consumes on two paths out of three — cannot use the effect at all, because `consumes` consumes on every path. That third shape was not priced when this entry was filed and is the one that keeps the sentinel a permanent idiom here rather than a migration.
- SL-4 — AMBIGUITY DIAGNOSTIC ANCHORS AT 1:1 WITH `<unknown>` MODULE: ``ambiguous struct `Thread`: defined in both `<unknown>` and `sos.thread` `` reports at line 1:1 of an arbitrary file — the location and the `<unknown>` are both noise (design 5, the std.task Thread<T> prelude collision). Resolution: real span + real module name on the ambiguity path.
- SL-5 — DECLARED-VS-IMPORTED NAME PRECEDENCE ASYMMETRY AGAINST THE PRELUDE: a locally DECLARED type name beats a prelude generic of the same name; a selectively IMPORTED one ties with it and errors (design 5: `struct Thread` won in-file, `import sos.thread.{Thread}` lost to std.task's `Thread<T>` — worked around with the qualifier at five sites). Resolution: rule which precedence is intended and make the two paths agree.
- SL-6 — `extern "C"` DECLARATIONS ARE PRIVATE-BY-CONSTRUCTION AND UNSHAREABLE: an extern decl carries no visibility modifier, and two sibling modules declaring one symbol is a hard ambiguity at the importer (probed, design 5) — which forces a package's entire extern surface into one bottom module. Resolution: visibility on extern blocks, or a ruled one-owner-module convention documented upstream.
- SL-7 — EXTENSION-METHOD LOOKUP DOES NOT FOLLOW FACADE RE-EXPORTS (designs 142/229 by design): a type's extension methods must live in its declaring module, so mutually-referential types cannot be split across files even behind a facade (design 5 finding 1 — System/Process/BootHandle are one 724-line module with three banners). Resolution is a language design question, named in the finding: an internal/forward-declaration tier, or lookup that follows `public import`. **SECOND SITE, design 6**: it also decides METHOD PLACEMENT, not just file layout. `Memory.map(into: &Process)` is unwritable — `BootHandle` carries a `Memory?` and an `IoMemory?`, so both region modules sit below `sos.system` and a `map` written in either naming `Process` is the DF-232e cycle. The funnels became `Process.map(memory:access:)` / `Process.map(iomemory:)` instead. That reads well here (it matches the `give` overloads), but the constraint chose it rather than the design. **THIRD SITE, design 8 — and this one MOVED A SPELLING THE BRIEF HAD RULED.** Design 8's ruled surface is `Waiter.add(process:, key:)`, the fourth member of the overload list `sos.waiter` already holds; it is unwritable, because `waiter` sits below `system` (its three `add` overloads read the waitables' handle fields, which is what put it above THEM) and `Process` is declared in `system` with `System` and `BootHandle`, all three mutually referential. So the surface is `Process.attach(waiter:, key:)` — design 6's flip applied verbatim — with a comment in `waiter.saw` where the fourth overload would have gone. The alternative was rejected on the same finding's other half: an `extension Waiter` written in `system.saw` IS legal under the orphan rule, but design 142 scopes extension-method LOOKUP to the declaring module plus the caller's DIRECT imports, so every consumer would have owed an `import sos.system` whose purpose nothing on the page explains. Resolution unchanged and now three-sited: an internal/forward-declaration tier, or lookup that follows `public import`. **THIRD SITE RE-LITIGATED AND UPHELD (lead, Aug 30):** a sawlang-side analysis (relayed by the user) proposed restoring `Waiter.add(process:)` via `extension Waiter` in the Process-declaring module, arguing the overload is visible to exactly the files that hold a Process. Verified against LANGUAGE_SPEC (extension scoping §142; re-export: "it does not widen extension scope") and REFUTED for this package: every consumer takes `Waiter` and `Process` through the `sos` facade's `public import`, never by importing `sos.system` directly, so the overload would be invisible at every real call site — and the same analysis's "move all typed overloads into the waitables' own modules" would break the three EXISTING `add` overloads the same way (they are visible today only because they are `sos.waiter`'s inherent API). A facade-placed extension fails separately: the body needs `process.handle`, deliberately module-private to `sos.system` (the no-raw-word surface guarantee). `Process.attach` stands. The proposal's one durable yield: the upstream resolution is now CONFIRMED to be "extension lookup that follows `public import`" — the other language-side fixes do not help a facade package. **FIX IN FLIGHT (user, sawlang-side, Aug 30):** extension lookup will follow `public import`; the user is implementing it and the seed edits (the `extension Waiter` in `sos.system`, the respelled test call sites) sit UNCOMMITTED in the working tree awaiting the pin bump that delivers it. The respell entry below is the capture; this entry closes when that bump lands. **CLOSED (Aug 30) — THE FIX LANDED: sawlang 0.2.0 @ `3f15d2ee`, delivered by the FIRST PIN BUMP.** Extension lookup follows `public import` now; the respell executed with the bump (`Waiter.add(process:, key:)` via `extension Waiter` in `system.saw`, funnel `public(package)`, `Process.attach` retired, three call sites respelled, facade header + design 8 As-built rider written). The first two sites' placements stand — the fix moves methods, not declarations — so the file-layout half of this entry is RESOLVED-BY-TOOL for methods and MOOT for declarations.
- SL-8 — DF-172d STILL BITES (already filed upstream as DF-172d; listed here as a cross-reference, not a new issue): unbracketed binary expressions do not wrap across lines; hit again in designs 2 and 4. Resolution tracked in sawlang's own DF. **THIRD SITE, design 6**: `if (a & X) != 0\n && (b & Y) == 0 {` in `kcore.dispatch`'s `map_access`, fixed by parenthesising the whole condition. **FOURTH AND FIFTH SITES, design 21** (`tests/svc-uart-*`), and the second of them is a shape the earlier sites had not shown: an `if` CONDITION carrying a three-term sum (`traps == answered + 2 * discarded + 1`) is the familiar one, fixed by hoisting the arithmetic into a parenthesised `let`; but a wrapped ASSIGNMENT (`window_discarded =\n    window_discarded + 1`, split only because the arm was deeply indented) fails as `Unexpected token: NEWLINE` at the LINE BELOW, and its diagnostic points into the continuation rather than at the assignment that ran out of room. It bites hardest where indentation is deepest, which is exactly where a line is most likely to need wrapping; the in-tree fix was the compound form (`window_discarded += 1`), which fits.
- SL-9 — A LONE RAW-BACKED ENUM CASE DOES NOT ADOPT A FIXED-WIDTH SLOT, THOUGH A COMBINATION OF THEM DOES: ``static `RO` has type `UInt32` but its initializer has type `MapAccess` `` (design 6, `tests/map-basics`, pinned sawc a82e06f4). `static RW: UInt32 = MapAccess.Read | MapAccess.Write` COMPILES and folds to 3 — DF-240a's flag-enum rule — while `static RO: UInt32 = MapAccess.Read` at the same slot is refused, so ONE bit needs an `as UInt32` projection and TWO bits do not. Minimal example: `enum E: UInt32 { case A = 1, case B = 2 }` then `static X: UInt32 = E.A | E.B` (ok) beside `static Y: UInt32 = E.A` (error). Workaround in-tree: write `MapAccess.Read as UInt32`, with the asymmetry noted at the line. Resolution: let a bare case adopt a fixed-width slot exactly as a combination does (the value is as constant either way), or rule the asymmetry permanent and say so beside DF-240a — the current state teaches that adding a second flag REMOVES a cast, which is backwards. **FIX IN FLIGHT (user, sawlang design 257 / DF-282b, Aug 31): closes at the NEXT pin bump (0.3.0). At closure, sweep the recorded workaround sites.** **CLOSED (third pin bump, sawlang 0.3.0 @ `66e353da`, Sep 1):** a lone raw-backed case now adopts a fixed-width slot; `map-basics`' `RO` lost its `as UInt32` and the recorded comment, and `pipe-no-post`'s keep-mask comment dropped its stale lone-case clause (that mask was policy, not workaround — its value is untouched).
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
  Workaround in-tree: import the receiver type by name, with the reason written at the import (`tests/waiter-revoked/src/main.saw`). The tree had not met this before because every other root server imports `Process` and `Thread` anyway — a program that only ever HOLDS a value of a type, never names it, is the shape that hits it. Resolution: bind the whole overload set for an extension method exactly as design 249 binds one for a free function, keyed on the receiver type rather than on which of its names an import happened to mention; failing that, a diagnostic that names the unbound sibling and the import that would bind it, since the current one sends the reader to the argument list. **FIX IN FLIGHT (user, sawlang design 256 / DF-280a, Aug 31): closes at the NEXT pin bump (0.3.0). At closure, drop the ceremony `Process` import in `tests/waiter-revoked` and re-verify the three-file repro resolves.** **CLOSED (third pin bump, sawlang 0.3.0 @ `66e353da`, Sep 1):** design 256's identity-keyed resolver binds the whole overload set for a resolved receiver; the ceremony `Process` import and its comment are dropped from `tests/waiter-revoked`, and the minimal repro re-verified under sawc 0.3.0 — `import dep.{hand}` alone compiles and prints 8.
- SL-13 — A `&var [T; N]` PARAMETER'S ELEMENTS ARE NOT ASSIGNABLE, THOUGH THE SAME ARRAY REACHED THROUGH A `&var STRUCT` IS: ``error: cannot assign to element of immutable array `a` `` with ``hint: consider using `var` instead of `let` to make it mutable`` (design 14, `tests/pipe-oneshot`, sawc 0.3.0 @ `87063387`). A fixed-size array borrowed DIRECTLY is treated as immutable at an indexed write; borrowed as a field of a struct it is mutable, so the referent's mutability is being decided by how it was reached rather than by the sigil. The hint compounds it — there is no `let` anywhere in the program, and the parameter already says `&var`, so it sends the reader to a declaration that does not exist. LANGUAGE_SPEC's own DF-232a list puts "a `&var`/`self` referent" among the assignment targets, which is what made this look like a bug rather than a rule. Minimal repro, one file, hosted:
  ```saw
  func bump(a: &var [UInt8; 4]) {
      a[0] = 9                       // error: cannot assign to element of
  }                                  //        immutable array `a`

  struct Crate { n: Int, xs: [UInt8; 4] }
  func set_nested(b: &var Crate) {
      b.xs[0] = 9                    // compiles, and runs
  }
  ```
  Workaround in-tree: the array is not lent mutably at all — a message buffer is filled by the CALLER and passed `&`, and the two helpers that wanted to write one take the byte as a parameter instead (`tests/pipe-oneshot`). That is livable here because the arrays are small and caller-owned; a helper that genuinely fills a caller's buffer (the shape `read_into` has) has no spelling today short of wrapping the array in a one-field struct. Resolution: make an indexed write through a `&var [T; N]` legal, exactly as it is through a `&var` struct field — or, if the restriction is deliberate, say so in LANGUAGE_SPEC beside DF-232a's list and give the diagnostic a hint that names the wrapper-struct workaround instead of a `let` the program does not have.
  **SECOND SITE, design 21, AND IT SHAPED A PUBLIC API RATHER THAN A HELPER.** `tests/uartproto` is the /dev/uart0 protocol's message builder, and its whole job is to fill a `[UInt8; PIPE_BODY_BYTES]` a caller then hands to `post`/`send` — the shape this entry says has no spelling. The wrapper-struct workaround is what shipped: `public struct UartBody { public bytes: [UInt8; PIPE_BODY_BYTES] }`, with `&buf.bytes` at every send site. It reads acceptably and the reason is written at the declaration, but note what the restriction decided: a builder that could have been three free functions over a borrowed array is a TYPE, and every consumer of the protocol now names it. That is the first time this deficiency has reached a package's published surface rather than a private helper's signature.
- SL-12 — STATEMENT-POSITION `try ... catch` ON A `Result<Void, E>` CALL IS AN INTERNAL COMPILER ERROR: ``internal compiler error at src/main.saw:257:5 (TryExpr): 'NoneType' object has no attribute 'type'`` (the Aug-31 idiom sweep, sawlang 0.2.0 @ `3f15d2ee`). `try give(...) catch { ... }` as a bare STATEMENT — and the `let _ =` spelling identically — dies in sawc when the callee's Ok type is `Void`; the same catch with a non-Void Ok compiles and runs, so it is the Void, not the discard. Probe: any `Result<Void, SosStatus>` op (`give`/`start`/`waiter.add`/`timer.arm`/`interrupt.ack`/`waiter.remove`/`mapping.unmap`) under a statement-position inline catch. In-tree workaround: statement-position checks stay `match { case Ok(_) -> {}, case Err(e) -> ... }` — 75 such sites deliberately kept at the Aug-31 sweep, and CLAUDE.md's idiom ruling carries the caveat until the fix ships in a pin. Resolution: the inline catch's lowering handles a Void Ok payload (nothing to bind is not nothing to type); the guard form should be legal at statement position exactly as the binding form is. **CLOSED (second pin bump, sawlang 0.2.1 @ `8ffc5809`, Aug 31):** DF-281a fixed it fix-on-discovery; the statement-position guard form is legal, CLAUDE.md's caveat is lifted, and the 75 kept `match` sites are a queued conversion pass.
- SL-14 — A `move` INSIDE A **DIVERGING** INLINE CATCH RETIRES THE BINDING ON THE FALL-THROUGH PATH TOO: ``error: use of moved variable `owned` `` with ``hint: value was moved at line 14 and can no longer be used`` (design 15, `kernel/sysapi/src/waiter.saw`, sawc 0.3.0 @ `87063387`). The catch block ENDS IN A `return`, so its `move` runs only on the error path and the binding is provably still live below the `try` — but the move checker records the move unconditionally and refuses the next use. It is the exact shape ruling 11(c)'s "a refused give HANDS THE OBJECT BACK" wants: consume on the error path, disarm on the success path. Minimal repro, one file, hosted:
  ```saw
  struct Res { n: Int }
  extension Res: NoCopy {}

  func might(x: Int) -> Result<Void, Int> {
      if x == 0 { return 1 }
      return
  }

  func hand(r: Res, x: Int) -> Result<Void, (Int, Res)> {
      var owned = move r
      try might(x) catch {
          let back: (Int, Res) = (error, move owned)
          return move back          // the catch DIVERGES
      }
      owned.n = 0                   // error: use of moved variable `owned`
      return
  }
  ```
  Workaround in-tree: the eight `Waiter.give` overloads disarm BEFORE the syscall (the transfer-funnel contract verbatim) and RE-MINT a fresh wrapper around the same word on the error path, which the kernel's own rule licenses — a refused add consumes nothing. That reads well enough that it is what shipped, and it is arguably stronger (no path can leave two owners of one word), so this is filed for the language rather than as a blocker. Resolution: the move checker should follow the catch block's divergence, exactly as design 228 makes a diverging catch satisfy any expected type — the two halves of "a diverging catch is an exit" should agree. Failing that, a diagnostic that says the move is on a path that cannot reach the use, rather than one that points at a line the fall-through never executes.
- SL-15 — A BARE INTEGER LITERAL DOES NOT ADOPT A `UInt` PARAMETER WHEN THE METHOD IS OVERLOADED, THOUGH THE SAME CALL ON A SINGLE-CANDIDATE FUNCTION ADOPTS: ``error: argument 1 expects `UInt` but got `Int` `` with the three-conversion hint (design 15, `tests/pipe-send-blocking`, sawc 0.3.0 @ `87063387`). Every candidate in the set declares the parameter `UInt` and the candidates differ by ARITY, so there is exactly one that could match and nothing about the literal is ambiguous — but adoption does not run and the call is refused. LANGUAGE_SPEC's rule is that a bare literal adopts a fixed-width or platform EXPECTED type at a parameter, and its documented ambiguity carve-out is about two candidates of DIFFERENT integer types (`h(Int)` vs `h(Int8)`), which is not this. Minimal repro, one file, hosted:
  ```saw
  struct Bag { n: Int }
  extension Bag {
      func put(&self, len: UInt) -> UInt { len }
      func put(&self, len: UInt, extra: Int) -> UInt { len + (extra as UInt) }
  }
  func solo(len: UInt) -> UInt { len }

  func main() {
      let b = Bag(n: 1)
      print("{}", solo(len: 1))     // adopts: one candidate
      print("{}", b.put(len: 1))    // error: argument 1 expects `UInt` but got `Int`
  }
  ```
  It bites wherever a ratified surface is overloaded on presence rather than on type — §2.1's `send(msg)` beside `send(msg, timeout:)` is exactly that shape, and the two differ only in trailing arguments. Workaround in-tree: name the number (`static TIMEOUT_MSG_LEN: UInt = 1`), with the reason written at the declaration; a suffix is not available for platform `UInt`, which is what makes the workaround a static rather than a one-character fix. Resolution: run literal adoption per CANDIDATE during overload resolution and let a set whose members agree on the parameter's type resolve it, keeping the refusal for the genuinely ambiguous case the spec already documents. **IT COST A NAMING IN M4 UNIT 4 (design 18, Sep 2), which is worth recording because the cost is now shipped surface.** Adding a handle-carrying `post(body:len:handles:)` beside `post(body:len:)` broke 38 `len: <literal>` call sites across the suite that had compiled for three units — every one of them a candidate set agreeing on `UInt` and differing by arity, this entry's exact shape. Rather than name 38 numbers, the handle-carrying variants ship as DISTINCT NAMES: `post_with`, `send_with`, `reply_with`, `reply_wait_with`, with the reason written at the definitions. They read honestly enough that this is not urgent, but collapsing them into overloads is a mechanical sweep the day this closes, and until then a ratified surface is being named around a resolution bug.

- SL-16 — THERE IS NO CONSUMING (`self`) METHOD RECEIVER, SO A TRANSFER FUNNEL CANNOT BE A METHOD ON THE THING IT CONSUMES: ``Parse error at 8:20: 'self' must be a reference: use '&self' or '&var self'`` (design 17, `kernel/sysapi/src/pipe.saw`, sawc 0.3.0 @ `87063387`). Saw has `&self` and `&var self` and nothing else — a by-value receiver is refused AT THE PARSER, before any ownership question is asked — so an op whose whole contract is "this call consumes the receiver" has to be spelled either as a `&var self` method that disarms a sentinel (what the tree does, and what `PipeRequest.reply`, `PipeReply.resolve` and every other funnel already do) or as a method on some OTHER receiver taking the value by move (`Waiter.give`, `Process.give`). Neither spelling can be checked at compile time: a caller may call a disarmed wrapper again, and only the kernel's handle generations catch it. Minimal repro, one file, hosted:
  ```saw
  struct Owned { w: Int }
  extension Owned: NoCopy { func deinit(&var self) { } }
  extension Owned {
      func spend(self, n: Int) -> Int {     // Parse error: 'self' must be a reference
          var owned = move self
          owned.w + n
      }
  }
  ```
  LANGUAGE_SPEC is ambivalent about it, which is what made this look like a spelling problem rather than a rule: the Gotchas section says "a bare `self` is likewise rejected", while the concurrency section's capture rules say "A CONSUMING `self` receiver (no `&`) is an owned binding and captures by value as usual" — a sentence about a form the parser does not accept. It bit design 17 because the unit's reviewed §API spells `reply_wait(self, …)`, and the two things the user ruled — the op lives ON THE REQUEST, and the receiver is consumed — are jointly unwritable today; the placement won, and the consume is the sentinel discipline. Workaround in-tree: `&var self` + disarm before the syscall, which is the transfer-funnel contract this tree already documents at `sos.floor`. Resolution: either allow a by-value receiver (which would make single-use a COMPILE error at every funnel in this kernel, rather than a runtime fault the generations diagnose), or strike the concurrency section's sentence and say once, in one place, that a consuming receiver is spelled as a by-value PARAMETER on another type. **FIX IN FLIGHT (user, Sep 1): sawlang grows a `consumes` effect — closes at the FOURTH pin bump.** The ruled syntax: `func f(&var self, ...) consumes { ... }` on the definition (the effect slot, receiver unchanged — exclusivity is the entry requirement, consumption the exit); call sites spell `(move obj).f(...)` (moves stay written — stronger than Rust's invisible-at-the-call consumption). THE SEMANTICS, RULED (user, Sep 1, second round): a `consumes` body SUBSTITUTES for the custom deinit body — the teardown pipeline today is custom-deinit-body -> synthesized member drops, and a consumed path is consumes-body -> synthesized drops FOR THE NON-MOVED MEMBERS; the custom deinit body never runs on that path. MULTIPLE fields may be moved out (partial moves stay banned everywhere outside a consumes body); no restriction on hand-written-deinit types is needed, because the substitution model IS the answer — the custom body is simply not on the consumed path. One note left open upstream: the foreign-module fence (a consumes method on a type with a custom deinit suppresses teardown the author wrote — declaring-module-only, or lean on field privacy). At closure: `reply`/`resolve`/`reply_wait` definitions gain `consumes`, their call sites gain `(move ...)`, THE NO_HANDLE SENTINEL RETIRES AT CONVERTED SITES (the consumes body makes the syscall; the handle word's synthesized drop is a no-op; no release fires), and the compile-time use-after-consume error this entry names becomes real. `Waiter.give` is untouched (by-value PARAMETERS were always legal; the gap was receiver-position only). **CLOSED (fourth pin bump, sawlang 0.4.0 @ `46eebb36`, Sep 2):** design 260 landed `consumes` and the conversion sweep executed. WHAT CONVERTED: `PipeRequest.reply` and `PipeRequest.reply_wait` — the two ops that consume the obligation on EVERY path they can take (`reply` on `Ok` and on `PeerClosed`; `reply_wait` on `Ok` and on both legs of `ReplyWaitError`) — now carry `consumes` in the effect slot beside an unchanged `&var self`, and their 22 call sites across 13 test packages — 20 `reply`, 2 `reply_wait` — spell `(move obligation).reply(...)`. There are no sysapi-internal callers of either: a tree grep found every one of the 22 in `tests/`, so the sweep never touched `kernel/`, `root/` or `rt/`. THE SENTINEL RETIRED AT BOTH: `reply_body` and `reply_wait_body` no longer take the wrapper by reference and no longer write `PipeRequestHandle(NO_HANDLE)` — they take the WORD, which is the shape `post_body`/`call_body`/`take_into` beside them already had, so the file's helper surface is uniform again. The consuming body substitutes for the custom `deinit` body on the consumed path, the handle field's synthesized drop is a no-op, and no release fires; the custom `deinit` STAYS, because an unconsumed obligation still has to release. WHAT THE COMPILE ERROR NOW CATCHES, both witnessed by probe against the pinned sawc: a second use is ``error: use of moved variable `obligation` `` + ``hint: value was already moved at line 76``, and a bare call is ``error: `reply` consumes its receiver — write `(move obligation).reply()` `` + a hint naming the moved-from binding and the `var` revival. `resolve` DID NOT CONVERT, and the argument is this file's own documented departure read forward: a `resolve` that answers `Ok(None)` consumed NOTHING, so it consumes on two paths out of three, and `consumes` consumes on every path — spelling it that way would spend a live claim at the first empty poll, which is the one thing a poll loop must survive. Its contract is consume-on-success-or-terminal-failure and the effect cannot express it, so `PipeReply.resolve` keeps `&var self` and the disarm decided by the ANSWER. Splitting it into a non-consuming poll plus a consuming resolve was considered and DECLINED: it gives one op two typed doors for a distinction the kernel already draws in the status, and §2.1's ratified surface is one `resolve`. NEGATIVE-TEST CONSEQUENCE, one case and not the two the sweep expected: `pipe-no-reply` uses its receiver ONCE (its fault is a RIGHTS refusal, not a double use) and needed only the `(move ...)`; `pipe-dead-claim` double-uses `resolve`, which did not convert, so its proof is untouched and it is now the ONLY place in the suite that can show the ledger's `BadHandle` from Saw. The case that broke is `pipe-reply-wait-dead`, which used `reply` and then `reply_wait` through one binding — a use-after-move now. A minted sibling cannot restore that proof (a sibling's entry SURVIVES the original's consume, so it answers `PeerClosed`, not `BadHandle`) and the raw altitude cannot be reached from a test without either widening the facade's floor re-export or giving a userspace package a `sosabi` dependency for the record layouts, which is the vDSO wall. So the case was RETARGETED to the property its own error enum owes and nothing in the suite exercised: the REPLY LEG firing — a client that abandoned its claim, answered through the fused op, against a Waiter with NOTHING attached, so a park would have been `every thread blocked` and reaching `done` is the proof that no park happened. That is design 17's "one dead client cannot stall a server loop" sentence, executed. Its three transcript rows moved and the case went from a fault case to a clean-exit one; the kernel-side `BadHandle` is untouched and still stands for a raw-altitude caller. GIVE-INTERNALS NOTE (recorded, deliberately not built): the four `Waiter.give` overloads and the other transfer funnels still disarm by hand, because `consumes` is a RECEIVER-position effect and they take their wrapper as a by-value PARAMETER. A private `consumes` dissolve helper on each wrapper (`(move owned).dissolve()` answering the word) would retire those too — SL-3's remainder, filed as a backlog item above.
- SL-17 — `move` OUT OF A **PLACE**-MATCH ARM COMPILES AND DOUBLE-DROPS: no diagnostic at all, and the moved-from payload's `deinit` RUNS A SECOND TIME when the matched-on value dies (design 18, `kernel/sysapi/src/pipe.saw`, sawc 0.4.0 @ `46eebb36`). Matching a `&var` place and writing `move o` in an arm yields a working value AND leaves the enum believing it still owns the payload; for a handle wrapper that is a DOUBLE RELEASE of a live capability, which the kernel's generations turn into a `BadHandle` fault in whichever unrelated code next holds that table slot. Probed with a drop counter, hosted, three files — the move variant drops TWICE, the same arm without the move drops ONCE, and the design-around drops once:
  ```saw
  unsafe static var DROPS: Int = 0
  struct Owned { w: Int }
  extension Owned: NoCopy { func deinit(&var self) unsafe { DROPS = DROPS + 1 } }
  enum Slot { case Empty, case Full(o: Owned) }
  extension Slot: NoCopy {}

  func take_it(s: &var Slot) -> Owned {      // compiles clean
      match s {
          case Empty -> { Owned(w: 0) },
          case Full(o) -> { move o },        // <-- the payload is NOT retired
      }
  }

  func main() unsafe {
      var s = Slot.Full(o: Owned(w: 7))
      var got = take_it(&var s)
      let _ = move got                       // drops=1  (correct so far)
      let _ = move s                         // drops=2  <-- the double drop
  }
  ```
  Control (`case Full(o) -> { o.w }`, no move): drops=1. Design-around (take the whole enum BY VALUE — `func take_it(s: Slot)`, `var owned = move s`, then match the OWNED LOCAL): drops=1, so an owned-local match consumes its payload correctly and only the PLACE form is unsound.
  **A SECOND FACE, FOUND IN THE SAME UNIT'S REWORK (Sep 2) AND NOT A `move` PROBLEM AT ALL: a NESTED place-match double-drops with no `move` written anywhere.** Match a place, bind a NoCopy payload, then make THAT BINDING the scrutinee of a second match and read a plain field of the inner payload — the intermediate binding is materialized and dropped at the arm's end while the outer place still owns it. Probed with the same drop counter: one read-only `peek` costs one spurious drop, two peeks cost two. It bites for real: `reply_len(r: &WaitResult)` written as ``match r.what { case Reply(outcome) -> match outcome { case Data(msg) -> msg.len, … } }`` released the outlet a reply was carrying, and the case that then spent it faulted `bad handle`. What does NOT materialize, probed alongside: BORROWING the arm binding onward — passing it as a `&` argument (`inner_w(&inner)`) or using it as a `&self` receiver (`inner.w()`) — both cost zero drops. So the in-tree discipline is: a place-match arm binding may be borrowed onward, never re-matched; where a nested read is wanted, the inner type publishes a `&self` accessor (`ReplyDelivery.len()`/`.byte(i)`/`.arrived()` exist for exactly that, and say so).
  **RESOLUTION: REFUSAL, RULED (user, Sep 2).** Option (a) — you cannot move a field or payload out of a BORROWED reference, uniformly; the place-match arm was a missing check rather than a missing semantics. The partial-move-tracking alternative was weighed and REJECTED: partial-move-through-a-borrow stays banned everywhere, so marking the payload moved-from and skipping the enclosing drop would carve out a special case the rest of the language does not have. `take()`, `swap_out` and the owned-local match remain the outs. ONE BOUNDARY, because the two rules meet: design 260's `consumes` carve-out is the licensed exception — a `consumes` body moves its own fields out because the effect marks the receiver DYING-OWNED rather than borrowed — so the refusal and the carve-out compose rather than conflict. The nested-place face wants its own answer in the same ruling: an arm binding is a borrow, so re-matching it must borrow too rather than materialize.
  **AND THE DESIGN-AROUND IS RETIRED, BY A SECOND USER RULING THE SAME DAY.** The paragraph that used to sit here said design 18's §API shape — a payload enum owning its wrappers — was unwritable; it was not. What SL-17 forbids is the INVALID construction, and the LICENSED one already exists: `WaitPayload` carries `Reply(outcome: ReplyDelivery)` and `Message(msg: PipeMsg, request: PipeRequest)`, `WaitResult` is `{key, what}` again, and extraction goes through a `consumes` accessor (`WaitResult.open(&var self) consumes -> (UInt, WaitPayload)`, an unconditional multi-field move-out) whose result is matched OWNED — the path this entry's own probe verified sound. The `message`/`request` optional fields are gone. Probed before it was written: the tuple-of-moved-fields return compiles under sawc 0.4.0 and drops each payload exactly once, so the ruled fallback (optionals inside the case, extracted with `Optional.take()`) was not needed and the type invariant is kept rather than traded away.
- SL-18 — THERE IS NO SIZE-OPTIMIZATION LEVEL: `sawc --help` offers exactly one optimization flag, ``-O0  Disable optimization passes (emit raw codegen output for debugging)``, and nothing that ASKS for smaller code — no `-O1/-O2/-Os/-Oz`, no per-build tradeoff of speed for image size (design 20, sawc 0.4.0 @ `46eebb36`). Probe: `sawc --help`, plus `llvm-size` on the riscv32 kernel image — `.text` 360,624 B, `.rodata` 27,004 B, `.data` 14,224 B, `.bss` 152,176 B. It is filed from sawos rather than as a nicety because on an MCU-class target it is the difference between a port and a park: design 20's ESP32-C3 unit parked on a 392.4 KiB loadable image against 400 KiB of on-chip SRAM, and the ONE build-side lever that could have closed a gap that size does not exist. The shape of the `.text`, measured with `llvm-nm --print-size --size-sort`, is a long tail with a heavy shoulder rather than a hot spot: the largest single function is 35,134 B (`end_process`), the next four are 23,294 / 19,764 / 17,400 / 16,642 B, and the top twenty together are 209,470 B — 58% of `.text` — with the remaining 42% spread over hundreds of mid-size functions. So no excision closes the gap and the lever wanted is whole-program codegen policy. Workaround in-tree: NONE — the alternatives are all source-side (sawos's own lazy-decode lever, filed in [BACKLOG], and the ESP32 family's XIP execution model, which moves the tax to flash rather than removing it). Resolution shape: an optimization-level flag that reaches LLVM's `optsize`/`minsize` function attributes and the matching pass pipeline, selectable per build so a freestanding MCU profile can ask for it without changing what the hosted profile does; the useful reporting companion is a per-module `.text` attribution the caller can diff across a change, since "which module grew" is currently only answerable with `llvm-nm --size-sort` on the linked image. **FIX IN FLIGHT (user, Sep 2, relayed): sawlang size work queued after its current run — probe bundle (llvm-size --format=sysv, llvm-nm --size-sort tail-40, --emit-bt-table: 138 B, zero frames) sent upstream; closes at a future pin bump. At closure: re-measure the C3 fit and record whether copy-to-SRAM becomes viable — the XIP ruling stands regardless (it is the family's execution model, not a size workaround).**
- SL-19 — A `u32`-SUFFIXED SHIFT STILL FOLDS IN THE SIGNED PLATFORM DOMAIN, so bit 31 of a `UInt32` cannot be written as a shift on a 32-bit target: ``constant expression -2147483648 does not fit in `UInt32` (range 0..=4294967295)``, anchored at the `<<` (design 20, `hal/riscv32-esp32c3/kernel/lib.saw`, sawc 0.4.0 @ `46eebb36`, target riscv32). `static SYSTIMER_CLK_EN: UInt32 = 1 << 31` is refused, which design 185's documented gotcha predicts and licenses — the fold is in the signed platform-`Int` domain, and on riscv32 `1 << 31` is `Int.min`. WHAT IS NOT PREDICTED is that the EXACT-TYPED spelling behaves identically: `1u32 << 31` is refused with the same message at the same column, even though a suffixed literal is documented as exact-typed and `UInt32` has 0x8000_0000 comfortably in range. So the suffix — the one lever LANGUAGE_SPEC offers for "no expected type reaches this subexpression" — does not reach the constant folder, and there is no spelling of the house style's bit-flag-as-a-shift rule that works at the top bit of a fixed-width unsigned slot on a 32-bit target. Probe: the two `static` lines above, either alone, on riscv32; both compile on a 64-bit host, which is what makes this easy to miss (the fold has 64 bits of room there and `1 << 31` is a comfortable positive). Workaround in-tree: write the literal — `static SYSTIMER_CLK_EN: UInt32 = 0x8000_0000` — with the reason at the line, while every other bit in the same file stays a shift, so the file is inconsistent exactly where the language forces it to be. Resolution shape: fold a suffixed operand in ITS OWN declared domain (the suffix is a type ascription, and `1u32 << 31` has an unambiguous `UInt32` answer), or — the wider fix — fold a const shift in the domain of the SLOT it is landing in, which is what DF-243a already does for a mixed binop's operand and what DF-240a did for the enum-flag combination. Either would let the bit-flag style hold at bit 31 as it does at bit 30.
- SL-20 — A GLOB IMPORT DOES NOT BIND A `type` ALIAS, THOUGH THE SELECTIVE FORM DOES: ``` `Handle` is not defined in `probe` `` with ``hint: available: SIZE, Tag, consume`` `` (design 26, the `sosabi` split, sawc 0.4.0 @ `46eebb36`). A module declaring `public type Handle = UInt` beside a `public static`, a `public enum` and a `public func` publishes three of those four through `import m.*` and silently omits the ALIAS — the hint enumerates the survivors, which is what makes the omission legible once you are already looking at it. It bites hardest through a FACADE, because `public import m.*` drops the alias from the re-exporting module's surface too and the failure then lands in a third file that names neither: splitting `kernel/abi/src/lib.saw` into leaves behind a glob facade turned all fifteen handle aliases into name-only types at the importer — ``argument `handle` expects `UInt` but got `MemoryHandle` `` and ``undefined function `MemoryHandle` `` in `kernel/sysapi/src/memory.saw`, a file that was not edited — so the alias lost BOTH halves of design 144's asymmetry at once: the implicit flow TO `UInt` and the explicit back-construction. Minimal repro, two files plus an entry, hosted, no facade needed (the plain glob fails identically, so this is not SL-7's re-export question):
  ```saw
  // pkg/leaf.saw
  public type Handle = UInt
  public enum Tag: UInt8 { case A = 0, case B = 1 }
  public static SIZE: Int = 8
  public func consume(h: UInt) -> UInt { h }

  // main.saw
  import pkg.leaf.*                        // error: undefined function `Handle`
  import pkg.leaf.{Handle, consume}        // compiles, prints 7
  func main() { print("{}", consume(Handle(7))) }
  ```
  Workaround in-tree: name every re-exported symbol — `kernel/abi/src/lib.saw`'s facade lists all 138 (generated from the leaves' public declarations, so the surface is provably the pre-split one), with the reason written in its header. The selective form carries an alias WHOLE, verified by probe in every position the split needed: construction, annotation, struct field, parameter, return, and the implicit widening to the underlying, two hops from the declaring module. Resolution: make a glob bind every public declaration KIND, aliases included — a `type` is a declaration like any other and nothing in design 150 or 229 says otherwise; failing that, a diagnostic AT THE GLOB naming what it declined to bind, since the current one fires at a use site in a module that may not import the alias's declarer and cannot see that a glob was involved.
