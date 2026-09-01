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

---

# As-built (Sep 1 2026)

BUILT AS BRIEFED, with one addition argued below. Base `2a405db`
(suite 196/196); gate 198/198, 99 cases per arch, `-j 4`, both
profiles. sawlang HEAD `87063387` at dispatch and unchanged at the
gate; `sawlang.pin` untouched.

## What landed

- **`ProcessOp.Stats = 11`** and **`ProcessRight.Stats = 1 << 20`**,
  the right in `process_rights()` — the ONE default set — so
  self-inspection composes free (`ProcessSelf` -> `proc.stats()`). The
  op takes the next number rather than displacing one; §5.7's
  renumberable-op discipline says it may, and appending is what keeps
  every existing table row where it was. The `< MINT_OP` assert moved
  from `PipeCreate` to `Stats`, which is the rule that file already
  states (one assert per table, on its HIGHEST case).
- **The record**: `PROCESS_STATS_RECORD_DOUBLEWORDS = 3` with named
  slots and `process_stats_record_bytes()`, on the `Clock.Now`
  precedent — DOUBLEWORDS rather than machine words, for the reason a
  nanosecond count is 64 bits on both profiles: **a count is a property
  of the quantity, not of the machine**, and a register-wide column
  would make one program answer two different things about itself
  depending on which profile built it.
- **The columns** on `ProcessSlot` (`syscalls`, `interrupts`,
  `faults`, all `UInt64`), zeroed at BOTH creation doors —
  `start_process` for root's boot-time reification and `process_create`
  for a child. The second is load-bearing rather than tidy: a process
  slot may be a RECLAIMED one (design 3 D-3), and without the reset a
  child would read a first `stats()` describing a program it never was.
- **The typed surface**: `struct ProcessStats { syscalls, interrupts,
  faults: UInt64 }` (plain data, automatic `Copy` tier — a reading is a
  fact you took, not a capability you hold) and
  `Process.stats(&self) -> Result<ProcessStats, SosStatus>`, over the
  `@export`ed `sos_process_stats` seam and a
  `process_stats_into_frame` helper that confines the pointer (design
  136). `ProcessStats` and `ProcessRight` joined the facade's
  re-export list.
- **The counting site**: `ktrap`'s own three-way head, one increment
  per trap ahead of every dispatch. Everything else follows from that
  placement and is argued below.

## The counting site, per arch

**ONE SITE, AND IT IS ARCH-FREE — which is the interesting half of the
answer.** `kernel/core/dispatch.saw`'s `ktrap` is where the cause class
is decided, exactly once in the system, and BOTH HALs land there:
riscv32's single trap vector (`boot.S` -> `call ktrap`) and arm64's TWO
vectors (`user_trap_entry` with ESR/FAR, `user_irq_entry` with the
synthetic `IRQ_CAUSE_HI` the syndrome register cannot hold — `bl ktrap`
in each). So the increment is a by-product of a decision the handler was
already making, there is no second site to keep in step, and no HAL file
was touched at all. The three branches are mutually exclusive and each
charges before it commits to its path:

| branch | column | why it is charged where it is |
|---|---|---|
| `hal.is_interrupt(cause)` | `interrupts` | before `service_irq`, so a delivery that reschedules is still charged to the process it interrupted |
| `not hal.is_syscall(cause)` | `faults` | before `fault_trap`, which does not return — the process ends and its slot lives on as `Gone`, so this increment is exactly what lets a supervisor read that its child faulted once |
| otherwise | `syscalls` | before `dispatch`, so a call that never comes back is counted like one that does: an op that PARKS is one trap however long it parks, an op that ENDS the process is charged to the process that made it, and an op that switches is charged to the one that asked |

Attribution reads `THREADS[CURRENT_THREAD].process` directly rather than
calling `calling_process()`, and the difference is deliberate:
`calling_process()` answers `ROOT_PROCESS` when there is no current
thread — the right answer for a syscall's dispatch, since the harness
kernels that enter user mode with no object model still have to be
served — and the WRONG one here, because charging root for traps taken
before root was reified would make its opening count a number nobody
could predict. `count_trap` counts nothing when there is no thread, and
says so.

**TWO CONSEQUENCES WORTH READING AS PROPERTIES RATHER THAN GAPS:**

1. **A BAD SYSCALL IS A SYSCALL.** An unknown handle, an unknown op or
   a missing right ends the process, but the trap arrived through the
   syscall instruction and the columns are keyed by how the machine
   entered the kernel rather than by how the kernel felt about it.
   Keying on the outcome would make the assertable column unpredictable,
   since a caller cannot know in advance which of its calls will be
   refused. `death_fault` and `process_isolation` assert the two sides
   of this as mirror rows.
2. **AN IDLE-TIME OR PREEMPTION-POINT INTERRUPT IS NOT A TRAP AND IS
   NOT COUNTED.** Both arrive through `irq_poll` inside the kernel
   (design 1 D-1), never through a vector, so they never reach `ktrap`.
   That is exactly right for a column that means "taken while this
   process was current", because at both of those sites no process was —
   and it is why the dedicated proof's `interrupts` row reads 0: root
   parks while its child runs, and a park idles rather than spins.

**THE UNATTRIBUTED SET IS EMPTY IN EVERY IMAGE THE SUITE BUILDS**, and
the guard is what keeps it empty rather than what compensates for it:
`start_process` is the only door into user mode and it installs
`CURRENT_THREAD` before it opens, so no user-mode trap can precede a
current thread. The boot-door sentence the brief asked for is written at
the site all the same, because the invariant is a property of one
function rather than of the type system.

## The one addition: `Process.mint(rights:)`

**ARGUED, NOT ASSUMED.** The brief's proof asks for "a mint lacking
`Stats` refused", and the surface to write it did not exist: System,
Memory and the four pipe ends each grew a typed `mint` with the funnel
that wanted one, and a Process handle had none, because nothing had ever
asked to hand out a NARROWED one. `ProcessRight.Stats` is that ask — the
bit exists to be WITHHELD ("you may learn my child died, and not watch
it work") — so without the method the new right would be unwritable at
the typed tier, which is dead surface under another name. It is
`System.mint`'s body verbatim at a different receiver, and it brought
`ProcessRight` onto the facade's re-export line under the rule that line
already states: a rights enum joins it with the funnel that needs it.

The alternative was reaching for the raw `sos_handle_mint` in the test,
as `refcount-free` does. Rejected: it would prove the kernel's check and
leave the authority unexpressible by the surface the design is about.

## The sweep census

**THE WHOLE CASE TABLE EXAMINED** — 101 cases, 84 of them carrying a
Saw root-server package, plus every `tests/*/src/main.saw` behind them.
The governing finding, which decided every placement: **every printed byte
is one `ecall`** — `System.debug_print` writes a byte per character and
freestanding `print` reaches the same seam — so a typical root's syscall
column is 100-400 of which ~90% is console text. An exact-delta
assertion over a window containing a `print` would be asserting the
PROSE, and would break on a future wording change rather than on a
kernel change. Every bracket below therefore encloses a print-free
window, which is the brief's own instruction and, here, the only
arithmetic that survives an edit.

### Augmented — five cases, each with its asserted delta

| case | new row | the arithmetic |
|---|---|---|
| `pipe_send_manual` | `the round trip cost 4 traps` | post + attach + wait = 3, plus 1 for the closing `stats()`, whose trap `ktrap` charges at entry before the op answers. **The third of those PARKED**: root blocked while the child was scheduled, woke on its outlet, took, replied and died — a whole cross-process round trip — and root paid ONE trap for it, because a wake writes the answer into the parked frame rather than re-entering through the door. Until this unit that was a sentence about the kernel's source |
| `pipe_oneshot` | `the exchange cost 5 traps` | post + take + reply + resolve = 4, plus the closing read. The bracket closes BEFORE the owning wrappers fall out of scope, deliberately: `claim` and `obligation` still hold live words, and a release is a trap too — it is the phase's epilogue, not its exchange |
| `process_isolation` | `child syscalls=0 faults=1` | `child-poke`'s whole biography. It holds no handles, so it never reached the kernel through the syscall door at all, and its ONE trap was the store into its neighbour's memory the hardware refused. Read AFTER the death, through the handle that outlived it |
| `death_fault` | `child syscalls=1 faults=0` | the exact mirror. `child-fault` made ONE `ecall` naming handle zero and the kernel ended it — charged to the SYSCALL column even though it killed the process |
| `thread_preempt` | `interrupts=N` (PRINTED, NOT ASSERTED) | the one case whose whole point is that ticks land in user mode, so the one place the column has something to show. Joins the two timing rows this case already carries |

Every one of the five numbers was predicted from the counting rule
before the case was run, and every one was right first time — which is
the claim the unit exists to make.

### Left alone, and why

- **NONDETERMINISTIC syscall columns — do not assert here.**
  `uart_echo_ns16550` / `uart_echo_pl011` and `child_echo_*` (wake count
  tracks the harness's feed rate — the existing unasserted
  `after {} wakes` print is the precedent this unit copies);
  `timer_interval` (a named timing row already); `clock_basics` and
  `timer_interval` both busy-poll `now()`, so each poll is an `ecall`
  and the trip count is unbounded; `pipe_child` and `pipe_delegate`
  (bounded poll loops whose trip count depends on when the child is
  scheduled). **The last three were NOT on the brief's candidate list
  and are recorded here**: `clock_basics` in particular is not in
  CLAUDE.md's timing bucket only because its printed values happen to be
  stable, and its syscall count is not.
- **`map_wx_refused` — the brief names it, and it does not work.** The
  fault is the LAST statement root executes and no other process exists,
  so nothing can read the column afterwards. The same structural problem
  disqualifies all 24 other root-faults cases (`event_dupkey`,
  `pipe_no_post`, `handle_*`, `give_*`, `mint_revoked`, `timer_bad*`,
  `iomemory_carve`, `memory_split`, `map_exec_gated`, `pipe_oversized`,
  `pipe_no_reply`, `pipe_big_reply`, `pipe_dead_claim`, `pipe_no_wait`,
  `pipe_bad_mode`, …): a fault kills the reader. **The fault column is
  observable only from a SURVIVOR**, which is why the sweep's two fault
  rows are child-fault cases and why the dedicated proof's negative arm
  asserts the refusal rather than the count.
- **Deterministic but not augmented**: `pipe_basics`,
  `pipe_wait_reply/give/room/server`, `pipe_abandon`, `timer_oneshot`,
  `process_lifecycle`, `process_reclaim`, `give_boot_drain`,
  `map_into_child`, `quota_exceeded`, `quota_vs_wall`,
  `child_no_shutdown`, `death_notify`, `death_late_attach`,
  `share_double_map`, `event_*`, `thread_basics`, `waiter_revoked`,
  `refcount_free`, `mapping_slot_free`, `handle_drop_release`,
  `process_bootdrain`, `interrupt_unbind`. Every one of them COULD carry
  a bracket; none of them would say anything the five above do not. The
  restraint is the point — a count asserted in thirty places is thirty
  chances for an unrelated edit to fail the gate, and the design asks
  for assertions where they are the CLAIM, not everywhere they are
  possible.
- **Non-Saw cases** (`trap_fault`, `panic_seam`, `umode_*`, `timer_tick`,
  `external_irq`, `preempt_*`, `task_dump*`, `no_root_image`,
  `root_image_*`): hand-written kernel entries with no `sos` surface to
  call. Out of scope by construction.

## The dedicated proof

`tests/process-stats` (root) + `tests/child-stats` (child), both arches,
`expect_status: EXIT_PROCESS_FAULT` — the case ends on its own negative
arm, `pipe_no_post`'s shape.

    SOS stats: self delta=9 over 8 probes
    SOS: process exit: code=0x…07 process=0x…01
    SOS stats: child syscalls=8 faults=0
    SOS stats: interrupts=0                    <- printed, NOT asserted
    SOS stats: minted without Stats
    SOS: process fault: access denied process=0x…00

Four claims, in the ruling's own order. The self delta is eight
`get_status` probes plus the closing read's own trap; `get_status` is the
probe because it makes no object, mints no handle, charges no quota and
prints nothing, so repeating it moves exactly one number in the machine.
**THE CHILD PRINTS NOT ONE BYTE**, and the silence is the design: a
talking child has a syscall count that is a hash of its own prose, and
its launcher asserts the exact number. Eight is `process_self` + six
probes + `exit`. The phases are one function each, per design 14 finding
3's 16 KiB ceiling.

**`interrupts=0` READS THE SAME ON BOTH PROFILES TODAY** and is still
not asserted, on the doctrine: a tick lands where the host puts it. The
zero is explained rather than lucky — root takes no tick in user mode
over so short a run, and the time it spends waiting for its child is
spent IDLE, where an interrupt is polled rather than trapped. CLAUDE.md's
timing-rows list names this row beside `thread_preempt`'s and
`timer_interval`'s, and says a non-zero reading is not a finding.

## Transcript accounting

Whole-report diff, base `2a405db` (captured before the first edit) vs the
gate, ANSI stripped and bucketed mechanically (case rows
`[n/m] MARK name`, image rows `path (N bytes)`, everything else):

| bucket | rows | what |
|---|---|---|
| byte-identical | 196 case-row MARKS + names + INDICES | every pre-existing case, same mark, same name, same index within its arch. `thread_preempt` and `timer_interval` included, so the documented-nondeterministic bucket is EMPTY at the report level this run |
| byte-identical | 87 image-size rows | every arm64 image except one — see the +4096 row below |
| byte-identical | every runner `expect_out` STRING | the runner diff has ZERO removed lines (`git diff` confirms: purely additive), so no existing assertion was reworded, reordered or relaxed. The five augmented cases gained rows and changed none |
| authorized-with-cause | 196 case-row denominators | `[n/98]` -> `[n/99]`, the DENOMINATOR alone, because one case was appended. Zero indices moved, which is what says APPENDED rather than inserted |
| authorized-with-cause | 83 image-size rows, riscv32, **+40 each, exactly** | every riscv32 image that links `sos` grew by the same 40 bytes, and **THE CORRELATION IS EXACT AND WAS VERIFIED RATHER THAN ASSUMED**: the base built 88 riscv32 images, 5 of them are the cases this unit edited, and the remaining 83 all moved by exactly 40 — none by more, none by less, and none unchanged. The cause is the new `@export("sos_process_stats")` seam — an exported symbol cannot be dead-stripped, so a package that never calls `stats()` still carries the stub |
| authorized-with-cause | 1 image-size row, arm64, +4096 | `pipe-send-manual`, one page. The same +40 lands on every arm64 image too and is absorbed by 4 KiB page granularity everywhere else; this is the one package whose growth crossed a boundary |
| authorized-with-cause | 5 image-size rows, riscv32 | the sweep's own code: `pipe-send-manual` +2992, `death-fault` +1520, `process-isolation` +1472, `pipe-oneshot` +1208, `thread-preempt` +368. Each is one bracket plus one formatted print, and each is inclusive of the +40 above |
| address-only | 0 | nothing printed an address that moved |
| documented-nondeterministic | 0 | none met at the report level. The two console rows in this bucket (`process_stats`' and `thread_preempt`'s `interrupts=`) are inside per-case transcripts, which the runner prints only on failure |
| new | 2 case rows | `process_stats`, index 99 of 99, both arches |
| new | 4 image rows | `process-stats` and `child-stats`, both profiles |
| new | 1 summary row | `196 passed` -> `198 passed` |

No row is unaccounted for, and **NO IMAGE SHRANK on either profile.**

**THE MOST INTERESTING THING THE DIFF DOES NOT SHOW IS THE FIVE
AUGMENTED CASES' EXISTING ROWS.** `pipe_send_manual`'s
`reply len=4 b=80,79,78,71` and `filled=16`, `pipe_oneshot`'s
`zero reply len=0` and both ring-depth rows, `process_isolation`'s
`child status=131072`, `death_fault`'s `woke key=45 status=131073`, and
`thread_preempt`'s `joined a=33 b=44` all still say what they said, byte
for byte, with a trap-count bracket now wrapped around the machinery
underneath them. That is what says the counting is an OBSERVATION rather
than a change: `ktrap` gained three increments and no behaviour.

## Findings

1. **THE PRINT SEAM IS THE DOMINANT SYSCALL COST, AND NOTHING SAID SO
   BEFORE THIS UNIT.** `debug_print` traps once per BYTE, so a root
   server's console prose outweighs its object ops by an order of
   magnitude — `pipe_oneshot` spends ~5 traps on the exchange it exists
   to prove and ~300 on describing it. That is fine for a test and would
   not be fine for a service, and it is now a number rather than a
   suspicion. A buffered `debug_print` taking a length is the obvious
   answer and is NOT taken here: it is an ABI change with no consumer
   yet, and unit 3.5's `Call` is the next thing that will want the same
   argument made properly. Filed as an observation for whoever writes
   that unit.
2. **THE RAII TIER COSTS TRAPS, VISIBLY.** Every owning wrapper's
   `deinit` issues a `sos_handle_release`, so a phase that mints five
   handles pays five traps to put them down. `pipe_oneshot`'s bracket is
   placed to exclude them precisely because they are real; a future unit
   that wants to know what a composition costs END TO END now has the
   instrument to measure it.
3. **THE `faults` COLUMN IS ONLY EVER OBSERVABLE FROM A SURVIVOR**, and
   that shaped the sweep more than anything else in the brief: 25 of the
   suite's fault cases are root faulting with nobody left to read the
   number. The brief's own `map_wx_refused` candidate is one of them.
   Recorded because the same trap awaits anyone extending the sweep.
4. **THE INTERRUPT COLUMN IS SMALLER THAN A READER WILL EXPECT**,
   because idle-time and preemption-point deliveries are polled rather
   than trapped. That is the correct reading of "taken while this process
   was current", but it means the column is NOT a measure of interrupt
   load on the machine — it is a measure of preemption pressure on one
   process. Worth stating wherever the shared-region future gets built,
   since a userspace `top` reading it would otherwise report a machine
   that takes almost no interrupts.

No SL-N filed: nothing in this unit met a sawlang deficiency. The one
language shape worth noting is that it met none of the four open ones
either — SL-13's `&var [T; N]` restriction never came up, because every
buffer here is a record the kernel writes and the caller reads by field.
