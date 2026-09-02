# SawOS design 22 — the one-shot discipline (M4 unit 4.5)

Ruling 12 (designs/010, Sep 2, user — in session, three parts taken
together), executed as its own small unit BEFORE unit 5 dispatches,
because unit 5's servers build on this surface. Numbered 22 because
20 (ESP32-C3) and 21 (unit 5) are reserved by the stub pass and the
queue; ladder-wise this is unit 4.5. The general principle is now in
CLAUDE.md too: an Optional in an API signature is an antipattern
unless the absence is a real domain value — transients ride the
error channel, error + retry.

## The ruled surface (#10 ruling 12, cited)

1. **The poll split is retired** (re-rules design 13 D-2). At the
   typed tier `WouldBlock` is an ERROR again: `take` and `post`
   lose their Optionals. D-2 was written at unit 1, when every
   caller polled; units 3/3.5 inverted the population and the tree
   showed it — ZERO `case None` arms in tests/ and root/, every
   site paying `try` plus a guard that treats `None` as a bug.
   The divergence from design 234 §4's `Channel.try_receive` is
   deliberate and gets a sentence at the seam. No ABI change.
2. **`resolve` carries `consumes` and PARKS when pending.** Every
   path consumes; `WouldBlock` leaves the op entirely. The poll
   moves to a new non-consuming `ready()`. States, the user's own
   words: **true = the reply has arrived** (resolve returns it
   without parking), **false = not ready yet and the connection is
   still good**, **error = the connection is gone** — the reply can
   never come, so the caller never spends a resolve on a dead
   exchange; the claim is then simply DROPPED (the wrapper's
   ordinary release), never resolved. Readiness is monotonic
   (pending -> settled, never back), so ready-then-resolve has no
   check-then-act race.
3. **A one-shot has ONE NAME**: `Mint` leaves `pipe_reply_rights()`
   and `pipe_request_rights()`. The use-case audit came up empty —
   attenuate-then-give is `give`'s keep mask, no in-tree caller
   mints a one-shot sibling — and a sibling undermines part 2's
   compile-time single use. The enum bits STAY (universal-bit-1
   doctrine, static_asserts untouched); monotonic attenuation makes
   the absence permanent. `Transfer` still moves the one name, so
   delegation (`pipe-delegate-msg`) is untouched.

## API (PROPOSED — the review gate's subject)

### Kernel ops (kernel/abi)

- `PipeReplyOp.Resolve` — CONTRACT CHANGE, number kept (§5.7):
  - answer written: copy out through the checked funnel, consume
    the caller's entry, `Ok`. Unchanged.
  - can never arrive: consume, `PeerClosed`. Unchanged.
  - PENDING: **PARK the calling thread on the claim's ready level**
    (ruling 8's logic — a park is mechanics, not authority; the op
    spends the existing `Resolve` right). Wake resumes inside the
    op and finishes one of the two arms above. `WouldBlock` is no
    longer a possible answer.
  - The wake arm is the fused call's parked-thread arm GENERALIZED,
    not a third protocol: a claim's interested party is a parked
    thread (fused `Call` or parked `Resolve` — the record does not
    care which op parked it) or a subscription, and the one
    existing dispatch function tells them apart.
  - `end_process` mirror: a thread parked in `Resolve` dies -> the
    entry releases, the claim is ABANDONED, the server's later
    `reply()` answers `PeerClosed` — the same arm unit 3.5 added
    for `Call`, reached from a handle-backed claim.
  - Compositions, defined by EXISTING rules (no new mechanism): a
    subscription delivery settling the slot first wakes a parked
    resolver with `PeerClosed` (the answer went to the waiter);
    a resolve settling first detaches the attachment implicitly
    (ruling 9(a) verbatim).
- `PipeReplyOp.Ready` — NEW (renumberable, §5.7), gated on
  `PipeReplyRight.Wait` (the level question asked non-blockingly;
  `Resolve` stays the consuming act's authority). Status-only, no
  copy-out, consumes nothing: `Ok` = answer written, `WouldBlock` =
  pending + exchange live, `PeerClosed` = it can never arrive.
- Rights: `pipe_reply_rights()` and `pipe_request_rights()` drop
  `Mint`. The two doc comments that argued for the uniform lean
  (`PipeReplyRight`'s sibling paragraph, `pipe_reply_rights`'
  attenuate-then-give sentence) rewrite to the one-name rule.

### Typed surface (sysapi)

```saw
// sos.pipe — Optionals struck (ruling 12(a)):
PipeOutlet.take(&self)              -> Result<(PipeMsg, PipeRequest), SosStatus>
PipeInlet.post(body:, len:)         -> Result<PipeReply, SosStatus>
PipeInlet.post_with(body:, len:, handles:) -> Result<PipeReply, SosStatus>
// WouldBlock = empty/full ring, on the error channel; pollers match it.

// The claim (ruling 12(b)):
PipeReply.ready(&self)              -> Result<Bool, SosStatus>
PipeReply.resolve(&var self) consumes -> Result<PipeMsg, SosStatus>
// call sites spell (move claim).resolve() — second use is a compile error
```

- `floor.saw`: `polled_value` RETIRES with its half of the poll-split
  banner; `polled_ok` SURVIVES as exactly `ready()`'s decode (Ok ->
  true, WouldBlock -> false, the rest to the error channel) and the
  banner rewrites to say so.
- The `_with` variants keep their distinct names (SL-15 unchanged).

## The sweep

~90 call sites in tests/ lose their guard line into the `try`; the
two genuine pollers rewrite: `pipe-delegate`'s `poll_reply` becomes
a `ready()` loop with one final `(move claim).resolve()`, and any
`WouldBlock`-arm matches a poller writes are the idiom ruling's
licensed real-work arms. `pipe-dead-claim` RETARGETS: its
double-resolve is a compile error now (the last Saw-visible ledger
`BadHandle` retires; the kernel check stands for raw callers —
`pipe-reply-wait-dead`'s precedent), and its new proof is the ruled
flow itself: obligation dropped, `ready()` answers the terminal on
the error channel, the claim is dropped WITHOUT resolving, clean
exit.

## The proof (harness)

- `pipe-resolve-park` (NEW): a client posts and calls `resolve`
  with NO waiter while the server takes and replies later — reaching
  done proves the park and the wake (a park-free run would have
  answered the old `WouldBlock`); the trap count shows post+resolve
  = 2 beside `Call` = 1.
- `pipe-dead-claim` retargeted as above (ready's error = no second
  call, the user's own sentence).
- A mint probe rides the retargeted case: `sos_handle_mint` on a
  claim word answers `Denied` (right absent), the one-name rule
  witnessed from Saw.
- Parked-resolver teardown: an arm on the existing death machinery
  (`pipe-call-orphan`'s shape at the handle-backed claim) — the
  unit brief may fold it into that case or add a sibling; census
  decides.
- The full gate: `make sos-test`, both arches, transcript
  accounting with the moved rows bucketed authorized-with-cause
  (the sweep touches most pipe cases' plumbing but should move few
  printed rows; anything unexpected is a finding).

## Docs owed

- spec.md: the §2.1 pipe row (resolve/ready contract, one-name
  rule), line ~269 ("pending is `WouldBlock` (`Ok(None)` at the
  typed tier)" — the parenthetical dies), the §5-era row at ~2414.
- `floor.saw`'s poll-split banner; the two abi rights comments;
  design 13 D-2 gets a re-rule pointer to ruling 12; design 14's
  claim/obligation prose where it names `WouldBlock` at resolve.
- SL-16's give-internals note is UNAFFECTED (parameter-position
  funnels; the dissolve-helper backlog item stands).

## Out of scope

- ENDPOINTS keep `Mint` (`pipe-call-orphan`, `give-keep-mask`,
  `pipe-no-call` depend on it; attenuable per-connection authority
  is the design).
- `take`/`post` do NOT grow parks — blocking those directions is
  `wait`/`Call`, shipped; they stay honest polls with an error.
- No change to `reply`/`reply_wait` (already `consumes`), to the
  request side beyond the Mint drop, or to any wire status value.
- The dissolve helper, module split, and lazy-decode backlog items
  stay where they are.
