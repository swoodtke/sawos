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

---

## As-built (M4 unit 4, Sep 2 2026) — SHIPPED

Base `c2a31ef` (the amendment above). The suite baseline for transcript
accounting was captured at `7362a3f`, which is the same tree for build
purposes — `c2a31ef` touches `designs/` only.

**EVERYTHING §API ASKED FOR LANDED, IN THE AMENDED SPELLING**, plus the
three riders. No new object, no new kind, no new slab and NOT ONE NEW
OP — §5.7's renumberable discipline paid for the whole unit on the
ARGUMENT side, exactly as unit 3 spent it on the flags word.

### 1. The record, and the argument the layout deserves

The wait record's final shape, in declaration order (declaration order
IS the layout, design 58):

```saw
public struct WaitBuffer {
    public header: [UInt; WAIT_HEADER_WORDS],   // key, tag, payload
    public meta: PipeMsgMeta,                   // len: UInt8, kinds: [UInt8; 4]
    public handles: [UInt; WAIT_HANDLE_SLOTS],  // request, then 4 message slots
    public body: [UInt8; PIPE_BODY_BYTES],
}
```

**THE PER-PROFILE ANSWER IS THE NATURAL ABI'S, AND NOBODY COMPUTED IT.**
`PipeMsgMeta` is five bytes at alignment 1; the handle region that
follows wants machine-word alignment, so the meta lives in padding the
struct needed anyway. Measured, both profiles:

| region | riscv32 | arm64 |
|---|---|---|
| header | 12 B (3 words) | 24 B (3 words) |
| meta | 8 B = **2 words** | 8 B = **1 word** |
| handles | 20 B (5 words) | 40 B (5 words) |
| body | 128 B | 128 B |
| `wait_record_bytes()` | **168** | **200** |
| `wait_prefix_bytes()` | 40 | 72 |
| `wait_meta_end_bytes()` | 20 | 32 |

The meta occupies two words on the 32-bit profile and one on the 64-bit
one, and it costs NOTHING on either: three header words plus five meta
bytes round to 20 on riscv32, which is where a word-aligned handle
region would have started regardless. Three `static_assert`s pin it —
the buffer holds every region; the fixed prefix is a whole number of
machine words (which is what makes "a contiguous prefix ending at `len`"
an arithmetic rather than a hope); and the meta fits between the header
and the handle region, which is the assert that would catch a raised
`PIPE_MSG_HANDLES`.

**THE RECORD GOT SMALLER, WHICH IS THE USER'S ADDITION PAYING OUT.**
Unit 3 left it at 4 header words + 128 body = 144 B / 160 B. Unit 4
added five handle words and a kind byte apiece and lands at 168 / 200 —
but the standalone length word DIED, so the growth is 24 B / 40 B rather
than 28 B / 48 B, and the SHORT prefix a Timer delivery writes went from
16 B / 32 B to **20 B / 32 B**: one word more on riscv32, IDENTICAL on
arm64. A Timer waiter pays for handles in buffer space and, on the
64-bit profile, in nothing at all.

**THE RAW-ALTITUDE BUFFERS STILL CLEAR IT**, re-verified as instructed:
`tests/waiter-revoked` over-provisions 48 words = 192 B / 384 B against
168 / 200, and the hand-assembled payloads' 256 B clears both. Neither
moved and neither had to.

Offsets are published in `sosabi` — `WAIT_KEY_WORD` / `WAIT_TAG_WORD` /
`WAIT_PAYLOAD_WORD`, `WAIT_HANDLE_SLOTS`, `WAIT_REQUEST_SLOT`,
`WAIT_MSG_SLOT_0`, and the three byte functions — and read once, in
`sos.pipe`'s decode.

**THE REQUEST SLOT CARRIES NO KIND BYTE, ON PURPOSE.** A wait record's
request slot is a `PipeRequest` or it is empty, so a non-zero word is the
whole of what a decode has to ask; the kind bytes describe the four
slots where the kind genuinely varies. Five slots, four kind bytes, one
length byte — which is exactly the five that fit the padding.

### 2. The vocabulary move

`PipeMsg`, `WaitPayload`, `WaitResult`, `decode_wait` and `Waiter.wait`
now live in `sos.pipe`, reached through `extension Waiter` (the tree's
own precedent). `ReplyDelivery` did not move — it DISSOLVED: a reply
delivery is a `PipeMsg` in the same optional field a delivery-as-take
fills, so the two producers of a message-shaped record answer with one
type. `sosabi` keeps the wire enums, the record layouts and the offsets;
`PIPE_MSG_HANDLES` landed there beside `PIPE_BODY_BYTES` — design 13
deviation 1 verbatim, since the two numbers the halves must AGREE on
live where both can see them, while the ring's DEPTH stays a slab
dimension in `kcore.limits`.

`kernel/sysapi/src/waiter.saw` keeps a comment block where the decode
used to be, naming where it went and why, so a reader who knows the old
shape is not left searching.

### 3. `PipeHandle` — the six cases, and the follow-up

```saw
public enum PipeHandle {
    case Memory(m: Memory),   case IoMemory(io: IoMemory),
    case Inlet(i: PipeInlet), case Outlet(o: PipeOutlet),
    case Reply(r: PipeReply), case Request(rq: PipeRequest),
}
```

Enforced kernel-side by `msg_kind_of`, which is `boot_kind_of` minus
`Process` and `System`, written as a `match` with named arms so a future
givable kind fails to compile until somebody says which side of the line
it is on.

**NAMED FOLLOW-UP, NOT BUILT: `Process` and `System` in messages.** The
obstacle is module ORDER, not doctrine. `sos.pipe` sits below
`sos.system` and `sos.process` in the package's dependency order (both
of those name pipe types in their `give` and factory surfaces), so a
`PipeHandle` case holding a `Process` wrapper is a cycle — DF-232e's
shape. The path I scouted and did not take: split the wrapper STRUCTS
into a leaf module both layers import, leaving the METHODS where they
are, which Saw's extensions make mechanical since a type's declaration
and its methods need not share a file. It is a sysapi refactor with a
real review surface — it moves every handle wrapper in the package — and
it buys one thing: a launcher able to hand a child a Process or System
handle AFTER `start`. Filed for the unit that wants it; the kernel side
is one line in `msg_kind_of` when it comes.

### 4. The ledger, and the refusal arm (design mine — argued)

**THE LEDGER HALF OF THIS SECTION WAS SUPERSEDED ON THE DAY IT SHIPPED** — see
§13, the Sep-2 re-rule to TAKE-AT-POST. It is kept verbatim because the re-rule
is stated as a delta against it and because the REFUSAL ARM below survived the
change unaltered.

Ruling 5 made mechanical: `PIPE_MSG_WORDS` (four words per exchange)
beside `PIPE_MSG_SENDER` (process + 1, so zero means "nothing staged").
The words are the SENDER's handle words — notes, not references — and
`stage_handles` writes them only after `check_send` has resolved every
one, checked `Transfer`, and checked `msg_kind_of`. A refused send
therefore stages nothing, and the validation happens before the ring
slot is touched at all.

At the rendezvous `move_staged` walks the row and calls `move_entry` per
live slot:

```saw
public(package) func move_entry(from: Int, to: Int, word: UInt,
                                entry: HandleEntry, keep: UInt32) unsafe -> UInt {
    let given = mint_handle(to, entry.obj_type, entry.rights & keep, entry.target)
    if given == NO_HANDLE { return NO_HANDLE }
    ref_object(entry.obj_type, entry.target)
    unbind_handle(from, handle_word_index(word))
    unref_object(entry.obj_type, entry.target)
    ...
}
```

**REF BEFORE UNBIND IS THE WHOLE OF THE SAFETY.** The count goes up
before it comes down, so moving the LAST handle onto an object cannot
transiently reach zero and free the slot the receiver is about to be
handed. `move_entry` is also what `Give`'s keep mask uses — one
function, two doors — which is why agenda 7b rode this unit rather than
standing on its own.

`live_staged(x)` counts the slots whose word still resolves (the
generation check is what turns a released staged entry into an EMPTY
slot rather than a dangling one), and every delivery pre-flights
`table_room(p, n)` AND `quota_room_for(p, QuotaKind.Handle, n)` for the
whole batch before anything moves.

**THE REFUSAL ARM IS THE PIECE THE BRIEF LEFT TO ME, AND HERE IS THE
ARGUMENT.** Ruling 11(d) says the stated arm exists; it does not say
what shape. Three were available:

1. **Fault the waiter.** Rejected: a full handle table is a RESOURCE
   condition, and §5.7's faults ruling is for things the caller could
   have checked. A server cannot know how full its table will be when a
   message it did not ask for arrives.
2. **Drop the delivery and leave the thread parked.** Rejected outright
   by §2.2's "nothing parked can be silently doomed": the level is still
   raised, so the thread would re-park on a readiness it can never
   consume — a livelock the kernel built.
3. **Wake with a status and no record.** Taken.

So `deliver_attachment` returns a `SosStatus` (it returned nothing
before), and on `NoResource` / `QuotaExceeded` NOTHING is copied,
consumed or detached: the message stays staged, the readable level stays
raised, the attachment survives, and the parked thread is woken with the
status in its answer register and a record it must not read. **THAT IS
EXACTLY REVOCATION'S SHAPE** — design 12's `Revoked` wake writes no
record for the same reason (nothing became ready, so there is no key,
tag or payload) — which is why it needed no new protocol anywhere: one
wake path already knew how to answer a parked thread with a status and
nothing else. The server frees a slot and waits again; the message is
still there. `tests/pipe-table-full` probes three table states, and the
middle one — ONE free slot, still refused — is the row that separates an
atomic refusal from a partial delivery.

Both wait doors return `op_status(deliver_attachment(...))`, so
`WaiterOp.Wait` and `PipeRequestOp.ReplyWait` refuse identically: the
singular wake protocol unit 3.5 established, kept.

### 5. Delivery-as-take

`WaitTag.Message = 8`, a tag of its own rather than a `Readable` with a
populated handle slot. The reason is a decode one: telling the two apart
by inspecting a handle word would be READING THE PAYLOAD TO FIND OUT
WHAT THE PAYLOAD IS. `Readable` still means "there is something", which
is what a BORROWING attach delivers; `Message` means "here it is".

The fifth matrix question (the ack-free drain) now reads the ATTACHMENT
and not only the kind — the first time it has had to — because a
readable outlet spends nothing under `add` and spends the message under
`give`. One funnel still does the draining.

**MEASURED, VERBATIM FROM `tests/pipe-cq`'s TRANSCRIPT:**

```
SOS cq: served=8 last request b=7,80,73,78
SOS cq: server traps=10 for 8 messages
SOS cq: per RPC client=1 server=1
SOS cq: the delivery was the take
```

against `tests/pipe-pingpong`'s 18 for the same eight round trips with
the SAME CLIENT PROGRAM — `child-pingpong`, unchanged — which is what
makes the comparison exact rather than analogous. The ladder is
complete: manual 3 → fused 2 → completion queue **1**. The two bracket
traps are the opening `wait` (the degenerate first turn: `reply_wait`
needs a request to consume) and the closing `stats()`.

The server holds ZERO endpoint handles: it GIVES the outlet to its
Waiter, so there is no handle left to call `take` through even if the
loop wanted one.

### 6. The keep mask

`ProcessOp.Give` reads it from arg2; `SystemOp.ProcessSelf` reads it
from arg0 and intersects with `process_rights()`. Sysapi: all seven
`give(...)` overloads and `process_self` gained
`keep: UInt32 = ALL_RIGHTS`, so not one call site moved. Fail-closed in
both places — a bit the source lacks is IGNORED rather than an error,
proved in `give-keep-mask` — because a request for authority that does
not exist is answered by not granting it, and erroring would turn a mask
written before a kind gained a right into a hard failure instead of a
narrower handle.

`ProcessSelf`'s mask closes spec §9's recorded finding (the two rights a
driver spends on itself). Note what it does NOT do: it gives the DERIVER
the verb, not the launcher reach. A launcher still cannot narrow the
Process handle its child derives — that would want a per-process policy
the kernel has no place for — and the finding closes by the child being
able to drop its OWN authority, which is the privilege-drop shape a
capability system should have.

### 7. Deviations from §API, argued

1. **`Post` / `Call` / `Reply` / `ReplyWait` take an ARGUMENT RECORD,
   not four handle-word arguments.** §API said "grow 4 handle-word
   args"; a body address, a length and four handle words is six things
   and an op has three argument registers after the handle and the op
   number. The record goes through the copy-in funnel the fused ops
   already use, so the convention did not move a millimetre.
   `PipeReplyWaitRecord` NESTS `PipeSendRecord` — the fused op's
   arguments are the composed ones, one layout read at two arities.
2. **`Take` and `Resolve` answer through a record too.** The same
   arithmetic on the way out, and it makes `PipeMsgRecord` one type both
   directions share.
3. **`post_with` / `send_with` / `reply_with` / `reply_wait_with` are
   NAMED rather than overloads of `post` / `reply` / …** — forced by
   SL-15 (a bare integer literal does not adopt `UInt` when the method
   is overloaded). Adding a handle-carrying overload broke 38
   `len: <literal>` call sites across the suite that had compiled for
   three units. The names are honest, the cost is recorded at the
   definition, and collapsing them is a mechanical sweep when SL-15
   closes.
4. **`WaitResult` holds `message` and `request` as optional FIELDS, not
   as enum payloads** — forced by SL-17 (below): `move` out of a
   place-match arm double-drops, so a caller spells
   `record.message.take()` and matches the owned local.
   `WaitPayload.Message` says both fields are populated, which reads
   well enough, and it is the shape a `NoCopy` payload can be got out of
   today.
5. **`PipeHandles` (parallel kind and word arrays) is the SEND-side
   carrier**, rather than an array of `PipeHandle`. Match-arm bindings
   are never mutable in Saw — probed, including under `&var self` — so a
   sender cannot destructure an enum of wrappers to reach the words. The
   send side wants words and kinds, the receive side wants wrappers; two
   types is the honest answer, and `PipeHandles.put(0, request: move rq)`
   reads fine.

### 8. What the compiler enumerated

The exhaustive-match discipline paid out four times, and each time the
compiler wrote the work list rather than a reviewer:

- adding `WaitTag.Message` failed every userspace `match` on a wait
  answer (six test packages);
- `Attachment.owns` failed all five `Attachment(...)` literal sites
  across `waitables` / `refs` / `sched` / `dispatch`;
- `deliver_attachment` changing to `-> SosStatus` failed both wait
  doors, which is exactly the two places that had to learn the refusal;
- `msg_kind_of`'s `match` over `BootHandleKind` is what will fail when a
  future kind becomes givable.

### 9. Findings

1. **`Process` / `System` in messages is a module-split ruling** (§3
   above) — the largest thing this unit deliberately did not build.
2. **A WRAPPED BINARY EXPRESSION IS TWO STATEMENTS IN SAW, and it can be
   silently wrong rather than an error.** `wait_meta_end_bytes()`
   written as `sizeof<WaitBuffer>() - PIPE_BODY_BYTES` then a newline
   then `- WAIT_HANDLE_SLOTS * sizeof<UInt>()` parses as a DISCARDED
   subtraction followed by a NEGATED tail; the symptom was
   `panic at refs.saw:1259: cast to UInt out of range: -20` at boot.
   Parenthesised, with the hazard recorded at the site. NOT filed as an
   SL-N: it is LANGUAGE_SPEC's stated statement rule working as
   designed, and the deficiency — if there is one — is that a discarded
   arithmetic expression draws no warning, which is a lint question for
   sawlang rather than a defect this tree met.
3. **Design 14 finding 3's stack ceiling bit three more times**, all on
   arm64: `pipe_delegate` (a PRE-EXISTING case, which grew a
   message-sized value when the take's answer changed shape),
   `pipe_table_full` and `pipe_delegate_msg`. One phase per function is
   the standing answer; `pipe-table-full` additionally traded a
   16-frame recursion for a 16-slot array. Root's 16 KiB grant is
   getting tight for cases that hold two message-sized values and format
   numbers, and unit 5 should expect to pay it.
4. ~~**A staged sender's handle word is STALE after the rendezvous**~~ —
   **STALE AFTER THE POST**, under the re-rule (§13), which is the same
   finding with a wider window: the entry is unbound in the sender's own
   syscall now, so the word is a diagnosed `BadHandle` from the moment
   the post returns. The C altitude feels it where the typed one does
   not, the wrapper there having been consumed by the send.
5. ~~**A reply carrying a handle needs the client PARKED, not merely
   alive**~~ — **AND THIS FINDING IS WHAT GOT RULING 5 RE-RULED.** Under
   sender-keeps it was true and it was the sharpest thing this unit
   learned: a fire-and-forget send of a capability RACED THE SENDER'S OWN
   EXIT, so `pipe-handles`' first spelling of the reply-carries-a-handle
   proof failed for a right reason and had to move to
   `pipe-delegate-msg`, where root is parked on the claim and the
   delivery runs inside the child's own `reply_with` syscall. The lead
   review took that as the argument against the ruling rather than as a
   property to document; §13 is the re-rule, and the finding inverts into
   a GUARANTEE — an accepted message delivers what it carried whatever
   becomes of the sender — with `pipe-send-exit` as its case.

### 10. The riders

- The facade's floor re-export gained `sos_pipe_inlet_call` and
  `sos_pipe_request_reply_wait` (unit 3.5's omission), plus the five new
  `sos.pipe` types and `PIPE_MSG_HANDLES`.
- The `var`→`let` tidy ran at the `consumes` call sites in the files
  this unit touched: a `move` retires the binding, so a local that feeds
  exactly one does not need to be mutable.
- `MAX_PROCESSES` stays 2, and `pipe-delegate-msg`'s case header says in
  as many words why a two-pipe round trip proves the mechanism the
  three-process spelling would.

### 11. Harness

Six new cases, appended so no existing index moved: `pipe_send_exit`,
`pipe_table_full`, `pipe_handles`, `pipe_delegate_msg`, `pipe_cq`,
`give_keep_mask`. Three new child packages (`child-handles`,
`child-narrow`, `child-sender`); `pipe-cq` reuses `child-pingpong`
UNCHANGED. The first case shipped as `pipe_stale_attach`, a C-altitude
proof that a released staged entry arrived empty; the re-rule made that
state unreachable and §13 records what replaced it.

**105 → 111 cases per architecture, 210 → 222 total.**

### 12. Transcript accounting (FIRST PARK — superseded by §13's re-gate)

Baseline captured at `7362a3f` before any edit; gated at `98e503e`. Every
row of the runner's transcript falls in one of five buckets, and no row
falls outside them.

| bucket | riscv32 | arm64 | argued |
|---|---|---|---|
| new image rows | 8 | 8 | the eight new packages |
| image rows GROWN | 49 | 48 | the pipe/wait surface — below |
| image rows SHRUNK | 3 | 0 | the send path's marshalling — below |
| image rows UNCHANGED | 47 | 51 | every package that names none of it |
| new case rows | 6 | 6 | the six new cases, appended |

Plus one summary row: `210 passed` → `222 passed`. No case row was
removed, no case row carries a mark other than `✓`, and the shared case
rows appear in an IDENTICAL ORDER — the six new cases are appended, so
not one existing index moved.

**THE GROWN ROWS HAVE ONE CAUSE AND IT IS EXACT.** Cross-tabulated
mechanically: all 100 moved rows belong to packages that name the
pipe/wait surface, and every package that names none of it is unchanged
in both columns. The five arm64 rows that name it and did NOT move are
the ones whose riscv32 twins moved by less than 4 KiB — arm64 images are
page-rounded, which is design 17's own finding about this transcript.

The cause is the TYPED MESSAGE VALUE, not the wire record. The record
grew 24 B / 40 B; `PipeMsg` grew from `{len, [UInt8; 128]}` to
`{len, [PipeHandle?; 4], [UInt8; 128]}`, and a `PipeHandle` is an enum
of six `NoCopy` wrappers — so every `take`, `resolve` and `wait` caller
now moves a bigger value and links the drop glue for four optional
six-way enums. Totals over the 99 shared rows: riscv32
1,717,380 → 2,176,068 B (**+26.7%**), arm64
2,051,032 → 2,513,880 B (**+22.6%**).

**THE SHRUNK ROWS ARE THE SEND PATH, AND THEY SHRANK FOR A REASON.**
`pipe-no-post` −128, `pipe-oversized` −96, `child-post` −80, all
riscv32, all pure posters: a post's arguments now travel as ONE record
the caller fills in, so the call site stopped marshalling three
registers. `child-reply` moved the other way by +48 for the mirror
reason on the reply side. Four rows, all under 128 bytes, all at the
argument seam the unit changed on purpose.

**AND THAT IS A FINDING, RECORDED, WITH A NAMED MITIGATION.** The cost
lands hardest where it is least deserved: `tests/event-wake` waits on an
Event, touches no pipe at all, and grew 5,584 B / 8,192 B — because
`decode_wait` (4,934 B on riscv32) plus `wait_message` (962 B) plus
`decode_handle` (568 B) link into every image that calls `wait`, whether
or not any of its attachments could produce a message. The lever is
LAZY DECODE: keep the kind bytes and the handle WORDS in `PipeMsg` and
construct a `PipeHandle` only when the receiver asks for a slot
(`msg.take_handle(0)`), which moves the six-way construction out of
`decode_wait` and into a function only a handle-reading program links.
It is a sysapi-shaped change with no kernel or ABI consequence, and it
is filed in the tracker's BACKLOG rather than built here, because it
would rewrite the receiving vocabulary this unit's §API was reviewed on.

---

## §13 — THE Sep-2 RE-RULE: TAKE-AT-POST (user, at lead review)

**RULING 5 FLIPPED, AND THE UNIT WAS REWORKED ON THE BRANCH BEFORE MERGE.** A
staged handle no longer stays the sender's until the rendezvous: it LEAVES AT
THE POST. Everything above stands except where this section says otherwise;
`designs/010` carries the ruling itself (the lead's, not mine — I did not touch
that file).

### 13.1 Why it flipped, in the lead's terms

Three arguments, each of which post-dates the Aug-30 ruling:

1. **Ruling 10's ORPHAN ACCOUNTING** (landed by design 14 D-3) made "charged to
   nobody, bounded, and reaped when nobody names it" an established shape in
   this kernel. Sender-keeps existed partly because there was no vocabulary for
   a thing in between two owners; there is one now.
2. **Design 260's `consumes`** (sawc 0.4.0, this unit's own dependency) made the
   typed tier say the opposite of what the kernel did. `post_with` CONSUMES the
   wrapper — the sender's binding is dead at the call — while sender-keeps left
   the ENTRY bound and re-validated it later. Two stories about one act.
3. **My own finding 5.** A fire-and-forget send of a capability raced the
   sender's exit, which is a footnote if you write it down and a trap if you do
   not. The lead read it as the argument rather than as the documentation.

### 13.2 What changed, mechanically

- **THE RING NOTE IS TYPED REFS, NOT WORDS.** `PIPE_MSG_WORDS` (handle words) +
  `PIPE_MSG_SENDER` (a process index) became `PIPE_MSG_HELD`, one `StagedRef`
  per slot: `{obj_type, rights, target}`. **THE RIGHTS HAD TO COME WITH IT** and
  that is worth saying, because the ruling names "obj_type + target": a
  reference names an OBJECT, but a capability is an object AND a rights word,
  and the receiver is owed the sender's rights verbatim. Carrying only the
  object would make the taker's mint consult a default set, which is the one
  thing a transfer must not do.
- **`stage_handles` → `hold_staged`**: per slot, `ref_object` → `unbind_handle`
  → `unref_object`, which is `move_entry`'s body minus the mint. The pair of
  arithmetic is deliberate: keeping "every bind refs, every unbind unrefs" a
  rule with no exceptions is worth more than the two instructions, and it means
  no object transiently reaches zero.
- **`live_staged` → `staged_count`**: a scan of four tags rather than four
  table lookups. **The stale-revalidation path is DELETED** — staleness cannot
  exist — and `None` in a received slot now means one thing only: the sender
  left it empty.
- **`move_staged` → `mint_staged`**: collect the batch, mint each ref into the
  taker (rights verbatim), release each held ref. Same up-before-down ordering
  at this half too.
- **`release_staged` is new**, and it is the price of the guarantee.
- **`exchange_settle` ANSWERS WITH THE BATCH.** It cannot release: releasing
  lives in `kcore.refs` and settling lives in `kcore.objects`, one module below.
  So the ledger is emptied there and the batch travels UP to be released at the
  call site — which is COLLECT-THEN-FREE spelled across a module boundary, and
  the module order enforcing exactly the discipline this needed. Three callers,
  each named.
- **`pipe_drop_staged` MOVED to `kcore.refs`** for the same reason: the outlet's
  zero-arm is a release site now.

### 13.3 The unwind arms, and the reentrancy argument

Three arms, and the third is the one that must NOT release:

| arm | what it does |
|---|---|
| outlet zero (`pipe_drop_staged`) | settles each staged exchange and RELEASES its held refs — nobody can ever take |
| `exchange_settle`, anywhere | collects the batch and hands it up; all three callers release |
| **inlet zero** | **nothing.** Close-drains-first (§2.1; design 230's rule one level down) says a staged message is still owed to a live outlet, so its handles stay held and arrive at the take exactly as they would have |

**REENTRANCY, ARGUED RATHER THAN CHECKED.** A release can take an object to zero
and that object can be a pipe endpoint or a one-shot pair, so a release out of
an unwind arm can re-enter `pipe_drop_staged` and `exchange_settle` on ANOTHER
exchange. Two things make that safe. The batch is COLLECTED before any release
runs, so the ledger a re-entry reads is already consistent and
`exchange_can_settle` answers `false` for the `Free` slot the outer call left
behind. And the recursion CANNOT CYCLE: a reference held by exchange x's message
keeps y's column non-zero, so y cannot be the one settling while x holds it —
the same forest argument design 7's header makes about the Waiter cascade, one
kind further along. Depth is bounded by `PIPE_EXCHANGES`, a fixed slab
dimension.

### 13.4 The quota, restated

The unbind credits the sender and the mint charges the taker, so a staged handle
is charged to NOBODY — D-4's write-off, at a second kind. The bound is stated at
the ledger's declaration: at most `PIPE_INFLIGHT * PIPE_MSG_HANDLES` uncharged
rows per connection, the connection itself cost a `QuotaKind.Pipe` row, and
every object a held ref names already occupies the slab slot it was charged for.
Quota <= wall holds; the SLAB is still what refuses.

### 13.5 The refusal arm survived — with ONE new consequence

Everything §4 says about the atomic refusal is unchanged: nothing minted, the
message left staged, the level left raised, the waiter woken with a status and
no record. What take-at-post adds is that **ONE refusal is now TERMINAL**, and
the asymmetry is worth stating because it is a real cost:

- a refused `Take` and a refused delivery-as-take leave the message STAGED, so
  freeing a slot and asking again is handed the same message, handles and all;
- a refused FUSED-CALL REPLY destroys them. The claim there is a PARKED THREAD,
  which cannot ask again without returning and returning is the thing a fused op
  does not do — so the reply's handles go back to their slabs when the exchange
  settles, because the replier gave them up at the reply and there is nobody
  left to hand them to.

Under sender-keeps the replier simply kept them. This is documented at the site
(`wake_call_reply`); a client that must not lose a capability that way makes
room before it calls, or uses the composed path, whose claim is a handle.

### 13.6 A finding the rework turned up: the room-park re-read

`PipeInletOp.Call` that meets a full ring parks with its argument record still
in the CALLER's memory and re-reads it at the resume. A sibling thread may have
rewritten that record since `check_send` blessed it — so the resume cannot
trust it, and under the old code it would have reached `msg_kind_of` with an
arbitrary handle word and `fatal_kernel`'d on a kind it could not spell. That
was a latent kernel-panic-from-userspace in my own first cut.

The fix is in `hold_staged`, which now asks the three questions itself —
resolves the word, checks `Transfer`, checks `msg_kind_of` — and simply leaves a
slot empty when one fails. It is not a duplicated check: `check_send` decides
whether to END THE CALLER (and can, because the caller is running), while this
decides what a message is CARRYING (and cannot fault, because at a resume the
caller is blocked). It is the ONE place a slot can arrive empty for a reason the
sender did not choose, and it needs a C-altitude race to reach.

### 13.7 The tests

- **`pipe-stale-attach` is GONE**, and it had to be: the state it proved —
  a released staged entry arriving as an empty slot — is now unreachable.
- **`pipe-send-exit` replaces it**, and it is the guarantee as a transcript.
  `child-sender` posts a message carrying a live connection END and EXITS at
  once, never waiting for anybody to take; root parks on the DEATH (§8's Process
  waitable, so the take provably happens after the sender's table is closed and
  its slot is `Gone`), takes, posts through what arrived, and reads its own byte
  back off the far end. Every capability root spends there belongs to a process
  that no longer exists.
  **THE SENDER'S TEARDOWN COUNT IS THE WRITE-OFF HALF** and is asserted: TWO
  handles closed — the boot System and the Process it derived. Under
  sender-keeps it would have read THREE, and the entry that teardown closed
  would have been the very one root was about to be handed.
- **`pipe-handles`' ledger rows INVERT, which is the sharpest single artifact of
  the re-rule.** The case measures root's free table slots at three moments:
  - before → after post: **+1 now** (was unchanged) — the post unbound the entry;
  - after post → after take: **unchanged now** (was +1) — the mint was the
    CHILD's, in a table root cannot see.
  Its assertion and its printed line moved with it: "the post moved the entry
  and the take was the child's". No other design produces that sequence.
- `pipe-delegate-msg`'s "the replier was alive when the rendezvous ran" note
  becomes "and it does not matter whether it was".

### 13.8 The SECOND user ruling of the day: `WaitPayload`'s owning payloads

The §API shape the review gate blessed is RESTORED — no pin bump needed, because
the thing SL-17 makes unsound is the INVALID construction and the licensed one
was available all along.

```saw
public enum ReplyDelivery { case Data(msg: PipeMsg), case PeerClosed }

public enum WaitPayload {
    …
    case Reply(outcome: ReplyDelivery),
    case Message(msg: PipeMsg, request: PipeRequest),
}

public struct WaitResult { public key: UInt, public what: WaitPayload }

extension WaitResult {
    public func open(&var self) consumes -> (UInt, WaitPayload) {
        (move self.key, move self.what)
    }
}
```

**THE TYPE PROVES WHAT THE DOCSTRING USED TO PROMISE**: a `Message` delivery
carries a message AND the obligation to answer it, so a server cannot reach one
without the other and cannot reach either on an arm where neither exists. The
`message`/`request` optional fields are gone.

**THE PRIMARY CONSTRUCTION WAS PROBED FIRST AND WORKS.** A `consumes` accessor
returning a TUPLE of moved-out fields compiles under sawc 0.4.0, and a match on
the owned payload drops each wrapper exactly once (drop-counter probe: two
`NoCopy` payloads, two drops). The ruled FALLBACK — optionals inside the case,
extracted with `Optional.take()` — was therefore not needed, and the type
invariant is kept rather than traded away.

**AND THE PROBE FOUND A SECOND FACE OF SL-17, which changed the sweep.** A
place-match arm binding may be BORROWED onward — passed as a `&` argument, or
used as a `&self` receiver — and neither materializes it. But making it the
scrutinee of a SECOND match DOES: the inner binding is dropped at the arm's end
while the outer place still owns it, so a purely READ-ONLY nested place-match
double-drops. It is not a `move` problem at all. I hit it live: the first sweep
of `reply_len`/`reply_byte` used a nested `match r.what { case Reply(outcome) ->
match outcome { case Data(msg) -> msg.len … } }`, and `pipe-delegate-msg` faulted
with `bad handle` — the intermediate binding's drop had released the outlet the
reply was carrying. The drop-counter probe confirms it: one peek, one spurious
drop.

The answer is `ReplyDelivery.len()` / `.byte(i)` / `.arrived()` — `&self`
accessors, single-level match inside — so a caller reads through ONE level and
borrows onward instead of nesting. That is better API anyway, and it is written
at the accessors as a soundness note rather than an ergonomic one.

**THE SWEEP'S DISCIPLINE, stated once**: match through the place to LOOK, open
the record to SPEND. `pipe-cq`'s serve loop is the worked example — a borrowing
`case Message(_, _)` decides whether to go on, and the arm that goes on writes
`let (_, what) = (move wake).open()` and matches the owned payload, moving both
wrappers out of one arm.

### 13.9 Re-gate, and the accounting

**222/222 on both architectures**, gated at `8b1d43b` against the SAME baseline
`7362a3f` §12 used. The buckets are unchanged in shape and the delta chain is
worth reading in two steps.

**AGAINST THE BASELINE**, the picture is §12's with two rows renamed:

| bucket | riscv32 | arm64 |
|---|---|---|
| new image rows | 9 | 9 |
| image rows GROWN | 49 | 48 |
| image rows SHRUNK | 3 | 0 |
| image rows UNCHANGED | 47 | 51 |
| new case rows | 6 | 6 |

Nine new packages rather than eight — `pipe-stale-attach` left and
`pipe-send-exit` + `child-sender` arrived. The case count is the same six, with
`pipe_send_exit` where `pipe_stale_attach` was, appended in the same position so
no existing index moved; no case row was removed, none carries a mark other than
`✓`, and the shared rows are in an IDENTICAL ORDER. Summary `210` → `222`.
Totals: riscv32 1,717,380 → 2,192,132 B (+27.6%), arm64 2,051,032 → 2,509,784 B
(+22.4%) — the same cause §12 names, cross-tabulated the same way: all 100 moved
rows belong to packages that name the pipe/wait surface, and every package that
names none of it is unchanged.

**AGAINST THE FIRST PARK**, which is what the re-rule and the payload
restoration actually cost, the answer is: almost nothing.

| | riscv32 | arm64 |
|---|---|---|
| shared rows | 106 | 106 |
| moved | 40 | 2 |
| total | +19,528 B (**+0.80%**) | **+0 B** (page rounding absorbs it) |

Every moved row is argued and they fall in four groups:

- **`pipe-delegate-msg` +5,848 / +4,096** — the largest by far, and it is the
  owned-match sweep: the case gained `reply_of`, a nested owned match that
  consumes its scrutinee twice, where it used to read two optional fields.
- **Five SHRANK on riscv32** — `pipe-handles` −1,864, `pipe-cq` −800,
  `pipe-wait-server` −760, `pipe-delegate` −232, `pipe-wait-reply` −96. These
  are the cases that stopped carrying `WaitResult`'s two optional fields, or
  (the first two) whose bodies got simpler under the re-rule.
- **A ~+600 B floor across every waiter-using image** — the decode grew a
  `ReplyDelivery` construction and the three `&self` accessors.
- **`timer-interval` −4,096 on arm64** is the same shrink, quantized: the
  wait record's decoded form lost two optional fields.

That the re-rule itself is nearly free in image terms is expected and worth
stating: it moved WHEN a handle changes hands, not how much code says so.
