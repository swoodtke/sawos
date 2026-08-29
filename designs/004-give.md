# SawOS design 4 — give, tags, and the boot drain (M3 unit 3)

Status: BUILT Aug 29 2026, after FOUR USER RULINGS taken at review
that reshaped the unit — see "THE RULING CHAIN" directly below, which
amends D-3 and D-4, and the "As built" at the end. AUTHORED Aug 29 2026 (lead),
implementing the Aug-16 launch-
flow ruling as amended same day (sawlang#232 unit 3: the give-return
carries ONLY status; the boot-delivery op is an ITERATOR — third
refinement, superseding the batch-buffer and one-shot shapes). Unit 2
built the iterator early for root; this unit makes it what the ruling
describes: the child's boot sequence as literally a receive loop.

## THE RULING CHAIN (user, Aug 29 — amends D-3 and D-4)

Four rulings landed in order during review, each answering what the
one before it exposed. They are recorded as a CHAIN because the last
one is only legible as the end of it.

**0. WHAT FORCED THE CONVERSATION (the implementation's finding 1).**
D-3's give-then-start sequence is not writable. `Start` is an op on
the CHILD's Process handle, `Give` is a MOVE that unbinds the giver's
entry, and there was exactly ONE such handle — so a launcher that
gave it away had, at that instant, no way left to name the child and
could never start it. Keeping it needed a SECOND handle onto one
process, which design 3's finding 2 recorded as impossible; giving
after start would unfreeze the boot set. An implementation folded the
transfer into `start` (a `Donate` form) to get past it. That fold is
SUPERSEDED.

**1. A SECOND UNIVERSAL OP: `MINT_OP = 0xFFFE`.** Intercepted where
`RELEASE_OP` is — after the lookup, before the kind match — it mints a
SIBLING handle onto the same object into the CALLER's own table. Gated
on a NEW UNIVERSAL RIGHT, `Mint`, using §3's reserved universal bits
as the layout always intended. A source lacking it is `AccessDenied`.
So design 3's finding 2 is CLOSED, and with it the whole ordering
problem: a launcher mints what it hands over and keeps its own.

**2. THE MASK IS A KEEP SET** (superseding a removal-mask first cut in
the same conversation): `new_rights = held & rights`, all-ones being
"the same rights". Still total — naming a right the source lacks is a
no-op — and still needs no introspection op. **THE REASON IS EVOLUTION
POLARITY: a keep mask FAILS CLOSED** as kinds gain rights, because a
whitelist denies what it has never heard of, where the removal form
failed OPEN. The case that decided it is ruling 3's: under a removal
mask, a launcher masking a System handle would have handed every
FUTURE System capability to every child it had ever configured.
Subtractive intent survives as NOTATION — `mint(rights: ~Transfer …)`
— so the fail-open choice is visible at the call site instead of being
the language of the op.

**3. CHILDREN GET A MASKED SYSTEM HANDLE.** `root_system_rights()`
gains `Transfer`; M2 withheld it because "there is nobody to transfer
a handle to in a one-process world", and unit 2 ended that world. So
the launch flow is MINT -> GIVE -> START: a launcher mints a System
sibling through a keep mask, gives it as the boot-tag record, starts
the child naming that tag, and RETAINS the child's Process handle.
**EVERY PROCESS THEN BOOTSTRAPS EXACTLY AS ROOT DOES** — System handle
in the first argument register, own Process handle derived from it,
boot set drained from there — which is a §12 symmetry the kernel now
has by construction rather than by coincidence. A child can PRINT
(`Debug` travelled) without owning a device, and cannot stop the
machine (`Shutdown` did not). The brief's out-of-scope line "children
stay console-silent until pipes" is RETIRED.

**4. RESOLUTION CONSUMES ITS RECORD.** `start(boot_tag:)` spends the
boot record it names: the register IS the delivery. This closes the
implementation's finding 2 — a child could otherwise meet one word
through two doors and release it twice — by making the hazard
unrepresentable rather than documented.

**5. `Manage` IS REMOVED AS A RIGHT, EVERYWHERE.** The doctrine:
SPECIFIC RIGHTS FOR SPECIFIC OPERATIONS, PER OBJECT TYPE; UNIVERSAL
BITS FOR UNIVERSAL OPS; THERE IS NO GENERIC AUTHORITY. `Manage` was
too generic to mean anything — it stood in front of `ProcessSelf`,
`ThreadSelf`, `Give` and a reading of sibling-derivation — and now
means nothing. `Mint` takes its bit (1 << 1; numbers are not ABI), and
`SystemRight.ProcessSelf`, `ProcessRight.ThreadSelf` and
`ProcessRight.Give` are the bits that replaced it. It does NOT mean
one bit per op number: `TimerRight.Arm` still gates `Arm` and
`Disarm`, because that pair is ONE capability and splitting it would
withhold nothing. The unit of a right is an AUTHORITY.

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

**AMENDED BY THE RULING CHAIN above; this section's reasoning stands
and its conclusion moved.** As authored: unit 2 minted the child's
Process handle with `Start | Wait | Manage` and wrote "unit 3
revisits", and D-3 revisited it to the full Process default set plus
`Transfer`, because the drain lives on Process and a child that is to
drain must hold its own Process handle.

What the rulings changed is WHERE the child's authority comes from,
and therefore what that width is for:

- **A CHILD DOES NOT RECEIVE A PROCESS HANDLE AT ALL.** It receives a
  MASKED SYSTEM handle and derives its own Process object from it
  (`SystemRight.ProcessSelf`), exactly as root does at boot. So the
  Process default set is what a process holds over ITSELF and what a
  launcher holds over its CHILD — one set, three minters, since the
  doctrine left nothing distinguishing them.
- **"ONE HANDLE, ONE CHOICE" IS GONE, and it was the finding that
  removed it.** `MINT_OP` closes design 3's finding 2, so a launcher
  KEEPS the child's Process handle (supervision: `get_status`, and
  unit 5.5's death-wait) while the child manages itself. Unit 5.5 no
  longer waits on the re-mint question.
- The set is named for its ops throughout, per ruling 5: `ThreadCreate
  | ThreadSelf | Exit | Wait | EventCreate | WaiterCreate |
  InterruptBind | Start | BootHandles | Give`, plus the universal
  `Transfer | Mint`.

Rights-audit rider: the implementer records, in the As-built, WHICH
kinds' default sets mint `Transfer` and `Mint`, BEFORE and AFTER. The
rulings changed three things — `Manage` removed everywhere, `Mint`
added everywhere (ruling 1's uniform lean, which the user may veto at
integration), `Transfer` added to `root_system_rights()` (ruling 3) —
and nothing else.

## D-4: The surface

- `ProcessOp.Give` (bare verb — the receiver is the child) — args:
  handle word, tag. Status-only return. **Gated on `ProcessRight.Give`
  per ruling 5** (as authored it borrowed `Manage`), plus the
  universal `Transfer` on the handle being given.
- `ProcessOp.Start` gains the `boot_tag:` argument. The no-tag form is
  arg = a sentinel; lean: reuse `NO_HANDLE`'s zero as "no tag" is
  WRONG (zero is a legitimate tag — root's region ordinal 0 exists
  today!). The op takes a HAS-TAG flag argument or a reserved all-ones
  sentinel; implementer picks and documents in sosabi. The existing
  `start()` callers are the no-tag form. **Resolution CONSUMES the
  record it names, per ruling 4.**
- **`MINT_OP` (ruling 1), universal, gated on the universal `Mint`
  bit, argument a KEEP MASK (ruling 2), answering the sibling's word.**
- sysapi: `Process.give(...)` — one funnel per givable wrapper kind,
  each consuming the wrapper and disarming per design 3's contract;
  `start(boot_tag:)` overload; `System.mint()` / `mint(rights:)`. The
  child-side story needs one more crossing, and ruling 3 made it one
  that ALREADY EXISTS: a child adopts a0 as a `System`, through
  `System(boot_handle:)`, which is root's own blessed crossing. No
  second one is owed.
- Ruling 2 makes a rights ENUM public API for the first time — a keep
  mask is a value a process assembles and passes as a syscall argument
  — so `sosabi`'s `SystemRight` is re-exported through `sos`. The vDSO
  wall is about OP NUMBERS and is unchanged.
- New rights: the universal `Mint`, plus `SystemRight.ProcessSelf`,
  `ProcessRight.ThreadSelf` and `ProcessRight.Give` (ruling 5).
  `Manage` is removed. No new statuses; faults reuse `DuplicateKey`,
  `BadState`, `BadArg`, `AccessDenied`, `BadHandle`.

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
4. **`give_no_transfer`** — as authored, root tries to give its own
   System handle (whose default set withheld Transfer): `AccessDenied`
   fault. **RESHAPED BY RULING 3**, which gave root's System handle
   `Transfer`: the ungivable handle is now one a launcher MADE
   ungivable, by minting a sibling through a mask that omits the bit.
   Same fault, sharper claim — it is simultaneously the give's gate and
   the attenuation proof. Rulings 1-2 add two more cases: `mint_revoked`
   (a sibling masked below `Mint` cannot mint) and `child_no_shutdown`
   (the mask holds at RUN TIME — the child prints, asks to halt, is
   refused, and dies while root survives).
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

**THREE OF THESE WERE BROUGHT IN SCOPE BY THE RULING CHAIN**, which is
what makes it a chain rather than a clarification. ~~Attenuation ops~~
and ~~a second handle onto an owned object~~ are BUILT, and they are one
op (`MINT_OP`, rulings 1-2); ~~System handles for children~~ is BUILT
(ruling 3), so the console-silent sentence is retired and the echo
driver is no longer the only way anything but root can speak.

Still out: give after start / any dynamic transfer to a RUNNING process
(M4 pipes, to a receiver expecting it); quotas on boot records (unit 5);
death notifications (5.5, which no longer waits on the re-mint
question). And one the rulings ADDED: a handle cannot be narrowed below
`Transfer` and still be given, so a receiver holds that bit too — a
ruling for the unit that gives a child a pipe.

## As built

Landed Aug 29. Gate: `SAWLANG_ROOT=$HOME/Projects/sawlang make
sos-test` — **124 passed across riscv32 + arm64 (62 cases each)**,
against a baseline of 108 (54 each) taken at the merge base `546d0de`.

D-1 and D-2 landed as briefed. D-3 and D-4 landed as the RULING CHAIN
amends them; the chain's history is above, and what follows is what the
tree now contains.

### The launch flow as landed

```saw
let for_child = system.mint(rights: Debug | ProcessSelf | ClockGet | Transfer)!
child.give(system: move for_child, tag: 7)      // and the regions, 3 and 5
child.start(boot_tag: 7)                        // resolution CONSUMES record 7
// ...root still holds `child`, and reads get_status through it
```

Three ordinary ops, and the child then does what root does at boot:
`System(boot_handle:)`, `process_self()`, drain. **The kernel has ONE
way into user mode for every process** — the same claim §12's
loader-above-boot rule makes about images — and the only difference
between root and a child is a rights word.

### `MINT_OP` as landed (rulings 1, 2)

`MINT_OP: UInt = 0xFFFE` in `sosabi`, intercepted in `dispatch()`
immediately after `RELEASE_OP` and before the kind match.
`mint_sibling` faults `BadHandle` on an unresolved source and
`AccessDenied` without the universal `Mint`; otherwise `rights =
entry.rights & UInt32.from(truncating: keep)`, a fresh `mint_handle`
into the caller's own table, `NoResource` if that table is full.

Two mechanics worth recording:

- **THE MASK IS TRUNCATED, NOT `as`-CAST.** `keep` is a register-wide
  word a PROCESS chose, and `as UInt32` on a 64-bit profile would PANIC
  INSIDE THE KERNEL over a number userspace picked. Rights are a 32-bit
  word, so bits above the budget name no right and dropping them
  changes no answer. (`handle_word_generation` refuses to narrow for
  the opposite reason — its high bits MEAN something — and the two
  sites together are the rule: narrow when the bits are meaningless,
  widen the comparison when they are not.)
- **THE EIGHT OP-TABLE ASSERTS NOW TEST `MINT_OP`**, the LOWER of the
  two universal numbers, so one assert per table covers both and a
  third universal op below it would tighten all eight at once. A ninth
  assert pins `MINT_OP < RELEASE_OP`.

### The universal byte as landed (ruling 5)

| bit | before | after |
|---|---|---|
| 0 | `Transfer` | `Transfer` |
| 1 | `Manage` | **`Mint`** |
| 2-7 | reserved | reserved |

`Manage` is REMOVED from all nine rights enums, not renamed; `Mint`
takes its bit because numbers are not ABI and a hole where a retired
right used to be is a number nobody can read a reason for. Each enum's
universal `static_assert` pair is a pair again (`Transfer` at 0, `Mint`
at 1), nine of each.

Three kind-specific bits replaced it, each named for the op it gates:
`SystemRight.ProcessSelf = 1 << 12`, `ProcessRight.ThreadSelf = 1 <<
16`, `ProcessRight.Give = 1 << 17`, with the ordinary "at bit 8 or
above" asserts.

### The rights-audit table (D-3's rider), before and after

`T` = `Transfer`, `M` = `Mint`, `Mg` = the retired `Manage`.

| default set | before | after |
|---|---|---|
| `root_system_rights()` | Debug, Shutdown, ClockGet, ProcessCreate, **Mg** | Debug, Shutdown, ClockGet, ProcessCreate, **ProcessSelf**, **T**, **M** |
| `root_process_rights()` | ThreadCreate, Exit, Wait, EventCreate, WaiterCreate, InterruptBind, BootHandles, **Mg** | *(collapsed — below)* |
| `child_process_rights()` | Start, Wait, **Mg** | *(collapsed — below)* |
| **`process_rights()`** *(new: one set, three minters)* | — | ThreadCreate, **ThreadSelf**, Exit, Wait, EventCreate, WaiterCreate, InterruptBind, Start, BootHandles, **Give**, **T**, **M** |
| `memory_rights()` | T, **Mg** | T, **M** |
| `thread_rights()` | Start, Join, Control | Start, Join, Control, **M** |
| `event_rights()` | Signal, Receive, Wait | Signal, Receive, Wait, **M** |
| `waiter_rights()` | Attach, Wait | Attach, Wait, **M** |
| `interrupt_rights()` | Wait, Ack | Wait, Ack, **M** |
| `clock_rights()` | Read, TimerCreate | Read, TimerCreate, **M** |
| `timer_rights()` | Arm, Wait | Arm, Wait, **M** |

**`Mint` IS IN EVERY SET UNIFORMLY**, the lead's lean the user may veto
at integration. The argument is at the definition: a per-kind table
nobody can predict from outside would mean a launcher has to know which
kinds are mintable before it can write a policy, and a withheld `Mint`
only means "this kind cannot be attenuated" — strictly worse, since the
alternative to giving a narrowed sibling is giving the full-rights
original.

**THE TWO PROCESS SETS COLLAPSED INTO ONE.** Unit 2 gave a child's
handle three bits, D-3 widened it to the self-management vocabulary
plus `Start` and `Transfer`, and once `Manage` was retired nothing
distinguished the lists. `Start` and `Give` read oddly on root's own
handle and are harmless there — both refuse on the process STATE word,
which answers for a child too.

**SO TWO KINDS TRAVEL** (`Transfer`): Memory, and — new in this unit —
System. A Process handle can travel and nothing needs it to; every
other kind still cannot be given at all.

### The keep mask, and the one thing it cannot narrow

`new = held & keep`, all-ones the default (`ALL_RIGHTS` in `sos`), and
subtractive intent spelled as a complement where a case means one:

```saw
static NO_TRANSFER: UInt32 = ~(SystemRight.Transfer as UInt32) & 0xFFFF_FFFF
```

The `& 0xFFFF_FFFF` is load-bearing and worth knowing: Saw folds
constants in the SIGNED platform-`Int` domain, so a bare `~x` is
negative and does not fit `UInt32` (design 185's documented gotcha).

**A HANDLE CANNOT BE NARROWED BELOW `Transfer` AND STILL BE GIVEN** —
found by building it, and caught by the gate on the first run. `give`
checks `Transfer` on the thing being given, so a launcher cannot narrow
past the ticket that lets the narrowed thing arrive; the child
therefore holds `Transfer` on its System handle. Inert today (no
`ProcessCreate` to make a receiver with, no pipe until M4), recorded at
four sites, and a ruling for the unit that gives a child a pipe. It is
the same constraint `give_no_transfer` proves from the refusing side.

### The boot-queue shape as landed (D-2, ruling 4)

The handle table's own idiom, so the file has ONE shape for per-process
storage: `BOOT_HANDLES[MAX_PROCESSES * MAX_HANDLES]` indexed `p *
MAX_HANDLES + i`, with `BOOT_HANDLE_COUNT` and `BOOT_HANDLE_NEXT`
arrays. `MAX_HANDLES` is the bound D-2 asked for and is not arbitrary:
every record names a handle in that process's table.

`BootHandleSlot` gained a `taken: Bool`, which is ruling 4's mechanism.
A record leaves through one of TWO doors — the drain, or
`start(boot_tag:)`'s resolution — and either spends it; the flag is
what lets the second door reach the middle of a queue a cursor alone
cannot, and `boot_handle_cursor` steps the cursor over spent records so
it still only ever advances. Six functions are the whole surface:
`boot_handle_room`, `boot_tag_used` (the duplicate scan, which counts
spent records because a tag is an IDENTITY for the process's whole
set), `boot_handle_take` (the consuming resolver), `boot_handle_cursor`,
`queue_boot_handle` and `clear_boot_handles`.

`clear_boot_handles` at the teardown is load-bearing, not tidiness: a
process slot is reclaimable since design 3 D-3, so a stale count and
cursor would give the next occupant a stranger's set to drain.

### The no-tag encoding as landed (D-4)

**A FLAG ARGUMENT, spelled as a backed enum: `BootTagForm: UInt {
Absent = 0, Present = 1 }`.** `Start` takes the form in `arg0` and the
tag in `arg1`. The all-ones sentinel was refused for a sharper reason
than the brief's own (zero being a legitimate tag settles it alone): a
tag is the giver's own word, so a kernel reserving ANY value would be
interpreting the vocabulary it exists not to interpret. It is an enum
rather than a bare 0/1 for the house rule, which also makes the decode
`from(raw:)` — total, so `arg0 = 3` is `BadArg` rather than a number
the kernel trusts.

### The funnel shapes as landed

| spelling | receiver | wrapper |
|---|---|---|
| `system.mint()` / `mint(rights:)` | `&self` | answers a new `System` |
| `child.give(memory: move r, tag:)` | `&self` | consumed |
| `child.give(system: move s, tag:)` | `&self` | consumed |
| `child.start()` / `start(boot_tag:)` | `&self` | — |

The give funnels take their wrapper BY VALUE and disarm before the
syscall (design 3 D-5); the consuming shape is `var w = move <param>`,
because a by-value parameter is a `let` and the disarm is a write.

**TWO GIVE FUNNELS AND NOT NINE**: a funnel for a kind whose default
set withholds `Transfer` would be a method whose every call is an
`AccessDenied` fault. There is no `give(process:)` because nothing
needs one — a launcher KEEPS its child's Process handle, and a Process
handle onto a THIRD process is a supervision hand-off nothing can hold
two of yet. `System.mint` is the only mint funnel for the mirror
reason: the op is universal and works on every kind, and a funnel
arrives with the unit that narrows one.

**`Process(boot_handle:)` WAS BUILT AND THEN DELETED.** Ruling 3 made
the ruled bootstrap System-in-a0, so a second crossing for a shape
nothing takes would be surface with no caller. `System(boot_handle:)`
carries the note about it.

`BootHandle` gained two private slots and two accessors —
`process`/`take_process` and `system`/`take_system` — beside `memory`.
Three optionals of which exactly one is ever occupied, with `kind`
saying which: asking the wrong question answers `None` and DISTURBS
NOTHING, where an enum payload would have to be taken out of the record
to be matched and a mismatch would then drop a capability the caller
wanted.

**`SystemRight` IS RE-EXPORTED THROUGH `sos`**, the first time a rights
enum has been public API. Ruling 2 makes a keep mask a value a process
assembles and passes as a syscall argument, which is exactly the module
docstring's own definition of PUBLIC. The vDSO wall is about OP NUMBERS
and is untouched; one enum is re-exported rather than nine, because a
kind whose handles nothing masks has no reason to publish its bits.

### One new kernel report line

```
SOS: process exit: code=0x00000002 process=0x00000001
```

Gated on `p != ROOT_PROCESS && kind == Exited`, so **no case written
before this unit can reach it**: root takes the other arm of §8's fork,
and no child before now could exit at all. It is a separate line rather
than a field appended to the teardown, because appending would rewrite
a line every process that ever died has printed.

### The proof, and exactly what was diffed

Eight all-arch cases, ten new Blade packages (eight root servers and
TWO children — the first SOS processes that are neither root nor
sandboxed).

| case | claim | verdict |
|---|---|---|
| `give_boot_drain` | mint -> give -> start; the child SPEAKS, drains TWO, exits 2; root reads status through the handle it KEPT | clean exit |
| `give_duplicate_tag` | a reused tag is `DuplicateKey` | root faults |
| `give_after_start` | the set freezes at start — `BadState` | root faults |
| `give_no_transfer` | a sibling masked below `Transfer` cannot be given — `AccessDenied` | root faults |
| `start_bad_tag` | a tag naming no record is `BadArg` | root faults |
| `give_word_dead` | the giver's word is stale afterwards — `BadHandle` | root faults |
| `mint_revoked` | a sibling masked below `Mint` cannot mint — `AccessDenied` | root faults |
| `child_no_shutdown` | the mask holds at RUN TIME: the child prints, asks to halt, is refused, dies; root survives | clean exit |

The money shot, riscv32 (arm64 identical but for word width):

```
SOS givedrain: created
SOS givedrain: minted a masked System
SOS givedrain: gave 7 3 5
SOS givedrain: started
SOS childdrain: n=2 tags=35 kinds=0
SOS: process exit: code=0x00000002 process=0x00000001
SOS: process teardown handles=0x00000002 ... process=0x00000001
SOS givedrain: root observed child status=65538
SOS givedrain: done
```

`SOS childdrain:` is the first time anything but root has spoken. `n=2`
is ruling 4 asserted — three capabilities given, the boot-tag record
consumed by resolution — and `tags=35` carries the ORDER as digits.
`code=2` and `status=65538` are the same fact from the two sides of the
boundary: the kernel's account, and root's, through the Process handle
it never gave away. `handles=2` is the child's estate — the System
handle it printed through and the Process handle it derived — the two
regions having been declined at the drain.

`mint_revoked` carries TWO claims in one transcript, recorded rather
than padded: every default set grants `Mint`, so the only way to hold a
source lacking it is to have minted one, and "the sibling cannot mint"
and "a source without `Mint` is `AccessDenied`" are the same program.
`give_no_transfer` is the same doubling for `Transfer` — the give's
gate and the attenuation proof at once.

**ACCEPTANCE.** The suite was run under the machine-wide lock at the
merge base (`546d0de`, unmodified) and again with the change.

- **The 108 pre-existing case rows are BYTE-IDENTICAL** in name,
  verdict and ORDER on both architectures with the `[i/N]` denominator
  stripped (`diff` reports no difference at all). The only row-level
  change is eight new rows appended per architecture.
- **The only other differences are sosimg build-info lines and the
  total**: ten new package lines, and every pre-existing riscv32 image
  grew 80-1712 bytes — the `sos` module gaining `sos_handle_mint`,
  `sos_process_give` and a wider `sos_process_start` as `@export`ed
  C-ABI seams that `--gc-sections` keeps in every image linking the
  module. On arm64 29 of 33 images did not move at all: that profile
  page-aligns its sections and the growth is absorbed by padding.
- Each new case was additionally booted by hand on both machines and
  its console transcript read line by line, not merely matched.

### Findings

1. **A HANDLE CANNOT BE NARROWED BELOW `Transfer` AND STILL BE GIVEN.**
   The one the rulings did not anticipate; caught by the gate. See the
   keep-mask section — recorded at four sites, inert today, a ruling
   for the unit that gives a child a pipe.
2. ~~A give whose tag is also the boot tag hands the child one word
   through two doors~~ CLOSED by ruling 4 — the hazard is
   unrepresentable rather than documented, which is what the ruling was
   for.
3. ~~A donation cannot be a give~~ CLOSED by rulings 1-3; it is what
   forced them, and design 3's finding 2 closed with it.
4. **`FaultReason.DuplicateKey`'s text still names attachments.** "an
   attachment already uses that key" was written for §2.2's keys and
   now covers a give's tag as well. The string is ASSERTED by a shipped
   transcript (`event_dupkey`) and this unit authorised no expectation
   changes, so it did not move; the generalisation is recorded at the
   declaration and `give_duplicate_tag` asserts the same sentence.
5. **`MINT_OP` makes design 3's finding-2 rewrite possible and it is
   NOT done here.** `event-wake` and `event-consume-wake` share a
   handle by ADDRESS through a parked `UnsafePointer` because no op
   could mint a second one; they can now hold one handle each, which is
   the shape those programs always wanted. BACKLOG on purpose: the
   rewrite would move shipped transcript rows, which this unit
   authorised none of.
6. **No compiler defect was hit.** Two ordinary Saw facts were met and
   both are documented: DF-172d (a binary expression does not wrap
   unless brackets enclose it), and design 185's signed-`Int` const
   fold (a bare `~x` is negative, so a complement mask needs
   `& 0xFFFF_FFFF`). Everything else worked as documented on the pinned
   toolchain — a keep mask assembled from re-exported enum cases at a
   `UInt32` parameter (DF-240a's const adoption), a third
   `Optional<NoCopy>` slot on a `NoCopy` record, a by-value NoCopy
   parameter moved into a `var` local to disarm it.

### Docs updated

spec §2's `MemoryObject`, `Process` and `System` rows; §3's universal
byte (the doctrine, `Mint` at bit 1, `Manage` removed), the
no-amplification example list, the `Transfer`-first-consumer bullet, a
new `Mint`/`MINT_OP` bullet, and the attenuation-is-monotonic bullet
(attenuation is PERFORMABLE now); §11's launch-flow entry plus a new
BUILT entry for attenuation-and-a-second-handle; §12's boot-set section
(per-process sets, the tag doctrine, the freeze, consume-on-resolution,
the bootstrap symmetry). `sosabi` throughout: the doctrine, nine rights
enums, three new kind-specific bits, `MINT_OP`, `rights_allow_mint`,
the collapsed `process_rights()`, `root_system_rights()`'s `Transfer`,
`BootTagForm`, `BootHandleKind`, and the `Manage` history notes.
`kcore.objects`' boot-queue section; `kcore.dispatch`'s mint
interception, `process_start`, `process_give`, the three re-gated ops
and the create-mint; `kcore.sched`'s teardown. `sysapi`: `System.mint`,
the give funnels, the two `start` forms, `BootHandle`'s three slots,
`SystemRight`'s re-export, and the C floor. Design 3's finding 2 marked
CLOSED with the chain that closed it. Tracker closed in place.
