# SawOS design 14 — the one-shot pair: PipeReplyHandle / PipeRequestHandle (M4 unit 2)

Status: DRAFT Sep 1 2026 (lead). Ladder unit 2 of the ruled plan of
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
