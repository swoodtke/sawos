# SawOS design 17 — the fused paths: Call and ReplyWait (M4 unit 3.5)

Status: **LANDED Sep 1 2026** (was AUTHORED Sep 1, lead); **§API USER-REVIEWED SAME DAY (the
review gate, discharged in three rounds): `reply_wait` ON THE REQUEST
with the required waiter and the leg-tagged `ReplyWaitError`; `Call`
under the existing `Post` right; `send -> Result<PipeMsg, SosStatus>`
as the Call wrapper — all user-ruled. Dispatches when design 16
(stats) integrates.** Ladder unit 3.5, split from
unit 3 as its brief provided: #10 ruling 4's rider (Aug 31) with the
Sep-1 reply-then-wait amendment, DATA-ONLY (reply handles ride unit
4's rendezvous exactly as `Post` grew its return — §5.7). The proof is
the ping-pong that COUNTS TRAPS, asserting over design 16's stats.

## The ruled surface (#10 ruling 4 + rider + amendment, cited)

- **`Call` = post; park; resolve behind ONE trap** (client). The
  claim is KERNEL-INTERNAL, tied to the parked thread, consumed at
  reply — no `PipeReplyHandle` minted, zero handle traffic on the
  fast path (`post` keeps minting the real handle for multiplexers).
  The reply body bounces through the ring slot; the caller's thread
  resumes inside its own syscall and copies out through its own
  checked funnel. NO duration argument — the kernel still never
  learns what a timeout is.
- **The wake dispatch grows ONE arm**: a claim is a parked fused call
  → wake the thread, or a split-phase claim → notify the Waiter — not
  a second wake protocol.
- **`ReplyWait` = reply-then-WAIT** (the Sep-1 amendment): discharge
  the request (consumed, 9(a)), park on the WAITER exactly as plain
  `wait` parks, resume with the next delivery. THE OP LIVES ON THE
  REQUEST (user, Sep 1): `reply_wait(waiter:)` — a reply with a
  built-in wait. No degenerate form: the first turn and the recovery
  are the existing `waiter.wait()`, reply-without-wait is the
  existing `reply()`. THE RETURN IS THE DELIVERY OR A LEG-TAGGED
  ERROR (user, Sep 1): a failed or abandoned reply returns
  `Err(Reply(...))` without parking. The request is CONSUMED on every
  path. One dead client still cannot stall the server — the loop
  never wedges.
- **`end_process` gains the orphaned-claim arm**: a process dying
  parked in a `Call` abandons the claim; the server's later `reply()`
  answers `Err(PeerClosed)`, obligation discharged (ratified rule).
  Mirror: the server end dying while a client is parked in a `Call`
  wakes that thread with `PeerClosed` through the one wake arm.
- **The proof counts traps**: client 1 trap/RPC under `Call` vs 3
  under the manual composition; server 1 trap/message steady-state
  under `ReplyWait` vs 2 (wait + reply). Asserted EXACTLY over
  `ProcessStats.syscalls` (design 16), both arches.

## API (PROPOSED — the review gate's subject)

### Kernel ops (kernel/abi)

- **`PipeInletOp.Call`** — args: body addr + len (≤ `PIPE_BODY_BYTES`),
  reply buffer addr + cap (≥ `PIPE_BODY_BYTES`). Gated by
  **`PipeInletRight.Post`** — no new right: a `Call` submits a message
  exactly as `post` does, and the fusion is mechanics, not authority
  (the park needs no Wait right — the claim is thread-tied, no Waiter
  involved). Answers: reply length on success; `PeerClosed` (outlet
  side gone, before or during); `WouldBlock` NEVER (the fused form
  parks on room exactly as the composed send did — ruling 8's level,
  kernel-internal here).
- **`PipeRequestOp.ReplyWait`** (USER-PLACED, Sep 1: on the REQUEST —
  "a reply with a built-in wait", the reply being the primary act
  that consumes the receiver, the wait the fused tail) — args: waiter
  handle word, reply body addr + len, record buffer addr + cap (the
  unit-3 record). Gated by **`PipeRequestRight.Reply`** on the
  request plus the waiter's own wait authority (both checked). The
  request is CONSUMED on every path (9(a) + the funnel contract).
  Kernel sequence plain: reply; on refusal return status + leg tag,
  no park; else park; return the record. NO degenerate form and NO
  optional waiter — reply-without-wait is `PipeRequestOp.Reply`
  (unit 2, exists), and the first turn is `WaiterOp.Wait` (exists):
  an optional waiter would change the return type on a runtime
  value, the exact shape ruling 4 and 11(c) rejected. `WaiterOp` is
  untouched by this unit.

### Typed surface (sysapi)

```saw
// pipe.saw — the client. §2.1's ratified send arrives, as the Call trap:
public func send(&self, body: &[UInt8; PIPE_BODY_BYTES], len: UInt)
    -> Result<PipeMsg, SosStatus>        // on PipeInlet; blocks; Err(PeerClosed)
                                         // is the abandonment answer (resolve's
                                         // own convention, unit 2)

// pipe.saw — the server's fusion, ON THE REQUEST (user-ruled, Sep 1):
public enum ReplyWaitError {             // design-234 tier 2: the leg
    case Reply(status: SosStatus),       //   names the op that failed
    case Wait(status: SosStatus),        // Wait => the reply LANDED
}
public func reply_wait(self, body: &[UInt8; PIPE_BODY_BYTES], len: UInt,
                       waiter: &Waiter) -> Result<WaitResult, ReplyWaitError>
// Consuming receiver (by-value self — the transfer-funnel contract,
// consumed on EVERY path: obligation discharged either way, §2.1).
// The waiter is REQUIRED: reply-without-wait is the existing
// `reply()`, and the first turn is the existing `waiter.wait()` — no
// sentinel, no optional, every signature total.
```

The steady-state server loop, as it reads:

```saw
var wake = try waiter.wait() catch { ... }       // the first turn IS wait
while serving {
    let (req, msg) = decode(wake)                // unit-3 payload cases
    let n = handle(msg, &var out)
    wake = try req.reply_wait(body: &out, len: n, waiter: &waiter) catch {
        match error {
            case Reply(_) -> try waiter.wait() catch { ... },  // dead client:
                                                 // the recovery IS the first turn
            case Wait(s) -> { /* reply landed; the wait itself broke */ ... },
        }
    }
}
```

## D-1: the parked-call state, and the one wake arm

A thread parked in `Call` records its claim in the thread slot (the
exchange index; the claim state rides the unit-2 exchange arrays — no
new storage kind). `drop_reference`'s wake path asks one question at a
settling exchange: thread-tied claim → wake that thread with the
outcome in its trap frame; else → the unit-3 notify. Every rule the
split-phase path obeys applies verbatim (the rider's own sentence) —
peer-gone, abandonment, drain order, the room level.

## D-2: teardown

`end_process`'s sweep gains the orphaned-claim arm (a dying process's
parked calls → claims ABANDONED, servers' later replies answer
`Err(PeerClosed)`); the close-all handles the rest (columns fall, the
unit-2/3 machinery fires). Interruptibility: the park sits at the
same preemption/wake seam plain `wait` uses (M3 unit 1.5's points
cover it — verify, record either way).

## The proof (harness)

1. **`pipe-pingpong`** — root serves via `reply_wait`, a child client
   calls `send` in a counted loop (both from design 16's stats):
   asserts client syscalls == N + brackets EXACTLY, server == N + 1
   (the degenerate first turn) + brackets; prints both columns; the
   payload round-trips byte-exact. THE ladder's performance claim as
   a transcript row, both arches.
2. **`pipe-call-abandon`** — server drops the request mid-call → the
   parked client wakes `Err(PeerClosed)`; client process dies parked
   in a call (child variant) → server's later reply `Err(PeerClosed)`;
   nothing un-closes.
3. Negative arms where cheap: `Call` through a mint lacking `Post`
   refused; `reply_wait` on an already-consumed request (the
   dead-handle fault); oversized reply len `BadArg`.
4. `pipe-send-manual` gains a companion row: the same exchange done
   both ways, trap counts printed side by side (3 vs 1) — the
   refactor-into-kernel-call story, proven.

## Docs owed

spec §2.1: `send` flips to BUILT (as the `Call` wrapper — the manual
composition note stays for the timeout shape); §2.2: the ReplyWait
return shape beside the record; §3: the wake arm; §5.7 note at both
op growths. Tracker entry closed in place; As-built here; SL-N for
genuine deficiencies.

## Out of scope

Reply handles in either direction (unit 4, with rendezvous and
11(d)'s completion-queue outlet); any timeout (a future kernel op by
ruling, when a consumer names itself); per-thread stats; the shared
stats region (design 16's seed).

---

# As-built (Sep 1 2026)

BUILT AS BRIEFED, with ONE FORCED DEVIATION FROM THE REVIEWED §API that the
lead/user must ratify (deviation 1 — the consuming receiver is not expressible
in sawc 0.3.0) and four argued deviations in D-level prose. Base `4773f7f`
(suite 198/198); gate 210/210, 105 cases per arch, `-j 4`, both profiles.
sawlang HEAD `87063387` at dispatch and unchanged at the gate; `sawlang.pin`
untouched.

## What landed

- **`PipeInletOp.Call = 1`**, gated by the EXISTING `PipeInletRight.Post`.
  Arguments (body, length, reply buffer); the value is the REPLY's length.
  It never answers `WouldBlock` — a full ring parks on the inlet's
  room-to-post level — and takes no duration. It MINTS NOTHING.
- **`PipeRequestOp.ReplyWait = 1`**, gated by `PipeRequestRight.Reply` here
  and `WaiterRight.Wait` on the waiter the args record names. Arguments
  (args record, args bytes, wait buffer); the answer is a delivered wait
  record, or a status beside a `ReplyWaitLeg`.
- **`ReplyWaitLeg { Wait = 0, Reply = 1 }`** and
  `PIPE_REPLY_WAIT_RECORD_WORDS = 3` (waiter, body address, body length) in
  `sosabi`, with `pipe_reply_wait_record_bytes()`.
- **The thread-tied claim**: `PIPE_CALLER` (per exchange, thread + 1) and
  four fields on `ThreadSlot` (`call_pipe`, `call_claim`, `call_src`,
  `call_len`), all zero-init and therefore `.bss`.
- **The wake arm**: `kcore.refs.notify_claim` — the ONE place the kernel
  asks whether a claim is a subscription's or a parked thread's — with
  `notify_room` beside it for the room level's own park, and
  `wake_call_reply` / `resume_call_room` as the two resumes.
- **`end_process`'s orphaned-claim pass** (`release_pending_calls`), ahead
  of the close-all, in two passes.
- **`copy_in_check`**, split out of `copy_in` exactly as `copy_out_check` is
  split out of `copy_out`.
- **The typed surface**: `PipeInlet.send(body:len:) -> Result<PipeMsg,
  SosStatus>`, `PipeRequest.reply_wait(body:len:waiter:) ->
  Result<WaitResult, ReplyWaitError>`, and `ReplyWaitError { Reply(status:),
  Wait(status:) }` with `Printable` + `Error`. `sos.waiter.decode_wait`
  became `public(package)` so the second door reads ONE decode.

## The topology, and why root serves

The brief asked for the choice to be made and stated. **ROOT SERVES AND THE
CHILD CLIENTS**, for three reasons in descending order of force:

1. **THE CLIENT'S MEASURED WINDOW IS ITS WHOLE LIFE, so the client must be
   silent.** Every printed byte is one `ecall` (design 16 finding 1), so an
   exactly-asserted count and a `print` cannot share a process — and a silent
   process cannot report its own number, so its launcher reads the column
   through the handle that outlives it. That is design 16's own arrangement,
   and it is the only one in which the CLIENT's per-RPC cost — the headline
   number of this unit — is measured rather than claimed.
2. **THE SERVER'S WINDOW CLOSES BY ITSELF, AT ZERO COST.** The child's exit
   takes the last inlet handle with it, the outlet's readable level turns
   terminal, and root is ALREADY PARKED in the wait that delivers `PeerGone`.
   So the loop needs no counter, no timer and no sentinel message, and the
   arithmetic has no termination term in it. Reversed, root would need a
   termination protocol it could not run without printing inside its own
   window.
3. Reversed, the exactly-asserted count would belong to the process that also
   has to print the report.

## The measured numbers

`tests/pipe-pingpong`, both arches, eight round trips:

    SOS pingpong: served=8 last request b=7,80,73,78
    SOS pingpong: client traps=11 for 8 round trips
    SOS pingpong: server traps=18 for 8 messages
    SOS pingpong: per RPC client=1 server=2

    CLIENT  process_self 1 + boot_handle_next 1 + 8 sends + exit 1  = 11
    SERVER  wait 1 + 8 x (take 1 + reply_wait 1) + closing read 1   = 18

`tests/pipe-send-manual`, the same exchange both ways against one server in
one boot:

    SOS sendmanual: reply len=4 b=80,79,78,71
    SOS sendmanual: the round trip cost 4 traps
    SOS sendmanual: fused reply len=4 b=80,79,78,71
    SOS sendmanual: ops manual=3 fused=1

**BOTH NUMBERS WERE PREDICTED FROM THE COUNTING RULE BEFORE EITHER CASE WAS
RUN, AND BOTH WERE RIGHT FIRST TIME**, which is the claim design 16 exists to
make and this unit is its first consumer.

**THE SERVER IS TWO TRAPS PER MESSAGE AND NOT ONE, AND THE BRIEF'S "N + 1" IS
OFF BY THE TAKES.** The brief predicted `server == N + 1` and contrasted
`ReplyWait` (1) against `wait + reply` (2); both counts omit the `take`, which
neither shape can avoid — the readable level says "there is something", never
"here it is". So the honest comparison is take + reply_wait (TWO) against wait
+ take + reply (THREE), and what the fusion removed is exactly one trap per
message. Ruling 11(d)'s delivery-as-take is what takes the server to one, and
it is unit 4's. The CLIENT's number is the brief's exactly: one against three.

## D-1 as built: the one wake arm, and the second one beside it

`notify_claim(x)` asks one question — `PIPE_CALLER[x]` — and routes to
`wake_call_reply` or to the unit-3 `notify_if_ready`. Every producer of
reply-readiness goes through it: `pipe_reply_status`, `drop_reference`'s
`PipeRequest` arm, and `notify_claims`' ring scan. **A THREAD-TIED CLAIM CAN
NEVER BE ATTACHED**, which is what makes the two branches alternatives rather
than two things to do: a fused call mints no handle, so there is no word
anybody could pass to `Waiter.Add`, and `PIPE_REPLY_ATTACH[x]` is provably
zero while `PIPE_CALLER[x]` is not.

`wake_call_reply` is `deliver_attachment`'s mirror image and deliberately so:
the same three acts in the same order — `waitable_answer` reads, then
`waitable_consume` spends, then the reference drops — differing only in where
the answer goes, because a `Call` resumes inside its own syscall rather than
inside a `Wait`. The unref is LAST because it can settle the ring slot and
cascade to a freed connection, and `exchange_settle` asserts nobody is parked.

**THE ROOM LEVEL NEEDED ITS OWN ARM, WHICH THE BRIEF'S "ONE ARM" DID NOT
ANTICIPATE**, and it is a consequence of ruling 8 rather than a second wake
protocol: `Call` never answers `WouldBlock`, so a full ring is a PARK, and the
level that ends it is the inlet's — which had exactly two producers
(`settle_exchange` and the outlet's zero-arm). `notify_room` replaces
`notify_if_ready(PipeInlet, …)` at both, asks the same question and routes into
the same kind of resume. It LOOPS where `notify_claim` does not, because room
is a connection-wide level several threads may wait on: an ordinary settle
serves one caller and the next finds no room, while a PEER-GONE transition
serves every one of them, which ruling 2 requires.

**A ROOM-PARKED CALL IS ON NO LIST AND IS FOUND BY A SCAN** (`call_room_waiter`,
bounded by `MAX_THREADS` = 8). A chain was considered and rejected: `ThreadSlot.link`
is already spoken for by the ready queue the resumed thread goes straight onto,
so a second list would be a second thing to keep in step — the trade
`alloc_thread`, `live_threads` and `ready_remove_process` all already take one
file down.

## D-2 as built: teardown, and the interruptibility check

`release_pending_calls(p)` runs FIRST in `end_process`, ahead of the close-all,
because the close-all drops endpoint columns and every one of those transitions
notifies — and a notification must never find a thread of the process being
torn down. It is TWO passes over `MAX_THREADS`: room-waiters first (which hold
no exchange and therefore no reference, so clearing them cascades nothing),
then claim-holders (whose unrefs CAN deliver, and by then this process has no
room-waiter left for a delivery to find). Dropping the claim IS the
abandonment: the column falls to zero, which `pipe_reply_status` already reads
as abandoned-by-client, so the server's later `reply()` answers
`Err(PeerClosed)` with no new vocabulary anywhere.

**INTERRUPTIBILITY: VERIFIED, AND THERE IS NOTHING TO ADD** (the brief asks for
the check either way). Both parks sit at exactly the seam plain `wait` uses —
`ThreadState.Blocked` plus `NEED_RESCHED`, answered by `write_result` into the
parked frame — and neither op contains an unbounded loop. `pipe_call` is
`pipe_post`'s body plus a park: one bounded body copy of at most
`PIPE_BODY_BYTES`, one `alloc_exchange` scan of `PIPE_INFLIGHT` = 16, both
already covered by design 1's audit at `pipe_post`. `pipe_reply_wait` is
`pipe_reply` plus `waiter_wait`, and both were audited in their own units. The
two NEW scans are `call_room_waiter` (`MAX_THREADS` = 8) and
`release_pending_calls`' two passes over the same table — smaller than
`ready_remove_process`, which the same section already covers. No preemption
point is wanted anywhere in this unit.

## What the compiler enumerated

1. **`ThreadSlot` grew four fields**, so every literal of it failed until
   updated — 5 sites across `threads`, `dispatch` (2), `process` and `sched`.
   The compiler asking five times is what makes "a thread parked in a call
   carries its call" a property of every door that makes a thread rather than
   of the one that parks it.
2. **`pipe_inlet_op` and `pipe_request_op` grew a third argument**, so the
   dispatch's two arms failed until widened.
3. **`PipeInletOp` and `PipeRequestOp` each grew a case**, so
   `pipe_inlet_op_decoded` and `pipe_request_op_decoded` failed as
   non-exhaustive until each new op had an arm — which is the raw-backed
   enum's own promise collected twice more.
4. **The two `< MINT_OP` asserts moved** to their tables' new highest cases,
   which is the rule `sosabi` already states (one assert per table, on its
   top).
5. `SosStatus` needed NOTHING and `QuotaKind` needed nothing: `PeerClosed` says
   here exactly what its unit-1 docstring says it says, and a fused claim mints
   no quota row — which is design 14 D-1's "a claim mints no quota row" holding
   at the one place the brief predicted it would matter.
6. **`WaitableKind` did not grow**, and that is the unit's structural claim: a
   fused call reuses the `PipeReply` and `PipeInlet` levels rather than adding
   a kind, so the five-question matrix was not touched at all.

**THE ENUMERATION PROPERTY WAS VERIFIED RATHER THAN ASSUMED** (the design-8
tradition): deleting `pipe_inlet_op_decoded`'s `case Call` and compiling gives
``error: match is not exhaustive, missing variants: `Call` `` anchored at the
`match`, and the arm was restored.

## Deviations from the brief, argued

1. **THE REVIEWED `reply_wait(self, …)` IS NOT EXPRESSIBLE, AND THIS IS THE ONE
   DEVIATION THE USER MUST RATIFY.** §API spells a CONSUMING RECEIVER; sawc
   0.3.0 answers ``Parse error: 'self' must be a reference: use '&self' or
   '&var self'``, probed minimally before anything was written on top of it
   (filed as SL-16). The two things the review ruled — **the op lives ON THE
   REQUEST** and **the receiver is consumed** — are jointly unwritable today,
   so one had to give. **THE PLACEMENT WON**, because it is the more strongly
   and more often stated of the two (`designs/010` ruling 4's amendment says
   "THE OP LIVES ON THE REQUEST (user, Sep 1)" and repeats the reasoning
   twice), and because the tree ALREADY HAS A SPELLING for the consume:
   `PipeRequest.reply` is `&var self` + disarm-before-the-syscall and its own
   docstring calls that "the transfer-funnel contract verbatim". So
   `reply_wait` is that shape one method down, and it is observably identical —
   the request is consumed on every path, and a second use through the husk is
   the diagnosed `BadHandle` fault (`tests/pipe-reply-wait-dead` proves it).
   What is LOST is a compile-time use-after-consume error, which `reply` does
   not have either. The alternative that would have kept a true by-value
   consume — moving the op onto the Waiter, `Waiter.give`'s shape — was NOT
   taken, because it contradicts the more strongly ruled half.
2. **`Call`'S REPLY BUFFER TAKES NO CAPACITY ARGUMENT, and `ReplyWait`'s wait
   buffer takes none either.** §API says "reply buffer addr + cap" and "record
   buffer addr + cap"; §5.7 gives an op three argument registers, and both ops
   need the third for something else. This is design 14 deviation 1 verbatim,
   including its argument: the buffer is checked against the published size of
   the caller's REAL memory through the copy door, which is STRICTER than the
   number a caller types — the old check caught a caller that ADMITTED its
   buffer was short, and this one catches a buffer that IS. A `sos_syscall4`
   was the alternative and was not taken, for design 14's reason: a HAL change
   on both profiles to carry one number that is not trusted anyway.
3. **`ReplyWait`'S THREE SMALL ARGUMENTS RIDE A COPY-IN RECORD.** With the cap
   dropped it still has four things to say — a waiter, a reply body, a length,
   and where the delivery goes. So the three words travel through the copy-IN
   funnel `Timer.Arm` opened and the register that is left carries the wait
   buffer. **THE BODY STAYS AN ADDRESS RATHER THAN JOINING THE RECORD**, which
   is the one real design decision in the layout: a record holding the body
   itself would make every `reply_wait` copy `PIPE_BODY_BYTES` in USERSPACE
   before the trap, where an address costs one word and the kernel's own
   copy-in is then the only copy there is.
4. **`copy_in` GREW AN EARLY-CHECK TWIN, AND A PARAGRAPH OF THE SPEC WAS
   AMENDED RATHER THAN WORKED AROUND.** `copy_in`'s docstring said no
   `copy_in_check` was owed because "no inbound op can have that shape: a
   copy-in happens while the calling process is still the running one, by
   construction". The fused `Call` is the op that broke that sentence — a full
   ring parks it with its message still in the caller's memory, so the copy
   happens at the RESUME, in whichever context freed the slot. The rule
   generalizes rather than bending: a door that can be used late owes an early
   check, both directions, for `Waiter.Wait`'s own reason.
5. **THE UNIT SHIPPED SIX PROOF CASES WHERE THE BRIEF LISTS FOUR ITEMS**, and
   the extra two are the fault cases the brief's "negative arms where cheap"
   asks for: a fault ends root, so one refusal per case is the only shape
   available (design 16's own finding 3). The orphaned-claim arm also became
   its own case rather than an arm of `pipe-call-abandon`, because reaching it
   needs a TWO-THREAD child and root's pipe quota is 2.

## The proofs, and how each is made deterministic

- **`pipe-pingpong`** (+ `child-pingpong`): the trap counts above, the payload
  round trip proved from both ends (root prints the last request's bytes; the
  child's exit code says every one of its eight replies carried ITS OWN
  sequence number plus one), both windows print-free, both arches.
- **`pipe-call-abandon`** (+ `child-caller`): two connections, two ways to kill
  a parked call — the server TAKES and drops the obligation (`Taken`, request
  column to zero) and the server END goes with the message still STAGED
  (`Staged`, connection's outlet column to zero) — and both must answer
  `PeerClosed` at the client. The child's exit code is a CONJUNCTION root could
  not have observed itself. Deterministic because the child is single-threaded
  and its calls park: it cannot reach its second connection until root has
  killed the first.
- **`pipe-call-orphan`** (+ `child-orphan`): the D-2 arm. `child-orphan` is TWO
  THREADS DOING THE SAME THING — one fused call each, and `ProcessOp.Exit` if
  it is answered — so root answering exactly one ends the process with the
  other still parked, and root never has to know which thread it is talking to.
  That symmetry is what makes it deterministic; root's two takes are separated
  by a PARK rather than a poll, because nothing is staged until the sibling
  runs. Both threads reach the pipe without passing a handle word, because root
  MINTS a second inlet and gives both — a sibling is a second name on one
  connection, which is design 13 D-1's `dup` precedent doing work.
- **`pipe-no-call`** / **`pipe-call-oversized`** / **`pipe-reply-wait-dead`**:
  the three refusals. The first is `pipe_no_post`'s case at the second op on
  the same object, and the pair is what says the two ops share one gate; it
  faults BEFORE it could park, which is worth its own case for a blocking op.
  The third is `pipe_dead_claim`'s shape on the server side and reaches a fault
  an ordinary program cannot meet, which is design 14 deviation 7's point made
  once more.
- **`pipe-send-manual` gained the companion window** (proof 4). `child-server`
  serves TWO messages now — persistent attach, a two-turn loop — so the same
  process measures the same exchange against the same server both ways in one
  boot. Nothing about the server differs between the turns, which is what makes
  the two numbers comparable.

## Transcript accounting

Whole-report diff, base `4773f7f` (the baseline captured before the first edit)
vs the gate, ANSI stripped and bucketed mechanically (case rows
`[n/m] MARK name`, image rows `path (N bytes)`, everything else):

| bucket | rows | what |
|---|---|---|
| byte-identical | 198 case-row MARKS + names + INDICES | every pre-existing case, same mark, same name, same index within its arch. `thread_preempt`, `timer_interval` and `process_stats`' `interrupts=` — CLAUDE.md's three timing rows — included, so the documented-nondeterministic bucket is EMPTY at the report level this run |
| byte-identical | 88 image-size rows | 86 arm64 and 2 riscv32 (`.build` blade artifacts). Every arm64 image but two absorbed the growth below in page granularity |
| authorized-with-cause | 198 case-row denominators | `[n/99]` -> `[n/105]`, the DENOMINATOR alone, because six cases were appended. ZERO indices moved, which is what says appended rather than inserted |
| authorized-with-cause | 88 image rows, riscv32, **+48 or +64 each** | every riscv32 image that links `sos`: 46 by +48 and 42 by +64. The cause is the two new `@export`ed seams (`sos_pipe_inlet_call`, `sos_pipe_request_reply_wait`) — an exported symbol cannot be dead-stripped, so a package that never calls either still carries the stubs — plus `sosabi`'s two new op cases and the reply-wait record. The two-valued split tracks alignment, not two causes: every image took the same code and landed on one of two padding boundaries |
| authorized-with-cause | 2 image rows, arm64, +4096 | `pipe-send-manual` and `process-stats`, one page each. The same growth lands on every arm64 image and is absorbed by 4 KiB granularity everywhere else; these two are the packages whose growth crossed a boundary |
| authorized-with-cause | 1 image row, riscv32, +6160 | `pipe-send-manual`'s own code: the second measured window, the fused call, the two new prints |
| authorized-with-cause | 1 image row, riscv32, **-744** — THE ONLY SHRINK | `child-server`, and it was DECOMPOSED rather than assumed. Controlled builds against the SAME kernel: baseline source 21808, the loop change alone 21736 (**-72** — persistent attach and a two-turn loop), the landed source 21016 (**-720** — moving the one `replied` line from `System.debug_print(String)` to freestanding `print`). Baseline was 21760, so the kernel growth contributed the same **+48** every other riscv32 image took and the child's own source is worth **-792**. See finding 3 |
| address-only | 0 | nothing printed an address that moved |
| documented-nondeterministic | 0 | none met |
| new | 12 case rows + 18 image rows | the six cases and nine packages, on both profiles |
| authorized-with-cause | 1 summary row | `198 passed` -> `210 passed` |

No row is unaccounted for.

**THE MOST INTERESTING THING THE DIFF DOES NOT SHOW IS `pipe-send-manual`'s AND
`pipe-oneshot`'s EXISTING ROWS.** `reply len=4 b=80,79,78,71`, `filled=16 then
room says there is space`, `timed out`, `after cancel …`, and both ring-depth
rows all still say what they said, byte for byte, with a whole second op on two
kinds and a new park state underneath them. What DID move inside
`pipe_send_manual`'s console transcript is the ORDER of two rows — root's
report of the first round now precedes the child's death instead of following
it, because the child no longer dies at the first reply — and the runner's
`expect_out` is order-sensitive, so that move is asserted rather than tolerated.
Every string in that list is byte-identical; two moved and two are new.

## Findings

1. **THE BRIEF'S SERVER ARITHMETIC WAS OFF BY THE `take`, AND THE UNIT IS
   BETTER FOR HAVING MEASURED IT.** "server == N + 1" and "1 trap/message
   steady state" both omit an op neither shape can avoid: a readable delivery
   says there is something, not here it is. The claim that survives is exact
   and still strong — the server goes from three traps a message to two, the
   client from three to one — and the number that would make the server one is
   ruling 11(d)'s delivery-as-take, which is unit 4's and now has a measured
   baseline to beat.
2. **"ONE WAKE ARM" TURNED OUT TO BE TWO, AND THE SECOND ONE IS RULING 8's
   BILL.** The rider's wake-arm sentence is about the SETTLING path and is
   exactly right there. What it does not price is that `Call` never answering
   `WouldBlock` gives the op a SECOND park state — waiting for room, on a level
   whose producers are different functions — so the room level needed the same
   treatment. Both are the same shape and route into one resume, so the
   property the rider wanted (not a second wake protocol) holds; what grew is
   the number of levels a thread can be tied to, from one to two.
3. **A CONSOLE LINE COSTS 720 BYTES MORE THROUGH `System.debug_print` THAN
   THROUGH `print`, ON RISCV32.** Measured, not inferred (see the accounting's
   shrink row). Design 16 finding 1 recorded the print seam as the dominant
   SYSCALL cost; this is the same seam showing up as IMAGE SIZE, and in the
   opposite direction from the intuition that the typed method is the cheap
   one. Worth knowing for the buffered-`debug_print` backlog item, which will
   want to know what it is competing with.
4. **A ROOM-PARKED CALL IS IMPLEMENTED AND NOT PROVEN, and that is stated
   rather than hidden.** Ruling 8's level is what a full ring parks on, and
   `resume_call_room` is written, audited and reachable — but a synchronous
   client has at most one exchange outstanding, so filling a ring takes a
   client that mixes `post` with `send`, or several threads calling at once. No
   case in this unit does either. The nearest existing exercise is
   `pipe-send-manual`'s room leg, which proves the LEVEL and not the fused
   park. A case for it is cheap whenever a unit wants it: fill the ring with
   TELLs, call `send` from a sibling thread, and settle one exchange.
5. **THE TWO PARKS NEEDED FOUR THREAD FIELDS AND NO NEW LIST, WHICH IS THE
   REFERENCE MODEL DOING THE WORK AGAIN.** Design 15 finding 1 recorded that
   its cascade census found invariants already holding; the same thing happened
   here from the other side. A parked caller counts ONE on the exchange's claim
   column exactly as a handle entry or an attachment would, so nothing about
   settling, freeing or cascading needed a special case — `exchange_can_settle`,
   `pipe_frees_now` and `exchange_settle` were all correct as written, and the
   only additions were two `fatal_kernel` invariants asserting what the model
   already guarantees.
6. **`MAX_PROCESSES = 2` SHAPED EVERY PROOF AND COST NOTHING.** Each case has
   exactly one client and one server, so the arms that need a THIRD party — a
   second client filling a ring, a delegated request replying to an original
   caller — are unit 4/5's by construction. What it did force is the
   two-thread child in `pipe-call-orphan`, which turned out to be the RIGHT
   shape anyway: nothing in SOS can kill a thread from outside, so a process
   that dies with a thread parked has to end itself, and symmetry is what makes
   that deterministic.
7. **ONE NEW sawlang DEFICIENCY: SL-16** (no consuming `self` receiver),
   minimally reproduced. It is the only one this unit met — SL-13's `&var
   [T; N]` restriction never came up, because every buffer here is either a
   local `var` array or a `&` parameter the callee only reads, and SL-14's and
   SL-15's shapes did not arise.
8. **THE IDIOM HELD.** Every bind-or-bail in the nine new packages and the ten
   kernel/sysapi files is the inline `try … catch` guard form or a plain
   propagating `try`. The `match` sites that remain are negative-test arms
   asserting a specific status (`child-caller`'s two, `pipe-call-orphan`'s one,
   the three refusal cases' one each) and the two `readable_msg` /
   `process_status` record decoders, which are genuinely-different-arms matches
   with a panic default rather than folds.

## Rider — SL-16 closed post-integration (Sep 2 2026)

The unit shipped with the sentinel discipline because the language had no
consuming receiver; finding 7 filed that as SL-16 and the tracker marked it
fix-in-flight. sawlang grew `consumes` at the FOURTH pin bump (design 260,
sawc 0.4.0 @ `46eebb36`) and the conversion landed here as its own change,
against this unit's shipped surface. What moved:

- **`PipeRequest.reply` and `PipeRequest.reply_wait` are `consumes` methods.**
  The declaration keeps `&var self` and adds the word in the effect slot; every
  caller spells `(move obligation).reply(...)`. Both consume on EVERY path they
  can take — `reply` on `Ok` and on `PeerClosed`, `reply_wait` on `Ok` and on
  both legs of `ReplyWaitError` — which is exactly the contract the effect
  expresses, so the conversion is a spelling change and not a semantic one.
- **The sentinel retired at those two ops.** `reply_body` and `reply_wait_body`
  no longer take the wrapper by reference and no longer write
  `PipeRequestHandle(NO_HANDLE)`; they take the WORD, like `post_body`,
  `call_body` and `take_into` beside them. A consuming body substitutes for the
  type's `deinit` body on the consumed path, so the husk that used to carry the
  sentinel does not exist, the handle field's synthesized drop is a no-op, and
  no release fires. The custom `deinit` stays — it still serves the ordinary
  drop path, which is what an unconsumed obligation takes.
- **`PipeReply.resolve` DID NOT convert, and the reason is this unit's own
  documented departure read forward.** §2.1's poll shape means an `Ok(None)`
  resolve consumed NOTHING; `consumes` consumes on every path, so spelling it
  that way would spend a live claim at the first empty poll. Its contract is
  consume-on-success-or-terminal-failure, which the effect cannot express, so
  it keeps `&var self` and the disarm decided by the ANSWER. Split into a
  non-consuming poll plus a consuming resolve was considered and declined: it
  would give one op two typed doors for a distinction the kernel already draws
  in the status, and §2.1's surface is one `resolve`.
- **`pipe-reply-wait-dead` was retargeted, and its rows moved.** The case
  proved that a spent obligation asked a SECOND time is the dispatch's
  `BadHandle`; with `reply` and `reply_wait` both consuming, that program is a
  use-after-move the compiler refuses, so the proof cannot be written in Saw at
  all. Its replacement proves the other property this unit's `ReplyWaitError`
  owes and nothing in the suite exercised: the REPLY LEG firing, against a
  Waiter with nothing attached, so a park would have been `every thread
  blocked` and reaching the `done` line is the proof that no park happened —
  the "one dead client cannot stall a server loop" sentence, executed. The
  kernel-side `BadHandle` is untouched and still stands for a raw-altitude
  caller; `pipe-dead-claim` still draws it through `resolve`, which is now the
  only place in the suite that can.

Finding 7's own claim survives the closure intact — it named a real gap, the
gap is closed upstream, and the sentinel it forced is gone from the two ops it
forced it at. What the closure adds to the record is that the gap had a THIRD
shape nobody had priced: a funnel whose consumption is CONDITIONAL. `consumes`
does not reach it, and after the sweep `resolve` is the one op in this file
that still spells single use in a value rather than in a type.

### Transcript accounting (the closure's own)

Baseline captured at `d7a74c0` before any edit; gate re-run at `3412be3`.
210/210 both times. **The two transcripts differ in image sizes and in
NOTHING else** — no case's verdict moved, no ordering moved, and none of the
three timing-dependent cases (`thread_preempt`, `timer_interval`,
`process_stats`) wobbled in this pair.

| riscv32 image | before | after | delta | why |
|---|---|---|---|---|
| `pipe-oneshot` | 24256 | 23648 | −608 | 6 sites, sentinel gone |
| `pipe-wait-reply` | 31552 | 31136 | −416 | 3 sites |
| `pipe-abandon` | 16984 | 16808 | −176 | 1 site |
| `pipe-big-reply` | 13952 | 13840 | −112 | 1 site |
| `pipe-no-reply` | 14376 | 14312 | −64 | 1 site |
| `pipe-send-manual` | 45760 | 45696 | −64 | 1 site |
| `pipe-call-orphan` | 32752 | 32688 | −64 | 2 sites |
| `pipe-dead-claim` | 14688 | 14640 | −48 | 1 site |
| `pipe-wait-give` | 25272 | 25240 | −32 | 2 sites |
| `pipe-reply-wait-dead` | 19192 | 22120 | **+2928** | the retarget, not the sweep |

**EVERY CONVERTED IMAGE SHRANK**, which is the retired sentinel showing up as
bytes: a disarm is a store plus the by-reference plumbing that carried the
wrapper into the `unsafe` helper, and taking the WORD instead removes both.
The one growth is `pipe-reply-wait-dead`, and it is the rework rather than the
conversion — the retargeted case renders a `ReplyWaitError` through the
`Printable` chain (leg describe + status describe) where the old one printed
two fixed strings and faulted.

`child-reply` and `child-server` each converted one site and did NOT move
(13984 and 21016 both runs, rebuilt in the gate). Both images are byte-exact
— neither segment is page-rounded — so the removed store was absorbed by
instruction alignment. **NO arm64 IMAGE MOVED AT ALL, and that is an artifact
worth writing down**: an arm64 sosimg's RX segment is rounded to a 4096-byte
page and its RW segment is entirely bss, so the whole file size is
`72 + round_up(text, 4096)` and any change smaller than a page is invisible.
riscv32 rounds neither, so it is the arch whose numbers are a measurement.
Currency was confirmed independently — the arm64 `pipe-reply-wait-dead` image
carries the new console strings.

**THE ROWS THAT MOVED ARE ONE CASE'S, AND THEY ARE IN THE RUNNER**, since the
harness asserts console lines rather than printing them. `pipe_reply_wait_dead`
lost `SOS replywaitdead: discharged the obligation` and
`SOS: process fault: bad handle process={zero}`, gained
`SOS replywaitdead: the client let its claim go`,
`SOS replywaitdead: reply_wait says the reply did not land: the other end of
this connection is gone` and `SOS replywaitdead: done`, and its
`expect_clean_exit` went `False` -> `True` with `expect_status` dropped. The
argument is the rider's fourth bullet: the proof it used to make is a compile
error now, and no Saw program can witness it.
