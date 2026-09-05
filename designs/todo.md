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

- M5: the memory milestone — `designs/025`: **COMPLETE, Sep 5 2026.**
  Every rung landed behind an independent lead gate: units 1 (`027`),
  1.5 (`029`), 2 (`033`), 4 (`035`), 5 (`028`), 6 (`032`), 6a (`034`),
  6b (`036`), 7 (`037`), the arena unit (`038`) and unit 8 (`040`,
  the docs sweep that closes it). Unit 3 (riscv32 Sv32) VACATED to
  [BACKLOG] by ruling 11; two riders touched no rung — the `sosabi`
  split (`026`) and the C-leg probe (`039`). The full unit digests
  moved to `designs/done_sep2-sep5.md` at the Sep-5 tracker sweep;
  each design's own As-built is the archival record, and
  `designs/025`'s closing note ("M5 as it ran") is the milestone
  retrospective — the ladder as executed, the gate, and the four
  things the plan got wrong. **Gate at close: 382/382 = 129 + 129 +
  124** across riscv32 / arm64 / riscv32-flat (238 at the milestone's
  start; the SHAPE changed once, at unit 4, when the flat profile
  became a third run). sawlang pinned **0.8.0 @ `449d2485`** with
  **-Oz on all freestanding builds** (user-ruled; kernel `.text`
  76,808 B, −37% at adoption), C3 board smoke 3/3. **M6's anchor is
  `designs/030-m6-storage-seed.md`; its scoping session unlocks
  here.**
- THE C-LEG PROBE (`designs/039`, user-ruled Sep 4): **BUILT AND
  CLOSED.** `tests/c-child/` is a freestanding C image (main.c +
  crt0.c + a hand-rolled syscall.c, no `sos`, no `sosrt`, no arena)
  that `tests/c-hello/` spawns and asserts; case `c_hello` appended,
  gate 380/380 = 128 + 128 + 124. NO kernel, sysapi, ABI or toolchain
  change was needed and NO new runner build leg — a C package reaches
  Blade's sosimg emit through the same `[sos] native` line the HAL
  stubs use. Findings and their M7 sizing are the As-built in
  `designs/039`. **THE FOUR DOC ITEMS IT HANDED UNIT 8 ARE DONE**
  (`designs/040` §4–§5): spec §5.7's vDSO claim is qualified for C,
  both `user/ABI.md`s' "Required of a process" grew from one bullet to
  five, the typed-C altitude row now records that it is unreachable by
  an image not already linking `sos`, and the stale script count is
  corrected in SIX places — two more than the probe had looked at.
- M5 UNIT 8 — the docs sweep: **BUILT AND CLOSED (`designs/040`,
  Sep 5). M5 CLOSES WITH IT.** Gate **382/382 = 129 + 129 + 124**;
  every row that moved is enumerated in that design's §2 (four
  classes, all caused by one deleted runner key: 256 denominator
  changes, 8 of them index shifts, 2 new case rows, 2 new `.sosimg`
  size rows, the totals pair — and NOT ONE existing image size moved,
  on any profile; the flat section is byte-identical). The whole pile
  landed; `designs/040` §4–§6 is the section-by-section record, and
  three things in it are the lead's to see. **`tier_word` runs on all
  three profiles.** **THE `thread_preempt` FLAKE HAD TWO CAUSES, NOT
  ONE** — design 35's prescribed letters-only projection does NOT fix
  the run design 35 recorded, because design 158's ordered matcher
  advances past each whole match and that run's three crossings
  OVERLAP; both halves are per-case keys now (`strip_kernel_lines`,
  `overlapping_matches`), verified against the recorded transcript and
  against the no-preemption control, and CLAUDE.md's timing-rows
  paragraph is corrected. **TWO STALE ABI ASSERTS**, both design 33's,
  both the species design 35 §7 fixed one unit earlier, found by a
  mechanical audit and repaired. Design 37's row-identity follow-on is
  RECORDED in spec §2's `Process` row, not built. The `pool_base`
  CANDIDATE is DECLINED with reasons and re-filed to [BACKLOG] below.
- M6 (after M5): the storage milestone — seed `designs/030` (user-
  ruled Sep 3): flash-first block driver, RO archive fs + a simple
  tmpfs (the write path, same protocol), userspace loader, the
  namespace as the protocol, and a simple UART shell (ls/read/write
  builtins — poke at a running system). Scoping session at M5's
  close.
- M7 (after M6): the POSIX compatibility layer, toybox first — seed
  `designs/031` (user-ruled Sep 3; the posix_spawn hinge). Scoping
  session at M6's close.

## [BACKLOG] — filed, not scheduled

- **`sos_test_pool_base()` MIGRATES ONTO `Mapping.base()`, and
  `map_basics`' header is corrected with it** [#33 findings 5/6,
  RE-FILED BY M5 unit 8 after declining it — `designs/040` §7].
  Eleven test packages read an address out of a per-triple C constant
  whose arm64 body has, since design 33, returned a VIRTUAL address
  under a name that says "pool base" — documented at length in
  `tests/poolbase_arm64.c` and true only by the coincidence that every
  process's first mapping lands at `0x4024_0000`. `Mapping.base()`
  exists, asks the kernel, needs no build-time constant and is right
  on every tier; `tests/map-placed` is the worked example. **DECLINED
  BY UNIT 8 for four reasons, the second decisive**: (1) it is a code
  migration in eleven programs, not a docs or harness edit, and unit
  8's constraint was comments/docs/spec/harness; (2) **IT IS NOT A
  MECHANICAL SUBSTITUTION** — `map-wx-refused` needs the address to
  attempt a map that is REFUSED (so no Mapping exists to ask),
  `child-touch` pokes an address in a CHILD holding no Mapping at all,
  and `memory-recycle` does arithmetic with the pool LENGTH, which
  `Mapping.base()` has no twin for; each wants its own answer and two
  want a new way to learn an address; (3) folding ~24 moving
  image-size rows into unit 8's diff would have made that unit's row
  enumeration unreadable as two causes; (4) **the authorization
  argument is answerable** — the rows it needs are riscv32 and flat
  image-size rows, and ANY unit editing a shared test package moves
  those, so this needs its own brief naming them rather than a rare
  standing authorization. Scope when taken: the eleven packages, the
  two `tests/poolbase_*.c` files (delete `sos_test_pool_base`, keep
  `sos_test_pool_len` — a length is a length on any tier), and
  `map_basics`' header, whose prose still describes a double map as
  two rows over ONE address deciding by "the hardware's own matching
  rule" — under placement they are two rows at two addresses and
  nothing is aliased. The case passes and asserts the right value
  today; only its explanation is of a machine arm64 no longer is.

- **arm64 EL0 FP/SIMD is enabled and the trap frame saves no FP state
  — a LATENT SILENT-CORRUPTION hazard** [#39 As-built §7 K1, filed by
  the lead at integration, Sep 5]. `hal/arm64/kernel/boot.S` sets
  `CPACR_EL1.FPEN = 3` (no trap at EL0 or EL1 — the kernel opened it
  for its own SIMD per design 172's contract) and `TrapFrame` is 34
  doublewords with no `q`/`v` state, so two userspace programs using
  SIMD corrupt each other across a context switch, silently. Nothing
  in-tree trips it today (the probe's arm64 image was disassembled to
  confirm zero SIMD references), but the kernel's own `support.c`
  compiles to 16 SIMD references at -O2 and a libc `memcpy` is the
  same idiom on the same optimizer — the first real C program is the
  likely finder. **Recommended first step (probe's, endorsed): narrow
  FPEN to 0b01 (trap EL0, keep EL1) and FAULT the process — one
  instruction, turns silent corruption into a named fault** — with a
  fault-witness case; price the lazy FP save (3-4 days) when a real
  FP workload asks. riscv32 unaffected (rv32imac_zicsr has no FP
  registers). Schedule: its own small unit, or M6 scoping's first
  housekeeping item — NOT unit 8 (a kernel behavior change is not a
  docs sweep's to take).

- riscv32 Sv32 tier-1 climb — PUNTED from M5 [`designs/025` ruling
  11, user, Sep 3]: 32-bit VA scarcity makes placement a genuinely
  different design (careful fitting vs 64-bit's space-for-tables
  trade); no in-tree hardware target wants Sv32 (C3/P4 are M+U); a
  future riscv MMU target is likelier rv64/Sv39, inheriting the
  64-bit shape. Revisit trigger: a real S-mode riscv target earning
  a HAL. Design 029 carries the Sv32-stays-identity linmap note for
  whoever picks it up.

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
- SL-3 — NO CONSUME-WITHOUT-DEINIT (mem::forget equivalent): a NoCopy value whose deinit must NOT run after its payload's ownership left by other means (a kernel moved the handle word) has no language spelling; sawos uses the NO_HANDLE-sentinel-in-the-field idiom, disarm-before-syscall, recorded as the transfer-funnel contract (design 3 D-5, design 4 funnels). Livable; a `forget`/dissolve construct would retire the sentinel pattern. Low urgency, filed for the record. **PARTLY ANSWERED (fourth pin bump, sawlang 0.4.0 @ `46eebb36`, Sep 2), and the remainder is named.** Design 260's `consumes` IS the dissolve construct for one position: a consuming body substitutes for the custom `deinit` body, so a method that hands the word to the kernel simply does not run the release, and SL-16's closure retired the sentinel at `PipeRequest.reply` and `PipeRequest.reply_wait` on exactly that mechanism. WHAT IT DOES NOT REACH, and why the sentinel is still in the tree: (a) `consumes` is RECEIVER-position only, so every funnel taking its wrapper as a by-value PARAMETER — `Process.give(memory:)`, `give(system:)`, the four `Waiter.give` overloads — still disarms by hand; the in-tree answer is a private `consumes` dissolve helper per wrapper, which is the backlog item above and needs no language change. (b) A funnel whose consumption is CONDITIONAL — `PipeReply.resolve`, which consumes on two paths out of three — cannot use the effect at all, because `consumes` consumes on every path. That third shape was not priced when this entry was filed and is the one that keeps the sentinel a permanent idiom here rather than a migration.
- SL-4 — AMBIGUITY DIAGNOSTIC ANCHORS AT 1:1 WITH `<unknown>` MODULE: ``ambiguous struct `Thread`: defined in both `<unknown>` and `sos.thread` `` reports at line 1:1 of an arbitrary file — the location and the `<unknown>` are both noise (design 5, the std.task Thread<T> prelude collision). Resolution: real span + real module name on the ambiguity path.
- SL-5 — DECLARED-VS-IMPORTED NAME PRECEDENCE ASYMMETRY AGAINST THE PRELUDE: a locally DECLARED type name beats a prelude generic of the same name; a selectively IMPORTED one ties with it and errors (design 5: `struct Thread` won in-file, `import sos.thread.{Thread}` lost to std.task's `Thread<T>` — worked around with the qualifier at five sites). Resolution: rule which precedence is intended and make the two paths agree.
- SL-6 — `extern "C"` DECLARATIONS ARE PRIVATE-BY-CONSTRUCTION AND UNSHAREABLE: an extern decl carries no visibility modifier, and two sibling modules declaring one symbol is a hard ambiguity at the importer (probed, design 5) — which forces a package's entire extern surface into one bottom module. Resolution: visibility on extern blocks, or a ruled one-owner-module convention documented upstream.
- SL-8 — DF-172d STILL BITES (already filed upstream as DF-172d; listed here as a cross-reference, not a new issue): unbracketed binary expressions do not wrap across lines; hit again in designs 2 and 4. Resolution tracked in sawlang's own DF. **THIRD SITE, design 6**: `if (a & X) != 0\n && (b & Y) == 0 {` in `kcore.dispatch`'s `map_access`, fixed by parenthesising the whole condition. **FOURTH AND FIFTH SITES, design 21** (`tests/svc-uart-*`), and the second of them is a shape the earlier sites had not shown: an `if` CONDITION carrying a three-term sum (`traps == answered + 2 * discarded + 1`) is the familiar one, fixed by hoisting the arithmetic into a parenthesised `let`; but a wrapped ASSIGNMENT (`window_discarded =\n    window_discarded + 1`, split only because the arm was deeply indented) fails as `Unexpected token: NEWLINE` at the LINE BELOW, and its diagnostic points into the continuation rather than at the assignment that ran out of room. It bites hardest where indentation is deepest, which is exactly where a line is most likely to need wrapping; the in-tree fix was the compound form (`window_discarded += 1`), which fits.
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
- SL-21 — A STRUCT-TYPED `static` MAY NOT BE A REPEAT LITERAL'S VALUE, THOUGH AN INTEGER-TYPED ONE MAY, AND THOUGH THE SAME STRUCT LITERAL WRITTEN INLINE COMPILES: ``static `C` must be initialized by a compile-time constant`` (design 32, `kernel/core/waitables.saw`, sawc 0.4.0 @ `46eebb36`). The hint that comes with it lists "an earlier module `static`" among the things a constant expression may name, which is what makes this look like a bug rather than a rule — the entry is a `static` and it IS earlier. Minimal repro, one file, hosted:
  ```saw
  struct Slot { a: Int, b: Int }
  static N: Int = 4
  static ZERO_INT: Int = 0
  static ZERO_SLOT: Slot = Slot(a: 0, b: 0)

  unsafe static var A: [Int; N] = [ZERO_INT; N]          // compiles
  unsafe static var B: [Slot; N] = [Slot(a: 0, b: 0); N] // compiles
  unsafe static var C: [Slot; N] = [ZERO_SLOT; N]        // error
  ```
  So the repeat literal's VALUE position accepts a struct literal and an integer static but not a struct static, and the three are equally constant. It bites where a slab's zero element is wanted by name: design 32 converts nine kernel slabs whose initializers each repeat a zero slot, and naming that slot once per kind (`static ZERO_EVENT: EventSlot = ...`) is exactly what the size-in-one-place idiom would suggest. Workaround in-tree: write the struct literal INLINE inside each repeat, which is what the arrays already did before the conversion, so nothing regressed — but the nine initializers now repeat their field lists in the one place a name would have read better. Resolution: let a repeat literal's value name a `static` of any constant-constructible type, exactly as it names an integer one — or, if the restriction is deliberate, say which types the value position admits and make the hint stop advertising "an earlier module `static`" for a case it does not accept.
- SL-22 — A `static` DECLARATION'S INITIALIZER CANNOT WRAP AFTER THE `=`, WHICH FORCES A LONG GENERIC TYPE ONTO ONE OVER-LENGTH LINE: ``Parse error at 289:72: Unexpected token: NEWLINE`` (design 32, `kernel/core/waitables.saw`, sawc 0.4.0 @ `46eebb36`). A newline ends a statement, and neither spelling of the wrap is available, so a declaration whose type must be written TWICE — which is every generic `static`, since constructors do not infer type arguments (design 93/105) — has no legal way to fit a line budget:
  ```saw
  unsafe static var EVENTS: Slab<EventSlot, MAX_EVENTS> =
      Slab<EventSlot, MAX_EVENTS>(...)      // error: Unexpected token: NEWLINE
  unsafe static var EVENTS: Slab<EventSlot, MAX_EVENTS>
      = Slab<EventSlot, MAX_EVENTS>(...)    // same, at the other break
  ```
  Only the argument LIST wraps, because it is inside `(`/`)` (design 129), so the head `NAME: T<...> = T<...>(` must be one line. In-tree that is 100–118 characters on nine declarations against a file that otherwise holds ~80, which is the whole cost — the code is correct and reads fine, it simply cannot be formatted. This is DF-172d's shape (unbracketed expressions do not wrap) at a declaration rather than at a binary operator, and a `type` alias is not the way out: an alias over a struct is a DISTINCT type whose back-conversion takes one argument, not the memberwise initializer. Workaround in-tree: accept the long lines, noted at the block. Resolution: allow a break after `=` in a declaration whose right-hand side is unambiguously incomplete, which is the same judgement the bracket rule already makes — or make constructor type arguments infer from the annotation, which removes the second spelling and the problem with it.

- SL-23 — A `borrows` ACCESSOR'S LENT PLACE HAS NO ADDRESS: `&var slab[i].field` IS REFUSED THOUGH THE SAME FIELD IS ADDRESSABLE INSIDE A `&var self` METHOD: ``can only take reference to a variable, field, or array element`` with ``hint: references require an addressable location`` (design 34, `kernel/core/objects.saw`, sawc 0.5.0). Design 146's whole claim for a place accessor is that it lends the element WHERE IT SITS, and design 130's one sanctioned crossing into the unsafe tier is `(&x) as UnsafePointer<T>` — but the two do not compose, so a kernel that needs the ADDRESS of a slot's field (a message body handed to a byte mover) cannot ask the accessor for it:
  ```saw
  // EXCHANGES: Slab<Exchange, N>, Exchange { body: [UInt8; 128], ... }
  ((&var EXCHANGES[x].body) as UnsafePointer<UInt8>) as UInt   // error
  ```
  The workaround is a method on the ELEMENT type, and it works because design 261 passes every receiver by pointer, so the receiver's own field is an ordinary addressable location:
  ```saw
  extension Exchange {
      public(package) func body_addr(&var self) unsafe -> UInt {
          ((&var self.body) as UnsafePointer<UInt8>) as UInt   // fine
      }
  }
  EXCHANGES[x].body_addr()
  ```
  So the capability exists and only the SPELLING is missing — which is what makes this a wart rather than a wall: one extra declaration per element type that needs it, and the address still comes out of the slot the accessor lent. It bit exactly once in-tree (the pipe body arena becoming a field of a slab slot) and the workaround is arguably the better code, since it names the operation. Resolution: let a `&`/`&var` of a lent place's field take the address the window already refers to, on the ordinary unsafe-tier terms — the window's extent is the expression, which is exactly as long as the existing crossing's.

- SL-24 — A BARE INTEGER LITERAL DOES NOT ADOPT A PLATFORM `UInt` AT A CALL ARGUMENT, THOUGH IT DOES AT A `static` AND AT EVERY FIXED WIDTH: ``argument 2 expects `UInt` but got `Int` `` with the three-conversion hint (design 34, `tests/thread-donate`, `tests/slab-donate-free-nodes`, sawc 0.5.0). Design 257 §1 put the platform pair on the adoption list and the entry in the saw-lang digest names a `static` slot; an ARGUMENT is on the bare-literal list for every fixed width, so the gap is the intersection of the two:
  ```saw
  static N: UInt = 0
  proc.thread_create(stack_top: t, arg: 0)   // error: expects `UInt`, got `Int`
  proc.thread_create(stack_top: t, arg: N)   // fine
  SlabKind.from(raw: 0)                      // same, on a UInt-backed enum
  ```
  Low severity and the workaround is what the house style wants anyway (design 153: name the constant, do not write a magic number), so both in-tree sites became named `static`s with a comment pointing here. It is worth filing because the ASYMMETRY is the surprising part — `arg: 0` at a `UInt32` parameter compiles and at a `UInt` one does not, and nothing at the call site says which width a parameter is. Resolution: extend design 257 §1's slot list to call arguments at the platform pair, which is where every other width already adopts.
- SL-25 — A MODULE-LEVEL `static` DOES NOT CARRY MODULE IDENTITY, THOUGH A FREE FUNCTION DOES, SO TWO MODULES IN ONE COMPILATION UNIT CANNOT BOTH DECLARE ONE: ``ambiguous static `X`: defined in both `core` and `top` `` with ``hint: rename one definition, or import `X` from a single module``, anchored at the ENTRY FILE's first line (design 35, `hal/riscv32-flat/kernel/lib.saw`, sawc 0.5.0). Design 249 ruled that a free function is identified by (defining module, name) and that two modules may declare the same one; design 144 ruled the same for types. A `static` was never swept with them, and it is the one module-level declaration kind that still reserves its name PROGRAM-WIDE. Minimal repro, three files, hosted — note that `h` is declared in both modules and draws no complaint, which is the whole asymmetry:
  ```saw
  // core/lib.saw
  public static X: Bool = true
  public func h() -> Int { 1 }
  public func only_core() -> Int { 7 }

  // top/lib.saw — a PLAIN import, nothing re-exported
  import core.{only_core}
  public static X: Bool = false      // <- the error is reported for this
  public func h() -> Int { 2 }       // <- ...and never for this
  public func board() -> Int { only_core() }

  // entry.saw — names `core` nowhere
  import top
  func main() -> Int { if top.X { top.h() + top.board() } else { 0 } }
  ```
  **THE DIAGNOSTIC IS WHAT MAKES IT EXPENSIVE**, twice over. It is anchored at `entry.saw:1:1` — a file that imports one module and mentions neither the name nor the other module — so it points at the importer rather than at either declaration, and the hint's second half ("import `X` from a single module") describes a fix the entry file cannot make, since it is not importing `X` from anywhere. And the ambiguity is reported whether or not anything ever READS the name, so a module can be broken by a static it does not use, added to a module it does not import.
  **WHERE IT BIT, and it is the interesting half:** a BUILD PROFILE is exactly a module that re-implements part of another module's seam. SOS's flat profile (design 35) re-exports the riscv32 virt board's device half and answers differently about protection, and `PROT_REPLAY_AT_SWITCH` — a `public static Bool` in the shared arch module since design 27 — could therefore not be overridden by it AT ALL. `prot_reset` and its six siblings ARE declared in both modules and coexist, which is what made the failure read as a spelling problem for an hour. **In-tree resolution: the two seam values a profile must override became FUNCTIONS** (`prot_replay_at_switch()`, `prot_isolated()`), which is livable — a leaf returning a literal still inlines and folds, so `load_domain`'s replay loop still compiles away where it is dead — and which yields a rule worth stating on its own: **a HAL constant that a build profile may need to override cannot be a `static`.** The other HAL constants (`PROT_GRAIN`, `GRANT_ROW_BUDGET`, `FRAME_BYTES`, the memory map) stay `static`s because nothing overrides them; they are declared once and re-exported, which is the shape that works — and which is also why this went unnoticed for eight designs' worth of HAL work.
  What it does NOT reach: a `static_assert` operand, an array length, a const generic argument. Those want a compile-time constant, and a function is not one — so a profile that had to override a SIZE rather than a flag would have no spelling at all today, which is the sharper version of this entry and is filed here rather than met.
  Resolution shape: give a module-level `static` the identity a free function has (design 249's rule at the fourth declaration kind), so `top.X` and `core.X` are two statics and each importer resolves its own. Failing that, the diagnostic should anchor on the two DECLARATIONS and say which modules they are in, rather than on an entry file that names neither.
- SL-26 — SAW CANNOT ASK FOR ALIGNMENT ON A BYTE BUFFER: a `[UInt8; N]` local has an alignment of ONE, no alignment attribute or aligned-array spelling exists, and the alignment a stack local actually gets is the optimizer's business — so an API whose ABI needs word-aligned bytes (`&[UInt8; N]` into a kernel copy door) cannot state that requirement anywhere in the type system, and every caller compiles into a latent fault (sawc 0.8.0 @ `449d2485`, found at the seventh pin bump, Sep 5). WHERE IT BIT: the whole pipe sysapi surface takes `body: &[UInt8; PIPE_BODY_BYTES]` and the kernel's copy-in door faults an unaligned source; every one of ~20 test programs' `var body: [UInt8; 128]` locals happened to land word-aligned at -O1 and below, and `-Oz`'s frame packing landed TWO of them odd on riscv32 (`pipe_donate`, both riscv32 profiles; arm64 by luck) — the first optimization-level change ever taken, and the gate caught it same-day. Neither a bug in -Oz nor in the kernel: the contract was unstatable, so it was never stated. IN-TREE RESOLUTION (the vDSO discipline): `kernel/sysapi/src/pipe.saw` grew `body_addr_for_kernel` — the four send-side funnels stage the body through a word-typed `[UInt; PIPE_STAGE_WORDS]` frame-local (word-aligned BY TYPE) when and only when the caller's storage is unaligned; aligned callers pay one branch, unaligned ones a 128-byte copy. The receive side was always safe (bodies ride inside word-aligned structs). Resolution shape upstream: an alignment request the type system carries — an `@align(N)` on locals/statics, or an aligned-array type — so a byte buffer with an ABI can say so; the staging copy then becomes deletable. Probe: `tests/pipe-donate` at `-Oz` on riscv32, before the sysapi fix.
- SL-27 — `sizeof`/`alignof` DO NOT FOLD IN A STATIC INITIALIZER, AND THE REFUSAL'S OWN HINT SAYS THEY DO: `static C: Int = sizeof<UInt64>()` is ``error: static `C` must be initialized by a compile-time constant`` whose hint reads "a static initializer is a CONSTANT EXPRESSION ...: literals, arithmetic and bitwise over them, `sizeof`/`alignof`, the integer limits, ..." (sawc 0.8.0 @ `449d2485`, hosted and freestanding alike, probed Sep 5 at the seventh pin bump). Minimal repro is that one line; mixing them into arithmetic (`16 / sizeof<UInt>()`) refuses identically. `sizeof` in a `static_assert`, an array length or a function body folds fine — the gap is the static-initializer position only. WHERE IT BIT: the SL-26 staging buffer wanted `static PIPE_STAGE_WORDS: Int = PIPE_BODY_BYTES / sizeof<UInt>()` (the natural derive-don't-restate spelling of design 186); the workaround in-tree is a literal count over `UInt64` plus a `static_assert` pinning it to `PIPE_BODY_BYTES`. Resolution shape: either fold the two builtins in that position (they are the hint's own promise, and design 186 tier 2 names them) or correct the hint — a hint that lists the refused spelling as allowed costs a round-trip per encounter.
