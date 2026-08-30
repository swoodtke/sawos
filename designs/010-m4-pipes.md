# SawOS design 10 — M4 SKETCH: pipes (agenda for the scoping session)

**Status: DRAFT Aug 30 2026 (lead), for user review — the plan of
record once ruled.** M4 starts after M3 closes (unit 6
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

**A connection is a PAIR of counted endpoint kinds**: the client end
(`Pipe`, §2.1's own name — "the PER-CLIENT connection object") and
the server connection end (kind name an agenda item; `PipeServe`
leading). Ops differ per end — send lives on one, receive on the
other — so rights stay named for ops (Aug-29 doctrine) and the roles
are structural, not a rights mask to count by. Then:

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
- **Unit 1 — the pair.** Both endpoint kinds: create (the factory
  and WHO owns it is agenda item 3), refcount arms, quota rows,
  blocking `send`/`receive` data-only rendezvous, PeerClosed on both
  zero-arms. No handles-in-messages, no waitability yet. The copy
  funnels exist (§2.2's one door); kernel interruptibility (M3 unit
  0) already covers the copy loops.
- **Unit 2 — the one-shot pair.** PipeReplyHandle/PipeRequestHandle
  as objects: consumed-on-reply, transferable (delegation), drop
  semantics as ratified, NoCopy in the sosrt surface (RAII
  end-to-end, the memory doctrine).
- **Unit 3 — waitability.** The three §2.2 arms (server end
  readable, reply-ready, request-abandoned) + the peer-gone terminal
  levels through the notify machinery; split-phase send (verb:
  agenda item 4); timeout composition (`TimedOut(pending:)` carries
  the live claim; drop = cancel).
- **Unit 4 — handles in messages.** Rendezvous transfer through the
  staged slot (a staged handle's refcount treatment is agenda item
  5), the delegation proof as transcript: request forwarded to a
  third process, reply lands at the original client.
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
  hotter. Lean: TAKE as a small unit 2.5 or fold into unit 4.
- **Event-wake / event-consume-wake one-handle-each rewrite via
  mint** (backlog; transcript-moving, pre-authorized case rewrite).
  Lean: ride whichever unit first touches those transcripts, else
  defer.

## Explicitly NOT M4

The namespace and the path-attached acceptor object (§2.1 names it a
separate, later design); kill; thread waitability; priorities/§7
band map, SMP/IntrSpinLock, FP-in-userspace, vDSO true-mapping (the
standing tail); the IOMMU driver (death-notification consumer 2).

## The decisions agenda (the scoping session, worked item by item)

1. **Ruling 1 confirmation**: revocation status word — new
   `SosStatus` (`Revoked` leading) vs reuse; and does the same word
   serve wait-on-freed-waiter and future revocation sites?
2. **D-1 confirmation**: the pair model as ruled above; the server
   end's kind name.
3. **The factory**: where does the pair come from? Candidates: a
   System op (`SystemRight.PipeCreate` — machine-wide resource like
   ProcessCreate) returning both ends to the creator, who gives them
   out (root wires children together — matches the launch flow); or
   a Process-object factory like EventCreate. Lean: System, because
   a connection spans processes and the wiring authority is the
   launcher's.
4. **Split-phase verb** (§2.1 leaves it to this brief): `post`
   leading.
5. **Staged-handle refcount rule**: a handle attached to an unsent/
   unreceived message — counted where? Lean: it stays the sender's
   entry until rendezvous (the ratified no-orphans rule), moved
   exactly at receive, so the ledger never has an in-flight limbo
   state.
6. **Limits**: body bytes (64–256 ratified range) and handle count
   per message; quota kinds and rows (ends, in-flight one-shots) —
   creator-pays says the pair's creator is charged for both ends.
7. **Slicing**: is unit 5's driver-as-service the right money shot,
   and do the riders come in?

## As ruled

(Filled at the scoping session; each unit then gets its own design
brief per the M3 process.)
