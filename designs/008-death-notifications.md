# SawOS design 8 — Death notifications (M3 unit 5.5)

Status: AUTHORED Aug 30 2026 (lead), implementing sawlang#232 agenda
item 9 (ruled Aug 16: IN M3, unit 5.5) with its two pinned
sharpenings. The smallest unit of the ladder, and smaller than
planned: the retain-while-donating problem it was sequenced around
was solved by universal mint (design 4's ruling chain), so what
remains is exactly the ruling's own sentence — **the existing Process
object becomes a WAITABLE. Not a new kind.**

## The ruled surface

- `Waiter.add(process:, key:)` — the waitable_slot pattern's next
  consumer: a `WaitTag.Process = 3` and a payload carrying the §8
  status word (`process_status_word(kind, code)` exists — kind in the
  high bits distinguishes CLEAN EXIT from FAULT, which is sharpening
  2 delivered by vocabulary that already ships).
- **DEATH IS A TERMINAL LEVEL** (sharpening 1): it stays signaled. A
  waiter attaching AFTER the death still wakes — the level-triggered
  Waiter contract's strongest case, and the reason supervision has no
  race: there is no window where a death can be missed.
- The parent already holds the child's Process handle from
  `process_create`; that handle (with `ProcessRight.Wait` — the
  existing bit that gates `GetStatus`, the same question asked
  asynchronously) is what attaches. No new right.

## D-1: How it lands on what exists (mostly compositions)

- **`waitable_slot` gains the Process arm**; `WaitableKind.Process`;
  the per-kind matrices (the five questions) each gain their arm —
  readiness is `state == Gone`, the payload is the recorded
  `exit_kind`/`exit_code` through `process_status_word`, and
  CONSUME does NOT clear it (terminal level — consuming a death
  reports it, nothing un-dies; write this at the consume arm, it is
  the one place terminal differs from Event's clear-on-consume).
- **`end_process` notifies**: after the slot records its status and
  before the fork, `notify_ready(WaitableKind.Process, slot)` — a
  parked supervisor wakes with the record exactly as a timer expiry
  wakes one. Ordering note: the notification happens while the dying
  process's teardown is mid-flight but after its status is recorded —
  the woken thread runs AFTER the teardown completes (run-to-
  completion; the wake only queues), so no observer ever sees a
  half-dead process. Say this at the site.
- **The refcount composes (design 7)**: a Process attachment is a
  counted reference — the D-1 table's Process row grows "+ its
  attachment" — so an attached-but-unreferenced dead process's slot
  SURVIVES until detach, which is exactly what terminal-level
  attach-after-death needs to be sound against design 3's slot
  reclaim. The census of reference kinds is why this is one line and
  not a hazard.
- **The idle/deadlock predicate is UNTOUCHED**: a process death
  arrives from INSIDE the thread set (some syscall or fault caused
  it), never from outside it, so `has_external_wake_source` gains no
  arm — a supervisor parked on a live child is not deadlocked
  (the child's threads are runnable), and one parked on a dead child
  was already woken (terminal level). Record the reasoning at the
  predicate; it is the question a reviewer will ask.
- Quotas: attachments are already counted vocabulary; no new rows.

## D-2: What this deliberately does not do

No `kill` (§8: it has neither op nor right — unchanged); no
process-exit CODES for fault kinds beyond the §8 word; no waitable
THREADS (attaching a Thread handle stays `NotWaitable` — §8's
deferred half); no notification to anyone but a parked/attaching
waiter (no signals, no callbacks — the Waiter IS the delivery
system).

## The proof (harness)

1. **`death_notify`** — root creates + starts a child, attaches the
   child's Process handle to a waiter, parks in `wait()`; the child
   exits with a code; root wakes with `WaitTag.Process` and the §8
   word showing Exited+code. The fault variant as a second arm or
   case: the child faults, root's payload shows Faulted+reason.
2. **`death_late_attach`** — the child is ALREADY DEAD when root
   attaches; the wait answers immediately with the same payload
   (terminal level observed); a SECOND wait on the same attachment
   answers again (nothing un-dies).
3. **Reclaim interplay arm** — after detach + release of the child
   handle, the slot frees (design 7's count observed through the
   supervision flow); `process_reclaim`'s claim is now provable with
   supervision in the loop.

Transcript expectation: NO authorized changes to existing rows (the
unit adds arms nobody existing exercises); new cases only.

## Docs owed

spec §8 (the "Process handles are waitables" promise flips to BUILT;
the status-word sentence cites the payload), §2.2 (the waitable list
grows its fourth member; terminal-level noted as the list's first),
§11 rows, design 7's D-1 table row amended (+ attachment), tracker
closed in place, As-built (arms as landed, findings, SL-10+ if met).

## Out of scope

kill; thread waitability; per-death payloads beyond the §8 word;
any teardown-report change; M4 anything.

## As built

(Implementer: arms as landed, the notify ordering as landed,
findings.)
