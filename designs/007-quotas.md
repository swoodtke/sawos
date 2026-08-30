# SawOS design 7 — Quotas and the reference count (M3 unit 5)

Status: AUTHORED Aug 29 2026 (lead), implementing sawlang#232 unit 5
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

(Implementer: everything delegated, the accounting table, findings.)
