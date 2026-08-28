# SawOS design 1 — Kernel interruptibility: preemption points (M3 unit 1.5)

Status: AUTHORED Aug 28 2026 (lead), implementing sawlang#232 pin 1 —
ruled Aug 16 by the user: M3 takes kernel interruptibility EARLY, lean
mechanism (a), explicit preemption points. The first sawos-native
design. Copies of the sawlang briefs cited here live in
`designs/sawlang/`.

## The ruling this implements (sawlang#232 pin 1, verbatim scope)

Design 178's D2 tripwire — "syscall latency bounds interrupt latency;
re-examine when any M3 syscall grows a loop" — fires in M3:
CreateProcess's image copy IS a loop, and so is arm()'s copy-in
validation. The ruling: add interruptibility to long kernel operations
NOW, while the kernel surface is at its smallest. The mechanism is
explicit PREEMPTION POINTS — interrupts stay masked in kernel mode
except at named points inside long loops where no IRQ-shared invariant
is in flight; the interrupt is taken, serviced, and the op resumes. No
IntrSpinLock, because a point IS the assertion that state is
consistent, and the placement rule ("no in-flight invariant over state
the IRQ path touches") is a reviewable sentence per site. Full kernel
preemption is what SMP forces later; by then the point placements are
a map of exactly where the locks must go. This unit lands BEFORE
CreateProcess so unit 2's image-copy loop is born with its points
rather than retrofitted.

Deliverables per the ladder entry: the mechanism; a synthetic long-op
+ selftest-line proof on both arches; the placement audit of every
existing kernel loop.

## D-1: A point POLLS AND DELIVERS; it does not unmask and trap (lead)

Two ways to "take" an interrupt at a point:

- **Trap**: briefly unmask (`mstatus.MIE` / PSTATE.I) so a pending IRQ
  traps into kernel mode, service it, remask. Requires a kernel-mode
  trap path on both machines — today that vector IS `kernel_fault`
  (riscv32 boot.S mscratch-zero branch; arm64's deliberate absence of
  a current-EL IRQ vector) — plus nested-frame save/restore asm.
- **Poll**: at the point, ask the hardware what is pending and deliver
  it synchronously — `hal.irq_poll()` → `deliver_line(...)`, which is
  EXACTLY the idle path's existing contract (`idle_poll`,
  kernel/core/irq.saw). The hardware still never delivers an interrupt
  in kernel mode.

LEAN, FIRMLY: **poll**. It reuses the delivery funnel the idle path
already proved; it adds zero assembly and no nested trap frames; and
it KEEPS D2's hardware enforcement as a live tripwire — a hardware
interrupt arriving in kernel mode remains a diagnosed stop on both
machines, which is worth more as an invariant check than as a delivery
path. The D2 sentence sharpens rather than repeals: "the hardware
never delivers an interrupt in kernel mode; the kernel ASKS, at named
points." Semantics are identical from the interrupt's view — serviced
at the point, op resumes — and pin 1's SMP story is untouched (real
asynchronous preemption arrives with IntrSpinLock; the points are its
placement map).

The primitive: `preempt_point()` in `kernel/core/irq.saw`, beside
`idle_poll`, whose body is the guard (D-2), one `hal.irq_poll()`, and
`deliver_line` on a hit. The tick-report pc argument uses a sentinel
as the idle path does (whether to share `IDLE_PC` or mint a
`PREEMPT_PC` for diagnosability is the implementer's call — record
which and why at the definition).

A reschedule signaled at a point (NEED_RESCHED from a tick, a wake
from an expired timer) is HONORED WHERE IT ALWAYS WAS: at the
user-return boundary via `pick_next`. A point interrupts the op's
latency, not its atomicity — the op still completes before the switch
it may have motivated. That is the whole design.

## D-2: The reentrancy guard, because the byte movers are shared

`copy_bytes`/`zero_bytes` (kernel/core/mem.saw) serve two masters: the
loader's segment placement (preemptible boot context, up to 240 KiB)
and the copy funnels — and `copy_out` runs IN IRQ context
(wake.saw's deliver_attachment writes a parked thread's wait record
from the waker's context, including timer expiry under
`deliver_line`). A point inside a mover must therefore be INERT when
the mover is itself running under delivery, or a timer firing during a
copy could re-enter delivery mid-delivery.

Mechanism: one kernel global, `IN_DELIVERY` (irq.saw, beside the
counters, with the design-149 serialization argument written out), set
around `deliver_line`'s body and checked first in `preempt_point()` —
set means return immediately. The flag is the MECHANICAL form of "no
point runs inside the IRQ path": it converts a placement error in
shared code from a corruption into a no-op, and it is one auditable
site instead of a per-call-site convention. (`idle_poll` and
`service_irq` both funnel through `deliver_line`, so the flag covers
trap-driven and polled delivery alike.)

With the guard, the movers carry their points UNCONDITIONALLY — no
`preempt:` parameter, no `_preemptible` twin. Today's IRQ-path copies
are ≤ 24 bytes (wait records), far under one stride, so the guard is
belt-and-braces there; the loader's 240 KiB copies are where the
points actually fire.

## D-3: Cadence — a stride, not a poll per byte

`irq_poll` costs a CSR read (riscv32 mip, plus a PLIC claim read on a
hit) or a GIC MMIO read (arm64) — per-byte polling would multiply a
240 KiB copy by an MMIO round-trip. The movers poll every
`PREEMPT_STRIDE` iterations (named constant beside the primitive;
start at 4096 — a ~4 KiB stride bounds the masked window at tens of
microseconds while keeping poll overhead under 1% of the copy).
Latency accounting: worst-case kernel-masked latency after this unit ≈
stride cost + the longest UNPOINTED audited section below, versus
"the whole syscall" before it.

## The placement audit (every kernel-mode loop, one verdict each)

The rule, per site: a point is legal where NO in-flight invariant
spans state the IRQ path touches (§3.4 of the census: TIMERS[],
NEXT_TICK_NS, the comparator, INTERRUPTS[].pending, ATTACHMENTS[],
WAITERS[], EVENTS[].word, THREADS[].state/link, READY_HEAD/TAIL,
NEED_RESCHED/SLICE_LEFT, the claimed-but-uncompleted line). Verdicts
land as comments AT THE SITES; this table is the design-time record —
the as-built section updates it if implementation disagrees.

| Loop | Verdict | The sentence |
|---|---|---|
| `copy_bytes` (mem.saw) | **POINT**, strided | Partially-written destination bytes are no IRQ-read state; guard covers the IRQ-context callers. |
| `zero_bytes` (mem.saw) | **POINT**, strided | Same argument; the loader's .bss zeroing is the largest single masked window today. |
| Loader validate walk (loader.saw) | no point | ≤ 8 iterations, O(1) body; bounded-small. |
| Loader place walk (loader.saw) | covered by movers | Staged uncommitted `prot_*` state is in flight across the walk, but the IRQ path never reads prot state — the movers' points are legal here. Timer ticks CAN fire mid-load in harness kernels that arm first (no thread: tick skips the slice; no armed timers exist pre-root) — legal by the same audit. |
| PLIC reset loops (95+3 stores) | no point | Half-masked controller IS IRQ-shared state in flight — ineligible; also pre-`intc_init`-completion, so nothing can be pending. Both reasons recorded at the site. |
| GIC disable-all (≤ 32 words) | no point | Same shape: distributor off for the whole loop. |
| arm64 page-table build (~2562 stores, pre-kmain C/asm) | no point | Pre-Saw, pre-IRQ by construction; native code takes no points. |
| `prot_reset`/`prot_region` walks (≤ 1536 RAM stores) | no point | Bounded, RAM-speed, µs-scale; and mid-rewrite descriptors are exactly an in-flight invariant. |
| Slab scans / handle walks (≤ 8/16/20) | no point | Bounded-tiny. |
| `end_process` teardown (~68 visits + report) | no point | The scheduler is dismantled mid-teardown (CURRENT_THREAD/READY cleared before the scans) — IRQ-shared state in flight the whole way; and the op ends in a machine stop or a fresh schedule. |
| IRQ-path loops (`expire_timers`, wake walks, attachment unlinks) | ineligible | Under the guard by definition. |
| Console write loops + UART ready spin | no point, ACCEPTED latency | Device-drain-bounded; a long diagnostic line is masked latency and recorded as such. Points in the console path would interleave delivery reports into the very transcript the oracle diffs. |
| `.bss` zero / `frame_init` / memset (asm/C) | no point | Pre-IRQ or tiny; native. |
| `MachineTimer.now` carry retry | no point | Bounded by one carry. |
| `idle_until_runnable` | none needed | It IS delivery. |

## The proof (harness; the ladder's "synthetic long-op + selftest-line")

Two new all-arch cases, modeled on the two existing D2 witnesses and
INVERTING their assertions — 43 entries, 42/arch, 84 runs:

- **`preempt_tick`** — timer_mask's mirror. Arm the tick, print
  "kernel section begin", then run the REAL mover (repeated
  `zero_bytes` over a kernel scratch buffer — the proof must exercise
  the shipped point, not a test-only loop) until `timer_ticks() != 0`;
  print "ticks taken={one}" BEFORE entering U-mode. The exit condition
  is itself the proof: with polling broken the counter can never
  advance in kernel mode (hardware delivery would be `kernel_fault`),
  so failure is a timeout, not a wrong number.
- **`preempt_extirq`** — extirq's mirror. Raise the selftest line
  (`hal.irq_raise_selftest_line()`), then run the pointed long-op in
  kernel mode; the harness asserts the external-irq report lands
  BEFORE "entering U-mode" (extirq asserts the opposite order today).

Existing 80 rows stay ROW-IDENTICAL to the unit-0 oracle
(`designs/sawlang/238-sos-oracle-2026-08-21.txt`) — the guard plus
"shipping kernels arm no tick" means no shipped transcript changes.
timer_mask KEEPS its masked-section semantics and its transcript: its
kernel section spins on `timer_pending()` and calls no mover, so it
still takes zero ticks in-section and remains the witness that
UNPOINTED kernel code stays masked. Both new cases follow the suite's
transcript-order discipline (cursor-ordered `expect_out`).

## Docs the unit owes

- `spec.md`: §9's D2 block and §9b's "still unbuilt" paragraph gain
  the BUILT M3-unit-1.5 amendment (poll-at-points, hardware tripwire
  retained); the §11 ledger row flips.
- `kernel/core/irq.saw:57-74` (the canonical D2 statement) rewritten
  to the sharpened sentence.
- HAL `ABI.md`s: expected NO new seam (`irq_poll` is reused); if the
  implementation grows one, both tables + the "what is arch-specific"
  sections, per convention.
- Tracker: the implementing agent closes the queue entry IN PLACE in
  `designs/todo.md`; the lead moves it at integration.

## Out of scope

Full preemption + IntrSpinLock (SMP, after pipes — sawlang#232);
points in console/diagnostic paths (audited-accepted above);
CreateProcess's copy loop (unit 2 consumes the mechanism); any change
to shipped transcripts.

## As built

(To be completed by the implementing agent: point sites as landed,
guard mechanics, stride value, any audit-table verdicts that moved and
why, final case names + counts, findings.)
