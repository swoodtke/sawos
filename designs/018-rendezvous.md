# SawOS design 18 — rendezvous: handles in messages, delivery-as-take, the keep-mask (M4 unit 4)

Status: AUTHORED Sep 2 2026 (lead); **§API USER-REVIEWED SAME DAY
(the review gate, discharged): the `PipeHandle` enum, the three
rendezvous-ledger rules, `give(keep:)` with no mask on message-attach,
and HANDLES-BEFORE-BYTES in the record (the fixed-prefix rule) — all
user-ruled. Dispatched Sep 2. **AMENDED SAME DAY at the agent's
clean STOP (three §API flaws caught against the tree; user-ruled
fixes): (1) the handle region carries KINDS — one byte per slot
(request + 4 message slots, BootHandleKind values, 0 = empty), and
`len` shrinks to a u8 PACKED WITH THEM (128-byte max needs no word);
the standalone body_len word dies; the agent lays out the exact
per-profile meta words inside the fixed-prefix rule and argues the
offsets. (2) The decoded vocabulary (PipeMsg, ReplyDelivery,
WaitPayload, WaitResult, decode_wait, Waiter.wait) moves from sosabi
to sos.pipe via extension Waiter; wire enums + offsets stay in
sosabi. (3) PipeHandle is SIX cases — Memory, IoMemory, Inlet,
Outlet, Reply, Request (givable AND declarable; the four
Transfer-less kinds were dead surface, System/Process are the
DF-232e cycle) — with Process/System-in-messages a NAMED FOLLOW-UP
wanting a module-split ruling. PIPE_MSG_HANDLES lands in sosabi
beside PIPE_BODY_BYTES (design 13 deviation 1 verbatim).** Ladder unit 4 of the ruled
plan of record (designs/010): ruling 5 (the staged handle's ledger),
ruling 11(d) (the completion-queue outlet), and the keep-mask rider
(agenda 7b — the ladder left the placement to this brief; folded here,
where `give` and message-attach share a funnel). Built on sawc 0.4.0's
`consumes` (SL-16 closed) — the brief writes the FINAL spellings.

## The ruled surface (from designs/010, cited per item)

- **Ruling 5 — a staged handle stays the SENDER's counted entry until
  receive**, transferred exactly at rendezvous: the ledger never has
  an in-flight limbo state, and an abandoned send unwinds with
  nothing leaked. `PIPE_MSG_HANDLES = 4` lands (unit 1 deferred it to
  the first unit that reads it), a `kcore.limits` static with the
  §2.1 static_assert.
- **Ruling 11(d) — the completion-queue server**: a
  persistent-consuming outlet attach makes delivery THE take — the
  record carries the message AND MINTS the `PipeRequestHandle` into
  the wait-caller's table at delivery. Includes the STATED ARM for a
  wait-caller whose table cannot accept the mint. Server trap count
  completes the measured ladder: manual 3 → fused 2 → completion
  queue **1**.
- **The record grows its handle slots** [ruling 11's sizing, the
  user's own]: (128 body bytes + 4 message handles) for the reply
  direction, plus the server delivery's one request handle — §5.7
  makes the growth free, exactly as unit 3 grew it.
- **The keep-mask rider** [agenda 7b, composing with the unit-6
  `ProcessSelf` finding]: attenuate-at-give — the giver states what
  the receiver keeps.
- **The unit-3.5 loose ends ride along**: the two seams missing from
  the facade's floor re-export (`sos_pipe_request_reply_wait`,
  `sos_pipe_inlet_call` — the 3.5 omission); the `var`→`let` tidy at
  the 22 converted call sites (authorized for the files this unit
  touches).

## API (PROPOSED — the review gate's subject)

### The received-handle type (sysapi)

A handle arriving in a message is typed at the boundary, the
`decode_boot_handle` precedent generalized:

```saw
public enum PipeHandle {              // NoCopy — each case OWNS a wrapper
    case Event(e: Event),  case Waiter(w: Waiter),  case Timer(t: Timer),
    case Memory(m: Memory), case IoMemory(io: IoMemory),
    case Process(p: Process), case Interrupt(i: Interrupt),
    case Inlet(i: PipeInlet), case Outlet(o: PipeOutlet),
    case Reply(r: PipeReply), case Request(rq: PipeRequest),
}
```

One case per GIVABLE kind (the `boot_kind_of` set). The sender builds
one by moving a wrapper in (`PipeHandle.Event(e: move ev)`); the
receiver matches it out. Four slots per message:

```saw
public struct PipeMsg {               // GROWN, not new (unit 3's type)
    public len: UInt,
    public handles: [PipeHandle?; PIPE_MSG_HANDLES],   // None = empty slot
    public bytes: [UInt8; PIPE_BODY_BYTES],
}
```

**HANDLES BEFORE BYTES (user, Sep 2)** — the fixed-prefix/
variable-tail rule, composing with unit 3's copy-out-sized-to-the-
delivery: the record is 4 header words, then the FIXED handle region
(the request slot + `PIPE_MSG_HANDLES` message slots, 5 words), then
the body — so every copy-out is a contiguous prefix ending at
`body_len`, a handles-only message never pays a 128-byte hole, and
every handle word sits at a fixed offset for the decode. The Saw
struct mirrors the record's order (declaration-order ABI).

### The grown ops (kernel/abi — §5.7 growths, no new ops)

- **`Post`/`Call`** grow 4 handle-word args (NO_HANDLE = empty).
  Staging validates kind+rights (a non-givable word, or one lacking
  `Transfer`, refuses the WHOLE post — atomic, nothing staged).
- **`Take`/`ReplyWait`'s record + `Reply`** mirror: replies carry
  handles on the same terms (§2.1's send(data?, handles?) both
  directions).
- **`WaiterOp.Add`'s consuming form on an OUTLET, Persistent** now
  legal (unit 3 refused it pending this unit): the completion-queue
  attach. Delivery-as-take mints the request handle + any message
  handles into the wait-caller's table.

### The rendezvous ledger (ruling 5 made mechanical)

The ring slot stages the sender's HANDLE WORDS (entry references),
not copies: the entries stay the sender's — counted in its table,
charged to it — until the take/delivery, where the kernel moves each
entry (unbind sender, bind taker — `give`'s own kind-generic
machinery). Consequences, each a proposed rule:

- **The sender may release a staged entry** (fire-and-forget is
  legal): at rendezvous each staged word RE-VALIDATES through the
  generation check; a dead one delivers as an EMPTY slot (`None`) —
  nothing leaked, nothing forged, the receiver told the truth.
- **The taker's table cannot accept the mints** → the take/delivery
  REFUSES ATOMICALLY: the message stays staged, the level stays
  ready, the answer is the table-full status; free a slot and retry.
  No partial delivery — handles and body move together or not at all.
- **Outlet-death staged-drop** (unit 1's `pipe_drop_staged`): staged
  handle references are simply forgotten — the entries never left the
  sender, so there is nothing to unwind; the sender still holds what
  it always held.

### The keep-mask (sysapi + kernel)

```saw
// give grows a defaulted mask — zero churn, runtime value, same type:
public func give(&self, event: Event, keep: UInt32 = KEEP_ALL) -> ...
// (all give overloads; message-attach does NOT take a mask — an
//  attenuated send is a minted-narrow sibling posted instead, the
//  mint machinery already owed exactly this)
```

The kernel `Give` op grows the mask word; the minted entry's rights
are `source & keep`; `keep` bits the source lacks are ignored (a mask
can only narrow — fail-closed). `ProcessSelf`'s mint joins the same
discipline (the unit-6 finding: it mints outside any mask today).

## The proof (harness)

1. **`pipe-handles`** — round trip with handles both directions;
   HANDLE-COUNT ACCOUNTING as transcript: the sender's count is
   unchanged after post, drops at the peer's take (ruling 5's ledger,
   observed); reply carries a handle back.
2. **`pipe-delegate-msg`** — the request handle forwarded INSIDE a
   message (root takes from pipe A, posts the request through pipe B;
   the child replies to the forwarded request; the reply lands at the
   ORIGINAL claim). MAX_PROCESSES=2 makes the topology a round trip
   through two pipes rather than three processes — the mechanism
   (a one-shot crossing a process boundary in a message) is fully
   proven; the three-process spelling waits on unit 5's topology and
   the MAX_PROCESSES bump it forces.
3. **`pipe-cq`** — the completion-queue server: persistent-consuming
   outlet attach, the serve loop at reply_wait only, trap counts
   asserted over ProcessStats: server == N + brackets (ONE trap per
   message), printed beside 3.5's measured 2 — the ladder complete.
4. **`pipe-stale-attach`** — sender posts a handle then releases its
   entry; the take delivers the slot as None; nothing leaks (teardown
   counts prove it).
5. **`pipe-table-full`** — the taker's table filled; the take refuses
   atomically; the message survives; freeing a slot and retrying
   succeeds.
6. **`give-keep-mask`** — a child given an Event with `keep` lacking
   Signal cannot signal it (refused), can wait on it; a keep bit the
   source lacks is ignored (fail-closed proven).

## Docs owed

spec §2.1 flips to BUILT WHOLE (message model complete: data,
handles, both directions, delegation); §2.2 the record's final
layout; the counted-kinds table's staged-reference note; quota table
(charge moves at rendezvous); §9 keep-mask; the facade re-export
line. Tracker entry closed in place; As-built here; SL-N for genuine
deficiencies.

## Out of scope

The money shot (unit 5 — driver-as-service, and the MAX_PROCESSES
bump with the three-process delegation); the M4 docs sweep (unit 6);
the dissolve-helper backlog item (SL-3's remainder); the shared stats
region; namespaces, kill, priorities (the standing tail).
