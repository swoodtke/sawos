# SawOS design 7 — Quotas and the reference count (M3 unit 5)

Status: AUTHORED Aug 29 2026 (lead); **BUILT Aug 29 2026** — see "As
built" at the end, which records the four shapes that changed and why,
and the finding that D-4's orphan claim does not hold for the one
exception D-3 itself rules.
Implementing sawlang#232 unit 5
("the table, the counted kinds, `QuotaExceeded`, creator-pays, orphan
write-off"; agenda item 8) TOGETHER WITH the ruled refcount design
from the Aug-29 scoping conversation (the user's Arc question, my
unit-5 recommendation, standing since design 3): the two land as one
unit because the refcount's zero-crossing and the quota credit are one
event, and doing either alone builds half a ledger.

## D-1: The reference count — every slab slot learns who holds it

Each countable slot gains `refs` (UInt16). The reference kinds,
enumerated PER OBJECT KIND (the per-kind matrix style — a new kind
fails to compile until somebody says what references it):

| kind | counted references |
|---|---|
| Event | handle entries (any process) + its attachment |
| Waiter | handle entries |
| Interrupt | handle entries + its attachment |
| Timer | handle entries + its attachment |
| Memory / IoMemory | handle entries |
| Mapping | handle entries |
| Process | handle entries (generalizes design 3 D-3's scan into a count) |
| Thread | NOT COUNTED v1 — the join/exit protocol owns thread-slot lifetime (joiners, the Exited-until-read rule); folding it in is real design with no current payoff. Recorded, deferred. |
| Clock | EXEMPT — kernel-eternal, owned by nobody (the Aug-17 ruling stands) |

Increment sites: `mint_handle`, give's child-side bind, attachment
creation. Decrement sites: release, give's caller-side unbind, the
teardown close-all, `Waiter.Remove`'s detach, waiter-death detach.
The count is maintained at the SAME sites that bind/unbind today —
no new funnels, each site one line.

**ZERO FREES, SYNCHRONOUSLY.** The kernel is one context running to
completion: the count hits zero inside the very syscall that dropped
the last reference, so the free is a plain call — no deferred
reclamation, no cleanup queue. `free_object(kind, slot)` is a
per-kind matrix: return the slot, run the kind's own last rites
(an Interrupt MASKS ITS LINE — the kernel taking the line back
exactly as teardown does today; a Mapping's row is already gone or
it wouldn't be at zero, see D-2), and CREDIT the quota (D-3).
Cascades compose: releasing a Waiter to zero detaches its
attachments, which may drop an Event to zero, which frees and
credits — each step the same two functions.

What zero does NOT free: a RANGE. A freed Memory slot returns to the
slab, but its bytes return to no pool — the front-cut parent cannot
absorb an arbitrary hole (the one-`{base,len}` shape; design 6 D-2).
QUOTAS COUNT OBJECTS, NOT BYTES, v1 — byte accounting arrives with a
real allocator (M4+), and §2.5's pages-free-on-last-reference clause
stays quoted in the spec with this narrowing stated beside it.

**This resolves design 6's recorded deviation**: a Mapping slot now
frees when unmapped AND unreferenced — the aliasing hazard that
forced teardown-only slots is exactly what the count retires, for
every kind at once.

## D-2: Unmap and the count

Unmap removes the ROW (as built); the SLOT frees when `refs` hits
zero. An unmapped-but-referenced Mapping is a husk: `Unmap` on it is
the existing `BadState` fault, release of its last handle frees it.
A mapped-and-unreferenced Mapping (every handle released, row still
installed) is §2.5's "permanent, safe-but-leaked" — the row is NOT a
counted reference to the Mapping (the Mapping owns the row, not the
reverse), so the slot frees and THE ROW STAYS — write this at the
free_object arm, it is the ruled §2.5 sentence made mechanical. The
row is then unremovable until teardown, which is what "leaked" means
and always meant.

## D-3: The quota table — policy above the physical

`ProcessSlot` gains the QUOTA ROWS (the §12-promised "field on the
process slot, checked where NoResource is returned today"): per
counted kind, `used` and `limit`. Charged at allocation (the alloc
sites that answer `NoResource` today), credited at `free_object` and
at teardown — against **the slot's `process` field: creator-pays**,
with ONE ruled exception: **a Mapping charges the TARGET process** —
the scarce thing a mapping consumes is the target's grant-row budget
(agenda item 8's own subject), and charging the caller would let a
launcher's quota gate another process's domain size. Recorded as the
lead ruling it is.

- **`SosStatus.QuotaExceeded = 7`** — the POLICY answer, distinct
  from `NoResource` (the MACHINE's answer: slab full, table full).
  A caller can distinguish "my budget" from "the machine's edge."
  Status, not fault: quota headroom is dynamic resource vocabulary
  (the sharpened faults line's own carve-out).
- Check order at every alloc: quota first (policy refuses before the
  machine is asked), then slab; and at map: quota, then hal row
  budget — **with agenda item 8's assert landed**: the per-kind
  mapping default is chosen ≤ the smaller per-arch free-row budget,
  `static_assert`ed, and map hitting the PHYSICAL wall with quota
  headroom is a `fatal` kernel-bug stop, not a status — the ruled
  sentence, now executable.
- **Defaults**: documented small values in `limits.saw` (lead
  starting points, implementer records as landed): children —
  threads 4, events 4, waiters 2, interrupts 2, timers 4, memories 4,
  iomemories 2, mappings 2, handles 12. ROOT'S LIMITS ARE THE SLAB
  SIZES — root is init; its policy cap IS the machine. The
  handle-count row is design 3's promised addition. No per-create
  quota arguments v1 (an ABI to keep; the launcher-assigns
  vocabulary arrives when something needs non-default children) —
  noted with the process_create receiver ruling's "assigned by
  policy" sentence as the destination.

## D-4: Orphan write-off — the vocabulary lands, the case is degenerate

An orphan is an object whose CHARGED process died first. In v1 none
can exist: nothing gives handles UPWARD (give targets Created
children only), so no object outlives the process charged for it —
teardown credits everything a process was charged for as its slabs
free, and the books balance by construction. The write-off is
therefore ONE sentence at teardown ("credits die with the ledger:
a Gone process's quota rows zero with its slot") plus the recorded
statement that the INTERESTING case — a charged object surviving via
a reference held elsewhere — arrives with M4 pipes, and the credit
site (`free_object`) already handles it: crediting a Gone process's
row is a write into a slot about to be reclaimed, harmless and
stated. No mechanism is invented for a case that cannot yet occur.

## D-5: Transcript accounting — the third authorized movement

Free-on-zero changes teardown counts wherever a case releases (or
drop-releases) the last handle to an object before exiting: that
object no longer appears in its kind's teardown count (it freed
early), exactly as 2.75's drop-release moved `handles=`. The
AUTHORIZED set is derived per case from its release/drop structure —
the implementer owes the same PER-CASE ACCOUNTING TABLE as design 3
D-6 (predicted from scope structure vs observed, both arches), and
the known candidates are the handle-* family, process_reclaim, and
any case whose wrappers die pre-shutdown (the 2.75 table's own
list). Rows outside the accounting are STOP-and-report. The teardown
report's COLUMNS are frozen (no quota column — design 6's precedent).

## The proof (harness)

1. **`quota_exceeded`** — a child creates events to its limit:
   `QuotaExceeded` as a VALUE at limit+1, creation resumes after a
   release frees one (the credit proven), and the machine's slab was
   never the limit that tripped (defaults < slab sizes, asserted in
   the case comment).
2. **`refcount_free`** — mint a second handle onto an Event
   (universal mint), release both: the slot frees only at the second
   release (teardown count says so), and an attachment keeps an
   Event alive past its last handle until `Waiter.Remove` drops it
   (the cascade observed).
3. **`interrupt_unbind`** — release the last Interrupt handle: the
   line masks and the slot frees mid-life (today only teardown does
   this); a re-bind of the same line then SUCCEEDS (the line
   genuinely returned).
4. **`mapping_slot_free`** — unmap + release frees the slot; map
   again reuses it (design 6's deviation shown resolved); the
   released-but-mapped husk arm shows the §2.5 leak sentence.
5. **`quota_vs_wall`** — mappings to the quota limit answer
   `QuotaExceeded` before any arch's physical wall is reachable
   (agenda 8's ordering, observable).

## Docs owed

spec §2 table (the counted kinds column), §2.5 (the narrowing beside
the quoted clause: objects not bytes, v1), §3 (release's "an object
whose last handle is gone is unreachable-but-live until teardown"
sentence AMENDED — that era ends here; the new sentence is
free-on-zero with the §2.5 mapping-husk exception), §11 rows, §12
(quota promise kept); design 3 D-2's same sentence gets the
superseded note; design 6's deviation note gains "resolved by design
7"; limits.saw defaults documented; tracker closed in place;
As-built (refs sites as landed, defaults, the accounting table,
findings; new SL entries if the unit meets any).

## Out of scope

Byte/range accounting and pool returns (M4+ allocator); thread-slot
refcounting (the join protocol owns it, recorded); per-create quota
assignment (the launcher vocabulary, with its process_create-ruling
sentence as destination); quota introspection ops; cross-process
orphans (M4 pipes); any teardown-report column change.

## As built

Status: BUILT Aug 29 2026. Every D above landed. THREE shapes deviate
from the brief's letter and each is argued below with the reason it had
to; one of them contradicts D-4's own claim and is filed as a finding.

Gate: `SAWLANG_ROOT=$HOME/Projects/sawlang make sos-test` under the mkdir
lock — **144 passed across riscv32 + arm64** (72 cases each), against a
baseline of 134 taken at the merge base (f04dea1).

### D-1 — the reference count as landed

`refs: UInt16` on eight slot types: `EventSlot`, `WaiterSlot`,
`InterruptSlot` (`kcore.waitables`), `TimerSlot` (`kcore.time`),
`MemorySlot`, `IoMemorySlot`, `MappingSlot` (`kcore.objects`) and
`ProcessSlot` (`kcore.process`). `ThreadSlot` gained a PARAGRAPH instead
of a field, at the struct, saying why: the join/exit protocol owns a
thread slot's lifetime on terms a count cannot express (`Exited` is a
state a slot stays in so a late join finds the exit code; a joiner holds
no handle and is counted nowhere), so folding it in is real design with
no current payoff. Clock is exempt and System has no slab, each with its
own arm in the matrix.

**THE MATRIX IS A NEW MODULE, `kcore.refs`, between `process` and
`wake`.** Its altitude is decided by its call graph exactly as the
teardown's was in design 2: it reaches every slab — the two memory slabs
and the handle table below it, the Timer slab, the three wait slabs — AND
`PROCESSES`, because a Process's count is what design 3 D-3's reclaim now
reads. Lower it could not see the process table; higher it would sit
above the teardown that frees the same slabs. Three exhaustive matches
over `ObjType` (`ref_object`, `drop_reference`, `free_object`), so a new
kind fails all three to compile until somebody says what references it.

**THE REFS-SITE INVENTORY, AS LANDED** — every line that moves a count,
and nothing else does:

| site | file:function | what |
|---|---|---|
| `+1` | `dispatch.ProcessSelf` | `ref_object(Process, p)` — a second name for one's own slot |
| `+1` | `dispatch.mint_sibling` | `ref_object(entry.obj_type, entry.target)` — `MINT_OP`, any kind |
| `+1 / -1` | `dispatch.move_handle` | give: ref then unref, IN THAT ORDER (the other order would free the object mid-give) |
| `+1` | `dispatch.waiter_add` | `ref_waitable(kind, slot)` — the attachment |
| `-1` | `dispatch.release_handle` | `unref_object(...)`, after the unbind — and this ONE line replaced design 3's `Process` arm |
| `-1` | `dispatch.waiter_remove` | `unref_waitable(...)`, reading kind/target BEFORE the detach clears them |
| `-1` | `sched.end_process` close-all | `unref_teardown(...)` — counts, DOES NOT FREE (see deviation 4 below) |
| `-1` (cascade) | `refs.free_object`'s `Waiter` arm | each attachment's reference, which may free a waitable in turn |
| `= 1` | 7 creation sites | `refs: 1` written in the slot literal: `event_create`, `waiter_create`, `interrupt_bind`, `timer_create`, `Split`, `Carve`, `install_row` |
| `= 1` | `dispatch.process_create` | `PROCESSES[slot].refs = 1` — the launcher's handle |
| `= 1` | `process.mint_boot_regions` | `refs: 1` in both the `MemorySlot` and `IoMemorySlot` literals |
| `+1` | `process.start_process` | root's own Process handle, one increment on its own table |

A CREATION SITE DOES NOT CALL `ref_object`, deliberately: a fresh slot's
first reference is not a change to a count, it IS the count, and writing
`refs: 1` in the literal keeps the all-or-nothing shape those sites
already have. That is also what lets `kcore.process`'s two boot mints
work at all — they sit BELOW `kcore.refs` and could not call it; between
them they need exactly two slot literals and one increment.

`free_object`'s last rites, per kind: **Interrupt** masks its line
(`hal.irq_mask`) and the slot returns, which is what makes the line
bindable again mid-life; **Timer** returns the slot and calls
`reprogram_timer()`, so a dropped deadline stops waking the machine at
the drop rather than at the death; **Waiter** walks its attachment list
whole, freeing each row, clearing each waitable's back pointer and
unref'ing it — the one cascade, which terminates because an attachment
is counted on the WAITABLE and never on the Waiter; **Memory** and
**IoMemory** return a slot and no bytes; **Mapping** per D-2 below;
**Process** calls `reclaim_process_slot`, unchanged except that it now
READS `refs` instead of walking `MAX_PROCESSES × MAX_HANDLES` entries to
recompute it. Event/Interrupt/Timer each carry a `fatal_kernel` assert
that they are not attached at zero — D-1's table written as a check,
unreachable if the table is right.

`ExitCode.KernelBug = 6` and `kcore.diag.fatal_kernel` are new: the third
failure vocabulary, for a state the KERNEL's own rules say is
unreachable, distinct from `fatal_image` (the build wrote those bytes)
and from `fault_process` (the caller was wrong). It has two consumers,
both design 7's: the `free_object` arms above and agenda item 8's assert.

### D-2 — unmap and the count as landed

Unmap removes the row and CREDITS the target's mapping quota; the slot
frees when `refs` hits zero. `free_object`'s `Mapping` arm frees the slot
and LEAVES the row, with §2.5's sentence written at it, and credits
nothing (see deviation 1). `mapping_slot_free` shows both: twelve
map/unmap/drop rounds past a slab of eight, then one map dropped WITHOUT
an unmap whose row still carries a write and a read.

### D-3 — the quota table as landed

`QuotaKind` is a raw-backed enum of nine dense columns — Thread, Event,
Waiter, Interrupt, Timer, Memory, IoMemory, Mapping, Handle — and the
ledger is two flat `[UInt16; MAX_PROCESSES * QUOTA_KINDS]` arrays,
`QUOTA_USED` and `QUOTA_LIMIT`, indexed `p * QUOTA_KINDS + kind`.

**THE DEFAULTS AS LANDED**, in `kcore.limits`, exactly the brief's
numbers: threads 4, events 4, waiters 2, interrupts 2, timers 4, memories
4, iomemories 2, mappings 2, handles 12. Nine `static_assert`s hold each
strictly under its slab (a default at or above its slab would make
`QuotaExceeded` unreachable for that kind and the row a lie), plus
`DEFAULT_QUOTA_MAPPINGS + IMAGE_GRANT_ROWS <= MIN_GRANT_ROW_BUDGET` —
agenda item 8's compile-time half — and `hal.GRANT_ROW_BUDGET >= 8`,
which pins the hand-written minimum against the HAL so the first assert
cannot go stale.

Check order at every alloc site: `quota_room` → `QuotaExceeded`, then the
slab → `NoResource`, then `mint_handle` → `mint_refusal(p)`, which
re-asks the handle row to say WHICH of the two the mint was. The charge
is the LAST thing after every refusal, joining `event_create`'s
all-or-nothing rule: a process must not pay for an object it never got.

The sites, all of them: `ProcessSelf`, `ThreadSelf`, `clock_get_typed`,
`event_create`, `waiter_create`, `interrupt_bind`, `thread_create`,
`timer_create`, `process_create`, `process_start` (charging the CHILD's
thread row), `process_give` (`mint_refusal(child)`), `mint_sibling`,
`install_row` (charging the TARGET), `Split`, `Carve`.

`SosStatus.QuotaExceeded = 7`, with the policy-vs-machine docstring and a
`describe()` arm. **No wrapper changed and no op was added**: `checked`
decodes through `SosStatus.from(raw:)` and `is_failure()`, so the new tag
reaches userspace as a typed `Err` with nothing edited — which the two
child cases spend by MATCHING THE CASE (`case QuotaExceeded -> true`)
rather than comparing a number.

### DEVIATION 1 — THE MAPPING ROW COUNTS INSTALLED ROWS, NOT OBJECTS

D-3 says the quota is credited at `free_object`. For every other kind it
is. For Mapping it is credited at `Unmap` and at the target's teardown
instead, and the object-based reading had to go because **it makes
agenda item 8's assert false.**

The sequence: map (row installed, charged), release the Mapping handle
(D-2: the slot frees and the row STAYS). Under object-based accounting
that release CREDITS, so `used` falls while `grant_count` does not.
Repeat, and a process reaches the physical row wall with its ledger
reading zero — which is precisely the state D-3 rules a `fatal` kernel
bug, reached by an ordinary userspace sequence. Counting ROWS instead
keeps `used` and `grant_count - image rows` equal by construction:
`install_row` charges one and appends one, `Unmap` credits one and
removes one, the teardown zeroes both. `child-mapwall` is the case that
exercises exactly this — it drops every Mapping it makes and is still
refused at three, which only a row-counting ledger can do.

It is also what D-3's own justification argues for: "the scarce thing a
mapping consumes is the target's grant-row budget."

### DEVIATION 2 — ROOT'S ROWS ARE `QUOTA_UNLIMITED`, NOT THE SLAB SIZES

D-3 says "ROOT'S LIMITS ARE THE SLAB SIZES — root is init; its policy cap
IS the machine." Written as the numbers, that sentence inverts itself.
The quota is asked FIRST, so a limit numerically equal to the slab
refuses at exactly the point the slab would have — and the refusal comes
out as `QuotaExceeded`. Every `NoResource` root has ever been answered
with becomes a policy refusal: the machine's edge speaking in policy's
voice, which is the opposite of what the sentence says. `memory_split`'s
own line is the concrete casualty.

So root's rows are a sentinel meaning "no policy cap — the MACHINE
answers", which is the sentence executed rather than approximated, and
`NoResource` stays root's refusal everywhere it already was.

**ONE EXCEPTION, and it is agenda item 8's runtime half**: root's MAPPING
row is clamped to `hal.GRANT_ROW_BUDGET - grant_count` at
`install_quota_limits`, as every process's is. That clamp is what makes
"quota limit ≤ free rows" true for EVERY process by construction, and
therefore what makes the `fatal` unreachable rather than merely unlikely
— a compile-time assert cannot know how many rows an image spends. Root's
mapping cap being the machine's row budget is not a departure from the
ruling; it is the ruling, stated in the ledger's vocabulary so the one
refusal a `map` can meet is the one the assert reasons about.

### DEVIATION 3 — THE LEDGER LIVES IN `kcore.objects`, NOT ON `ProcessSlot`

§12 promised "a field on the process slot" and D-3 repeats it. The rows
are per-process storage one module lower, and the module ORDER decides
it rather than taste: the HANDLE row (design 3's promised addition, and
the one kind whose "slab" is a region of a per-process table) has to be
charged inside `mint_handle` and credited inside `unbind_handle` — both
in `kcore.objects` — and `kcore.process` sits ABOVE that file. A table on
`ProcessSlot` could not be reached from the two functions that most need
it, and the alternative was a second funnel over "the one place a binding
is destroyed", which is a property this tree values more than the
promise's exact wording. The flat `p * QUOTA_KINDS + kind` array is the
handle table's and the boot-handle queue's own idiom, in the file whose
docstring already states it. `ProcessSlot`'s `refs` docstring and §12
both point at the real location.

### DEVIATION 4 — THE TEARDOWN'S CLOSE-ALL COUNTS BUT DOES NOT FREE

D-1 lists the teardown close-all as a decrement site. It is one —
`unref_teardown` — but it does not free at zero, and the asymmetry is
what keeps D-5's authorized set the size D-5 says it is. A teardown frees
what a process OWNED, by ownership, in per-slab sweeps that also COUNT
what they freed for the report. A close-all that freed by reference would
collapse every one of those counts for objects being reclaimed either
way, in the same function, microseconds apart — and would move every
teardown line in the suite, which D-5 authorizes for pre-exit releases
and for nothing else. So the close-all keeps the ledger honest (a handle
in a dying table really is one reference fewer, and objects OTHER
processes own must see that) and the sweeps below it free as they always
have. For the dying process's own objects the two agree by construction:
the sweep zeroes the slot, count included.

### D-5 — the per-case accounting table

**THE THIRD AUTHORIZED MOVEMENT TURNED OUT TO BE EMPTY, and that is the
result rather than an absence of work.** D-5 predicted teardown counts
would move wherever a case releases or drop-releases the last handle to
an object before exiting. Predicted per case from release/drop structure,
then observed: **no teardown count moved, on either architecture.** The
reason is a property of the suite the prediction did not have in view:

- the `handle-*` family drops CLOCK handles, and a Clock is EXEMPT
  (kernel-eternal, owned by nobody), so nothing frees and nothing moves.
  `handle_drop_release`'s `handles={five}` is unchanged.
- `process_reclaim` releases a child's Process handle, which design 3
  already reclaimed by scan; design 7 reaches the same answer by count,
  so the transcript is byte-identical.
- `process_bootdrain` and `give_boot_drain`'s child DO drop the last
  handle to a countable object — boot `Memory` records they decline —
  and those slots really do free early now. **The teardown line has no
  `memories=` column** (design 6's finding 6 declined to add one), so
  the movement is real and invisible.
- every Event/Waiter/Timer case holds its wrappers to `shutdown()`,
  which never returns, so no deinit runs and the teardown counts them
  exactly as before.

**THE ONE ASSERTED LINE THAT MOVED IS `memory_split`'s**, and it is a
case CLAIM rather than a teardown count: `pool exhausted after 13 cuts` →
`cuts=20 refused=0`. Design 6's finding 5 tied that number to this unit
by name ("free-on-last-reference is unit 5's and a dropped piece returns
a slot to nobody... raising the slab should move a line somebody reads"),
and unit 5 is what made it false. The case's loop now runs twenty
cut-and-drop rounds past a slab of sixteen; what it would eventually meet
is the POOL, not the slab, which is §2.5's narrowing observable.

**THE FULL BUCKETED ACCOUNT of the 134 pre-existing rows**, classified
mechanically (the classifier normalizes exactly three fields — `entry=`,
`epc=`/`elr=`, `at 0x…` — and deliberately leaves `tval=` alone, design 6
finding 8's own tool):

| bucket | rows | cause |
|---|---|---|
| byte-identical | 107 | — |
| ADDRESS ONLY | 23 | the `sos` module grew by a status's worth of decode and the kernel by a module, so every root image's entry and the kernel's own code moved. No `tval=`, no `segments=`, no teardown count, no status word and no program's own output differs in any of them |
| AUTHORIZED | 2 | `memory_split` on both arches — the one line above, accounted for by name |
| DOCUMENTED NON-DETERMINISM | 2 | `thread_preempt`, both arches |

- `thread_preempt`, arm64 — the spinner INTERLEAVING
  (`ABABABBABABAAB` → `ABABABABBABAAB`). The case asserts DIRECTION
  CHANGES, never a sequence, and its comment says why: which worker runs
  first depends on where the first tick lands relative to two `start`
  calls. `joined a=33 b=44` is unchanged.
- `thread_preempt`, riscv32 — where root's banner interleaves MID-WORD
  with the tick narration. Same cause, same non-assertion.

**AND A THIRD ROW MOVED IN ONE RUN AND NOT THE OTHER, WHICH IS THE
STRONGEST EVIDENCE THE BUCKET COULD ASK FOR.** Two full runs of the
CHANGED tree were taken (the first caught two expectation mistakes of
mine, both handle counts I had mispredicted; the second is the gate
above). `timer_interval` on riscv32 read `tick one fires=1` → `fires=2`
in the first and was byte-identical in the second, off the same kernel —
so the number differs between two runs of ONE build, which is exactly
what its comment says IN CAPITALS is not asserted and why: a fire
delivered more than a period late coalesces BY DESIGN, and pinning the
count would make the feature itself the flake. Design 6 saw the same
number move the other way (2 → 1). `tick two fires=3 ackfree=1` — the
deterministic half — is unchanged in both runs.

### The proof — five cases, ten new rows

| case | shape | claim |
|---|---|---|
| `refcount_free` | root, C altitude | a SIBLING handle keeps an Event past the first release (`sibling word=42`); an ATTACHMENT keeps a second past its LAST (`attached key=77 word=9`); and the teardown counts `events={zero} waiters={one}` — two events made, both freed mid-life, and the Waiter that outlived one of them |
| `interrupt_unbind` | root, typed | releasing the last Interrupt handle masks the line and frees the slot, so the SAME line binds again — a `LineBound` FAULT before this unit, so the case could not have been written to survive |
| `mapping_slot_free` | root, pool | `rounds=12 refused=0` (past a slab of 8 AND past root's row allowance, so the slot and the quota row both come back); `husk wrote 94 read 94` through a row whose Mapping no longer exists |
| `quota_exceeded` | launcher + `child-quota` | `made=4 quota=1 resumed=1` — the policy refusal at limit+1, and creation RESUMING after one release. `MAX_EVENTS` is 8 and root makes none, so half the slab was free at the refusal: the machine was demonstrably not what stopped it |
| `quota_vs_wall` | launcher + `child-mapwall` | `maps=2 quota=1` — agenda item 8's ordering, IDENTICAL on both profiles, with the wall never reached (a clean run IS the proof, since meeting it is a `fatal_kernel` stop) |

**WHY TWO OF THE FIVE NEED A CHILD**, which is not a shape the brief
named: **root cannot meet a quota.** Deviation 2 makes root's rows
unlimited, so only a launched process has a budget to exceed — and that
is the ruling working, not a limitation. It also bought the quota cases
something better than they would have had: a child's mapping allowance is
the same number on both profiles (two, against five free rows on riscv32
and six on arm64), where root's IS its free row count and differs per
machine, so `maps=2` is one expectation for two architectures.

`refcount_free` reaches the C altitude for `handle_remint`'s reason: the
typed layer has no word extractor and `mint` exists only on `System`, so
"a second handle onto this Event" is unspellable there. Every pointer is
confined to a small named `unsafe` helper, which is `timer_badrecord`'s
arrangement.

### Findings

1. **D-4 IS WRONG ABOUT v1, AND THE RULED MAPPING EXCEPTION IS WHY.**
   D-4 says no orphan can exist because "nothing gives handles UPWARD, so
   no object outlives the process charged for it". That holds for every
   kind charged under creator-pays — and a Mapping is charged to its
   TARGET, which is exactly the case where the charged process can die
   FIRST: a launcher maps into a child, the child dies, and the launcher
   still holds the Mapping handle (design 6's "the teardown comes apart
   from both ends"). Under the brief's object-based credit that handle's
   release would later credit a slot that may by then hold a DIFFERENT
   process — a live process's quota silently decremented by a stranger's
   release. Deviation 1 closes it structurally rather than by a guard:
   the credit follows the ROW, the row died with the target's domain, and
   the target's own `quota_clear` already wrote the charge off. Recorded
   because the ruling and its justification pointed in opposite
   directions and only one of them survives.

2. **FREEING A WAITER STRANDS ANY THREAD PARKED ON IT.** A Waiter at zero
   references detaches its list and returns its slot; threads on its
   `blocked` list are not woken and never will be. It is reachable only
   by a program releasing the last handle to a Waiter a SIBLING THREAD is
   parked on — which is the same class of self-inflicted mistake as
   releasing your own System handle, is diagnosed by the existing
   deadlock report rather than being silent, and corrupts nothing (a
   reused slot starts with an empty blocked list; the stranded thread
   stays `Blocked` forever). Written at the arm and recorded here rather
   than guarded, because the alternative — a Waiter husk that never frees
   — is a deviation from D-1's "zero frees" that no case yet needs. A
   unit that wants it should rule on what a parked thread is WORTH to the
   count.

3. **`fatal_kernel` IS NOT `-> Never`**, so `install_row`'s assert arm
   still owes a `return` — design 6's finding 4 met at a second site,
   for a second function, and worked around the same way (a note at the
   line). The signature change is a real decision nobody has ruled on.

4. **NO COMPILER DEFECT WAS HIT, AND NO NEW `SL-` ENTRY IS FILED.** Every
   Saw construct this unit needed worked as the skill documents on the
   pinned toolchain: a nine-case raw-backed enum indexing two flat
   arrays, a mutually recursive pair (`unref_object` / `free_object`)
   across an exhaustive `ObjType` match, `UInt16` arithmetic with bare
   literals adopting, `as UInt16` on an `Int` static in a call argument,
   a tuple return out of an `unsafe` helper, and a NoCopy wrapper bound
   BY A MATCH ARM and dropped at the arm's end (which is what
   `mapping_slot_free`'s round-trip loop rests on). The one shape avoided
   was a `continue` inside a match arm feeding a `let` — not tested, and
   the arm-scoped restructure reads better anyway.

### Docs updated

spec §2 (the COUNTED-KINDS column, as a table after the eleven-kinds
paragraph, with the Thread deferral and the Clock exemption stated), the
Process row (design 3 D-3's mechanism superseded, its answer unchanged,
and "the ONLY slab that reclaims on release" retired); §2.5 (the BUILT
block: the refcount exists, quotas count OBJECTS not bytes with the
quoted clause left standing beside the narrowing, and the mapping-husk
ruling); §3's release bullet (the "unreachable-but-live until teardown"
sentence amended to free-on-zero, with the per-kind last rites and the
Mapping exception); §11's quota entry (BUILT, with creator-pays, the
Mapping exception, root's unlimited rows and the clamp) and its lifecycle
entry (the deferred refcounted reclamation is built; what stays absent
named); §12's "a quota stays ADDITIVE" promise (kept exactly — no rights
bit moved, no op added — with deviation 3's placement stated).

designs/003 D-2's sentence carries its SUPERSEDED note; designs/006's
deviation 1 is marked RESOLVED at its head and its finding 5 records that
unit 5 moved the line it predicted would move. `kcore.limits` documents
the nine defaults and the two agenda-8 asserts; `QuotaKind`,
`QUOTA_UNLIMITED` and `install_quota_limits` carry the deviations'
arguments at the code. Tracker closed in place.
