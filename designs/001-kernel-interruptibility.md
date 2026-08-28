# SawOS design 1 — Kernel interruptibility: preemption points (M3 unit 1.5)

Status: BUILT Aug 28 2026 (see "As built" at the end — one deviation,
D-2's placement, with the module cycle that forced it). AUTHORED
Aug 28 2026 (lead), implementing sawlang#232 pin 1 —
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

> **AS BUILT — this paragraph's placement did not survive.** A point
> delivers, and delivery reaches back down to the byte loops, so a point
> written inside `mem`'s movers is an import cycle. The guard, the point
> and the loop are each still single-sited; the cadence became its own
> module between them. Full argument in "As built" below.

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

Landed Aug 28 2026. `SAWLANG_ROOT=… make sos-test`: 84/84 across
riscv32 + arm64, 42 cases per architecture, 43 entries.

### The mechanism, three pieces in three places

- **`preempt_point()` — `kernel/core/irq.saw`, beside `idle_poll`**, as
  D-1 asked. Body: the guard, `hal.irq_poll()`, `deliver_line(line,
  PREEMPT_PC)`. It is `idle_poll` with a different reason for asking, and
  the file says so. NO NEW HAL SEAM — `irq_poll` was reused exactly as
  expected, so neither `ABI.md` changed.
- **`IN_DELIVERY` — `irq.saw`, beside the counters**, `public(package)
  unsafe static var`, with design 149's serialization argument written
  out (uniprocessor, hardware delivery from user mode only, and the
  polled entries are the flag's own readers, so the single kernel context
  is the only writer; SMP is where that ends). `deliver_line` SPLIT into
  a guarded entry (`IN_DELIVERY = true` / body / `= false`) and its body
  `service_claimed_line`. The split is the mechanism, not decoration:
  Saw has no `defer`, and the old body had an early return, so an inline
  set/clear pair would need the clear written twice — the second is the
  one a later edit forgets.
- **`PREEMPT_STRIDE = 4096` and the movers — a NEW module,
  `kernel/core/preempt.saw`**, between `irq` and `sched`. It holds the
  stride and `long_copy` / `long_zero`, the strided long-op movers every
  bulk copy goes through. This is the one deviation from the brief's
  letter, and the finding below is why.

### FINDING: the points cannot live inside the movers (module cycle)

D-2 asked for the points inside `copy_bytes`/`zero_bytes`, no twin and no
parameter. That is not expressible, and the obstacle is real rather than
stylistic:

- A point DELIVERS, so it needs `irq.deliver_line`.
- Delivery reaches back DOWN to the byte loops: `deliver_line` →
  `on_timer_interrupt` → `expire_timers` → `fire_timer` →
  `wake.notify_ready` → `wake_one_waiter` → `deliver_attachment` →
  `process.copy_out` → `mem.copy_bytes`.
- So a point written in the loop is `mem` importing `irq` from below it —
  the import cycle DF-232e diagnoses. **The call graph genuinely has this
  cycle, and `IN_DELIVERY` is exactly what breaks it at run time**; the
  module system has no way to express a cycle a runtime flag makes safe.

An indirection was probed and does not exist either: a `FuncPointer` hook
installed at boot needs a mutable static holding a function address, and
``static `X` must be initialized by a compile-time constant`` refuses it
(probed against the pinned sawc). `@export`/`extern "C"` would break the
cycle at the linker, but this tree reserves that for genuine machine
seams and it would put a kernel-internal call outside the type system.

**Resolution, and what it preserves.** Every piece stays single-sited:
the byte loop exists once (`mem`), the point exists once (`irq`), the
cadence that joins them exists once (`kcore.preempt`). Nothing is
duplicated and no call site picks a POLICY — it picks an ALTITUDE, and
the two altitudes are named for what they are (`mem`'s loops are the
primitive, `preempt`'s movers are the operation). `mem.saw`'s movers and
`kcore/lib.saw`'s module-order list both carry the reason at their site,
and `preempt.saw`'s header has the full argument.

**What it costs.** A bulk copy issued from BELOW `kcore.preempt` cannot
take a point. Today that is only `process.copy_out`/`copy_in`, whose
whole traffic is a 24-byte wait record — under one stride, and inert
under `IN_DELIVERY` anyway. §2.1's Pipe bodies are the length that will
want one; their callers (`dispatch`) sit ABOVE `kcore.preempt`, so the
pointed door goes there in M4. Unit 2's CreateProcess image copy is
unaffected — it can call `long_copy` directly.

### The two calls the brief delegated

- **`PREEMPT_PC = 1`, a distinct sentinel rather than a shared
  `IDLE_PC`.** Diagnosability decides it: the two polled deliveries are
  the two ways the kernel notices an interrupt nothing trapped it into,
  and a transcript that cannot tell "idle" from "halfway through a
  240 KiB copy" has lost the fact the unit exists to make visible. Safe
  for the same reason zero is — an odd address is not an instruction
  address on either profile, both machines fetching on at least a
  two-byte boundary. Recorded at the definition. `preempt_tick` ASSERTS
  it (`SOS: timer tick 0x…01 at 0x…01`), which is what makes "which
  delivery ran" a claim rather than an inference.
- **Stride placement: with the movers (`preempt.saw`), not with the
  point.** The cadence is a property of the operation, and the point is
  correct at any cadence. Recorded at the definition.

### The audit, as landed

Every verdict in the design-time table SURVIVED — none moved. The
verdicts are comments at the sites, in the one-sentence-rule form:

| Site | Where the verdict is written |
|---|---|
| `copy_bytes` / `zero_bytes` | `kernel/core/mem.saw` (primitive; and why the point is not here) |
| the strided movers | `kernel/core/preempt.saw`, on `long_copy` / `long_zero` |
| loader validate walk | `kernel/core/loader.saw`, at the walk |
| loader place walk | `kernel/core/loader.saw`, at the walk (incl. the mid-load tick note) |
| PLIC reset | `hal/riscv32/kernel/lib.saw`, on `Plic.reset` |
| GIC disable-all | `hal/arm64/kernel/lib.saw`, on `Gic.reset` |
| arm64 page-table build | `hal/arm64/kernel/lib.saw`, on `page_tables_build` |
| `prot_reset` / `prot_region` walks | both HALs, on `prot_reset` |
| slab scans / handle walks | `kernel/core/limits.saw` header — one verdict where the bounds are declared |
| `end_process` teardown | `kernel/core/process.saw`, on `end_process` |
| IRQ-path loops | `kernel/core/irq.saw`, on `expire_timers` |
| console write loops + UART spin | `kernel/core/diag.saw` (`write_str`) and `rt/common/src/lib.saw` (`ConsoleSink.write_byte`) |
| `.bss` zero / `frame_init` / `mem*` | both `boot.S`s, both `frame_init`s, `rt/common_c/support.c` |
| `MachineTimer.now` carry retry | `hal/riscv32/kernel/lib.saw`, on `now` |
| `idle_until_runnable` | `kernel/core/irq.saw`, on the function |

One addition the table did not name: `mem*` in `support.c` gets the
verdict too, because its CALLER is codegen and there is no source site to
write a placement sentence at.

### The proof, and the transcript

`preempt_tick` and `preempt_extirq`, both all-arch, both entered at
`tests/preempt_*.saw` and both running the SHIPPED mover (`long_zero`
over an 8 KiB kernel scratch buffer — two strides, so one call crosses
two points).

```
SOS M3: preemption check on riscv32 (QEMU virt)
SOS M3: kernel section begin
SOS: timer tick 0x00000001 at 0x00000001      <- taken IN KERNEL MODE
SOS M3: kernel section end, ticks taken=0x00000001
SOS M3: entering U-mode

SOS M3: raised external irq 0x0000000a
SOS M3: kernel section begin
SOS: external irq 0x0000000a                  <- BEFORE the entry
SOS M3: kernel section end
SOS M3: entering U-mode
```

Counts: **43 entries, 42 per architecture, 84 runs**, as the brief
specified.

ACCEPTANCE, and exactly what was diffed. The Aug-21 oracle
(`designs/sawlang/238-sos-oracle-2026-08-21.txt`) predates the repository
split, so its build-info lines carry `sos/` path prefixes and pre-split
image sizes; a raw diff against it cannot separate this unit's effect
from three months of intervening commits. So the suite was run TWICE on
this machine under the suite lock — once with the change stashed
(baseline, at `1893143`) and once with it applied — and both were diffed:

- **baseline vs the Aug-21 oracle**: the 80 case rows
  (`[i/40] ✓ <name>`) are byte-identical, in order, both architectures.
  The only differences are the 38 sosimg build-info lines: the `sos/`
  path prefix (the split) and image sizes that grew before this unit.
- **baseline vs this change**: the ONLY differences are the two new rows
  per architecture at positions 13-14, the `/40` → `/42` denominator on
  every row, and the total line. **Every sosimg size line is unchanged**,
  and `timer_masked_in_kernel` still reads `ticks taken=0x…0` — the
  witness that UNPOINTED kernel code stays masked is intact.

`thread_preempt` is the one shipped case whose CONSOLE output moves: it
arms a tick before loading root, so ticks are now taken mid-load at
preemption points. The brief anticipated this and ruled it legal (no
current thread, no armed Timer objects pre-root); its assertions do not
name the tick lines and the row is unchanged. The loader's place walk
carries that note at the site.

### Nothing left open

No HAL `ABI.md` changed (no new seam). No spec text was contradicted —
§9 gained a `BUILT M3 unit 1.5` block, §9b's timing bullet now says still
unbuilt *after* unit 1.5 and why a point needs no lock, and §11's ledger
row flipped from "kernel interruptibility … unbuilt" to
"`IntrSpinLock`/SMP unbuilt, interruptibility BUILT". No compiler defect
was worked around silently: the one language limit met (a `FuncPointer`
cannot initialize a mutable static) is recorded above and in the tracker,
and it shaped the module layout rather than being hidden by it.
