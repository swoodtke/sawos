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

## Out of scope (as briefed)

- ENDPOINTS keep `Mint` (`pipe-call-orphan`, `give-keep-mask`,
  `pipe-no-call` depend on it; attenuable per-connection authority
  is the design).
- `take`/`post` do NOT grow parks — blocking those directions is
  `wait`/`Call`, shipped; they stay honest polls with an error.
- No change to `reply`/`reply_wait` (already `consumes`), to the
  request side beyond the Mint drop, or to any wire status value.
- The dissolve helper, module split, and lazy-decode backlog items
  stay where they are.

---

# As-built (M4 unit 4.5, Sep 2)

**ALL THREE PARTS LANDED AS BRIEFED. NO DEVIATION FROM §API.** The gate is
green on both arches — 113 cases, 226 assertions — and the ABI's wire values
are untouched, which was the ruling's own prediction.

## What landed, by area

**`kernel/abi` (7 edits).** `PipeReplyOp` gained `Ready = 1` (renumberable,
§5.7) and its table's `static_assert` moved to the new highest case.
`pipe_reply_rights()` and `pipe_request_rights()` dropped `Mint`; the enum
bits and all four universal-bit asserts are untouched. Three doc comments
rewrote: `PipeReplyRight`'s sibling paragraph (now the one-name rule, with
the argument that a sibling can only ever be told `PeerClosed`),
`PipeRequestRight`'s "holds word for word" clause, and the default-sets
banner — which said `Mint` was uniform and now says the two one-shots are the
whole exception list, with the reason being SINGLE USE and nothing else.
`pipe_reply_rights()`'s attenuate-then-give sentence became the keep-mask
pointer the ruling asked for.

**`kernel/core` (2 files with code, 1 with prose).** `dispatch.saw`: the
`Ready` arm (gated on `PipeReplyRight.Wait`), `pipe_ready` (a status-only
read of the same five exchange states `pipe_resolve` reads), and
`park_resolve` — the pending arm. `pipe_resolve`'s two pending branches now
call it, its `Settled` arm gained the composition note, and its mint-refusal
branch now CONSUMES (see Deviations). `refs.saw`: NO CODE, three banner
rewrites — the wake-arm section now says the record does not care which op
parked the thread, `wake_call_reply`'s docstring says the two parked shapes
agree down to the register, and `release_pending_calls`' says it covers a
parked resolver with no arm of its own. `waitables.saw`: untouched.

**`kernel/sysapi` (3 files).** `floor.saw`: `polled_value` retired,
`polled_ok` kept and re-documented as the LEVEL decode, the poll-split banner
rewritten to the new doctrine with the `Channel.try_receive` divergence
recorded at the seam, and `sos_pipe_reply_ready` added beside
`sos_pipe_reply_resolve` (whose docstring now says it parks and consumes on
every path). `lib.saw`: one name added to the facade's re-export list.
`pipe.saw`: `take -> Result<(PipeMsg, PipeRequest), _>`,
`post`/`post_with -> Result<PipeReply, _>`, `PipeReply.ready(&self)`,
`PipeReply.resolve(&var self) consumes`, the two module-docstring paragraphs
rewritten, and `resolve_into_frame` reduced to the file's uniform
take-the-word shape (this file's one documented departure from the
transfer-funnel contract is closed).

**`tests` (26 files touched, 3 packages added).** ~60 call sites swept. THREE
PROGRAMS ARE GENUINE POLLERS and now say so with a `WouldBlock` arm —
`pipe-basics` (fill-until-refused / drain-until-empty), `pipe-send-manual`
(the room leg) and `pipe-wait-room` (the same leg at the level under test);
their `tell` / `take_and_drop` helpers carry the arm inside a diverging
`catch`, which is the idiom ruling's licensed real-work shape. Everything
else lost a guard line into the `try` it already had. `pipe-delegate`'s
`poll_reply` became a `ready()` loop with one final `(move claim).resolve()`
and takes its claim BY VALUE; `pipe-oneshot`'s `resolve_one` did the same
(finding 3).

**Docs.** spec.md: §2's `Pipe` row (a unit-4.5 paragraph), §2.1's resolve
line (rewritten — the `Ok(None)` parenthetical is gone), the §5-era row's
built-so-far block, and §5.7's `consumes`-funnel paragraph (which named
`PipeReply.resolve` as a sentinel-keeper and now names it as the third
consumer). designs/013 D-2 and designs/014's `Resolve` bullet, `Mint`
finding and consumed-handle finding all carry re-rule blockquotes.
`designs/todo.md`'s queue entry is closed in place.

## The wake-arm generalization, argued

**THE PARK NEEDED NO NEW MACHINERY, AND THAT IS THE UNIT'S ONE STRUCTURAL
CLAIM.** `park_resolve` is `consume_entry` with the last step left out:

```
consume_entry:  detach_consumed  →  unbind_handle  →  unref_object
park_resolve:   detach_consumed  →  unbind_handle  →  block_on_call_reply
```

The reference the handle row was holding becomes the PARKED THREAD's, which
is exactly the invariant `pipe_call` establishes when it sets
`PIPE_REPLY_REFS[x] = 1` for a thread rather than for a row. From that line
the two shapes are indistinguishable everywhere downstream, and every one of
these was reached by a second producer without being edited:

- `notify_claim(x)` asks one question — parked thread or subscription — and
  `PIPE_CALLER[x]` answers it the same way for both ops.
- `wake_call_reply(t, x)` writes the same record to `THREADS[t].wait_buf` and
  the same value register. That is not a coincidence to be maintained: a
  `Resolve`'s success IS `op_value(len)` with a `PipeMsgRecord` copied out,
  and so is a `Call`'s; its `PeerClosed` is a bare status, and so is a
  `Call`'s. The two ops were already answer-compatible.
- `release_pending_calls(p)` — the `end_process` mirror — walks threads with
  `call_claim != 0` and drops the claim reference. It never asks which op set
  the tie, so a parked resolver is abandoned by unit 3.5's code and the
  server's later `reply()` answers `PeerClosed` by the ratified rule.
- `exchange_settle`'s "no caller parked" assert and `free_pipe`'s
  "not attached" assert both hold unchanged.

**AND THE ALTERNATIVES INVARIANT SURVIVED.** `kcore.refs`' banner says
`PIPE_REPLY_ATTACH[x]` is provably zero while `PIPE_CALLER[x]` is not, which
was free for a fused call (it mints no handle, so nothing can be attached).
A parked resolver COULD have had an attachment, so `park_resolve` detaches it
— which is ruling 9(a) verbatim (a consuming op detaches implicitly) and
happens to be exactly what keeps the invariant true. One line, two jobs.

**THE COMPOSITIONS RULING 12(b) NAMES NEED NO MECHANISM.** "A resolve
settling first detaches the attachment implicitly" is `park_resolve`'s detach
(and, on the non-parking arms, `consume_entry`'s). "A subscription delivery
settling the slot first" cannot wake a parked resolver: a resolve does not
park on a settled or ready exchange, and `notify_claim` routes to the parked
thread while one exists — so that composition is reached as a resolve finding
`Settled` at ENTRY and answering `PeerClosed` without parking, which is unit
3's arm unchanged. Noted at the arm.

## The unref census

Every site that drops a reference, and what unit 4.5 did to it:

1. **`park_resolve`'s `detach_consumed(PipeReply, x)`** — MOVED, not new. It
   is `consume_entry`'s first step run at the park rather than at the answer,
   and it CANNOT cascade a free: the caller's entry still holds its reference
   when it runs (the unbind is the next line), so the column cannot reach
   zero here.
2. **`park_resolve`'s `unbind_handle` with NO `unref_object`** — the only
   unbind-without-unref in the kernel, and deliberately not a drop: it is the
   transfer of one reference from a table row to a thread. Written as two
   lines in one function so the pairing is visible; the alternative (unref
   then re-ref) would take the column to zero between two lines of one op and
   free the exchange being parked on, which is `move_handle`'s own
   up-before-down rule read backwards.
3. **`pipe_resolve`'s mint-refusal arm now calls `consume_entry`** — a NEW
   unref site, and the one place this unit changed an existing answer's
   accounting (see Deviations). It can cascade a settle and a free; nothing
   reads the exchange after it, and the status is computed BEFORE the consume
   so the answer does not depend on what the cascade did.
4. **`wake_call_reply`'s trailing `unref_waitable(PipeReply, x)`** — same
   site, wider population. The unref-last ordering it already documented is
   what makes the parked-resolver case safe too.
5. **`release_pending_calls`' `unref_waitable(PipeReply, x)`** — same site,
   wider population; the two-pass ordering it already documented is unchanged
   and covers both shapes for the same reason.
6. **`pipe_ready`** — no unref anywhere. Status-only, consumes nothing, on
   every arm including the terminal.
7. **Typed tier.** `PipeReply.resolve` becoming `consumes` REMOVES the
   sentinel write on the success path (a consuming body substitutes for the
   `deinit` body), which changes no release count: the old code wrote
   `NO_HANDLE` so the drop was already a no-op. `PipeReply.ready` releases
   nothing. `PipeReply`'s `deinit` is now reached only by a claim its holder
   chose not to spend — which is what `pipe-dead-claim` and `pipe-wait-reply`
   now exercise, the latter having swapped a spent `resolve` for a `ready()`
   plus a drop (same net effect on the table, one syscall either way, and no
   asserted row reads it).

## Transcript accounting

The harness prints console transcripts only on failure, so the accounting is
over the two things the gate surfaces: the case report, and the `expect_out`
assertion set every console row is matched against.

- **byte-identical**: 108 of 111 existing cases' asserted rows, unchanged in
  content and in order, on both arches.
- **address-only**: none.
- **authorized-with-cause**: 3 cases, 4 rows removed and 6 added.
  - `pipe_oneshot`: `pending is none` → `pending is not ready`. Cause:
    ruling 12(b) — the pending question is `ready()` now, and a `resolve` at
    that line would park.
  - `pipe_no_reply`: `minted without Reply` → `minting a sibling of an
    obligation`. Cause: ruling 12(c) — the case's `MINT_OP` on an obligation
    IS the refusal now (finding 1). Its fault row (`access denied`) is
    byte-identical, and so is its exit status.
  - `pipe_dead_claim`: `resolved len=1` and `process fault: bad handle` out;
    `the server let its obligation go`, `ready says the other end of this
    connection is gone`, `claim dropped unresolved`, `minting a sibling of a
    one-shot` and `process fault: access denied` in. Cause: the brief's
    retarget — the double resolve is a compile error.
- **documented-nondeterministic**: none moved. The three cases carrying such
  rows (`thread_preempt`, `timer_interval`, `process_stats`) assert nothing
  this unit touches and were byte-identical in both runs.
- **new rows**: 14 asserted rows across the two new cases, plus 4 report
  lines (2 cases × 2 arches).
- **mechanical**: every `[n/111]` report line became `[n/113]` (222 lines)
  and the total moved 222 → 226, which is the two new cases arriving.
- **image sizes** (printed, never asserted): 102 images byte-identical, 114
  changed, 3 new. NET −57,832 BYTES across the changed ones — the Optionals
  coming out of the surface. Biggest single win `pipe-dead-claim` at −10,504
  (riscv32); the one image that grew meaningfully is `pipe-send-manual` at
  +6,064, which is the explicit `WouldBlock` match block replacing an
  `if let`.

Nothing else moved.

## Measured

- **`pipe-resolve-park`: `ops resolve=2 fused=1`.** Post plus resolve against
  the fused `PipeInlet.send`, measured by one process against one server in
  one boot, each delta less its own closing `stats()` trap. Unit 3's composed
  spelling is three for the same answer (`pipe-send-manual`'s
  `ops manual=3 fused=1`), so ruling 12(b) took a trap out of the path for a
  client that wants a HANDLE on its exchange.
- **`pipe-oneshot`'s zero-reply bracket stayed at 5** (post + take + reply +
  resolve + the closing read). The resolve does not park there — the reply is
  already written — which is the monotonic-readiness claim showing up as an
  unchanged number.
- The park itself is proved by ORDER, not by a count: `SOS childserver:
  replied` precedes root's reply row in `pipe-resolve-park`, and root holds no
  Waiter at all, so nothing but the child's reply could have produced the
  answer. A park-free resolve would have answered the old `WouldBlock` and the
  program would have failed its own length check.

## The teardown proof: placement, and why

**A SIBLING CASE (`pipe-resolve-orphan` + `child-resolver`), NOT AN ARM ON
`pipe-call-orphan`.** The census decided it on determinism. The proof needs
the ORPHANED thread to be the shape under test, and a two-thread child can
orphan exactly one shape — whichever thread the launcher answers is the one
that exits the process. `child-orphan`'s two threads are interchangeable ON
PURPOSE, which is what lets its launcher answer "one of them" without knowing
which; making them differ would trade that fact for a scan order, and
answering the fused one would LOSE design 17 D-2's proof to gain this one. A
three-thread child would keep both, at the cost of rewriting a passing case
whose value is its own symmetry. Two cases, one spelling each, keeps both
proofs and both symmetries — and the new case is purely additive, so
`pipe-call-orphan`'s transcript is byte-identical.

What the sibling proves is that NOTHING WAS ADDED: it reaches the arm through
`release_pending_calls`, written for unit 3.5, with no branch anywhere asking
which op parked the thread.

## Deviations, argued

**One, and it is a consequence rather than a choice.**

**`pipe_resolve`'s mint-refusal arm now CONSUMES the claim.** The brief's
contract enumerates `Ok` and `PeerClosed` as the consuming paths and says
`WouldBlock` leaves the op; it does not mention the `NoResource` /
`QuotaExceeded` answer a reply carrying handles can produce, which through
unit 4 left the claim ALIVE so a caller could free a slot and resolve again.
That is unwritable under `consumes`: the wrapper is dead at the call, so a
surviving kernel entry would be exactly the leaked reference ruling 12(b)
gives as its reason for putting the effect in the kernel rather than at the
typed tier. So the refusal consumes, on `wake_call_reply`'s existing
precedent (its own comment already argues that a claim which cannot ask again
must be answered terminally) and with the advice written at both sites — a
client that must not lose a capability that way makes table room before it
resolves. No wire value or status changed; one accounting did, in the
direction the ruling's own reasoning points.

## Findings

1. **THE `Mint` DROP TOOK OUT THE ONLY TEST OF `PipeRequestRight.Reply`, AND
   THE RULING'S AUDIT MISSED IT.** Ruling 12(c) says "no in-tree caller mints
   a one-shot sibling"; `tests/pipe-no-reply` did — as the only way to
   produce an obligation handle WITHOUT `Reply`, since `Take` mints the full
   set and a message-carried handle carries the sender's entry rights
   verbatim. The audit was right about PRODUCTION callers and wrong about
   negative tests, which is exactly the category the ruling converts into
   faults. The case retargeted to the one-name rule on the obligation half
   (the mirror of `pipe-dead-claim`'s probe on the claim half), and the
   `Reply` gate — plus `PipeReplyRight.Resolve`, which has the same shape and
   the same hole — is now reachable only through a `give` keep mask into
   another process. Filed in `designs/todo.md`'s backlog with the case shape
   scouted; not done here, because design 22's scope was the discipline
   rather than the coverage the discipline displaced.
2. **`PipeReply.mint` AND `PipeRequest.mint` ARE KEPT, DEAD, AND DOCUMENTED
   AS SUCH** — against this tree's own precedent, deliberately. `Mapping`
   withholds `Transfer` and simply has no `give(mapping:)` overload, and
   `mapping_rights()` grants `Mint` while `Mapping` has no `mint()` method at
   all: the typed tier adds these per kind on demand, so deleting the two
   would have been consistent and would have made the one-name rule a COMPILE
   error, which is stronger. What stops it is that a test cannot then witness
   the rule at run time: `sos_handle_mint` needs a handle WORD, and the
   wrappers' handle fields are `public(package)`, so a test package has no way
   to reach one. The brief asks for the probe "witnessed from Saw", so the
   methods stay, with docstrings saying plainly that they cannot succeed and
   why. If the probe is ever judged redundant, deleting both methods is the
   cleanup — and it deletes two cases' last acts with it.
3. **THE `consumes` EFFECT TOOK A HELPER SIGNATURE WITH IT, TWICE.** A
   `&var PipeReply` parameter cannot be moved into a consuming method, so
   `pipe-delegate`'s `poll_reply` and `pipe-oneshot`'s `resolve_one` both had
   to take the claim BY VALUE. That is the right answer — a helper that
   resolves a claim IS single-use, and the signature now says so — but it is a
   shape the sweep had to notice rather than a mechanical rewrite, and the
   next unit writing a claim helper should reach for it first.
4. **NO SAWLANG DEFICIENCY WAS MET.** No new SL entry. The two shapes this
   unit leaned on hardest — a diverging `catch` containing a `match` over
   `error`, and `(move claim).resolve()` on a by-value NoCopy parameter — both
   compiled first time on sawc 0.4.0, for both arches.
