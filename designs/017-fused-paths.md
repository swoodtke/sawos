# SawOS design 17 — the fused paths: Call and ReplyWait (M4 unit 3.5)

Status: AUTHORED Sep 1 2026 (lead); **§API USER-REVIEWED SAME DAY (the
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
