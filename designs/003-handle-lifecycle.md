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

## D-1: The word — low 8 index, 24 bits of generation, portable

`word = (gen << 8) | (index + 1)`, gen and split IDENTICAL on both
profiles (24 bits of generation, wrapping; the top 32 bits of an
arm64 word stay zero). Same width both arches means the wrap behaves
identically everywhere — one story, one test. Index keeps its 1-based
spelling and `NO_HANDLE = 0` still names nothing at any generation.

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

(Implementer: the split as landed, RELEASE_OP mechanics, the
BootHandle shape chosen, the per-case transcript accounting table,
findings.)
