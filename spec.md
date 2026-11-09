# SOS — a capability-based microkernel in Saw

SawOS ("SOS" for short) is a capability-based microkernel written in Saw. This
document specifies it: the object model, handles and rights, the ops and what
each answers, the invariants the kernel keeps, and the protocols built on
them. Where something is specified but not built, the text says so; §11
collects what is not built, the known limits, and the plan.

The first hardware target is the ESP32-P4 (riscv32) through Saw's freestanding
profile, and the design must not preclude MMU-class targets. Two profiles run
under QEMU (§5b).

## 1. Requirements

- **Capability-based microkernel.** The kernel implements only what is
  required to efficiently provide core OS resources:
  processes/address spaces, threads/tasks/scheduling, timers,
  interrupts, memory management. Everything else runs in userspace,
  with processes communicating via shared memory and pipes/events.
- **Handles.** Every kernel resource is referenced by a Handle: an
  opaque integer mapping to a kernel object through a per-process
  table lookup (fd-style). No ambient authority.
- **Derivation.** A Handle is ideally obtained via an operation on
  another Handle the process already holds, with the required
  capability enabled — authority flows only through held authority.
- **Pipes** send data synchronously, optionally carrying Handles,
  which are moved to the receiving process. Not every Handle is
  movable — transferability is a capability of the Handle itself.

## 2. Object model

The kernel's object kinds, each a slab-allocated kernel struct:

| Object | Role |
|---|---|
| `AddressSpace` | Isolation domain, defined abstractly. P4: PMP region set + APM/REE security context (see §5.5 — the P4's MMU is real but global and external-memory-only, not per-process). Paging targets: page-table root. There is no AddressSpace object: the domain is the process (see below). |
| `Thread` | Kernel-scheduled execution context bound to an AddressSpace. Saw's cooperative TaskGroups run *inside* a thread, in userspace — the kernel never sees tasks. Ops `Start`/`Join`/`Exit`/`Yield`; rights `ThreadRight.Start`/`.Join`/`.Control`. The saved trap frame is the context, so a switch is the trap handler returning a different frame (§7). Lifecycle: §8. |
| `MemoryObject` | A range of RAM that can be mapped into AddressSpaces; the kind is `Memory`. Ops `Split`/`Map`; rights `MemoryRight.Split`/`.Map`/`.MapExecute`. Roots are minted at boot from the region table. §2.5. |
| `IoMemoryObject` | Physical memory-mapped device registers; the kind is `IoMemory`. A distinct kind rather than a flag on `MemoryObject`, so a driver cannot obtain a cacheable view of a register block by construction. Ops `Carve`/`Map`; rights `IoMemoryRight.Carve`/`.Map`. §2.5. |
| `Mapping` | One installed protection row, with its own handle. Ops `Unmap`/`Base`; rights `MappingRight.Unmap`/`.Base`. Not givable. §2.5. |
| `Pipe` | Synchronous message IPC with request/reply built in. Four kinds: `PipeInlet` and `PipeOutlet` (the client and server ends of a connection) and `PipeReply` and `PipeRequest` (the client's claim on one reply and the server's obligation to send it). §2.1. |
| `Event` | Accumulating non-blocking notification (OR / saturating-sum); a waitable. Ops `Signal`/`Receive`, the mode chosen by the caller at creation (`event_create(mode:)`). The word is consumed by whoever takes it, through either door — `receive` is the non-blocking poll, a `Waiter.wait` delivery is the blocking one, and both read and clear, so a value is reported exactly once. §2.2, §2.4. |
| `Clock` | A granted time source — time is a capability, not an ambient facility. §2.6. |
| `Timer` | Deadline object bound to the Clock that created it; directly waitable. The process-sleep primitive. §2.6. |
| `Interrupt` | Binds an IRQ line to a waitable; userspace drivers wait on it and ack through the handle. One op (`Ack`), two rights (`InterruptRight.Wait`/`.Ack`), created by `SystemOp.InterruptBind` on `SystemRight.InterruptBind`, because a line is a machine-wide resource (§2.8). The binding is the object's existence (creation takes the line, there is no rebind), which stops one handle naming two devices over its life. It has one name, its handle, and releasing that handle destroys it (§2.10). A line the board does not have, the timer's line, and a line already bound are all faults. Not yet enforced: binding is `ProcessOp.InterruptBind` on `ProcessRight.InterruptBind`, and the default set carries `Mint`. §9. |
| `Waiter` | Generic wait aggregator (epoll/Port-style). Ops `Add`/`Remove`/`Wait`; rights `WaiterRight.Attach`/`.Wait`; the wait answer is a copy-out record. §2.2. |
| `Process` | Two entries: a small record (the state, the status word, the quota ledger, the references naming it) that outlives the process, and a body (AddressSpace + handle table + threads) that the teardown frees. Handles name the record. No kernel Job or hierarchy. The kernel guarantees teardown on exit or fault — invalidating the objects bound to it, releasing every handle it holds, clearing its domain and freeing its body (§2.10; the invalidation is not yet enforced for the bound kinds). Supervision (restart, kill-trees, launchd-style) is a userspace concern. §2.7, §8, §12. |
| `System` | Kernel singleton: the object behind system-scoped primitives, so that every syscall is an object op (§5.7). §2.8. |

The kinds are System, Process, Thread, Event, Waiter, Interrupt, Clock, Timer,
Memory, IoMemory, Mapping, PipeInlet, PipeOutlet, PipeReply and PipeRequest
(`ObjType`, `kernel/abi/`, the kernel-internal numbering §5.7's
vDSO discipline keeps renumberable). The `Pipe` row is four kinds rather than
one: a connection is a pair of counted endpoint kinds, and each in-flight
message is a second pair — the client's claim on a reply and the server's
obligation to send it — with "Pipe" the collective name.

**Every kind but Clock and System is counted.** Every countable kind's slab
slot carries the number of references naming it, and reaching zero frees the
slot synchronously, inside the syscall that dropped the last reference. The
column is enumerated per kind, so that a new kind fails to compile until
somebody says what references it (Thread's row is not yet enforced):

| kind | counted references |
|---|---|
| Event | handle entries (any process) + its attachment |
| Waiter | handle entries |
| Interrupt | handle entries + its attachment. Only the one handle keeps it live: releasing it destroys the Interrupt, and an attachment then holds the invalid object until it is removed (§2.10; not yet enforced). A give or a staged message moves that one name and is not a release |
| Timer | handle entries + its attachment |
| MemoryObject / IoMemoryObject | handle entries (a Memory's installed grant rows are a second count, §2.5) |
| Mapping | handle entries — not its row, which the Mapping owns rather than the reverse (§2.5) |
| Process | handle entries + its attachment, on the record. A shared object charged to the process holds no reference on it: the process's death disowns the object (§2.10). A live process is never freed by losing its last handle, and a `Gone` one's record is reclaimed when the count reaches zero (§2.7); its body went back at the teardown, whatever the count. The attachment column is what makes attach-after-death sound against that reclaim: a watched dead record cannot reach zero while somebody is watching it |
| PipeInlet | handle entries + its attachment, on the connection's inlet column. The two endpoint kinds share one `PIPES` row and count on separate columns, which is what makes "every client handle is gone" askable at all; a side reaching zero raises peer-gone on the other, and the slot frees only when both columns are zero. The attachment is per end, not per connection: a client waits for room and a server waits for a message, so §2.2's at-most-one-Waiter rule holds per column exactly as the count does |
| PipeOutlet | handle entries + its attachment — the same row's outlet column, mirrored. The two arms of `free_object` are one act written twice, because reaching it through either kind means both columns are already zero |
| PipeReply | handle entries + its attachment, on one ring slot's claim column. A delivery of this kind drops that attachment's reference — a claim is a one-shot, so its delivery is inherently consuming — which is the one place in the ledger where answering a wait is what frees an object. A one-shot exists for exactly as long as one in-flight message, so the pair is ring-slot state rather than a third slab: the object is an exchange (`connection * PIPE_INFLIGHT + ring slot`) and the claim counts on its reply column. The ring slot goes back when both of the exchange's columns are zero and nothing is owed — a staged message is still owed to a live outlet whatever became of its claim, which is §2.1's TELL idiom |
| PipeRequest | handle entries + its attachment — the same exchange's obligation column, mirrored. Reaching zero on one column is the abandonment transition, read off the counts by `reply` and `resolve` exactly as the connection's peer-gone is; and the connection cannot free while any exchange in it is live, which keeps a claim from being handed a recycled row |
| Thread | handle entries. A thread that has not exited is never freed by losing its last handle, as a live process is not: its slot is what it runs in, and a thread whose count reached zero while it ran is freed when it exits or is invalidated. A thread that exited normally is not invalid: it is `Exited`, its slot keeps the exit code a late `Join` reads through any handle, even after its process is `Gone`, and it is freed when the count reaches zero. Only a thread that has not exited when its process dies is invalidated, so its `Join` answers `Revoked` and its slot keeps nothing for one (§2.10). Not yet enforced: thread slots are not counted, and the join/exit protocol holds a slot until its process's teardown, which frees every thread slot of the process whatever names it |
| Clock | exempt — kernel-eternal, owned by nobody (§2.6). Freeing a domain's slot on a release would take the machine's counter from everybody else |
| System | no slab: the singleton every System handle names |

**A staged message is a second kind of holder.** A handle riding in a staged
message is not a handle entry: the entry was unbound at the post and the
reference it held was parked in the ring slot, so what counts on the object's
column is the kernel rather than a table (§2.1). No column above is affected and
no kind has to know about messages; handle entries are simply not the only thing
that can hold a reference. That makes the delivery guarantee mechanical: an
object a message is carrying cannot reach zero while the message exists, so it
cannot be freed by its sender's teardown, and it is still there when the
receiver takes. The move is `Give`'s (§3), performed in two halves — unbind at
the post, bind at the take or delivery, rights verbatim, the count raised before
it is lowered at each half — so a transfer changes no count and a message in
flight changes none either. The price is the unwind: an arm that drops a staged
message must release what it was holding, which is the outlet's zero-arm and
every `exchange_settle`. The inlet's zero-arm must not, because
close-drains-first says the message is still owed to a live outlet.

**Last rites.** Three kinds hold something outside their slab and act on it when
they are destroyed, whether by a free or by an invalidation (§2.10): an
Interrupt masks its line (and is then bindable again, mid-life), a Timer
reprograms the comparator, and a Waiter detaches its list — which may take a
waitable to zero in turn, a cascade that terminates because an attachment
references the waitable and never the Waiter that holds it. A Waiter's
destruction also wakes its parked threads (§2.2). Not yet enforced: only a free
runs them, since neither invalidation of the bound kinds nor an Interrupt's
destruction by the release of its handle while attached is built. Delivery and
the ledger are mutually recursive — a delivery can drop a counted reference (a
one-shot attachment detaches at its copy-out) and a dropped reference can
deliver (a column reaching zero raises a peer-gone level) — so both live in one
module, `kcore.refs`.

**AddressSpace is implicit, deliberately.** Each process gets one granted range
plus a writable window, and its protection rows are recorded in its process body
and installed when the scheduler switches it in (§7). That record is what an
AddressSpace object would own if one existed, and a `Mapping` is a handle onto
one row of it. `map` names a target process, because §5b says the domain is the
process on every profile, so an AddressSpace object would be a second name for a
thing every op already has. Nothing above it would change if it existed.

### 2.1 Pipes: bounded messages and built-in request/reply

**The model.**

- **`send(data?, handles?) -> PipeReplyHandle`.** Body bytes are copied
  from the sender's address space; handles travel with the message and are
  moved into the receiver's table (the transfer rule is below). Both are
  optional: data-only, handles-only, or both.
- **Bounded by design, kept simple on purpose:** a fixed maximum body size
  and a fixed maximum handle count per message, so the kernel stages messages
  in fixed slots with zero dynamic kernel allocation. Bulk data goes through
  shared memory (§2.5); the small message carries the MemoryObject handle.
- **Request/reply is a primitive, not a convention.** `send` returns a
  **PipeReplyHandle** — a single-use, one-shot "reply pipe" the sender
  waits on. `receive(...) -> (message, PipeRequestHandle)` returns a
  matching one-shot **PipeRequestHandle** the server replies through
  (`request.reply(data?, handles?)`). Properties:
  - The reply capability is unforgeable and **consumed on reply**
    (NoCopy in Saw — the type system enforces single use on top of the
    kernel's own check).
  - If the server drops its PipeRequestHandle without replying, the
    client's wait wakes with a **peer-closed error** — no hung
    clients, no timeout hacks. (seL4 call/reply + Zircon channel-call,
    fused.)
  - PipeReplyHandle/PipeRequestHandle **are transferable** — this is the
    delegation primitive, not a hazard. A server may move its
    PipeRequestHandle to a third party who replies directly to the
    original client. **Canonical example — zero-copy delegation:** the
    filesystem receives a read (a PipeRequestHandle) and forwards that
    handle over a pipe to the flash driver; the flash driver replies
    through it with the hardware bytes straight to the client — the
    payload never transits the fs address space. Single use is
    preserved through transfer: the handle is consumed on reply
    regardless of who holds it (transfer moves the reply *obligation*).
  - **Payload-free fire-and-forget is not a pipe concern** — use an Event
    (§2.4), the accumulating non-blocking notification object. The TELL
    idiom below is the payload-carrying one-way message.

**The name.** The object is a **Pipe**. Saw has its own `Channel` with different
semantics (any-holder close and fire-and-forget sends, where a Pipe is a bounded
request/reply connection whose ends close separately), and the planned Plan
9-style namespace resolves paths like `/dev/uart0` to a server-owned object
speaking a protocol, which is the named-pipe model nearly verbatim
(path-registered, message-framed, transactional). `Pipe` names the per-client
connection; the path-attached acceptor the namespace will need is a separate,
later object, named in the namespace design and not built. Handle types follow
the abi's object-kind convention: `PipeInletHandle` and `PipeOutletHandle` for
the two ends, and the auxiliary pair `PipeRequestHandle` / `PipeReplyHandle`
(the prefix also reserves bare Request/Reply for a future protocol layer's own
message types). Rights and ops are per endpoint kind
(`PipeInletRight`/`PipeInletOp` and so on).

**The client API.** Blocking-ness is never a parameter — a runtime flag cannot
change a return type — so the blocking and split-phase forms are different
verbs. `send(msg)` (and `send_with`, carrying handles) suspends until the reply;
there is no `timeout:` overload. The split-phase form (send now, wait later;
returns the bare claim) is `post`, and it is what a client attaches to a Waiter
to multiplex outstanding requests. `send(msg, timeout:)` is the caller's own
composition (below); its contract is that a timeout leaves the still-live claim
in the caller's hands.

**Abandoned requests** (a client drop and a client crash are the same event to
the kernel): `reply()` to an abandoned request answers `Err(PeerClosed)`, never
a silent success — ignorable with `let _ =`, the std-Channel close precedent.
The obligation is discharged either way. The handles attached to such a reply
are not delivered, and they are not left with the replier either: a reply to an
abandoned request releases them (§3), so an object whose last handle one was
is freed. The `PipeRequestHandle` is waitable for "abandoned" (opt-in
early notice; state on the kernel object, so it travels with a delegated
handle). Dropping the last reference to a claim is thereby the cancellation
primitive — time out, then drop the pending claim; no `cancel()` verb, and
deterministic destruction makes the timing deterministic. A handle drop with a
live attachment is not abandonment: the subscription is the interested party,
and a client that gave its claim to one holds no handle at all, so what signals
cancellation is the last reference going — removing the attachment, or the
Waiter dying before delivery. Abandonment is information, not an imperative:
whether it cancels is the protocol's per-request-type decision (a tell-style
message ignores the signal; a cancellable read honors it). The TELL idiom is
sanctioned: send-then-drop carries data an Event cannot, the server discharging
with `let _ = request.reply()`; it is spelled on the split-phase form, since the
suspending `send` would wait for the very reply it is about to discard.

**The connection is a pair of counted endpoint kinds.** `PipeInlet` is the
client end — requests flow in — and `PipeOutlet` is the server end, where they
come out; "Pipe" is the pair's collective name. Roles are objects rather than
rights: a single symmetric object cannot express "the writers went to zero" (a
parked receiver is itself a holder — the socket-`shutdown()` hole), and a
rights-partitioned count would be a second ledger beside the reference count,
so the two kinds share one slab row with two reference columns, and which
column a handle counts on is decided by its kind — which §3's order
establishes before any right is read. The factory is `ProcessOp.PipeCreate` on
`ProcessRight.PipeCreate` — pipes are IPC and carry nothing system-shaped, so
the authority is per-process and attenuable — answering both ends through a
two-word copy-out record, because one op returns one word. Both ends carry
`Transfer`, so a launcher creates the pair and wires it outward with `give`,
and a child drains an end out of its boot set.

**The staging is a ring of fixed slots.** The body maximum is `PIPE_BODY_BYTES`
and the ring is `PIPE_INFLIGHT` slots per connection, both build defines.
`static_assert`s pin `PIPE_BODY_BYTES` to at least 8 (a body holds at least one
64-bit word), a multiple of 8 (a staging slot is doubleword-sized on both
profiles), at most 1024 (a body is copied with interrupts masked, so it stays
bounded), and — the binding bound — at most `UInt8.max`, because a delivery's
body length rides one byte of the packed meta (§2.2). `PIPE_INFLIGHT` is
`2 × MAX_THREADS` — two per thread, so a system in which every thread has a
request in flight and a reply outstanding still cannot fill the ring on the
sender's own account — and is pinned to 1..64, since the ring is inline per
connection. The ring delivers in post order (FIFO). The two defines live in two
places for one reason: the body maximum and `PIPE_MSG_HANDLES` are a published
contract a caller sizes buffers against, so they sit in `kernel/abi/` beside the
record layouts, while the ring depth is nobody's business but the kernel's and
sits in `kcore.limits` with the slab sizes. A ring rather than a single slot is
what lets a client post again before the server has taken the first; zero
dynamic kernel allocation is untouched. Body bytes are copied from the sender
through §2.2's copy-in funnel, and the `data?` optionality is real: a
zero-length body is a legal message, distinguishable at the typed tier from
nothing staged.

**Ops.** `PipeInletOp.Post` (`PipeInletRight.Post`) stages a message and
answers the claim; `PipeOutletOp.Take` (`PipeOutletRight.Take`) answers the
message together with the matching obligation, minted into the taker's table.
A full ring at `Post` and an empty one at `Take` both answer
`SosStatus.WouldBlock`, and `WouldBlock` is an error at the typed tier: `take`
and `post` carry no Optionals, and a genuine poller declares itself with a
`WouldBlock` arm. `PipeReplyOp.Resolve` and `PipeRequestOp.Reply` each spend
their own right (`PipeReplyRight.Resolve`, `PipeRequestRight.Reply`) and each
destroy the caller's handle entry. `PipeInletOp.Call`, `PipeRequestOp.ReplyWait`
and `PipeReplyOp.Ready` are described below.

**Peer-closed is read off the columns** — the peer-gone doctrine (§2.2). The
outlet column reaching zero drops what is staged, and every later `Post`
answers `SosStatus.PeerClosed`. The inlet column reaching zero drains first:
`Take` hands over everything already staged, and only once the inlet column is
zero and the ring is dry does it answer `PeerClosed`. The level is terminal:
nothing un-closes. Accounting is creator-pays: one `QuotaKind.Pipe` row
charged at create, credited when both columns reach zero and the slot goes back
(§2.9, §2.10).

**The one-shot pair is ring-slot state.** A one-shot exists for exactly as
long as one in-flight message, so the claim and the obligation are two more
reference columns on the staging slot that carried the request and now carries
the reply back — no third slab and no new quota row. A ring slot therefore
lives until the exchange settles rather than until its bytes are taken, so
`PIPE_INFLIGHT` bounds awaiting-reply exchanges (in-flight messages per
client, read literally).

**Single use is the ledger and the compiler both.** `Reply` and `Resolve`
destroy the caller's entry, so the kernel's check stands for a raw-altitude
caller; above it `PipeRequest.reply`, `reply_with`, `reply_wait`,
`reply_wait_with` and `PipeReply.resolve` are all `consumes` methods whose
callers spell `(move …)`, so a second use is a compile error before the ledger
is ever asked. Both one-shots are transferable — the delegation primitive — and
the zero-copy example runs end to end.

**A one-shot has one name.** `Mint` is absent from `pipe_reply_rights()` and
`pipe_request_rights()` (the enum bits stay declared, since §3's universal low
byte is pinned by assert in every kind), because a second name on an object one
op spends can only ever be told `PeerClosed`, and it would undermine the single
use the rest of this section makes structural. Delegation is untouched: it is
`Transfer` moving the one name.

**Abandonment is derived, not flagged.** A server that drops its obligation
makes the client's `resolve` answer `SosStatus.PeerClosed`; a client that drops
its claim makes the server's `reply()` answer `Err(PeerClosed)`, and the
obligation is discharged either way. A message still staged when the outlet
column reaches zero is abandoned on the same terms, because its obligation can
never be minted. Cancellation-by-drop and the TELL idiom both fall out of that.

**All four kinds are waitable** (§2.2's list). The outlet is readable while a
message is staged or the client end is gone (drain-then-terminal, so its
`PeerGone` payload waits for a dry ring); the inlet is postable while the ring
has room or the server end is gone — `post` never blocks, so a refused poster
parks here instead of polling, and the payload word says which of the two it was
woken for; the reply is ready once its answer is written or can never be; and
the request is ready once the claim behind it was abandoned, which is this
section's early notice. A server that does not attach still learns that fact at
its own `reply()`.

**A delivered reply is the resolve.** The wait record carries the reply's
bytes out of the ring slot that already holds them, so a client that waits
this way never calls `resolve` at all and a multiplexed RPC is post + attach +
wait. The delivery discharges the exchange — a claim the caller kept answers
`PeerClosed` at a later resolve, because a second copy of an answer must not
exist — and a one-shot kind's delivery detaches whatever mode was passed, since
a subscription to something that has happened for the last time is a promise
nothing can keep.

**`Resolve` parks and consumes on every path.** A pending exchange blocks the
calling thread on the claim's ready level, so the op ends the exchange
whichever way it returns; it is the waiter-free blocking receive, and it is
what lets `PipeReply.resolve` carry `consumes`. The non-consuming poll is
`PipeReplyOp.Ready`, gated on `PipeReplyRight.Wait` because the level question
is the same question attaching asks: `Ok(true)` means the answer is there
(readiness is monotonic, so check-then-act has no race), `Ok(false)` means not
yet and the exchange is live, and the terminal rides the error channel —
`Err(PeerClosed)` means no answer can arrive, at which point the claim is
dropped rather than resolved.

**`send(msg)` is one kernel call.** `PipeInletOp.Call` is post, park and
resolve behind one trap — the composition run without returning to userspace,
so every rule the composed path obeys applies verbatim (same ring, same body
maximum, same peer-gone answer), and it is gated by the existing
`PipeInletRight.Post`, because what it adds is a park and a park is mechanics
rather than authority. It never answers `WouldBlock`: a full ring parks on the
inlet's room-to-post level, reached from inside the op. It takes no duration —
the kernel never learns what a timeout is. The typed spelling is
`PipeInlet.send`.

**`send(msg, timeout:)` is the caller's own composition, and there is
deliberately no library wrapper for it.** The shape is post + attach + wait plus
a Timer on the same Waiter, which `tests/pipe-send-manual` writes out inline. A
wrapper type would freeze a shape the fused call replaces with a single kernel
call, and a kernel-side timeout, if a consumer ever names one, arrives as an op
rather than as a method. `TimedOut(pending:)`'s substance holds without a type
to carry it: the composition uses the non-consuming attach, so the claim is the
caller's the whole time and is still in hand when the Timer's key comes back.

**A fused call mints nothing.** The claim is kernel-internal and tied to the
parked thread — one reference on the exchange's claim column that no handle
table holds — so a blocking client spends no table row and no quota on it, and
`MAX_HANDLES` does not bound a client blocked in calls. `post` keeps minting
the real handle for multiplexers.

**`PipeRequestOp.ReplyWait` is the server's half, and it lives on the
request:** a reply with a built-in wait, the reply being the act that consumes
the receiver and the wait the fused tail. It spends the existing
`PipeRequestRight.Reply`, plus `WaiterRight.Wait` on the waiter it is handed
(holding an obligation must not imply authority over somebody else's Waiter).
It discharges the obligation and then parks on a Waiter exactly as a plain
`wait` parks — never on the outlet directly, so the wake protocol stays
singular — and the waiter is required, because the first turn of a loop and
the dead-client recovery are both the existing `WaiterOp.Wait`, and
reply-without-wait is the existing `Reply`. The answer names its leg: a reply
that does not land returns `Err(Reply(...))` without parking, so one dead
client can never stall a server loop, and `Wait(_)` means the reply landed. The
request is consumed on every path. §2.2 has the record.

**Abandonment reaches a parked caller through one wake arm.** A claim is either
a subscription's or a parked thread's, and exactly one place in the kernel tells
them apart; a parked fused caller and a parked resolver are one shape to it, and
to `notify_claim`, `wake_call_reply` and `end_process`'s orphan arm
(`park_resolve` is `consume_entry` with the unref left out, so the entry's
reference becomes the parked thread's). `end_process` carries the mirror, so a
process dying with a thread parked in a call or a resolve has that claim
abandoned by its teardown, and the server's later `reply()` answers
`Err(PeerClosed)` by the rule above (`tests/pipe-resolve-park`,
`tests/pipe-resolve-orphan`).

**Messages carry handles.** A message carries up to `PIPE_MSG_HANDLES`
handles on `Post`, `Call`, `Reply` and `ReplyWait` alike — `send` is one
sentence, and the answering direction is not a different one — and all four
take the message through one argument record, because a body address, a length
and the handle words do not fit three registers. `Take` and `Resolve` answer
through one copy-out record.

**A send is half of a move — take-at-post.** The handles a message carries
leave the sender at the post: each entry is unbound, the reference it held is
parked in the ring note as a typed ref (object, target, rights), and the
sender's handle-quota row is credited. They are minted into the receiver at
the take or the delivery, where the taker is charged. It is `Give`'s single act
performed in two steps with the kernel holding the reference in between, and
each step keeps `Give`'s ordering — the count goes up before it goes down, per
handle — so no object can transiently reach zero and be freed mid-move. The
`Transfer` bit is checked at the sender exactly as `give` checks it. A reply
cannot carry the request handle it is invoked through (§3).

- **What it buys is a guarantee: a message the kernel accepted delivers what
  it was carrying, whatever becomes of the sender.** A capability handed over
  fire-and-forget does not race its sender's exit (`tests/pipe-send-exit`
  takes the message after the sender is `Gone` and posts through what
  arrived); the typed tier is honest, since `post_with` consumes the wrapper
  and a consumed wrapper must not come back; and `None` in a received slot
  means one thing: the sender left it empty.
- **A taker that cannot accept the batch refuses atomically**: if its table
  or its quota cannot take the handles, nothing is minted, and the message
  stays staged with its references still held and its level raised
  (`tests/pipe-table-full`).
- **The unwind arms are releases**: the outlet's zero-arm and every settle
  hand the held references back, while the inlet's zero-arm deliberately does
  not, because close-drains-first means a staged message is still owed to a
  live outlet. So "no orphaned handles if the send is abandoned" is true: a
  send nobody takes releases exactly what it took, at the arm that discovers
  nobody will.

**A handle arriving in a message is typed at the boundary** — the
`decode_boot_handle` precedent generalized: the record carries a kind byte per
slot beside the word, so a receiver matches a `PipeHandle` out instead of
holding a number. The kinds a message may carry are the givable ones the
receiving vocabulary can name: `Memory`, `IoMemory`, `PipeInlet`, `PipeOutlet`,
`PipeReply`, `PipeRequest`, `Event`, `Thread`, `Waiter`, `Timer` and `Interrupt`
— every kind a boot record can carry but two (`msg_kind_of` is `boot_kind_of`
less those two). Not yet enforced for the last four: their default sets withhold
`Transfer`, no boot record kind names them (§3), and the typed tier has neither
`PipeHandle` cases nor `decode_handle` arms for them, nor the `Process.give`
overloads (`thread:`, `waiter:`, `timer:`, `interrupt:`) or the `BootHandle`
accessors that would carry them through a give. `Process` and
`System` are refused at the sender, as a scope line rather than a rule: both
wrappers live above `sos.pipe` in the module order, so admitting them is a
sysapi module split and a named follow-up (§11).

**The delegation example is a protocol, across three real processes.** A
`PipeRequest` travelling in a message is the zero-copy example's own move made
at run time, about a request that did not exist when either process was
launched (`give` can only wire one before `start`). `tests/pipe-delegate-msg`
is that flow across a process boundary, with the replier holding no end of
the connection it answers on, and `tests/delegate-3p` is the canonical example
with the names filled in: root asks a middle process, the middle forwards the
obligation in a message and exits, and a far process answers it, the bytes
landing at root's original claim. The middle is a different principal from
both ends — its own table, its own quota, its own death — and the answer lands
after that death, which take-at-post makes irrelevant.

**The delivery can be the take.** A server that attaches its outlet consuming
and persistent is handed each message through `wait` itself, with the
obligation minted at the delivery (§2.2). It holds zero endpoint handles for
however many clients it serves, and hanging up is destroying the subscription.

**The trap ladder.** The multiplexed composition is three traps per RPC at the
client (post, attach, wait). The fused `Call` is one. A server is three per
message with take + reply + wait, and `ReplyWait` makes it two — a readable
level says "there is something" and never "here it is", so the `take` stays its
own op. Delivery-as-take makes it one, because the delivery is the take. The
one-per-message floor is for a message the server answers: a TELL costs the
server two, and the second is not overhead — nothing is owed back, so there is
nothing to reply to, and the obligation the delivery minted has to be released,
which is a syscall because a one-way message carries a real capability rather
than a datagram. `reply_wait` on an abandoned claim answers without parking, so
there is no cheaper spelling. Post + resolve is two traps. `ProcessOp.Stats`
counts them (`tests/pipe-pingpong`, `tests/pipe-cq`, `tests/pipe-send-manual`
and `tests/pipe-resolve-park` assert the ladder).

**Driver-as-service needs nothing beyond this section.** A UART driver whose
clients reach the device only through a pipe (§9) uses this object as built,
with no added op, kind, right or status:

- **A blocking read is composed, not built.** A client that wants the next
  byte spends a fused `Call` and parks. The driver, finding no input, holds
  the obligation — a transferable one-shot is state a server may simply keep —
  and answers it when its interrupt line delivers something. The kernel never
  learns what a UART is, exactly as it never learns what a timeout is.
- **Cancellation is the drop, and the server is told by a level it was
  already watching.** A client that gives up composes its own deadline — a
  Waiter with the claim and a Timer on it — and drops the claim when the timer
  wins; the abandoned level fires at the driver, which discards the held
  obligation and serves the next request. There is no `Cancel` op and no
  cancel byte on the wire: a correlation the protocol would have had to carry
  is a reference count the kernel already maintains (`tests/uart-cancel`).
- **A TELL is the one-way write** — post-then-drop at the client, obligation
  discharged by being dropped at the server.
- **Multiple clients cost no new mechanism and no second connection.** Two
  processes are clients of one service over one connection through minted
  sibling inlets — per-client authority as a keep mask rather than as a session
  table — and each request carries its own reply obligation, so a
  completion-queue server routes every answer to the claim that asked for it
  with nothing on the wire to correlate. `tests/uart-service` pins two
  outstanding reads in a fixed order — the ring delivers in post order, so its
  FIFO decides it — and each answer lands at its own claim in its own table.
- **What the namespace still owes is a path and nothing else.** A client finds
  this service by a boot tag its launcher chose; `/dev/uart0` is the same
  connection reached by a name, and the acceptor object that resolves one is
  the separate, later design named above.

**A spelling note.** The tree renders a §2 object name short at its typed tier
— `MemoryObject` is `Memory`, `PipeInlet`'s handle alias is `PipeInletHandle`
— so `sos`'s owning wrappers for the one-shot pair are `PipeReply` and
`PipeRequest`, with `PipeReplyHandle` / `PipeRequestHandle` the `sosabi`
aliases they wrap. The resolved reply value is **`PipeMsg`** — the same struct
a reply delivery carries, which is why there is one type and not two — and
`ReplyDelivery` is the wait record's name for the delivery that carries it.

### 2.2 Waiter: the generic wait aggregator

- epoll/Port-style. `waiter.add(handle, key)` attaches a waitable;
  `waiter.wait(buffer, capacity)` blocks until any attached handle is
  ready and **copies out a record** naming the **word-sized key the
  attacher supplied** (not just a bit — a word can directly encode the
  waiting Saw task's identity, so kernel wake sources dispatch to
  userspace tasks with no side-table lookup — the async-executor
  integration point) plus a per-kind payload.
- **The result is a record, not a register pair.** The record is a `key`
  word, a `tag` word and payload, with the sizes and offsets published as
  constants and the tag a raw-backed enum whose value space is
  extensible. Why: the key is universal and the payload
  is not — a Pipe's readiness is not a Timer's is not an Event's — so a fixed
  register pair would have had to be the union of every waitable's answer
  forever, and the register file is the one thing that cannot grow. The
  Event's payload is its accumulated word, which makes the common wait one
  syscall instead of two.
- **The answer consumes what it reports.** A delivery of an Event reads and
  clears the word into the wait record, in one interrupts-masked step — so
  the bits a record carries are bits nothing else will ever be told about,
  and a `receive` right after a wake answers zero. A signal landing after the
  take re-arms readiness with its own bits alone: nothing lost, nothing
  double-counted. The alternative — a snapshot the wait leaves behind —
  reports the same bits to the next wait as well, and a recipient cannot tell
  that duplicate from a real second signal. §2.4's `receive` keeps its exact
  semantics as the non-blocking poll; the two are doors onto one value, and a
  process picks by whether it wants to park.
- **The multiplexing rule, in one line:** many threads per Waiter is
  distribution (one wake per delivery, the worker-pool shape); one Waiter per
  waitable is ownership, and it is structural — an object is attached to at
  most one Waiter, whichever handle names it, so a delivered value has exactly
  one recipient by construction and no multi-watcher caveat exists.
- **Level-triggered is an absence.** Nothing records that a waiter has been
  told, so `Wait` scans the attachment set, and three properties fall out of
  that one decision rather than being coded separately: a signal before
  anyone waits is not lost, an event still ready wakes the next waiter, and
  attaching an already-ready handle reports immediately. An attachment is its
  own object — (waiter, kind, target, key) — rather than a field of the
  waitable, because a Waiter's set has to be one list: a wait scans it once
  and a `remove` walks it once, so per-kind lists would make both a matrix
  over kinds.
- **The Waiter itself can go, and a parked thread is told.** When a Waiter is
  destroyed — its last reference drops, or its owner dies (§2.10) — every
  thread on its blocked list is woken with `SosStatus.Revoked`. Three things
  make that one sentence:
  - **No record is written, and that is the contrast to hold onto.** A
    delivery copies a record into the caller's memory and then answers `Ok`,
    because something became ready and the record is what became ready.
    Nothing became ready here — no key, since a key names an attachment; no
    tag, since no waitable fired; no payload, since there is no value — so a
    revocation writes the status register and nothing else, and the woken
    `wait` returns with its buffer exactly as it left it. As a pair: a
    delivery copies out and answers `Ok`; a revocation answers `Revoked` and
    copies nothing. (The wake therefore lives beside the free rather than in
    the delivery path: it touches no process memory.)
  - **The walk is the whole list, not the pop a readiness does.** The
    distribution rule above is about a ready handle, of which there is one;
    here the thing every parked thread is waiting on is what ceased to exist,
    so every one of them is answered.
  - **On the release path the count is the gate, not the release.** A
    release with a minted sibling still live frees nothing and wakes nobody —
    the §2 reference ledger unchanged, and the difference between revocation
    and any-release. The owner's death is the other gate, and it needs no
    count (§2.10).

  Not yet enforced for the owner's death: the teardown frees its Waiters
  without the wake, which nothing can observe while only the owner's threads
  can park on them.

  The alternative of counting a parked thread as a reference, so the Waiter
  never reaches zero, does not work: it keeps the thread alive and makes the
  drop silent, turning an error a program can handle into a hang somebody has to
  infer. **This is the peer-gone doctrine, and the doctrine is the contract:
  nothing parked can be silently doomed** — every "what you are waiting for can
  no longer happen" is a delivered wake carrying a distinguishable status, never
  a strand. Its other instances are the one-shot reply pair (`PeerClosed`, both
  directions), a connection endpoint reaching zero references (§2.1), any object
  invalidated under a parked thread (`Revoked`, §2.10), and an attachment on a
  waitable that becomes invalid, which fires (§2.10); the last two are not yet
  enforced for the bound kinds or the Interrupt. Terminal levels follow the
  Process precedent (§8): attaching after the fact still wakes, and nothing
  un-closes. The deadlock predicate has no arm for revocation: the wake is
  synchronous inside the act that destroyed the Waiter (the syscall that dropped
  the last reference, or the owner's teardown), so there is no idle-and-doomed
  state for it to see. The typed surface needs nothing new, since `Waiter.wait`
  already answers `Result<WaitResult, SosStatus>`.
- **Copy-out is checked, and it is one door.** The destination must be
  word-aligned and inside the process's writable grant; one that is not
  terminates the process (§5.7's faults ruling — a process linked its own
  image and knows where its data is). A single kernel funnel, `copy_out`,
  performs that check for everything that copies out, §2.1's message body
  included; with its mirror `copy_in` it is the only place the kernel
  dereferences a user address, which is what keeps a per-address-space
  mapping switch a one-place change.
- **`write_result` is the only function that writes an answer into a frame** —
  its callers are `ktrap` for an op that answered at once and the wake paths
  that answer a parked thread — which is what makes "exactly one write per
  syscall" checkable.
- The buffer is the caller's, because the kernel has no allocator and nowhere
  to put one that would outlive the call. The typed Saw wrapper supplies it
  out of its own frame, so a raw address never appears in that surface.
- **`remove` takes the key, and keys are unique per Waiter.** Detaching edits
  the Waiter's own attachment table and never touches the waitable, so the
  authority it spends is the Waiter handle plus the key that names the
  attachment — a waitable's handle would be authority the operation does not
  use. It is also the only form that survives handle close: a closed
  waitable's stale attachment has no handle left to name it with. The
  invariant that forces: `add` with a key the Waiter already uses is a fault
  (`DuplicateKey`), because a duplicate makes two questions ambiguous at once
  — which attachment a `remove` names, and which one an answer came from.
- **Waitables — the list is closed, and these are its members:**
  `PipeOutlet` (readable — a message is staged, or the client end is gone),
  `PipeInlet` (room-to-post), Event, Timer, Interrupt, Process (§8),
  `PipeReply` (reply-ready), and `PipeRequest` (abandoned, §2.1). Each pipe
  kind spends a `Wait` bit (`1 << 9`) in its own rights enum and default set.
  The inlet's room-to-post level is ready while the staging ring has a free
  slot or the server end is gone, with the payload word saying which, so a
  waker learns whether to post or to stop without a probe post. It is a plain
  level, not a terminal one: consume clears nothing, and the level's end is
  the poster's own next post. A Thread is not waitable (§8), and attaching one
  is a `NotWaitable` fault; so is attaching a MemoryObject, which has no state
  that could become ready.

  What stays per-kind is five questions — who is watching me, set who is
  watching me, am I ready, what does a record say, and a record was
  delivered — each an exhaustive match, and `waitable_slot` is the only place
  waitability is decided, so a further waitable cannot be added silently:
  adding a `WaitableKind` fails all five matrix arms, adding the `WaitTag` and
  `WaitPayload` cases fails every userspace match on a wait answer, and
  `waitable_slot` fails its own, so the compiler enumerates the work.

  The fifth question is the ack-free drain, and the kinds answer it
  differently. An Event's word and a Timer's fire count are both spent by the
  delivery. An Interrupt answers with nothing, because its readiness ends at
  the driver's ack — the line stays masked until the device has actually been
  serviced, so the kernel cannot know a record was enough. A Process's death
  also spends nothing, for the opposite reason — it is the terminal level, so
  there is nothing to end: a delivery reports the §8 status word and leaves
  it, so every later wait on that attachment answers the same word, and a
  waiter that attaches after the death is told at once. A readable outlet and
  a postable inlet spend nothing on the Interrupt's terms: their readiness has
  an end the kernel does not own — a take, and the poster's own next post. An
  abandoned claim spends nothing on the Process's terms: nothing un-abandons.
  The reply is the only one that is neither: its delivery discharges the
  exchange, because the record carried the reply itself and a second copy of
  an answer must not exist. One answer depends on the attachment and not only
  the kind: a readable outlet watched by a borrowing attach spends nothing —
  the take is a later op, and that is what "there is something" means — while
  under a consuming attach the delivery is the take, so it spends the staged
  message exactly as a reply delivery spends the exchange. Delivery is one
  funnel, so the drain cannot happen on the already-ready path and not on the
  wake path, whichever door the readiness came through.
- **The copy-in door is the copy-out funnel's mirror twin**, used by
  `Timer.Arm` and by §2.1's message body. It refuses the same two things — a
  misaligned address, an address outside the process — and terminates the
  process for either, on the same reasoning. One asymmetry is deliberate:
  copy-out's window is the process's writable memory and copy-in's is the
  whole granted region, because a record may legitimately come out of a
  process's own rodata, and the two doors guard different hazards (writing
  outside the writable window corrupts the process; reading outside the
  granted region reads somebody else). A copy-in length is checked for
  equality rather than "at least": a capacity may be over-provisioned, but an
  inbound length is the caller's statement of what it is handing over.
- **Attach semantics:** **level-triggered** (keeps reporting ready until the
  waiter handles it — no lost edges); attachments are **persistent by
  default** with explicit `remove(key)` (by key, never by the waitable's
  handle); an object is attached to **at most one Waiter**, whichever handle
  names it, but **multiple threads/tasks may wait on the same Waiter** (a
  ready handle wakes one waiter — the shared readiness-queue shape). Attaching
  an object that is already attached faults the caller (`BadState`), through
  any holder's handle: the rule binds every holder of the object, so holders
  must agree which of them attaches it, and a giver that attaches an object
  itself withholds `Wait` when it gives the object on. `WaiterOp.Add` takes a
  target, a key and a flags word (§5.7).
- **One-shot is an opt-in property of the attachment.** `AttachMode.OneShot`
  detaches at delivery — when the record for its key is copied out, never at
  mere readiness — and the delivery is otherwise identical. It is the
  attacher's choice like the key, spelled as a defaulted trailing parameter.
  What it buys: a blocked poster's room-wait, a request-abandoned watcher and a
  wait-once death supervisor all stop owing a trailing `remove`. **The footgun
  is stated rather than guarded**: attach one-shot, take the delivery, and
  wait again on a Waiter with nothing left attached, and the thread parks
  forever — which the deadlock report names, as it does for any empty Waiter.
  **An op that consumes its object detaches it implicitly, and must**:
  `PipeReplyOp.Resolve` and `PipeRequestOp.Reply` destroy the caller's entry,
  and an attachment is a counted reference, so without the implicit detach the
  op could not free what it consumed, and refusing while attached would wedge
  the op ordering instead.
- **The consuming attach is a different verb, not a third mode.**
  `Waiter.give(...)` takes the wrapper by move: the op releases the caller's
  handle entry and the attachment owns the object from there, so a multiplexer
  holds zero handles however many things it is watching, and "hanging up" is
  destroying the subscription. It is a verb rather than a mode value because
  consuming is an effect change and a runtime flag may not make one — the reason
  §2.1 has no send-mode enum. The two axes are orthogonal: `add`/`give` crossed
  with `Persistent`/`OneShot` is a 2x2. A refused `give` hands the object back
  in its error payload, because a full attachment table is a resource condition
  and the caller keeps what it had. An Interrupt is the one waitable that cannot
  be given to a Waiter: its handle is its one name and the only thing that can
  `Ack` it (§2.10), so a consuming attach of an Interrupt is a `BadArg` fault,
  the flags word asking for something the target's kind excludes, and
  `Waiter.add(interrupt:)` borrows. Not yet enforced: `Waiter.give(interrupt:)`
  exists and consumes its wrapper.
- **The buffer is max-sized and the copy is not.** A parked waiter cannot
  predict which arm fires, so the buffer holds the biggest answer its
  attachments could give, while the kernel copies only what the delivery
  needs. A larger record costs a Timer waiter buffer space and never copy time.
  `wait_record_bytes()` is written once and read once, since every typed
  caller's buffer comes out of the wrapper's own frame.
- **The record layout: fixed prefix, variable tail.** A delivery can carry a
  message, so the record is four regions in this order, and the order is the
  rule:
  1. a **header** of three words — key, tag, payload;
  2. the **packed meta**, one byte of body length followed by one kind byte
     per message handle slot (a `BootHandleKind` value, 0 = empty);
  3. the **handle region**, one word per slot: the request slot first, then
     one per message slot;
  4. the **body**, `PIPE_BODY_BYTES` of it, last because it is the only
     region whose used length varies.

  The published capacity a caller must supply is all four together. Bytes
  last is what makes every copy-out a contiguous prefix: a delivery carrying
  handles and no data writes up to the end of the handle region and stops,
  rather than skipping the body region to reach a region behind it. There are
  exactly two copy lengths — a word-payload delivery ends at the meta (all
  meta bytes zero, so the decode reads no handle word and no body byte), and a
  message delivery writes the whole fixed prefix plus the meta's `len`. The
  length is a byte and not a word, because a number bounded by
  `PIPE_BODY_BYTES` fits in a byte and has to travel beside the kind bytes
  anyway. The per-profile answer is the ABI's rather than anybody's
  arithmetic: declaration order is layout (§5.7), so the meta bytes sit
  in the padding the word-aligned handle region needs regardless — one machine
  word of it on the 64-bit profile, two on the 32-bit one — and the fixed
  prefix stays a whole number of machine words on both, which is what makes
  "a prefix ending at the body's length" an arithmetic rather than a hope.
  `sosabi` publishes the region offsets and `static_assert`s all three facts.
- **The delivery may be the take.** A consuming, persistent attachment on a
  `PipeOutlet` is handed each message through `wait` itself: its own tag
  (`WaitTag.Message`, distinct from `Readable` because the two say different
  things about the record's shape), the message's bytes in the body, its handles
  in the message slots, and — minted at the delivery — the obligation to answer,
  in the request slot. A server built this way holds zero endpoint handles and
  spends one trap per message it answers, `wait` then `reply_wait` forever, and
  two for a message it discards (§2.1). **A delivery that mints can be refused,
  and the refusal is atomic.** The mints a message delivery needs — the
  obligation and one per carried handle — can meet a full handle table or a
  spent quota, and that is a resource condition rather than a protocol one. So
  the kernel pre-flights the whole batch: if it will not fit, nothing is copied,
  consumed or detached, the message stays staged with its readable level still
  raised, and the waiter is woken with the status and no record — the shape
  revocation established (a wake that hands nothing over is still a wake). The
  server frees a slot and waits again.
- **A second door answers with the same record.** `PipeRequestOp.ReplyWait`
  (§2.1) discharges a request and then parks on a Waiter exactly as
  `WaiterOp.Wait` does, resuming with exactly this record into exactly this
  buffer — the wake protocol stays singular, and there is one decode because
  there is one record. What differs is only the refusal, and it is the shape a
  two-legged op needs: the answer carries a `ReplyWaitLeg` beside the status
  saying which half produced it. `Reply` means the reply did not land and the
  op did not park, so a server loop recovers by waiting afresh and one dead
  client cannot stall it; `Wait` means the reply landed and the wait itself
  broke. `Wait` is the zero value, which costs nothing to maintain: every path
  that answers a parked thread — a delivery, a revocation, a teardown —
  already writes a zero value register, so only the non-parking leg has to
  stamp anything. A plain `wait()` is unaffected by the second door.

### 2.4 Event: accumulating non-blocking notifications

- The fire-and-forget primitive. `event.signal(bits)` never blocks; the kernel
  accumulates into the Event's word. `event.receive() -> word` drains and resets
  to 0. Waitable (non-zero = ready → attach to a Waiter).
- **The word has two doors and both consume it.** `receive` is the
  non-blocking poll — drain-and-reset, 0 when empty, never parks — and a
  `Waiter.wait` delivery is the blocking one, whose record carries the same
  accumulated word and clears it in the same interrupts-masked step (§2.2). A
  value is therefore delivered exactly once, whichever door it came through,
  and the choice between them is only whether the caller wants to park. The
  recipient is unique by construction: an object is attached to at
  most one Waiter, so no second watcher can be deprived of a word a wait took.
- **Accumulation mode is a per-Event property:**
  - **bitwise-OR** — "which of these events occurred since last drain"
    (flag set; the signal use case).
  - **saturating-sum** — "how many times" (counting-semaphore use case;
    saturates rather than wrapping — a fast producer must not corrupt
    the count).
- Accumulation is a plain OR or saturating add on the Event word (`accumulate`).
  Multiple senders race harmlessly, because the kernel runs a syscall to
  completion and takes no interrupt in kernel mode (§9) on one processor (§7);
  one drain reads-and-clears.

### 2.5 Memory: MemoryObject vs Mapping

**Names.** This section's design vocabulary is `MemoryObject`; the kinds the
kernel declares are **`Memory`** (RAM), **`IoMemory`** (device MMIO) and
**`Mapping`** (one installed row) — `ObjType` cases in `kernel/abi/`, under
the numbering §5.7's vDSO discipline keeps renumberable. The verbs are `split`
(RAM: one cut from the front, the parent becomes the remainder), `carve`
(device: the parent stays whole), `map` (install a row, answer a `Mapping`)
and `give` (§3's move of a region into a child's table). The longer spellings
name the same three kinds wherever they appear.

**The model.**

- **`MemoryObject`** = authority over a physical page range, allocated
  **from a typed pool**. The pool's attribute governs three things at once:
  - **RAM pool** (cacheable, `Normal`): unique allocation — a region is
    allocated once and unavailable until freed; returns to the pool when its
    last reference drops (see the refcount invariant below). Maps as
    `UnsafeMemory<_, Normal>`-class cacheable memory.
  - **Device pool** (uncacheable MMIO, `Device`): a fixed region may be
    handed out many times (multiple drivers can hold handles to the same
    peripheral window) and is **never freed** — MMIO is pinned. Maps as
    `UnsafeMemory<_, Device>` volatile memory.
  - The pool attribute is Saw's `UnsafeMemory` intent marker surfacing at the
    kernel layer — a Device MemoryObject can only produce Device mappings, so
    a driver physically cannot get a cached view of a register block. The
    attribute travels with the handle.
- Sendable over pipes — so the same physical memory can be mapped into
  multiple address spaces at multiple virtual locations (the shared-memory
  primitive). Derived by splitting / attenuating a parent; pool roots given
  to the root server at boot.
- **`map(aspace, ...) -> Mapping`.** Mapping is a distinct kernel object with
  its own handle, recording the virtual placement. **Only the Mapping handle
  can unmap** (`mapping.unmap()`).
- **Deliberate stance:** dropping a Mapping handle without unmapping means
  that mapping is permanent — safe-but-leaked, never a dangling-unmap hazard.
- **Refcount invariant:** a Mapping holds a reference to the physical pages
  (not merely to a MemoryObject handle). The pages are freed only when the
  last reference of either kind — any MemoryObject handle or any Mapping —
  drops. This is what makes multi-mapping safe: no mapping can outlive its
  backing.

**The pool attribute is a kind.** A RAM pool root is a `Memory` and a device
pool root is an `IoMemory`, decided by a kind column in the build-emitted
region table (§12). The intent marker is therefore not a flag anybody checks:
an IoMemory's `map` takes no access argument and installs a device-attribute
row, and there is no other op on the kind, so "a Device MemoryObject can only
produce Device mappings" holds by construction. The attribute travels with
the handle because the attribute is the handle's kind.

**A region is a capability, not a description.** There is no op that reads a
MemoryObject's bounds: a process that holds one can hand it to the kernel and
cannot learn a number from it. What a process knows about where its memory is,
it knows from the config that gave it the region — which is what root's
tag-to-meaning config is, and where a driver's `UART_BASE` comes from.

**`Memory`: ops `Split` and `Map`, rights `MemoryRight.Split`, `.Map` and
`.MapExecute`** beside the universal `Transfer` and `Mint`.

- **`split(len)` is one cut from the front, and the parent becomes the
  remainder.** Allocation is repeated front-splits and the parent is the pool
  cursor, so a piece is allocated once and unavailable until freed.
  Arbitrary-offset carving is refused by the shape rather than by a check,
  since one `{base, len}` slot cannot hold two remainders.
- **`map(process, access)` installs a protection row and answers with a
  `Mapping`.** It spends `MemoryRight.Map` on the region and `ProcessRight.Map`
  on the target, because possession of bytes must not imply authority over an
  address space. `aspace` is a process: §5b says the domain is the process on
  every profile, so the argument names the process rather than an object that
  would be a second name for it. A process may be given access without
  possession: a launcher that keeps a region and installs the child's row
  itself leaves the child holding no `Memory` handle at all
  (`tests/share-double-map`), while a driver child given an `IoMemory`
  installs its own row. Two installation directions, no mode flag.
- **Access is a property of the mapping.** A region carries no R/W/X triple,
  and double-mapping one region RO here and RW there is expressible and
  allowed. An access word asking for write without read is refused.
- **Executable is a right.** `MemoryOp.Map` refuses `MapAccess.Execute`
  unless the Memory handle carries **`MemoryRight.MapExecute`** — the same
  refusal (`AccessDenied`, a fault) every unspent right answers. It is
  capability flow, not identity: "only root maps executable" holds because
  root never grants the bit, not because the kernel asks who is calling, and
  `memory_rights()` therefore mints it at boot, of necessity — §3's attenuation
  is monotonic, so a bit not minted at boot could never appear later, and the
  narrowing is a holder's `MINT_OP` keep mask (a launcher writes "this child
  may map its RAM and may never map it executable" with
  `Memory.mint(rights:)` before the region travels).
- **No row is both writable and executable.** `Write | Execute` in one access
  word is a caller-checkable `BadArg` fault, beside the write-without-read
  refusal. It costs nothing expressible,
  because the double map is sanctioned: an RW row here and an RX row there
  over one region is the JIT-shaped pattern in full, and what is removed is
  only the single row a process could write and then fetch from.
- **The boundary is the dynamic map.** Neither rule governs a process image's
  own executable segments: those arrive through `process_create`'s loader as
  `SegFlag` bits read out of the image, validated by `imgformat.has_sane_perms`
  (which refuses write-without-read and X-on-device and deliberately not W|X
  — an image's segments are kept apart by its linker script), under
  `SystemRight.ProcessCreate` authority that a driver child does not hold.
- **It is per handle, not per region.** A second handle that still carries
  the bit can map the same bytes executable. A region-level immutable flag
  would be a second mechanism doing overlapping work.

**`IoMemory`: ops `Carve` and `Map`, rights `IoMemoryRight.Carve` and `.Map`**
beside the universal pair.

- It is pinned (never freed — MMIO is not reclaimable), carved
  non-exclusively — `carve(offset, len)` leaves the parent whole where
  `split` consumes, which is "a fixed region may be handed out many times" —
  and it has no contents.
- The machine's granularity is checked at the carve against a per-profile
  HAL predicate (Profile A needs a naturally aligned power of two — one
  protection entry; Profile B needs whole pages), because a window that could
  never be installed anywhere is a capability that lies about itself.
- `IoMemoryOp.Map` takes no access argument at all, so execute-on-device is
  refused vocabulary and there is no `IoMemoryRight.MapExecute`; the
  executable gate does not apply to the kind, by construction.
- `iomemory_rights()` mints `Transfer`, so a launcher can `give` a driver
  child its device window (`give(iomemory:)`), and the child maps it into
  itself (§9). The kernel's `boot_kind_of` has its `IoMemory` arm for the
  same flow.

**`Mapping`: one installed protection row.** The object records which
process's domain carries the row and which row it is. A grant row carries both
halves — a user VA range and the physical base it translates to — so
"recording the virtual placement" is literal on tier 1, and the two halves are
asserted equal on the tiers that do not translate (§5.5): one mechanism with
one degree of freedom unspent on the MPU tier, not two mechanisms.

- **Two ops.** `Unmap` (on `MappingRight.Unmap`) and `Base` (on
  `MappingRight.Base`) — the address the kernel chose, asked for rather than
  assumed, which is the portable spelling every program should use
  (`tests/map-placed`). `Base` is a separate bit from `Unmap` because locating
  a row and revoking one are different authorities, so a launcher may hand a
  child a Mapping it can find and not take away (the ordinary shape — the
  child needs the address to use the memory) or one it can revoke and not
  locate. The answer is a user address on every tier, and on a tier that does
  not translate it is also the physical one.
- **A mapping's address is the kernel's answer and is treated as data.** `map`
  takes no VA hint on any tier; `Mapping.base()` asks. On tier 1 `map` chooses
  the address, so two processes sharing one region hold two different numbers
  for the same bytes and `tests/map-placed` asserts both. On tiers 2 and 3 a
  region is shared at its own address and `share-double-map` reads one
  constant on both sides.
- **Releasing the handle is not an unmap.** Release destroys a table entry and
  never the object's state (§3), so "dropped without unmap = permanent,
  safe-but-leaked" falls out of existing doctrine rather than being a new rule.
  There is no unmapping `Deinit`: it would make the row's lifetime the
  wrapper's, and a launcher that maps a page into a child holds the Mapping in
  a local and drops it right after it hands the child its memory, so a
  scope-exit unmap would revoke the page before the child ever ran.
- **A Mapping owns its row; the row is not a counted reference back.** So a
  mapped-but-unreferenced Mapping — every handle released, the grant still
  installed — reaches zero, frees its slot and leaves the row: the
  safe-but-leaked stance as a line of kernel. The row is then unremovable until
  the target process's teardown, which is what "leaked" means and is the bound
  on it (`tests/mapping-slot-free` writes and reads through such a row). The
  mirror case is a husk of the other kind — unmapped but still referenced —
  whose `Unmap` is a `BadState` fault. The row being leaked does not leak the
  bytes past it: the region survives its own last handle because the row still
  counts against it, and the range returns when the row goes (below).
- **The live-domain rule.** Any edit to a grant record reloads immediately
  when that domain is the installed one, because `run_thread` skips equal
  domains and an unmap that waited for the next reschedule would be a
  revocation that did not revoke.
- Unmapping twice is `BadState`.
- **It is bound to its target.** A Mapping charges the target process's quota,
  not the caller's (§2.9), and the target is its owner in §2.10's sense: when
  the target dies its row goes with the whole domain and the Mapping is
  invalid, so `Unmap` and `Base` answer `Revoked`. Not yet enforced: both are
  `BadState` faults on a Mapping whose target has died.
- **It is not givable** — `mapping_rights()` withholds `Transfer` — because a
  Mapping names a row in one specific domain.

**The refcount invariant, as built.** The region's own slab slot carries two
counts: `MemorySlot.refs` (handle entries naming the region, and references held
by a staged pipe message) and `MemorySlot.maps` (installed grant rows naming
it).

- Reaching zero on `refs` frees the slot synchronously, inside the release
  that drops the last reference (§3) — no deferred reclamation, no cleanup
  queue.
- The range returns to its pool at the second zero, which is the invariant
  above executing as written: a region whose handles are all released but
  whose grant row is still installed stays out of the pool until that row
  dies. `maps` counts installed grant rows rather than `Mapping` objects,
  because a Mapping's slot and its row have different lifetimes (the husk
  above), and a row cannot die unwitnessed — `record_grant` makes one, and
  `remove_grant` and `clear_domain` are the only two things that take one
  away.
- Where a returned range goes (the pool root, coalescing, first fit), how bytes
  are accounted, and why `IoMemory` is exempt are §5.9.

**Device grants.** A driver obtains its device window as an `IoMemory`
capability — from its boot set, or given by a launcher — and maps it into
itself; or a launcher that keeps the window maps it into the driver
(`Process.map(iomemory:)` on the child's handle, §12). Children cannot declare
windows in their images (`allow_device: false`). The image-declared device grant
(`SegFlag.Device` and the boot loader's `allow_device: true` door) still exists
but is dormant: no image in this tree uses it, and its emitter is sawlang-side
(`blade/src/sosimg.saw`), so deleting the flag is a toolchain-pin event (§11).

**A DMA-capable device's driver is inside the TCB.** On the isolated tiers, a
`map` bounds what a driver's CPU may reach. It bounds nothing a bus master may
reach — so a driver that can program a DMA engine can make that engine write any
physical address on the machine, and the window it was granted is a fence around
the driver, never around the device. Until an IOMMU exists, such a driver is a
TCB member and is trusted like one; isolating it is not something a rights mask
can do. The architecture is a userspace IOMMU driver that owns the IOMMU as an
ordinary device (a window and a line, granted by root) and to which a DMA driver
sends Memory handles over IPC, creator-pays charging composing unchanged. It has
two prerequisites. The first, process-death notifications (§8), is built, so the
IOMMU driver can stop a dead process's device operations and release its memory
deliberately rather than by teardown. The second is not: **critical processes**
— a process root marks at launch whose exit for any reason faults the system,
checked at the top of teardown ahead of any release, because the halt must
preempt the free or the window it exists to close reopens inside it. Both v1
boards' consoles are programmed I/O, so no case in the suite is a DMA driver and
no capability in the tree is a lie about one.

### 2.6 Clock and Timer

**Clock.** A granted time source: time is a capability, not an ambient
facility. Obtained through `SystemOp.ClockGet` on `SystemRight.ClockGet`; ops
`Now`/`TimerCreate`; rights `ClockRight.Read`/`.TimerCreate`.

- **A hardware-backed clock is one kernel-eternal object per `ClockType`**,
  existing from boot and owned by nobody: the machine has one monotonic
  counter, and a per-process object naming it would be a copy of a fact with a
  lifetime attached. So there is one slot per domain (the slot is the domain's
  ordinal), no allocation and no slab `NoResource`, and process teardown frees
  no clock — a dead process's clock handle is unbound like any other, and the
  object it named is not the process's to reclaim.
- **`ClockGet` is a getter that mints a handle onto a well-known object, and
  it mints on every ask.** Two asks are two capability instances naming the
  one Clock, each independently owned and independently released, which is
  what §4's owning wrapper is an owner of. It amplifies nothing (§3), and it
  makes the op fallible on repetition — a full handle table is `NoResource` —
  which is why it is subject to the handle quota (§2.9). The label is
  `clock_get(type:)`.
- `ClockType` declares `Monotonic` only (`Boot`/`Realtime` are future values
  of a raw-backed enum, undeclared because an unproducible case is dead
  surface), and `Now` dispatches on the clock's domain, so a second domain
  fails to compile until somebody says what its reading is. `Now` answers
  through a copy-out record, because a nanosecond count is 64 bits and one
  profile's registers are not.
- **The point of the capability:** strip the right from a child and hand it a
  virtual clock over IPC instead, with no code change on either side. A
  virtual clock is a different animal — separately created and stateful
  (offset, rate, owner) — so it gets its own creation op and its own lifetime
  rather than a row in this table. Not implemented: virtual clocks.

**Timer.** A deadline object bound to the Clock that created it, and directly
waitable; it is the process-sleep primitive (without it a wait either returns
at once or blocks forever). Ops `Arm`/`Disarm`; rights `TimerRight.Arm`
(gating both) and `.Wait`.

- `arm(after_ns, interval_ns)` arrives through the copy-in record (§2.2),
  because two 64-bit times exceed the argument registers on a 32-bit profile.
  `interval_ns == 0` is a one-shot, which disarms itself when it fires.
- **The re-arm is drift-free** (next = previous deadline + interval, the
  timerfd model), and missed expiries coalesce into a saturating fire count
  delivered as `WaitPayload.Timer(fires:)`. The count is computed by division
  rather than by a loop, so an interval far below what the machine can serve
  costs one divide; what keeps it from livelocking is a floor on how soon the
  hardware is ever programmed, which leaves the logical schedule untouched. No
  minimum interval is imposed and nothing is refused.
- **There is no ack**: unlike §9's Interrupt there is no mask to release, so
  the wait that reports the fires is what consumes them.
- Arming an armed timer replaces its schedule and clears the count; disarming
  an unarmed one is a no-op, deliberately opposite to §9's ack-with-no-fire —
  a one-shot disarms itself, so cancelling a timeout that just expired is an
  ordinary race rather than a caller error.

**One hardware timer, two customers.** Both machines have a single
comparator, and the scheduler tick (§7) and every armed Timer want it. The
per-arch seam is absolute (`timer_now_ns()` + `timer_set_deadline_ns(at)`) — a
periodic seam can express exactly one customer — and the tick's period lives
in the arch-free kernel, where scheduler policy belongs. The kernel keeps every
deadline it owes in nanoseconds and programs the earliest; a fire means
"something may be due", and one reading of the clock answers for every
customer with the tick asked first. The tick is inviolate by construction: a
Timer deadline can only ever move the hardware earlier, never later. The two
re-arm rules are opposite, deliberately: the tick re-arms from now (a late tick
is one tick, never a burst of catch-up), a Timer from its own deadline — a
timeslice has no history to be faithful to and an interval does.

### 2.7 Process

The Process object: an AddressSpace, a handle table and threads while it
lives, and a status word once it has died. Its lifecycle is §8 and its boot set
is §12.

**A process is a record and a body**, two entries in two tables, because what a
dead process has to keep is small and what a live one needs is not.

- **The record** holds what must outlive the process: its state and §8 status
  word (`exit_kind`, `exit_code`), the quota ledger (§2.9), `refs` and the
  attachment (§2's counted-kinds table), and the final trap counters (`Stats`,
  below). It is where per-process identity lives. A Process handle names the
  record, an attachment on one names it, and so does every object charged to
  the process (its `process` field, or a Mapping's `target`, §2.9), so a handle
  to a `Gone` process stays valid and reads the status word.
- **The body** holds what only a live process needs: the handle table, the boot
  set, the protection domain and its grant rows, the recorded entry and stack,
  and the thread bookkeeping. A live record names its body. The body is freed at
  the teardown (§2.10), which clears that link, so a `Gone` process holds a
  record and no body.

`ProcessCreate` takes one record and one body, and either table full answers
`NoResource`, a status with no quota asked first (§2.9); a create that finds one
and not the other takes neither. Root gets a record and a body at boot as a
child does at its create, the first of each table, so "process 0" names both
(§8, §12). An op that needs a body answers through a `Gone` process's handle
exactly as §2.10 rules for it, since the body is gone: the creation ops and a
`map` naming it answer `Revoked`, `Start` and `Give` are `BadState` faults
(below), and the self-only ops are `NotSelf` faults. Not yet enforced: the
creation ops and a `map` do not answer `Revoked` there (§2.10).

**Ops**, each on its own `ProcessRight`: the creation ops `ThreadCreate`,
`EventCreate`, `WaiterCreate` and `PipeCreate`, then `ThreadSelf`, `Exit`,
`GetStatus` (on `Wait`), `Start`, `BootHandleNext` (on `BootHandles`), `Give`,
and `Stats`. `ProcessRight.Map` is the target-side right `MemoryOp.Map` and
`IoMemoryOp.Map` spend (§2.5). Creating a process is not an op on this object:
`ProcessCreate` is on System, because a process is a machine-wide resource
(§12), and binding an interrupt line is on System for the same reason (§2.8).
Not yet enforced: binding is still `ProcessOp.InterruptBind`, a Process op on
`ProcessRight.InterruptBind`. `kill` has no op and no right (§8).

**Which process an op acts on is part of its contract**, and the ops fall into
three groups:

- **The creation ops act on behalf of the process the handle names**, through
  any Process handle carrying the op's right. That process owns the new object
  and is charged for it: a thread created through a child's handle runs in the
  child and counts against the child's thread quota. The new handle — two, for
  `PipeCreate` — is placed in the caller's table, charged to the caller's
  `Handle` row as every mint is (§2.9), and the caller gives it on if the
  other process needs it: every kind these ops create is givable (§3). A
  caller error (a bad entry, stack or mode) faults the caller. Not yet
  enforced: the handles are minted into the named process's table, so through
  another process's handle the caller is handed words relative to that table;
  Thread and Waiter handles cannot yet be given; and a bad entry or
  stack in `ThreadCreate` (`BadEntry`) or a bad mode in `EventCreate`
  (`BadArg`) faults the named process. `ProcessOp.InterruptBind` does the same
  while it exists: it mints into the named process's table and faults that
  process for a bad line (`BadArg`) or a bound one (`LineBound`). The teardown
  assumes the dying process is the caller, so the caller's running thread is
  then never scheduled again. `PipeCreate` already faults the caller, and
  `WaiterCreate` has no fault path.
- **`Exit`, `ThreadSelf` and `BootHandleNext` are self-only**: through a handle
  naming another process the kernel faults the caller (`NotSelf`). `Exit` ends
  the calling process, `ThreadSelf` names the caller's running thread, and
  `BootHandleNext` drains the caller's own boot set. A `Gone` process's handle
  never names the live caller, so through one these are the same `NotSelf` fault
  (already so for `Exit`). Not yet enforced for `ThreadSelf` and
  `BootHandleNext`: each checks its right on the handle used and acts on the
  caller whichever process the handle names, so a holder of another process's
  handle with `BootHandles` can drain its own boot set even when its own Process
  handle lacks the right.
- **`Start`, `Give`, `GetStatus` and `Stats` act on the named process**, which
  is what supervision is. `Start` and `Give` require a `Created` child, and
  naming one that is not, a `Gone` one included, is a `BadState` fault rather
  than `Revoked` (§2.10): a `Gone` child was always started, and the launcher
  that started it knows. Not yet enforced: a `Created` child can die unstarted
  today, because a caller error in `ThreadCreate`, `EventCreate` or
  `ProcessOp.InterruptBind` through its handle faults the child (above).

**The default set.** One Process default set serves all three minters (root's,
`ProcessSelf`'s and `ProcessCreate`'s), named for its ops throughout:
`ThreadCreate`, `ThreadSelf`, `Exit`, `Wait`, `EventCreate`, `WaiterCreate`,
`Start`, `BootHandles`, `Give`, `Map`, `PipeCreate` and `Stats`, plus the
universal `Transfer | Mint`. Not yet enforced: the set still carries
`ProcessRight.InterruptBind`, the right `SystemRight.InterruptBind` replaces
(§2.8). A launcher keeps the child's handle — it is what supervises with — and
the child derives its own authority from the masked System handle it was given
(§12), so supervision and self-management are not alternatives.
`Process.mint(rights:)` makes each bit attenuable at the typed tier: a
supervisor may be handed a sibling that learns whether its child is alive and
may not watch it work.

**`Give` — the courier op.** `give(handle, tag:)` on the child's handle, gated
by `ProcessRight.Give` there plus the universal `Transfer` right on the handle
being given, moves a handle into a fresh slot of the child's table and returns
only its status: the child-side word is meaningless to the giver, which can
call no op through it. What crosses instead is the tag — the giver's own word,
handed back unread at the child's drain.

- It takes a keep mask: a third argument word, intersected with the entry's
  rights, `ALL_RIGHTS` spelling "the rights the giver held". It is `MINT_OP`'s
  mask reached through the courier: fail-closed (a bit the source lacks is
  ignored, never an error), structurally non-amplifying (the result is an
  intersection), and it leaves the move a move — no default set is consulted,
  and the caller's entry unbinds exactly as a release does, so the giver's word
  goes stale.
- Its faults and statuses are listed in full at §3 ("Which failures fault and
  which answer a status", beside the transfer-funnel contract). A caller error
  ends the caller — among them a child that has already been started (`BadState`
  — the boot set freezes at start, which is the launch flow's whole soundness
  argument) and a tag the child's set already carries (`DuplicateKey` — the tag
  is the identity, and one naming two handles would make the boot lookup
  ambiguous). A resource refusal is a status, and the give has not happened:
  the typed tier hands the object back in the error payload (§3).
- `Start` takes a `boot_tag`: the kernel resolves the tag to the child-side
  word, puts it in the child's first argument register and consumes the record
  it named (the register is the delivery, so a child can never be handed one
  word twice). The child's entry is `_start(boot_handle)`, and the launch flow
  never shows a launcher a child-relative word. Nor do the creation ops, which
  place their handles in the caller's table (above) — not yet enforced: today
  they hand a launcher words relative to the child's table.
- `BootHandleNext` drains the caller's own per-process set, and only the
  caller's (above) — the kernel writes root's at boot and a launcher writes a
  child's with `give`, through one op with one exhaustion rule (§12).

**A Process handle is a waitable**, a member of §2.2's list. Readiness is
`state == Gone`; the payload is the §8 status word (`WaitTag.Process`,
`WaitPayload.Process(status:)`); the right it spends is `ProcessRight.Wait`, the
same bit `GetStatus` spends, because attaching is that question asked
asynchronously, and a second bit would let a supervisor poll a death it may not
be woken by. It is a terminal level — a delivery does not consume it — so a
waiter attaching after the death still wakes, a second wait answers the same
word, and supervision has no lost-edge race. The attachment is a counted
reference like every other waitable's, which keeps a dead child's record
readable for exactly as long as somebody is watching it. There is no
notification to anyone but a parked or attaching waiter: the Waiter is the
delivery system.

**The record of a dead process is reclaimed.** A `Gone` record holds its §8
status word and its final trap counters, and the only ways to read them are a
Process handle (`GetStatus`, `Stats`) and an attachment on one (a wait —
including an attachment that owns a reference to the object after
`Waiter.give(process:)`), so "no handle entry or attachment names this record"
is "no possible reader", exactly. A record is reclaimed when it is `Gone` and
its `refs` is zero, whoever drops the last reference. Any drop can be the last:
among them a release, a detach, a delivery that consumes the attachment, the
release of a staged message carrying a Process handle, and the teardown of a
process that held one, which drops its handles and its attachments.

A record is never reclaimed, and so never reused, before its own teardown has
ended, because that teardown still writes it: the notify that wakes its
watchers comes first and can consume the last attachment, and the counters are
copied and the ledger cleared after it. A record whose count reaches zero
during its own teardown is reclaimed when the teardown ends. `refs` is
maintained at the same sites as every other kind's count, so a `Gone` process is
ordinary rather than special. Two rules make the reuse safe, and a record
carries no generation of its own. The count covers the names that hold a
reference, so none of them can name a reclaimed record, and a released word
fails its entry's generation (§3). The teardown's disown (§2.10) covers the
fields that hold none, so no object that outlives a process still names its
record, and a refund at a survivor's later free is owed to nobody rather than
to the record's next occupant.

**The body is reclaimed at the teardown**, whatever names the record: nothing
reads a dead process's handle table, boot set or domain, so none of them waits
on a reference. `clear_domain` invalidates `LAST_PROT_PROCESS` as the body goes,
so a later process landing in it cannot compare equal to a domain that is no
longer installed. Where the kernel walks every handle table, the walk is over
the body table's extent chain and is bounded by `process_slots_usable()` ×
`MAX_HANDLES`.

**`Stats` — exact trap introspection.** `ProcessOp.Stats` on
`ProcessRight.Stats` answers through a three-doubleword copy-out record:
`syscalls`, `interrupts`, `faults`, split by the cause class the trap entry
decides on.

- The split is what makes the numbers usable: a single total would mix a
  quantity the program determines with one the machine determines, so nothing
  could be asserted about it. Separated, `syscalls` is exact for a given
  program path (one per syscall instruction, whatever the op, whatever the
  answer, and one for a call that parks for a millisecond), `interrupts` moves
  with the host, and `faults` is exact where a test provokes one.
- **A bad syscall counts as a syscall**: an unknown handle, an unknown op or a
  missing right ends the process (§5.7's faults ruling) but arrived through the
  syscall instruction, and keying on the outcome would make the assertable
  column unpredictable.
- **Attribution is to the process current at trap entry**, decided before any
  dispatch, so a syscall that switches processes is charged to the one that
  made it and an interrupt that preempts is charged to the one it interrupted.
  Traps taken through the boot door before any process exists are
  unattributed, and an interrupt taken while the kernel is idle or at a
  preemption point is not counted at all, because it arrives through the poll
  rather than through a vector and no process was current.
- While the process lives, the columns are its body's row of the shared stats
  region (below), keyed by the body's index, and the row is zeroed at process
  create, on both doors, so a reused body never inherits its predecessor's
  traffic. They survive the death: the teardown copies them into the record as
  the body goes, and `Stats` through a `Gone` process's handle reads that copy,
  which is how a supervisor observes a fault column, since a faulting process is
  not around to ask about itself. Nothing is charged to a process once its
  teardown has begun, so the copy is final. A mapped stats-region reader can
  also see the dead process's row without any handle, until a create reuses the
  body and clears it. The reading includes the call that took it (the column is
  charged at trap entry, ahead of the op), so a delta between two readings
  carries the second reading's own trap.
- Per-process only: per-thread stats wait for a consumer that names one.

**The stats region.** `SystemOp.StatsRegionMap` on `SystemRight.StatsRegionMap`
installs a row, read-only on the isolated tiers, over the kernel's own counter
page in the caller's domain and answers a `Mapping` plus the caller's own row
index; the kernel writes each process's row at the same trap-entry sites that
maintain the columns above, so a reader's `top` costs no syscalls at all after
the map. `ProcessOp.Stats` is independent of it and is what a program uses for
another process's counters. Three things about the region:

- **No `Memory` handle is minted.** The caller is granted the access and
  nothing that owns it — a Memory onto kernel storage would carry `Split` and
  would make the page donatable.
- **`rows` is a capacity, not a census.** There is a row for every body the
  build can hand out, not for every record. A row for an unoccupied body reads
  whatever its last occupant left until a create zeroes it, and v1 publishes no
  liveness column.
- **A row index is not a name.** The op answers the caller's own row and
  nothing else, so a general `top` that maps an arbitrary process to a row
  still wants either a per-row identity column or an op resolving a Process
  handle to its row. Not built.

**Capacity.** Two tables, two floors, and a create needs room in both.

- **Bodies.** `MAX_PROCESSES` is the compiled number of bodies the kernel boots
  with — the floor of the bound, not the whole of it. A deployment donates
  memory to back more (`SlabKind.Processes`, through `SystemOp.SlabDonate`,
  §2.8), and the running bound is `process_slots_usable()` =
  `min(PROCESSES.capacity(), hal.PROT_DOMAIN_SLOTS)`. The second term exists
  because a per-process translation-table set comes out of a static HAL pool
  (donation does not reach HAL-internal storage), so a translating tier has a
  lower ceiling than its slab does, and a create past it answers `NoResource` —
  a status, not a fault, because the caller could not have known a number the
  kernel does not publish. `kcore.limits`'
  `static_assert(MAX_PROCESSES <= hal.PROT_DOMAIN_SLOTS)` binds the floor at
  compile time.
- **Records.** `MAX_PROCESS_RECORDS` is the compiled number of records, defined
  as `PROCESS_RECORDS_PER_BODY` × `MAX_PROCESSES`, with
  `PROCESS_RECORDS_PER_BODY` greater than one and chosen generously. A body is
  spent again as soon as its process dies, and each death leaves a record
  behind for as long as a handle or attachment holds it. Every live process
  holds a record too, so the floor keeps `PROCESS_RECORDS_PER_BODY` − 1 dead,
  still-held processes per body answerable before a create is refused. It is a
  floor and not a guarantee: every handle entry and attachment in the machine
  could name a different dead process, and a table sized to that would outweigh
  the bodies. A supervisor that keeps its dead children's handles is what fills
  it, and releasing them is the remedy. The record table is its own donatable
  slab, `SlabKind.ProcessRecords`, because demand for records follows how long
  holders keep dead processes, not how many run, and on a tier whose
  protection-domain pool caps bodies, records still grow.

Per-process state is never a parallel `MAX_PROCESSES ×` array: the handle
table, the boot-handle queue and the grant record are fields of the body, and
the quota ledger, the status word and the final trap counters are fields of the
record (`kcore.pslot`), because a table beside a slab would be left behind by a
donation. `MAX_ATTACHMENTS` derives from the waitable slabs an attachment can
name, pipes aside, and a Process attachment names a record:
`MAX_EVENTS + MAX_INTERRUPTS + MAX_TIMERS + MAX_PROCESS_RECORDS`. The
protection-row budget is per domain, so a process adds nothing to another's
(root's own grant record stays at `IMAGE_GRANT_ROWS` whatever it wires up,
because a launcher hands out capabilities and maps nothing).

### 2.8 System

The kernel singleton: the object behind system-scoped primitives, so that every
syscall is an object op (§5.7). Root receives its handle at boot; a child
receives a masked sibling of its launcher's when the launcher gives one and
names its tag at `start`, and a child started with no tag receives none in its
first argument register (§12). Each op names a machine-wide fact or resource,
which is why it lives here: System is the object that stands for the machine as
a whole.

| op | right | what it does |
|---|---|---|
| `DebugPrint` | `SystemRight.Debug` | places a byte on the console (§9a) |
| `Shutdown(status)` | `SystemRight.Shutdown` | stops the machine (QEMU: `sifive_test` on Profile A, semihosting on Profile B) |
| `ProcessSelf` | `SystemRight.ProcessSelf` | mints the caller's own Process handle, narrowed by a keep mask |
| `ClockGet` | `SystemRight.ClockGet` | mints a Clock handle (§2.6) |
| `ProcessCreate` | `SystemRight.ProcessCreate` | creates an inert process (§8, §12) |
| `InterruptBind(line)` | `SystemRight.InterruptBind` | binds an interrupt line to a new Interrupt (§9); not yet enforced, see below |
| `SlabDonate` | `SystemRight.SlabDonate` | donates a `Memory` region to a kind's slab |
| `TierGet` | `SystemRight.TierGet` | answers the protection tier (§5b.1) |
| `StatsRegionMap` | `SystemRight.StatsRegionMap` | maps the trap-counter page read-only, on the isolated tiers (§2.7) |

- **`exit` is not here**: process exit is `ProcessOp.Exit`, which ends the
  calling process, and faults a caller whose handle names another (§2.7).
- **`process_self` is §3's derivation rule made real.** The boot register
  stays one handle wide, and a process obtains its Process object through the
  System handle rather than being handed it. Strip `SystemRight.ProcessSelf`
  and a child may print and tell the time and never learn its own identity. It
  takes a keep mask, intersected with the one Process default set (§2.7), so a
  process can narrow the Process handle it derives at the moment it derives it
  — a driver that has made its Waiter dropping the authority to make another,
  a plugin deriving a handle that cannot start children. The addressee is the
  deriver, not the launcher: a keep mask on this op is a process narrowing
  itself, which is the privilege-drop shape a capability system should have,
  and the launcher's lever remains its mask on the System handle. It is
  fail-closed like every keep mask: a bit the default set lacks is ignored.
- **`root_system_rights()` includes `Transfer`**, so a launcher mints a masked
  sibling of its System handle (`MINT_OP`, §3) and gives that to a child. A
  child therefore bootstraps exactly as root does (§12): System in the first
  argument register, its own Process handle derived from it, its boot set
  drained from there. `Debug` in the mask is what lets a child print without
  owning a device; `Shutdown` left out is what stops it halting the machine
  through syscalls (a flat-tier process can write the finisher, §5b.1).
- **`SlabDonate`** consumes the donated capability and backs a named object
  kind's slab with it, permanently in v1 — so the compiled limits are a floor
  and capacity is policy, decided by the deployment rather than by the build.
  The region must have no live mappings and no sibling references (`BadState`
  otherwise): the kernel never shares slab backing with user-reachable memory.
- **`InterruptBind`** binds a line to a new Interrupt and places its one handle
  in the caller's table. A line is a machine-wide resource, which is why the op
  is here, and the bind charges no quota row (§2.9). What bounds Interrupt
  objects is the Interrupt slab (`MAX_INTERRUPTS`, a floor, since
  `SlabKind.Interrupts` is donatable), and a full slab answers `NoResource`.
  There is at most one live Interrupt per line, so the board's line count bounds
  the live ones; an invalid Interrupt an attachment still holds keeps its slot
  while its line is bound again (§2.10). A line the board does not have and the
  kernel's own timer line are `BadArg` faults, and a line already bound is a
  `LineBound` fault. Per-line authority is which bound handles go where: a
  privileged binder — a launcher holding the right — binds each line and gives
  the Interrupt to the driver that services it (§9), so a driver holds exactly
  the lines it was given and no authority to bind another. There is no per-line
  capability object. Not yet enforced: the op and the right do not exist, and
  binding is `ProcessOp.InterruptBind` on `ProcessRight.InterruptBind`, which
  mints into the table of the process the handle names, charges that process's
  `Interrupt` quota row, and faults that process, not the caller, for a bad or
  bound line (§2.7).
- **`TierGet`** answers `Isolated` or `Flat` (§5b.1). It reads no state, spends
  no resource, takes no argument and cannot fail. Info queries of this kind
  belong on this object and are gated like any other op.

### 2.9 Quotas

The factory capabilities (§12) decide whether a process may create an object;
the quota decides how many. The table is two per-process rows, used and limit,
with one column per `QuotaKind` — `Thread`, `Event`, `Waiter`, `Timer`,
`Memory`, `IoMemory`, `Mapping`, `Handle` and `Pipe` — stored in the process
record (`quota_used` / `quota_limit`, §2.7), the entry a charged object's
`process` field (a Mapping's `target`) names, not in the body. The `Handle` row
is charged inside `mint_handle` and credited inside `unbind_handle`. An
Interrupt has no column: there is at most one live Interrupt per line, and the
Interrupt slab bounds the objects (§2.8). Not yet enforced: the ledger still has
an `Interrupt` column, charged at the bind.

- **Policy first.** A quota is checked at every allocation site that answers
  `NoResource`, ahead of the slab, so a process at its budget is told
  `SosStatus.QuotaExceeded` and only one still within it can meet the machine's
  edge (`NoResource`). The charge itself is made once every refusal is behind it
  (`quota_room` is the check, `quota_charge` the charge). The exceptions are
  the kinds with no column: `ProcessCreate` (a full record or body table,
  §2.7's capacity), `InterruptBind` (§2.8) and an attachment (below) answer
  `NoResource` at a full slab with no quota asked first.
- **Creator-pays**, against the object's own `process` field, which names a
  process record and which a creation op sets to the process its handle names,
  not always the caller (§2.7). There is one exception: a Mapping charges the
  target, because what it consumes is that process's grant-row budget, and
  charging the caller would let a launcher's allowance gate another process's
  domain size.
- **Root's rows are unlimited** — root is init, and its policy cap is the
  machine, so the slab is what refuses it — except its mapping row, which is
  clamped to its own free grant rows. That clamp makes a `map()` meeting the
  physical wall with quota headroom left (which would be fatal) unreachable
  rather than merely unlikely.
- A child gets `kcore.limits`' documented defaults; there is no per-create
  quota argument in v1.
- **`Pipe` counts a connection.** `ProcessOp.PipeCreate` charges it once, and
  the credit lands when both of the connection's reference columns reach zero
  and the slot goes back. Charging per endpoint would be wrong: two ends of
  one connection are two handle entries, which the `Handle` row already
  counts, so a launcher that keeps both ends would pay twice for one slot, and
  giving an end away would look like a refund. Creator-pays means a child
  given an end spends nothing here, which lets a server child hold many
  sessions under a small pipe budget.
- **A one-shot pair has no column.** It is ring-slot state inside a connection
  the creator was already charged for, so `QuotaKind.Pipe` covers the row and
  its whole staging ring. An exchange spends `QuotaKind.Handle` twice — one
  entry for the claim at the post and one for the obligation at the take —
  the ordinary rule that a capability costs a table row; a fused `Call` mints
  neither, a difference of handle traffic and of nothing else.
- **Attachments have no column.** They are bounded by `MAX_ATTACHMENTS` — a
  floor, since `Attachments` is a donatable kind — because an attachment
  already costs the one thing quotas count on this axis: a Waiter to hold it,
  charged at `QuotaKind.Waiter`. A client that gives its claim to a
  subscription spends no `Handle` row while the request is outstanding, so
  `MAX_HANDLES` is not the bound on a client's in-flight depth.
- **A handle in a staged message is charged to nobody.** The post unbinds the
  entry, which credits the sender at the one place a binding is destroyed, and
  the take or the delivery mints it, which charges the taker; the charge is
  written off at one end and raised at the other. The hole is bounded, which
  is why it is safe: a connection's ring is `PIPE_INFLIGHT` slots each
  carrying at most `PIPE_MSG_HANDLES` handles, a connection cost a `Pipe` row,
  and every object a staged reference names already occupies the slab slot it
  was charged for. Quota <= wall therefore holds, and the slab is still what
  refuses the next allocation.
- **The taker's budget refuses, atomically.** A delivery mints up to
  `PIPE_MSG_HANDLES` + 1 entries at once, so the whole batch is pre-flighted
  against the taker's table and its `Handle` row before anything is minted; a
  receiver at its budget is told `QuotaExceeded` and the message stays staged,
  references and all. A partial charge would leave a message half-delivered,
  which is the state the pre-flight makes unrepresentable. Creator-pays decides
  who pays for an object; a message only ever moves who pays for a name.
- **Refunds follow the lifecycle (§2.10).** A bound kind's charge comes back
  when the object is invalidated, and a shared kind's when it is freed. A dying
  process's ledger is cleared at the end of its teardown, and a shared object
  that outlives the process it is charged to is disowned there: it is charged to
  nobody from then on, and its free credits nobody, so a refund never reaches
  the record's next occupant. The books balance and the machine stays bounded
  because quota <= wall: an object that outlives its charge still occupies the
  slab slot it was charged for.
- Quotas count objects, not bytes, in v1; bytes are the pool's ledger (§2.5).

### 2.10 Object lifecycle

Every kind that can be destroyed is in one of three states:

- **Live.**
- **Invalid:** the object is destroyed. Its working state is torn down, its
  quota charge (if it has one) is refunded, and anything parked on it is
  woken. Handles naming it still exist, and its slot is held for them; for a
  Process the slot held is its record, since its body goes at the teardown
  (§2.7).
- **Freed:** the last handle is released and the slot is reusable. A handle
  here is any counted reference (§2's table): an attachment or a message in
  flight holds a slot exactly as a table entry does.

**An op on an invalid object answers a status, not a fault.** Every op on an
invalid object answers `SosStatus.Revoked`, and a thread parked on an object
that becomes invalid is woken with it and handed no record. The holder did not
cause the invalidation and could not have checked for it, which is the
reasoning `PeerClosed` uses (§2.1). The word is §2.2's: a Waiter freed under a
parked thread is one instance of an object destroyed by somebody else, and a
caller is told the same fact the same way whichever kind it named. `PeerClosed`
stays a separate word, because a connection whose peer is gone is still there
and still the caller's, and what it reports is for the protocol to act on.

**What "every op" covers.** Dispatch keeps §5.7's order, so a handle that names
nothing, a missing right and a malformed argument fault exactly as they would
on a live object; `Revoked` only reports that the object is gone. Release works
on an invalid object as on any other (§3), since releasing is how an invalid
object reaches Freed. `MINT_OP` on an invalid object answers `Revoked`. The
self-only Process ops through a `Gone` process's handle are the `NotSelf` fault
they are through any other process's handle, because such a handle never names
the live caller (§2.7). The Process row below lists the ops that read a `Gone`
process's remnant and the two that stay faults.

**An attachment on an invalidated waitable fires.** Invalidation makes the
attachment ready, so a waiter is never stranded on something that can no longer
happen (§2.2's peer-gone doctrine). The delivery is an ordinary record of the
waitable's own kind, and no tag or payload case is added for it; a one-shot
attachment detaches at that delivery as usual. Nothing un-invalidates, so the
level is terminal, as a death is: a persistent attachment keeps reporting it
until it is removed (by key, with `Waiter.remove`). What the holder learns from
it comes at its next op on the object, which answers `Revoked`; an Interrupt is
the exception, below. Not yet enforced: invalidation of the bound kinds and of
an Interrupt is not built. A Timer, the one bound kind that is waitable, can
today be attached only by its owner's own Waiters, because neither handle
carries `Transfer` and the creation ops mint into the named process's table. The
teardown frees those attachments with the Waiters (`end_process`'s attachment
sweep) and then frees the Timer without notifying anybody (its timer sweep). An
attached Interrupt whose handle is released stays bound, since the attachment's
reference keeps it live.

**An invalid Interrupt's record carries no line.** Its attachment fires as any
invalidated waitable's does, but the payload is the board's no-line value,
`hal.IRQ_NONE` (the value a HAL answers for "the controller had nothing to
give", never a real line), and never the line the Interrupt was bound to. That
line may already be bound to a new Interrupt, and a driver dispatching by line
(§9) must not act on a line that has since been rebound. The record is also how
the holder learns: an Interrupt destroyed by its handle's release leaves no
handle to make the op that would answer `Revoked`. Not yet enforced: the
payload is the slot's stored line whatever the object's state
(`waitable_answer`).

**Which kinds live how.**

| kind | lifetime |
|---|---|
| Thread, Timer, Waiter, Mapping | **Bound** to the process that owns it: invalidated when that process dies, freed when the last handle goes. A Mapping's owner is the process whose domain holds its row (the target of its `map`), not the process holding its handle. A Thread that exited before its process died is not invalidated: `Exited` wins, and a late `Join` through any handle reads its exit code from the slot, even after the process is `Gone`, the slot being held while a handle names it. A Thread that has not exited when its process dies is invalid, so `Join` answers `Revoked` and the slot keeps nothing for it. A Thread whose count reached zero while it ran is freed when it exits or is invalidated |
| Interrupt | **One name, its handle.** No `Mint` in its default set, and attenuation is monotonic, so no handle can regain it (§3). Releasing the handle, explicitly or by its holder's teardown, destroys the Interrupt: it unbinds and masks the line. A give or a staged message moves the one name and is not a release. An attachment does not extend its life; it holds the invalid Interrupt until it is removed, and fires as an invalidated waitable does. A consuming attach of an Interrupt is refused as a caller error (`BadArg`, §2.2), so the driver keeps the one handle it acks with. Charged to no quota (§2.9) |
| Event, Pipe, Memory, IoMemory | **Shared:** live until the last handle goes, whatever happens to the process charged for them |
| Process | `Gone` is its invalid state (§8), and a `Gone` process is a record with no body. `GetStatus` and an attachment read the status word the record keeps, and `Stats` reads the final trap counters the teardown copied into it (§2.7). `Start` and `Give` naming a `Gone` child stay `BadState` faults, because a `Gone` child was always started and its launcher knows (§2.7). Every other op that acts on the process answers `Revoked`, a `map` naming it as the target included: the mapper did not cause the death. The teardown frees the body; the record is reclaimed when it is `Gone` and its count is zero (§2.7) |
| Clock, System | Never destroyed |

**A free of a live object runs its last rites.** The last handle going is a
destruction, so the kind's last rites (§3) do what an invalidation does: a
Waiter wakes its parked threads with `Revoked`, a Timer gives the comparator
back, an Interrupt masks its line, and the charge is refunded. A Mapping keeps
§2.5's shape: its row is not torn down by the free, and its charge, which
counts the row, comes back when the row goes. A Thread that has not exited is
not freed by losing its last handle (§2's table).

**The teardown.** Process teardown runs in this order:

1. Invalidate every bound object the dying process owns (a Thread only if it
   has not exited).
2. Release its handle table, where each release drops a reference and a count
   reaching zero frees.
3. Disown every object that survives the death and still names its record
   (below).
4. Copy the trap counters into the record, clear the domain, drop the boot set
   and clear the ledger, then free the body for the next create.

Owned objects are found by sweeping the slabs by owner. The releases are
ordinary ones, so an Interrupt the process held is destroyed by its own handle's
release, and a shared object or another `Gone` process's record that the dying
process was the last to name, by a handle or an attachment, is freed by it. The
disown comes before the domain clear, which drops the grant rows' references
and can free a region, so that free finds the region already disowned. The
ledger is cleared after every step that can credit it. The dying process's own
record outlives the body, `Gone`, for as long as a handle or an attachment
names it, and the teardown reclaims it if nothing does (§2.7).

**No object outlives its owner's teardown still naming its record.** A record is
reused once nothing references it, but some fields name a record without
holding a reference, so the teardown disowns every object that survives the
death and names the dying record, writing no owner (`NO_PROCESS`) into that
field:

- the `process` field of a shared object still charged to the dying process (a
  Memory, an IoMemory, a connection, an Event), of an `Exited` Thread, of any
  invalid bound object (a Thread, Timer or Waiter), and of an Interrupt it bound
  that outlives it, whether another table, a staged message or an attachment
  holds it;
- the `target` of a Mapping whose target is the dying process, so the husk
  names nobody;
- and the record's own link to its body, cleared as the body goes (§2.7).

Without it a reused record would inherit its predecessor's survivors: its
teardown's owner sweeps would match them, a free would refund it a charge
already written off, and a husk's check of its target would read a stranger's
state. A disowned object's free credits nobody, whatever its kind; an object
freed before its disown credits the dying ledger as any free does, and the
teardown clears that ledger. Not yet enforced: only the shared kinds are
disowned (a Memory, an IoMemory, a connection or an Event still charged to the
dying process), and only the shared kinds' frees test for no owner; the
teardown frees the process's Threads, Waiters, Interrupts and Timers instead
(below), so none of them survives it. The rest arrives with the invalidation of
the bound kinds and the teardown order above.

The teardown reports itself on one console line, `SOS: process teardown`, and
the harness asserts the line. Its fields today (`handles=`, `threads=`,
`events=`, `waiters=`, `interrupts=`, `timers=`, `process=`) count the handles
the close-all closed and the objects the teardown freed. `events=` counts the
Events charged to the dying process that the reap's Event scan freed, which are
those nothing named once its handles and attachments were gone. It does not
count an Event freed earlier, inside the close-all (one staged on a connection
whose last outlet the close-all released, say), nor one freed later by the
reap's exchange scan, nor one still named elsewhere, which survives disowned.
The other fields follow the owner sweeps, so they change with the teardown:
under the order above no owner sweep reaches an Interrupt, and the Mappings
that are swept have no field.

**The refund rule.**

- **A bound kind's charge is refunded at invalidation.** Its owner is then
  either live or mid-teardown with its ledger about to be cleared, so a refund
  can never land on a reused process record, and the invalid object is
  disowned at the teardown (above).
- **A shared kind can outlive the process charged for it**, so that process's
  death disowns it (above). The teardown first frees the shared objects nothing
  else names, which credits the dying ledger as any free does; one freed after
  its disown (a region whose last namer is a grant row in the dying domain, or
  an object only a staged message named) credits nobody. Either way its charge
  ends with the ledger, which the teardown clears. A dead process's ledger has
  no consumer, since creation through a `Gone` process's handle is refused. The
  objects it was charged for do not hold the dead record, which is reclaimed on
  its handles and attachment alone.

Not yet enforced:

- Thread slots are not counted (§2's table).
- The teardown releases the handle table first, counting without freeing, and
  then frees the dying process's Threads, Waiters, Interrupts and Timers by
  owner, frees the Mappings it made, and frees the Event, Memory, IoMemory,
  exchanges and connections nothing names any more, disowning the rest it was
  charged for; that reap ends with a process scan, which reclaims every
  `Gone` process record whose last reference the teardown dropped. An
  Interrupt is therefore freed by its owner's death, and a Thread, Waiter or
  Timer is freed rather than left invalid, an `Exited` Thread included, which is
  sound only while every handle naming it is in the dying table. A Mapping that
  targets the dying process already has the invalid shape — its slot kept, its
  row cleared, its handles in other tables — though its ops answer `BadState`
  today (next item).
- Ops on an invalid object do not answer `Revoked`. `Unmap` and `Base` on a
  Mapping whose target died are `BadState` faults (§2.5), and so is a `map`
  naming a `Gone` target. The creation ops through a `Gone` process's handle
  answer `QuotaExceeded` when their arguments are well formed, its ledger
  having been cleared, and `ThreadCreate` answers it whatever its entry and
  stack, since no region is left to check them against; a bad mode or line
  faults the `Gone` process again, which reruns the teardown (with no body left
  to free), overwrites its status word and strands the caller. `MINT_OP` mints
  onto an invalid object as onto a live one.
- An Interrupt carries `Mint`, is charged an `Interrupt` quota row, can be
  consumed into an attachment (`Waiter.give(interrupt:)`), and stays bound
  while an attachment holds it after its handle is released.

## 3. Handles, rights, derivation

- **The handle table.** Each process has a table: index → (object ref, rights
  word). Handles are plain integers in the syscall ABI; the kernel validates
  index + generation (stale-handle detection) + rights on every use. An entry is
  (object type, rights, target, generation), and a handle word is
  `(generation << HANDLE_INDEX_BITS) | (index + 1)` — the index field's width is
  one named constant in `sosabi` (`HANDLE_INDEX_BITS`) with every mask and shift
  derived from it, and the remaining bits of a portable 32-bit budget are
  generation, identically on both profiles. Four properties:
  - **Generation 0 stamps nothing.** A slot's first-life word is the bare
    1-based index, so §12's boot order holds verbatim.
  - **"Names nothing" is the index field's zero, at every generation.** A
    malformed word carrying generation bits over a zero index is not equal
    to `NO_HANDLE` and must still resolve to nothing.
  - The generation survives an unbind and is incremented by it, so a
    released word fails the equality test and becomes the ordinary
    `BadHandle` fault.
  - **Best-effort detection of a bug, not a uniqueness guarantee.** A word
    held across a full wrap of one slot's generation aliases silently;
    tables are per-process, so such a collision crosses no boundary and
    grants nothing. This is not to be "fixed" into unbounded bookkeeping.
- **Rights are a bitmask, scoped per object kind.** Each kind defines its own
  backed rights enum — `SystemRight: UInt32`, whose cases include
  `Transfer = 1`, `Mint = 2`, `Debug = 256` and `Shutdown = 512`, or
  `PipeInletRight`, whose cases include `Post` and `Wait` — spelled
  `SystemRight.Debug`, scoped by the enum (no flat name-mangling; exhaustive
  match and the raw-backed wire discipline come free). Kind-specific bits
  overlap freely across kinds: a rights word is only ever interpreted against
  its handle's kind, which dispatch establishes before the check (§3 order), so
  each kind owns 24 bits instead of sharing 32. Kernel-side, dispatch resolves a
  handle to a typed object view whose check takes only its own kind's enum
  (`SystemObject.allows(&self, right: SystemRight)`, and likewise per kind), so
  testing a pipe right against a System handle is a compile error. The
  exception: **universal rights own the pinned low byte — bits 0-7, identical in
  every kind's enum**, with room reserved because more shared operations are
  coming. Assigned: bit 0 = `Transfer` (may be moved to another process — the
  movability capability), bit 1 = `Mint` (may mint a sibling handle onto the
  same object); bits 2-7 reserved (candidates as they prove universal:
  wait/signal, introspect/info, revoke).
- **The doctrine: specific rights for specific operations, per object type;
  universal bits for universal ops; there is no generic authority.** Each
  right is named for the op it gates, so a launcher stripping one strips
  exactly one authority instead of an unpredictable bundle — which is why
  there is no `Manage`-style bit meaning "derive children": a bit general
  enough to be borrowed by several unrelated ops (`ProcessSelf`,
  `ThreadSelf`, `Give`, sibling derivation) is specific enough to gate none
  of them. `SystemRight.ProcessSelf`, `ProcessRight.ThreadSelf` and
  `ProcessRight.Give` are separate bits for that reason. What the doctrine
  does not mean is one bit per op number: `TimerRight.Arm` gates `Arm` and
  `Disarm`, and `WaiterRight.Attach` gates `Add` and `Remove`, because each
  pair is one capability and splitting it would produce a right that
  withholds nothing. The unit of a right is an authority. Generic kernel paths
  (handle transfer) check universal bits without knowing the kind; `sosabi`
  `static_assert`s each kind's enum against the pinned table so no kind can
  drift, and a kind-specific right below bit 8 is a spec violation the assert
  catches.
- **`Transfer` is read by the courier.** `ProcessOp.Give` checks it on the
  handle being given, at the one altitude that has the entry and has not yet
  branched on its kind — the same place `RELEASE_OP` is intercepted — and a
  pipe send checks it on each handle a message carries (§2.1).
  `rights_allow_transfer` is the one function that reads it. Which kinds'
  default sets mint it is a per-kind ruling: v1 mints it in
  `root_system_rights()`, `process_rights()`, `thread_rights()`,
  `event_rights()`, `waiter_rights()`, `timer_rights()`, `memory_rights()`,
  `iomemory_rights()`, `interrupt_rights()`, `pipe_inlet_rights()`,
  `pipe_outlet_rights()`, `pipe_reply_rights()` and `pipe_request_rights()`,
  so a Clock handle cannot be given (nor a Mapping, §2.5). Root's System
  handle carries it, which is what lets a launcher hand a child a masked
  System sibling (§12). Thread, Event, Waiter and Timer carry it because a
  creation op places the new handle in the caller's table and the caller gives
  it on (§2.7), and an Interrupt carries it so that a launcher can hand a
  driver its line (§2.8). Each givable kind has a boot record kind
  (`BootHandleKind`), which is what lets `give` deliver it and a message carry
  it (§2.1). Not yet enforced for Thread, Waiter, Timer and Interrupt: their
  default sets withhold `Transfer`, and `BootHandleKind` has no case for any of
  them.
- **The invariant is no amplification: no sequence of asks yields authority
  the asker did not already have.** A handle is a unique entry, which is the
  `NoCopy` correspondence §4 builds on — but uniqueness is the mechanism, and
  no amplification is the law. Three consequences of stating it that way:
  - **Asking twice gives two handles.** Every getter of an existing object
    mints a fresh one (`clock_get`, `process_self`, `thread_self`): no
    find-or-create, no handle-table search, no cached self-handle fact.
  - **There is no creator-holds-manage re-mint path**; rights are named for
    the operations they gate.
  - **Attenuation is an operation a holder performs**, not a property of what
    the kernel happened to mint at a creation — `MINT_OP`, below.

  Three facts hold the invariant, each a line of kernel rather than a
  discipline:
  - **A mint carries the kind's default set, or a subset of the source.** A
    getter mints the kind's default rights and nothing else; `MINT_OP` mints
    the source's rights intersected with a caller-supplied keep mask. Neither
    spelling can exceed what already exists.
  - **The authority to mint is itself rights-gated** — `SystemRight.ClockGet`,
    `SystemRight.ProcessSelf`, `SystemRight.ProcessCreate`,
    `ProcessRight.ThreadSelf`, and the universal `Mint` for `MINT_OP`. A
    process that cannot spend the bit cannot perform the mint.
  - **A give moves rights verbatim, or narrower** — the same invariant read
    from the transfer side. `ProcessOp.Give` copies the entry's kind, target
    and rights word into the receiver's table, intersected with the giver's
    keep mask, and consults no default set at all, so what the giver
    attenuated stays attenuated and no handle is ever wider on the far side of
    a transfer than it was on the near one. The mask lets a giver narrow at
    the give without minting a sibling, narrowing it, giving that away and
    releasing the original; it has `MINT_OP`'s fail-closed polarity and
    no-amplification structure, and `ALL_RIGHTS` is the mask that gives
    everything the entry holds. The giver's entry is unbound by the same act —
    `unbind_handle`, generation bump included, the same function release and
    the teardown call — so exactly one process holds the capability at every
    instant and the giver's word is a diagnosed `BadHandle` afterwards rather
    than an alias. `SystemOp.ProcessSelf` takes a keep mask for the same reason
    at the derive side (§2.8).
    **Rendezvous is a third door onto this same move.** A handle arriving in
    a message is unbound from the sender and bound in the receiver by the same
    mechanism, rights verbatim — but the two halves are separated in time, so
    the kernel holds the reference in between and the unbind happens at the
    post rather than at the receive (§2.1). What differs from `Give` is when
    (a give happens before the child starts, a rendezvous at any time between
    two running processes), who consents (both ends, since one had to send and
    the other had to take), and that a rendezvous has a middle — during which
    the capability belongs to the message, which is what makes it survive its
    sender.

  **The Clock is the worked example, and it is why mint-per-call is not a
  hole in attenuation.** A Clock is kernel-eternal and owned by nobody
  (§2.6), so `SystemOp.ClockGet` is a getter onto a well-known object and
  every ask mints another handle to it. Read carelessly that sounds like a
  leak — if asking always works, what does attenuating a clock handle buy? It
  buys everything, because the ask is gated too. A process whose System
  handle carries `ClockGet` could always read the clock, and a second handle
  adds nothing to what it already had. A process whose System handle had
  `ClockGet` masked out at the mint cannot ask at all, so a narrowed Clock
  handle it was given is the only clock it will ever have, and narrowing that
  handle means exactly what it says. Attenuating a handle you give away is
  meaningful precisely when the receiver lacks its own minting authority —
  which is the launcher's ordinary case, since the keep mask that builds a
  child's System handle is where a launcher decides. What the extra instances
  buy is therefore ownership rather than authority: each is independently held
  and independently released, which is what makes §4's `NoCopy` wrapper an
  owner rather than a name, and it is what a same-handle model could not give
  (two values wrapping one word double-release).
- **`Mint` is the second universal bit, and `MINT_OP` is the second universal
  op.** A handle carrying it may mint a sibling onto the same object, in the
  caller's own table, whose rights are the source's intersected with a keep
  mask the caller supplies. It is the attenuate op. Four properties, and the
  first two are why it is safe to hand out:
  - **No-amplification is structural.** The result is a subset of the source
    by construction, not by a check that could be forgotten, so a garbage mask
    cannot widen anything.
  - **`Mint` is itself maskable.** A sibling minted through a mask that omits
    it cannot mint again, so proliferation stops where the mask says it does.
  - **The mask is a keep set, which fails closed.** A mask written before a
    kind gains a right is a whitelist that has never heard of it and therefore
    denies it. A removal mask would fail open: a launcher masking System
    handles for children would hand every future System capability to every
    child ever configured.
  - **It is total, and needs no introspection.** There is no
    rights-introspection op; naming a right the source lacks is a no-op, so
    nothing can be over-asked and nothing has to be read first.

  It is what lets a launcher mint what it hands over and keep its own, so
  supervision and self-management are not alternatives (§12).

  **The typed tier spells every mask.** Every kind's rights enum is public in
  the `sos` module, because a keep mask is assembled from its bits at a mint, a
  give or before a send. Every wrapper whose kind's default set carries `Mint`
  has a `mint(rights:)`, whose default is the all-ones mask. An Interrupt has
  no typed `mint`, because it has one name.

  **An Interrupt has one name.** `Mint` is absent from `interrupt_rights()`,
  and attenuation is monotonic, so no handle onto an Interrupt can regain it:
  authority over a line is exactly one handle, which moves by `Transfer` and is
  never copied, and releasing it unbinds the line (§2.10). Not yet enforced:
  `interrupt_rights()` carries `Mint`.
- **Attenuation is monotonic**: any derivation may only strip rights, never add.
  The only rights source is the boot handle set given to the root server, so
  every rights word in the machine is a subset of one of those, reached by some
  path of mints and gives (a statement about the syscall path; a flat-tier
  process can write kernel memory, §5b.1). It is structural rather than a rule
  to obey — a sibling's rights are an intersection, so no sequence of mints
  recovers a masked bit — and it is performable, so a launcher narrows a
  capability itself instead of depending on what the kernel happened to mint.
  **Monotonicity is what makes a right a policy knob.** `MemoryRight.MapExecute`
  gates `MapAccess.Execute` on a `map` (§2.5), so "only root maps executable" is
  a fact about capability flow rather than about identity: `memory_rights()`
  mints the bit at boot — of necessity, because a bit not minted at boot could
  never appear later — and root simply never grants it, narrowing a child's
  region handle with a keep mask before the region travels. The kernel asks no
  question about who is calling. The policy lives entirely in a mask a launcher
  writes, which is what a right named for its op buys and what a generic bit
  could not have expressed at all.
- **Release and close are different acts.** Handle close is explicit in ABI,
  automatic in Saw (`deinit`).
  - **Release** destroys the caller's handle: one universal op number
    (`sosabi.RELEASE_OP`, documented forever outside every per-object table,
    which is dense from 0), intercepted by dispatch between the table lookup
    and the kind match. It is ungated, since destroying your own capability
    instance harms nobody and identity lives in the object's slot. It unbinds
    the entry, bumps the generation and never touches the object's state.
  - **It does touch the object's reference count, and zero frees.** Every
    countable slab slot carries a count of the handle entries naming it, plus,
    for a waitable, its attachment (§2); a release
    decrements, and a count reaching zero frees synchronously, inside the very
    syscall that dropped the last reference — the kernel runs to completion, so
    there is no deferred reclamation and no cleanup queue. The kind's own last
    rites run there: an Interrupt masks its line and becomes re-bindable (its
    handle's release does that even while an attachment holds it, §2.10; not yet
    enforced), a Timer gives the comparator back, a Waiter detaches its list
    (which may take a waitable to zero in turn — a cascade) and wakes its parked
    threads, a Memory returns its slot (and its range once no grant row names
    it, §2.5), and a `Gone` process's record is reclaimed, its body having
    gone at the teardown (§2.7). **The one exception is a Mapping**, whose row
    is not a reference back: it frees its slot and leaves the row installed
    (§2.5).
  - Releasing a word that does not resolve — `NO_HANDLE`, a malformed word, a
    stale one, a second release — is the ordinary `BadHandle` fault.
  - In Saw it is automatic: every `sos` wrapper is `NoCopy` with a `deinit`
    that releases, so drop is release and there is no typed `release()`
    method to write.
  - **Close** — ending the object for everyone — is not built, and the kinds
    that exist do not need it. A connection end is closed by releasing the last
    handle onto it, which is the reference column reaching zero — a fact the
    ledger already maintains at every bind and unbind. A `close` op beside it
    would be a second way to reach the same state, reachable while a sibling
    handle is still live, and would have to answer what a minted sibling then
    names. So drop-is-release is the close for these kinds, `let _ = move inlet`
    is how a program says it, and a per-kind close right has nothing to gate.
    Whether a later kind genuinely needs one is open on its own terms; such a
    close would be object protocol, existing only on kinds with an end state.
    Per-kind release rights (which would be a new universal bit) are not built.
  - A process's remaining handles are released by the teardown (§2.10, §8).
- Syscall ABI sketch (args in registers; §5.7 has the convention): every call
  is `(handle, op, args...) -> Result`. The kernel's dispatch is a table
  lookup + rights check + object-op — the fast path must stay tens of
  instructions.
- **Typed handles in the Saw API.** Each object kind gets a distinct handle
  type, declared in `sosabi` (`types.saw`) — `type SystemHandle = UInt`,
  `type PipeInletHandle = UInt`, … — and the Saw-facing `sos` wrapper holds the
  typed handle (`System` stores a `handle: SystemHandle`; its `shutdown` method
  passes it to the raw exported `sos_system_shutdown`). Saw's distinct-alias
  rule gives exactly the wanted asymmetry for free: the typed handle flows to
  `UInt` implicitly (one zero-cost lowering at the `sos_syscallN` stub), but a
  raw word or a different kind's handle cannot flow in — crossing into a handle
  type is an explicit construction, done at creation and, kernel-side, in
  dispatch after table/generation/rights validation (the type then means
  "validated as this kind", not just "a number"). The typing stops at the ABI
  boundary: `@export`ed vDSO symbols and the syscall stubs keep raw `UInt` words
  (C callers see words; the export whitelist is primitives), and the kernel
  handle table indexes by word. This is tier one (kind safety) of two. **Tier
  two is ownership**: every wrapper is a `NoCopy` struct over the aliases with a
  hand-written `deinit` that releases the word — the TcpStream pattern, the
  exact `NoCopy` correspondence of a unique handle entry. Release, generations,
  mint-per-call and owning wrappers exist together because three pairings are
  broken: release beside copyable (non-owning) wrappers is a stale-fault factory
  (copy, drop, and the sibling's next use is a manufactured `BadHandle`),
  mint-per-call without release is a leak by design, and release without
  generations is aliasing. Consequences:
  - The word field doubles as the disarm sentinel, since `NO_HANDLE` is
    unrepresentable as a live handle at any generation.
  - There is deliberately no typed `release()` method. Early release is
    dropping the value (`let _ = move w`), which is one concept rather than
    two spellings of it.
  - **The transfer-funnel contract.** When an op moves the word out of the
    caller's table, the sysapi funnel consumes the wrapper, reads the word, sets
    the field to `NO_HANDLE` before the syscall, and lets the disarmed value
    drop. User code never touches the sentinel; generations backstop the
    discipline, so a funnel bug that released after a transfer is a diagnosed
    `BadHandle`, not corruption. `Process.give(memory:tag:)`,
    `give(system:tag:)` and the other `Process.give` overloads take their
    wrapper by value and disarm it before the syscall, so a given capability is
    unreachable from the giver by construction rather than by discipline.
  - **Which failures fault and which answer a status.** A failed transfer is one
    of two kinds, and neither loses the kernel object. `ProcessOp.Give` faults
    the caller for a handle that names nothing (`BadHandle`), a caller handle
    without `ProcessRight.Give` or a given handle without `Transfer`
    (`AccessDenied`), a kind no boot record can carry (`BadArg`), a child
    already started (`BadState`) and a duplicate tag (`DuplicateKey`), and it
    checks every fault before touching either table. It answers a status —
    `NoResource` when the child's table or boot set is full, `QuotaExceeded`
    when the child is at its handle budget — and then the give has not happened:
    the child-side binding is made first, so a refusal leaves the caller's entry
    exactly as it was. `WaiterOp.Add` with the consuming flag (`Waiter.give`)
    faults for a Waiter handle without `WaiterRight.Attach` (`AccessDenied`), a
    flags word outside the published set or a consuming flag on an Interrupt
    target (`BadArg`; the second not yet enforced, §2.2), a target that names
    nothing (`BadHandle`), is not waitable (`NotWaitable`) or lacks its `Wait`
    right (`AccessDenied`), a target already attached to a Waiter (`BadState`)
    and a duplicate key (`DuplicateKey`), and answers `NoResource` when the
    attachment slab is full, having consumed nothing.
  - **What the typed tier does on a status.** A status refusal (`NoResource`,
    `QuotaExceeded`) is a resource condition, and the caller keeps what it had:
    the funnel disarms first, so no path can leave two owners of one word, and
    the refusal arm builds a fresh wrapper around the word the kernel provably
    did not consume and hands it back in the error payload. So no path leaves a
    disarmed-but-unsent word. The same rule covers the handles a pipe message
    carries, and the status decides it. A transient refusal of the send itself
    (`WouldBlock`, `NoResource`, `QuotaExceeded`, answered before the message is
    staged) leaves the carried handles in the caller's table, and the typed tier
    keeps the wrappers with the caller: `post_with` leaves its carrier
    untouched, so the caller may retry with it. Every other outcome consumes
    them. On success they travel with the message. On a terminal refusal
    (`PeerClosed`) the kernel releases them: each carried entry is unbound from
    the caller's table and its reference dropped, so an object whose last handle
    this was is destroyed (a pipe end reaching zero tells its peer, for
    example). The typed tier empties the carrier on those outcomes. A bad
    carried handle faults the caller before anything is taken, and so does a
    carried word equal to the handle the op is invoked through when the op
    consumes that handle: `Reply` and `ReplyWait` consume the request handle,
    which therefore cannot travel in their own message (`BadArg`). A `Post` or
    `Call` cannot carry an outlet of the connection it enters either (`BadArg`):
    the staged reference would hold the connection's outlet column above zero,
    so its exchange would never settle and, with the sender's inlet and claim
    gone, nothing could reach the connection to free it. A `Call` that parked
    for room is checked again when it resumes, from a record a sibling thread
    may have rewritten; there is no running caller to fault, so the slot naming
    such an outlet arrives empty and the outlet stays in the caller's table. A
    reply is unaffected, since it carries handles back to the client and not
    into the request's own ring. No new status marks the difference; the status
    says which half applies. The fused `send_with` never refuses the send itself
    transiently, since it parks for room and mints no claim, so its `PeerClosed`
    consumes the handles whether or not the message was staged. A `NoResource`
    or `QuotaExceeded` answered at the reply's delivery comes after the carried
    handles were staged, so it consumes them too. A reply refuses nothing
    transiently either: `reply_with` and `reply_wait_with` consume their handles
    on every answer, by delivery or, for a reply to an abandoned request, by
    release. `Waiter.give` and every `Process.give` overload follow the keep
    rule: a refused give answers the status and the object together, as
    `Result<Void, (SosStatus, T)>`, since every status `ProcessOp.Give` answers
    leaves the giver's entry in place.
  - **The funnel has a second spelling, for receivers.** Where the op consumes
    the handle it is invoked through, the sysapi method carries `consumes` in
    its effect slot beside `&var self`, and the caller spells
    `(move w).op(...)`. The word is then read and passed, and nothing is
    disarmed: a consuming body substitutes for the wrapper's `deinit` body on
    that path, the handle field's synthesized drop is a no-op, and no release
    fires. What this buys over the sentinel is the diagnosis moving from run
    time to compile time — a second use is `use of moved variable`, not a
    `BadHandle` the kernel has to draw. `PipeRequest.reply`, `reply_with`,
    `reply_wait`, `reply_wait_with` and `PipeReply.resolve` carry it. One shape
    keeps the sentinel: a funnel taking its wrapper as a by-value parameter
    (`Process.give`, the `Waiter.give` overloads), because `consumes` is a
    receiver-position effect.

## 4. The Saw synergy (why this language, this kernel)

- **Handle lifecycle = ownership.** Userspace handles are
  `NoCopy + Deinit` wrappers: scope exit closes (capability leaks are
  a compile error class); `move` is transfer;
  `pipe.send(move h)` enforces at compile time that the sender no
  longer holds what it sent. Kernel-side transferability (`Transfer`
  right) is checked at runtime; the language makes the userspace
  discipline free. Distinct types (`type TimerHandle = ...`) make
  handle-kind confusion unrepresentable in the syscall wrappers.
- **`sync` effect = ISR discipline.** Interrupt entry paths and
  kernel critical sections are `sync` contexts — suspension-free,
  compiler-verified.
- **Slabs + statics**: kernel objects live in fixed-size per-type slabs over
  static regions — allocation-free after boot, exhaustion is an explicit
  Err.
- **UnsafeMemory**: userspace drivers get MMIO MemoryObjects mapped into
  their space and wrap them in `UnsafeMemory<Regs, Device>` register blocks —
  typed, volatile, access-mode-checked driver code with no kernel driver
  surface.
- **Cooperative tasks in userspace**: the kernel schedules threads only; Saw
  TaskGroups/channels give each process its own async runtime; kernel Events
  integrate as wake sources (the `__wake` protocol's externally-signaled
  variant — future design).
- **No-forced-destroy carries over**: thread/process teardown is
  cooperative-first (signal + join); the kill-tree question is §5.3.

## 5. Design decisions

The questions the kernel had to answer before it could be built, each with
its decision.

### 5.1 Pipe semantics

Decided by §2.1. Asynchronous payload-free notification is the Event's job
(§2.4). The send-and-wait form is `send` (`send_with` with handles), one kernel
call (`PipeInletOp.Call`); the split-phase form is `post`; there is no
`timeout:` overload (§2.1). Message limits — a maximum body and a maximum handle
count — apply per message (§2.1).

### 5.2 Duplicate handles

Decided by §3: there is no duplicate right, and the invariant is no
amplification. A second handle onto an object a process holds comes from a
getter (which mints the kind's default set) or from `MINT_OP` (a subset of
the source); a handle is a unique entry, which mirrors `NoCopy` exactly.

### 5.3 Process/Job hierarchy

Decided by §2: a flat Process, with kernel teardown on exit or fault only, and
supervision in userspace. The kill-tree question is answered in §8: `kill`
is specified process-granular only, with no kernel trees — supervision
topology is userspace's.

### 5.4 The async wait primitive

Decided by §2.2: the `Waiter` aggregator with word-sized keys. Attach
semantics: level-triggered, persistent by default with `remove` (and an
opt-in `AttachMode.OneShot`), one Waiter per object, many waiters per Waiter.

### 5.5 Memory hardware and the address story

**The ESP32-P4's memory hardware**, verified against Espressif's documents:
the P4 has an MMU with basic virtual memory — but it translates external
memory only (SPI flash + PSRAM), as a single global mapping (one system-wide
virtual pool, per-page R/W/X, no ASIDs); internal SRAM is physically
addressed and bypasses it. The cores implement Machine + User modes only — no
S-mode, hence no Sv32 per-process paging. Isolation = up to 16 PMP/PMA
regions (first-level check) + the APM/TEE module (bus-level permission
contexts; User-mode accesses tagged into one of 3 REE contexts). Kernel
consequences: AddressSpace on the P4 = PMP region set + APM/REE context; the
global MMU mapping is a kernel-managed resource (executable code / large data
mapped from flash/PSRAM with W^X — shared mapping, not per-process;
per-context remapping would cost cache flushes). Physical memory, no
overcommit/COW, small-N processes (PMP/REE context counts bound simultaneous
domains). This is a profile, not a limitation of the model.

**"An address is the same number in every process" is a fact about a tier,
not about SOS** (§5b.1):

- **On tier 1 an address is the kernel's answer.** arm64-virt runs each
  process under its own translation-table set with the kernel relocated into
  the upper half, so every user image links at one canonical base
  (`hal.USER_IMAGE_BASE`), loads at whatever physical frames the allocator
  supplied, and `map` chooses the virtual address it answers with. Two
  processes sharing one region see it at two different addresses, and
  `tests/map-placed` asserts exactly that. There is no VA hint on any tier —
  the address is the answer, never a request.
- **On tiers 2 and 3 an address is the same number in every process**,
  because those platforms do not translate: a grant row's input range is its
  output range, and two resident images therefore cannot share a link base.
  riscv32-virt keeps one linker script per resident child image for precisely
  that reason, deliberately, so the two HAL directories side by side are the
  tier split.
- **The kernel above the seam does not know which of those it is on.**
  `GrantRow` carries a user VA range and the physical base it translates to;
  on a non-translating profile the two are asserted equal, so the identity
  tier is the general mechanism with one degree of freedom unspent rather than
  a second mechanism. Addresses-as-data stay physical everywhere they are
  stored — Memory capabilities, the build-emitted region table, `sosimg`
  records — and only the moment of kernel dereference converts, through
  `hal.phys_to_virt`.

### 5.6 Boot protocol

**The kernel loads the root server** from a separate flash partition — real
separation from day one; independent updates. To keep the loader
microkernel-sized the image format is not ELF but a minimal flat **sosimg** (a
header carrying magic, version, segment count, arch tag, priority map and an
absolute entry address; then segments of {load_addr, file_off, file_len,
mem_len, flags}; `imgformat` owns the layout) that Blade emits as a build
target. Kernel: parse header → map/copy segments (PMP/APM per §5.5) → mint root
handles (the System handle in the entry register; root `Memory` and `IoMemory`
regions for RAM and device ranges, collected through the boot set; the root IRQ
table is a right rather than an object, §12) → enter U-mode at the entry.
Everything else derives from those handles.

- **The format.** A versioned flat header plus fixed-size segment records
  (the layout is `imgformat`'s), all
  fixed-width little-endian so 32- and 64-bit profiles interoperate, carrying
  an `arch` tag (a wrong-profile image is a clean load error) and the §7
  priority map. It is emitted by a Blade `emit = "sosimg"` build target reading
  the package's `[sos]` manifest section (with `[sos.<triple>]` sections, so
  one package builds for both profiles with `src/` unchanged), and appended to
  the kernel image as a `.payload` blob with linker-symbol bounds. The layout
  lives once, in the shared `libs/imgformat/` package (sawlang-side): Blade
  consumes it as a path dependency and emits bytes through it, the kernel
  consumes it through `--module-path` and reads images by overlaying the same
  structs as `UnsafeMemory` typed views, with `static_assert` pinning the sizes
  on both sides.
- **The kernel validates an image in full before placing a byte of it**, so a
  segment aimed at the kernel is refused rather than obeyed. A segment must be
  aligned to the target's grant granularity (a page on Profile B, four bytes on
  Profile A) — without that, code could become writable because data started in
  the same unit. Blade reads ELF32 and ELF64. Image addresses are 64-bit fields
  on every profile, and a 32-bit kernel refuses an entry or segment address that
  does not fit its machine word (`fits_target_word`) rather than narrowing it.
- **The build also emits a region table** (§12), the second boot input the
  kernel reads.

### 5.7 Syscall ABI

**A (status, value) pair, and every call is an object op.** Conceptually every
syscall is `object.method(args)` — there are no bare numbered syscalls. The
register convention, per profile:

| Role | Profile A (riscv32) | Profile B (arm64) |
|---|---|---|
| handle in / status out | `a0` | `x0` |
| op (a method id on that object's table) | `a7` | `x8` |
| arguments | `a1`-`a3` | `x1`-`x3` |
| value/handle out | `a1` | `x1` |
| trap | `ecall` | `svc #0` |

The two are the same shape one-to-one, deliberately: the op sits in a
register outside the argument run on both, so arguments are a clean sequence
and neither profile has to shuffle. `x8` is where AArch64 Linux puts a syscall
number, so every disassembler and every reader who has seen one arm64 syscall
reads this one correctly. One difference is not cosmetic and belongs to the
HAL rather than the kernel: `ecall` leaves the saved PC pointing at the
instruction and `svc` leaves it pointing past, so advancing on return is a
per-profile decision (a kernel that advanced on Profile B would skip the
instruction after every syscall).

**Statuses.** The status word is 0 for ok, else a small **`SosStatus`** enum
tag. An enum with an `Ok` case is a status, not an error (`SosStatus.Ok`,
`SosStatus.NoResource`, …), and the `Sos` prefix names whose status it is. The
hosted runtime's `SysError` in sawlang's runtime ABI (`runtime/ABI.md` at the
pinned toolchain) is a separate frozen contract (errno tags, machine-parsed) and
keeps its name; the two correspond and nothing more. An `Err(SosStatus.Ok)`
never arises by construction: the wrapper boundary splits on the status — `Ok` →
`Result.Ok(value)`, anything else → `Err(status)`.

**The faults ruling: a caller error faults, a condition the caller could not
have known answers with a status.** A caller error the process could have
checked — an invalid handle, a malformed op, a rights violation — terminates the
offending process, so `BadHandle`, `BadOp` and `AccessDenied` are `FaultReason`
tags rather than statuses, keeping their numbers — `SosStatus` leaves 1–3
unused. The kernel stays up, reports, and runs the teardown (§8). The rule is
uniform because it has to be: an unbound handle names no kind, so there is no op
table to ask which rule applies. Not yet enforced through another process's
handle: a caller error in `ThreadCreate`, `EventCreate` or
`ProcessOp.InterruptBind` faults the named process instead (§2.7). What is left
in `SosStatus` is what a caller could not have known — `NoResource` (a full
slab), `QuotaExceeded` (§2.9), `BadImage` (§8), `Drained` (§12), `Revoked`
(§2.2, and every op on an invalid object, §2.10), `PeerClosed` and
`WouldBlock` (§2.1) — with `Unknown` the userspace mapping artifact. A dynamic
resource condition answers with a status; broken code faults.

**Dispatch** is §3's shape verbatim: handle-table lookup → object type → op
table → rights check → op. The System object (§2.8) is minted to root at boot,
and `debug_print` / `shutdown(status)` are among its ops (§2.8 lists them),
rights-gated (`SystemRight.Debug`/`.Shutdown`) — a process without the System
handle cannot even print. Process exit is a Process op. Ops map 1:1 onto the
`sos` module's typed wrappers (`system.debug_print(...)`,
`-> Result<T, SosStatus>`). Op tables and rights sets are named for the §2
object, spelled out (`SystemOp`, `ProcessOp`, `ThreadOp` beside `SystemRight`,
…).

**Rights-gated ops are the whole of the surface, and every gate is named for its
op.** There is no bare numbered syscall, no ambient facility, and no generic
authority anywhere in the ABI (§3): an op is reached through a handle of the
right kind and spends a bit named for that op. Binding an interrupt line is
`SystemRight.InterruptBind` on a System handle, not a root IRQ-table object or
a per-line capability: a right is the cheaper answer whenever an "object" would
have been a table with no state and one verb. Not yet enforced: the right is
still `ProcessRight.InterruptBind`, on a Process handle (§2.8).

**The boot set is one handle wide at the entry register.** A process enters user
mode with exactly one word in its first argument register — root's System
handle; for a child, the handle its start tag names (a System sibling, by the
§12 convention), or `NO_HANDLE` when it was started with no tag — and everything
else the kernel or a launcher minted for it drains through
`ProcessOp.BootHandleNext`, one `{tag, kind, handle}` record per call (§12). The
entry ABI is fixed and the furnishing is open-ended.

**API ownership: the vDSO discipline.** The `sos` module is U-mode library
code (never kernel-mode), but it is owned and exported by the kernel package:
syscall and method numbers are not ABI. They live in one kernel-internal
constants package (`kernel/abi/`) shared by the dispatch tables and the
wrappers (`kernel/sysapi/`), so the two can never skew and the kernel may
renumber freely; userspace links the exported module and never sees a raw
number. Delivery is static linking in v1 (the manifest/module-path
mechanism); a true mapped vDSO would change nothing about the shape and is not
built. The kernel package also exports a C-ABI surface for non-Saw languages
(`@export`, whitelist-clean — handles, statuses and ops are integers): per-op
functions (`sos_system_shutdown(handle, status)`) as the supported C
interface, over fixed-arity raw forms (`sos_syscall1(h, op, a)` — no varargs
across the trap boundary), over the per-arch HAL `ecall`/`svc` stub. One
implementation chain, three entry altitudes: typed Saw, typed C, raw.

**The discipline holds for Saw and does not yet hold for C, and the reason is
structural.** The typed-C altitude is real — the per-op `@export`s exist, are
linked, and run on every boot — but they are compiled from the `sos` Saw module,
which depends on `sosrt`, so reaching them makes an image a Saw image with a C
`main` in it, at the price of the runtime and an arena. A genuinely non-Saw
process therefore writes op numbers itself: `tests/c-child/sos.h` copies them
out of `kernel/abi/`, a module whose own header calls itself kernel-internal, on
`tests/riscv32/payload_sosimg.S`'s precedent. **So for a C image an op number is
ABI**, and a renumbering that costs a Saw caller a rebuild would cost that image
correctness. The claim is scoped to the altitude that can honour it. What is
missing is an `sos.h` this tree owns and keeps in step with
`kernel/abi/src/ops.saw`, and a way to link the exported floor without the Saw
runtime behind it (a `sos` build profile carrying the floor alone, or generated
numbers). The C-ABI surface itself is correct; it is the linkability, not the
exports, that is missing.

**Two copy funnels are the only places the kernel touches user memory.**
`copy_out` (§2.2 — the wait record and every answer wider than the registers,
including the boot-set record) and its mirror `copy_in` (`Timer.Arm` and the
message records) are the only code in the kernel that dereferences a user
address. Each validates alignment and containment and terminates the process
on a violation, on the faults ruling above; the asymmetry between their
windows is deliberate and is stated at §2.2. That keeps "where does the kernel
read user memory" a two-site audit rather than a property of the whole
dispatch table, and keeps a per-address-space mapping switch a one-place
change. A handle word arriving through either is data until dispatch resolves
it against the caller's table, which is the rule every op follows for a handle
in a register.

**Growing an op costs no register and no ABI.** Because the numbers are not ABI,
a new op takes the next value in its own table and displaces nothing, and each
table's `< MINT_OP` assert sits on its highest case — one assert per table, on
its top — which keeps a growing table from ever reaching a universal op number.
An op's argument list may grow the same way, up to the three argument registers:
the raw form is fixed-arity, so a grown argument rides a register the raw form
already passes, and the convention does not move. An op that needs more than
three argument words passes an argument record in memory through the copy-in
funnel (the pipe send records below are the example). An op answers one value
word (`a1`/`x1`); an answer wider than that goes through the copy-out funnel.
And a contract may change where a number may not: an op can keep its number,
right and record while its semantics change, because nothing a Saw program links
against is a number (a C image is the exception stated above) — the vDSO
discipline read at the semantics rather than at the numbering. Surface is not
ops: a typed-tier change (such as `take` and `post` answering `WouldBlock`
through the error channel rather than an Optional) needs no dispatch arm at all.

**Declaration order is layout.** Records the ABI publishes are laid out in
declaration order. §2.2's wait record is the worked example: `sosabi` publishes
its region offsets and `static_assert`s them.

**`WaiterOp.Add` takes a target, a key and a flags word.** Bit 0 detaches the
attachment at delivery (`AttachMode.OneShot`), bit 1 releases the caller's
handle entry into the attachment's keeping (`Waiter.give`). **A flags word
outside the published set is a fault, not a status**: the two bits are
published in the module userspace links against, so a word carrying any other
is a mistake the caller could have checked. The typed surface cannot spell
one at all — `AttachMode` has two cases and the consuming half is a distinct
verb — so the four legal combinations are the only things a Saw caller can ask
for, and the kernel's check exists for the C altitude beneath it. One target
kind narrows them: an Interrupt takes only the two borrowing combinations, and
the consuming bit on an Interrupt target is the same `BadArg` fault (§2.2).
Not yet enforced: `Waiter.give(interrupt:)` spells the consuming combination,
and the kernel accepts it.

**The fused pipe ops add no right.** `PipeInletOp.Call` spends
`PipeInletRight.Post` and `PipeRequestOp.ReplyWait` spends
`PipeRequestRight.Reply`, because each does what its neighbour does and adds a
park — and a park is mechanics, not authority. `ReplyWait` additionally spends
`WaiterRight.Wait` on the waiter it is handed, which is the
two-rights-on-two-objects shape `MemoryOp.Map` has and for the same reason:
holding an obligation must not imply authority over somebody else's Waiter.
`PipeReplyOp.Ready` spends `PipeReplyRight.Wait`, because asking whether a
level is up is the same question attaching to a Waiter asks, and a second bit
would let a caller poll a readiness it may not be woken by — the
`ProcessRight.Wait` precedent.

**Pipe messages travel in records.** A message is a body address, a length
and up to `PIPE_MSG_HANDLES` handle words, which do not fit three registers on
any profile. So `Post`, `Call`, `Reply` and `ReplyWait` each take one argument
record through the copy-in funnel, and `Take` and `Resolve` answer through one
copy-out record:

- `Call` is (argument record, its length, reply buffer); the reply buffer
  carries no capacity of its own — it is checked against
  `pipe_msg_record_bytes()` of the caller's real memory, a stricter test than a
  number the caller types. (`Take`, by contrast, takes a capacity and checks it
  against `pipe_take_record_bytes()`.) `ReplyWait` has four things to say — a
  waiter, a reply body, its length, and where the delivery goes — so the small
  words travel through the copy-in funnel and a register carries the wait
  buffer. `ReplyWait`'s record contains the send record it shares with `Reply`,
  because the fused op is the composition and its arguments are the composed
  ones: one layout, declared once, read at two arities.
- **The copy-in funnel has an early-check twin.** A `Call` that meets a full
  ring parks with its message still in the caller's memory, so the copy
  happens at the resume, in whichever context freed the slot. So
  `copy_in_check` validates at the call, while the caller is still the running
  process and a refusal can still terminate it, and the later copy cannot
  fail. The audit stays two sites: it is the same door, asked its
  question a moment earlier.
- **Validation happens before anything is unbound.** A send's handle words are
  resolved, checked for `Transfer`, and checked against the kinds a message may
  carry, all before the ring slot is touched — and the unbinds that take the
  entries away from the sender are the last thing a submission op does, after
  the body copy and the claim mint have both succeeded. That ordering makes the
  batch atomic without an unwind: every step from the first unbind onward is
  infallible, so a half-staged message is unrepresentable rather than merely
  unlikely. A caller error ends the caller by the faults ruling. One path asks
  twice, and it has to: a `Call` that met a full ring re-reads its argument
  record at the resume, since a sibling thread may have rewritten it. The three
  questions are asked again there, and a slot that fails one simply arrives
  empty — there is no honest fault to raise at a resume, because the caller is
  not the running process.
- **`Resolve` parks rather than answering `WouldBlock`**: a pending exchange is
  not a status it returns.

### 5.8 One-way send

Decided by §2.4: there is no `send_oneway`; the accumulating Event is the
payload-free one-way primitive, and the TELL idiom (§2.1) carries a payload.

### 5.9 Memory: the RAM/device distinction and the region refcount

Decided by §2.5's typed pools: RAM is `Memory`, device MMIO is `IoMemory`. The
per-physical-region refcount, and its reconciliation with device regions that
are never freed:

- **The counts live on the region's own slab slot**: §2.5's "refcount invariant,
  as built".
- **There is no pool-side region descriptor**, and that is the answer rather
  than a shortcut: a pool is not a fourth object kind. Every region carries a
  `root` back-reference (a boot-minted region names itself; a split carries its
  parent's answer down), and the pool's ledger — free-list head, bytes
  outstanding, bytes dropped — lives on the root's own `MemorySlot`. Return is
  to the root, never to the intermediate cursor a piece was cut from, because
  the derivation chain is an artifact of front-cutting and not an ownership
  tree. Adjacent free ranges coalesce, and `split` serves from the free list
  before advancing the cursor (first fit).
- A release is infallible, so a range that finds no free-list node after
  coalescing is leaked and counted (`dropped`) rather than refused.
- **Byte accounting is that ledger**: `MemorySlot.out_bytes` (outstanding)
  beside `pool_free_bytes(root)` (available). It is the pool's business and not
  a per-process authority — quotas stay object-counted in v1 (§2.9) — and no op
  fronts it.
- **`IoMemory` is the carve-out.** `carve` leaves the parent whole, MMIO is
  pinned and never freed, and a device region's counts are therefore
  effectively pinned.

## 5b. Two machine profiles, two architectures

SOS targets two first-class profiles, both QEMU-runnable for a fast dev loop:

- **Profile A — MPU**: riscv32, QEMU `virt` first, ESP32-P4 hardware later (the
  P4 is this profile: M+U modes, PMP/APM, no per-process paging). Single
  multitask image, physical addresses, coarse protection, small-N domains.
  32-bit. M-mode kernel, U-mode processes, PMP with U-mode default-deny doing
  the work, so the kernel and the finisher are protected by never being granted,
  and a device such as the UART is reachable only through an `IoMemory` window a
  process was given (§2.5). QEMU runs it as
  `qemu-system-riscv32 -M virt -bios none` at RAM base 0x8000_0000, and the
  machine stops through `sifive_test` (0x5555 = exit 0).
- **Profile B — MMU**: arm64 (aarch64), QEMU `virt` (`-cpu cortex-a53`).
  Kernel at EL1, processes at EL0, real per-process page-table address
  spaces (§5.5). 64-bit. (arm64 moots the RISC-V S-mode/SBI question: QEMU
  enters EL1 directly; no firmware protocol is needed for M1-class work.)

Rationale: multi-arch and 32/64-bit awareness from day one. The
object/handle/pipe/syscall model is identical across profiles — divergence is
confined to a small per-arch HAL (boot, trap entry, context switch,
Mapping/AddressSpace implementation), selected at build time via the
module-path mechanism (`--module-path hal=hal/<target>/kernel` — Blade's
machinery). All wire and boot formats (sosimg, message headers) use fixed-width
fields so 32- and 64-bit profiles interoperate. AddressSpace on A = PMP region
set (+APM on the P4); on B = page-table root.

`make sos-test` boots the same kernel on both profiles and on the flat build
profile of §5b.1 — three gate runs — and any run failing is red.

- **The kernel has no architecture in it.** `kernel/core/` names no register,
  no trap cause, no protection hardware and no board; it reaches all of it
  through one module it imports as `hal`, mapped per build to the target's
  `hal/<target>/kernel/`. The harness scans that directory for architecture
  names and fails the run on a hit, comments included — because a leaked
  constant still compiles and is only wrong on the profile nobody happened to
  be building. The riscv32 HAL is split into an architecture half
  (`hal/riscv32-common/`) and per-board halves.
- **The HAL is the whole of the difference**, and it is small: boot +
  vectors, the privilege transition, the protection primitive, a console byte
  sink, a way to stop the machine, the trap-frame accessors, the interrupt and
  timer seams (§9, §2.6), and the board's memory map. Everything else —
  dispatch, rights, the loader's order of checks, the console's formatting — is
  shared, and `kernel/core/` is shared by every kernel image, which keeps them
  all on the same trap path.
- **The same root server sources build for both.** `root/src/` is unchanged
  between profiles; only its manifest's `[sos.<triple>]` section differs.
- **Images are arch-tagged**: a wrong-profile image is a clean load error,
  tested on both.
- **The two profiles report the same fault names.** ESR's exception class
  plus the data-abort direction bit decode to `store-access-fault` and
  friends, so the harness asserts one string against both machines.
- **Profile A's trap entry** uses `mscratch` as the mode witness (0 in the
  kernel, the running thread's frame address in U-mode), which splits a syscall
  from a kernel bug in one branch. A U-mode trap saves 31 GPRs + `mepc` into a
  32-word frame (word `i` holds `x<i>`; word 0 holds `mepc`). A kernel-mode trap
  is `kernel_fault`, which reports and fails the run rather than hanging.
- **Profile B facts:** FP/SIMD must be enabled at EL1 before any compiled code
  runs — `CPACR_EL1.FPEN` traps Advanced SIMD out of reset, and LLVM vectorizes
  ordinary loops. FP state is not saved across a trap (FP-in-userspace is in
  §11's plan). Semihosting (`SYS_EXIT`), not PSCI, stops the machine: PSCI
  `SYSTEM_OFF` always exits 0, and the harness asserts on exit status.
  Cortex-A53 is ARMv8.0 and has no LSE atomics; `ldxr`/`stxr` exclusives cover
  everything the kernel and `SpinLock` need.
- The kernel's hex output follows the target's word width.

### 5b.1 The protection tier, and the word the platform advertises

**A profile is an architecture-and-board; a tier is what its protection
hardware can do.** The two are independent, and the tier is a thing a program
can ask about (`SystemOp.TierGet`, §2.8):

| tier | what the hardware does | address story (§5.5) | `TierGet` answers |
|---|---|---|---|
| 1 — MMU | per-process translation; a peer's memory is not mapped at all | the kernel's answer — `map` places, one link base | `Isolated` |
| 2 — MPU | numbered protection regions replayed per switch; a peer's memory is mapped and denied | identity — the same number in every process | `Isolated` |
| 3 — flat | nothing; every access lands | identity | `Flat` |

| target | tier | in the gate? | notes |
|---|---|---|---|
| arm64 `virt` | 1 | yes — Profile B | per-process table sets, higher-half kernel, `map` places |
| riscv32 `virt` | 2 | yes — Profile A | PMP regions replayed through the one seam; keeps its per-child linker scripts, deliberately, as the living identity-tier demonstration |
| riscv32 `virt`, flat build profile | 3 | yes — a third run | a module swap over the same board: `prot_*` no-ops, PMP opened with one TOR entry, `prot_isolated()` false. No new toolchain, and the tier-3 story's test target |
| ESP32-C3 | 2, at the floor | no — a gating board smoke (`make sos-smoke-esp32c3`) | 16 PMP entries exactly, 400 KB SRAM; XIP is the entry price |
| ESP32-P4 | 2 | no — no QEMU machine yet | 32 PMP + 16 PMA, 768 KB; the first real tier-2 part |
| ESP32-S3-class | 3 | no | WORLD0/1 only; a `--target` port, not a new backend |

**Two words and not three.** `Isolated` and `Flat`; MMU-vs-MPU is not
advertised, because slot pricing and the `map` answer are already the visible
difference and a third word would invite tier-sniffing. A program that relies
on isolation checks the word once and refuses to run; everything else is
written against the one idiom set and never asks.

**What the word disclaims is protecting memory from a process — the kernel's
included.** On `Flat`, `map`'s contract — "make this accessible with at least
these rights" — is trivially true and succeeds honestly; what a flat platform
cannot do is the other half, `unmap`'s revocation and peer isolation. Its
user-mode protection range is read/write/execute over the whole address space,
so a process can read and write any address, the kernel's memory and the test
finisher included, and a process that writes kernel memory directly bypasses
every check (`SystemRight.Shutdown` included). **The checks are identical on the
syscall path**: handles, rights, generations, quotas and lifetimes are checked
on every syscall on all three tiers, so a W^X refusal, a missing-right fault and
a full-slab `NoResource` read the same on a machine that cannot deny a single
load. The isolated tiers enforce both halves. The gate shows the syscall-path
half: the object-model suite runs unchanged on all three gate runs (both
profiles and the flat build profile), because every case reaches the kernel
through syscalls, and the flat run excludes only the cases whose assertion is a
hardware denial, each marked isolated-only in the runner and printed with its
reason (for example `umode_access_fault`, `root_server_oversteps`,
`process_isolation`, `map_unmap` and `stats_region_ro`).

**The tier-2 floor: shape decided, number deferred.** A tier-2 part has few
enough protection slots that a mapping quota must be priced against them, and
TOR-costs-two / NAPOT-costs-one stays HAL-internal under a conservative quota,
so a budget never varies by allocation luck. `DEFAULT_QUOTA_MAPPINGS` is to be
priced at 6–8 when a real tier-2 board asks; no in-tree target consumes that
price yet, and a number nobody runs is a number nobody believes.

## 5c. The native floor

SOS is written in Saw, and the exceptions are enumerated rather than tolerated.
Every surviving line of C states, in the file that holds it, why it is not Saw —
and there are only two reasons in the whole system:

1. **An instruction with no Saw spelling.** `ecall` / `svc`, `mret` / `eret`,
   the vector tables, `csrw` / `msr` / `mrs`, `dsb` / `isb` / `tlbi`, `wfi`,
   the semihosting `hlt`, and the register pinning each of them needs (so, for
   example, `sos_syscall3` and `sos_wait_for_irq` per profile, and the
   interrupt-mask and timer system registers). Inline asm in Saw is a separate
   design conversation.
2. **What only the environment can supply.**
   *A linker symbol's address.* Saw cannot name one — `extern` declares only
   functions, an extern function is not usable as a value, and `@export` on a
   static emits a definition rather than a reference. The accessors are the
   kernel HALs' `sos_payload_start`/`_end` and
   `sos_region_table_start`/`_end`, and `rt/common_c/support.c`'s
   `sos_arena_base`/`sos_arena_size`: a package's arena size is a
   linker-script knob (`ARENA_SIZE`, a `PROVIDE`d symbol bounding a reserved
   span of `.bss`), and `sosrt` reads those bounds back at run time with the
   same `extern unsigned char sym[]` idiom. That is what lets a C image set
   `ARENA_SIZE = 0` and link no arena at all, and a small board scale the arena
   per package rather than per tree. *The word atomics.* On a target whose word
   compare-and-swap is no instruction (the ESP32-C3, riscv32 without the A
   extension) the language leaves the `__atomic_*_4` routines to the
   environment; elsewhere the compiler inlines atomic instructions and nothing
   references them.

The first half of reason 2 is a language gap: it closes the day Saw can name a
linker symbol.

The compiler's own libcalls are mostly not part of the floor. `memset` /
`memcpy` / `memmove` and the 64-bit division helpers come from sawc's std,
written in Saw, on every profile.

**Where the floor is.** `rt/common_c/support.c` — one copy, shared by the kernel
and every process — is the arena accessors and the word atomics. Each user HAL is
its syscall instruction and nothing else. The bump arena and the frozen
`__saw_rt_*` seams are Saw in `rt/common/src/lib.saw`, one copy serving the
kernel and every process; the process side's two hooks and its parked boot
handle are Saw in `kernel/sysapi/`, beside the System object whose authority
they spend. A thread context is built in Saw (`frame_init`, and a
`resume_frame` that branches into the trap entry's own restore path), so no
register-clearing prologue is needed in assembly.

**The panic path is check-free by construction.** The console writer the runtime
seams call is Saw, and it is check-free by construction rather than by
inspection: raw pointer reads, wrapping arithmetic, no indexing, no allocation.
That is what makes a panic raised inside the panic reporter unreachable instead
of merely unlikely: the whole call cone contains no bounds check, no overflow
trap and no call back into `__saw_rt_panic`. A harness case pins it on both
machines by taking a compiler-raised bounds check and asserting the message
arrives in three independent pieces. In the kernel and in root, the panic seam
writes its message to the console and then fails the run; it never hangs. A
child's panic ends in a `shutdown` request through its System handle: the launch
mask of §12 withholds `Shutdown`, so the child dies by `AccessDenied`, and a
child started with no tag received `NO_HANDLE` in its first argument register,
so its runtime has no System handle parked (even if the launcher gave it one),
prints nothing and dies by `BadHandle`. Sawlang's runtime ABI (`runtime/ABI.md`)
freezes `__saw_rt_panic` as `noreturn`; the Saw seam produces `Never` with a
conditionless `while { }` that has no `break`.

**Who declares the runtime.** `@export`ing a frozen `__saw_rt_*` name needs
the compile to say it implements the runtime ABI, so `tools/sos_runner.py`
passes `--runtime-provider` for kernel images (a kernel is not a Blade
package) and a process image carries `[package] runtime = true`. The seam
bodies arrive from a dependency in both cases, which is sound because the flag
describes the compile and a package build compiles its whole module graph into
one unit.

## 6. Explicitly not in the kernel

Drivers (userspace via Interrupt + MMIO MemoryObjects), filesystems,
network stacks, POSIX personality, dynamic linking/loading, package/
process management policy, time-of-day (a userspace service over the
Timer primitive). The kernel knows objects, handles, rights, threads,
memory words, and nothing else.

## 7. Scheduling

**What is built.** The kernel schedules one round-robin FIFO — no levels, no
ready bitmap — on a one-tick slice. The tick's period is kernel policy (§2.6);
the tick re-arms from now, so a late tick is one tick, never a burst of
catch-up. No shipping kernel image arms the tick: `kernel/main.saw` arms none,
and the harness kernels that test preemption arm their own.

- **The trap frame is the thread context**, so a context switch is `ktrap`
  returning a different frame than it was called with: one switch point, at the
  user-return boundary by construction, which enforces §9's
  no-interrupts-in-kernel-mode rule by shape rather than by promise, and leaves
  no half-saved state to protect. Root's first thread enters user mode through
  `hal.resume_frame`, called from `kcore.start_process`; after that a thread
  enters user mode when `ktrap` returns `pick_next`'s frame to the trap entry's
  restore path, and through `hal.resume_frame` again only from
  `resume_after_death`, after a teardown. Every thread belongs to a Process: a
  kernel that could enter user mode without a Process would have syscalls no
  handle table answers.
- **Protection is installed at the switch.** A process's rows are data in its
  body. On a replay tier (`hal.prot_replay_at_switch()` true: Profile A, which
  has a small fixed budget of numbered regions) the scheduler replays them when
  the incoming thread's process differs from the installed domain. On Profile B
  each process holds a persistent translation-table set, brought up to date when
  its grant record is edited, and the switch is one table-base write. A
  same-process switch reloads nothing.
- **Nothing runnable is idle or deadlock.** The kernel idles iff something
  outside the thread set can still wake somebody — a bound interrupt line or an
  armed Timer (an unarmed Timer is not one: it will never fire) — and reports
  deadlock otherwise (§9).

**Not built: priorities.** The priority map is a loader artifact: the loader
parses the image's priority field and reports it, no process stores it, no
syscall carries a band tag, and there is therefore no enforcement point.
`SystemOp.ProcessCreate` exists (§8) and takes no priority map. The rest of
this section specifies the scheduler that is not yet built.

- **Fixed-priority preemptive, 8 system levels (0–7).** Ready queue =
  per-level FIFO + a one-byte ready bitmap; pick-next = find-first-set +
  pop, O(1), a handful of instructions on both profiles. Idle is not
  a level — a per-profile WFI loop runs when the bitmap is empty.
- **Round-robin within a level**, fixed timeslice (provisionally 10 ms), tick
  from the per-profile timer (CLINT mtimer / ARM generic timer — the same source
  the Timer object uses). Preemption is immediate: readying a higher-priority
  thread switches on the way out of the kernel; same-priority never preempts
  mid-slice.
- **Priority bands, not raw levels.** Processes never see or name
  system levels. The only priority surface is
  `enum Priority { Background, Low, Normal, High }` in the userspace
  `sos` module, with fixed ABI tags 0–3 (the wire representation never
  changes). The thread-spawn syscall carries the band tag; no syscall
  accepts a raw system level.
- **Per-process band→level map, kernel-side.** Each Process object
  holds a 4-slot map resolving band → system level; the kernel
  resolves at thread-spawn — the map is the enforcement point (a
  process cannot escape its band with raw syscalls). A thread's
  system priority is resolved once at spawn and stored plain — the
  scheduler hot path is numeric-only. Initial default map:
  `Background→0, Low/Normal/High→1` (apps cluster at 0–1; levels 2–7
  are headroom for drivers/services). Low/Normal/High are declarative
  until a deployment differentiates them — intended.
- **The map comes from build-time metadata + launcher policy.** The process
  image declares its requested map in `sosimg` metadata (a 4-byte header field;
  Blade emits it from the package manifest, e.g.
  `[sos] priorities = { background = 0, low = 1, normal = 1, high = 1 }` in
  Saw.toml). Metadata is a request, not authority: the launcher reads it and may
  honor, clamp, or override; the kernel stores whatever map the launcher passes
  to `process_create` and never parses metadata for policy. The root server's
  own map is applied verbatim from its image at boot (the kernel parses that
  image anyway; root is trusted by construction).
- **Launching is a capability.** `process_create` requires the launch
  capability, `SystemRight.ProcessCreate`, minted at boot to the root
  server. Ordinary processes cannot create processes in v1 — they ask the
  launcher service over a pipe.
- **The map is immutable after creation.** No remap syscall, no
  self-modification, no visibility into the map from inside the
  process (each process sees only its 4 named bands). Changing a
  running process's priority = restart it. (A future dynamic
  re-prioritization design could add a launch-gated syscall without
  disturbing anything here.)
- **No priority inheritance.** Inheritance chains through transferable
  PipeReplyHandles are ill-defined (the reply obligation migrates). The
  mitigations: (a) the convention that servers run at ≥ the max priority of
  their clients (the launcher assigns both, so this is enforceable policy); (b)
  the **direct-switch fastpath** — when a post or a reply readies a parked
  receiver (a server parked on a Waiter its outlet is attached to, or a client
  parked in a fused `Call`), switch straight to it when runnable, which removes
  most incidental inversion with no donation semantics. MCS-style
  budgets/inheritance remain possible later designs.
- **Uniprocessor kernel in v1**, both profiles (the P4 is dual-core and
  QEMU can do SMP; SMP is its own future design — locking model,
  per-core queues — and nothing above precludes it).

## 8. Thread & process lifecycle

**Two phases, inert until started.** `SystemOp.ProcessCreate` (on the caller's
System handle, gated by `SystemRight.ProcessCreate` — processes are minted by
the System, §12) takes two Memory regions — a read-only image blob and the
child's destination RAM — and returns a process with an address space, a
recorded entry and stack, and no thread. `ProcessOp.Start` (on the child's
handle, gated by `ProcessRight.Start`) mints the first thread at that recorded
entry and makes it runnable; the caller keeps running. The ordering is the
soundness argument for the launch flow: whatever a launcher gives a child must
be complete at its first instruction, and the start call is the barrier that
makes a half-populated table unrepresentable — with no synchronization
invented. **A second `start` is a `BadState` fault**: broken code, and the
caller is the one that started it the first time. **A malformed image is a
`BadImage` status, not a fault** — the one caller-supplied thing that does not
end the caller, because image bytes are data a launcher was handed rather than
something it wrote (the `from(raw:)` precedent).

**The fault rule, and how teardown ends.** A thread fault kills its process and
the teardown runs unconditionally; what happens after forks. Process 0 — root,
the one image the kernel loads (§12) — stops the machine. **Any other process
reschedules**: its threads are removed from the ready queue (leaving everybody
else's), the objects bound to it are invalidated, its handles are released, its
protection rows go back and its body is freed (§2.10, which marks what of that
order is not yet enforced), and the path ends in the scheduler rather than in a
stopped machine. Children die with the machine when root ends, uncounted. A
supervisor may read `get_status` through the handle it holds — which outlives
the child, because the teardown closes the handles in the dead process's table
and not in its creator's — or be woken by the death (below). The teardown
(`end_process`) lives in the scheduler module beside `pick_next`, because the
reschedule can idle, idling delivers, and delivery reaches back down into the
copy doors; so the copy doors report a bad buffer, and the callers above the
switch point terminate on it.

**`process.kill()` has neither op nor right** (`Exit` through another
process's handle faults the caller, §2.7). It is specified below; what it
waits on is a ruling about what killing a process that is not the caller does
to a scheduler that may be running its thread.

**What is built.** The fault rule, whole. `get_status` as
`ProcessOp.GetStatus`, gated on `ProcessRight.Wait`, answering the one
fixed-width word (kind in the high bits — `Running` beside the three below —
code in the low). Thread `Start`/`Join`/`Exit`/`Yield`: a joiner parks on the
target's list and is answered exactly once through the funnel §2.2 describes.
Process waitability (below). Not built: a thread handle is not waitable —
attaching one is a `NotWaitable` fault, and it is the last kind for which that
arm is a scope line rather than a statement about the object.

**The status word has a neighbour.** `get_status` answers whether and how a
process ended; `ProcessOp.Stats` answers what it cost, in traps, split by cause
class (§2.7). The two are separate rights on purpose — `ProcessRight.Wait` and
`ProcessRight.Stats` — so a supervisor that may learn a child died need not be
able to watch it work. Both survive the death, in the process record (§2.7).
The status word is written there at the death and the final trap counters are
copied there as the teardown frees the body, and the `Gone` record lives while
some reference names it (a handle entry, including a minted sibling's entry, or
an attachment). Those holders are the only readers there can be, so the record
is reclaimed when the last of them goes, however it goes, though never before
its own teardown has ended (§2.7). A mapped stats-region reader can also see the
dead process's counters in its body's row until a create reuses the body.

- **A thread fault kills its process.** The process exits with a
  fault status; there is no per-thread fault recovery (a faulted
  thread shares mutable state with its process — "keep running minus
  one thread" is silent corruption). Kernel teardown-on-exit/fault
  runs unconditionally (§2.10).
- **No thread kill.** Threads are expected to be pool workers: created at
  startup, draining task queues, dying at process exit. Work completion is a
  task-level concept (channels/Events), not a thread concept. A
  wedged/hostile thread is a process-level problem by the fault rule.
- **Thread handles are waitables** (level-triggered ready when the
  thread has fully exited) — the observability that join traditionally
  provides, needed only for safe stack reclamation when a dynamic pool
  scales down (thread stacks are userspace memory passed to
  `thread_create`; reuse before real exit is a use-after-free). v1
  fixed pools never use it. Not built.
- **Process handles are waitables** (ready on exit, any cause) — the
  primitive userspace supervision parks a Waiter on, and the existing Process
  object being waitable rather than a new kind (§2.7). §2.2's
  `waiter.add(handle, key)` takes a Process handle on `ProcessRight.Wait` —
  the same bit `get_status` spends, because being woken by a death and asking
  about one are the same question at two tempos. Readiness is the record being
  `Gone` whatever ended it, and the payload the wait record carries is exactly
  the status word the next bullet defines (`WaitTag.Process`,
  `WaitPayload.Process(status:)`). It adds no vocabulary to this section; it
  adds a second door onto its word. **Death is a terminal level**: the
  delivery consumes nothing, so a waiter attaching after the death still wakes
  and a second wait answers the same word — which is what makes supervision
  race-free, since there is no window in which a death can be missed. The
  attachment counts as a reference on the process record (§2's counted-kinds
  table), so a watched dead process's record survives until the watcher
  detaches. **The ordering:** the kernel notifies once the status word is
  recorded and before the teardown runs, but a notification only queues a
  thread, so the woken supervisor runs after the teardown has completed and no
  observer ever sees a half-dead process. The deadlock predicate has no arm
  for it: a death arrives from inside the thread set, so a supervisor parked on
  a live child is not idle-and-doomed — the child's own threads are runnable —
  and one parked on a dead child was already woken.
- **`process.get_status()`**, gated on the wait right: one fixed-width status
  word — kind in the high bits (`Exited | Faulted | Killed`), code in the low
  bits (exit code, or a fault-cause tag). The kernel's fault report prints the
  faulting PC and address on the console (`epc=`, `tval=`); fault forensics a
  supervisor can read is a later design.
- **`process.kill()`**, gated on a kill right. Cooperative-first
  teardown stays the norm; kill is the capability-gated escape hatch
  that makes userspace supervision real against wedged/hostile
  processes. This answers §4's kill-tree question: kill is
  process-granular only, with no kernel trees — supervision topology is
  userspace's. Not built.

## 9. Interrupt delivery

- **Mask-on-fire, ack-to-rearm.** IRQ fires → kernel masks the line +
  marks the Interrupt object ready (wakes per Waiter rules). At most
  one unacked fire exists per Interrupt, ever. `irq.ack()` unmasks;
  a still/again-asserting device re-fires — correct level semantics.
  Level-triggered Waiter readiness means no interleaving loses a
  fire (readiness persists until consumed).
- **Ack is a release.** The discipline: everything that touches device
  registers happens before ack; post-ack code runs only on data
  already extracted. Under it, a second worker entering the pre-ack
  section while the first runs its post-ack tail is pipelining (a
  throughput feature), not a race. Drivers that need post-ack register
  work serialize with a Mutex — their choice; the kernel does not
  enforce single-servicing.
- **v1 canonical driver shape: one task owns one Interrupt.** A single
  cooperative servicer can never race itself — the ack-position
  question evaporates. Multi-threads-on-one-Waiter is for servers
  multiplexing independent streams, not for a device IRQ.
- **The serve idiom needs no kernel support**: a `sos`-module closure
  wrapper that acks on handler exit gets non-reentrancy directly from
  mask-until-ack (the line is masked for the whole handler body — the
  "mask while handler runs" mechanism is the interrupt mask). An
  explicit mid-handler ack is the deliberate opt-in to pipelining.
- **Combined form (provisional): `waiter.wait(ack: irq_handle)`** — atomically
  ack, then block. Pure syscall-halving for the hot loop (one syscall per
  interrupt); not needed for correctness (level-triggering already covers the
  gap). One optional arg on wait. Not built: drivers run the two calls
  separately.
- **The wait answer carries the line** — `WaitPayload.Interrupt(line:)`
  through §2.2's copy-out record. There is no fire count, because the
  mask-on-fire rule makes it always one; a dispatcher parked on several lines
  gets which one without a side table, exactly as it gets the key.
- **An ack with no fire outstanding is a fault**, not a tolerated no-op. The
  only way to learn about a fire is to be told about one, so an extra ack is a
  servicer that will ack the next fire without servicing it — a dropped byte
  or a wedged device with no first symptom.
- **There is no mask op and no mask right.** Masking is the kernel's half of
  the cycle; a driver that could mask at will could leave a line masked, which
  is a denial of the device with no diagnostic. What a driver controls is its
  own device's interrupt-enable register, through the window it was granted.
- **An interrupt line is readiness from outside the thread set**, so a bound
  line keeps "nothing runnable" from being a deadlock (§7). The kernel idles
  by parking the core and polling rather than by taking the trap.
- **The interrupt seam.** Each HAL exposes a small set of names and the kernel
  reaches interrupts through those and nothing else — among them `intc_init`,
  `is_interrupt`, `irq_line_valid`, `irq_claim`/`irq_complete`,
  `irq_mask`/`irq_unmask`, `irq_poll`, `wait_for_irq`, the timer seam of §2.6,
  and a selftest line. `ktrap` decides interrupt / fault / syscall in that order
  — an interrupt's instruction has not run, so it must never reach the syscall
  return path that steps the saved PC — and `service_irq` is the one arch-free
  entry: claim, rearm or mask, run the hook, complete.

**The hardware never delivers an interrupt in kernel mode; the kernel asks,
at named points.** The first half is enforced by both machines — an interrupt
arriving in kernel mode is each HAL's kernel-bug path, which stays a live
tripwire rather than becoming a delivery path. Delivery has three entry
points: the trap path from user mode, the idle poll, and a preemption point
inside a long kernel operation, which polls through the same `irq_poll` and
the same delivery funnel. No new seam, no brief unmask, no nested trap frame.

- **A point interrupts latency, not atomicity.** A reschedule the delivery
  signals is honored where it always is, at the user-return boundary; the
  operation still completes before the switch it may have motivated.
- **The placement rule is one reviewable sentence per site**: a point is
  legal where no in-flight invariant spans state the IRQ path touches. Every
  kernel-mode loop carries its placement verdict as a comment at its site.
- **One guard backs the rule mechanically** — a kernel flag set around the
  delivery funnel's body and checked first at every point, so a point reached
  from inside delivery is inert. It exists because the byte movers are shared:
  the loader's segment placement is preemptible boot context, and §2.2's
  record copy-out runs in IRQ context.
- **The mechanism** is `preempt_point()` beside the idle poll, that guard, and a
  `PREEMPT_STRIDE` stride on the long-op movers every bulk copy goes through. A
  point delivers, so it sits above the byte loops it drives in the module order
  while delivery reaches back down to them; that cycle is why the cadence is its
  own module (`kcore.preempt`) rather than a point written inside `copy_bytes`.
- **What takes no point**: the console write loops and the UART ready spin
  (device-drain-bounded; a long diagnostic line is masked latency, accepted),
  the controller and grant walks (half-configured hardware is IRQ-shared state
  in flight), the bounded slab scans, and process teardown (the scheduler is
  dismantled across it).

**A driver is a child process.** Every rule above holds; what the tree adds is
who the driver is.

- **A driver's device arrives from another process.** Root drains the
  console's register page as an `IoMemory`, `give`s it to a child under a tag
  (spending the universal `Transfer` bit on the window and `ProcessRight.Give`
  on the child), and the child maps it into itself with
  `Process.map(iomemory:)`. What crosses the boundary is authority over a
  device, and where the row goes is then the holder's own business. The line
  crosses the same way: root binds it (`SystemOp.InterruptBind`, §2.8) and
  gives the bound Interrupt under a tag, so the driver holds that line and no
  authority to bind another. Root never maps the window and never learns the
  UART's address. Not yet enforced: the driver children bind their own lines
  through `ProcessRight.InterruptBind` on the Process handle they derive.
- **The driver program is the same wherever it runs, apart from three things.**
  The child driver packages are the root-as-driver packages, driver body for
  driver body — same registers, same enable bit, same drain-before-ack loop —
  differing only in that the window and the line arrive by give (the line not
  yet, above), that they end with `Process.exit` (a driver child holds no
  `SystemRight.Shutdown` and should not), and that they are linked at the child
  base. Both twins stay in the suite. Each driver comes in two packages, one per
  device rather than per machine, because a driver is its device.
- **The rights a driver spends on itself are the driver's to narrow.**
  `ProcessRight.Map` arrives on the handle `SystemOp.ProcessSelf` mints, and
  `ProcessSelf` takes a keep mask (§2.8), so a child narrows its own Process
  handle at the moment it derives it; `Give` takes the same mask in the same
  shape, so attenuate-at-give and attenuate-at-derive are one rule spelled
  twice. The intersection is fail-closed in both places (§3): a bit the source
  lacks is silently ignored, because a request for authority that does not exist
  is answered by not granting it. A launcher still cannot reach into the handle
  its child derives — only the child can narrow that one (`tests/child-narrow`).
- **The supervisor parks on the death, not on a deadline.** A driver waits on
  a real serial port, so a timer would have to be guessed at — and §9a's one
  bounded exception to the console handover is a kernel whose armed tick
  narrates over a process that owns the device. The Process waitable (§8) is
  what lets a launcher arm nothing.

**A driver is a service.** Two other processes read and write the console
through a pipe protocol (§2.1) and hold no device authority at all.

- **The device half of this section is untouched.** The service driver is the
  child driver with a server face: same register block, same `listen`, same
  drain, same ack-is-a-release ordering — everything this section specifies
  still happens in exactly one process. What is new sits entirely above the
  line: where the bytes go after the drain, and where a write comes from.
- **The interrupt is one arm of a `wait`, beside the messages.** A service
  driver attaches its Interrupt and its `PipeOutlet` to one Waiter, so a
  device event and a client request arrive through the same door and are told
  apart by the record's tag. §2.2's aggregator needs nothing added for it.
- **A read the device cannot answer yet is a held obligation** rather than a
  poll, a queue of its own, or a kernel notion of blocking I/O. §2.1's
  one-shot is the driver's only per-request state, and dropping one is how a
  client is told the answer will never come.
- **The driver still owns the console alone**, which keeps §9a true with
  several processes in the machine: the clients write through the protocol,
  so exactly one address space names the transmitter and the handover
  protocol's single-owner rule is unweakened by the number of writers.

### 9a. The console handover protocol

Both v1 machines have one console UART, and both the kernel and a driver
process can drive it. Unstated, that is a flaky-test mystery — two writers on
one device interleave mid-word and neither is wrong.

- **Boot**: the kernel owns the device outright and narrates.
- **Handover**: entering user mode writes one marker line, and it is the
  last thing the kernel says on its own initiative. After it, the process
  owns the device.
- **Reclaim, on diagnostics only**: a fault report, a process teardown, a
  panic, or an interrupt on a line nothing is bound to. Interleaving
  there is acceptable because there is no longer a transcript to protect.

A `SystemOp.DebugPrint` is not a violation: that is the process asking
the kernel to place a byte, and the process is the one sequencing it.
What the protocol forbids is the kernel narrating over a process that is
using the device. The one bounded exception is a kernel that arms a
timer, whose first few ticks are narrated after handover — which is why a
case whose transcript matters after handover arms none.

## 9b. Kernel sync primitives: IntrSpinLock

- **std owns `SpinLock<T>`**: safe cross-context lock, any environment with
  real atomics, userspace included. **The kernel owns `IntrSpinLock<T>`** —
  SpinLock composed with interrupt masking, which is privileged (mstatus.MIE /
  DAIF) and therefore cannot exist in userspace even in principle.
- Semantics (the spin_lock_irqsave discipline): `lock` saves current
  interrupt state and disables before spinning (an ISR must not arrive
  mid-spin and deadlock on the same core); the epilogue restores, never
  blindly re-enables — nesting works, irqs-off callers stay irqs-off.
  Saved flags are per-acquisition, carried by the closure scope. The
  body inherits `sync` and carries the latency contract: irqs-off time
  is interrupt latency — short sections only.
- Mechanism: two per-arch HAL one-liners, `hal_irq_save() -> Flags` /
  `hal_irq_restore(Flags)`, under `hal/<arch>/kernel/`. The CAS
  stays even on uniprocessor (SMP-future correctness; under `+a` it is
  cheap; a failed acquire on uniprocessor signals reentrancy — panic,
  never spin forever).
- **Not built, and correctly so.** Interrupts are taken from user mode only,
  enforced by both machines, and the idle path polls rather than taking the
  trap (§9), so the kernel holds no state an ISR could interrupt. Kernel
  interruptibility is explicit preemption points, and a point is the
  assertion that state is consistent, so it needs no lock — what it needs is a
  placement sentence per site plus one reentrancy guard for the code the two
  contexts share. SMP is what will force the lock (§11), and the point
  placements are a real map of where it must go.

## 10. Userspace runtime: HandlerGroup + the wake bridge

Not built: `HandlerGroup` is userspace runtime work rather than kernel
surface, and a process wires a Waiter by hand. What follows specifies it.

Saw's sequential surface stays as it is: `pipe.send()`, `receive()`,
`read()` suspend in place on plain tasks — colorless straight-line
code stays the substrate, and TaskGroup remains exactly what it is
(a lifetime/join scope for tasks on a thread pool). The
event-driven edge of a process gets a second, distinct construct:

- **`HandlerGroup`** — a group of waiting handles running on a task
  pool; the userspace face of the kernel Waiter, one-to-one (TaskGroup
  ↔ threads/tasks; HandlerGroup ↔ Waiter/handles). Deliberately not
  bolted onto TaskGroup: attachments are persistent subscriptions by
  default (removed, never "joined" — §2.2's `AttachMode.OneShot` is the
  opt-in exception), and K workers service M handles (no
  frame-per-handle).
  ```saw
  var dispatch = HandlerGroup(workers: 2)
  let id  = dispatch.add(move timer)  { t, fired -> ... }
  let cid = dispatch.add(move server) { ch, msg, req -> ... }
  let timer = dispatch.remove(id)     // coat check: ownership back
  // Deinit: detach all, close unclaimed handles, cancel workers
  ```
- **Ownership: move-in, forced by the language.** Handles are NoCopy
  and references are non-escaping — a HandlerGroup cannot store
  `&Timer`, so group-owns-while-attached is the only representable
  design, exactly mirroring unique kernel handles. `add` returns a
  distinct-typed `AttachmentId` (not authority — meaningless without
  the group). `remove(id) -> Box<any Waitable>?` returns ownership
  (recover the concrete type with `take<T>()`; typed sugar can come
  later); None = stale id.
- **Per-attachment non-reentrancy, guaranteed**: one handle's handler
  never runs concurrently with itself (in-service flag; readiness
  arriving mid-handler is deferred and re-dispatched on completion).
  Generalizes the one-task-per-Interrupt decision to every handle
  kind as a stated guarantee.
- **The handler borrows the source per invocation** (`&Source`, a
  scoped non-escaping lend — the `with_ref` shape), which is sound
  precisely because of non-reentrancy: at most one borrow live per
  attachment. Per-kind signatures: Timer → `(&Timer, fired)`;
  Pipe → `(&Pipe, msg, PipeRequestHandle)` — the PipeRequestHandle is
  fresh per message and moves in (forwardable, per §2.1 delegation);
  Interrupt → `(&Interrupt)` with ack-on-exit default (§9).
- **Cross-handle parallelism up to `workers`.** Parallelism within one source
  stays explicit user code (spawn from the handler body into a TaskGroup) — the
  concurrency decision is visible, never implicit in a dispatch engine. Handler
  bodies are ordinary task code and may suspend; a suspended handler parks its
  worker, others keep dispatching. Backpressure is free: all workers busy →
  readiness sits (level-triggered) and the connection's staging ring fills; a
  `post` to a full ring answers `WouldBlock`, and the fused `send`
  (`PipeInletOp.Call`) parks on the inlet's room-to-post level (§2.1). Nothing
  drops.
- **The wake bridge is one mechanism**: a parked task's wake-word is
  the Waiter key supplied at attach; kernel readiness returns the key;
  the executor marks that task runnable. Handlers, blocking-shaped
  calls, and raw waits all ride the same path (this is what §2.2's
  word-sized key was designed for). Implementation freedom (runtime
  design, not spec): whether a HandlerGroup owns its own kernel Waiter
  or registers through the process runtime's reactor Waiter — the
  kernel permits both (one Waiter per object; many Waiters per
  process).

## 11. What is not built, known limits, and the plan

Built features are specified in their sections. This section lists what is
not built, the limits that are deliberate or known, and the plan.

**Settled implementation pins.** The rights-word bit assignments and the
per-object op tables are concrete and kernel-internal (`kernel/abi/`, one
package shared by the dispatch and the wrappers — §5.7's vDSO discipline is
what keeps them renumberable without a userspace edit). The per-profile kernel
memory layout is the linker scripts under `hal/<arch>/kernel/` over per-type
static slabs. The `sosimg` header is concrete, the §7 priority-map field
included, beside the build-emitted region table and its kind column (§5.6,
§12).

**Open pin:** the §7 priority map, and root's bootstrap band map, which the
loader parses and reports while no process stores either.

**Not built**, one area per line, with the section that specifies it:

- **Priorities and the band map** (§7). The scheduler is one round-robin FIFO.
- **Thread waitability, and `kill`** (§8). Attaching a thread is a `NotWaitable`
  fault; `kill` has no op and no right. A supervisor learns a child died by
  reading `get_status` through the handle it holds, or by being woken through an
  attachment on the same handle.
- **`IntrSpinLock` and SMP** (§9b, §7). A preemption point is the assertion
  that state is consistent, so nothing yet has a critical section for the type
  to protect.
- **`HandlerGroup`** (§10): userspace runtime work; a process wires a Waiter by
  hand.
- **The combined wait-and-ack**, `waiter.wait(ack:)` (§9).
- **DMA confinement — the userspace IOMMU driver, and critical processes**
  (§2.5). On the isolated tiers a `map` fences a driver's CPU and not its
  device's bus mastering, so a DMA-capable device's driver is a TCB member until
  an IOMMU exists. Process-death notifications, the first prerequisite, are
  built.
- **The mapped vDSO** (§5.7): delivery is static linking, which changes
  nothing about the shape.
- **The C leg of the vDSO discipline** (§5.7): an `sos.h` the tree owns and
  keeps in step with `kernel/abi/src/ops.saw`, and a way to link the exported
  floor without the Saw runtime. Until then, for a C image an op number is ABI.
- **Close** (§3): drop-is-release is the close for every existing kind;
  whether a later kind needs a close op is open. Per-kind release rights are
  not built.
- **The namespace and its path-attached acceptor object** (§2.1), and the v1
  bootstrap protocol convention (§12). A client finds a service by the boot
  tag its launcher chose.
- **`Process` and `System` handles in messages** (§2.1): a sysapi module split.
- **Virtual clocks**, and clock types beyond `Monotonic` (§2.6).
- **Stats:** a per-row identity column in the stats region, or an op resolving
  a Process handle to its row (§2.7); per-thread stats.
- **Fault forensics a supervisor can read** (the faulting PC and address reach
  only the console's fault report) (§8).
- **Retiring the image-declared device grant** (`SegFlag.Device` and the
  loader's `allow_device: true` door, dormant) (§2.5): its emitter is
  sawlang-side, so deleting it is a toolchain-pin event.
- **The tier-2 mapping quota price** (`DEFAULT_QUOTA_MAPPINGS`, §5b.1).
- **FP in userspace**: FP/SIMD state is not saved across a trap (§5b).

**Known limits.**

- The Process creation ops do not yet place their handles in the caller's
  table, and a caller error in `ThreadCreate`, `EventCreate` or
  `ProcessOp.InterruptBind` through another process's handle faults the named
  process; `ThreadSelf` and `BootHandleNext` are not yet self-only (§2.7).
- The object lifecycle is not yet enforced as §2.10 states it: Thread slots
  are not counted; the teardown's close-all releases every handle but counts
  without freeing, and the owner sweeps after it free the dying process's
  objects outright instead of invalidating the bound kinds first and leaving
  the shared ones to their counts; and an op on an invalid Mapping or a
  `Gone` Process does not answer `Revoked`.
- Binding an interrupt line is not yet a System op: it is
  `ProcessOp.InterruptBind` on `ProcessRight.InterruptBind`, charged to an
  `Interrupt` quota row, minting into the named process's table and faulting
  that process for a bad or bound line. An Interrupt handle carries `Mint` and
  not `Transfer`, so a launcher cannot hand a driver its line. Separately, an
  Interrupt can be consumed into an attachment, an attachment keeps it bound
  after its handle is released, and an attached Interrupt's record carries its
  line whatever its state (§2.8, §2.10, §3).
- Thread, Waiter and Timer handles cannot yet be given: their default sets
  withhold `Transfer` and no boot record kind names them (§3).
- `sosrt`'s bump arena takes no lock, so under a kernel that preempts (today
  only the preemption harness; §7) two threads of one process allocating at once
  can race (§5c).
- Quotas count objects, not bytes, and there is no per-create quota argument
  (§2.9). Slab donation is permanent (§2.8).
- Handle generations are best-effort bug detection, not a uniqueness
  guarantee (§3).
- A cycle of connections, each carrying the next one's outlet in a staged
  message, is never freed. Only the one-link cycle, a connection's outlet
  through its own inlet, is refused (§3).
- A Mapping whose handles are all released leaves its row installed until the
  target's teardown (§2.5).
- The kernel is uniprocessor (§7).

**The plan.** The standing tail, each item specified in its own section: the
namespace and its path-attached acceptor object (§2.1), `kill` (§8), thread
waitability (§8), priorities and the §7 band map, SMP with `IntrSpinLock`
(§9b), FP-in-userspace, the vDSO true-mapping (§5.7), and the IOMMU driver with
critical processes (§2.5).

## 12. The root server

- **Root = init + launcher + name service in one process, v1.** The
  decided model already pushes supervision, discovery, and policy to
  userspace; v1 collapses them into root rather than booting a service
  constellation. The design test: splitting the launcher or the name
  service into its own process later must require zero kernel changes —
  if the boot handle set can't support that split, the set is wrong.
- **Boot handle set** (minted by the kernel to root; everything else derives):
  the **System handle** (§2.8), which carries the launch capability
  `SystemRight.ProcessCreate` (§7); root MemoryObjects — the RAM pool root and
  the device-range roots (§2.5); and the authority to bind interrupt lines.
  Root's band map applies verbatim from its image (§7, not built). Two of these
  are rights rather than objects, both bits on the System handle: binding a line
  is `SystemRight.InterruptBind` rather than a root IRQ table root is given, and
  the launch capability is `SystemRight.ProcessCreate` (not yet enforced for the
  first: binding is `ProcessRight.InterruptBind` on root's Process handle,
  §2.8). **The set is one handle wide at the register** — the pool roots are
  collected, not passed: the build-emitted region table carries a kind column, a
  Ram row mints a `MemoryObject` and a Device row an `IoMemoryObject`, and both
  arrive through `BootHandleNext` under their row ordinal. A driver obtains its
  window instead of declaring it, which is what makes handing one to a driver
  child possible at all. The kernel assigns no meaning to which ordinal is
  which; that is root's config.
- **Creation authority.** The creation-authority model for plain objects is the
  factory capability, spelled as rights bits on the Process object:
  `ThreadCreate`, `EventCreate`, `WaiterCreate` and `PipeCreate` are Process ops
  each on its own right, so creation flows through a handle the process already
  holds — §3's derivation rule rather than a new mechanism next to it — and a
  launcher strips the bit from anything with no business creating. A quota is
  additive (§2.9): the factory capabilities decide whether a process may create,
  and the quota decides how many. **Process-scoped objects are minted through a
  Process; machine-wide ones by the System.** Threads, Events, Waiters and
  Timers are charged to a process — its slab slots, its quota, and for the bound
  kinds its teardown (§2.10) — so their creation flows through a handle already
  held: a rights bit on the Process object for Threads, Events and Waiters,
  charged to the process that handle names (§2.7), and for a Timer
  `ClockOp.TimerCreate` on `ClockRight.TimerCreate`, through a Clock handle
  (§2.6). The process tables and the interrupt lines are machine-global
  resources bounded by machine facts (for processes the record and body slabs,
  the protection-domain reloads, ASIDs on a translating profile; for lines the
  board), so their refusals and their limits are System answers:
  `process_create` is `SystemOp.ProcessCreate` gated on
  `SystemRight.ProcessCreate`, binding a line is
  `SystemOp.InterruptBind` gated on `SystemRight.InterruptBind` (§2.8; not yet
  enforced, binding is a Process op today), and `SystemOp.ProcessSelf` is the
  precedent — every Process handle comes from System. With a launcher creating
  every process, a creation right on the caller's own Process handle would gate
  nothing real, and creator-pays accounting through the caller's record would
  attribute everything to root — so attribution is assigned by policy (the quota
  vocabulary), not derived from who called.
- **Loader-above-boot.** The kernel loads exactly one image ever: root (sosimg,
  §5.6). Every later process is loaded by root from images root obtains itself
  (e.g. a flash MemoryObject it holds), via the launch capability and
  `process_create`. The kernel has no second code path for process two, and
  root's process is made of the same parts: the boot door takes a record and a
  body for it, the first of each table, as the create door takes a child's
  (§2.7). **The kernel has one loader**, doing the copying (a userspace loader
  split is deferred until it is necessary): the kernel parses sosimg at boot and
  at `process_create` through the same phases — `validate_image` and
  `place_image` take the destination as a region parameter (`LoadRegion`) — and
  the only thing that differs between the two doors is the failure vocabulary:
  the boot door raises `fatal_image`, because a machine whose only image is
  malformed has nothing else to run, and the create door returns `BadImage`,
  because a launcher must be able to report what it was handed. "No second code
  path" is therefore kept by construction: nowhere else in the tree reads a
  segment record. **Root provides the memory and supplies no numbers.**
  `process_create(image:memory:)` takes two Memory capabilities, segments are
  copied to their virtual link addresses inside `memory`, and **the stack is the
  kernel's grant at that region's top** — root's own loader rule, applied to a
  child. So a launcher never parses an image, never computes a load address and
  never chooses a stack, which is what makes "root never parses" airtight. On a
  translating tier the link address is the virtual address the kernel's mapping
  establishes, over the frames of the `memory` region the launcher passed
  (§5.5); on a non-translating tier the virtual and physical addresses coincide.
- **The build emits a region table; the kernel interprets nothing.** The
  stitcher appends each child image as it is and records its rows (`{base, len}`
  and the kind column) in a fixed-symbol section; the kernel mints one sealed
  region per row and hands them to root through `BootHandleNext`; root's config
  says which ordinal is which. A missing table is zero regions. An image without
  children may still have a table: a root that drives a device boots with its
  device row.
- **The boot set is collected, not widened.** The entry register carries exactly
  one handle — System — and everything else the kernel minted for root before it
  ran is drained through `ProcessOp.BootHandleNext`, one `{tag, kind, handle}`
  record per call, through §2.2's copy-out funnel. Exhaustion is a status
  (`Drained`) that stays exhausted, because a loop that drains an iterator ends
  by meeting the end. The tag is a region ordinal out of the build-emitted table
  and the kernel assigns it no meaning: which ordinal is an image, which is a
  destination, which is a device is root's config. That keeps the kernel's half
  of the launch flow identical for a hand-built system and for dynamic loading
  whenever it arrives.
- **Every process has one.** The boot set is per-process: the kernel writes
  root's at boot and a launcher writes a child's with `ProcessOp.Give`,
  through the same machinery, and a child drains its own through its own
  Process handle exactly as root drains root's. The two producers are
  indistinguishable on purpose — the kernel is the giver nobody gave to — and
  the drain is one op with one exhaustion rule. Three rules carry the model:
  - **The tag is the identity, and tags are the launch flow's only cross-process
    vocabulary.** A tag is the giver's own word handed back unread (the
    `Waiter.add` key precedent): the kernel is a courier, never an interpreter,
    and a launcher and a child agree on meaning through config and manifest. So
    a give answers with a status alone — the child-side word means nothing to
    the giver, which can call no op through it — and a duplicate tag is refused
    at the give, because an identity naming two handles would make the boot
    lookup ambiguous.
  - **The set freezes at `start`.** Whatever a launcher gives must be complete
    at the child's first instruction, and the start call is the barrier that
    makes a half-populated table unrepresentable with no synchronization
    invented; a give afterwards is a `BadState` fault. The drain has no
    deadline at all — records persist until consumed, so a child that drains
    late drains correctly.
  - **`start(boot_tag:)` is how the first handle arrives.** The kernel resolves
    the tag to the child-side word and puts it in the first argument register,
    so the entry is `_start(boot_handle)` and the launch flow never shows a
    launcher a child-relative word (§2.7 holds the creation ops to the same; not
    yet enforced). Resolution consumes the record the tag names — the register
    is the delivery — so the drain never answers with a word the child already
    holds. The no-tag form is the default, and a child started with it and given
    nothing is the sandboxed compute process (sandboxed on the isolated tiers);
    a tag naming no given record is a `BadArg` fault, and "no tag" is a form
    argument rather than a reserved tag value, because zero is a legitimate tag
    (root's region ordinals start there).
- **A child bootstraps exactly as root does.** A launcher mints a masked
  sibling of its own System handle (§3's `MINT_OP`) — a keep mask naming
  `Debug | ProcessSelf | ClockGet | Transfer` and nothing else — gives the
  sibling under a tag, and starts the child naming that tag. The child then
  does what root does at boot: adopt the System handle in its first argument
  register, derive its own Process object from it, drain its boot set from
  there. So the kernel has one way in for every process, which is the same
  claim loader-above-boot makes about images, and the differences between root
  and a child are entirely in a rights word. The launcher keeps the child's
  Process handle, so it supervises (`get_status`, and the death wait, §8) while
  the child manages itself. What the mask withholds is enforced at run time by
  the ordinary rights check — a child that asks to `shutdown` is
  `AccessDenied` and dies, and the machine keeps running.
- **What a launcher furnishes reaches hardware.** `give(iomemory:)` hands a
  child a device's window, over the `Transfer` bit `iomemory_rights()` mints,
  and `give(interrupt:)` hands it the device's line, a bound Interrupt (§2.8),
  with no further mechanism: moving a driver out of root needs no kernel change,
  because a device window and a line are capabilities rather than manifest keys.
  Not yet enforced for the line: an Interrupt handle carries no `Transfer` and
  no boot record kind names it (§3), so a driver child binds its own line today.
  Two shapes are both ordinary and a launcher picks by policy: give the
  capability and let the child install its own row (the driver child), or keep
  the capability and install the child's row yourself (a shared page the child
  holds no region object for). Attenuation runs the same way on either:
  `Memory.mint(rights:)` narrows a region before it travels (§2.5's
  `MemoryRight.MapExecute`).
- **v1 protocol conventions** (userspace convention, not kernel surface): a
  launched process receives one bootstrap pipe handle at launch; its first
  messages request its initial handle set from the launcher (name-service
  discovery = ask by string name over that pipe). Conventions are versioned in
  the `sos` userspace module, not in the kernel. Every mechanism the
  convention needs exists — a launcher creates a connection
  (`ProcessOp.PipeCreate`), gives an end to a child under a tag, and the child
  drains it and calls (`tests/uart-service` is that flow with a driver at the
  far end), and a message can carry the handles an answer consists of (§2.1).
  The convention itself is not written: a client finds a service by the boot
  tag its launcher chose rather than by a string, and the path-attached
  acceptor that would resolve `/dev/uart0` is the separate, later design §2.1
  names.
