# SawOS design 10 — M4 SKETCH: pipes (agenda for the scoping session)

**Status: RULED Aug 30 2026 (user, all seven agenda items — see "As
ruled") — THE PLAN OF RECORD; ruling 4 amended Aug 31 (the fused
fast paths `Call` + `ReplyRecv`, taken together).** M4 started at
unit 0's dispatch (Aug 31). M3 closed Aug 30 (unit 6
= design 9, unit 7 = docs sweep). What this does NOT reopen: §2.1 is
RATIFIED (Jul 29; renamed + client API amended Aug 20) — the message
model, the one-shot reply pair, abandonment, the TELL idiom, and the
send/timeout API shape are carried by reference. This sketch adds
the Aug-30 rulings, resolves the structural question those rulings
surfaced (who counts as a "writer" on a bidirectional connection),
and slices the ladder.

## What is already ratified (§2.1, carried whole)

Bounded synchronous message IPC with request/reply as a PRIMITIVE:
`send(data?, handles?) -> PipeReplyHandle`, rendezvous handle
transfer (attached handles enter the receiver's table only at
receive), `receive() -> (message, PipeRequestHandle)`, both one-shots
single-use and TRANSFERABLE (the delegation primitive — the fs/flash
zero-copy example). Abandonment is information, not an imperative:
server drops its PipeRequestHandle → client wakes PeerClosed; client
drops its PipeReplyHandle → cancellation signal the server may honor
or ignore; `reply()` to an abandoned request answers `Err(PeerClosed)`,
obligation discharged either way. Fire-and-forget payload-free stays
Event's; the TELL idiom (send-then-drop on the split-phase form) is
the payload-carrying one-way. Fixed body + handle maxima, zero
dynamic kernel allocation. Waitables owed: Pipe readable,
PipeReplyHandle reply-ready, PipeRequestHandle abandoned (§2.2).

## The Aug-30 rulings (user), folded in here

1. **Waiter revocation — the free arm wakes, it does not count.**
   Design 7 finding 2's open question is answered: when a Waiter's
   last reference drops, `free_object`'s Waiter arm walks `blocked`
   and wakes every parked thread with a distinguishable terminal
   status (revocation), instead of stranding them for the deadlock
   report to name. The husk alternative (parked thread worth 1 to
   the count) is REJECTED: it keeps the thread alive but makes the
   drop silent — a hang to infer rather than an error to handle.
   Strikes the legal-but-doomed paragraph at `refs.saw`'s Waiter arm
   and design 7 finding 2's deferral.
2. **The peer-gone doctrine: NOTHING PARKED CAN BE SILENTLY DOOMED.**
   Every "what you are waiting for can no longer happen" is a
   DELIVERED WAKE with a distinguishable status, never a strand.
   M4 has three instances: the Waiter itself dying (ruling 1), the
   reply pair (already ratified: PeerClosed both directions), and
   the connection's request direction (this sketch's D-1). Terminal
   levels per design 8's precedent: attach-after-the-fact still
   wakes, nothing un-closes.

## D-1: the structural answer — ROLES ARE OBJECTS, not rights

The question that motivated this sketch: a Pipe carries traffic both
ways (requests in, replies out), so "wake the receiver when writers
reach zero" needs the kernel to know how many handles can still
send. Do we track separate read/write reference counts?

**No — the bidirectionality dissolves instead.** A single symmetric
object cannot express it (a parked receiver is itself a holder, so a
write-capable count never reaches zero until nobody is left to wake
— the socket shutdown() hole). And a rights-partitioned count on one
object is a SECOND LEDGER, against design 7's one-ledger doctrine.

**A connection is a PAIR of counted endpoint kinds** (names RULED
Aug 30, user): **`PipeInlet`** is the CLIENT side — requests flow IN
here — and **`PipeOutlet`** is the SERVER side, where they come out.
"Pipe" survives as the connection's collective name (§2.1's
"per-client connection object" sentence amends to name the pair).
Ops differ per end — send lives on the inlet, receive on the outlet
— so rights stay named for ops (Aug-29 doctrine) and the roles are
structural, not a rights mask to count by. Then:

- "Writers went to zero" IS "the client end reached zero references"
  — the existing ledger, no new vocabulary. `free_object` grows an
  arm: client end at zero → PEER-GONE TERMINAL LEVEL on the server
  end (a parked receive wakes PeerClosed; a later receive answers
  PeerClosed; buffered/staged rendezvous state is unwound per the
  rendezvous rule — nothing was transferred, nothing leaks).
- Symmetric for free: server end at zero → every parked/future
  send answers PeerClosed. No special case, same arm mirrored.
- The reply direction needs NOTHING: the ratified one-shot pair
  already carries per-role drop semantics — it was the precedent,
  and D-1 extends its shape to the connection itself.
- The client end is per-client BY CONSTRUCTION (mint/transfer of a
  client end duplicates references to the SAME connection, counted
  as ever); a server serving many clients holds many server ends on
  one Waiter, keys telling them apart — the §2.2 model unchanged.
- Attenuated-tap follow-on RECORDED, not built: if an end's handles
  are ever minted down (e.g. wait-only), a write-capable count would
  be the precise detector, and monotonic mint makes its zero
  terminal (no new sender can appear). Deferred until a case wants
  it; v1 rule is any handle to an end holds that end open (the Unix
  dup precedent).

## The ladder (proposed slicing)

- **Unit 0 — waiter revocation.** Ruling 1, standalone and pre-pipe:
  the status word, the free-arm walk, `waiter-revoked` case (sibling
  parks, main drops last handle, sibling wakes with the status).
  Small; lands the terminal-status vocabulary everything later uses.
- **Unit 1 — the pair.** `PipeInlet`/`PipeOutlet`:
  `ProcessOp.PipeCreate` (agenda ruling 3), refcount arms, quota rows,
  blocking `send`/`receive` data-only rendezvous, PeerClosed on both
  zero-arms. No handles-in-messages, no waitability yet. The copy
  funnels exist (§2.2's one door); kernel interruptibility (M3 unit
  1.5, design #1) already covers the copy loops.
- **Unit 2 — the one-shot pair.** PipeReplyHandle/PipeRequestHandle
  as objects: consumed-on-reply, transferable (delegation), drop
  semantics as ratified, NoCopy in the sosrt surface (RAII
  end-to-end, the memory doctrine). **Plus ruling 10's rider**: the
  teardown force-free arms for counted kinds are deleted, D-4's
  orphan write-off activates, references govern lifetime.
- **Unit 3 — waitability.** The §2.2 arms — now FOUR with ruling 8
  (the inlet's room-to-post level joins the list) — plus ruling 9's
  attachment pair (consume-detaches + `AttachMode.OneShot`), plus
  ruling 11's delivery-payload record and
  `AttachMode.OneShotConsuming` (the §2.2 and §2.1 amendments all
  ride together): (server end
  readable, reply-ready, request-abandoned) + the peer-gone terminal
  levels through the notify machinery; the `send`/`send(timeout:)`
  LIBRARY compositions over post + wait + resolve become real here
  (agenda ruling 4; `TimedOut(pending:)` carries the live claim;
  drop = cancel); **and the FUSED FAST PATHS `Call` + `ReplyRecv`**
  (ruling 4's Aug-31 rider — the unit brief may split them into a
  3.5 if the unit runs long; the ping-pong trap-count case is their
  proof).
- **Unit 4 — handles in messages.** Rendezvous transfer through the
  staged slot (a staged handle's refcount treatment is agenda ruling
  5), the delegation proof as transcript: request forwarded to a
  third process, reply lands at the original client. Plus ruling
  11(d): the completion-queue server — persistent-consuming outlet
  attach, delivery-as-take minting the request handle at the wait
  (the same rendezvous census, including the quota-refusal arm).
- **Unit 5 — the money shot.** The M3-unit-6 driver child grows a
  SERVER FACE: a sibling client child reads the UART through a pipe
  protocol instead of touching registers — driver-as-service, the
  namespace's `/dev/uart0` model with the path resolution still to
  come. Both arches. TELL and cancellation cases ride along.
- **Unit 6 — docs.** §2.1 flips to BUILT, §2.2 waitable list closes,
  §11 refresh, the M4 recap.

## Candidate riders (take or defer at scoping)

- **Attenuate-at-give keep-mask** (the backlog's M4 seed): Transfer
  is inert-but-present on child System handles, recorded at four
  sites; pipes multiply the handles children hold, so the seam gets
  hotter. RULED IN (agenda 7b) — unit 2.5 or folded into unit 4,
  the unit brief decides.
- **Event-wake / event-consume-wake one-handle-each rewrite via
  mint** (backlog; transcript-moving, pre-authorized case rewrite).
  RULED DEFERRED (agenda 7b), on the standing reason — it rides
  only a unit that moves those transcripts anyway.

## Explicitly NOT M4

The namespace and the path-attached acceptor object (§2.1 names it a
separate, later design); kill; thread waitability; priorities/§7
band map, SMP/IntrSpinLock, FP-in-userspace, vDSO true-mapping (the
standing tail); the IOMMU driver (death-notification consumer 2).

## The decisions agenda — RULED Aug 30 (user), items 4 and 7 open

1. **RULED: `SosStatus.Revoked`**, a new status. One word for
   wait-on-freed-waiter and future revocation sites alike.
2. **RULED: the pair model stands; the kinds are `PipeInlet`
   (client) / `PipeOutlet` (server).**
3. **RULED: the factory is on the PROCESS object**, like
   `EventCreate`/`WaiterCreate` — pipes are IPC and carry nothing
   system-shaped (user's words; the System lean is REJECTED). So:
   `ProcessOp.PipeCreate` gated by a new `ProcessRight.PipeCreate`
   in the default set, both ends minted into the creator's table,
   wired outward with `give` (a launcher creates the pair and gives
   inlet to one child, outlet to the other — the launch flow works
   unchanged, and the authority is per-process attenuable, which the
   System spelling could not offer without a mask).
4. **RULED (Aug 30, user): `post` IS THE ONE KERNEL PRIMITIVE — the
   PipeReplyHandle composes everything else.** The kernel's client
   side has exactly one submission op: stage the message, return the
   claim, never suspend. Blocking send is WAITING ON THE CLAIM
   (attach to a Waiter, park, resolve on wake); timeout is the same
   wait with a Timer on the same Waiter — `TimedOut(pending:)` falls
   out because the composition holds the still-live handle when the
   timer fires first; cancel is drop; TELL is post-then-drop; a
   receipt/"rendezvous-complete" signal is NOT a send mode but a
   RESERVED second level on the reply handle, unbuilt until a
   consumer names itself. THE KERNEL NEVER LEARNS WHAT A TIMEOUT IS
   — no duration argument, no 64-bit copy-in funnel for it, no
   timeout code in the pipe paths; it composes from shipped Timer
   machinery. §2.1's ratified client API is PRESERVED AS LIBRARY
   SURFACE (vDSO discipline: surface is not ops): `send(msg)` /
   `send(msg, timeout:)` are typed-sysapi compositions over post +
   wait + resolve. Costs stated: a composed RPC is three traps where
   a fused call could be fewer (a fused op is a later ADDITIVE
   optimization, Zircon-call precedent, if profiles demand);
   blocking a reply requires a Waiter, which the sosrt wrapper owns.
   [The ring-full cost note this rider carried is SUPERSEDED by
   ruling 8: the composed send PARKS on the inlet's room level.] Ladder consequence: unit 1's proofs POLL (post +
   nonblocking take), and the blocking wrappers become real when
   waitability lands in unit 3. A mode-enum parameter on send was
   CONSIDERED AND REJECTED: the four modes split into two success
   types (the reply vs the claim), a runtime flag can change neither
   a return type (§2.1 Aug-20's own sentence) nor a Saw function's
   effect, and a unified outcome enum forces every blocking caller
   through a `Pending` arm its mode cannot produce — the signature
   shape design 234 bans.
   **RIDER (ruled Aug 31, user): THE FUSED FAST PATHS — `Call` and
   `ReplyRecv` — join `post` in the kernel, taken TOGETHER in one
   unit.** The performance amendment, not a semantic one: a fused op
   is THE COMPOSITION RUN WITHOUT RETURNING TO USERSPACE, so every
   rule the composed path obeys applies verbatim. Client side:
   `Call` = post; park; resolve behind one trap — the dominant RPC
   shape drops from three traps to one. Server side: `ReplyRecv` =
   reply-then-receive-next behind one trap — the steady-state server
   loop halves. Mechanics, ruled with it: the reply body BOUNCES
   THROUGH THE RING SLOT (replier copies in, wakes; the caller's
   thread resumes inside its own syscall and copies out through the
   one checked funnel — the kernel never dereferences another
   process's memory, and ruling 6's fixed slots carry the reply);
   the wake dispatch grows ONE arm (a claim is a parked fused call
   -> wake the thread, or a split-phase claim -> notify the Waiter —
   not a second wake protocol); a fused call mints NO
   `PipeReplyHandle` into the caller's table (the claim is
   kernel-internal, tied to the parked thread, consumed at reply —
   zero handle traffic and zero one-shot quota rows on the fast
   path; `post` keeps minting the real handle for multiplexers);
   and `end_process`'s sweep gains an arm for a process dying while
   parked in a call (the orphaned claim becomes ABANDONED, so the
   server's later `reply()` answers `Err(PeerClosed)` per the
   ratified rule — name this in the unit brief). `Call` takes NO
   duration: the kernel still never learns what a timeout is, and
   `send(msg, timeout:)` keeps the library composition. Surface
   unchanged: `send(msg)` compiles to the `Call` trap,
   `send(timeout:)` to the composition, `post` to the primitive.
   The proof is a ping-pong case that COUNTS TRAPS. Unit placement:
   unit-3 territory (wants the pair, the one-shots and the reply
   path); the unit brief places it.
   **AMENDED (Sep 1, user, composing with ruling 11): `ReplyRecv` IS
   REPLY-THEN-WAIT, not reply-then-receive-next.** The shape:
   `ReplyRecv(request, reply_body, reply_handles[4], waiter)` —
   discharge the request (consumed, ruling 9(a)), then park ON THE
   WAITER exactly as a plain `wait` parks, resuming with ruling
   11(d)'s delivery record (the next message, the minted request
   handle). No direct park-on-outlet: the wake protocol stays
   SINGULAR, the same property this rider already demanded of the
   client side. `NO_HANDLE` for the request is the degenerate plain
   wait, so the loop head is uniform — the first iteration and the
   steady state are one call shape. TWO RETURN CHANNELS, kept
   separate: the reply's status rides BESIDE the wait record, and a
   `PeerClosed` reply (an abandoned client) does NOT abort the park —
   obligation discharged, the loop continues; one dead client must
   not stall the server. A single-connection server uses a
   one-attachment waiter the sosrt wrapper owns (the client
   composition's own precedent). PLACEMENT: the full form needs
   handles-in-messages and delivery-minting, so it is UNIT 4's; a
   data-only `ReplyRecv` may land with 3/3.5 and grow its handle
   slots in unit 4 exactly as `Post` grew its claim return (§5.7).
   Steady state: ONE TRAP PER MESSAGE — client `Call` and server
   `ReplyRecv` symmetric at one trap each.
   **REVIEW GATE (user, Sep 1): the proposed USER AND KERNEL APIs
   for the fused paths, the consuming attach, and the delivery
   record go to the user for review BEFORE they are committed.**
   Usability of the whole pattern hangs on the spellings; the unit
   3/3.5/4 briefs carry their proposed surfaces to the user first
   and implementation waits on the nod.
5. **RULED: a staged handle stays the SENDER's counted entry until
   receive**, transferred exactly at rendezvous — the ledger never
   has an in-flight limbo state, and an abandoned send unwinds with
   nothing leaked (the ratified no-orphans rule made mechanical).
6. **RULED: body 128 bytes, 4 handles per message, in-flight
   messages per client 2 × MAX_THREADS — all BUILD DEFINES** (named
   statics in `kcore.limits`, the tree's build-define mechanism, so
   they change without touching logic; static_asserts pin the §2.1
   ranges). Note the in-flight budget AMENDS §2.1's "stages one
   message in a fixed slot": the staging is a small per-connection
   ring of fixed slots — still zero dynamic kernel allocation, the
   property the sentence existed for. Quota rows: creator-pays; the
   pair's creator is charged for both ends.
7. **RULED (Aug 30, user)**: (a) the MONEY SHOT is CONFIRMED —
   driver-as-service: the M3 driver child grows a pipe-server face
   and a sibling client reads the UART through the pipe protocol
   instead of registers (the namespace's /dev/uart0 model, paths
   still to come). (b) The attenuate-at-give KEEP-MASK rider is
   TAKEN into the ladder (unit 2.5 or folded into unit 4, the unit
   brief decides — and it now composes with unit 6's recorded
   finding that `ProcessSelf` mints outside any keep mask); the
   event-wake/event-consume-wake one-handle-each rewrite is
   DEFERRED again, on the standing reason (transcript-moving, rides
   only a unit that moves those transcripts anyway).

8. **RULED (Aug 31, user): `PipeInlet` IS A WAITABLE — the
   ROOM-TO-POST level.** `post` can never block, so a refused poster
   must have something better than a poll loop: the inlet joins
   §2.2's waitable list with a level that is READY while the ring
   has a free slot OR the peer is gone (the payload word
   distinguishes room from `PeerClosed`, so a waker learns which
   without a probe post). A plain LEVEL, not terminal: true while
   space exists, false while full, consume clears nothing — the
   level's end is the poster's own next post, the Interrupt-shaped
   emptiness. `PipeInletRight.Wait` gates the attach (the Timer
   precedent). The composed blocking `send` gains its missing leg —
   wait-for-room, post, wait-for-reply — so the library form PARKS
   under backpressure instead of surfacing `WouldBlock` (which
   remains the polling caller's honest answer). Lands in UNIT 3 with
   the rest of waitability; §2.2's ratified list amendment (the
   inlet as a member) rides that unit. This promotes the "reservable
   level" hedge ruling 4's rider carried, which is struck.

9. **RULED (Aug 31, user): ONE-SHOT ATTACHMENTS — a pair of rules,
   landing in unit 3.**
   (a) **MANDATORY — an op that CONSUMES the object DETACHES it
   implicitly.** An attachment is a counted reference (design 7),
   and `resolve` consumes the reply object as `reply()` discharges
   the request — without this rule the consuming op either cannot
   free the slot or must refuse while attached, wedging the op
   ordering. Precedent: unit 5.5's teardown taught the kernel to
   unhook an attachment's far end. This alone takes the multiplexed
   RPC from five traps to four.
   (b) **OPT-IN — `AttachMode.OneShot` on `add`**: the attachment
   detaches AT DELIVERY (when the record for its key is copied out —
   never at mere readiness; the delivery is otherwise identical).
   One-shot is a property of the ATTACHMENT, the attacher's choice
   like the key; spelled as a mode-value (the `EventMode` doctrine)
   with a defaulted trailing parameter (`mode: AttachMode =
   AttachMode.Persistent`) — zero churn at existing call sites. What
   it buys: the composed blocked send's room-wait is attach-oneshot
   / wait / post (no trailing remove per backpressure stall);
   request-abandoned watchers and wait-once death supervisors — the
   LEVEL kinds nothing consumes — get the same save.
   Pinned with it: the detach-at-delivery drops a counted reference
   and CAN CASCADE A FREE — a new unref site the unit-3 brief must
   census (delivery runs in the notifier's context and the waiter's
   both), plus iterate-while-removing care in the delivery walk.
   AMENDS ratified §2.2: "attachments are persistent subscriptions /
   there is no one-shot mode" flips to persistent-BY-DEFAULT (the
   amendment rides unit 3, beside ruling 8's). The
   wait-on-now-empty-Waiter footgun (attach one-shot, deliver, wait
   again) gets the existing behavior STATED at the site. Recorded as
   not-the-motivation: EPOLLONESHOT's re-arm race does not exist
   here (§2.2's one-wake distribution) — this is the syscall save
   and lifecycle hygiene only; the fused `Call` still beats all of
   it at one trap.

10. **RULED (Aug 31, user): REFERENCES GOVERN LIFETIME — teardown
    WRITES OFF, it never force-frees a counted kind.** Design 13's
    finding 2 named the hazard (a creator dying would free a pipe's
    slot under a peer still holding an end — the staged messages
    with it) and proposed ownership-transfer-at-`give`; this ruling
    takes the OTHER fix, because it deletes code instead of adding
    bookkeeping. The slabs were always ONE GLOBAL POOL — what is
    per-process is the quota charge and the teardown sweep, and the
    sweep's by-charged-process FORCE-FREE is a pre-refcount survivor
    (units ≤4 had no count to consult). Now: a dying process's
    teardown drops ITS OWN references (the close-all, unchanged) and
    WRITES OFF its quota charges — design 7 D-4's orphan vocabulary,
    landed as a degenerate case in anticipation of exactly this —
    and the force-free arms for counted kinds are DELETED. A slot
    outlives its creator for exactly as long as anyone holds a
    reference: possession keeps alive, the capability model's own
    answer. An orphaned object is charged to NOBODY until it frees;
    the machine stays bounded because quota ≤ wall (unit 5's
    assert). Ownership-transfer-at-give is REJECTED: charge-follows-
    handle goes incoherent once minted siblings span processes.
    UNREACHABLE TODAY, and provably (MAX_PROCESSES = 2, give flows
    only downward, root dies last) — ruled on the unit-5.5
    principle: an invariant that has quietly become false is worse
    than a branch nothing takes. Lands as a RIDER ON UNIT 2 (sweep
    arms deleted, D-4's write-off activates, the reasoning recorded
    at the sweeps); the transcript proof is DEFERRED to whatever
    wiring first makes a holder outlive a creator. Parked beside it
    as an M5 SEED, deliberately not entangled: a GROWABLE pool wants
    a kernel allocation story — a Memory-backed slab-donation op is
    the capability-shaped candidate.

11. **RULED (Sep 1, user): DELIVERY CARRIES THE PAYLOAD — the wait
    record grows a body, and attach gains a CONSUMING mode.** The
    pattern that motivated it: a client that attaches its
    `PipeReplyHandle` to a Waiter and hands the claim over entirely —
    the reply DATA arrives through the wait itself, so the handle is
    functionally useless after the attach and holding it is pure
    ceremony. Three parts, taken together:
    (a) **The reply body rides the wait record.** `WaitResult` grows
    from a word-sized record to a VARIABLE-SIZED one by kind, maxing
    out at the message size (`PIPE_BODY_BYTES`, 128 — the user's own
    sizing). The body is copied out during the winning `wait`'s
    copy-out through the existing checked funnel — the kernel still
    never touches another process's memory — and the ring slot
    SETTLES AT DELIVERY (the resolve leg of the composition
    disappears; `wait_record_bytes()` moves, which the vDSO
    discipline makes free).
    (b) **On a one-shot kind, delivery is inherently consuming** —
    the claim settles, so the attachment detaches by 9(a)'s own
    logic whatever mode was passed. Stated so nobody looks for a
    persistent reading.
    (c) **Consuming attach is a MOVE-TAKING `add` variant, not a
    mode value** (amended same day, user's wrinkle): consuming is an
    EFFECT change, and ruling 4's own mode-enum rejection applies —
    a runtime flag can change neither a return type nor a Saw
    function's effect — so the typed surface splits by signature:
    the consuming `add` takes the wrapper BY MOVE (transfer-funnel
    contract, sentinel disarm, the object provably dead at the call
    site), while `AttachMode { Persistent, OneShot }` stays the
    defaulted delivery-behavior parameter. The axes are ORTHOGONAL —
    a 2x2, not a third mode value (`OneShotConsuming` as an enum
    case is struck). Kernel-side one op with a flag bit is fine; the
    split is a typed-surface requirement. The attach takes the
    caller's handle entry through the transfer funnel and the
    ATTACHMENT owns the object — kernel-side move, not a library
    add+release, which would give the saved trap back. Legal on any
    kind: on an Event it makes the attach-and-forget supervisor
    deliberate (the attachment-owned husk becomes an opt-in state,
    not an accident).
    (d) **The OUTLET takes the same pattern — persistent + consuming
    is the COMPLETION-QUEUE SERVER** (ruled Sep 1, the symmetry the
    user named): a server attaches its `PipeOutlet` consuming and
    persistent, and every incoming message flows out through `wait`
    itself — the delivery IS the take, so the record carries the
    message AND MINTS the `PipeRequestHandle` into the wait-caller's
    table at delivery. The multiplexed server drops from
    wait+take+reply (three traps per RPC) to wait+reply (two), holds
    ZERO outlet handles for N connections, and hanging up IS
    destroying the subscription: the attachment holds `outlet_refs`,
    so waiter-death or removal drops the server end to zero and
    clients answer `PeerClosed`. `ReplyRecv` keeps the
    single-connection steady loop at one trap; the two serve
    different shapes. PLACEMENT: delivery-minting is
    rendezvous-at-receive (§2.1: handles enter the receiver's table
    only at receive), so this half rides UNIT 4 with the rendezvous
    census — including the stated arm for a wait-caller whose handle
    quota cannot accept the mint at delivery — while unit 3 lands
    the data-only reply delivery of (a). RECORD SIZING, the user's
    own: `WaitResult` maxes at the reply's (128 body bytes + 4
    message handles) plus the server delivery's one request handle —
    variable-sized by kind, the few extra bytes accepted as the cost
    of the symmetry.
    Consequences pinned with it: the multiplexed client is post →
    attach-consuming → wait, THREE traps and ZERO held reply handles
    however many requests are outstanding (the fused `Call` still
    wins at one trap for the plain blocking case — this serves the
    select loop); and §2.1's abandonment sentence re-words to the
    LAST REFERENCE — a handle drop with a live attachment is not
    abandonment (the subscription is the interested party), while
    removing the attachment or the Waiter dying before delivery
    drops the last reference and signals cancellation, which is
    exactly what destroying a subscription should mean. The
    amendments ride units 3 and 4 beside ruling 8's and 9's. Parts
    (a)-(c) land in UNIT 3 with the rest of waitability; part (d)
    lands in UNIT 4 with rendezvous transfer.

## As ruled

Aug 30 (user): ALL SEVEN ITEMS RULED — this sketch is the PLAN OF
RECORD. 1 `SosStatus.Revoked`; 2 `PipeInlet`/`PipeOutlet`; 3 the
factory is `ProcessOp.PipeCreate`; 4 post is the ONE kernel
primitive, send/send(timeout:) are library compositions; 5 staged
handles stay the sender's until receive; 6 limits 128 B / 4 handles
/ 2×MAX_THREADS in-flight as `kcore.limits` statics; 7
driver-as-service confirmed, keep-mask rider taken, event rewrite
deferred; 8 (Aug 31) the inlet's room-to-post waitable level; 9
(Aug 31) one-shot attachments — consume-detaches mandatory,
`AttachMode.OneShot` opt-in; 10 (Aug 31) references govern lifetime
— teardown writes off, never force-frees a counted kind (rider on
unit 2; growable-pool M5 seed parked); 11 (Sep 1) delivery carries
the payload — `WaitResult` variable-sized by kind (max: 128 body
bytes + 4 message handles + the server delivery's request handle),
the ring slot settles at delivery, consuming attach is a MOVE-TAKING
`add` variant orthogonal to `AttachMode { Persistent, OneShot }`,
abandonment re-worded to the last reference, and the
persistent-consuming outlet is the completion-queue server — (a)-(c)
unit 3, (d) unit 4 with rendezvous. Ruling 4's rider amended Sep 1:
`ReplyRecv` is REPLY-THEN-WAIT (one wake protocol, NO_HANDLE
degenerate, two-channel return; full form unit 4). REVIEW GATE: the
fused/attach/delivery API spellings — user and kernel both — go to
the user before commit. Unit briefs are authored per the M3 process;
M4 started Aug 31.
