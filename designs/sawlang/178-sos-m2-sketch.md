# Design 178 — SOS M2 SKETCH (slicing RULED, agenda in progress)

**Status: FULLY RULED (user, Aug 15). Option A — the concurrent kernel:
interrupts + timers + threads + scheduler + Event + Waiter; channels and
memory objects are M3. D1-D6 RATIFIED AS WRITTEN, plus the carried
fault-don't-status item: BadHandle, BadOp and AccessDenied ALL become
faults — none survives as a status; statuses are reserved for exhaustion
and peer-death classes; `Unknown` stays a userspace mapping artifact.
D2's cost sentence is the recorded M3 tripwire: syscall latency bounds
interrupt latency — re-examine when any M3 syscall grows a loop.
The deliverable ladder below is now the M2 plan of record; every unit
per-arch-gated, branch parked for user review per SOS policy.
CORRECTION (Aug 15): pin 1 is ALREADY SATISFIED — design 158 landed
Aug 8 whole (the `__saw_bt_table` blob, tools/lldb_saw.py, the
in-process post-panic dump hosted + freestanding; the `bttable`
battery lane is its gate). The sketch's "first M2-track dispatch"
phrasing predated that landing by a day. The first REAL M2 dispatch is
therefore the trap/timer/interrupt-controller HAL unit.**

**Original sketch header (Aug 7):** M1+M1b+172 are landed: one arch-free
kernel, two HALs, typed handles / SosStatus / kind-scoped rights, sosimg v3,
the C floor at 207 stated-reason lines. This document proposes M2's SCOPE
for the user conversation — the decisions section is the agenda, not a
ruling. Standing pins that shape it are cited from ratified spec sections.

## The three candidate slicings

**Option A — the concurrent kernel (RECOMMENDED).** Interrupts + timers +
threads + scheduler + Event + Waiter. Channels and memory objects wait for
M3. The milestone proof is the microkernel money shot: a USERSPACE UART
echo driver — root server holds an Interrupt handle (§2 object table),
waits on it through a Waiter (§2.2), reads the device through the
design-112 driver idiom, no kernel driver code at all.

**Option B — IPC first.** Channels (§2.1 rendezvous + ReplyHandle) + Waiter
+ Event over the existing single-threaded kernel; interrupts M3. Cheaper
bring-up, but a blocking `receive` wants a real scheduler underneath, so
half of A gets built implicitly and unnamed.

**Option C — memory first.** MemoryObject/Mapping (§2.3/2.5 typed pools) +
loading a SECOND process; interrupts M3. Exercises the handle table
hardest, but defers everything that makes a kernel feel alive, and M1's
static grant window already covers the single-root case.

Why A: interrupts are the hairy foundation the 172 C-diet was explicitly
bought for; the design-158 pin (below) already sequences the debugger
tooling ahead of it; timers force the preemption questions while the
kernel is still small; and Event/Waiter are the two SMALLEST objects that
prove the handle machinery generalizes past System.

## Standing pins that bind M2 (already ratified/recorded)

1. **Design 158 unit (c) lands BEFORE interrupt bring-up** (approved Aug 6:
   in-binary task state tables + panic-time dump — "the sos task dump
   sells it"). This is the first M2-track dispatch, and it is compiler/
   tooling surface, not sos/.
2. **Every syscall is an object op** (§5.7) — new objects mean new op
   tables + rights enums (145 backed-enum idiom), never new syscall shapes.
3. **Handles are (slot, generation)**; new object kinds join the typed-
   handle scheme (M1's ratified asymmetry: alias flows out, construction
   gated in dispatch).
4. **IntrSpinLock (§9b)** is the M2-era lock; v1 proposal below avoids
   needing it immediately.
5. **arm64 FP state is not saved across traps** (M1b's recorded
   inheritance).
6. **Fault, don't status (ratified Aug 8, from the 180 review).** A caller
   error the process could have checked — an invalid or stale handle, a
   malformed op, a rights violation — TERMINATES the offending process
   (the kernel stays up; it is the process's bug, the same line the
   language-level accessor rule draws). SosStatus returns are reserved for
   conditions the caller could not reasonably know: memory exhaustion, a
   peer process dying mid-operation. This cuts userspace error handling to
   the cases that are real. M2 agenda item: restate the op tables under
   this rule and decide which of BadHandle/BadOp/AccessDenied survive as
   statuses at all (candidate: none — all three become faults; `Unknown`
   stays a userspace mapping artifact per §5.7).

## Proposed simplifications to ratify (the decisions agenda)

- **D1: processes stay integer-only in M2.** DF-162a made freestanding
  aarch64 NO-FP by default, and the root server builds under exactly that —
  so the FP-context-switch question can be DEFERRED WHOLE: no FP state to
  save because no process may generate any. Revisit when the Float family
  (173) meets userspace. riscv32 mirrors (no F extension enabled).
- **D2: kernel is non-preemptible in v1** — interrupts taken from USER mode
  only (kernel runs with interrupts masked, M1's discipline retained inside
  syscalls). Kills the IntrSpinLock dependency for M2; §9b implements in
  M3/SMP. Cost: syscall latency bounds interrupt latency — fine at M2's
  syscall lengths.
- **D3: the scheduler is round-robin over runnable threads, timer-driven,
  ONE core.** No priorities in M2 (the §7 priority map stays a loader
  artifact until M3); `yield` op + block-on-wait + timer preemption only.
- **D4: Thread and Process become real objects** (spec §2 table rows) with
  create/start/exit/join ops, teardown per the ratified §2 Process row
  (close-all, free-owned); still ONE address space domain per process via
  the M1 grant mechanism (real Mapping objects are M3, per option A).
- **D5: Event (OR + saturating-sum, §2.4) and Waiter (level-triggered,
  persistent attach, §2.2) exactly as ratified — no deviations proposed.**
- **D6: Interrupt object** binds an IRQ line to a waitable (§2 table);
  ack via the handle; PLIC (riscv32) and GIC (arm64) bring-up live in the
  HALs behind the seam 172 hardened — kcore stays arch-free.

## Deliverable shape (if A + D1-D6 ratify)

Units roughly: 158(c) first (separate dispatch, compiler surface) →
trap/timer/PLIC/GIC HAL work → Thread/Process objects + scheduler →
Event/Waiter → Interrupt object + the userspace UART echo proof → spec
updates + the M2 test set (both arches, sos_runner grows again). Every
unit per-arch-gated like M1b; SOS policy: branch parks for user review.

## UNIT 1 — trap / timer / interrupt controller (BUILT Aug 15, branch PARKED)

The first real M2 dispatch, and the foundation the next three units stand on.
Both profiles, gated together, 38 harness cases.

**The seam.** Each HAL gained twelve names and the kernel reaches interrupts
through those and nothing else: `is_interrupt(cause)`, `IRQ_NONE`, `IRQ_TIMER`,
`intc_init`, `irq_unmask`, `irq_mask`, `irq_claim(cause)`, `irq_complete(line)`,
`timer_start(period_us)`, `timer_rearm`, `timer_pending`, and
`irq_raise_selftest_line`. `fault_pc` became `trap_pc` — the value never was
fault-specific and the tick path reports it. Both `ABI.md` files carry the
table and what each machine does differently.

**One funnel, two hooks (obligation 1).** `ktrap` decides interrupt / fault /
syscall IN THAT ORDER — an interrupt is not the running program's business and
its instruction has not run, so it must never reach the syscall return path,
which steps the saved PC. An interrupt goes to `service_irq`, the single
arch-free entry: claim the line, rearm the timer OR mask the device line (spec
§9), run the hook, complete. The hooks are `on_timer_tick(frame)` — where the
scheduler's timeslice accounting goes — and `on_external_irq(line)` — where the
Interrupt object will mark itself ready. Both are empty of policy today and say
in their docstrings what they must not grow into: they run with interrupts
masked, so their length IS interrupt latency.

**D2 is enforced by the machines, not intended by the kernel.** Profile A never
sets the global interrupt enable, and its architecture delivers to a lower
privilege mode regardless of it — so that bit staying clear IS "user mode only",
and a wrongly-arrived interrupt hits the mode witness and the kernel-bug path.
Profile B masks at EL1 throughout, unmasks only in the SPSR the user-mode return
loads, and routes all four current-EL vectors to the kernel-bug path. Neither
kernel needs a critical section; `IntrSpinLock` (§9b) stays unbuilt and unneeded.

**What each machine does differently**, because the seam hides it and a porter
should not have to rediscover it: Profile A's timer is a memory-mapped core-local
comparator that is NOT one of its controller's sources (so `IRQ_TIMER` there is a
number one past the last real source, and `irq_complete` does nothing for it),
its counter is 64 bits on a 32-bit machine (carry-safe read, all-ones-first write
order), and a line is masked by PRIORITY because that controller ignores a
completion for a disabled source. Profile B's timer IS a controller line, so
every tick runs the ordinary claim/complete cycle; its controller version is
pinned on the emulator command line rather than defaulted; and its interrupt
vector calls `ktrap` with a cause the syndrome register cannot hold.

**The proof** (`make sos-test`, 19 cases per machine): `timer_tick` — armed,
into user mode, tick 1 and tick 2 land in the arch-free hook with the
interrupted user-mode address on each line; `timer_masked_in_kernel` — the
kernel spins until the timer is DUE, reports `ticks taken=0`, and the tick
arrives only after the entry to user mode (D2, read from the order);
`external_irq` — a line raised in the kernel, claimed, masked and completed in
user mode, which is the only coverage Profile A's controller gets since its
timer never reaches it.

**C floor: 135 to 140 code lines.** One line on Profile A (the interrupt-class
mask register), four on Profile B (its timer is system registers, one
instruction each) — reason 1 in every case. Everything else is Saw: both
controllers, the comparator arithmetic, the periods, the policy. Assembly: +13
lines on Profile B (an interrupt vector entry that shares the frame save, now a
macro, and the return path with the syscall entry), none on Profile A, whose
trap entry already handled every trap from user mode.

**Not built here, deliberately:** Thread/Process, the scheduler, Event/Waiter,
the Interrupt object — the next three units. `sos/kernel/main.saw` arms no timer,
so the real kernel boots exactly as it did.

## UNIT 2 — Thread / Process objects + the scheduler (BUILT Aug 15, branch PARKED)

D3 and D4 as ratified, over unit 1's tick. Both profiles, 22 harness cases each,
44 total.

**The trap frame IS the thread context, and that is the whole context switch.**
A thread's saved registers are the frame the HAL's trap entry already wrote on
the way in and read on the way out, so a switch is `ktrap` RETURNING A DIFFERENT
FRAME ADDRESS than it was called with. One switch point; at the user-return
boundary by construction, which is D2 enforced by the shape rather than
promised; no half-saved state to protect, because the kernel never holds one.
Entering user mode for the FIRST time is the same operation with a frame nothing
has run in, so `enter_user` — a context built by zeroing thirty registers in
assembly — is gone from both HALs, replaced by `frame_init` (Saw) and
`resume_frame` (a mode select and a branch into the trap entry's own restore
path). Each machine already had a "current thread" register to carry the frame
between traps and neither needed a new one: `mscratch` on Profile A, `SP_EL1` on
Profile B.

**One way into user mode.** `kcore.start_process` reifies a program as a Process
with one Thread and resumes its frame; the loader reaches it with a root image,
and the four harness kernels that grant a hand-written payload its own bytes
reach it with theirs. A kernel that could enter user mode WITHOUT a Process
would be a kernel with syscalls no handle table answers, which is what M1 was.

**The faults ruling, applied whole.** BadHandle, BadOp and AccessDenied are gone
from `SosStatus` — they are `FaultReason` tags now, keeping their numbers — and
meeting one terminates the process while the kernel stays up to report it and
the ratified teardown (close all handles, free every thread and frame). It had
to be uniform: an unbound handle names no KIND, so there is no op table to ask
which rule applies. `umode_bad_calls` therefore asserts the opposite of what it
asserted the day before, and both payloads say so. What is left as a status is
`NoResource` — a full slab, which a caller could not have known.

**Naming, ruled at review (user, Aug 15).** An op table or a rights set is named
for the spec §2 OBJECT it belongs to, spelled out, plus §5.7's own suffix:
`SystemOp` / `ProcessOp` / `ThreadOp`, `SystemRight` / `ProcessRight` /
`ThreadRight`. No abbreviated object prefix — the four characters `Sys` saves in
a file nobody types into cost every reader the question of whether it means the
System object or the syscall. The rule extends to anything else named after an
object, and `sos/kernel/abi/`'s module docstring carries it so the next object
kind cannot drift.

**Two ops per object, and derivation doing work.** System gained `SelfProcess`;
Process has `CreateThread` / `SelfThread` / `Exit` / `GetStatus`; Thread has
`Start` / `Join` / `Exit` / `Yield`. Root's boot register is still ONE handle
wide and everything else arrives through it, which is §3's derivation rule
earning its place rather than being quoted. `Process.Kill` and `CreateProcess`
are deliberately absent: with one process the first is `Exit` under a second
name and the second has nothing to load, and an op whose only reachable use is
degenerate is an op nothing tests.

**The proof, per machine.** `thread_basics` boots under a kernel that arms NO
timer: two workers print and yield, and the transcript reads `ABABABAB` — one
substring, eight switches, nothing but a `yield` able to cause any of them —
then joins both for 11 and 22, each worker's own value. `thread_preempt` is the
same shape with the yields REMOVED and the tick armed: `BABABABABABABABA` on
Profile A, `AABBAABABABABABB` on Profile B, sixteen switches nobody asked for.
`thread_fault` joins a handle it never held and is terminated for it, reported,
torn down, exit status 5.

**The native floor: assembly 342 to 268 code lines, C 140 to 166, total 482 to
434.** The assembly went DOWN because a context built in Saw needs no
register-clearing prologue in either HAL. The C went UP by exactly one function
per profile — `sos_syscall3`, three arguments in and the value register back out
through a pointer, because the ops that answer with a value need one and the C
ABI the Saw side declares against has no aggregate return. Reason 1 in both
cases, and the diet's direction is unchanged.

**Three findings, filed rather than worked around silently: DF-178c** (a process
cannot name a function's address, so `create_thread` has no entry a Saw program
can compute — the M2 API answers it with an image-entry overload and the real
fix is DF-172a's reason 3), **DF-178d** (a `-> Never` call is not accepted as a
`guard ... else` exit and emits malformed IR in some value positions), and
**DF-178e** (a bare literal does not adopt an annotated type through a `match`).

**Not built here, deliberately:** Event and Waiter — the wait/wake SUBSTRATE is
in (block, joiner lists, a wake that writes the sleeper's syscall return exactly
once) and the objects that will attach to it are the next unit. No priorities:
D3 keeps the §7 map a loader artifact, and the kernel still parses and reports
it. No idle loop: with no external wake source, "nothing runnable" is a genuine
deadlock in M2 and is reported as one; the wait-for-interrupt loop arrives with
the first object that can be signalled from outside.

## UNIT 3 — Event and Waiter (BUILT Aug 15, branch PARKED)

D5 as ratified, no deviations. Both profiles, 25 harness cases each, 50 total.

**Five object kinds where there were three, and the dispatch did not move.** An
Event and a Waiter cost one `struct`, one `allows`, one arm in `dispatch` and
one op table each — which is the claim §3 has been making since M1, tested for
the first time by objects that are neither singletons nor scheduler internals.
The handle table, the typed-handle construction after validation, the per-kind
rights scoping and the ratified teardown all took them with no shape change.

**Level-triggered is an ABSENCE, and that is the implementation.** Nothing
records that a waiter has been told about a ready event, so `Wait` SCANS the
attachment set for a non-zero word. Three ratified sentences fall out of that
one decision rather than being coded separately: a signal that lands before
anyone waits is not lost (§2.2's no-lost-edges), an event still ready after one
waiter has been woken is still ready for the next, and attaching an
ALREADY-ready handle reports it immediately — which is the order "signal, then
attach, then wait" that an edge design drops on the floor. An edge-triggered
Waiter would have needed a reported-flag per attachment and would have lost the
first of them.

**§2.4's saturation is a correctness requirement, not a nicety**, and it is
worth stating why in one place: zero is what "not ready" MEANS, so a counting
event that wrapped would go quiet exactly when its producer was fastest. The sum
uses the wrapping add and a carry test; the OR mode saturates by being
idempotent.

**The result shape.** SUPERSEDED BY RIDER 3 (below): the unit first widened the
ABI with a second value register and a `sos_syscall1_pair` per profile. The
ruling replaced that with a validated copy-out, which is both the smaller ABI
and the shape §2.1's message body needs anyway. What survives unchanged is the
key: handed back unread, because §2.2's point is that a word can encode the
waiting task's identity.

**One funnel for a syscall's answer (obligation 1).** `write_result` is now the
only place an answer reaches a frame, and its docstring names its three entry
points: the return path, a joiner wake, a waiter wake. Two of the three write
for a caller that was answered by nobody when it blocked, which is the invariant
the parking protocol rests on — exactly one write per syscall, or the saved PC
steps twice on the profile that steps it at all. **Waitability is the second
funnel**: `waitable_slot` decides in ONE exhaustive match both whether a kind
can be attached and whether the holder may, because which enum a rights word is
read against is decided by the kind — the two questions are one question, and
every case is spelled out so the next waitable kind cannot be added silently.

**Creation authority answers a §12 open pin.** `CreateEvent` / `CreateWaiter`
are Process ops on their own rights bits, so the Process handle IS the factory
capability rather than a quota — §3's derivation rule instead of a new
mechanism. A quota stays additive: a field on the process slot, checked where
`NoResource` is returned today.

**Faults, per the ruling**, with two new tags: `NotWaitable` (attaching
something that is not) and `BadArg` (a creation mode that is not a mode).
Attaching an already-attached handle and removing one this Waiter does not hold
are `BadState`. Every one is a mistake only the caller could have made.

**The proof, per machine.** `event_basics` is one thread and no timer, so
nothing in it is about scheduling: signal-then-wait answers with the right key,
a second attachment is told apart by its key alone, `1|2|1` is 3, five counted
signals are 5, and two signals of the largest word there is saturate instead of
wrapping. `event_wake` is the parking half and is read as an ORDER — the
worker's line lands BETWEEN the initial thread's "parking" and its "woke",
which it could only do if the first thread blocked, since that kernel arms no
timer and `start` does not switch. `umode_not_waitable` is a hand-written
payload, for the same reason `umode_bad_handle` is one: `Waiter.add` takes an
`&Event`, so attaching the System object is not a thing a Saw process can spell
— the typed layer's guarantee putting the kernel's own validation test at the
raw altitude.

**How two threads of one process share a kernel object**, since `event_wake` is
the first program that needs to: not by passing a handle word, which the ruled
typed layer makes unspellable, but by parking the `Event` VALUE in the process's
own memory. The kernel is not involved — the handle TABLE is the process's and
both threads were already inside it.

**Native floor: UNCHANGED, 268 assembly and 166 C code lines** — the pair stubs
that briefly took C to 194 are gone with rider 3, so the whole unit's native
delta is zero. An object model grew by two kinds and the machine-specific code
did not move.

**Not built here, deliberately:** the Interrupt object and the userspace UART
echo proof — the next unit, and the one that turns `pick_next`'s deadlock report
into an idle loop, since an Interrupt is the first readiness that arrives from
outside the set of runnable threads.

### Unit 3 rider — an argument encoding is API (ruled Aug 16, user)

The unit first offered `create_event()` / `create_counting_event()` to keep
`EventMode`'s wire value inside kernel-internal `sosabi`. **Reversed**: the mode
is a value the CALLER chooses, so it is spelled as one —
`create_event(mode: EventMode.SaturatingSum)`, with `EventMode.Or` as a default
so the common call stays `create_event()`. A method per mode reads fine at two
and stops scaling at the next object: M3's Mapping brings an access mode AND a
memory type, and a method per combination is a matrix rather than a list.

**The line, recorded in the `sos` module docstring for Mapping to inherit: the
vDSO discipline is about OP NUMBERS, not argument encodings.** An enum whose
values a process chooses and passes as a syscall argument is public,
single-declaration API; op numbers, rights bits, `ObjType` and `FaultReason`
stay kernel-internal, which is what keeps renumbering free.

**Mechanism: Saw's imports RE-EXPORT, so one declaration serves both halves.**
`sosabi` keeps the declaration the kernel's dispatch decodes, `sos` imports it,
and a process writes `import sos.{EventMode}` — no redeclaration, no new module,
no kernel change. Two things learned by probing rather than assuming, both worth
having on record:

- **The re-export is INDISCRIMINATE.** Everything a module imports is reachable
  through it, op numbers included — and `sosabi` is on a process's module path
  as a transitive dependency anyway. So the split above is held by what
  userspace is TOLD to write, not by a wall. That was already true before this
  rule; the rule only decides which side each family sits on.
- **The enum cannot MOVE into `sos` instead.** A Saw import compiles the whole
  module into the unit, and `sos` and the kernel HAL export the two runtime
  hooks under the same C names for their two sides — so a kernel importing `sos`
  is a duplicate-`@export` error on `sos_rt_write` and `sos_rt_abort`, before
  any question of the undefined `sos_syscall1` it would also pull in. Probed
  directly.

### Unit 3 rider 3 — the wait answer becomes a validated copy-out (ruled Aug 16, user)

The unit's `(key, readiness)` register pair is REPLACED, and with it the two C
stubs it needed. `Waiter.Wait` now takes a buffer and a capacity on the ordinary
three-argument syscall shape, and the kernel COPIES OUT a record. **This is the
one place a ratified spec section has changed** — §2.2 carries the amendment and
cites this rider.

**Why the record wins.** The KEY is universal and the PAYLOAD is not: a
Channel's readiness is not a Timer's is not an Event's. A register pair would
have had to be the union of every waitable's answer forever, and the register
file is the one thing that cannot grow. So the shape is `key` word, `tag` word,
payload words — the tag a raw-backed enum with an extensible space, the sizes
and offsets public constants, one declaration serving the kernel that writes it
and the `sos` module that decodes it (the re-export convention rider 2
established, now covering records as well as argument enums).

**The Event's payload is its accumulated word, and it is a SNAPSHOT.** That
makes the common wait one syscall instead of two, and the `event_wake`
transcript shows the seam honestly: the worker signals five times, the FIRST
wakes the sleeper, so the record reads 1 and the following `receive` drains 5.
Neither is wrong — the record says what the wait was woken FOR, `receive` says
what has accumulated — and filling the record at resume instead would move a
user-memory write away from the wake and buy nothing, since the count can go
stale again before the thread reads it.

**COPY-OUT IS A FUNNEL BUILT ON ITS FIRST USE (obligation 1)**, because the
second use is already specified: §2.1's Channel `receive` is this operation with
a bigger length. `copy_out(p, dst, src, bytes)` is the ONLY place the kernel
dereferences a user address; it refuses a destination that is not word-aligned
(`BadAlign`) or not inside the process's writable window (`BadBuffer`), and both
are FAULTS — a process linked its own image and knows where its data is. Its
docstring names its consumers: Waiter now, the Interrupt object's result next,
M3's Channel body after. Two things follow from having exactly one door: the
per-address-space mapping switch M3 needs is a one-place change, and the
writable window is one field pair on the process rather than a check scattered
per op.

**The window comes from the loader**, which now records where a process's
writable grants are (its `Write`-flagged segments, plus the stack it is given).
The four harness kernels that grant a payload its own bytes read+execute pass
NOTHING and get an empty window — which is the honest answer for a process with
nowhere to receive a record, and is what makes the two fault payloads need no
particular address.

**AN UNKNOWN TAG PANICS** in the userspace decode rather than becoming an `Err`.
The kernel wrote that tag, out of the same declaration the decode reads, so a
tag userspace cannot name is a kernel/`sos` skew — a bug in the pair, not data
from an untrusted source — and never-hide says it should be loud.

**C delta: NET NEGATIVE, back to the pre-pair floor.** `sos_syscall1_pair` is
deleted from both profiles and `syscall_return_pair` from both kernel HALs; C
goes 194 -> 166 and assembly stays 268, so the whole unit's native delta is
zero. The seam has the shape it had in M1, and an op whose answer does not fit a
word grows a buffer argument rather than a register.

**Proof: 27 cases per machine, 54 total.** The two new ones are the funnel's two
rejection rows — a destination outside the writable window and one that is not
word-aligned — each a payload, because the typed `Waiter.wait` supplies its own
buffer out of its frame and never lets a caller name one. They are told apart by
the CHECK ORDER (alignment first), which is stated in `copy_out_check` and
asserted by the pair.

**One finding: DF-178f** — `sizeof<T>()` does not fold in a static initializer,
though design 186's own diagnostic lists it among the things that do. Filed, and
routed around in the open: the two sizes that wanted it are small functions with
the reason written beside them.

### Unit 3 rider 4 — `remove` names the attachment, not the waitable (ruled Aug 16, user)

`WaiterOp.Remove` takes the KEY. Detaching edits the WAITER's own attachment
table and never touches the waitable, so the authority it spends is the Waiter
handle the dispatch already validated plus the key — asking for the waitable's
handle as well would be asking for authority the operation does not use, and the
old form spent an `EventRight.Wait` it had no business spending. It is also the
only form that survives M3: once handles can be CLOSED, a closed waitable's
stale attachment has no handle left to name it with, and a handle-based remove
could never reach it. §2.2 carries the amendment.

**The invariant it forces, now stated: KEYS ARE UNIQUE PER WAITER.** `Add` with
a key the Waiter already uses is a FAULT (`DuplicateKey`). It has to be refused
rather than tolerated because a duplicate makes two questions ambiguous at once
— which attachment a `remove` names, and which one a wait answer came from — and
the attacher chose both keys, so it is caller-checkable in the strongest sense.
The attachment set is walked BY KEY for both operations, which is the same walk
and therefore one place to be wrong.

**Its test is the one fault case in this unit written in ordinary Saw**, and
that is worth noticing rather than incidental. The others are hand-written
payloads because the typed layer makes their mistakes unspellable — you cannot
name a handle you were not given, and `wait` supplies its own buffer. A
duplicate key is different: both Events are real and both keys are the caller's
own words, so no type can catch it and the check must be the kernel's. The test
therefore lives at the altitude a real process would hit it from.

**A harness defect surfaced with it and was fixed rather than worked around.**
The arch-free seam scan is substring-based, and `plic` is inside "duplicate" —
also "explicit", "implicit", "complicated", "replica". A check that exists to
police the kernel's DEPENDENCIES was about to dictate the kernel's VOCABULARY,
which is backwards. The scan now suppresses a hit only when the token has a
letter on BOTH sides: real leaks are prefixes or suffixes of longer identifiers
(`mtimecmp`, `csrw`, `PLIC_BASE`, `gicd_ctlr`, `cntfrq_el0`) and all still fire,
while a token buried mid-word is English. Verified against eighteen real leak
spellings and seven English sentences before it was trusted.

### Why `Waiter.add` is OVERLOADED per waitable kind, and not generic

Recorded because the alternative is the obvious one and the reason it is refused
is a LANGUAGE fact rather than a taste (ruled Aug 16, user).

The tempting shape is `add<T: Waitable>(_ w: &T, key: UInt)` over a public
`Waitable` trait. It cannot be that, because the requirement such a trait needs
is one that PRODUCES A HANDLE — that is the only thing `add` wants from its
argument — and under Saw's ORPHAN RULE a public trait in the `sos` module can be
conformed by any module that owns a type. So any process could declare its own
`struct Forged`, conform it, and return whatever handle word it liked from the
requirement: a forged-handle door straight through the guarantee the typed layer
exists to provide. Saw has no SEALED traits, so there is no way to publish the
trait and withhold the ability to conform to it.

Overloads have no such door and cost nothing here, because **kinds are a LIST**:
Event now; Channel, Timer, Interrupt and ReplyHandle as they land; a method
each. That is the same list-vs-matrix rule that pushed `create_event`'s MODE the
other way — modes multiply (M3's Mapping has access × memory type), so a mode is
a value, while kinds enumerate, so kinds are overloads.

**The future shape, when it is earned:** a WITNESS-SEALED trait — the
requirement returns a module-private type with no public constructor, so only
`sos` can satisfy it and the trait is effectively sealed without language
support. The trigger is a real consumer that is generic over waitables, which is
M3 wait-library territory (a `HandlerGroup`-shaped dispatcher, §10). At that
point the language question worth ruling is whether Saw wants sealed traits
outright, since the witness idiom is a workaround for their absence and would
read better spelled.

## UNIT 4 — the Interrupt object + the userspace UART echo (BUILT Aug 16, branch PARKED)

D6 as ratified, plus the five finale constraints below. Both profiles, 32
harness cases each, 64 total. **The milestone's proof:**

```
SOS M1: kernel up on riscv32 (QEMU virt)
SOS: root image ok segments=0x00000003 entry=0x8020052a prio=0x01010100
SOS: console handover
SOS echo: driver up
Zq7#
SOS echo: done 4 bytes on line 10 after 4 wakes
```

```
SOS M1: kernel up on arm64 (QEMU virt)
SOS: root image ok segments=0x0000000000000003 entry=0x00000000402005f0 prio=0x0000000001010100
SOS: console handover
SOS echo: driver up
Zq7#
SOS echo: done 4 bytes on line 33 after 4 wakes
```

The harness types those four bytes AT the emulator's serial port and they come
back out of it, echoed by a PROCESS. There is no driver in the kernel: its whole
part is that it granted the register window the image declared, routed the line,
and woke the thread, and none of those knows what a UART is.

**THE SECOND WAITABLE KIND MOVED THE ATTACHMENT OUT OF THE WAITABLE.** Unit 3
kept `waiter`/`key`/`link` on the Event, which is right at one kind and becomes a
matrix at two: a Waiter's set has to be ONE list, because a wait scans it once
and a `Remove` walks it once, so per-kind lists would have made rider 4's "the
same walk, and therefore one place to be wrong" false at exactly two. An
attachment is now a third object — (waiter, kind, target, key) — and each
waitable keeps one back pointer, which is all the SIGNAL side ever asks. What
varies per kind is FOUR SMALL FUNCTIONS IN ONE PLACE, each exhaustive over a
`WaitableKind` enum only `waitable_slot` can produce: who is watching me, set
who is watching me, am I ready, what does a record say. A fifth waitable fails
all four to compile; a fifth non-waitable object kind costs them nothing.

**"NOTHING RUNNABLE" STOPPED MEANING DEADLOCK**, which unit 3 predicted and this
unit had to answer. An Interrupt is the first readiness that arrives from
outside the set of runnable threads, so the same state now has two readings: it
is IDLE if any line is bound and the ratified deadlock report otherwise. The
rule is deliberately conservative — the kernel does not try to prove that a
specific blocked thread is reachable by a specific line, because a wrong answer
there only makes a hang slower to diagnose, and it never calls a real wait a
deadlock.

**THE IDLE PATH POLLS RATHER THAN TAKING THE TRAP, and that is what keeps D2
untouched.** Interrupts are taken from user mode only, enforced by the machines;
the kernel idles with them masked, so a line that arrives then is PENDING rather
than delivered. `wait_for_irq` parks the core until one is pending (both
machines wake from `wfi` whether or not the current level would take the
interrupt), `irq_poll` asks the controller directly, and the same `deliver_line`
services it either way. So the kernel still never takes an interrupt in kernel
mode, and there is one service path with two ways in rather than two paths.

**Finale constraint 1-3, the DEVICE GRANT.** `[sos.<triple>] device-window =
"0x10000000"` in the driver package's manifest becomes a `SegFlag.Device` record
in its `sosimg` — a segment with no bytes whose `mem_len` is the window's size —
and the loader installs it through a new `hal.prot_device` instead of copying
it. THE IMAGE DECLARES AND THE BOARD AUTHORIZES: each HAL publishes one window
(the console's page) and an image naming anything else is a refused image, which
is the strictest honest answer for M2 and the thing §2.5's Mapping replaces. The
migration case is written down at the check.

riscv32 spends ONE page-aligned NAPOT entry rather than a TOR pair — the DF-178b
lesson applied literally (a byte-tight device region puts every register access
on the emulator's slow path) and what keeps the four-region budget intact now
that an image can want segments, a stack AND a window; a `static_assert` holds
that budget. arm64 had to SPLIT ITS DEVICE BLOCK: the whole space below RAM was
one 1 GiB block, and a block is not a grant unit, so granting EL0 the console's
page would have granted every peripheral beside it. The block became a level-2
table and the console's 2 MiB block a level-3 one, and the page carries
`Device-nGnRE` with both no-execute bits — never Normal-cacheable, which is a
correctness property and not a performance one.

**Finale constraint 4, the HANDOVER PROTOCOL, is now spec §9a.** `start_process`
writes one marker — `SOS: console handover` — as the last thing the kernel says
on its own initiative; after it the process owns the device and the kernel
writes only on a diagnostic path. Stated on `kcore`'s `Console` with the DEVICE
half in each HAL's console section: what a second writer costs (a kernel byte
lands BETWEEN two of the process's, never inside one, since each write is one
store behind its own poll) and what the kernel never touches (the receive side,
the interrupt-enable register, and on Profile B the control register — which is
why a driver there has to enable the device itself).

**Finale constraint 5** is the `WaitPayload.Interrupt(line:)` variant through
unit 3's copy-out funnel. There is no fire COUNT because §9's mask-on-fire makes
it always one; the LINE is what a dispatcher parked on several needs and cannot
get any other way. Both existing Event programs had to answer for the new
variant, which is the extensible tag space working as designed.

**`irq_raise_selftest_line` STAYS**, and unit 1 expected it not to. The role it
was built for is genuinely covered — the echo driver takes a real line round the
whole claim / mask / complete cycle on both profiles — but two claims are
reachable only through it, and both are the KERNEL's rather than a driver's: a
line raised in kernel mode and serviced only after entry to user mode is D2 for
a DEVICE line (where the timer case makes the same claim for a timer), and a
line bound to no Interrupt object is the one path where `on_external_irq`
reports and leaves the line masked. Scaffolding that became a test of the
kernel's own behaviour.

**THE HARNESS DELAY IS THE TEST.** Handed the whole string at once, the emulator
buffers it before the driver exists and every byte is already waiting — which
proves the echo and NOT the interrupt path, since a driver that merely polled
would pass. Typed one byte at a time behind a delay, the driver must PARK, which
leaves the kernel with nothing runnable, which is what makes it wait for a wake
from outside the set of runnable threads: one wake per byte, and the whole
ladder under test. Both transcripts above read `after 4 wakes` for that reason,
and the count is reported rather than asserted, since it is the host's timing.

**Two packages, one per DEVICE**, named for the chip rather than the machine: a
driver IS its device, and the two profiles happen to have different UARTs. The
same 16550 would want the same file on a different architecture, which is the
honest axis. It cost one real bug worth recording: **the PL011's receive
interrupt is a LATCH**, set when the FIFO level crosses the trigger rather than
read off the FIFO, so the obvious bring-up sequence (enable, clear stale status,
unmask) destroys the only edge an already-waiting byte will ever produce — the
driver hung holding a byte. The fix is not to clear a device this driver owns
from reset. The 16550 derives its status from the receiver and forgives the same
sequence, which is exactly the kind of difference a per-device driver exists to
hold.

**Two fault cases, both ROOT SERVERS**, which is rider 4's distinction earning
its second use: a LINE NUMBER and an ACK are ordinary things a Saw driver
writes, so no type can catch either and the check has to be the kernel's — which
puts the test at the altitude a real driver would hit it from. A line the board
does not have is `BadArg`; an ack with no fire outstanding is `BadState`, and it
is a fault rather than a tolerated no-op because the only way to learn about a
fire is to be told, so an extra ack is a servicer that will ack the NEXT fire
without servicing it.

**And a third case for the branch that did NOT change.** Making "nothing
runnable" conditional left a rule with two answers and one test, which is a rule
that can lose its other answer silently — widen the idle condition by accident
and every real deadlock becomes a harness timeout with nothing to read. So a
process that parks on an Event nobody will signal, having bound no line, is
pinned to the ratified report. It was never covered before this unit either, and
`event_basics` relies on it out loud: a wait there that stopped answering
immediately parks the only thread in the system, and this report is what makes
that fail in microseconds rather than at the emulator's timeout.

**Native floor: +1 C line per profile, assembly unchanged.**
`sos_wait_for_irq` — `wfi` is an instruction and there is no Saw spelling for
one. Everything else the unit added is Saw: the NAPOT encoding, two page tables
and a memory-attribute index, the line validation, the poll, and both drivers.

**Not built here, deliberately:** §9's combined `wait(ack:)` form, which is
syscall-halving and not correctness (the echo runs the two calls separately and
the transcript does not notice); a second bindable device, which needs M3's
device pool root before the board's one-window whitelist stops being the honest
answer; and `HandlerGroup` (§10), which is userspace runtime work rather than
kernel surface.

## Explicitly out (M3+ candidates)

Channels + ReplyHandle IPC; MemoryObject/Mapping + multi-process loading;
SMP + IntrSpinLock; priorities; FP in userspace; the vDSO true-mapping
upgrade; big-endian anything.

## M3 memory notes (Aug 15 conversation, user + lead — seed for the M3 sketch)

PMP facts that shape MemoryObject/Mapping, established in conversation
after unit 1's DF-178b work:

- **The kernel costs ZERO slots.** M-mode is unchecked by PMP (absent
  L-bit entries) — exception entry always reaches kernel code/stacks —
  and S/U default-deny protects kernel memory with no entry at all.
  L-bit hardening (kernel code RO even from M-mode) is an optional 1-2
  entries, not architecture.
- **Slots are per-hart, reloaded at context switch** — the 16-entry
  budget is the RUNNING process's concurrent-region cap, not a
  system-wide partition. Switch cost: a dozen CSR writes.
- **The region model** (user-proposed, fits): contiguous image layout
  code|rodata|rw, TOR-chained = ~4 entries per process (base + 3);
  stack at the RW edge gets overflow faults free from default-deny;
  +1 NAPOT entry per granted device window (driver processes only).
- **Shared memory needs no MMU** under switch-reload: 1 entry in each
  sharer's loaded set. The real PMP limit is NO TRANSLATION — one
  physical address for every sharer — so Mapping ADDRESSES ARE
  KERNEL-ASSIGNED (returned from map, never process-chosen).
- **Portable contract = the PMP floor**: page-granular (G may be
  page-size on real silicon — byte-tight grants are not portable),
  identity-mapped, contiguous objects. Translation hardware (arm64's
  MMU today, an ESP32-P4-style minimal MMU someday) is a HAL bonus
  behind the same seam, never the contract.
- **Open for the M3 sketch**: (a) heap growth under contiguity —
  per-process arenas sized at spawn vs grow-by-relocation;
  (b) the documented per-process mapping cap (candidate 8), with
  over-cap map() a FAULT per the ratified faults rule
  (caller-checkable against a documented cap).

## FINALE UNIT CONSTRAINTS (ruled in conversation, user, Aug 16 — the
## Interrupt + userspace-UART-echo dispatch folds these in)

1. **The UART MMIO grant is a STATIC BOOT-TIME DEVICE GRANT** — M1's
   grant machinery extended by one declarative device window (named in
   the root's image/boot config, not hardcoded in kernel logic), and
   explicitly marked as the placeholder M3's Mapping retires (device
   memory then = a MemoryObject with device attributes; this grant is
   its migration case).
2. **riscv32**: one PAGE-ALIGNED NAPOT PMP entry over the UART page
   (virt NS16550, 0x1000_0000) — the DF-178b lesson applies literally
   (a byte-tight device grant puts every register access on QEMU's
   slow path).
3. **arm64**: the EL0 mapping carries DEVICE memory attributes (nGnRE),
   never Normal-cacheable — a cacheable PL011 mapping is a correctness
   bug — plus device-memory access-size discipline (32-bit registers).
4. **UART ownership handover protocol**: both machines have ONE console
   UART and the kernel currently owns it (debug_print board sink).
   After boot handover the kernel goes QUIET on the device — root
   drives it — and the kernel reclaims only on panic, where
   interleaving is acceptable because the test already failed. State
   the protocol in the unit; unstated it is a flaky-test mystery.
5. The Interrupt object consumes unit 3's copy-out funnel with its own
   WaitPayload variant, per the wait() redesign.

## M3 TIME NOTES (Aug 16 conversation, user + lead — Clock/Timer as M3's
## proposed FIRST unit)

No Timer object exists (spec §2 ratifies the row; M2's "timers" were the
kernel tick). M2 made it cheap: the per-arch timer seam, the tick hook,
and the waitable pattern (slot arm + WaitPayload variant + Waiter.add
overload) are all in place. USER-RULED API SHAPE:

- `System.get_clock(type: ClockType.Monotonic) -> Clock` — time is a
  GRANTED CAPABILITY. The sleeper strength: a virtual Clock handed to a
  process gives virtual now() and virtual timers with zero code change —
  design 214's deterministic-simulation seam at the kernel API layer;
  the hosted executor's virtual-clock branch and this are twins.
- `clock.create_timer() -> Timer` — the timer BINDS its clock/domain at
  creation (Boot/Realtime later, each with its own step semantics);
  ClockType is a public single-declaration enum per the
  create_event(mode:) convention.
- `clock.now() -> ns` (UInt64) — v1 a syscall via the COPY-OUT record
  (64-bit on rv32 is two words); the vDSO true-mapping upgrade (already
  on the M3+ list) is the named syscall-free future.
- `timer.arm(relative_ns, interval:)` — kernel-side drift-free re-arm
  (next = previous_fire + interval, the timerfd model); missed ticks
  COALESCE into a fire count delivered as WaitPayload.Timer(fires:) —
  the SaturatingSum machinery reused. Timer is directly waitable
  (waitable_slot arm) per the spec row.
- ABI wrinkle that seeds the next funnel: arm()'s 64-bit inputs exceed
  rv32 argument registers → a validated COPY-IN record — the copy-out
  funnel's mirror twin, which M3 IPC send needs anyway; Timer.arm is
  its small first consumer exactly as wait() was for copy-out.
- Also the process-sleep story: today a process cannot sleep at all
  (wait blocks indefinitely); Timer closes that gap, which is why it
  heads the M3 ladder ahead of IPC (whose select shape needs timeout
  regardless — design 214's standing question).

## M3 device-grant + memory-surface rulings (Aug 16 conversation, user +
## lead — seed for the M3 sketch; supersedes the M2 grant and narrows the
## memory notes above)

THE MODEL: a "device" is POLICY IN ROOT, never a kernel type. The kernel
offers primitives; root's board table (name -> windows + lines, one row
per arch) and the child manifest (`devices = ["uart"]` — NAMES, so an
image carries no physical address) decide who gets what. Devices are
messy (multi-BAR, shared lines, MSI) in ways that are DATA problems in
root's table and would be MECHANISM problems in a kernel bundle; seL4
(caps separate, capDL bundles) and Genode (bundles in a userspace
platform driver) both landed here. A kernel-visible bundle earns itself
only at delegation (a PCI bus driver granting subsets onward) — not v1.

THE OP SURFACE — three System ops, each behind its own SystemRight:

- `system.bind_iomem(base, len) -> IoMemory` — BIND, not create: an
  assertion of the right to use an immutable system resource, the same
  verb and the same shape as the line bind. Checks: page-aligned base
  and len; inside the board's MMIO ranges (RAM is never bindable this
  way — a board fact the HAL already owns); NO OVERLAP with an existing
  binding (`RegionBound`, LineBound's sibling — two writers on one
  register page is two acks for one fire in memory clothing). Unbind is
  handle death; the resource is eternal.
- `system.bind_interrupt(line) -> Interrupt` — MOVES HOME at M3: the
  line is a fact about the MACHINE, and in the give-model the binding
  authority was never about the caller's process. `ProcessOp.
  BindInterrupt` (op 6) and `ProcessRight.BindInterrupt` (bit 8192)
  RETIRE rather than migrate; the M2 rename carried the verb, the move
  carries it further. LineBound stays as the kernel backstop.
- `system.alloc_memory(size, attr) -> Memory` — ALLOC, not bind: RAM is
  fungible, so this one genuinely creates. `attr` is RW | RO, never X.

STRIPPED-BY-CONSTRUCTION INVARIANT: every SystemRight above — plus
`Shutdown` (a UART driver that can power off the machine is the same
category of wrong) — is stripped from every derived System handle. A
child keeps DebugPrint + SelfProcess. The guarantee rests on exactly two
things §3 already commits to: attenuation is monotonic, and handles
never duplicate — so THE FULL-RIGHTS SYSTEM HANDLE EXISTS ONCE, IN
ROOT, FOREVER, and the whole bind/alloc surface is unreachable from
everything below root, transitively, by construction.

THE TYPES (names ruled): `Memory`, `IoMemory`, `MemoryMapping` — no
`Object` suffix (it carried nothing). `IoMemory` has ONE op, `Map`, and
its mappings carry device attributes BY CONSTRUCTION (nGnRE, PXN|UXN —
there is nothing to ask for, so nothing to ask for wrongly). §2.3's
"MemoryObject" prose is superseded by this section at M3.

MAP: into the CALLER's own address space only (v1 — cross-space mapping
is exactly the machinery the give-model avoids), and THE KERNEL ASSIGNS
THE ADDRESS (returned, never chosen — already forced by the PMP
no-translation note above). Returns a `MemoryMapping` with an explicit
`unmap()`.

DROP SEMANTICS (ruled, and deliberately inverted from RAII):

- Dropping a `Memory`/`IoMemory` handle does NOT unmap — it makes the
  region UNMAPPABLE from then on. The grant and the mapping have
  separate lifetimes.
- Dropping a `MemoryMapping` SEALS it: the mapping persists and can
  never be unmapped (bounded by the process's life — address-space
  teardown reclaims everything regardless). Rationale: under
  DETERMINISTIC destruction, unmap-on-drop turns an accidentally-scoped
  binding into a use-after-unmap fault at a surprising line; the
  destructive operation must be explicit-only. Drop-means-sealed makes
  the leak-shaped mistake SAFE instead of latent.

DERIVED PROPERTY worth its own line: SHARED MEMORY WITHOUT DUPLICATE
HANDLES. Root allocs, maps, `give`s the Memory handle onward; the child
maps too. Both mappings persist; at every instant exactly ONE Memory
handle exists — a token that travels, leaving mappings behind where it
has been. §3's no-duplicate rule survives shared memory untouched. Do
not "fix" this later by adding duplication.

HANDLE DELIVERY: `give` is a launch-time kernel op moving a handle into
the child's table (MOVE, never copy — after it, root cannot ack the
child's interrupt or touch its registers, which is the isolation working
in both directions). The child learns WHAT it holds via
`system.boot_handles(&buffer)` — a copy-out op on the wait()-funnel
precedent, answering tagged records {kind: Window/Interrupt/Memory,
ordinal: manifest order, handle}. Names never cross the kernel;
`_start(boot_handle)` stays one register wide forever. (The
zero-mechanism alternative — grant order IS manifest order, pure
convention — was considered and declined: it fails silently on
disagreement where the record fails checkably.)

TWO ALLOCATION CLASSES, TWO ACCOUNTING ANSWERS (ruled Aug 16, second
round — the first round's blanket no-quota ruling was missing this
distinction):

- STATIC ROOT-MINTED GRANTS (IoMemory, Interrupt bindings, launch-time
  Memory): NO accounting — these are defined at build time, the config
  IS the budget, the fixed slabs ARE the backstop.
- DYNAMIC SELF-SERVICE CREATION (events, waiters, channels, threads,
  heap, mappings): a PER-PROCESS QUOTA, because this is exactly where a
  bad actor meets a shared slab. Quotas restore the isolation global
  slabs give up.

THE QUOTA TABLE: per process, one row per counted kind, set by ROOT at
launch from the child's manifest (the same policy home as devices),
IMMUTABLE after start (v1 — mutable quotas need raise/lower ops nobody
has asked for). Root is the explicitly-unlimited case. Config spelling:
ABSENT = a small documented default; unlimited must be WRITTEN — the
safe default is the bounded one. Charge on create, credit on destroy;
process death reclaims everything, so the table zeroes itself.

EXHAUSTION IS A STATUS, NOT A FAULT — decided against the letter of the
faults rule, deliberately: quota exhaustion is technically
caller-checkable (a process can count its own allocations), but
faulting a server for hitting its event cap converts resource pressure
into termination, the opposite of what quotas are for. Precedent
agrees: alloc_event already answers slab exhaustion with `NoResource`.
Add a DISTINCT `QuotaExceeded` beside it — "your budget" vs "system
pressure" is a diagnostic distinction worth a status, and a process at
its cap degrades gracefully (refuses a connection) instead of dying.

HARD + SOFT: hard refuses with `QuotaExceeded`. Soft is NOT the rlimit
reading (self-raisable-up-to-hard is pointless in a capability system —
a process that could raise it might as well have been given the hard
number); soft = a ROOT-NOTIFICATION THRESHOLD — crossing it signals an
Event root attached at launch, so the launcher learns a child is
nearing its cap and can log, restart, or widen at next launch.
Telemetry with zero new mechanism. v1 may ship hard-only with soft
flagged; the Event wiring is the natural extension, not a prerequisite.

TWO UNIFICATIONS THIS BUYS: the per-process MAPPING cap the PMP notes
demanded (candidate 8 — the 16-slot budget) stops being a special case
and becomes a quota row like any other; and spec §5.7's deferred
"quota-gated free creation" object-model note now has its seed — this
ruling IS that brief starting.

CHARGING (ruled, third round): ALWAYS THE CREATOR, NEVER TRANSFERRED.
P1 creates a Memory, maps it, gives it to P2, P2 maps: P1 Memory=1
Mapping=1, P2 Memory=0 Mapping=1. Three things make this the right
rule, not just the simple one:

- THE MAPPING CHARGE MATCHES THE HARDWARE: a mapping's real cost is a
  slot in the MAPPER's PMP set (entries reload per-process), so
  charging the mapper charges the process that physically consumes the
  resource. Memory's slab entry charges its creator; each mapping
  charges its mapper. The charge lands where the kernel resource sits.
- DELEGATION IS NOT LAUNDERING: the classic charge-follows-handle
  argument ("P2 exceeds its budget via P1 creating on its behalf")
  describes delegation — P1 spends its OWN budget by choice, and the
  invariant that matters survives: every process's spend is bounded by
  its own table, so total slab pressure is bounded by the sum of
  quotas. A broker process's big Memory quota IS its role description;
  a P1 tricked into creating for others is a confused-deputy bug in P1,
  not a kernel accounting failure.
- THE ORPHAN RULE (the one edge this creates): creator dies while its
  object is still held elsewhere — the charge is WRITTEN OFF with the
  dead table, the object persists in the slab until its own handles and
  mappings die, and the slab stays the global backstop. A quota bounds
  LIVE CREATIONS BY A LIVE PROCESS; the slab is the truth. (Stated so
  nobody writes naive credit-on-destroy bookkeeping that decrements a
  dead process's row.)

OPEN FLAGS for the scoping session: (a) does a CHILD keep AllocMemory,
or is it root-only like the binds? (lean: stripped — root allocs and
gives; a child that needs a buffer is given one, and heap stays the
child's quota-counted allocator); (b) may one Memory map twice in one
process? (lean: allow, one MemoryMapping each); (c) boot_handles record
layout + tag values; (d) op/right numbering; (e) spec §5.7's "become
real objects in M3" line is AMENDED by this ruling (rights-gated ops,
boot set stays one handle wide) — spec edit owed at M3, not before;
(The former charge-on-transfer, counted-kinds, soft-limit and DMA
flags are all RULED — see CHARGING above and the FOURTH ROUND below.)

FOURTH ROUND (Aug 16): THE FAULT LINE SHARPENED, SOFT LIMITS OUT, AND
DMA GETS AN ARCHITECTURE.

- THE FAULT-VS-STATUS LINE, RESTATED (user): a fault exists to punish
  BROKEN CODE quickly and loudly — a programmer error is never
  silently hidden (`let _ = channel.send(<bad data>)` FAULTS; a
  discard cannot swallow a termination). An operation with a RUNTIME,
  DYNAMIC aspect — allocation, quota'd creation — returns an ERROR:
  correct code legitimately meets exhaustion. This resolves the
  candidate-8 tension: over-cap map() is a dynamic resource condition
  and answers `QuotaExceeded`, AMENDING the Aug-15 memory note's
  "over-cap map() is a FAULT" phrasing above. The v1 counted-kinds
  list stands as written (events, waiters, threads, mappings, heap
  pages; channels when they exist) — grow it as needed.
- SOFT LIMITS ARE OUT OF v1 (ruled): hard limits only, answering the
  quota error. The Event-wiring sketch above survives as the future
  shape, to land together with root's first management behaviors —
  a warning nobody can act on is telemetry theater.
- DMA CONFINEMENT (ruled) — an architecture, not just a gap note: a
  USERSPACE IOMMU DRIVER manages device-memory bindings. It owns the
  IOMMU hardware (a device like any other: window + line, granted by
  root), and a DMA driver SENDS IT MEMORY TO USE — Memory handles over
  IPC, creator-pays charging composing unchanged. TWO PREREQUISITES
  this names for the ladder:
  (1) PROCESS-DEATH NOTIFICATIONS — a waitable that fires when a
      process dies (root's supervision wants this anyway) — so the
      IOMMU driver can stop the dead process's device operations and
      release its memory intelligently rather than by teardown.
  (2) CRITICAL PROCESSES — the wrinkle is the IOMMU driver ITSELF
      dying: its held memory would release while the device keeps
      writing blindly. The answer is FAIL-STOP: a process marked
      CRITICAL (by root, at launch) that exits FOR ANY REASON faults
      the SYSTEM. ORDERING CONSTRAINT: the critical check runs AT THE
      TOP of process teardown, BEFORE any resource release — the halt
      must preempt the free, or the window it exists to close reopens
      inside it. (Zircon's critical processes are the precedent, same
      name. Restart-style supervision a la Minix is exactly the
      blind-DMA hazard for this driver class — fail-stop is right.)
  Until the IOMMU objects exist, DMA drivers are TCB members and the
  spec §2.5 note says so; the virt-board consoles are non-DMA, so M2/
  M3 scope stays honest.
