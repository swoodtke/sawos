# SawOS design 15 — waitability: the pipe arms, delivery-carries-payload, and the attach modes (M4 unit 3)

Status: **LANDED Sep 1 2026** (was AUTHORED Sep 1, lead); **§API USER-REVIEWED SAME DAY (the
Sep-1 review gate, discharged): `give` as the consuming attach's name,
copy-out sized to the delivery, `PipeMsg` as the shared bytes struct,
and give's Err carrying the object back — all user-ruled.**
Ladder unit 3 of the ruled plan of record (designs/010): rulings 8,
9(a)/(b), and 11(a)-(c), taken together. The fused paths (`Call`,
`ReplyRecv`) are SPLIT to unit 3.5 (design 16, next brief) — this unit
is already the largest of the ladder, and the fuses want the delivery
machinery landed first. Ruling 11(d) (the completion-queue outlet,
delivery-as-take, delivery-minting) stays in unit 4 with rendezvous.

## The ruled surface (from designs/010, cited per item)

- **Four waitable arms** [§2.2 + ruling 8]: the OUTLET's readable
  level (drain-then-terminal: ready while staged > 0 OR inlet gone);
  the REPLY handle's reply-ready (one-shot delivery, 11(a)/(b)); the
  REQUEST handle's request-abandoned level (the server's cancellation
  watcher); the INLET's room-to-post level (ready while the ring has a
  free slot OR the peer is gone — the payload distinguishes which; a
  plain level, not terminal: consume clears nothing, the level's end
  is the poster's own next post). Wait rights gate every attach
  (`*Right.Wait`, the Timer precedent).
- **Peer-gone terminal levels** [D-1, design 8/12 precedent]: a parked
  waiter on any pipe arm whose "can still happen" dies gets a
  DELIVERED wake with a distinguishable payload, never a strand.
- **Ruling 9(a), MANDATORY**: an op that CONSUMES the object DETACHES
  it implicitly — `resolve` and `reply` drop the attachment as part of
  consuming. The detach drops a counted reference and CAN CASCADE A
  FREE: this unit censuses the new unref sites (delivery runs in the
  notifier's context and the waiter's both) and takes
  iterate-while-removing care in the delivery walk.
- **Ruling 9(b), OPT-IN**: `AttachMode.OneShot` — detach AT DELIVERY
  (the record for its key copied out; never at mere readiness).
  Spelled as a defaulted trailing parameter; zero churn at existing
  call sites. §2.2's "attachments are persistent" flips to
  persistent-BY-DEFAULT (amendment rides this unit, beside 8's).
- **Ruling 11(a)**: DELIVERY CARRIES THE PAYLOAD — the wait record
  grows a body region (data-only in this unit; handles are unit 4's),
  and a delivered reply SETTLES its exchange (the ring slot recycles
  at delivery). 11(b): on a one-shot kind, delivery is inherently
  consuming — the attachment detaches whatever mode was passed, and a
  kept sibling handle goes stale (generation-checked; a later resolve
  is the dead-handle fault — state it at the site and in §2.2).
- **Ruling 11(c)**: CONSUMING ATTACH IS A MOVE-TAKING VARIANT, not a
  mode value — ruling 4's own mode-enum rejection (a runtime flag
  cannot change a Saw function's effect). Kernel-side one op + a flag
  bit; the typed surface splits by signature. Orthogonal 2x2 with
  `AttachMode`.
- **The blocking send compositions** [ruling 4, amended]: `send(msg)`
  and `send(msg, timeout:)` become real as LIBRARY compositions over
  post + attach + wait (+ Timer on the same waiter). With 11(a) the
  resolve leg disappears on the success path. `TimedOut(pending:)`
  carries the still-live claim — so the COMPOSITION uses the
  NON-consuming one-shot attach (it keeps the handle; the timeout path
  hands it back per ratified §2.1, cancel = drop). The consuming
  attach is the multiplexer's tool, not the composition's.
- **Abandonment re-words to the LAST REFERENCE** [ruling 11]: a handle
  drop with a live attachment is not abandonment; removing the
  attachment or the Waiter dying before delivery drops the last
  reference and signals cancellation. §2.1 amendment rides this unit.

## API (PROPOSED — the review gate's subject)

### The record (sosabi)

Today: three words — `WAIT_KEY_WORD`, `WAIT_TAG_WORD`,
`WAIT_PAYLOAD_WORD`; `wait_record_bytes()` = 3 words. Proposed:

```
word 0  key            (as today)
word 1  tag            (as today; WaitTag grows 4 cases)
word 2  payload        (as today; per-kind meaning below)
word 3  body_len       (0 for every non-reply delivery in this unit)
then    body           PIPE_BODY_BYTES bytes (128), present in the
                       BUFFER always — wait_record_bytes() = 4 words
                       + 128; the kernel writes body_len of it
```

**THE COPY-OUT IS SIZED TO THE DELIVERY** (user, Sep 1): the caller's
buffer is max-sized because a parked waiter cannot predict which arm
fires, but the kernel COPIES only what the delivery needs — a
word-payload delivery (Event/Interrupt/Timer/Process/Room/Readable/
Abandoned) writes the 4 header words and stops; a reply-data delivery
writes 4 words + `body_len`. The record growth costs non-pipe waiters
BUFFER space only, never copy time.

One fixed-size record buffer for every waiter (the user's accepted
cost);
unit 4 grows it again with the handle slots (4 message handles + the
request handle), exactly as `Post` grew its return (§5.7).

### The decoded record (sosabi `WaitPayload` — the variable-sized enum)

```saw
public enum WaitPayload {
    case Event(word: UInt),
    case Interrupt(line: UInt),
    case Timer(fires: UInt),
    case Process(status: UInt),
    // new, this unit:
    case Readable(state: OutletLevel),    // outlet: Msg(depth) | PeerGone
    case Room(state: InletLevel),         // inlet: Room | PeerGone
    case Reply(outcome: ReplyDelivery),   // NoCopy — carries the body
    case Abandoned,                       // request handle's level
}
public enum OutletLevel: UInt { case Msg = 0, case PeerGone = 1 }
public enum InletLevel: UInt  { case Room = 0, case PeerGone = 1 }
public struct PipeMsg {                   // NoCopy; UNIT 4 REUSES THIS
    public len: UInt,                     // (its message deliveries carry
    public bytes: [UInt8; PIPE_BODY_BYTES],  //  the same struct + handles)
}
public enum ReplyDelivery {               // NoCopy (owns the bytes)
    case Data(msg: PipeMsg),
    case PeerClosed,                      // server abandoned: terminal
}
```

`WaitResult` keeps its `{ key, what }` shape and becomes `NoCopy`
(wrapper-carries-tier: the `Reply` payload owns 128 inline bytes).
`PipeMsg` is USER-RULED (Sep 1) over an inline array case: declared
this unit, so unit 4's message-carrying deliveries grow ONE type.
Callers that only route the bytes take them by reference out of the
payload.

### The kernel op (kernel/abi)

`WaiterOp.Add` grows a FLAGS word: bit 0 = OneShot, bit 1 = Consuming
(a consuming add's handle entry is released by the op — the transfer
funnel's kernel half; §5.7 makes the growth free). `WaiterOp.Wait` and
`Remove` unchanged. Four new attach-rights cases:
`PipeInletRight.Wait`, `PipeOutletRight.Wait`, `PipeReplyRight.Wait`,
`PipeRequestRight.Wait`, each `1 << 9`, in the default sets.

### The typed surface (sysapi `waiter.saw`)

```saw
public enum AttachMode: UInt { case Persistent = 0, case OneShot = 1 }

// The existing family gains the defaulted mode — zero churn:
public func add(&self, event: &Event, key: UInt,
                mode: AttachMode = AttachMode.Persistent) -> Result<Void, SosStatus>
// ... same on interrupt/timer/process, plus the four pipe kinds:
public func add(&self, outlet: &PipeOutlet, key: UInt,
                mode: AttachMode = AttachMode.Persistent) -> Result<Void, SosStatus>
public func add(&self, inlet: &PipeInlet, key: UInt, mode: ...) -> ...
public func add(&self, reply: &PipeReply, key: UInt, mode: ...) -> ...
public func add(&self, request: &PipeRequest, key: UInt, mode: ...) -> ...

// The CONSUMING variant is `give` (USER-NAMED, Sep 1): the tree's one
// verb for "ownership leaves through this receiver" — `Process.give`
// hands a handle to a process, `Waiter.give` hands it to a
// subscription. A distinct name rather than a by-value overload of
// `add`, so the consuming semantic is visible at the call site and
// overload resolution never hinges on a sigil. SAME VERB, DIFFERENT
// GATE — say so at the op: give-to-PROCESS crosses a process boundary
// and is gated by the `Transfer` right; give-to-WAITER crosses none
// and is gated by the `Wait` right alone.
public func give(&self, reply: PipeReply, key: UInt,
                 mode: AttachMode = AttachMode.OneShot)
    -> Result<Void, (SosStatus, PipeReply)>
public func give(&self, event: Event, key: UInt, mode: ...)
    -> Result<Void, (SosStatus, Event)>
// (one give per waitable kind; by-value parameter = move in, sentinel
//  disarmed on Ok. The Err shape is USER-RULED (Sep 1): a refused give
//  HANDS THE OBJECT BACK — the signature above is per kind
//    give(&self, reply: PipeReply, ...) -> Result<Void, (SosStatus, PipeReply)>
//  — the TimedOut(pending:) shape: a full attachment table is a
//  resource condition, not a bug, and the caller keeps what it had.)

// The composed sends (sysapi pipe.saw; the wrapper owns a Waiter):
public func send(&self, body: &[UInt8; PIPE_BODY_BYTES], len: UInt)
    -> Result<ReplyDelivery, SosStatus>          // parks: room, then reply
public func send(&self, body: ..., len: UInt, timeout: &Timer, after_ns: UInt64)
    -> Result<SendOutcome, SosStatus>
public enum SendOutcome {                        // NoCopy
    case Replied(r: ReplyDelivery),
    case TimedOut(pending: PipeReply),           // §2.1 ratified: the live claim
}
```

**THIS BLOCK IS STRUCK — USER RULING AT LEAD REVIEW, Sep 1.** No library `send`
ships: a wrapper type freezes a shape that cannot later be refactored into a
simple kernel call, the manual composition stays user-writable with the shipped
primitives, and a kernel-side timeout, if ever needed, will be added to the
kernel directly, then, by ruling. Everything else in this §API landed. See the
As-built's "The composed sends, as struck".

`wait()` keeps its signature (`-> Result<WaitResult, SosStatus>`); the
record buffer inside `wait_into_frame` grows to the new
`wait_record_bytes()`.

## D-1: delivery, the one copy-out, and settlement

The reply body BOUNCES THROUGH THE RING SLOT (ruling 4's mechanics,
already landed unit 2): `reply` copies in; the winning `wait` copies
out through its own checked funnel during the wait syscall — the
kernel never touches another process's memory. The slot settles at the
copy-out, and the exchange's columns fall with the attachment's
reference (9(a)'s cascade site). A reply delivered to a CONSUMING
attachment ends everything in one act; a non-consuming attachment's
delivery still settles the exchange (11(b)) and the client's kept
entry goes stale — generation-checked, stated at the site.

## D-2: the delivery walk, and the two cascade sites

Detach-at-delivery (9(b)) and consume-detach (9(a)) both drop counted
references inside walks that iterate the attachment list — the brief's
census obligation: (1) the delivery walk in the wait path (waiter's
context); (2) the notify path when a terminal level fires under
`drop_reference` (notifier's context — the unref there can cascade
`free_pipe` while the pipe's own arm is on the stack; the unit states
the reentrancy argument at the site or restructures to a
collect-then-free shape). Iterate-while-removing: the walk snapshots
next before delivering, design 12's free-arm precedent.

## Proofs (harness) — split print phases per function (design 14
finding 3: root's 16 KiB stack ceiling; arm64 meets it first)

1. **`pipe-wait-reply`**: post, attach reply one-shot (non-consuming),
   wait → the record carries the bytes; the exchange settled (a later
   resolve is the dead-handle fault, asserted); FIFO of two claims on
   one waiter by key.
2. **`pipe-wait-give`**: post, `give` the claim to the waiter, wait →
   data; zero handles held mid-flight (transcript prints the handle
   count); cancel-by-remove: give a second claim, `remove(key)` →
   server's `reply` answers `PeerClosed` (abandonment = last
   reference).
3. **`pipe-wait-room`**: fill the ring, attach inlet, wait → Room after
   a take-and-settle on the far side; drop the outlet → Room delivery
   with PeerGone payload; nothing un-closes.
4. **`pipe-wait-server`**: outlet readable arm — staged message wakes;
   drain; inlet dies → Readable(PeerGone) terminal after the drain
   (design 230 order); request-abandoned arm: client drops claim (last
   ref) → Abandoned delivery at the server's waiter.
5. **`pipe-send-blocking`**: the composed `send` round trip against a
   sibling-thread server (thread-basics' two-worker shape);
   `send(timeout:)` with no server → `TimedOut(pending:)`, then the
   pending claim dropped = cancel, server's later reply `PeerClosed`.
   **REWORKED, per the Sep-1 ruling above: it is `pipe-send-manual`, the
   composition written INLINE out of the primitives, against a CHILD server
   (a sibling thread cannot be handed a typed endpoint, so the boundary is a
   process); the room leg joins it, and `TimedOut(pending:)`'s substance is the
   non-consuming attach leaving the claim in hand.**
6. Negative arms where cheap: attach without the Wait right (refused);
   OneShot re-wait footgun stated (wait on now-empty waiter — the
   existing deadlock report, asserted); mode word out of range
   (`BadArg`).

Transcript expectation: existing rows byte-identical or address-only
(images grow — the facade compiles the new arms); `NotWaitable` arms
for the four pipe kinds are REMOVED (design 13's promise executed) —
no transcript reads them (unit 1 recorded why). New rows only.

## Docs owed

spec §2.2: the waitable list closes over the four arms (+ the inlet
membership amendment, ruling 8); persistent-by-default (9(b));
delivery-carries-payload and the record layout (11(a)); §2.1:
abandonment re-worded to last-reference; the send compositions marked
BUILT; §5.7 note at the Add flags growth. Tracker entry closed in
place; As-built in this file; SL-N for genuine deficiencies.

## Out of scope

`Call`/`ReplyRecv` and the ping-pong trap-count (unit 3.5 — design 16,
authored after this unit integrates; the end_process orphaned-claim
arm rides it); ruling 11(d) delivery-as-take + delivery-minting +
handle-carrying records (unit 4); the keep-mask rider (unit 4);
`select`-shaped composition sugar beyond the two ruled sends; the
money shot (unit 5).

## As built

**LANDED Sep 1 2026.** Suite 196/196 (98 cases/arch, riscv32 + arm64) from
182/182; SEVEN new cases across eight new packages, and no pre-existing case
row moved.

**AMENDED AT LEAD REVIEW THE SAME DAY BY A USER RULING: THE COMPOSED-SEND
SURFACE IS STRUCK.** `PipeClient` and `SendOutcome` are removed from
`sos.pipe`; nothing kernel-side changes, because the primitives — post, the
eight attaches, wait, resolve, the room level — WERE the surface all along. The
rationale is recorded here rather than argued: a wrapper type freezes a shape
that cannot later be refactored into a simple kernel call, the manual
composition stays user-writable with what shipped, and if a timeout is ever
genuinely needed it will be added to the kernel directly, then, by ruling. The
proof case was reworked rather than deleted — see "The composed sends, as
struck" below — and this is a RULING at review, not a deviation the unit
argued. §API's `send` block is the one part of the reviewed surface that did
not land; everything else did.

**VALIDATED ON sawlang 0.3.0** (`SAWLANG_ROOT` at `87063387`, unchanged from
dispatch to gate and re-checked at both ends — the tree's `sawlang.pin`
version). The baseline and the gate were compiled by the SAME compiler, so the
transcript diff below is attributable to this change alone. Nothing here leans
on anything 0.3.0 added.

### Transcript accounting

Whole-transcript diff, base `1ceb2d4` (the baseline captured before the first
edit) vs the gate, ANSI stripped and bucketed. The parse is mechanical (case
rows `[n/m] MARK name`, image rows `path (N bytes)`, everything else):

| bucket | rows | what |
|---|---|---|
| byte-identical | 182 case-row MARKS + names + INDICES | every pre-existing case, same mark, same name, and the same index within its arch. `thread_preempt` and `timer_interval` — the two known timing rows — included, so the documented-nondeterministic bucket is EMPTY this run |
| authorized-with-cause | 182 case-row denominators | `[n/91]` -> `[n/98]`, the DENOMINATOR alone, because seven cases were appended. Zero indices moved, which is what says they were appended rather than inserted |
| authorized-with-cause | 52 image-size rows | every image that CALLS `Waiter.wait` grew: 27 riscv32 rows by +24 to +13000 bytes, 25 arm64 rows by +4096 to +12288 (page granularity on that profile). **THE CORRELATION IS EXACT AND WAS VERIFIED RATHER THAN ASSUMED**: every package with a grown image reaches the wait door, and NO package that does not reach it grew — 53 riscv32 and 55 arm64 images did not move at all. The cause is the decode: `decode_wait` now names the four new `WaitPayload` cases, so an image that waits links `sos.pipe`'s payload types and the composed sends with them, and an image that never waits links none of it |
| authorized-with-cause | 2 image-size rows | `refcount-free` (+40 riscv32) and `waiter-revoked` (+24 riscv32) are the two C-altitude waiters, and they grew for a DIFFERENT reason: their hand-sized record buffers went from 3 and 4 words to 48, because the published minimum grew by a message body. They are inside the 52 above; named separately because the cause is the frame and not the link |
| authorized-with-cause | 1 summary row | `182 passed` -> `196 passed` |
| authorized-with-cause (SECOND PARK) | 2 case rows removed, 2 added; 2 image rows removed, 2 added | `pipe_send_blocking` -> `pipe_send_manual` at the user ruling above. A renamed case's rows are a removal and an addition, and the new one takes the SAME INDEX (`[96/98]`, both arches) with the same mark, so nothing else in the ordering moved. Its five text rows moved with it: three by prefix alone (`sendblock` -> `sendmanual`), one by name (`send got len=4` -> `reply len=4`, since there is no `send` to have got it), and ONE IS GENUINELY NEW — `filled=16 then room says there is space`, the room leg the wrapper hid and the manual composition has to take. The two image rows are the renamed package's, and they SHRANK: -9432 bytes riscv32, -12288 arm64 |
| address-only | 0 | nothing printed an address that moved |
| documented-nondeterministic | 0 | none met |
| new | 14 case rows + 16 image rows | the seven cases and eight packages, on both profiles |

No row is unaccounted for, and **NO IMAGE SHRANK on either profile.**

**THE MOST INTERESTING THING THE DIFF DOES NOT SHOW IS `pipe-basics`' AND
`pipe-oneshot`' RING-DEPTH ROWS, WHICH DID NOT MOVE.** This unit added an
`ExchangeState` case, two attachment arrays, two `PipeSlot` fields and a notify
at four zero-arms; `ring depth=16`, `drained=16`, `depth with one in flight=15`
and `depth settled=16` all still say what they said, byte for byte, which is
what says the lifecycle those numbers describe is untouched by the wait
machinery bolted onto it.

### The four arms, as landed

`WaitableKind` grew from four cases to eight, and the four new ones are the
four kinds units 1 and 2 wrote `NotWaitable` arms for. What each answers:

| kind | slot | ready when | delivery spends |
|---|---|---|---|
| `PipeOutlet` | the CONNECTION | anything staged **or** the inlet column is zero | nothing — the level ends at a `Take` |
| `PipeInlet` | the CONNECTION | the outlet column is zero **or** a ring slot is free | nothing — the level ends at the poster's next `Post` |
| `PipeReply` | an EXCHANGE | `Replied`, or abandoned-by-server | **the exchange**: it moves to `Settled` |
| `PipeRequest` | an EXCHANGE | `Taken` with the claim column at zero | nothing — terminal |

Four things about that are worth reading:

- **THE TWO ENDPOINT ARMS INDEX THE CONNECTION AND THE TWO ONE-SHOT ARMS INDEX
  AN EXCHANGE, WHICH IS THE IDENTITY THE HANDLE TABLE ALREADY CARRIED.**
  `entry.target` means the connection for an endpoint kind and the exchange for
  a one-shot, because designs 13 and 14 made it mean that; the wait machinery
  inherited both and invented no index of its own. What it DID add is one
  attachment field per column — two on `PipeSlot`, two parallel arrays beside
  `PIPE_LENS` — because §2.2's at-most-one-Waiter rule holds per WAITABLE, and a
  claim and its obligation are two waitables that two different processes may
  watch at once.
- **THE TWO `PeerGone` ORDERINGS ARE OPPOSITE, AND EACH MIRRORS ITS OWN OP.**
  The outlet is drain-then-terminal (`Msg` wins while anything is staged, design
  230's rule, `pipe_take`'s own order); the inlet is peer-first (`PeerGone` wins
  even with room, because `pipe_post` asks about the outlet column before it
  asks about the ring). Writing each level to read in its op's order is what
  makes the two impossible to disagree — a waiter and a caller ask the same two
  questions in the same sequence.
- **`ExchangeState` GREW A FIFTH CASE, AND IT IS THE ONLY THING THIS UNIT ADDED
  TO THE UNIT-2 STATE MACHINE.** `Settled` means "the answer went out through a
  wait". It has to exist: a delivery hands the bytes over exactly as `Resolve`
  does, but it does not travel through the claim's handle ENTRY, so it cannot
  consume one — and without a state to move to, a claim the caller KEPT would
  resolve afterwards and get the same answer a second time. That is the
  duplicate §2.2's consume rule exists to make impossible, and it is the one
  place the columns genuinely could not derive the state (see deviation 1).
- **NOTHING NEW IS ALLOCATED ANYWHERE.** The two attachment arrays are
  zero-initialized `.bss` (design 149), the two `PipeSlot` fields are two words,
  and the record's body region is the caller's frame. `MAX_ATTACHMENTS` was
  already sized as `MAX_EVENTS + MAX_INTERRUPTS + …` and needed no change,
  because the four new kinds attach through handles that already existed.

### The delivery, and where it moved

**`kcore.wake` STOPPED EXISTING.** Its four functions are the second half of
`kcore.refs` now, and the module list in `kcore/lib.saw` is one seam shorter.
The reason is a genuine cycle rather than a preference, and both directions
arrived with this unit's own rulings:

- ruling 9(b) makes a DELIVERY drop a counted reference (a one-shot attachment
  detaches when its record is copied out, and an attachment is a reference), so
  `deliver_attachment` has to reach `unref_waitable`;
- the peer-gone terminal levels make a DROPPED REFERENCE deliver (a column
  reaching zero is what raises them), and the only function that observes a
  column reaching zero is `drop_reference`.

Two modules cannot hold that, and DF-232e does not diagnose the cycle — it
empties an export table and blames a third file. The alternatives were weighed:
threading the released attachment back up through `deliver_attachment` ->
`wake_one_waiter` -> `notify_ready` -> `signal_event` to seven call sites, each
of which would then owe an unref, was the "collect-then-free shape" the brief
sanctions and was REJECTED as seven chances to leak. The merge changes no
altitude: the merged module still sits above `process` (it needs the copy door)
and below `irq`, which is exactly where `refs` already was.

### The composed sends, as struck

**WHAT WAS REMOVED**: `PipeClient` (the struct, its `init`, its forwarded
`post`, both `send` methods and the private `stage`), `SendOutcome`, and the two
record-reading helpers `delivery_of` / `room_of` that only the composition used
— together with the `sos.timer` import they needed and the facade's two
re-exports. About 200 lines of `sos.pipe`, and nothing else in the tree
referenced them.

**WHAT DID NOT MOVE**: the eight `Waiter` overloads, `AttachMode`, the four
matrix columns, the record's body region, the flags word — every primitive the
composition was written over. `sos.pipe` still imports `sos.waiter` and still
sits above it, because the four pipe `add`/`give` overloads are the reason for
that ordering and they stay.

**AND NO OTHER IMAGE IN THE SUITE CHANGED BY A BYTE**, which is the removal's
own receipt: 174 of the 176 image rows are identical across the two parks, so
the composed sends were code the linker was already dropping everywhere except
the one case that called them. The two that did change are that case's, and they
got SMALLER — -9432 bytes on riscv32 and -12288 on arm64 — because a
composition written once inline costs less than a wrapper's generic machinery.
A library layer that measured as dead weight in every image but one is a library
layer the ruling was right about.

**WHAT REPLACED THE PROOF.** `pipe-send-blocking` became `pipe-send-manual`,
and the change is not a rename with the same body: the composition moved INTO
the test, inline, as the four calls a caller makes — post, `add(reply:)` one-shot,
`wait`, and a Timer on the same Waiter for the timeout leg, with the KEY the
wait answers deciding which of the two won and the three lines of bookkeeping
after it (remove the loser, keep the claim) written out. Two things improved by
being written by hand rather than hidden:

- **THE ROOM LEG IS NOW EXERCISED BY THIS CASE TOO** (ruling 8). The wrapper's
  `stage` looped over it invisibly; the manual version fills the ring first, so
  the post is genuinely REFUSED and the program has to park on the inlet's level
  before it has a claim at all. `filled=16 then room says there is space` is the
  new row, and it is the leg a library `send` would have concealed.
- **`TimedOut(pending:)`'s SUBSTANCE SURVIVES WITHOUT THE TYPE.** The attach is
  non-consuming, so the claim is the caller's the whole time and is simply still
  in hand when the Timer's key comes back — which is what the enum case was
  carrying. `after cancel …` is unchanged: dropping it is §2.1's cancellation
  primitive and the server's later `reply` is told.

The other three observable rows survive verbatim in substance and moved only in
their prefix (`sendblock` -> `sendmanual`) and in one name (`send got len=4` ->
`reply len=4`, because there is no `send` to have got it): the child's parked
service, the `PONG` bytes arriving in the wait record, and the timeout. The
case is still the only one in the unit whose threads really park.

### The cascade census (the brief's D-2 obligation)

Every site where this unit drops a reference inside something that is being
walked, and what makes each safe. **All four are safe by a guard that already
existed**, which is the census's actual finding — the reference model was
already doing this work for units 1 and 2's cascades:

1. **`deliver_attachment`'s detach** (9(b) + 11(b)). It reads the kind, target,
   key and waiter UP FRONT and then detaches, so the attachment slot being
   recycled underneath it is reachable by nothing. The unref can settle an
   exchange and cascade into `free_pipe`; nothing after the detach reads either.
   `wake_one_waiter` above it has already taken its thread OFF the blocked list
   before calling in, so no list walk is open.
2. **`drop_reference`'s `PipeOutlet` arm.** `pipe_drop_staged` walks a LIST
   threaded through `PIPE_NEXT`, and settling an exchange zeroes its link — so a
   delivery inside that walk could truncate it. **RESTRUCTURED**: the list walk
   finishes first, and the notifications go out afterwards as `notify_claims`, a
   bounded SCAN over the ring's fixed indices that holds no position in anything.
   An exchange settled underneath the scan becomes `Free`, which is unattached,
   which the next iteration skips.
3. **`drop_reference`'s two one-shot arms.** Each notifies the OTHER column,
   whose delivery can re-enter `drop_reference` on this very exchange and settle
   it. Safe because the arm's own predicate is evaluated AFTERWARDS:
   `exchange_can_settle` answers `false` for a `Free` slot, which is the honest
   answer for "should the caller free it" once somebody else already has.
4. **`drop_reference`'s two endpoint arms.** A delivery can free the connection
   under them; `pipe_frees_now` is guarded on `PIPES[slot].state`, and design
   14's As-built already recorded that the test is not defensive.

Two INVARIANTS were added rather than argued: `free_pipe` and `exchange_settle`
now `fatal_kernel` if either of their attachment fields is non-zero. Both are
unreachable by the reference model (an attachment is a reference on the column
being tested), which is exactly why they are worth asserting — if either fired,
the ledger and the wait machinery would have disagreed.

### What the compiler enumerated

1. `kcore.waitables` — the per-kind matrix, all FIVE questions, 4 arms each:
   `waitable_attachment`, `set_waitable_attachment`, `waitable_ready`,
   `waitable_answer`, `waitable_consume`. **THIS IS WHERE THE DESIGN GOT
   DECIDED**, exactly as `drop_reference` was in units 1 and 2: being made to
   answer "what does a delivery of this kind SPEND" for a reply is what produced
   `ExchangeState.Settled`, and being made to answer it for the two levels is
   what surfaced that they spend nothing for two DIFFERENT reasons.
2. `kcore.refs.waitable_obj_type` — 4 arms, and a second copy of the same
   translation in `dispatch.waitable_obj_of` for the consuming attach's release.
3. `kcore.dispatch.waitable_slot` — the 4 `NotWaitable` arms REPLACED. The
   design-8 precedent collected a fifth, sixth, seventh and eighth time, and
   this is the shape a `_` arm would have hidden entirely.
4. `sos.waiter.decode_wait` — 4 arms from the four new `WaitTag` cases.
5. `ExchangeState.Settled` enumerated itself at THREE sites —
   `exchange_can_settle`, `pipe_resolve` and `pipe_reply` — which is what turned
   "a delivered reply is discharged" from a sentence into three answers.
6. **AND IT REACHED INTO FIFTEEN TEST PACKAGES**, which is the property working
   at its widest: adding four `WaitPayload` cases broke every exhaustive match
   on a wait answer in the tree. See deviation 3 for what was done about it.
7. `Attachment` grew `oneshot` and `PipeSlot` grew two fields, so every literal
   of each failed until updated — 3 and 2 sites, in `waitables`, `refs`, `sched`
   and `dispatch`.
8. The raw-altitude callers failed at the LINK rather than at a match:
   `sos_waiter_add` grew an argument (1 site, `tests/refcount-free`), and the
   record's published minimum grew past four hand-written buffers (2 asm
   payloads × 2 arches) and two Saw ones.

`SosStatus` needed NOTHING — `PeerClosed` says here exactly what its unit-1
docstring says it says — and `QuotaKind` needed nothing either, which is the
quota note in spec §11 holding.

### Deviations from the brief, argued

**The user-reviewed §API landed as written except where noted; the two
departures below are both in D-1's PROSE rather than in §API, and each is a
soundness argument rather than a preference.**

1. **A NON-CONSUMING DELIVERY DOES NOT FORCE-SETTLE THE EXCHANGE; IT DISCHARGES
   IT, AND A LATER `resolve` IS `PeerClosed` RATHER THAN A DEAD-HANDLE FAULT.**
   D-1 says "a non-consuming attachment's delivery still settles the exchange
   and the client's kept entry goes stale — generation-checked". Taken
   literally that is unsound: a settled ring slot returns to the ring, and a
   handle ENTRY still naming it is not caught by any generation — handle
   generations live on the handle-table ROW, which is still bound, so the entry
   would resolve cleanly onto whatever exchange the next `Post` puts there.
   (Concretely: the stale holder's own drop would decrement somebody else's
   claim column and settle their exchange.) What landed instead keeps the
   reference rule intact — the exchange settles when its columns fall, which for
   a CONSUMING attach is at the delivery and for a non-consuming one is at the
   client's own drop or resolve — and adds `ExchangeState.Settled` so the
   observable half of D-1's sentence is true: the answer is spent, and a later
   `resolve` gets the word an abandoned peer gets. The kernel cannot destroy a
   handle entry it did not travel through; that is what `give` exists for, and
   the composed `send` is the one caller that deliberately does not use it.
2. ~~**THE COMPOSED SENDS ARE METHODS ON A NEW `PipeClient` WRAPPER**~~ —
   SUPERSEDED, and the whole surface with it. This deviation existed because
   §API's `send(&self, body:, len:)` is unwritable on a `PipeInlet` (a Waiter is
   made by `ProcessOp.WaiterCreate`, an inlet has no `Process` handle and no way
   to reach one, and giving every connection a Waiter at `pipe_create` would
   charge a `QuotaKind.Waiter` row to clients that never block), so the wrapper
   that owns a Waiter became a TYPE. The user's ruling at review struck the
   surface rather than choosing between the two spellings, which retires the
   question: see "The composed sends, as struck".
3. **FIFTEEN TEST PACKAGES' `WaitPayload` MATCHES COLLAPSED TO ONE ARM PLUS A
   DEFAULT, RATHER THAN GAINING FOUR PANIC ARMS EACH.** Design 8's tradition is
   to write every arm out; at eight waitable kinds that is seven identical
   panics per program, 105 lines across the tree, in code whose whole content is
   "this program attached only an Event". The enumeration property is what
   FOUND these (the compiler broke all fifteen), and it is a property the KERNEL
   wants and a test program does not: a case that does not use a kind should
   keep compiling when the kernel grows one. So each is now
   `case Event(word) -> word,` plus `case _ -> panic("this program attached only
   an Event")`, which says the same thing in one line. The kernel's own matrix
   keeps every arm.
4. **`AttachMode` IS DECLARED IN `sos.waiter`, AS §API SHOWS, AND THE FLAG BITS
   ARE IN `sosabi`.** The enum's numbers are the typed surface's own and the
   wire's are the ABI's, converted by a `match` — which is the vDSO discipline
   said out loud, and is why the kernel carries a `Bool` on the attachment
   rather than the enum.
5. **THE `give` FAMILY IS EIGHT OVERLOADS, ONE PER WAITABLE KIND**, as §API's
   parenthesis asks. Four live in `sos.pipe`, three in `sos.waiter` and one in
   `sos.system`, and the placement is design 8's rule rather than a choice: an
   overload reads its argument's handle field, so it sits where the argument's
   type is declared. `sos.pipe` moved ABOVE `sos.waiter` for it (it names
   `Waiter` now, because the composed sends park on one), which is the first
   time that ordering has had a reason other than the facade's convenience.
6. **A REFUSED `give` RE-MINTS THE WRAPPER RATHER THAN HANDING THE ORIGINAL
   BACK.** §API's `Result<Void, (SosStatus, T)>` is exact; what differs is the
   body. The natural spelling — disarm on success, `move owned` into the error
   tuple — is refused by sawc even though the catch DIVERGES (filed as SL-14).
   The landed shape is stronger anyway: disarm BEFORE the syscall, which is the
   transfer-funnel contract verbatim, and build a FRESH wrapper around the word
   on the one answer that provably consumed nothing. No path can leave two
   owners of one word.

### Findings

1. **THE CENSUS'S REAL RESULT IS THAT THE REFERENCE MODEL HAD ALREADY DONE THE
   WORK.** Every one of the four new cascade sites is safe by a guard units 1
   and 2 wrote for their own reasons — `pipe_frees_now`'s state test,
   `exchange_can_settle`'s `Free` arm — and the one site that genuinely needed
   restructuring (`pipe_drop_staged` beside the staged-claim notifications) was
   fixed by not walking a list at all. That is worth recording because the
   brief's obligation was framed as new-hazard-hunting and what it found was an
   invariant holding one level further out than it was written for.
2. **THE MODULE MERGE IS THE UNIT'S ONE STRUCTURAL CHANGE, AND IT WAS FORCED BY
   A RULING RATHER THAN BY CODE VOLUME.** `kcore`'s facade has a paragraph about
   where a seam lives — state below the teardown, acting above it — and this
   does not bend it: `wake` and `refs` were both the acting half, and 9(b) plus
   the peer-gone levels made them one act. A reader looking for the delivery now
   finds it in the file that owns the ledger, which reads correctly once the
   header's two-directions paragraph is read.
3. **A `NoCopy` PAYLOAD IS READ THROUGH A BORROW AND THAT COSTS A CALLER
   NOTHING.** The reviewed API makes `PipeMsg`, `ReplyDelivery`, `WaitPayload`
   and `WaitResult` all `NoCopy`, which was the one thing about the surface that
   looked risky to land: a `match` binding a move-only payload out of a place is
   normally refused. It is not, because a `match` on a PLACE matches where the
   value sits — `match r.what { case Reply(outcome) -> match outcome { case
   Data(msg) -> msg.bytes[i] } }` compiles and reads one byte. So the discipline
   is free: 128 inline bytes cannot be duplicated by an accident of binding, and
   nothing at a call site pays for that.
4. **ROOT'S 16 KiB STACK CEILING HELD WITH NO INCIDENT, BECAUSE THE PHASE SPLIT
   WAS WRITTEN FIRST.** Design 14 finding 3 said unit 3's cases would be longer;
   they are (four phases in the largest), and one-phase-per-function was applied
   from the first line rather than after an arm64 fault. The record's body region
   is a further 128 bytes in `wait_into_frame`'s frame and 192-384 in the two
   C-altitude cases' — neither is close to the grant.
5. **THE HANDLE TABLE STOPS BOUNDING A MULTIPLEXING CLIENT.** Design 14 finding
   5 recorded that `MAX_HANDLES` (16) rather than `PIPE_INFLIGHT` (16) is what a
   client that holds its claims meets first, and named the fused `Call` as the
   answer. `Waiter.give` is a SECOND answer and it arrived first: a client that
   gives its claims to a subscription holds zero handles for them, so the
   in-flight budget becomes reachable from one process. Nothing in this unit
   proves it at depth (`pipe-wait-give` gives one claim at a time), and a case
   that fills the ring through given claims is a cheap addition whenever a unit
   wants it.
6. **TWO NEW sawlang DEFICIENCIES, BOTH MINIMALLY REPRODUCED**: SL-14 (a `move`
   inside a DIVERGING inline catch retires the binding on the fall-through path
   too) and SL-15 (a bare integer literal does not adopt a `UInt` parameter when
   the method is OVERLOADED, though the same call on a single-candidate function
   adopts). Both have in-tree workarounds that read fine — the re-mint in
   deviation 6, and a named `static` at the one call site — so neither blocked
   anything.
7. **THE IDIOM HELD.** Every bind-or-bail in the eight new packages and the six
   kernel/sysapi files is the inline `try … catch` guard form or a plain
   propagating `try`. The `match` sites that remain are the negative-test arms
   asserting a specific status (`pipe-wait-reply`'s one, `pipe-wait-give`'s two,
   `pipe-send-manual`'s one, the two refusal cases' one each) and the four
   `give` bodies' success/failure split, which is a genuinely-different-arms
   match rather than a fold.
8. **THE LEAD-REVIEW RULING COST THE UNIT NOTHING KERNEL-SIDE, WHICH IS WHAT
   SAYS THE SLICE WAS DRAWN IN THE RIGHT PLACE.** Striking the composed sends
   removed 200 lines of one sysapi file and reworked one test; no op, no right,
   no matrix column, no record field and no test but that one moved. A unit
   whose library layer could be deleted without disturbing what it built is a
   unit whose library layer was a convenience — which is the ruling's own
   argument, arriving as evidence rather than as a prediction.
