# SawOS design 4 — give, tags, and the boot drain (M3 unit 3)

Status: AUTHORED Aug 29 2026 (lead), implementing the Aug-16 launch-
flow ruling as amended same day (sawlang#232 unit 3: the give-return
carries ONLY status; the boot-delivery op is an ITERATOR — third
refinement, superseding the batch-buffer and one-shot shapes). Unit 2
built the iterator early for root; this unit makes it what the ruling
describes: the child's boot sequence as literally a receive loop.

## The ruled surface, transcribed

- **`give(handle, tag:)` on the CHILD's Process handle**, gated by the
  UNIVERSAL Transfer right on the handle BEING GIVEN: MOVES the handle
  into a fresh child-table slot and returns ONLY ITS STATUS — the
  child-side word is irrelevant to root (root can call no op through
  it), so nothing returns it.
- **THE TAG IS THE IDENTITY**, and tags are the ONLY cross-process
  vocabulary: the giver's own word handed back unread (the Waiter.add
  key precedent — the kernel is a courier, never an interpreter; root
  and child agree on meaning through config + manifest). A DUPLICATE
  tag is REFUSED at the give — a FAULT: an identity naming two handles
  is broken config, caller-checkable, and it would make the record a
  multimap and the boot lookup ambiguous. `FaultReason.DuplicateKey`
  already exists and is this exact sentence.
- **`start(boot_tag:)`**: the KERNEL resolves the tag to the
  child-table word and puts it in a0, so `_start(boot_handle)` is
  unchanged from M2 and root never sees a child-relative word at any
  point. Start with NO boot_tag keeps unit 2's `NO_HANDLE` in a0 —
  the sandboxed compute process stays a feature. A boot_tag that
  names no given record is a FAULT (`BadArg`) — config error,
  caller-checkable.
- **The drain**: `boot_handle_next`, unchanged in shape from unit 2 —
  one `{tag, kind, handle}` record per call, CONSUMED on delivery,
  exhaustion `Drained` and stays `Drained`. Records come back IN GIVE
  ORDER. What changes is WHOSE: the queue becomes PER-PROCESS (D-2).
- **`give` AFTER `start` is REFUSED in v1** (`BadState` fault): the
  boot set freezes at start — the give-before-start ordering IS the
  soundness argument (boot_handles must answer completely at the
  child's first instruction, and start is the barrier that makes a
  half-populated table unrepresentable, with no synchronization
  invented). Dynamic transfer is M4 IPC's job, over pipes, to a
  process expecting it.

## D-1: What a give does to the tables (2.75 composes)

A give is UNBIND-AND-REBIND, rights VERBATIM:

- The caller's entry unbinds exactly as release does — generation
  bump included, so the giver's word is stale and its later use is
  the diagnosed `BadHandle` fault. The sysapi funnel disarms the
  wrapper per design 3's recorded transfer contract
  (disarm-before-syscall; every failure of the give is a fault, so a
  disarmed word never leaks).
- A fresh entry binds in the CHILD's table — lowest free slot, the
  child slot's own generation — carrying the SAME kind, target and
  RIGHTS. Give is a MOVE, not a mint: no default set is consulted and
  nothing amplifies; what root attenuated stays attenuated. (No
  attenuate op exists yet; rights today are as minted. Recorded, not
  solved.)
- A full child table is `NoResource` STATUS (dynamic resource), and
  the give did not happen — the caller's entry is untouched. Order of
  checks: rights and state faults first, then the capacity status,
  then the move; a fault leaves both tables unchanged by definition.
- The boot RECORD stores the tag, the kind, and the CHILD-side word,
  appended in give order (D-2).

Gating on the op itself: `give` requires `Manage` on the child's
Process handle — the courier op is management of the child, the same
bar `Start` sits behind. The Transfer check is on the GIVEN handle
and is the ruling's own gate.

## D-2: The boot queue goes per-process

Unit 2's `BOOT_HANDLES`/`BOOT_HANDLE_COUNT`/`BOOT_HANDLE_NEXT` are
root-only globals — correct for one drainer, wrong for two. They
become per-process state: a bounded record array per process slot
(bound: `MAX_HANDLES` — a boot set larger than the table it fills
could never be drained into existence), with count and cursor.
Root's boot-region records flow through the same machinery, written
by the kernel at boot exactly as a give writes a child's (the kernel
is the giver nobody gave to; tags stay region ordinals). The
duplicate-tag check scans the same array — bounded, tiny.
`boot_handle_next` reads the CALLER's own set through its own
Process handle, exactly as before; `ProcessRight.BootHandles` still
gates it.

Freeze at start: the give-refusal after start is what freezes the
set; the DRAIN has no deadline — records persist until consumed, and
a child that drains late drains correctly. Records of a process that
dies undrained die with it in teardown (their handles are the
child's table entries, already closed there; the records are
bookkeeping and clear with the slot).

## D-3: The child's first handle — the create-mint re-ruled

Unit 2 minted the child's Process handle with `Start | Wait |
Manage` and wrote "unit 3 revisits". Revisited: **the handle
`process_create` returns carries the FULL Process default set plus
`Transfer`** — the self-management vocabulary (`ThreadCreate | Exit |
Wait | EventCreate | WaiterCreate | InterruptBind | Manage`) plus
`Start`, `BootHandles`, and `Transfer`.

Why: the drain lives on Process, and a child that is to drain must
hold ITS OWN Process handle — which only root can put there, by
giving the one create minted. With Transfer on it, root chooses per
child:

- **KEEP it — supervision**: root retains `get_status` (and unit
  5.5's death-wait); the child holds nothing and is the sandboxed
  compute process. Unit 2's cases keep working unchanged this way.
- **GIVE it (tagged, say, as the boot_tag) — donation**: the child
  receives its own Process handle in a0, drains its boot set, makes
  threads, exits with a code. Root retains nothing on the child.

ONE handle, ONE choice — root cannot have both today, and that is
stated rather than papered over: holding supervision AND donating
self-management needs a SECOND handle onto the same process, which
is exactly the re-mint question design 3's finding 2 recorded (no op
mints a second handle onto an owned object). Unit 5.5 owns it: death
notifications are when root genuinely needs to retain while the
child holds its own. The harness meanwhile needs no root-side
observation — the kernel's exit/fault/teardown transcript is the
oracle either way.

Rights-audit rider: the brief's implementer records, in the
As-built, WHICH kinds' default sets currently mint `Transfer` (the
universal bit exists in every enum; whether each `*_rights()` SETS
it was decided before give existed). No default changes in this unit
beyond the child-Process re-rule above — a kind whose default
withholds Transfer simply cannot be given yet, and adjusting that is
a per-kind ruling for the unit that needs it. Known today:
`memory_rights()` mints it (unit 2), `root_system_rights()` does NOT
(deliberate M2 choice — "nobody to transfer to"; root's System
handle staying ungivable is v1-correct and gets a proof case).

## D-4: The surface

- `ProcessOp.Give` (bare verb — the receiver is the child) — args:
  handle word, tag. Status-only return.
- `ProcessOp.Start` gains the `boot_tag:` argument. The no-tag form
  is arg = a sentinel; lean: reuse `NO_HANDLE`'s zero as "no tag" is
  WRONG (zero is a legitimate tag — root's region ordinal 0 exists
  today!). The op takes a HAS-TAG flag argument or a reserved
  all-ones sentinel; implementer picks and documents in sosabi. The
  existing `start()` callers are the no-tag form.
- sysapi: `Process.give(...)` — one funnel per wrapper kind (the
  overload set or per-kind labels, implementer's call), each
  consuming the wrapper and disarming per design 3's contract;
  `start(boot_tag:)` overload. The child-side story needs one more
  crossing: **adopting a0 as a Process wrapper** — `_start`'s raw
  word becomes a typed `Process` (the `System(boot_handle:)`
  precedent: one blessed crossing into the typed layer, sysapi-owned).
- No new rights beyond D-3's re-rule; no new statuses; faults reuse
  `DuplicateKey`, `BadState`, `BadArg`, `AccessDenied`.

## The proof (harness; children are real Blade packages)

1. **`give_boot_drain`** — the ruling's money shot: root gives the
   child its own Process handle (tag 7, the boot_tag) plus two
   Memory regions (tags 3 and 5), starts it; the child's `_start`
   adopts a0, drains THREE records, asserts tags and kinds and give
   order, asserts `Drained` stays `Drained`, and exits with the
   drain count via its Exit right. Kernel transcript shows the exit
   code; that is the proof root never had to witness.
2. **`give_duplicate_tag`** — second give with a used tag: root's
   `DuplicateKey` fault, transcript shows the sharpened line.
3. **`give_after_start`** — `BadState` fault.
4. **`give_no_transfer`** — root tries to give its own System handle
   (whose default set withholds Transfer): `AccessDenied` fault. The
   case doubles as the D-3 rights-audit's negative exhibit.
5. **`start_bad_tag`** — boot_tag naming no record: `BadArg` fault.
6. **Giver's word is dead** — an arm of case 1 or its own case: after
   a give, root touches the given handle's old word → `BadHandle`
   fault (2.75's generations composing with give).

Existing transcripts: NO authorized changes. The unit adds entries
to tables and ops nobody existing calls; every shipped row stays
byte-identical (unit 2's cases keep the supervision shape, whose
counts are unchanged by D-3's wider rights mint — rights are not
counted anywhere a transcript shows).

## Docs owed

- spec §12 (the boot-set story: give order, tags, the drain — the
  "receive loop" sentence comes true; the one-bootstrap-pipe line
  stays M4), §2 Process row (Give + the boot_tag start), §3 (the
  Transfer bit's first consumer; the no-amplification note gains the
  give sentence), §11 rows.
- sosabi docstrings: Give, the boot_tag encoding, the D-3 mint set.
- `dispatch.saw`'s `process_start` and create-mint comments; the
  unit-2 "unit 3 revisits" notes all resolve.
- design 3's finding-2 note gains "unit 5.5" as its owner (the
  re-mint question, now with the supervision-vs-donation argument).
- Tracker closed in place; As-built (the boot-queue shape as landed,
  the no-tag encoding, the rights-audit table, funnel shapes,
  findings).

## Out of scope

Attenuation ops (rights travel as minted); a second handle onto an
owned object (unit 5.5's re-mint question); give after start / any
dynamic transfer (M4 pipes); System handles for children (nothing
mints one to give; children stay console-silent until pipes — the
echo driver speaks UART, not debug_print); quotas on boot records
(unit 5); death notifications (5.5).

## As built

(Implementer: queue shape, no-tag encoding, rights-audit table,
funnel shapes, case names/counts, findings.)
