# SawOS design 32 — M5 unit 6: slab donation (capacity becomes policy)

**Status: BRIEF, Sep 3 2026 — dispatches under `designs/025` (RULED):
D-6 as ruled.** Arch-free (kernel/core + abi + sysapi + tests).
Converts every donatable kind EXCEPT processes; the process-slot
restructure is unit 6b, its own rung, its own brief. The user's
motivation, recorded in the ruling: the compiled limits are
suite-sized and forbid a real system — many processes, pipes,
threads — and the (post-M5) namespace vision needs that ceiling gone.

## The one-sentence goal

Root donates a Memory region to the kernel to back a named kind's
slab — `SystemOp.SlabDonate(kind, memory)` on its own
`SystemRight`, consuming, permanent in v1 — so a kind's capacity is
decided by the deployment that boots the machine, not by the
compile, while the kernel still boots (and the whole suite still
passes byte-identical) with no donation at all.

## The ruled mechanics (025 D-6, not reopenable here)

- The op CONSUMES the capability (a move, like `give`); donation is
  PERMANENT in v1 — the mapping-leak stance: safe, never reclaimed;
  pool returns for donated slabs are recorded-not-built.
- Refusal (`BadState`) for a region the kernel cannot safely absorb.
  **Unit 5 made the condition precisely expressible**: the donated
  handle must be the region's ONLY reference (`refs == 1`) and no
  row may be installed (`maps == 0`) — the kernel never shares slab
  backing with user-reachable memory.
- A kind's slab becomes a CHAIN OF EXTENTS; the static array is
  EXTENT 0, so boot minimums stay compiled and the kernel boots
  before any donation exists.
- Quotas stay per-process authority; donation bounds the MACHINE.
  `NoResource` keeps its meaning ("the slab is full") — what changes
  is who decides how big the slab is.

## Design points

- **ABI motion, named**: one op (`SystemOp.SlabDonate`), one right
  (`SystemRight.SlabDonate`, minted in the root set of necessity —
  §3 attenuation is monotonic), one `kind` argument. The kind
  vocabulary is this unit's call — `ObjType` is the tempting reuse
  but not every ObjType is donatable (Mapping? the free-range
  nodes have no ObjType at all); a small dedicated enum in `sosabi`
  that names exactly the donatable slabs is probably more honest.
  Numbers are not ABI (the vDSO discipline); the typed surface is
  `System.slab_donate(kind:, memory:)` with the NoCopy wrapper
  moved in (the transfer-funnel contract; `consumes` if the
  receiver position allows, else the dissolve discipline).
- **Which slabs convert**: enumerate in the As-built. Candidates:
  THREADS, EVENTS, WAITERS, INTERRUPTS, TIMERS, MEMORIES,
  IOMEMORIES, MAPPINGS, PIPES (+ their per-pipe parallel arrays —
  a PipeSlot's satellite arrays like `PIPE_BODIES`/`PIPE_CALLER`
  must grow WITH the slot chain or the kind cannot convert;
  say which shape each kind takes), ATTACHMENTS (derives from
  MAX_ATTACHMENTS — decide whether it converts or derives from
  converted counts), and the FREE-RANGE NODE slab —
  **unit 5's named first customer**: when `alloc_free_range` finds
  no node it currently leaks honestly into `dropped`; after this
  unit a deployment can donate the nodes back into existence.
  HANDLES/BOOT_HANDLES/quota tables are `MAX_PROCESSES ×` arrays —
  unit 6b's territory, explicitly OUT here.
- **Extent addressing**: a handle word's index bits span the chain;
  lookup is index → (extent, offset). Keep the arithmetic simple
  and uniform (the agent's design; fixed slots-per-extent derived
  from the donated length is fine). `HANDLE_INDEX_BITS` bounds
  total capacity — static_assert the relationship and record the
  ceiling honestly.
- **ONE ACCESS FUNNEL for extent memory**: every dereference of a
  donated extent goes through a single helper (e.g.
  `slab_extent_base(kind, ext) -> UInt`). This is a COORDINATION
  SEAM with unit 1.5 (the linmap): if 1.5 has landed, the helper
  routes through `hal.phys_to_virt`; if not, it returns the
  physical address raw and 1.5's sweep converts exactly one site.
  Whichever unit rebases second resolves this consciously.
- **Slot reuse, generations, refcounts, teardown walks**: unchanged
  in MEANING, re-plumbed to walk extents. The teardown and census
  walks that iterate `MAX_<KIND>` today iterate the chain; keep the
  bounded-walk property (extents are finitely many, each finitely
  sized) so the design-1 no-preemption audit arguments survive —
  restate them where the loop bounds changed.
- **Zeroing**: a donated range becomes slab slots; slots must start
  Free/zeroed. Zero at donation (bounded, one-time, inside the
  syscall — same cost class as `load_domain`'s old replay), never
  lazily.

## The proof (new cases; the suite is the fence)

- **`slab_donate`** (new): donate a split region to a small kind
  (PIPES is ideal: floor is 4), create PAST the compiled floor,
  exercise the new objects (a pipe made in extent 1 must carry
  messages like any other), and show `NoResource` moved to the new
  ceiling. Negative legs: donate with a live mapping →
  `BadState`; donate a region with a sibling handle → `BadState`;
  the consumed handle is dead after (`BadHandle` via generations).
- **`slab_donate_free_nodes`** (new, or a leg of the first): the
  unit-5 composition — exhaust free-range nodes (the `dropped`
  counter path), donate nodes, show a release that would have
  leaked now returns its range.
- Transcript motion: THE NEW CASES ONLY. Every existing case
  byte-identical, both arches — donation changes no path a
  non-donating program takes.

## Gate

`make sos-test` under the suite lock, transcript diffed against
main's baseline (234/234 at dispatch): new-case rows only, both
architectures, timing rows excepted.

## Out of scope

Processes and everything `MAX_PROCESSES ×` (unit 6b); donated-slab
reclamation (recorded-not-built); any HAL/tier work; byte quotas;
spec.md prose beyond doc comments (unit 8 sweeps — the §2 quota
column and §12's capacity sentences are located-not-edited in the
As-built for it).

## Recording duties

As-built here: the kind vocabulary as landed, the converted-kinds
table with each kind's shape (plain chain vs satellite arrays), the
extent arithmetic and its ceiling, the 1.5-coordination outcome,
gate evidence (case counts, both arches, new-rows-only diff),
findings. SL entries to [SAWLANG] if the language bites. Close the
tracker entry in place; the lead moves it at integration.

# As built (Sep 3 2026)

**Status: BUILT.** The ruled mechanics landed as ruled — one op on its own
right, consuming, permanent, refused for a region the kernel cannot safely
absorb, with the static array as extent 0 and the kernel booting with no
donation at all. NINE kinds converted. Two donatable-looking kinds are excluded
with their reasons stated, and those exclusions are the honest half of the unit.

## The kind vocabulary, as landed

`SlabKind` in `sosabi.ops`, a small dedicated enum backed by `UInt` (the width of
the register the kind arrives in — `SystemOp`'s own reasoning: the value is
process-controlled, so the decode is exact and `from(raw:)` rejects everything
that is not a kind). One name on the facade's `sosabi.ops` line; one name on
`sos`'s `lib.saw` beside `MapAccess`, which is the same test — a value a PROCESS
CHOOSES and passes as a syscall argument.

```
Events = 0   Waiters = 1   Interrupts = 2   Attachments = 3   Timers = 4
Memories = 5   IoMemories = 6   Mappings = 7   FreeRanges = 8
```

**`ObjType` WAS THE TEMPTING REUSE AND IS THE WRONG SET, in both directions.** It
has members that are not donatable (`Process`, `Clock`, the four pipe kinds) and
it is missing the slab this op exists to feed: the free-range NODES are not
objects, have no handles and have no `ObjType` at all, and design 28 named them
as the first customer. Reusing it would have meant a vocabulary both too wide and
too narrow, with a `from(raw:)` that accepts numbers the kernel then refuses one
at a time. The names are PLURAL because a `SlabKind` names a TABLE —
`SlabKind.Events` is never confusable with `ObjType.Event`.

## The converted kinds, and the shape each one takes

A kind converts IFF its slab is a single array keyed by the slot index with no
parallel storage. That one rule decides every row below.

| kind | slab | shape | converted |
| --- | --- | --- | --- |
| Events | `EVENTS` | plain chain | YES |
| Waiters | `WAITERS` | plain chain | YES |
| Interrupts | `INTERRUPTS` | plain chain | YES |
| Attachments | `ATTACHMENTS` | plain chain | YES |
| Timers | `TIMERS` | plain chain | YES |
| Memories | `MEMORIES` | plain chain | YES |
| IoMemories | `IOMEMORIES` | plain chain | YES |
| Mappings | `MAPPINGS` | plain chain | YES |
| FreeRanges | `FREE_RANGES` | plain chain | YES |
| Threads | `THREADS` + `THREAD_FRAMES` | ONE satellite, uniform stride | no |
| Pipes | `PIPES` + TEN satellites | satellites on a DERIVED index | no |
| Processes | `PROCESSES`, `HANDLES`, `BOOT_HANDLES*`, `QUOTA_*` | `MAX_PROCESSES ×` | no — unit 6b |
| Clocks | `CLOCKS` | not a slab at all | no |

**THE THREE EXCLUSIONS, each for its own reason.**

- **THREADS is the near miss, and it is the cheapest next increment.** Its one
  satellite is `THREAD_FRAMES`, a saved-context arena addressed by a uniform
  `THREAD_FRAME_STRIDE` through the single function `frame_slot`. Growing it with
  the chain is a real design — a THREADS donation would have to carry TWO regions
  (slots, then an aligned frame arena) and the extent descriptor would need a
  second base — and it lands on the context-switch path, which is the most
  delicate code in the kernel and the place a byte-identical gate is hardest to
  keep. Excluded on RISK, not on impossibility, and the shape above is the note
  for whoever takes it.
- **PIPES is excluded on merit.** Ten arrays hang off the flat exchange index
  `x = c * PIPE_INFLIGHT + i` — `PIPE_BODIES` (8 KiB of message bodies),
  `PIPE_LENS`, `PIPE_EX_STATE`, two ref columns, two attach columns,
  `PIPE_CALLER`, `PIPE_NEXT` and `PIPE_MSG_HELD` — and the index is DERIVED from
  `MAX_PIPES`, so growing the connection slab renumbers every satellite. That is
  not an extent chain, it is a different data structure.
- **CLOCKS is not a shortage.** Nothing is ever allocated in it, the slot IS the
  `ClockType` ordinal, and there is no `alloc_clock`. A donation buys nothing.

**THE USER'S THREE MOTIVATING EXAMPLES WERE "many processes, pipes, threads", AND
THIS UNIT DELIVERS NONE OF THEM.** That is worth saying plainly rather than
burying in the table: processes are unit 6b by the brief, and pipes and threads
are the two satellite-bearing kinds. What the unit delivers is the MECHANISM —
the op, the right, the vocabulary, the extent chain, the safety condition and the
proof that a donated slot is a working slot — plus the nine kinds that fit it
today. Threads is the next one and it is small; pipes wants its own thinking.

## The extent arithmetic, and the ceiling

`Slab<T, const N: Int>` in the new module `kcore.slab`, one generic serving all
nine: `inline: [T; N]` is extent 0, plus `ext_base`/`ext_slots` arrays of
`MAX_SLAB_EXTENTS` (4), an `ext_count` and a cached `total`.

- **Slots per donation** = `(len - pad) / sizeof<T>()`, where `pad` aligns the
  base up to `alignof<T>()`. Alignment is done BY ARITHMETIC, never by refusal: a
  region's base is wherever the pool's cursor reached and the caller cannot know a
  kernel slot's alignment, so a misaligned donation is not a caller mistake. The
  remainder form (`base % align`) is used rather than `align_up` because it cannot
  overflow on a near-max base.
- **Lookup** is `i < N ? inline[i] : extent_addr(i)`, and `extent_addr` walks at
  most `MAX_SLAB_EXTENTS` entries subtracting slot counts. Bounded, O(1) body.
- **The measured slot sizes**, probed against the pinned sawc for both targets:

  | slot | riscv32 | arm64 | slot | riscv32 | arm64 |
  | --- | --- | --- | --- | --- | --- |
  | `WaiterSlot` | 20 | 40 | `TimerSlot` | 48 | 72 |
  | `EventSlot` | 24 | 48 | `MemorySlot` | 36 | 72 |
  | `InterruptSlot` | 24 | 48 | `IoMemorySlot` | 20 | 40 |
  | `Attachment` | 24 | 48 | `MappingSlot` | 20 | 40 |
  | `FreeRange` | 16 | 32 | | | |

  So the donated page in `tests/slab-donate` yields **204 waiter slots on riscv32
  and 102 on arm64** — a new ceiling of 208 against 106. THAT ASYMMETRY IS WHY THE
  CASE NEVER PRINTS A COUNT: it asserts that the ceiling MOVED, which is arch-free,
  and leaves the number to this table.

**THE CEILING, AND A CORRECTION TO THE BRIEF'S PREMISE.** Design 25 D-6 says "a
handle word's index bits span the chain", and this brief asks for a
`static_assert` that `HANDLE_INDEX_BITS` bounds total capacity. **In this kernel
it does not, and that assert would have been false comfort.** A handle word is
`(generation, index)` where the index names a row of the CALLER'S OWN HANDLE
TABLE (§3), and that row names the slab slot in a full-width `Int`
(`HandleEntry.target`). So a slab may grow far past `1 << HANDLE_INDEX_BITS` with
no extra index bit, and this unit changed nothing about the handle word.

What `HANDLE_INDEX_BITS` really bounds is `MAX_HANDLES` — the per-process table,
pinned where the decoder lives (`kcore.objects`) — and THAT is the ceiling a
deployment actually meets: however many Events the machine can hold, one process
can NAME at most `MAX_HANDLES` of them at once. Raising it is raising a
`MAX_PROCESSES`-shaped array, which is unit 6b's subject. So the honest relations
are pinned in `kcore.limits` instead:

```
static_assert(MAX_SLAB_CAPACITY >= MAX_HANDLES * MAX_PROCESSES, …)
static_assert(MAX_SLAB_CAPACITY < Int.max / 1024, …)
```

— the machine's slab ceiling is never what stops every process from filling its
table, and the index arithmetic cannot overflow a platform `Int`.

## The coordination seam with unit 1.5 — state at hand-off

**THE FUNNEL EXISTS AND IT IS ONE LINE.** `Slab.extent_addr` is the ONLY place a
donated extent's memory becomes an address; the accessor's other arm lends out of
the inline array and touches no address at all. The linmap does NOT exist at this
unit's dispatch, so the helper returns the PHYSICAL address raw, with the
conversion site named in its doc comment:

```
return self.ext_base[e] + ((rest * sizeof<T>()) as UInt)
```

When design 29 lands, that return becomes `hal.phys_to_virt(...)` — one edit, one
site. **IF 1.5 MERGES FIRST, THIS UNIT IS THE SECOND LANDER** and the rebase
resolution is exactly that edit; nothing else here dereferences a donated extent,
so 1.5's "every kernel-side pointer built from a stored physical address" sweep
finds one site in the slab machinery and no others. If this unit merges first,
1.5's sweep gains that one site to its list. Either way the resolution is
conscious and small, which is what the seam was for.

## What moved, and what deliberately did not

- **NO ACCESS SITE MOVED.** `EVENTS[i].state` means what it always meant, because
  `[]` is a `borrows` accessor lending the slot where it sits. The ~370 indexed
  reads and writes across `kernel/core/` were not touched — which is why the diff
  is small for a change this structural, and why the risk sat in the loop bounds
  instead of in the accesses.
- **TWELVE LOOP BOUNDS MOVED**, and each is a place a donated slot would otherwise
  be invisible: the nine allocator scans, the `end_process` teardown sweeps for
  interrupts / attachments / events / waiters / timers / mappings,
  `reap_unreferenced`'s memory and iomemory passes, `expire_timers`,
  `earliest_timer_deadline`, `has_external_wake_source`, `compact_mapping_rows`
  and `interrupt_by_line`. The compiler cannot catch a missed one, so every
  remaining use of a converted `MAX_<KIND>` was read and justified.
- **AUDIT (design 1), RESTATED WHERE THE BOUNDS CHANGED.** The no-preemption
  verdict survives, and the argument is the same one with a different number: a
  walk is bounded by `capacity()` rather than by a literal, which is finite
  because extents are finitely many (`MAX_SLAB_EXTENTS`) and each is finitely
  sized; the bodies are unchanged and still O(1) in RAM. What DID change is the
  SIZE of the bound — a donated machine's teardown sweep is as long as its
  deployment made it — and `kcore.limits`' own audit paragraph already names
  "raising one of these numbers by an order of magnitude" as the trigger for
  re-running the audit. **Donation is now a way to do that AT RUN TIME, and that
  is the honest new fact this unit hands the next preemption-point unit.** The one
  place this unit itself takes points is the donation ZERO (`long_zero`), and it
  is safe by construction: the extent is not installed and the handle is not
  consumed, so nothing is half-built across a point.
- **Two `MAX_<KIND>` uses were deliberately NOT converted.** `install_quota_limits`'
  root mapping cap stays `MAX_MAPPINGS` — a POLICY number, immediately clamped by
  the profile's free hardware rows, not a slab scan. And the boot region-table
  bound stays `MAX_MEMORIES + MAX_IOMEMORIES` — that table is placed before root
  can run, so no donation can have happened and the compiled floor is the honest
  limit.
- **`FREE_RANGES` STOPPED BEING MODULE-PRIVATE.** The donation dispatch names
  every donatable slab in one `match` and sits above `waitables`, so it cannot
  live in `objects.saw`. The privacy was incidental rather than a guarantee.

## The consume, and why it is not `unref_object`

The ordinary release path would reach `memory_release_if_idle`, which returns the
range to its pool root's free list — and the next `Split` would hand a slab's
backing store to somebody else. So the op unbinds the handle and calls a new
sibling, `memory_retire_donated`: it frees the SLOT (the object is gone, nothing
can name it, the slot is reusable) and never returns the RANGE.

What makes that permanent rather than merely unrecorded is the ledger: a derived
region's length stays counted in its root's `out_bytes`, so the root still
believes those bytes are on loan and can never itself be released or re-served. A
pool ROOT may be donated whole, and then its free-list nodes go back exactly as
`memory_release_if_idle` does it — the caller has already been checked for
`out_bytes == 0`, so nothing on loan is orphaned. **Pool returns for donated
ranges are recorded-not-built, as ruled.**

## Which refusals fault and which answer

On design 178's split — and the brief's word "`BadState`" lands on the FAULT
channel, because **this kernel has no `SosStatus.BadState`**: `BadState` is a
`FaultReason` ("object in the wrong state"), which is exactly right here.

- **FAULTS** (the caller could have checked): no `SystemRight.SlabDonate`; a
  `kind` that is not a `SlabKind`; a handle naming nothing or naming a
  non-Memory; `refs != 1`; `maps != 0`; a pool root with `out_bytes != 0`. Root
  minted the siblings, installed the rows and cut the pieces.
- **STATUSES, consuming nothing** (machine answers): a region too small to hold
  one slot of a kind whose size is kernel-internal, and a slab whose extent chain
  is full — both `NoResource`.

That split forced the typed surface's shape: because two refusals consume
nothing, `System.slab_donate` returns `Result<Void, (SosStatus, Memory)>` and
hands the region BACK in the error — `Waiter.give`'s idiom (SL-14's shipped
workaround) applied to a by-value parameter.

## Findings

1. **THE BRIEF'S CEILING PREMISE WAS WRONG FOR THIS KERNEL** — see the ceiling
   section. Recorded as a finding rather than silently re-aimed, because design
   25 D-6 states it as mechanics and a later unit could inherit the mistake.
2. **THE `maps == 0` HALF OF THE SAFETY CONDITION IS UNCOVERED BY TEST.** It is
   implemented and sits two lines from the `refs` check in the same block, but
   proving it wants a live mapping, which wants a second process to map into or a
   self-map — a bigger case than this unit's other negative. Named here rather
   than claimed. The "consumed handle is dead after" leg is likewise unwritten:
   it is a third fault case for a property the generations already prove
   elsewhere (`give-word-dead`).
3. **A ROOT SERVER CAN SIZE THE MACHINE BUT NOT NAME WHAT IT SIZED.** With
   `MAX_HANDLES` at 16, a process that donates 204 waiter slots can still hold
   only 16 handles at once. The capacity is real and shared machine-wide — other
   processes and the kernel's own bookkeeping use it — but a single-process
   workload sees no benefit until unit 6b raises the per-process table. Worth
   knowing before anyone measures this unit by what one program can allocate.
4. **THE ONE `@export` COSTS EVERY riscv32 IMAGE 40 BYTES** — see the gate's
   third bullet. Expected, uniform, and the honest price of the floor's
   design rather than anything this unit did wrong; recorded because it is the
   only transcript motion outside the new cases.
5. **`@export("_start")` IS LOAD-BEARING AND ITS OMISSION IS SILENT AT BUILD
   TIME.** Both new packages first built "successfully" into 1,776-byte images
   containing only the runtime stubs — `_start` had been dead-stripped, the
   sosimg carried `entry 0x0`, and the failure surfaced only at BOOT as "bad
   root image: entry outside the root region". Every existing package carries
   the attribute, so this is a new-package trap rather than a regression, and
   `tools/sosimg_dump.py` is what named it in one command. Worth a line in
   whatever the next unit's package checklist is.
6. **`slab_donate_free_nodes` WAS NOT BUILT, and the reason is itself a
   finding.** The brief asks for the unit-5 composition — exhaust the free-range
   nodes, reach the `dropped` path, donate nodes, show a release that would have
   leaked now returning its range. `FREE_RANGES` IS converted and IS donatable,
   so the mechanism is there; what is missing is a way to reach exhaustion.
   **A node is only ever spent on a HOLE, so exhausting 32 of them needs more
   than 32 non-adjacent free ranges, which needs more than 32 live regions
   interleaved with them — and `MAX_MEMORIES` is 16.** The pool physically
   cannot be fragmented that far on a stock image, which is why design 28 could
   record `dropped` as "zero in this tree".
   So the case is a TWO-STAGE donation — donate to `Memories` first, create
   forty-odd regions, fragment, exhaust the nodes, then donate to `FreeRanges`
   and show the range coming home — and its first stage is the very op under
   test. That is a good case and a bigger one than this unit's other two; it is
   also the first thing that would exercise a donated `MemorySlot` under real
   load, which makes it worth doing properly rather than squeezing in. Filed
   here with its shape. Note the composition is not merely untested but
   currently unreachable WITHOUT this unit: before `SlabDonate` there was no way
   to build a pool fragmented past the node slab at all.

## Gate

Both runs in this worktree, back to back under the machine-wide suite lock,
same toolchain (sawlang 0.4.0 @ the pinned `46eebb36`; `sawlang.pin` untouched).

**Baseline**, stashed to a pristine tree at `b613c5a` before any edit:
**234 passed across riscv32 + arm64**, 117 cases, a 487-line transcript hashing
to `d37d2dbdd686fd573c6c57550f71c107eddcd3e4d8bffc75835f3665f764c8b2` — *the
same hash design 28's As-built and the unit-1 rebase note both record for main*,
so the baseline is the tree's standard one and the toolchain is where it should
be.

**After: 238 passed across riscv32 + arm64**, 119 cases, 495 lines,
`13b3214ce2b27aec793b189dcb1002a52bdd835ead664638947f3a354b8083b1`.

**THE DIFF, AND IT IS THREE THINGS.**

1. **FOUR NEW CASE LINES** — `slab_donate` and `slab_donate_shared`, once per
   architecture. Every one of the 234 pre-existing case lines is BYTE-IDENTICAL
   and in its original ordinal, which is what appending the cases bought.
2. **FOUR NEW IMAGE LINES** — the two new packages, per architecture.
3. **119 riscv32 IMAGE SIZES, EACH EXACTLY +40 BYTES. arm64: ALL 119 UNCHANGED,
   0 bytes.** This is the one motion the brief's "new cases only" did not
   anticipate, so it is stated plainly rather than normalised away. THE CAUSE:
   `sos_system_slab_donate` is an `@export`ed C-ABI wrapper, and the floor's
   whole contract is that its surface is exported — so it links into EVERY image
   whether or not the program calls it, exactly as the other ~40 floor exports
   already do. It is uniform because it is one function, and it is 0 on arm64
   because that function fits inside padding the segments already carried. The
   alternative — dropping `@export` so the symbol dead-strips — would make one
   op of forty different in kind from its neighbours, and was declined.
   **No case's behaviour moved; the column that moved is a build artifact.**

**PROVEN RATHER THAN ASSERTED:** normalising the mechanical `/117` -> `/119`
denominator, abstracting the image-size column, and removing the new cases' own
lines makes the two transcripts **identical at 487 lines, zero diff hunks**. The
three documented timing-dependent rows never enter this comparison at all — a
case's console output is printed only on FAILURE, and nothing failed.

The two new cases also passed in isolation (`--case slab_donate,slab_donate_shared`,
4/4 both arches) on the first run after the `@export("_start")` fix.
