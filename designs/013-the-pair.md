# SawOS design 13 — the pair: PipeInlet / PipeOutlet (M4 unit 1)

Status: AUTHORED Aug 31 2026 (lead). Ladder unit 1 of the ruled plan
of record (designs/010): **the two endpoint kinds exist, messages
flow one direction, and everything polls.** No reply path (the
one-shot pair is unit 2's), no handles in messages (unit 4's), no
waitability and no parking (unit 3's) — unit 1 is the connection as
an OBJECT, with the refcount, quota and peer-gone machinery every
later unit stands on.

## The ruled surface (all from designs/010, cited per item)

- **Kinds: `PipeInlet` (client — requests flow IN here) and
  `PipeOutlet` (server — they come out here)** [ruling 2]. Two
  `ObjType` cases, two handle kinds, two rights enums named for
  their ops (Aug-29 doctrine): `PipeInletRight { Transfer, Mint,
  Post = 1<<8 }`, `PipeOutletRight { Transfer, Mint, Take = 1<<8 }`.
  Wait rights arrive with waitability (unit 3).
- **The factory: `ProcessOp.PipeCreate` on `ProcessRight.PipeCreate`**
  [ruling 3], the new bit joining the ONE Process default set. Both
  ends are minted into the CREATOR's table and wired outward with
  `give`. Creator-pays: one `QuotaKind.Pipe` row, charged at create,
  credited when the CONNECTION frees (below).
- **Limits as build defines** [ruling 6]: `PIPE_BODY_BYTES = 128`
  and `PIPE_INFLIGHT = 2 * MAX_THREADS` land in `kcore.limits` as
  named statics with static_asserts pinning §2.1's ranges.
  `PIPE_MSG_HANDLES = 4` is DECLARED WITH UNIT 4, which is the first
  unit that reads it — an unused constant is a promise, not a record.
- **The staging is a per-connection RING of fixed slots** [ruling 6's
  amendment to §2.1's "one fixed slot"]: `PIPE_INFLIGHT` deep, each
  slot `PIPE_BODY_BYTES` wide, inline in the connection slab — zero
  dynamic kernel allocation, the property the ratified sentence
  existed for.

## D-1: one slab, two counted kinds

One `PIPES` slab holds the CONNECTION: state, the ring
(head/count/slots), and **TWO reference columns — `inlet_refs` and
`outlet_refs`** — each maintained by its own kind's handle entries
exactly as every counted kind's column is (design 7's sites). This
is D-1's "roles are objects" made mechanical without a second slab:
the two `ObjType` kinds TARGET THE SAME PIPE SLOT, and which column
a handle counts on is decided by its kind, which dispatch establishes
before anything else (§3 order).

- **A side reaching ZERO raises PEER-GONE on the other** [D-1]: the
  slot's state records which side died. In unit 1 (nothing parks)
  that is visible only as STATUS ANSWERS — see D-2's zero-arms.
- **The connection slot frees when BOTH columns are zero**, and that
  is when the quota row credits. The counted-kinds table gains two
  rows; the compiler enumerates the arms (the design-8 precedent —
  say in the As-built what it caught).
- Attach of either kind is `NotWaitable` in unit 1 — the refusal arm
  states that unit 3 removes it (the §8-thread-deferral pattern).

## D-2: the two data ops, and the two new statuses

- **`PipeInletOp.Post`** (`PipeInletRight.Post`): copy the caller's
  body (addr + len, len ≤ `PIPE_BODY_BYTES`, len 0 LEGAL — a
  data-free message is §2.1's option) IN through the one funnel,
  into the next ring slot. Ring full answers **`WouldBlock`** —
  caller-checkable, transient, never a park [ruling 4: post never
  suspends]. NOTE: in unit 1 `Post` answers plain status — the
  `PipeReplyHandle` it will return is UNIT 2's, and the op growing a
  return value between units is what §5.7's renumberable-ops
  discipline makes free. Say so at the op.
- **`PipeOutletOp.Take`** (`PipeOutletRight.Take`): nonblocking —
  copy the oldest staged message OUT through the funnel (caller
  buffer ≥ `PIPE_BODY_BYTES`; answers the length), or **`WouldBlock`**
  when the ring is empty. The typed wrapper surfaces design 234 §4's
  two-channel shape: `Ok(Some(msg)) / Ok(None) / Err(...)` — a
  would-block is NOT an error at the typed tier.
- **Two new `SosStatus` cases, appended**: `PeerClosed = 9` (§2.1's
  own word, the peer-gone doctrine's second instance) and
  `WouldBlock = 10` (one transient for both directions: post-full and
  take-empty; `describe()` worded for the condition, not the op).
  Lean recorded, agent may argue the naming on the design-234
  narrowest-type doctrine.
- **The zero-arms** [D-1 + design 230's drain precedent]:
  - outlet_refs hits zero → a later `Post` answers `PeerClosed`
    (nobody can ever take), and the ring's staged bodies are DROPPED
    at that transition (data was copied, nothing owns anything —
    state it at the site).
  - inlet_refs hits zero → **staged messages DRAIN FIRST**: `Take`
    keeps answering messages until the ring is empty, THEN
    `PeerClosed` — design 230's close-drains-first rule, terminal
    thereafter (no new sender can appear: attenuation is monotonic
    and the count is zero). This is the message-model reading of
    D-1's peer-gone; the "unwound" clause in 010 D-1 is about
    HANDLES-in-flight, which unit 1 does not carry.
- Faults stay faults: bad handle, unspent right, malformed
  word/length — the existing vocabulary, no new cases.

## D-3: creation, give, and the boot drain

- `PipeCreate` answers BOTH handles through a COPY-OUT RECORD (two
  words, offsets published as constants beside the wait record's) —
  the one-op-one-return convention cannot carry two words in a0, and
  the copy funnel is the precedented door (Clock's `Now`). Lean
  recorded; the agent may argue an alternative shape with the §5.7
  doctrine in hand.
- **`give` carries both kinds** (kernel give is kind-generic
  already): sysapi grows `Process.give(inlet:)` / `give(outlet:)`
  funnels beside the five that exist, `BootHandleKind` grows two
  cases, and `BootHandle` grows the matching `take_*` accessors.
  The sysapi endpoint modules sit BELOW `system` (like
  memory/iomemory); the facade re-exports `PipeInlet`/`PipeOutlet`.
  (Extension lookup follows `public import` since 0.2.0 — placement
  is unconstrained; put methods where they read best.)
- Typed wrappers: `NoCopy`, drop-is-release, the NO_HANDLE disarm
  sentinel — the nine siblings' exact shape.

## D-4: what composes, untouched

Teardown: close-all releases entries, the columns fall, the
zero-arms fire — NO new teardown arm (the fused-call claim arm is
unit 3's, with `Call`). `has_external_wake_source`: untouched,
nothing parks. Interruptibility: the two copies are bounded by
`PIPE_BODY_BYTES`; note whether the preemption-point rule wants a
point (M3 unit 1.5's threshold decides — record the answer either
way).

## The proof (harness)

1. **`pipe-basics`** (root-only, both ends held): round trip
   (post → take, bytes identical, transcript proves the payload);
   FIFO across several messages; zero-length message; ring depth
   COUNTED (post until `WouldBlock` — the count printed must equal
   `PIPE_INFLIGHT`); take-empty answers the typed `Ok(None)`.
2. **`pipe-peer-gone`**: drop the outlet, post answers `PeerClosed`;
   fresh pair, stage messages, drop the inlet — takes DRAIN all
   staged messages then answer `PeerClosed` (the transcript shows
   drain-then-terminal); a second take still `PeerClosed` (nothing
   un-closes).
3. **`pipe-child`**: root creates the pair, `give`s the INLET to a
   child (boot drain), keeps the outlet; the child posts one
   message; root polls take until it arrives and prints the bytes —
   `give`/drain/`Transfer` proven for the new kinds, the seed of
   unit 5's client.
4. Negative arms where they are cheap: post through a mint lacking
   `Post` (refused); oversized len (`BadArg` fault); attach →
   `NotWaitable`.

Transcript expectation: existing rows byte-identical or address-only
(kernel and images grow); new rows only. Poll loops must be BOUNDED
(a counted retry with a timer-park between rounds, the existing
harness pattern) — never a spin the deadlock report can't see.

## Docs owed

spec §2 Pipe row (BUILT M4 unit 1: the slice, named honestly — no
reply path yet), §2.1 annotated (built-so-far markers; nothing
ratified reopens), the counted-kinds table (+2 rows), quota table
row, §11; tracker entry closed in place; As-built here (what the
compiler enumerated, deviations argued, findings); SL-13+ only if a
genuine deficiency is met. CLAUDE.md's idiom ruling applies to all
new Saw (statement-position checks stay `match` per SL-12).

## Out of scope

The one-shot pair and abandonment (unit 2); waitability, peer-gone
LEVELS, `Call`/`ReplyRecv`, the send wrappers (unit 3); handles in
messages (unit 4); the keep-mask rider (2.5/4); the money shot
(unit 5); kill; namespaces.

## As built

(Implementer: the slab shape as landed, the zero-arms as landed,
what the compiler enumerated, findings.)
