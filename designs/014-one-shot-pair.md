# SawOS design 14 — the one-shot pair: PipeReplyHandle / PipeRequestHandle (M4 unit 2)

Status: **LANDED Sep 1 2026** (was DRAFT Sep 1, lead). Ladder unit 2 of the ruled plan of
record (designs/010): **the request/reply obligation becomes a pair of
counted, transferable, single-use objects, and `Post` grows its
reply-claim return.** No waitability and no parking (unit 3's — the
proofs POLL, ruling 4's ladder consequence), no handles in messages
(unit 4's), no fused paths (unit 3/3.5's). **Plus ruling 10's rider**:
the teardown force-free arms for counted kinds are deleted and D-4's
orphan write-off activates — references govern lifetime.

A NOTE ON RULING 9(a) (consume-detaches-implicitly): the session
handoff carried it with this unit's queue line. It is VACUOUS here —
no pipe kind is attachable before unit 3 (design 13's `NotWaitable`
arms), so an op that consumes a one-shot has no attachment it could
detach — and its mechanical landing stays in unit 3 per 010 ruling 9,
where `resolve`-under-attachment first becomes writable. Recorded so
the pull-forward is seen to be considered, not missed.

## The ruled surface (all from designs/010 and ratified §2.1)

- **The pair, as objects** [§2.1 ratified; ruling 2's naming
  precedent]: `PipeReplyHandle` is the CLIENT's claim on a reply;
  `PipeRequestHandle` is the SERVER's obligation to send one. Both
  SINGLE-USE (consumed by the op that discharges them) and
  TRANSFERABLE (the delegation primitive). Two new `ObjType` cases,
  two rights enums named for ops: `PipeReplyRight { Transfer, Mint,
  Resolve = 1<<8 }`, `PipeRequestRight { Transfer, Mint,
  Reply = 1<<8 }`. Wait rights arrive with unit 3.
- **`Post` grows the claim return** [ruling 4: post stages, returns
  the claim, never suspends]. Unit 1's `Result<Bool, _>` becomes the
  two-channel `Result<PipeReplyHandle?, _>`: `Ok(Some(claim))`
  staged / `Ok(None)` ring full / `Err(...)` — §5.7's
  renumberable-ops discipline makes the growth free, and design 13
  D-2 said so at the op.
- **`Take` grows the request return**: the oldest staged message AND
  the `PipeRequestHandle` that obliges its reply, minted into the
  taker's table at the take.
- **`Reply`** (`PipeRequestRight.Reply`): copy the reply body in
  (len ≤ `PIPE_BODY_BYTES`, 0 legal), CONSUME the request. To an
  abandoned request: `Err(PeerClosed)`, obligation discharged either
  way [§2.1 ratified].
- **`Resolve`** (`PipeReplyRight.Resolve`): nonblocking — the reply
  bytes if present (claim CONSUMED), `WouldBlock` while pending,
  `PeerClosed` if the server side abandoned. The blocking
  `send(msg)`/`send(msg, timeout:)` library compositions stay unit
  3's, when there is something to park on.
  > **RE-RULED Sep 2 by `designs/010` ruling 12(b), landed as sawos
  > design 22 (M4 unit 4.5): `WouldBlock` LEFT THIS OP ENTIRELY.** A
  > pending exchange PARKS the calling thread on the claim's ready
  > level, so every path `Resolve` can return through consumes the
  > caller's entry — `Ok(msg)` or `PeerClosed` — which is what lets
  > `PipeReply.resolve` carry `consumes` and makes a second resolve a
  > compile error rather than the `BadHandle` this unit's
  > `pipe-dead-claim` used to prove. The level question moved to the new
  > `PipeReplyOp.Ready`, gated on `PipeReplyRight.Wait`. Everything else
  > this section says still holds, including the `PeerClosed` arm and its
  > consume.
- **Abandonment is information, not an imperative** [§2.1 ratified]:
  server drops its request → the claim answers `PeerClosed`; client
  drops its claim → a cancellation signal the server MAY honor or
  ignore (in this unit, visible only as `reply()` answering
  `Err(PeerClosed)`; the request-abandoned waitable level is unit
  3's).
- **Ruling 10's rider, taken whole**: teardown force-free arms for
  counted kinds are DELETED; a dying process's teardown drops its own
  references (close-all, unchanged) and WRITES OFF its quota charges
  (D-4's orphan vocabulary); an orphaned object is charged to NOBODY
  until it frees; the reasoning is recorded AT THE SWEEPS. The
  transcript proof stays deferred [010 ruling 10] — unreachable with
  MAX_PROCESSES = 2 and root dying last — so acceptance here is the
  suite green with no reachable behavior change, plus the arms gone.

## D-1: the claim IS ring-slot state — no third slab

The lean (agent may argue with the doctrine in hand): a one-shot pair
exists exactly as long as one in-flight message does, and ruling 4's
rider already pins the reply body to the ring slot ("ruling 6's fixed
slots carry the reply"). So the claim state lives WITH THE SLOT:
parallel per-slot arrays beside `PIPE_LENS` (design 13's own layout
move) carrying `reply_refs`, `request_refs`, and a small claim state
(pending / replied / abandoned-by-client / abandoned-by-server), all
zero-initialized (.bss, no image bytes). The two new `ObjType` kinds
target (pipe slot, ring slot); which column a handle counts on is its
kind, dispatch's §3 order deciding — design 13 D-1's shape, one level
down. No `MAX_ONESHOTS`, no new quota kind: the in-flight budget
[ruling 6] IS the bound, and the pair's creator-pays row already
covers the connection [ruling 6]. A claim mints no quota row — which
is what makes ruling 4's "zero one-shot quota rows on the fast path"
a difference of HANDLE traffic only when `Call` lands.

**THE RING SLOT NOW LIVES UNTIL THE EXCHANGE SETTLES, not until
`Take`.** Unit 1 freed a slot when its bytes were taken; unit 2's
slot carries the reply back, so it recycles when the claim resolves
(or when both one-shot columns fall with nothing owed). Ring-full
therefore counts awaiting-reply exchanges against `PIPE_INFLIGHT` —
ruling 6's "in-flight messages per client" reading, now literal. Say
this at `pipe_take`, and the ring-depth proof must SHOW it (below).

## D-2: drop semantics, mapped to the columns

Monotone columns are the abandonment state, exactly as design 13's
two connection columns are the peer-gone state — no flag beside them:

- `request_refs → 0` with no reply written: abandoned-by-server. A
  later `Resolve` answers `PeerClosed` and consumes the claim (the
  slot recycles then — nothing un-closes, terminal per design 8).
- `reply_refs → 0` with the reply unclaimed: abandoned-by-client. A
  later `Reply` answers `Err(PeerClosed)`, obligation discharged, and
  the slot recycles at that discharge (or immediately, if the reply
  was already written — nobody can ever claim it).
- Both columns zero → the slot settles and recycles, whatever state
  it was in.
- The CONNECTION zero-arms compose, one addition: design 13's
  `pipe_drop_staged` at outlet-death settles the staged, untaken
  messages' claims as abandoned-by-server (their requests will never
  be minted). Inlet-death changes NOTHING here: claims are counted
  objects independent of the inlet — a client may drop its inlet and
  still resolve.
- Consumed-on-reply / consumed-on-resolve is a release of the
  caller's handle entry THROUGH THE OP (the transfer-funnel contract,
  design 3 D-5): the wrapper disarms its sentinel, the kernel drops
  the entry's reference, the column falls. `Mint` composes: siblings
  of a consumed handle keep the object alive; the op consumed an
  ENTRY, the columns decide the OBJECT — single-use is a property of
  the claim state (one reply may be written, one resolve may
  succeed), not a per-handle bit.

## D-3: teardown, the rider executed

- `end_process`'s by-charged-process force-free arms for the counted
  kinds are DELETED (the `PIPES` sweep design 13 finding 2 named, and
  the Memory/IoMemory siblings with it). What remains: close-all
  drops the dying process's entries (columns fall, zero-arms and
  claim settlements fire — the machinery above, no special case),
  and the quota charges the dying process still holds are WRITTEN
  OFF (D-4's vocabulary, landed as its degenerate case: the orphan
  is charged to nobody; the wall stays because quota ≤ wall).
- Record at each deleted sweep WHY it is gone (ruling 10's text:
  references govern lifetime; a slot outlives its creator exactly as
  long as anyone holds a reference).
- No new arm for a process dying parked in a call — nothing parks in
  this unit; the orphaned-claim arm is unit 3's, with `Call`.

## The proof (harness)

1. **`pipe-oneshot`** (root-only): full round trip — post answers a
   claim, take answers the message AND the request, reply, resolve
   answers the reply bytes (transcript proves the payload both ways);
   zero-length reply; FIFO of claims across several in-flight
   exchanges; resolve-while-pending answers the typed would-block
   (`Ok(None)` at the typed tier); ring depth COUNTED under the new
   lifecycle — post-until-full, then TAKE ALL and show the ring is
   STILL full (slots held by unresolved claims), then resolve one and
   post succeeds — the slot-lives-until-settled claim as transcript.
2. **`pipe-abandon`**: client drops the claim → server's reply
   answers `PeerClosed`, obligation discharged (a second reply is the
   consumed-handle fault); server drops the request → client's
   resolve answers `PeerClosed`; nothing un-closes; outlet-death
   settles staged claims (post, drop outlet, resolve → `PeerClosed`).
3. **`pipe-delegate`**: root posts (claim held), takes (request
   held), `give`s the REQUEST to a child (boot drain); the child
   replies; root resolves and prints the child's bytes — the
   obligation discharged across processes, transferability proven,
   the seed of unit 4's in-message delegation.
4. Negative arms where cheap: reply through a mint lacking `Reply`
   (refused); oversized reply len (`BadArg`); double-resolve (the
   second is a dead-handle fault — consumed is consumed).

Transcript expectation: existing rows byte-identical or address-only;
new rows only; `pipe-basics`' ring-depth rows are AUTHORIZED to move
if the slot-lifecycle change reaches them (the case takes everything
it posts, so the count should hold — verify, and argue any move).
Poll loops bounded, the existing harness pattern.

## Docs owed

spec §2.1 built-markers advance (send→claim, receive→request, reply,
resolve, abandonment: BUILT; blocking send: unit 3), the counted-kinds
table (+2 rows), §11 refresh (the deleted sweeps, the write-off),
quota table note (claims ride the connection's row); tracker entry
closed in place; As-built here; SL-N only for genuine deficiencies.
CLAUDE.md's idiom is FULLY in force — try/catch at every position,
statement and fold both (the tree converted Sep 1).

## Out of scope

Waitability and every level (reply-ready, request-abandoned, inlet
room — unit 3); ruling 9 both halves (unit 3 — 9(a) vacuous here, see
the note); `Call`/`ReplyRecv` (unit 3/3.5); handles in messages and
the keep-mask rider (unit 4/2.5); the money shot (unit 5); kill;
namespaces; the growable-pool M5 seed.

## As built

**LANDED Sep 1 2026.** Suite 182/182 (91 cases/arch, riscv32 + arm64)
from 170/170; SIX new cases across seven new packages, and no
pre-existing case row moved.

**VALIDATED ON sawlang 0.3.0** (`SAWLANG_ROOT` at `87063387`, the tree's
`sawlang.pin` version). The baseline and the gate were compiled by the SAME
compiler, so the transcript diff below is attributable to this change alone.
Nothing here leans on anything 0.3.0 added.

### Transcript accounting

Whole-transcript diff, base `cce59da` vs the gate, ANSI stripped and bucketed.
The parse is mechanical (case rows `[n/m] MARK name`, image rows
`path (N bytes)`, everything else):

| bucket | rows | what |
|---|---|---|
| byte-identical | 170 case-row MARKS + names | every pre-existing case, same mark, same name, and the same INDEX within its arch. `thread_preempt` and `timer_interval` — the two known timing rows — included, so the documented-nondeterministic bucket is EMPTY this run |
| authorized-with-cause | 170 case-row denominators | `[n/85]` -> `[n/91]`, the DENOMINATOR alone, because six cases were appended. Zero indices moved, which is what says they were appended rather than inserted |
| authorized-with-cause | 73 riscv32 image-size rows | every `.sosimg` grew, by +64 to +1168 bytes (27 of them by exactly 64). The `sos` facade compiles two more wrapper types and four more C seams into every image, and `sosabi` carries two more kinds, two more rights enums and two more op tables |
| authorized-with-cause | 5 arm64 image-size rows | +4096 each, and 68 arm64 images did not move AT ALL — page granularity on that profile, so the same code growth crosses a page boundary in five images and is invisible in the rest |
| authorized-with-cause | 1 summary row | `170 passed` -> `182 passed` |
| address-only | 0 | nothing printed an address that moved |
| documented-nondeterministic | 0 | none met |
| new | 12 case rows + 14 image rows | the six cases and seven packages, on both profiles |

No row is unaccounted for, and no image SHRANK on either profile.

**`pipe-basics`' RING-DEPTH ROWS DID NOT MOVE, and the brief was right to make
us check.** The lifecycle change reaches them — a ring slot now lives until its
exchange SETTLES — but that case posts and takes with §2.1's TELL idiom at every
step (the claim is given up at the post, the obligation dropped at the take), so
both columns of every exchange fall before the next line runs and each slot
recycles exactly where unit 1's did. `ring depth=16` and `drained=16` therefore
mean what they always meant, byte for byte. What would have moved them is a case
that HOLDS an exchange, and `pipe-oneshot` is that case, written new: it counts
the ring around one unsettled exchange (15) and again after settling it (16),
which is the same claim stated as a difference rather than as an absolute.

### The exchange, as landed

`kcore.objects` gains four parallel per-ring-slot arrays beside `PIPE_LENS`, all
zero-initialized and therefore `.bss` at no image cost (design 149):

```saw
PIPE_EX_STATE   [ExchangeState; MAX_PIPES * PIPE_INFLIGHT]   Free/Staged/Taken/Replied
PIPE_REPLY_REFS [UInt16; ...]                                the CLAIM column
PIPE_REQUEST_REFS [UInt16; ...]                              the OBLIGATION column
PIPE_NEXT       [Int; ...]                                   the staged FIFO's links
```

and `PipeSlot` LOSES `head` and `count`, gaining `staged` (a 1-based list head).
Four things about that are worth reading:

- **THE RING STOPPED BEING A QUEUE OF BYTES AND BECAME A TABLE OF EXCHANGES.**
  Unit 1's slots were contiguous because a slot lived exactly from its post to
  its take, so a head plus a count described them. Unit 2's settle in whatever
  order their two holders act — a server may answer the third request first, a
  client may resolve out of order — so occupancy is PER SLOT and the free ones
  are not contiguous. `alloc_exchange` scans (bounded by `PIPE_INFLIGHT` = 16,
  which `kcore.limits`' audit note already covers), and the staged messages are
  a LIST threaded through `PIPE_NEXT`. The list is a strict FIFO by
  construction: appended at the tail, removed at the head, or cleared whole at
  the outlet's zero-arm — never removed from the middle — which is why it needs
  no removal path and no tail index (design 13's own argument against a second
  index to keep in step, applied again).
- **THE TWO COLUMNS ARE THE ABANDONMENT STATE, exactly as design 13's two are
  the peer-gone state.** Abandoned-by-client IS `reply_refs == 0`;
  abandoned-by-server IS `Taken` with `request_refs == 0`, or `Staged` with the
  CONNECTION's `outlet_refs == 0`. No flag anywhere says it. The one thing the
  columns cannot say on their own is why a request column is zero — not minted
  yet, or minted and dropped — and that is the whole of why `ExchangeState`
  exists as a recorded state rather than being derived too.
- **THE SETTLE RULE IS THE COUNTED-OBJECT RULE PLUS ONE CLAUSE.** A slot goes
  back when both columns are zero AND nothing is owed, where "owed" is a
  `Staged` message with a live outlet — §2.1's TELL idiom, which requires that
  post-then-drop-the-claim still delivers. Without the clause the TELL idiom
  would recycle the slot under the message.
- **A CONNECTION CANNOT FREE WHILE AN EXCHANGE LIVES IN IT** (`pipe_idle`, a
  third clause on `drop_reference`'s both-zero test). A claim is a reference on
  a RING SLOT and the ring slots live inside the `PIPES` row, so freeing the row
  under a live claim would hand it a recycled connection. This is ruling 10's
  "references govern lifetime" one level down, and it is the one place the two
  levels of the design are not independent.

Numbers: `MAX_PIPES = 4` × `PIPE_INFLIGHT = 16` = 64 exchanges machine-wide,
costing 64 × (1 + 2 + 2 + 4) bytes of `.bss` at the two profiles' widths.

### What the compiler enumerated

Adding two `ObjType` cases broke, and would not build until answered:

1. `kcore.refs.ref_object` — 2 arms (which column a bind counts on).
2. `kcore.refs.drop_reference` — 2 arms. **This is where the design got decided
   a second time**, exactly as it was in unit 1: the matrix demanded an answer
   to "was that the last" for an object with two names, and writing
   `exchange_can_settle` is what turned D-2's drop semantics from four bullet
   points into one predicate the ledger evaluates.
3. `kcore.refs.free_object` — 2 arms.
4. `kcore.dispatch.dispatch` — 2 arms (the kind -> op-table match).
5. `kcore.dispatch.waitable_slot` — 2 arms. The design-8 precedent collected a
   FOURTH time; the `NotWaitable` refusals are written out with the note that
   unit 3 removes them beside unit 1's two.
6. `sos.system.decode_boot_handle` — 2 arms from the two new `BootHandleKind`
   cases, and `BootHandle`'s literal grew two fields at all EIGHT arms, which is
   the compiler asking the same question eight more times.
7. `kcore.dispatch.boot_kind_of` — reached through its `_` arm, so the compiler
   did NOT ask; the two arms were added because `pipe_reply_rights()` and
   `pipe_request_rights()` mint `Transfer` and a givable kind with no record
   spelling would arrive mis-tagged. Unit 1 recorded the same gap and it is
   still the one place in this blast radius where the enumeration property is
   off, deliberately.
8. The new `ExchangeState` enum enumerated ITSELF at five sites —
   `alloc_exchange`, `pipe_idle`, `exchange_can_settle`, `pipe_resolve` and
   `pipe_reply` — which is what made the two op bodies read as answer TABLES
   rather than as chains of `if`s, and what forced the two `fatal_kernel` arms
   for the states a handle cannot name.

`SosStatus` needed NOTHING: `PeerClosed` and `WouldBlock` both existed and both
say here exactly what their unit-1 docstrings say they say. `QuotaKind` needed
nothing either, which is D-1's "a claim mints no quota row" holding.

**THE ENUMERATION PROPERTY WAS VERIFIED RATHER THAN ASSUMED** (the design-8
tradition, run as a probe): deleting `ref_object`'s `case PipeReply` and
compiling the kernel alone gives ``error: match is not exhaustive, missing
variants: `PipeReply` `` anchored at the `match`, and the arm was restored.

### Deviations from the brief, argued

1. **`Take` ANSWERS THROUGH A RECORD, and `PIPE_BODY_BYTES` IS CHECKED AGAINST
   THE CALLER'S MEMORY RATHER THAN AGAINST A CAP ARGUMENT.** The brief says the
   take grows the request return and does not say how. A take now answers TWO
   things (the length and the obligation) and one op answers one word, so the
   two ride the copy-out record `ProcessOp.PipeCreate` already opened —
   `PIPE_TAKE_RECORD_WORDS = 2` in `sosabi`, `{len, request}`. That spends the
   third argument register on the record's capacity, which left none for the
   BODY buffer's; so the body is checked with `copy_out_check(p, body,
   PIPE_BODY_BYTES)` instead. That is STRICTER than what it replaced, not
   looser: unit 1 faulted a caller whose CAP NUMBER was under the published
   minimum, and this faults a caller whose actual memory is not writable for a
   whole slot — the number a caller types is no longer the thing trusted. The
   fault tag changes from `BadArg` to `BadBuffer` on that path, which no
   shipped transcript asserts. The alternative was a `sos_syscall4` (the floor's
   own sanctioned move for a wider op), and it was not taken: it is a HAL change
   on both profiles for one argument, where the record was already a door.
2. **THE TYPED `resolve` DISARMS BY THE ANSWER, NOT BEFORE THE SYSCALL.** The
   transfer-funnel contract (design 3 D-5) says disarm BEFORE, because every
   failure of such a syscall is a fault. A resolve has a third outcome the
   funnels do not — `Ok(None)`, which consumes nothing — so disarming first
   would throw away a live claim on every poll. The rule is exact and it is the
   kernel's: the entry is consumed unless the answer is a would-block. `reply`
   keeps the contract verbatim (it consumes on every answer it can give), so the
   two sit side by side in one file with the difference written at the site.
3. **THE WRAPPERS ARE `PipeReply` / `PipeRequest`, NOT `PipeReplyHandle` /
   `PipeRequestHandle`.** The tree renders a §2 object name short at the typed
   tier (`MemoryObject` -> `Memory`, `PipeInlet` beside `PipeInletHandle`), so
   §2.1's ratified names are the `sosabi` ALIASES and the owning wrappers are the
   short ones. That takes the name §2.1's client-API paragraph uses for the
   RESOLVED reply VALUE — a type that does not exist yet and whose only producer
   is unit 3's suspending `send`. Recorded in §2.1's built-so-far block as a
   notice to that unit rather than resolved here, because naming a type nothing
   builds is how dead surface starts.
4. **`Mint` IS IN BOTH DEFAULT SETS**, which the brief's rights enums imply and
   its D-2 paragraph reasons about. It costs one answer the brief did not have to
   give: a SIBLING that acts second. A second `reply` (the exchange is already
   `Replied`) and a second `resolve` (the reply is already taken) are both
   `PeerClosed` — the same word an abandoned peer gets, and for the same reason,
   which is that there is no longer anybody on the other side of THIS exchange.
   `AccessDenied`-style faults were rejected: two holders of sibling handles
   racing is precisely what a caller could not have checked.
   > **REVERSED Sep 2 by `designs/010` ruling 12(c), landed as sawos design
   > 22 (M4 unit 4.5): `Mint` LEAVES BOTH DEFAULT SETS.** The answer this
   > finding gives is exactly the reason — a sibling on a one-shot can only
   > ever be told `PeerClosed`, so it is a name that buys nothing and
   > undermines the compile-time single use ruling 12(b) then made
   > structural. The enum BITS stay (universal bit 1 is pinned by assert in
   > every kind's enum); the default sets simply do not hand them out, and
   > attenuation being monotonic makes the absence permanent.
5. **NO `pipe-not-waitable` CASE, again.** Unit 1's deviation 2 holds word for
   word for the one-shots: the typed `Waiter.add` has no overload for either kind
   and the wrappers' handle fields are `public(package)`, so the refusal is a
   COMPILE error, which is strictly stronger than a runtime fault. `NotWaitable`
   now has NINE arms and no test in the tree exercises any of them.
6. **A `reap_unreferenced` STANDS WHERE THE THREE DELETED SWEEPS WERE.** Ruling
   10 advertises deleting code; deleting those three loops outright would have
   LEAKED, and finding 1 below carries the argument. What went is the
   by-charged-process force-free — the hazard the ruling names — and what stands
   is a reap of what nobody names, which is the ruling's own sentence made
   mechanical.
7. **THE CONSUMED-HANDLE FAULT IS PROVED ON THE RESOLVE SIDE, NOT THE REPLY
   SIDE.** The brief names it twice — "a second reply is the consumed-handle
   fault" in proof 2 and "double-resolve" in proof 4 — and they are ONE
   mechanism: an op that consumes destroys the caller's entry, so the second use
   of the word is `BadHandle`. `pipe-dead-claim` is that case, and it takes the
   resolve side because a fault ends the process and one case can only meet one.
   Reaching it at all takes deliberate effort, which is itself the finding: the
   typed wrappers disarm on consume, so an ordinary program never meets either
   fault and the case has to ask a SECOND time through a spent value to show that
   the kernel's check is real underneath the wrapper's.
   > **AND IT IS OUT OF SAW'S REACH SINCE M4 UNIT 4.5** (`designs/010` ruling
   > 12(b); sawos design 22). `resolve` carries `consumes` now, so the second
   > ask is a COMPILE error and the suite's last Saw-visible ledger
   > `BadHandle` retires with it — the same thing SL-16 did to the reply side
   > at unit 3.5, which is what retargeted `pipe-reply-wait-dead`.
   > `pipe-dead-claim` retargets to the ruled flow (obligation dropped,
   > `ready()` answers the terminal, claim DROPPED unresolved) plus a mint
   > probe. The kernel's check is unchanged and still stands for a
   > raw-altitude caller holding a word.

### Findings

1. **THE CLOSE-ALL'S DESIGN-7 D-5 ASYMMETRY IS WHY RULING 10 CANNOT BE A PURE
   DELETION.** The teardown's close-all counts WITHOUT freeing
   (`unref_teardown`), and it does that deliberately: the per-slab sweeps that DO
   free are also what COUNT for the teardown report, so a close-all that freed by
   reference would collapse every `events=`/`waiters=`/`timers=`/`interrupts=`
   number in the suite. Delete the three silent sweeps and a slot whose LAST
   handle the dying process happened to hold reaches zero references with nothing
   left to free it — a `Live` slab row with `refs == 0` that no later op can
   ever reach. That is a leak, not a write-off, and it is reachable today (a
   child that splits a Memory and exits). So the sweeps' CONDITION changed from
   "charged to this process" to "named by nobody", and the free goes through
   `free_object` so each kind's quota CREDIT runs — which the old sweeps never
   did for a slot another process had been charged for. Recorded here because
   the next unit that revisits ruling 10 should know the deletion was not free.
2. **MAPPING IS A COUNTED KIND WHOSE SWEEP IS NOT ONE OF THE THREE.** It looks
   like them and is not: `mapping_rights()` withholds `Transfer`, so a Mapping
   handle cannot leave the process that owns it and a sibling cannot outlive its
   owner — the hazard ruling 10 names is structurally unreachable — and the
   loop's second arm clears a ROW for a dead target rather than freeing a slot.
   Left alone, with the reason written at the site.
3. **ROOT'S 16 KiB STACK GRANT IS A REAL CEILING ON A TEST'S `_start`, AND THE
   64-BIT PROFILE MEETS IT FIRST.** `pipe-oneshot` was first written as one long
   `_start` and died on arm64 with `store-access-fault … tval=0x4023bd20` — four
   pages below the stack grant's base — while passing on riscv32. The cause is
   design 137's own guarantee: every `print` with format arguments assembles its
   message in STACK SCRATCH so that a panic survives an exhausted allocator, and
   a function with forty-seven such call sites reserves that scratch for all of
   them in one frame. The fix is structural and reads better anyway — one phase
   per function, each reporting by RETURNING a status, `_start` printing once —
   and it is worth knowing before unit 3's cases, which will be longer. The
   kernel is not implicated: the fault is a userspace stack overflow, correctly
   diagnosed and correctly fatal.
4. **A `&var [T; N]` PARAMETER'S ELEMENTS ARE NOT ASSIGNABLE** — filed as SL-13.
   Probed minimally: `func bump(a: &var [UInt8; 4]) { a[0] = 9 }` is ``cannot
   assign to element of immutable array `a` ``, while the same element reached
   through a `&var STRUCT` (`b.xs[0] = 9`) compiles and runs. It shaped two test
   helpers (a message buffer is filled by the caller and passed `&`), and nothing
   in the kernel wanted it.
5. **THE HANDLE TABLE, NOT THE RING, IS WHAT BOUNDS A CLIENT THAT HOLDS ITS
   CLAIMS.** `MAX_HANDLES` is 16 and `PIPE_INFLIGHT` is 16, so a process that
   posts without resolving meets its own table before it meets the ring: sixteen
   claims plus a System, a Process and two ends cannot coexist. That is not
   wrong — a capability costs a table row, and design 10 ruling 4's fused `Call`
   is precisely the answer for a client that does not want the row — but it means
   the in-flight budget is not independently reachable from one process, and it
   is why `pipe-oneshot` counts the ring with TELLs and holds exactly one
   exchange. A unit that wants to prove `PIPE_INFLIGHT` head-on will need two
   processes or a bigger table.
6. **THE IDIOM HELD, INCLUDING THE FOLD SHAPE.** Every bind-or-bail in the seven
   new packages and the four kernel files is the inline `try … catch` guard form
   or a plain propagating `try`; the `match` sites that remain are the four
   negative-test arms asserting a specific error value (`pipe-abandon`'s three
   and each fault case's one) plus the two `give`/`post` status checks that print
   different text per arm. No new sawlang deficiency was met beyond SL-13.
