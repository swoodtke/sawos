# Design 232 — SOS M3 SKETCH (agenda for the scoping session)

**Status: SKETCH.** This document proposes M3's SCOPE for the user
conversation — the decisions agenda at the bottom is the session, worked
item by item. What it does NOT reopen: the Aug-16 seed rounds in
designs/178 ("M3 TIME NOTES", "M3 device-grant + memory-surface
rulings", rounds one through four) are RULINGS, carried in here by
reference and lifted only where slicing needs them. M2 is integrated
and verified on main (Interrupt + userspace UART echo, both arches);
the M2 spec-recap unit (§11 refresh) is still owed and is proposed
below as unit 0.

## What M3 has on its plate (the full candidate surface)

From spec §2's unratified rows, 178's "Explicitly out (M3+ candidates)"
list, and the seed rulings:

1. **Clock/Timer** — API shape user-ruled (178 time notes): granted
   Clock capability, clock-bound Timer, drift-free interval re-arm,
   coalesced fires via WaitPayload.Timer(fires:), the COPY-IN funnel
   (arm()'s 64-bit inputs exceed rv32 registers). Closes the
   process-sleep gap.
2. **CreateProcess + multi-process loading** — a second address space,
   the launch flow. Everything the device-grant rulings assume (root
   LAUNCHES children) rests on this.
3. **The device grant + memory surface** — fully ruled (178 rounds 1-4):
   `bind_iomem`/`bind_interrupt`/`alloc_memory` as rights-gated System
   ops, stripped from children by construction; Memory / IoMemory /
   MemoryMapping with inverted drop semantics; `give` + `boot_handles`;
   manifest device NAMES over root's board table. Retires the M2
   loader device-window grant (pinned as its migration case) and
   ProcessOp.BindInterrupt (op 6, bit 8192).
4. **Quotas** — ruled: per-process, dynamic-creation kinds only, hard
   limits only, `QuotaExceeded` status, creator pays, orphan written
   off.
5. **Pipes + PipeReplyHandle IPC** (§2.1, ratified semantics).
6. **Process-death notifications** — new from round 4; two named
   consumers (root supervision now, the M4+ IOMMU driver later).
7. **Priorities / §7 band map, SMP + IntrSpinLock, FP in userspace,
   vDSO true-mapping** — the standing M3+ tail.

Items 1-4 form a coherent milestone; 5-7 do not fit beside them.

## The three candidate slicings

**Option A — the multiprocess kernel (RECOMMENDED).** Units 0-7 below:
Clock/Timer, then CreateProcess + the launch flow, then the ruled
device/memory surface, then quotas. IPC waits for M4. The milestone
proof is the natural sequel to M2's money shot: **the same UART echo
driver, launched as a CHILD of root from config** — root reads
`devices = ["uart"]`, binds the window and the line, gives both to a
process it created, and the child maps its own registers and echoes.
No driver in the kernel (M2's proof) and now no driver in root either.
A second, cheaper proof rides along free: two processes sharing memory
through `give` — the token-travels property demonstrated live.

**Option B — IPC first.** Pipes + PipeReplyHandle over the M2
single-process kernel; CreateProcess deferred. Rejected shape: with one
process there are no interesting peers (root rendezvousing with itself),
so the tests prove plumbing rather than IPC — and the device-grant
rulings, which are hot and complete, would sit unbuilt for a milestone.

**Option C — A plus pipes.** Everything at once. M2's surface took
the full ladder to land well; A alone is already M2-sized. Rejected on
size.

Why A: the seed rulings ARE most of its design work — the scoping
session inherits four ruled rounds and spends itself on the genuinely
open items instead; Timer heads the ladder per the time notes (process
sleep, and IPC's select-with-timeout shape needs it REGARDLESS, so it
is M4-enabling too); and CreateProcess is the load-bearing prerequisite
everything ruled this week quietly assumes.

## Standing pins carried into M3

1. **D2's tripwire FIRES in M3, and the answer is RULED (user, Aug
   16): M3 TAKES KERNEL INTERRUPTIBILITY EARLY.** "Syscall latency
   bounds interrupt latency — re-examine when any M3 syscall grows a
   loop" — CreateProcess's image copy IS a loop, and so is the arm()
   copy-in validation. Ruling: add interruptibility to (long) kernel
   operations NOW, while the kernel surface is at its smallest — this
   is a bug class to iron out early, not one to meet at SMP scale.
   SMP itself WAITS FOR CHANNELS (meaningful concurrent actions to
   exercise it with). The MECHANISM is the implementing brief's one
   big open: (a) explicit PREEMPTION POINTS — interrupts stay masked
   in kernel mode except at named points inside long loops where no
   IRQ-shared invariant is in flight; the interrupt is taken,
   serviced, and the op resumes. No IntrSpinLock needed, because a
   point IS the assertion that state is consistent — auditable in a
   kernel this size, and the placement rule ("no in-flight invariant
   over state the IRQ path touches") is a reviewable sentence per
   site. (b) full kernel preemption — interrupts unmasked throughout,
   IntrSpinLock around every IRQ-shared structure. LEAN: (a) for M3;
   (b) is what SMP forces anyway, and by then the point placements
   are a map of exactly where the locks must go — the points become
   documentation for the SMP conversion rather than waste.
2. **D1 carries**: processes stay integer-only (no FP in userspace).
3. **One core**; round-robin unchanged. The §7 band map stays a loader
   artifact (agenda item 10 confirms or moves it).
4. **Faults rule as sharpened in round 4**: programmer errors fault
   loudly; dynamic resource conditions return errors.
5. **SOS policy unchanged**: every unit per-arch-gated, branch parks
   for user review, sos_runner grows per unit.

## Proposed unit ladder (option A)

0. **M2 spec recap** — the deferred §11 refresh plus the M2 rows'
   status flips (§2 Interrupt row is BUILT, §2.5's placeholder note,
   §9a handover). Docs-only, dispatchable first and separately.
1. **Clock/Timer** per the time notes: `System.get_clock(type:)`,
   `clock.create_timer()`, `clock.now()` via copy-out, `timer.arm`
   via the NEW COPY-IN FUNNEL (the wait() copy-out's mirror twin —
   built here, consumed by IPC later), WaitPayload.Timer(fires:),
   waitable_slot arm. Files collide with the finale's wait machinery
   (waitable_slot, WaitPayload) — never parallel with another SOS
   unit touching them.
1.5. **Kernel interruptibility** (pin 1's ruling) — the mechanism
   (lean: preemption points), a synthetic long-op + selftest-line
   proof on both arches, and the placement audit of every existing
   loop (zero_bytes, the loader's segment walk, the PLIC/GIC reset
   loops). Lands BEFORE CreateProcess so the image-copy loop is born
   with its points rather than retrofitted.
2. **CreateProcess** — second address space, image load (points from
   unit 1.5 in its copy loop), teardown generalized (M2's close-all/
   free-owned per process), the idle/deadlock report generalized to N
   processes. Child image provenance per agenda item 2.
   **THE LIFECYCLE IS TWO-PHASE, INERT UNTIL STARTED (ruled Aug 16):**
   `create_process(image: Memory)` populates the address space, RECORDS
   the image's entry point and initial stack in the process slot (the
   sosimg declares both — how root itself boots), and returns a
   Process with NO thread. Root gives handles; then `process.start()`
   mints and runs the FIRST thread at the recorded entry on the
   recorded stack — root supplies neither, which is what keeps "root
   never parses" airtight. The give-before-start ORDERING is the
   soundness argument: boot_handles must answer completely at the
   child's first instruction, and the start call IS the barrier that
   makes a half-populated table unrepresentable, with no
   synchronization invented. Subsequent threads are the child's own
   CreateThread with stacks in its own memory, unchanged from M2.
   A SECOND start() is a FAULT (broken code, the sharpened line).
   start() with no System handle given is legal-but-doomed — a fully
   sandboxed compute process is a feature, not an error to detect.
   (Zircon's process_create/process_start is the precedent, including
   the bootstrap-handle-at-start convention.)
3. **The launch flow (shape ruled Aug 16; give-return AMENDED same
   day)** — `give(handle, tag:)` on the CHILD's Process handle, gated
   by the universal Transfer right: MOVES the handle into a fresh
   child-table slot and returns ONLY ITS STATUS — the child-side word
   is irrelevant to root (root can call no op through it), so nothing
   returns it. THE TAG IS THE IDENTITY, and tags are the ONLY
   cross-process vocabulary: the giver's own word handed back unread
   (the Waiter.add key precedent — the kernel is a courier, never an
   interpreter; root and child agree on meaning through config +
   manifest). A DUPLICATE tag is REFUSED at the give (fault — an
   identity naming two handles is broken config, caller-checkable,
   and it would make the record a multimap and the boot lookup below
   ambiguous). `start(boot_tag:)` completes the principle: the KERNEL
   resolves the tag to the child-table word and puts it in a0, so
   `_start(boot_handle)` is unchanged from M2 and root never sees a
   child-relative word at any point. start() with NO boot_tag puts
   the no-handle word in a0 — the sandboxed compute process stays a
   feature. **DELIVERY IS THE RETRIEVAL OP, NOT A BOOTINFO
   SECTION**: a bootinfo section written into the address space would
   freeze a struct layout in raw memory as permanent ABI — the exact
   disease the vDSO discipline exists to prevent (seL4's BootInfo
   frame is the cautionary precedent; Zircon went message-based for
   this reason) — where a copy-out record rides sysapi and evolves
   with the kernel. **THE OP IS AN ITERATOR (user, Aug 16, third
   refinement — supersedes the batch-buffer + one-shot-call
   shapes)**: `self_process()` then
   `proc.next_boot_handle() -> BootHandleRecord?` — each successful
   call hands out ONE `{tag, kind, handle}` record and CONSUMES it
   (the kernel frees that entry's bookkeeping immediately — it
   tracked tags for DELIVERY, not as a registry); EXHAUSTION IS
   `None` AND STAYS `None` — asking again is a loop's termination
   check, not broken code, so nothing here faults and nothing can be
   lost or truncated. This dissolved the whole buffer question (no
   capacity argument, no count protocol, no too-small policy) AND
   the new kernel machinery: one record per call is exactly the
   FIXED-SIZE record shape the wait() funnel already has — the
   variable-length array copy-out is never built. Per-record
   consumption is the no-stale-re-answer property at its natural
   grain: a word handed out once is never re-answered after the
   child drops or (M4) gives it onward. Records come back in give
   order (deterministic for free; tags stay the identity, nothing
   semantic rides on order), and the child asserts its drained count
   against its manifest with no protocol owed. The child's boot
   sequence is thereby literally a receive loop — the exact shape
   M4's pipe receive will have (Zircon's processargs message,
   drained one record at a time). Unchanged: `give` AFTER start() is
   REFUSED in v1 (the boot set freezes at start; dynamic transfer is
   M4 IPC's job, over pipes, to a process expecting it); the op
   lives on Process, not System (the handles are process state).
4. **Memory/IoMemory/MemoryMapping + map** — the ruled surface;
   SystemRights + stripping; manifest `devices = [names]` over root's
   board table; the M2 loader grant retired (its NAPOT/nGnRE
   discipline moves into map()'s implementation).
5. **Quotas** — the table, the counted kinds, `QuotaExceeded`,
   creator-pays, orphan write-off.
6. **The money shot** — the child echo driver from config, both
   arches; plus the shared-memory give demonstration.
7. **Spec updates** — §2.3 Memory rename, §5.7 amendment (rights-gated
   ops, boot set stays one handle wide), the DMA-TCB note (round 4),
   §11 refresh for M3.

Death notifications: PROPOSED unit 5.5 IF cheap after CreateProcess's
lifecycle work (a Process handle becomes waitable — the waitable_slot
pattern's third consumer), ELSE first unit of M4. Agenda item 9.

## THE HANDLE-LIFECYCLE RULING (user, Aug 17 — from the unit-1 review;
## adds a unit, supersedes the same-handle model unit 1 built)

**MINT-PER-CALL**: every `process_self` / `clock_get` (and every future
getter of a singleton object) MINTS A FRESH HANDLE to the existing
kernel object, carrying the kind's DEFAULT rights. No find-or-create,
no cached `self_handle` fact, no handle-table search — a handle is a
CAPABILITY INSTANCE, independently held and independently released.
THE DEEP REASON IS RAII: the same-handle model breaks NoCopy+Deinit
sysapi wrappers (two values wrapping one word double-release); mint-
per-call is what makes §4's handle-wrapper vision buildable. It also
dissolves both unit-1 review flags (the clock re-mint edge and the
self-handle give refusal) rather than patching them.

**THREE THINGS LAND TOGETHER** (each alone is broken — mint-per-call
without release is a leak by design; release without generations is
aliasing): the mint-per-call flip, an UNGATED release op, and
GENERATIONS in the kernel handle entries, with a stale-word use a
`BadHandle` FAULT (using a handle you released is broken code — the
sharpened line; table EXHAUSTION stays a status). This pulls §3's
deferred close/generations tier into M3 as **UNIT 2.75, the handle
lifecycle**, landing just before or with `give` (unit 3, which
manipulates tables anyway). Quotas (unit 5) gain a handle-count row.

**GENERATIONS ARE BEST-EFFORT, NOT GUARANTEED (user, Aug 17)**: the
top N bits of the handle word, incrementing per slot reuse and
WRAPPING on overflow — a guide, not a uniqueness guarantee. What this
trades is stated so nobody later "fixes" it into unbounded
bookkeeping: the stale-use `BadHandle` fault becomes BEST-EFFORT
DETECTION of a bug — it catches the overwhelmingly common case (a
recently-released word), and a word held across exactly 2^N reuses of
one slot aliases silently. This loses NO security whatsoever: handle
tables are PER-PROCESS, so a wrap collision can only confuse a
process with its own entries — it crosses no boundary and grants
nothing. Generations guard against bugs; the capability model guards
against attacks; the two are different jobs. The N split (index bits
vs generation bits) is the unit's to pick and document per word size.

**THE RIGHTS VOCABULARY (ruled)**: MOVE is the existing universal
`Transfer` right — no synonym minted; `give` already gates on it.
RELEASE IS UNGATED — destroying your own capability instance harms no
one, identity lives in the process slot not the handle, and gating it
on Manage would pin every handle whose kind withholds Manage (thread
handles, deliberately, and every attenuated grant — the least-trusted
holders would get the most-pinned entries). If a gate is ever wanted,
it is a NEW universal bit, never a retasking of Manage. CLOSE is an
OBJECT-PROTOCOL operation, distinct in kind from release (release =
per-instance, per-process; close = ends the protocol for everyone),
existing only on kinds whose protocol has an end-state — Pipe at
M4 (design 230's language-side close is the exact twin: Closed comes
from close() only) — with a per-kind `CloseRight` per the convention.

**THE §3 REWRITE THIS OWES** (unit 7 docs): the no-duplicate rule's
real invariant was never uniqueness — it is NO AMPLIFICATION. A mint
always carries the kind's DEFAULT set, and minting authority is
itself rights-gated (`ClockGet`, `Manage`), so attenuating a handle
you give away is meaningful exactly when the receiver lacks its own
minting authority. State this with the clock example so mint-per-call
is never read as a hole in attenuation.

**NAMING CONVENTION (user, Aug 17)**: kernel-object operations read
`object_operation` — the noun of the object the op CONCERNS leads
(`process_self`, `clock_get`, `thread_create`, `event_create`,
`waiter_create`, `interrupt_bind`, `timer_create`; future:
`process_create`, `iomem_bind`, `memory_alloc`, `boot_handle_next`).
Receiver-verb ops (`arm`, `ack`, `wait`, `signal`, `join`, `now`,
`shutdown`, ...) are already correct — the receiver IS the object.
Applied to the whole existing surface as a rider on the parked unit-1
branch (op/right enum cases mirror, numbers unchanged).

## The decisions agenda (the scoping session, in order)

1. **The slicing: RATIFIED (user, Aug 16)** — option A, the unit
   ladder as ordered (with 1.5 and 5.5 in place).
2. **Child image provenance: RULED (user, Aug 16) — the HYBRID.**
   The STITCHER appends child images to the boot blob and records a
   region table (offsets + lengths, nothing more); the KERNEL at boot
   mints one Memory per region, delivered through boot_handles tagged
   by region ordinal — it never interprets the bytes there; ROOT
   correlates ordinals to its config (which image, which child, which
   link range, devices, quotas) and calls `create_process(image:
   Memory)`; the KERNEL is the ONLY sosimg parser, at create_process,
   reusing the loader that loads root today — it re-validates
   unconditionally (root MAY parse via the imgformat library for
   diagnostics; nothing depends on it). One door from M3 static
   children through M4 dynamic loading. Two small flags for the
   CreateProcess brief: (i) a malformed image from root — fault or
   status? LEAN: STATUS (`BadImage`) — image bytes are DATA on the
   `from(raw:)` precedent (an unknown wire byte is data, never a
   trap), and M4's dynamically-fetched images make that unambiguous;
   (ii) image-region reclamation — after loading, the blob region is
   dead bytes; LEAN: v1 accepts the waste (static systems, small
   images), noted rather than engineered around.
   THE BUILD-TIME CONSTRAINT the unit designs around: under the PMP
   no-translation contract all processes share one physical address
   space, so every child image is LINKED at a distinct base assigned
   in config — blade/the stitcher emit per-child link addresses, and
   create_process validates segments against the assigned range.
3. **ClockType set for v1** (lean: Monotonic only; Boot/Realtime
   reserved in the enum, per the raw-backing wire idiom).
4. **The arm() copy-in record layout** + its validation rules (the
   funnel's first consumer sets the pattern IPC inherits).
5. **boot_handles record layout + tags** (178 flag c).
6. **Child AllocMemory right** (178 flag a; lean: stripped, root-only).
7. **Double-map one Memory in one process** (178 flag b; lean: allow).
8. **Quota defaults per kind** — the documented small defaults, and
   the mapping-quota ceiling's relation to the per-arch PMP budget
   (the quota is the POLICY cap; the PMP slot count is the PHYSICAL
   one; map() meeting the physical wall with quota headroom is a
   kernel bug to assert against, not a user-visible state).
9. **Death notifications: IN M3 (ruled Aug 16), unit 5.5.** The
    user's read is right with one sharpening: it is not a new object
    KIND — the existing Process object becomes the THIRD WAITABLE
    (waitable_slot's pattern, Waiter.add(process:key:), a
    WaitTag/WaitPayload.ProcessDeath variant carrying the exit
    status/fault reason). The parent already holds the child's
    Process handle from CreateProcess, which is what it attaches.
    Two small semantics to pin in the brief: death is a TERMINAL
    LEVEL (stays signaled — a waiter attaching after the death still
    wakes, matching the level-triggered Waiter contract), and the
    payload distinguishes clean exit from fault.
10. **Scheduler: RULED (Aug 16)** — round-robin stays for M3; SMP
    will require something more, designed when SMP arrives (after
    pipes, per pin 1).
11. **D2 tripwire: RULED** — see pin 1 (kernel interruptibility in
    M3, unit 1.5; SMP waits for pipes).
12. **Op/right numbering + §5.7 amendment wording** (178 flags d/e —
    mechanical, may be delegated to the implementing brief).

## UNIT 1 AS BUILT — the Clock and Timer objects (Aug 17)

Landed on its own branch, parked for review. The ruled shape (the 178 time
notes) is built unchanged; what follows is the decisions the implementation
had to pin, recorded here because the brief delegated them.

**The op/right numbering.** `ObjType.Clock = 7`, `ObjType.Timer = 8`.
`SystemOp.ClockGet = 3` behind a NEW `SystemRight.ClockGet = 1024`.
`ClockOp { Now = 0, TimerCreate = 1 }` over
`ClockRight { Transfer = 1, Manage = 2, Read = 256, TimerCreate = 512 }`.
`TimerOp { Arm = 0, Disarm = 1 }` over
`TimerRight { Transfer = 1, Manage = 2, Arm = 256, Wait = 512 }`.
`WaitTag.Timer = 2`, `WaitPayload.Timer(fires:)`. Both new kinds withhold
`Manage` at creation on the existing precedent (a Clock's only derivation is
a Timer, which has its own bit; a Timer derives nothing). `TimerRight.Arm`
gates BOTH `Arm` and `Disarm` on `WaiterRight.Attach`'s reasoning — a holder
who may arm can silence a timer by arming it a century out, so splitting the
pair would withhold nothing.

**`clock_get` IS A GETTER, NOT A FACTORY.** It mints on first ask and returns
the SAME handle thereafter, per (process, ClockType) — the `ProcessSelf`
shape. A machine has one monotonic clock, so two Clock objects would be two
names for one thing, which §3's no-duplicate rule exists to prevent; and an
op that cannot allocate on repetition needs no quota row in unit 5.

**`SystemRight.ClockGet` is NOT in the stripped-by-construction set.** The
device/memory rulings strip `Shutdown` and the bind/alloc rights from every
derived System handle; a clock grants authority over nothing and every
process needs to tell the time, so this bit travels. What the bit buys is the
substitution seam: strip it from a child and hand it a virtual Clock over IPC
instead, with no code change on either side.

**The copy-in record layout** (ABI-adjacent, and the pattern M4 IPC inherits):

```
Timer.Arm request          Clock.Now answer
  doubleword 0  after_ns     doubleword 0  now_ns
  doubleword 1  interval_ns
```

INDEXED IN DOUBLEWORDS, not machine words — the one way these differ from
the wait record. A wait record's fields are a key, a tag and a payload, all
machine-word-shaped, so it follows the register; these fields are TIMES, and
a time is 64 bits on every machine (anything narrower wraps in four seconds),
so following the register would make the layout differ per profile for no
reason. `after_ns` is RELATIVE to now, so no caller has to read the clock
first and race between two syscalls; `interval_ns` is a period, and ZERO IS
A ONE-SHOT.

**The copy-in funnel's window is the WHOLE GRANTED REGION**, where copy-out's
is the writable one. Deliberate asymmetry: a process may legitimately hand
the kernel a record out of its own rodata, and the two doors guard different
hazards — writing outside the writable window CORRUPTS the process, reading
outside the granted region READS SOMEBODY ELSE. `FaultReason.BadBuffer`'s
text is now direction-neutral for the same reason. The record LENGTH is
checked for EQUALITY, not "at least": a copy-out capacity is a buffer that
may be over-provisioned, while a copy-in length is the caller's statement of
what it is handing over.

**DISARM/RE-ARM, the semantics the brief left to the implementation:**

- **Arming an armed Timer REPLACES its schedule and CLEARS the fire count.**
  Not a fault: re-arming a running timer is the ordinary way to change a
  timeout — the shape M4's select-with-timeout is made of — and a count
  carried forward would be owed against a schedule the caller just discarded.
- **Disarming an UNARMED Timer is a NO-OP, not a fault**, and this is
  deliberately the opposite call from §9's ack-with-no-fire. The difference
  is whether the caller could have known: an extra ack is provably a bug,
  since the only way to learn about a fire is to be TOLD about one — while a
  ONE-SHOT DISARMS ITSELF, so "is it still armed" is kernel state that
  changed without the caller acting. Cancelling a timeout that has just
  expired is the ordinary race in every select-with-timeout, and faulting it
  would make the safe idiom unwritable. Disarm clears the fire count too,
  which is what makes it a clean cancel.
- **THE WAIT CONSUMES THE FIRES** (ack-free, the timerfd model). This added a
  FIFTH question to the per-kind matrix — `waitable_consume`, "a record was
  delivered" — whose Event and Interrupt answers are empty and whose
  emptiness is the honest statement of a real difference: an Event's payload
  is a snapshot `receive` remains authoritative over, an Interrupt's
  readiness is ended by the driver's ack, and a Timer has no second op and
  needs none. Delivery is now one funnel (`deliver_attachment`) so the drain
  cannot happen on one path and not the other.

**THE SHARED HARDWARE, which was the real design surface.** Both machines
have ONE comparator and now two customers. The seam flipped from periodic
(`timer_start(period_us)` + `timer_rearm()`) to ABSOLUTE (`timer_now_ns()` +
`timer_set_deadline_ns(at)`) on both arches, and the tick's PERIOD moved out
of the HALs into arch-free `kcore.start_tick` — it was always scheduler
policy sitting in a HAL, and a periodic-only seam can express exactly one
customer. The kernel keeps every deadline it owes in nanoseconds and programs
the earliest; a fire means "something MAY be due", and the handler asks every
customer out of ONE reading of the clock with the TICK ASKED FIRST. **§7's
tick is thereby inviolate by construction rather than by promise**: a Timer
deadline can only ever move the hardware EARLIER, never later, so the tick's
cadence is unchanged by any number of Timers. The two rules are deliberately
opposite — the tick re-arms from NOW (a late tick is one tick), a Timer from
its own DEADLINE (drift-free) — because a timeslice has no history to be
faithful to and an interval does.

**Coalescing is a DIVISION, not a loop** (`missed = late / interval + 1`),
so an interval far shorter than the machine can serve costs one divide rather
than a spin — there is no long loop here for unit 1.5's preemption points to
have to cover. What makes a too-fast schedule harmless is `TIMER_MIN_LEAD_NS`
(50 µs): the hardware is never programmed sooner than that, so the intervening
expiries coalesce instead of the machine living in the trap handler, and the
LOGICAL schedule is untouched. No minimum interval has to be invented and
nothing is refused.

**The idle/deadlock report generalized**, as the brief required: an ARMED
Timer is a wake source exactly like a bound IRQ line. "Idle iff a line is
bound" was always the special case of "idle iff something outside the thread
set can still wake somebody". A merely EXISTING Timer does not count — an
unarmed one will never fire — and that distinction has its own harness case,
because getting it wrong that way fails as a timeout rather than a report.

**Harness:** six new cases per arch (`clock_basics`, `timer_oneshot`,
`timer_interval`, `timer_deadlock`, `timer_badclock`, `timer_badrecord`),
38 per architecture, 76 runs.

**Three findings, none worked around** (the third from the Aug-17 review pass):

- **DF-232a** — a bare integer literal does not adopt the expected
  fixed-width type in PLAIN ASSIGNMENT position, and the mismatch is an
  INTERNAL COMPILER ERROR rather than a diagnostic. Probed matrix: it adopts
  in an annotation, a struct literal, a repeat literal, a COMPOUND assignment
  and both array- and Vector-element writes, and fails for a local, a struct
  FIELD and a field through an element. Pinned by
  `examples/assignment_target_adopts_fixed_width.saw`. Not fixed here: it is
  a core typechecker change and does not belong buried in a parked SOS
  branch.
- **DF-232b** — `type` is a Saw keyword, so the ruled
  `System.clock_get(type:)` spelling is unwritable. Built as `kind:`, which
  is the vocabulary this system already uses (`ProcessStatusKind`,
  `WaitableKind`).
- **DF-232c** — a raw-backed enum's case value takes an INTEGER LITERAL only, so
  the Aug-17 bit-flag ruling below is unwritable for the rights enums and
  `SegFlag`. Not the shift operator: no const arithmetic parses in that position
  (`(1 << 0)` and `2 * 4` are refused too). Design 185's fold covers USING a case
  value as a const operand, not its own initializer. Pinned by
  `examples/enum_raw_value_takes_const_expression.saw`; the enums keep decimals,
  values unchanged.

**REVIEW RIDER (ruled Aug 17, user) — an op reads `object_operation`.** The
noun of the object kind an op concerns leads and the verb follows, so unit 1
landed as `SystemOp.ClockGet` / `ClockOp.TimerCreate` rather than `GetClock` /
`CreateTimer`, and the M2 ops were swept with it: `ProcessSelf`, `ThreadCreate`,
`ThreadSelf`, `EventCreate`, `WaiterCreate`, `InterruptBind`, each with its
mirroring right, typed method (`system.clock_get`, `proc.thread_create`) and C
export (`sos_system_clock_get`). An op whose RECEIVER IS the object it concerns
keeps the bare verb — `Arm`, `Ack`, `Wait`, `Signal`, `Join`, `Start`, `Exit`,
`Yield`, `Now`, `Add`, `Remove`, `Shutdown`, `DebugPrint`, `GetStatus` — since
there is no second noun to lead with, and a right naming a CAPABILITY rather
than an op (`Transfer`, `Manage`, `Read`, `Control`, `Attach`) is untouched.
NUMBERS DID NOT MOVE: this is spelling, and both arches' harness runs are what
say so. The rule is recorded where the names live, in `sos/kernel/abi/`'s module
docstring beside the Aug-15 family-naming rule, so the M3 units that add object
kinds inherit it. THE AGENDA AND UNIT-LADDER SECTIONS ABOVE PREDATE THIS RULING
and keep the spellings they were written with; the names built are the ones
here, and a later unit takes its op names from `sos/kernel/abi/` rather than
from the ladder.

**REVIEW RIDER (ruled Aug 17, user) — NUMERIC LITERALS SAY THEIR MAGNITUDE.**
A literal long enough that a reader would have to count digits takes `_`
separators: decimal by thousands (`NS_PER_SECOND = 1_000_000_000`,
`TIMER_MIN_LEAD_NS = 50_000`, the LCG constants in `thread-preempt`), hex by
nibble-quads (`0x1000_0000`, `0x0200_BFF8`, and the arm64 descriptor bits
`ATTR_PXN = 0x20_0000_0000_0000` / `ATTR_UXN = 0x40_0000_0000_0000`, which had
been written as fourteen unbroken digits). Short ones are exempt — `4096`,
`0x301`, `0x20` read at a glance — and a device-register mask stays HEX, since
it is a datasheet field. Swept across `sos/` (kernel, hal, tests, root,
imgformat, rt); VALUES ARE IDENTICAL, underscores only, which both arches'
harness runs are what prove.
The companion half of the ruling — a BIT-FLAG value spelled `1 << n` rather than
as an absolute decimal — could not be built: the enum case-value grammar takes a
literal and nothing else (DF-232c above). The rights enums and `SegFlag` keep
their decimals until that is fixed, and the `static_assert` bit-8 floors keep
theirs with them, so each file reads consistently rather than half-converted.

**REVIEW RIDER (ruled Aug 17, user) — AN EVENT'S WORD IS CONSUMED ON
DELIVERY.** A Waiter delivery of an Event now READS AND CLEARS the word into the
wait record, where M2 left a snapshot behind and made `receive` the
authoritative drain. The take is one act: `waitable_answer` reads and
`waitable_consume` clears, both inside `deliver_attachment` and therefore inside
one interrupts-masked region (D2 — the kernel takes no trap in kernel mode), so
nothing can land between them; a signal arriving after the take re-arms
readiness with its own bits alone, which is neither a loss nor a double count.
`waitable_consume`'s Event arm stops being empty and the per-kind table now reads
the other way round: TWO of the three kinds are spent by delivery (Event, Timer)
and the INTERRUPT is the odd one out, because §9 ends its readiness at the
driver's ack — the only readiness in the system whose end is a device's business.
`event.receive()` KEEPS ITS EXACT SEMANTICS as the non-blocking poll
(drain-and-reset, 0 when empty, never parks): the two are DOORS ONTO ONE VALUE,
and a process picks by whether it wants to park rather than by what it will be
told. The reason the snapshot had to go is that it reported the same bits to the
next wait as well, and a recipient cannot tell that duplicate from a real second
signal.

NO MULTI-WATCHER CAVEAT IS OWED, because the recipient is unique BY
CONSTRUCTION: §2.2 gives a waitable at most ONE attachment, so a signal reaches
exactly one Waiter, which hands it to exactly one parked thread. That is the
multiplexing rule in one line — MANY THREADS PER WAITER IS DISTRIBUTION (one wake
per delivery, the worker-pool shape), ONE WAITER PER WAITABLE IS OWNERSHIP, and
the second is structural rather than a convention. Spec §2.2 and §2.4 carry both
sentences, and the §2 Event row is amended with them.

ONE RIGHTS CONSEQUENCE, recorded rather than designed around: `EventRight.Wait`
is now a CONSUMING right. It was always half true — the record has carried the
accumulated word since M2 — and what changed is that it empties the object too,
so the two consumer bits attenuate by DOOR (park or poll) rather than by whether
the holder learns anything, and there is no bit left that means "observe
readiness without taking". Nothing in SOS needs one before M4: with no handle
transfer, every right on an Event is held by the process that made it.

**Harness:** two new packages, split by DOOR because the delivery runs a
different path in each — `event-consume` (the poll door: two signals coalesce
into ONE record carrying the merged word, in both accumulation modes; the
`receive` after every take reads 0; a signal after the take answers with only the
new bits, 8 rather than 1|2|8) and `event-consume-wake` (the wake door: two
threads, two real parks, `at-wait=5 after=0` then a second wake carrying 2 rather
than 5|2). `event-basics` and `event-wake` moved with the semantics — the former
now reads `after=0` on every line, with its `detached` line the one place
`receive` is the only taker and answers a real number; the latter reads
`at-wait=1 after=4`, one plus four being the five the worker signalled. 40 cases
per architecture, 80 runs.

**REVIEW RIDER (ruled Aug 17, user) — A TIMER'S VERBS ARE METHODS ON THE SLOT.**
The repeated `TIMERS[slot].<field>` chains in the timer operations became an
extension on `TimerSlot`: `arm(deadline:interval:)`, `disarm()`, `is_due(now)`,
`sooner_of(deadline)` and `fire(now)`, with the ops reduced to indexing the slab
and calling one — `TIMERS[slot].fire(now)` where five field fetches used to sit
in a row. Design 212's extraction idiom with a RECEIVER rather than a reference
parameter, since a slot is a type and these are its verbs.

THE UNSAFE EFFECT RETREATED, WHICH IS THE HALF THAT IS NOT STYLE. `TIMERS` is an
`unsafe static var`, so every function NAMING it is declared `unsafe` (design
130/136) — while `TimerSlot` is an ordinary safe type, so a body working through
`self` names nothing unsafe and the compiler accepts it as a SAFE function. 26
statements moved out of unsafe bodies into five safe ones, the whole drift-free
re-arm and coalescing division among them (`fire_timer` is now two lines: the
call and the wake). `TIMERS[` occurrences in the kernel fell 32 → 22, and the
count of safe functions rose 24 → 29 against an unchanged 96 unsafe ones — no
existing function flipped, because each still indexes the slab or calls an unsafe
helper, and the honest delta is the ARITHMETIC that no longer sits inside an
unsafe body rather than a smaller list of unsafe names.

Left alone deliberately, per the ruling: single field accesses (`TIMERS[t].armed`
in the idle rule) and the whole-slot writes in `timer_create` and teardown —
wrapping one load in a method buys a name for something that already had one. No
`ClockSlot` methods are owed: the global-clock change below deletes every
multi-access clock body there was.

No behavior changed, and the 80 harness runs on both arches are what say so.

**REVIEW RIDER (ruled Aug 17, user) — THE HARDWARE CLOCK IS A GLOBAL
SINGLETON.** A hardware-backed Clock is ONE KERNEL-ETERNAL OBJECT PER
`ClockType`, existing from boot and owned by nobody. THE RATIONALE FOR THE
RECORD: a `ClockSlot` holds no per-process state — the machine's counter is the
clock, and the slot recorded only whose it was and which domain it named — so a
per-process object was a copy of a fact with a lifetime attached to it. Virtual
clocks later are a DIFFERENT ANIMAL: separately created, STATEFUL (offset, rate,
owner), with their own creation op and their own lifetime. Sharing this slab
with them would put two kinds of thing in one table for the sake of a word they
share.

DELETED: the `process` field, `alloc_clock`, the `NO_CLOCK` sentinel, the
find-or-create scan, `clock_get`'s slab `NoResource` path, the teardown's clock
loop, and the `clocks=` column of the teardown report (a line that counts what a
process OWNED has no place for a permanent zero). `MAX_CLOCKS = MAX_PROCESSES + 1`
became `CLOCK_KINDS = 1` — not a budget but a census of domains — and `ClockSlot`
is now one field wide. THE SLOT IS THE DOMAIN'S ORDINAL (`clock_slot`), since
`ClockType`'s raw values are dense by construction.

KEPT, DELIBERATELY: the same-handle return. `clock_get` looks the process's own
table up through `clock_handle_of` and mints only if nothing is there, so asking
twice still answers one handle and §3's no-duplicate rule still holds — the
object cannot say who holds it, so the holder's table is the only place that
answer could live. THE MINT-PER-CALL FLIP IS NOT MADE HERE: the lifecycle unit
2.75 owns it. What CAN still fail is the handle table, which is the process's own
resource, so one `NoResource` path remains and it is the honest one.

`clock_now` now DISPATCHES ON THE DOMAIN — it takes the slot and matches
`CLOCKS[slot].kind` — which is what gives the surviving field a reader and makes
adding a domain fail to compile until somebody says what its reading is. That is
the one question a second `ClockType` actually poses.

Timers reference the global slot unchanged (`TimerSlot.clock` is still bound at
creation, and it now names an object that outlives every process, which is
strictly more true than before). The close-all path was verified rather than
assumed: it unbinds handle-table ENTRIES and never touches the objects they name,
so it needed no per-kind arm to skip clocks — the evidence is `clock_basics`
still reading `handles=5` after two asks, which is now the whole of the
same-handle claim since the `clocks=` column it used to rest on is gone.

## Explicitly out (M4+ candidates)

Pipes + PipeReplyHandle IPC (with select-with-timeout via unit 1's
Timer); the IOMMU driver + critical processes (round 4's architecture;
death notifications now land IN M3, so only the driver work remains);
priorities/§7 bands; SMP + IntrSpinLock — EXPLICITLY SEQUENCED AFTER
CHANNELS (ruled Aug 16: SMP wants meaningful concurrent actions to
exercise, and unit 1.5's preemption-point map is its conversion
guide); FP in userspace; vDSO true-mapping; program loading beyond
root's children; big-endian anything.
