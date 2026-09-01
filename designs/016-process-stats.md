# SawOS design 16 — process stats: exact trap introspection (ProcessOp.Stats)

Status: AUTHORED + USER-RULED Sep 1 2026 (the API settled in
conversation — the review gate's in-conversation form): PER-PROCESS
counters, an op + right, and the trap-count assertions swept into the
tests that assume trap behavior. The consumer that names this surface
is unit 3.5's ping-pong trap-count proof (#10 ruling 4: "a proof that
COUNTS TRAPS"), which today would have to infer trap economy from code
inspection — this makes the machine say the number.

## The ruled surface

- **`ProcessOp.Stats`** — answers through a copy-out record (the
  `Clock.Now`/`PipeCreate` precedent), gated by **`ProcessRight.Stats`**
  in the default set (per-kind right named for its op, Aug-29
  doctrine). Self-inspection composes free: `ProcessSelf` →
  `proc.stats()`.
- **The record — three `UInt64` columns, split by TRAP KIND**, because
  the split is what makes assertions deterministic:
  - `syscalls` — ecall traps. DETERMINISTIC for a given program path
    (a park is one ecall however long it parks) — the assertable
    column.
  - `interrupts` — timer/device traps taken while this process was
    current. Nondeterministic; printed, never asserted.
  - `faults` — exception traps that are neither. Deterministic where a
    test provokes one on purpose.
- **Typed surface**: `struct ProcessStats { syscalls, interrupts,
  faults: UInt64 }` (plain data, Copy tier);
  `Process.stats(&self) -> Result<ProcessStats, SosStatus>`.
- **Counting site**: one increment per column at TRAP ENTRY dispatch,
  keyed by cause class, attributed to the process CURRENT at entry.
  Columns zero at process create. Boot-door traps before the root
  process exists are unattributed — say so at the site.
- **PER-PROCESS ONLY (user ruling, Sep 1)** — per-thread is deferred
  until a consumer names it. **THE RECORDED FUTURE SHAPE (user, same
  ruling): a SHARED MEMORY REGION exporting the stats read-only to
  every process granted the Right to map it — a purely-userspace
  `top`, reads free.** Parked as a seed beside the M5 growable-pool
  seed (it wants the Memory-mapping machinery and a published record
  layout); the op-shaped v1 is deliberately thin so the region can
  later back it (or a vDSO read replace it) without a surface break —
  the same refactor-into-cheap concern that struck the send wrappers.

## The sweep (the second half of the ruling)

Census the tests that ASSUME trap behavior and make them ASSERT it
where deterministic:

- Candidates (census, don't trust this list): `pipe-send-manual` (the
  composition's exact ecall sequence is the point — assert its
  syscall delta per leg); the ping-pong seed cases (`pipe-oneshot`
  round trips); fault-provoking cases (`death-fault`, `map-wx-refused`
  — assert the fault column moved by exactly one); interrupt-driven
  cases (`uart-echo-*`, timer cases — PRINT the interrupt column,
  never assert it; the timing rows doctrine applies).
- Augmentation is APPEND-ONLY new rows in existing transcripts —
  existing rows stay byte-identical; every augmented case named in the
  As-built with its asserted delta.
- The stats calls themselves trap: bracket outside the measured
  window, or subtract the boundary — state the arithmetic at each
  assertion.

## Proof (dedicated)

**`process-stats`**: self-stats via `ProcessSelf` (a known syscall
sequence asserts an EXACT delta); a child's stats through its Process
handle (create, run to a known point, assert); the interrupts column
printed; a mint lacking `Stats` refused (negative arm). Split print
phases per function (the 16 KiB stack ceiling, design 14 finding 3).

## Docs owed

spec: the op + right rows, the record layout, §8-adjacent note on
attribution; the counted-kinds/quota tables untouched (stats allocate
nothing). Tracker entry closed in place; As-built here (census list,
augmented cases + deltas, transcript accounting); SL-N for genuine
deficiencies.

## Out of scope

Per-thread columns; the shared stats region and its Right (the seed
above); scheduler/queue-depth metrics; any timeout machinery.
