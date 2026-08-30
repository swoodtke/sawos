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

BUILT Aug 30 2026. `make sos-test` green on both profiles — 150 passed —
against a baseline captured at the merge base (6616775). The bucketed
account of the 144 baseline rows, whole transcripts diffed rather than
"green" read:

| bucket | rows |
|---|---|
| byte-identical | 103 |
| address-only (`entry=` alone; the `sos` module grew) | 40 |
| authorized changes to existing rows | **0**, as D-1 predicted |
| documented-nondeterministic | 1 — `thread_preempt` on arm64 |

Plus 6 new rows (3 cases × 2 arches). The nondeterministic row is the
alternation string `thread_preempt` prints, which the case's own comment
records as timing-dependent ("WHICH worker runs first depends on where
the first tick lands relative to the two `start` calls") and which
asserts DIRECTION CHANGES rather than a sequence; four earlier runs of
unrelated trees in this session's scratchpad show four different strings,
one of them starting with `B`, which no code change can cause. The 40
address-only rows are the `entry=` line alone — nothing else in any of
them moved.

### The arms, as landed

- **`WaitableKind.Process = 3`** (`kcore.waitables`) and the five matrix
  arms beside it: `waitable_attachment` / `set_waitable_attachment` read
  and write a new `ProcessSlot.attachment` field; `waitable_ready` is
  `state == Gone` (a nested match, with `Created` refused by name —
  see the findings); `waitable_answer` is
  `WaitAnswer(tag: WaitTag.Process, payload: process_status_word(
  exit_kind, exit_code))`; `waitable_consume` is **empty, and the
  one-sentence contrast with Event's clear-on-consume is written at the
  arm** — the two empty arms (Interrupt, Process) are empty for opposite
  reasons, and that is the sentence.
- **`WaitTag.Process = 3` and `WaitPayload.Process(status:)`** in
  `sosabi`, both with the docstrings the brief asked for. The tag's own
  "the space is extensible" paragraph got a line saying it had now been
  collected.
- **`waitable_slot`'s Process arm REPLACES its `NotWaitable` refusal**,
  gating `ProcessRight.Wait` on the handle — the `GetStatus` bit, no new
  right. `Thread` stays `NotWaitable` (D-2) and its arm now carries the
  scope sentence rather than nothing.
- **`end_process` notifies** — `notify_ready(WaitableKind.Process, p)`
  immediately after the three status assignments, before the exit report
  and before the close-all. The D-1 ordering note is at the site: the
  wake only QUEUES, the teardown runs to completion inside the same
  syscall, and `resume_after_death` at the bottom is what picks the woken
  supervisor up. The transcripts show it — the wake line lands after the
  teardown line in both notify cases.
- **The refcount composes**: `ref_waitable` / `unref_waitable` reach
  `ObjType.Process` through `waitable_obj_type`'s new arm, so the
  attachment increments and decrements `PROCESSES[slot].refs` at exactly
  the sites design 7 already had. `free_object`'s Process arm gained the
  attached-at-zero `fatal_kernel` its three siblings carry. Design 7's
  D-1 table row is amended, and so is §2's copy of it.
- **`has_external_wake_source` gained NO arm**, and the D-1 reasoning is
  recorded at the predicate in the three cases it resolves (parked on a
  live started child / on a dead child / on a created-never-started
  child).
- **sysapi**: `decode_wait`'s tag vocabulary grew one arm. The attach
  overload is **`Process.attach(waiter:, key:)`** and not
  `Waiter.add(process:, key:)` — see finding 1. Borrow, nothing
  consumed; no other wrapper changed.
- **`MAX_ATTACHMENTS` grew by `MAX_PROCESSES`** (20 → 22): an attachment
  is one (Waiter, waitable) pair, and there is a fourth kind of waitable
  now.

### The proof, as landed

Three cases, six rows, one new child package.

- **`death_notify`** (`tests/death-notify` + the new `tests/child-bye`) —
  **the first launcher in the suite with no timer armed.** It attaches
  the child's Process handle before starting it and parks; the child
  exits with code 5; root wakes with `key=44 status=65541`
  (`Exited` << 16 | 5). A kernel that failed to notify would have nothing
  runnable and die with `every thread blocked`, so a plain `wait()` is
  the assertion.
- **`death_fault`** (`tests/death-fault` + the existing `child-fault`) —
  the same program with the `give` taken away, so the child dies at its
  first `ecall`. `status=131073` (`Faulted` << 16 | `BadHandle`), byte
  for byte the word `process_lifecycle` reads through `get_status`.
  Reusing the existing child is what made the fault arm cost one package.
- **`death_late_attach`** (`tests/death-late-attach` + `child-bye`) —
  root sleeps on a timer until the child is already dead, then attaches
  and waits TWICE: `first=65541 second=65541`. Then the reclaim
  interplay, asked one reference at a time: release the Process handle
  and a second `process_create` is still refused (`held=1` — the
  attachment alone holds the `Gone` slot); `remove` the attachment and
  the same create succeeds (`freed=1`). A kernel that did not count the
  attachment prints `held=0`, and its second wait would have been reading
  a slot already given away.

### Findings

1. **SL-7's THIRD SITE, and it moved the ruled spelling.** The brief's
   `Waiter.add(process:, key:)` is unwritable: `sos.waiter` sits BELOW
   `sos.system` (its three `add` overloads read `Event`/`Interrupt`/
   `Timer` handle fields, which is why it is above THEM), and `Process`
   is declared in `sos.system` because System/Process/BootHandle
   mutually reference. A fourth overload naming `Process` in
   `waiter.saw` is the DF-232e cycle. Design 6 met the same wall and
   answered it by flipping the receiver
   (`Memory.map(into: &Process)` → `Process.map(memory:)`); this unit
   applies that verbatim, so the surface is
   **`child.attach(waiter: &w, key: K)`** with identical semantics, and
   `waiter.saw` carries a comment where the fourth overload would have
   gone so its list still reads as four. The rejected alternative was an
   `extension Waiter` written in `system.saw`: design 142 scopes
   extension lookup to the DECLARING module plus the caller's DIRECT
   imports, so every consumer would have needed an `import sos.system`
   whose purpose is invisible. SL-7 updated with this site.
   **RIDER (first pin bump, sawlang 0.2.0, Aug 30): superseded as
   ruled.** The user landed SL-7's resolution — extension lookup
   follows `public import` — so the rejected alternative became the
   ruled surface: `Waiter.add(process:, key:)` now lives in
   `system.saw` as an `extension Waiter`, the raw funnel widened to
   `public(package)` for that one caller, and `Process.attach`
   retired. The three test call sites respelled; semantics
   byte-identical. This unit's interim spelling was correct for the
   toolchain it shipped against.
2. **`kcore.waitables` moved ABOVE `kcore.process`** — the one altitude
   change. A Process's readiness, payload and watcher are all reads of
   `PROCESSES`, so the matrix has to see that table. The flip is sound
   because `process` names nothing in `waitables`; `kcore/lib.saw`'s
   ordered list, both module headers and the "where the line fell"
   paragraph are updated. Worth knowing for the next unit: the
   state-below-teardown rule never fixed the order of two STATE modules
   relative to each other, only relative to the teardown, and both are
   still below it.
3. **A Process attachment is the FIRST CROSS-PROCESS attachment**, which
   falsified a sentence `end_process`'s attachment sweep rested on ("an
   attachment only ever joins two objects of the SAME process, so
   freeing both ends is freeing the list"). The sweep now also clears the
   watched waitable's back pointer and drops the reference through a new
   `unref_waitable_teardown` (the `unref_teardown` twin, count-don't-free,
   for the close-all's own stated reason). Unreachable at
   `MAX_PROCESSES == 2` — the only possible supervisor is root, and
   root's death stops the machine — and it moved no transcript row,
   because for a same-process waitable the per-slab sweep zeroes the slot
   two loops later either way. Written because an invariant that has
   quietly become false is worse than a branch nothing takes.
4. **The compiler enumerated the unit.** Adding the three enum cases
   broke, by name: five matrix arms, `waitable_obj_type`,
   `waitable_slot`, `decode_wait`, and EIGHT userspace `WaitPayload`
   matches in `tests/`. Nothing had to be searched for. §2.2's "a further
   waitable cannot be added silently" is recorded as COLLECTED rather
   than claimed, and `waitable_slot`'s docstring notes the sharper half:
   a `_` arm would have hidden this one entirely, since a kind moving
   from the refusal list to the waitable list is not a new case at all.
5. **The deadlock predicate's third case is load-bearing, not just
   correct.** A supervisor parked on a child it CREATED and never
   STARTED is genuinely deadlocked — nothing can ever make that slot
   `Gone` — and `has_external_wake_source` answering `false` is what
   reports it. An arm counting live processes as a wake source (the
   obvious "fix" a reviewer might propose) would have turned exactly that
   mistake into a silent hang. Recorded at the predicate.
6. **No new SAWLANG deficiency met.** Nothing in this unit needed a
   language feature that is missing, and no existing SL entry got a new
   site except SL-7 (finding 1). SL-10 is not owed.

### What is still open

Nothing from this brief. §8's other half — a waitable THREAD — stays
deferred and is now the only `NotWaitable` arm in `waitable_slot` that
§8 promises to remove; `kill` still has neither op nor right.
