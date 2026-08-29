# SawOS design 3 — The handle lifecycle (M3 unit 2.75)

Status: AUTHORED Aug 29 2026 (lead), implementing the Aug-17 user
ruling (sawlang#232, "THE HANDLE-LIFECYCLE RULING") — mint-per-call,
an UNGATED release, and best-effort GENERATIONS, landing TOGETHER
because each alone is broken: mint-per-call without release is a leak
by design; release without generations is aliasing. Plus one Aug-29
user ruling taken during scoping: **DROP IS RELEASE** — the sysapi
wrappers flip to NoCopy + deinit in THIS unit, because a release op
beside copyable wrappers is a stale-fault factory (copy, drop, and
the sibling's next use is a manufactured BadHandle), and an explicit
`release()` method beside RAII wrappers is redundant surface. The
kernel mechanism and the wrapper discipline are one unit.

## D-1: The word — a CONFIGURABLE index field, the rest generation, portable

`word = (gen << HANDLE_INDEX_BITS) | (index + 1)`, split IDENTICAL on
both profiles (the remaining bits of a portable 32-bit budget are
generation, wrapping; the top 32 bits of an arm64 word stay zero).
Same width both arches means the wrap behaves identically everywhere
— one story, one test. Index keeps its 1-based spelling and
`NO_HANDLE = 0` still names nothing at any generation.

**THE SPLIT IS ONE NAMED CONSTANT, NOT A HARDCODED SHIFT** (user
note, Aug 29, amending this brief's original "low 8 index, 24 bits of
generation"): `HANDLE_INDEX_BITS` — default 8 — lives in `sosabi`
beside `NO_HANDLE`, since it defines what a handle word IS, and
everything else derives from it per the design-185/186 const idiom:
`HANDLE_INDEX_MASK = (1 << HANDLE_INDEX_BITS) - 1`, the generation
shift IS `HANDLE_INDEX_BITS`, and the generation width is the
remaining bits of the budget. No `0xFF` and no `>> 8` appears
anywhere. `static_assert`s guard it: each field at least one bit, the
two exactly dividing the portable word, and `MAX_HANDLES <=
HANDLE_INDEX_MASK` (the `<=` is the 1-based index — the field must
hold `MAX_HANDLES` itself). RATIONALE: as systems grow, ~254
per-process indices may not suffice, and the split is policy stated
once rather than arithmetic scattered — raising the constant must be
a one-line edit that moves every mask, shift and assert with it.

**GENERATION 0 STAMPS NOTHING**: a slot's first-life word equals the
bare index, so every existing contract survives verbatim — the §12
boot order (1 = System, 2 = Process, 3 = Thread) the nine asm
payloads hard-code, and `root_server_boot`'s "boot handle 1". Only a
REUSED slot's word grows high bits, and no reuse exists until release
does.

Mechanics: the slot's generation lives in the handle entry and
SURVIVES the unbind — release and the teardown's close-all zero the
binding and INCREMENT the generation (wrapping at 2^24), so the next
mint in that slot stamps a fresh word. Lookup splits the word, range-
checks the index, and requires entry-bound AND generation-equal;
anything else is the existing Unbound answer, which the callers
already turn into a `BadHandle` FAULT. Per the ruling, verbatim:
BEST-EFFORT detection of a bug, not a uniqueness guarantee — a word
held across exactly 2^24 reuses of one slot aliases silently, tables
are per-process so a wrap collision crosses no boundary and grants
nothing, and nobody later "fixes" this into unbounded bookkeeping.

## D-2: Release — one UNIVERSAL op, intercepted before the kind

Release is the same act for every kind, so it is not nine enum cases:
sosabi reserves ONE universal op number, `RELEASE_OP = 0xFFFF`,
documented as forever outside every per-object table (all of which
are dense from 0), and `dispatch()` intercepts it between the lookup
and the per-kind match — the one point that knows the entry and not
yet the kind. Semantics, per the ruling:

- UNGATED — destroying your own capability instance harms no one;
  identity lives in the object's slot, not the handle. No right is
  checked. (If a gate is ever wanted it is a NEW universal bit, never
  a retasking of Manage.)
- Releasing an Unbound or stale word is the ordinary `BadHandle`
  FAULT — using a handle you released is broken code, the sharpened
  line. Releasing `NO_HANDLE` likewise.
- Release unbinds the ENTRY and bumps the generation; it NEVER
  touches the object — the teardown close-all's own precedent. An
  object whose last handle is gone is unreachable-but-live until its
  process's teardown; quotas (unit 5) are where that costs something.
  ONE exception, D-3.
- Releasing your System handle or your own Process handle is
  legal-but-doomed, the sandboxed-compute precedent: you lose the
  authority, that is what you asked for.
- CLOSE is not built and not this op: close is object-protocol (ends
  the thing for everyone), exists only on kinds with an end-state,
  arrives with Pipe in M4, per-kind right per the convention.

## D-3: The one reclamation — a Gone process's slot

Unit 2 recorded the pend at `alloc_process`: a dead slot is never
spent again until 2.75, because the creator's handle must keep
answering `get_status`. Release is the missing reader-count: when a
released entry named a PROCESS whose slot is `Gone`, scan the handle
tables for any other reference to that slot — none left means the §8
status word has no possible reader, and the slot goes `Free`. The
same check runs at the END of a process's own teardown for its own
slot (the dying process's handles are closed there; the CREATOR's
handle is what usually keeps the slot). Bounded: MAX_PROCESSES ×
MAX_HANDLES = 32 entries. `clear_domain` already invalidated
`LAST_PROT_PROCESS` in anticipation, and D-1's generations are what
make the reused slot safe. `MAX_PROCESSES` goes back to bounding
CONCURRENT processes, which is what the name says.

No other slab reclaims on release in this unit — Events, Waiters,
Timers, Memories still free only at teardown. The Process slot is
special because unit 2 explicitly queued it and because its "may I
free" question has a precise answer (no referencing handle) where the
others need refcounts nothing yet justifies.

## D-4: Mint-per-call — the flip, and what it deletes

Every getter of an existing object MINTS A FRESH HANDLE carrying the
kind's DEFAULT rights. A handle is a capability INSTANCE,
independently held and independently released — the RAII foundation.

- `clock_get`: `clock_handle_of` (the tree's only by-kind scan of
  HANDLES) is DELETED; `clock_get_typed` becomes pure minting, and
  its `NoResource` path is now reachable on repetition — documented,
  quota row at unit 5.
- `process_self`: stops reading the cached word; mints fresh with the
  kind's default set (v1 default = today's `root_process_rights()`
  vocabulary; which bits a CHILD's default withholds is unit 3+'s
  question, noted not answered). `ProcessSlot.self_handle` loses its
  last reader and IS DELETED (boot still mints root's fixed 1/2/3).
- `thread_self`: same flip; `ThreadSlot.self_handle` DELETED.
- `boot_handle_next` is NOT a getter — it delivers an already-minted
  word exactly once; unchanged.

The §3 story this owes (compact amendment now; the full rewrite stays
unit 7 per the ruling): the no-duplicate rule's real invariant was
never uniqueness — it is NO AMPLIFICATION. A mint always carries the
kind's DEFAULT set and minting authority is itself rights-gated
(`ClockGet`, `Manage`), so attenuating a handle you give away is
meaningful exactly when the receiver lacks its own minting authority.
Stated with the clock example so mint-per-call is never read as a
hole in attenuation.

## D-5: The wrapper flip — drop is release (Aug-29 ruling)

All nine sysapi wrappers become `NoCopy` with one hand-written
deinit; the raw word field doubles as the disarm flag because
`NO_HANDLE` is unrepresentable as a live handle:

```saw
func deinit(&var self) {
    if self.raw != NO_HANDLE { let _ = sos_handle_release(self.raw) }
}
```

- NO typed `release()` method exists — early release is dropping the
  value (`let _ = move w`); the C seam `sos_handle_release` is the
  syscall the deinit invokes and the only spelling of release in the
  module.
- **THE TRANSFER-FUNNEL CONTRACT, recorded now for unit 3 and M4**:
  when a kernel op MOVES the word out of the caller's table (give, a
  pipe send), the sysapi funnel that owns the syscall consumes the
  wrapper, reads the word, sets the field to `NO_HANDLE` BEFORE the
  syscall, and lets the disarmed value drop. Disarm-before-syscall is
  deliberate: every failure of the syscall is a fault (the process
  ends, teardown covers everything), so there is no path where a
  disarmed-but-unsent word leaks. User code never touches the
  sentinel. Generations backstop the discipline: a wrapper bug that
  releases after a transfer is a diagnosed `BadHandle` fault, never
  corruption.
- `shutdown()` never returns, so wrappers in scope at shutdown never
  run deinit and die in kernel teardown instead — the backstop
  working as designed; transcript handle counts say which path each
  handle took.
- The `BootHandle` record: exactly-one-wrapper-per-word is the
  constraint. Candidate shapes, implementer's pick recorded in the
  As-built: `memory: Memory?` + a `take_memory(&var self)` accessor
  (Optional.take is exactly this tool), or the record staying raw
  words behind a NoCopy shell with a sentinel-swap accessor. A
  copyable record with a wrapper-minting method is REFUSED — two
  copies would mint two owners for one word.
- `sosabi`'s `SystemHandle` docstring and sysapi's `Memory`/`System`
  docstrings all prophesied this flip by unit number; their prophecy
  paragraphs get flipped to the present tense.

## D-6: The transcripts MOVE, each row authorized by name

The first unit whose claims change shipped rows. Two causes: getters
mint (counts rise), and wrappers dropped before `shutdown()` release
(counts fall). The AUTHORIZED set, from the census — `clock_basics`
(the `handles={five}`-after-two-asks row WAS the same-handle model's
recorded evidence; its payload and case comments flip to assert the
mint-per-call claim, including that the second ask's word DIFFERS),
`thread_basics`, `event_basics`, `event_dupkey`, `event_consume`,
`irq_bad_line`, `irq_early_ack`, `wait_deadlock`, `timer_oneshot`,
`timer_interval`, `timer_deadlock` — teardown `handles=` counts only,
plus any placeholder rows the runner needs above `{seven}`. Every
other expected line in the suite is UNCHANGED, notably: both child
teardown lines (`handles={zero}`), `process_doublestart`'s
count-free prefix, `root_server_boot`'s "boot handle 1", and every
asm payload's 1/2/3 contract (D-1's gen-0 rule). The implementer owes
a PER-CASE ACCOUNTING TABLE in the As-built: predicted count from the
case's scope structure (which wrappers die before shutdown, which
survive to teardown), and the observed transcript agreeing, both
arches. A row that moves outside the authorized set is a STOP — a
finding, not an expectation edit.

## The proof (harness)

New all-arch cases; claims, each impossible or silently wrong today:

1. **`handle_remint`** — release a handle, mint again: the slot
   REUSES (lowest-free order, same index), the WORD DIFFERS (the
   generation moved), and the OLD word faults `BadHandle` — the
   money proof that stale-use is a diagnosed bug. Fault transcript
   names the process; expect the fault path.
2. **`handle_release_ungated`** — release succeeds through a handle
   whose rights are ZERO... no attenuation op exists yet to build
   one; instead: release a freshly-minted clock handle (whose kind's
   rights gate nothing about release) and re-ask — release is
   ungated and mint-per-call refills. Also: releasing `NO_HANDLE`
   faults; double-release faults (second is stale).
3. **`process_reclaim`** — create a child, run it to death, release
   the child's Process handle, create ANOTHER child successfully:
   the Gone slot reclaimed (today this is `NoResource`), and the old
   child handle word faults after release. Uses the existing
   child-fault package.
4. **Drop-is-release visible**: a case (or an arm of 1) where a
   wrapper dies in an inner scope and the teardown count is LOWER
   than the mint count — the deinit seam observed end to end.

## Docs owed

- spec §3: the compact no-amplification amendment (D-4); §11 ledger
  (deferred close/generations tier: BUILT); §2 table Process row
  note (slot reclaim); the `clock_get` "same handle" sentences in
  §9-era text and the unit-1 as-built claims SUPERSEDED notes where
  spec restates them.
- sosabi: `RELEASE_OP` + its universal-forever rule; generation
  split documented at the handle aliases; `SystemHandle` prophecy
  flipped; `SosStatus`/`FaultReason` untouched (release reuses
  `BadHandle`).
- sysapi: the nine wrapper docstrings (owning tier arrives), the
  transfer-funnel contract comment at the deinit.
- `dispatch.saw`'s `clock_get_typed`/`ProcessSelf`/`ThreadSelf`
  docstrings flip; `alloc_process`'s "never spent again" paragraph
  updates to the D-3 rule; `clear_domain`'s "without handle
  generations" sentence updates (they exist now).
- Tracker closed in place; As-built filled (split as landed, the
  accounting table, findings).

## Out of scope

close (M4 Pipe); refcounted object reclamation on last release
(quotas era); per-kind release rights (would be a new universal bit,
explicitly not built); give/transfer (unit 3 — but the funnel
contract above is written for it); quota rows for the newly-fallible
getters (unit 5); the full §3 rewrite (unit 7).

## As built

Landed Aug 29. Gate: `SAWLANG_ROOT=$HOME/Projects/sawlang make
sos-test` — **108 passed across riscv32 + arm64** (54 cases each),
against a baseline of 94 (47 cases each) taken at the merge base.

### The split as landed (D-1)

`HANDLE_INDEX_BITS = 8` in `sosabi`, beside `NO_HANDLE`, with
`HANDLE_WORD_BITS = 32`, `HANDLE_GENERATION_BITS = 32 - INDEX_BITS`,
`HANDLE_INDEX_MASK` and `HANDLE_GENERATION_MODULUS = 1 <<
GENERATION_BITS` all derived from it. Three `static_assert`s in
`sosabi` (each field ≥ 1 bit; the two exactly divide the portable
word) and one in `kcore.objects` (`MAX_HANDLES <= (1 <<
HANDLE_INDEX_BITS) - 1` — `<=` because the field carries the 1-based
index, so it must hold `MAX_HANDLES` itself). No `0xFF` and no `>> 8`
appears anywhere in the kernel or sysapi.

Two mechanics differ from the brief's letter, both hardening:

- **`handle_word_generation` answers in the WORD's own width, not
  `UInt32`.** Narrowing with `as` would panic INSIDE THE KERNEL on a
  64-bit profile over a word a process chose; masking to 24 bits would
  be worse, making a word with garbage in the top half compare EQUAL to
  a low generation and RESOLVE. Answering the whole field makes the
  equality test in `lookup_handle` do the range check for free: a
  stored generation is always below the modulus, so a word claiming
  more is equal to no entry. That turns D-1's "the top 32 bits of an
  arm64 word stay zero" from an assumption into an enforced rule.
- **The malformed-word rule is a named function, not an inline test.**
  `handle_word_index` answers `MAX_HANDLES` for an index field of zero,
  so the ONE range test below it covers both refusals — a word whose
  field is zero at any generation (`0x100`) and one past the table.

`unbind_handle(p, index)` is the single place a binding is destroyed;
the release op and the teardown's close-all both call it, which is what
makes "release and teardown do the same thing to a slot" a fact rather
than two implementations that agree today.

### RELEASE_OP mechanics (D-2, D-3)

`RELEASE_OP: UInt = 0xFFFF` in `sosabi`, with eight `static_assert`s —
one per op table's highest case — holding the dense-from-zero rule that
makes one universal number safe. `dispatch()` intercepts it between
`lookup_handle` and the kind `match`; `release_handle` faults
`BadHandle` on `Unbound`, else unbinds and, when the entry named a
`Process`, calls `reclaim_process_slot(entry.target)`. The reclaim is
asked AFTER the unbind, because the entry being released is one the
scan must not find. `reclaim_process_slot` also runs at the END of
`end_process`, after `clear_domain`, for the dying process's own slot.

### The BootHandle shape chosen (D-5)

**The brief's lean pick: `memory: Memory?` (private) +
`take_memory(&var self) -> Memory?`**, on `extension BootHandle:
NoCopy {}`. The rejected alternative is recorded at the declaration: a
copyable record with a wrapper-minting method would be two owners for
one word. The plain `memory: Memory` field was also possible — NoCopy
on the record satisfies exactly-one-wrapper either way — but it pins the
region's life to the RECORD's, since a NoCopy field cannot be moved out
of a struct. The Optional is what lets a launcher own the region past
the iterator entry, which `process-reclaim` needs (it uses both regions
twice).

A record whose region is never TAKEN releases it when the record drops.
That is the right default and it shows up unasserted in
`process_bootdrain`, whose count FELL by two.

**Param-mutability probe, as the brief asked:** a by-value parameter
needs no derived-shadow rebind here, because the accessor is
`&var self` and `Optional.take` writes through it — the field case
`move` cannot do. The consumer cost is one word: `let image` became
`var image` in five packages, plus `image.take_memory()!`.

### The per-case transcript accounting table (D-6)

Predicted from each case's scope structure, observed on BOTH arches
(every row below was byte-identical in the two transcripts apart from
build addresses). `+1 self` is the `process_self()` mint that used to
read a cached word; `+2 derive` is a worker thread's own
`process_self` + `thread_self`.

**ASSERTED rows — D-6's authorized set, all eleven, and nothing else:**

| case | was | now | why |
|---|---|---|---|
| `thread_basics` | 5 | 10 | +1 self, +2 derive × 2 workers |
| `event_basics` | 7 | 8 | +1 self |
| `event_dupkey` | 6 | 7 | +1 self |
| `event_consume` | 6 | 7 | +1 self |
| `irq_bad_line` | 3 | 4 | +1 self |
| `irq_early_ack` | 4 | 5 | +1 self |
| `wait_deadlock` | 5 | 6 | +1 self |
| `timer_oneshot` | 6 | 7 | +1 self |
| `timer_interval` | 6 | 7 | +1 self |
| `timer_deadlock` | 6 | 7 | +1 self |
| `clock_basics` | 5 | 8 | +1 self, +2 more Clock asks (three asks, three handles) |

`clock_basics` also flips its claim line, `same=1` →
`minted=1 differs=1`, which D-6 authorized by name.

**UNASSERTED transcript lines that moved.** The runner asserts no
`handles=` count on these, so no expectation was edited — but the
movement is accounted for here, since a transcript diff shows it:

| case | was | now | why |
|---|---|---|---|
| `event_wake` | 6 | 9 | +1 self, +2 derive (worker) |
| `event_consume_wake` | 8 | 11 | +1 self, +2 derive (worker) |
| `process_badimage` | 5 | 6 | +1 self |
| `process_doublestart` | 6 | 7 | +1 self (its asserted prefix is count-free, exactly as D-6 predicted) |
| `process_isolation` | 9 | 10 | +1 self |
| `process_lifecycle` | 9 | 10 | +1 self |
| `process_bootdrain` | 5 | **4** | +1 self, **−2**: it never TAKES either region, so both records drop and release them |

`thread_preempt` (both arches) and `timer_tick` / `trap_fault` moved
only in interleaving and code addresses. The preempt case asserts
direction changes (`AB`/`BA`/`AB`), never a fixed sequence, and those
still hold; ten more transcripts moved in build addresses ONLY.

**Rows that did NOT move, checked explicitly:** `root_server_boot`'s
"boot handle 1"; both child teardown lines (`handles={zero}`); every
hand-assembled payload's 1/2/3 (`umode_*`, D-1's gen-0 rule doing its
job); 44 transcripts byte-identical.

### New proof cases (7, both arches)

`handle_remint` (the money proof: release, re-mint, `differs=1
reused=1`, then the old word FAULTS); `handle_release_ungated` (the
only clean exit — release is an ordinary successful op, and the slot
refills); `handle_release_nothing`, `handle_release_twice`,
`handle_malformed_word` (three shapes of "this word resolves to
nothing", one per case because a fault ends the process);
`handle_drop_release` (the TYPED side — four Clock handles minted,
three die in an inner scope, teardown counts five); `process_reclaim`
(`refused_before=1 reclaimed_after=1` — both sides of D-3 in one line).

`reused=1` is asserted WITHOUT naming the split, which matters now that
`HANDLE_INDEX_BITS` is configurable: a reused slot's word carries
generation bits and is therefore GREATER than any first-life word, so
the case mints three handles and compares the re-mint against a fresh
one. A case that hardcoded `0xFF` would silently stop checking the day
the constant moved.

### Findings

1. **A platform-`UInt` static does not adopt an `Int`-domain constant
   expression.** `static M: UInt = (1 << BITS) - 1` over a
   `static BITS: Int` is refused — "initializer has type `Int`" —
   because DF-240a's const adoption reaches FIXED-WIDTH slots and
   platform `Int`/`UInt` are not among them. Minimal example:
   ```saw
   static BITS: Int = 8
   static MASK: UInt = (1 << BITS) - 1   // error
   static MASK: UInt = ((1 << BITS) - 1) as UInt   // compiles, folds
   ```
   Written `as UInt` at the definition with the reason beside it. Not
   a defect to file against the compiler so much as a documented
   asymmetry worth knowing; the workaround costs nothing (design 170
   answers a constant cast at compile time).
2. **No op mints a second handle onto an object a process already
   owns** — the getters that mint are System's and Process's, so a
   process cannot derive a second `Event`/`Waiter`/`Timer`/`Interrupt`
   handle for a sibling thread.
   **OWNER: UNIT 5.5** (recorded by design 4 D-3, Aug 29, and sharpened
   by building it). Unit 3 met this finding from the other side and it
   is now the launch flow's one real limitation: a child that is to
   drain must hold its OWN Process handle, the only such handle is the
   one `process_create` minted, and `give` is a MOVE — so a launcher
   either KEEPS it and supervises (`get_status`, and 5.5's death-wait)
   or HANDS IT OVER and donates, and cannot do both. Unit 3 found the
   ordering half of this too: because `Start` is an op on that same
   handle, the donation cannot even be a separate give — a launcher
   that gave it away a moment earlier would have nothing left to start
   the child with — so it happens AT the start barrier
   (`BootTagForm.Donate`). A second handle onto one process is what
   would dissolve both halves, and death notifications are the unit
   that genuinely needs root to retain while the child holds its own. Before the owning tier this was hidden:
   `event-wake` and `event-consume-wake` shared the COPYABLE wrapper
   value through an `unsafe static var`, which NoCopy correctly
   refuses (and which a static could not hold anyway — statics are
   immortal and never run a deinit). Both were rewritten to share BY
   ADDRESS: the initial thread stays the sole owner and the worker
   borrows through a parked `UnsafePointer`, sound because the owner is
   parked inside `wait()` for the worker's whole life. Recorded rather
   than worked around — the shape those programs WANT is one handle
   each, and unit 3's `give` is where it arrives.
3. No compiler defect was hit. Every Saw friction point in the brief's
   candidate list (NoCopy fields, the record shape, match arms binding
   NoCopy payloads, `Optional.take` on a field, method calls through a
   pointer place) worked as documented on the pinned toolchain.

### Docs updated

spec §2 Clock row (mint-per-call supersedes "same handle") and Process
row (slot reclaim); §3's generations bullet, the no-amplification
amendment, the release-vs-close bullet, and the tier-two/transfer-funnel
paragraph; §11's ledger entry flipped to BUILT with what is still
absent named. `sosabi`: the encoding block, `RELEASE_OP`, the
`SystemHandle` prophecy flipped to present tense, the `SystemOp.ClockGet`
paragraph. `sysapi`: `sos_handle_release`, the owning-tier section
header, nine wrapper docstrings, `Memory`'s and `System`'s prophecy
paragraphs, `process_self`/`clock_get`. `dispatch.saw`'s three getters,
`alloc_process`'s "never spent again" paragraph, `clear_domain`'s
"without handle generations" sentence. Tracker closed in place.
