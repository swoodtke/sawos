# SawOS design 15 — waitability: the pipe arms, delivery-carries-payload, and the attach modes (M4 unit 3)

Status: AUTHORED Sep 1 2026 (lead); **§API USER-REVIEWED SAME DAY (the
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
