# SawOS design 12 — waiter revocation (M4 unit 0)

Status: AUTHORED Aug 31 2026 (lead). M4's first rung, per the ruled
plan of record (designs/010, agenda ruling 1): **when a Waiter's last
reference drops, the threads parked on it WAKE with a distinguishable
terminal status instead of stranding.** The smallest unit of the M4
ladder, sequenced first because it lands the vocabulary everything
later uses — the PEER-GONE DOCTRINE's first instance: nothing parked
can be silently doomed.

## The ruled surface

- **`SosStatus.Revoked`** — a NEW status case, appended. One word for
  this site and every future revocation site (ruling 1's own text).
- **The free arm wakes, it does not count** (answering design 7
  finding 2 as "wake, don't count"): `free_object`'s Waiter arm
  currently detaches the attachment list (the one cascade) and
  deliberately leaves `blocked` unwalked — the recorded
  "legal-but-doomed" stance. It now ALSO walks `blocked` and wakes
  every parked thread with `Revoked`. The husk alternative (a parked
  thread worth 1 to the count) stays REJECTED: it keeps the thread
  alive but makes the drop silent — a hang to infer rather than an
  error to handle.

## D-1: the mechanism (mostly what exists)

- The tracking already exists: `WaiterSlot.blocked` is the parked
  list, and the wake machinery that `notify_ready` uses is the model.
  The revocation wake differs in exactly one way: **NO RECORD IS
  WRITTEN** — there is no key, no tag, no payload, because nothing
  became ready; the parked `wait()` syscall ANSWERS `Revoked` in its
  status register and the caller's buffer is untouched. Write the
  contrast at the site: a delivery copies out then answers Ok; a
  revocation answers Revoked and copies nothing.
- Ordering: the wakes happen inside the syscall that dropped the last
  reference, before the slot is reused (the reused slot starts with
  an empty blocked list exactly as today — that sentence survives;
  what dies is the stranding above it).
- The blocked list may hold threads of ANY process that held a handle
  (Waiter handles transfer); the walk is process-blind, like every
  wake.
- A release that is NOT the last reference (a minted sibling still
  live) frees nothing and wakes nobody — the count is the gate,
  unchanged. Say this at the arm; it is the difference between
  revocation and any-release.
- The deadlock predicate is untouched: the wake is synchronous inside
  a syscall, so no arm of `has_external_wake_source` changes.
- Sysapi: `Waiter.wait` already answers `Result<_, SosStatus>`;
  `Err(Revoked)` flows through with ZERO new surface. The wrapper's
  docstring gains the sentence.

## D-2: what this deliberately does not do

No pipe anything (units 1+); no receipt levels; no revocation of
ATTACHMENTS (removing a waitable from a live Waiter is `remove(key)`,
unchanged); no change to what release does when references remain;
no husk tier.

## The proof (harness)

1. **`waiter-revoked`** — a sibling thread parks in `wait()` on a
   Waiter; the main thread releases the LAST handle; the sibling
   wakes with `Revoked` and prints it. The not-last arm rides in the
   same case: before the final release, release a MINTED sibling
   handle first and show the parked thread did NOT wake (the count
   gates, not the release).
2. **A two-parked arm** if `MAX_THREADS` allows it cheaply: two
   threads parked on one Waiter, one release, BOTH wake with
   `Revoked` — the walk is the whole list, not a pop.

Transcript expectation: existing rows UNMOVED (a new appended status
case changes no existing bytes; the struck strand was reachable by no
shipped case). New case rows only.

## Docs owed

spec §2.2 (the revocation wake; the peer-gone doctrine named as the
contract, with this as its first instance and the reply-pair /
connection-end instances pointed at designs/010), §11 row; the
`refs.saw` Waiter-arm comment REPLACED (the legal-but-doomed
paragraph dies, the reused-slot sentence survives); design 7 As-built
finding 2 gains a RIDER (answered Aug 30/31: wake, don't count —
never rewrite the finding); tracker entry closed in place; As-built
here filled; SL-11+ only if a genuine deficiency is met.

## Out of scope

Everything else in designs/010's ladder; kill; thread waitability.

## As built

Landed Aug 31 2026, as briefed, on both profiles. Gate: **160/160**
(80 cases per architecture, riscv32 + arm64) — the baseline's 158 plus
one new case per profile.

### Transcript accounting (baseline d22e7c1 -> this tree, whole diff)

| bucket | rows | why |
| --- | --- | --- |
| byte-identical | 105 | arm64 35, riscv32 70 |
| address-only | 52 | arm64 44, riscv32 8 — the kernel grew a function and `sosabi`/`sos` grew a status case, so kernel text and EVERY root image's entry shift. Checked with a normalizer that masks ONLY text-segment addresses (`0x8…`, `0x40…`) and deliberately NOT small hex words, so a moved handle/thread/object COUNT would have landed in the bucket below instead. None did. |
| documented-nondeterministic | 1 | `timer_interval` on riscv32: `tick one fires=2` -> `1`. The periodic fire COUNT is unasserted by the case ON PURPOSE and the runner's own comment records the identical move at M3 unit 4, "where a bigger kernel changed how much of the first period was spent before the wait" — coalescing is the feature, so pinning the number would make it the flake. |
| new | 2 | `waiter_revoked`, one row per profile |

**THE TWO TIMING ROWS WERE ISOLATED RATHER THAN ASSUMED.** The suite was
run twice on ONE unchanged tree and diffed against itself: the ONLY rows
that moved were `thread_preempt` (its `ABBA…` interleaving, and the PC a
tick happened to land on) and `timer_interval` (both fire counts). Every
other row was byte-identical run to run. So both are nondeterministic by
measurement, not by reputation — and in the gating run `thread_preempt`
happened to reproduce the baseline's interleaving exactly on both
profiles, which is why it sits in the address-only bucket above rather
than in the nondeterministic one.

No pre-existing row moved for a REASON. Nothing in this unit runs outside
`free_object`'s Waiter arm, and no shipped case releases the last handle
to a Waiter with a thread parked on it — the struck strand was reachable
by no shipped case, which is what the brief predicted.

### The wake path, as landed

`kcore.refs` grew one module-private function, `revoke_blocked(w)`,
called from `free_object`'s `Waiter` arm between the attachment cascade
and the slot clear. It unhooks the whole `blocked` list (head first —
`WAITERS[w].blocked = 0` before the walk, so nothing can be woken
twice), and per thread: reads `link` BEFORE handing the field to the
ready queue, clears `wait_buf`, writes `op_status(SosStatus.Revoked)`
into the thread's frame through `write_result`, sets `Runnable`, and
`ready_push`es. That is `exit_thread`'s joiner loop with a status in
place of an exit value, which is the right ancestor: both answer a
syscall that was parked with nobody to answer it, and neither writes a
byte of process memory.

`SosStatus.Revoked = 8` is appended after `QuotaExceeded = 7`, with a
`describe()` arm — "what this call was waiting for is gone" — worded for
the doctrine rather than for the Waiter, since ruling 1 says one word for
every revocation site.

**THE ALTITUDE QUESTION ANSWERED ITSELF, and it is the unit's one
structural finding.** `wake` is where a wake belongs by name, and `wake`
sits ABOVE `refs` (it reaches `copy_out`, which is `process`'s, which is
above `refs`), so a revocation written there would be the cycle DF-232e
diagnoses. It did not have to be: a revocation touches no process memory,
so it needs neither the copy door nor `wake`'s altitude — only a frame's
status word and the ready queue, both `threads`', below `refs`. **The
property that makes it not-a-delivery is exactly the property that lets
it live beside the free it belongs to.** Two imports were added to
`refs.saw` (`kcore.result`, `kcore.threads`), both strictly below it; no
module moved.

### The no-record contrast, as landed

Written at three altitudes, in the same words each time:

- `revoke_blocked`'s docstring — "a delivery copies out and then answers
  `Ok`; a revocation answers `Revoked` and copies nothing", with the
  three reasons there is nothing to write (no key, because a key names an
  attachment; no tag, because no waitable fired; no payload, because
  there is no value).
- spec §2.2's new bullet, as the first of the three things that make the
  revocation one sentence.
- `Waiter.wait`'s docstring in `sos`, from the caller's side: the buffer
  is untouched, there is nothing to decode, and the handle is stale from
  that moment.

The harness proves it negatively and deliberately: `waiter-revoked`'s
workers allocate a record buffer, pass it, and NEVER READ IT. Nothing in
the transcript could have come out of one.

### Everything else the brief predicted, confirmed

- **The count is the gate, not the release.** Untouched — the mint's
  `ref_object` and the release's `unref_object` are design 7's, and a
  release with a sibling still live never reaches `free_object`. Said at
  the arm; proved by the case's middle line.
- **The reused-slot sentence survives**, moved down onto the `WaiterSlot`
  literal it actually describes, with a note that the wake above is what
  makes the empty list honest rather than merely empty.
- **The deadlock predicate gained no arm.** Re-read at the site: the wake
  is synchronous inside the syscall that dropped the last reference, so
  the woken threads are on the ready queue before `pick_next` runs and
  `has_external_wake_source` is never consulted about them.
- **Zero new sysapi surface.** `Waiter.wait` already answers
  `Result<WaitResult, SosStatus>`; `checked` turns 8 into
  `Err(Revoked)` by the same total rule it applies to 4, 5, 6 and 7, and
  `wait_into_frame`'s `try checked(...)` propagates before `decode_wait`
  is reached — which is what keeps that function's deliberate panic
  ("kernel wrote wait tag N") out of the revocation path, since no tag is
  written.
- **The process-blind walk** is what the code does, though the tree
  cannot yet exercise it: no `Waiter` handle carries `Transfer`, so every
  handle to one Waiter is one process's today. The walk asks nothing about
  a thread's process, so nothing changes when that stops being true.

### Deviations

**One, and it is a test-shape deviation rather than a surface one: the
harness case works at the C ALTITUDE.** The brief's proof 1 asks for a
minted sibling released before the last handle, and the typed `sos`
surface cannot spell a second name for a Waiter — `mint` is published on
`System` and on `Memory` (the two kinds a launcher assembles keep masks
for) and a `Waiter` keeps its handle word module-private, by the
no-forged-handles rule. So `waiter-revoked` creates the Waiter, mints the
sibling, parks and releases through the `sos_*` floor, exactly as
`handle-remint` and `timer-badclock` do and for the same stated reason: a
claim ABOUT a handle word is unspellable where a handle word does not
exist. The cost is that the transcript does not exercise the typed
`Waiter.wait` returning `Err(Revoked)`; what it does instead is decode
the raw status through `SosStatus.from(raw:)`, which is the same table
`checked` reads, so the words on the console are the ABI enum's own and a
case added without a `describe` arm would not compile. **A typed
`Waiter.mint(rights:)` was considered and NOT taken** — it is public
surface the brief did not ask for, on a unit whose ruled surface is one
status case — and it is recorded as a finding below.

### Findings

1. **A TYPED `Waiter.mint` WOULD MAKE THIS CASE TYPED END TO END, and it
   is the same gap the deferred `event-wake` rewrite wants.** `MINT_OP`
   is UNIVERSAL in the kernel — intercepted before the kind match, so it
   works on every kind today — while the typed surface publishes `mint`
   on exactly two of them. That asymmetry is defensible as it stands
   (a rights mask is only worth writing where a launcher narrows), but it
   means a Saw process can hold two names for a `System` or a `Memory`
   and for nothing else, which is why both this case and the backlog's
   `event-wake` / `event-consume-wake` one-handle-each rewrite reach for
   a raw word. Not filed as work: the unit that wants a second handle
   onto an Event or a Waiter should rule on whether `mint` is per-kind
   surface added as needed, or a universal one the wrappers all carry.
2. **SL-11 — AN EXTENSION-METHOD OVERLOAD SET BINDS ONLY ITS FIRST
   MEMBER WHEN THE RECEIVER TYPE'S NAME IS NOT IMPORTED.** ``missing
   argument for parameter `entry` ``, anchored at the first LABEL of the
   call, for `proc.thread_create(stack_top:, arg:)` in a file that
   imported everything it names except `Process`. Adding `Process` to the
   same import list fixes it. Reduced to three files with no facade
   involved (a direct import fails identically), so it is NOT SL-7's
   re-export question — it is DF-242b's free-function rule at the other
   declaration kind. Workaround in-tree: import the receiver type, with
   the reason written at the import. Filed with the minimal repro; the
   tree had not met it because every other root server imports `Process`
   and `Thread` anyway.
3. **NO OTHER SAW CONSTRUCT MISBEHAVED.** The list walk, `write_result`
   through a sibling thread's frame, a raw-backed enum gaining a case
   without renumbering, `SosStatus.from(raw:)` on the new value, a
   `guard let` over an `Optional<SosStatus>`, `[UInt; N]` frame buffers
   under `--no-hidden-alloc`, and `(&var buf) as UnsafePointer<UInt>`
   confined to one `unsafe` helper each all worked as the skill
   documents.
4. **THE MID-FLIGHT INLINE-TRY/CATCH RULING (user, Aug 31) IS APPLIED
   THROUGHOUT THE NEW CODE.** Every bind-or-bail on a `Result` in
   `tests/waiter-revoked` is `let x = try f() catch { … return }`; the
   two `join()`s use the fallback-value form of the same construct. No
   `match` on a `Result` remains in the new file, and no existing file
   was swept — the ruling scopes that to a separate pass. Worth
   recording for the sweep: the shape composes with the guard-form
   `catch` returning `Never` in a `Void` function and in a value
   position alike, and it did NOT interact with SL-11 (the overload
   failure reproduces under `match`, `try!` and `try … catch` equally).

### Docs updated

spec §2's `Waiter` row (the second last rite, with the count-gates
sentence and a pointer to §2.2); §2.2 (the revocation bullet — the
no-record contrast, the whole-list walk, the count gate, the rejected
husk, and THE PEER-GONE DOCTRINE named as the contract with this as its
first instance and the reply-pair / connection-end instances pointed at
`designs/010`); §11 (a struck M4-unit-0 entry beside the pipes one,
whose "which its unit 0 lands" clause becomes "has LANDED"); design 7's
As-built finding 2 RIDER (the finding itself never rewritten);
`kcore.refs`'s module header and `free_object`'s last-rites list;
`WaiterSlot.blocked` in `kcore.waitables`; `Waiter.wait` in `sos`; the
tracker entry closed in place and SL-11 filed.
