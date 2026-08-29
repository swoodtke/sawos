# SawOS design 4 — give, tags, and the boot drain (M3 unit 3)

Status: BUILT Aug 29 2026 (see "As built" at the end — ONE deviation
from a ruled sentence, with its argument, plus three findings; the
deviation is the lead's to rule on). AUTHORED Aug 29 2026 (lead),
implementing the Aug-16 launch-
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

Landed Aug 29. Gate: `SAWLANG_ROOT=$HOME/Projects/sawlang make
sos-test` — **120 passed across riscv32 + arm64 (60 cases each)**,
against a baseline of 108 (54 each) taken at the merge base `546d0de`.

D-1, D-2 and D-4's encoding landed as briefed. **D-3 could not**, and
the reason is a genuine gap in the brief rather than a preference; it
is the first section below, because everything else composes with it.

### THE FINDING THAT MOVED THE DESIGN: a donation cannot be a give

D-3 rules that a child which is to drain must hold its own Process
handle, "which only root can put there, by giving the one create
minted", and proof case 1 describes give-then-start. **That sequence
is not writable.** `Start` is an op on the CHILD's Process handle;
`Give` is a MOVE that unbinds the giver's entry; and there is exactly
ONE such handle in the system. So a launcher that gives it away has,
at that instant, no way left to name the child and can never start it.
Both halves of the collision are ruled, so neither could bend:

- keeping the handle needs a SECOND one onto the same process, which
  is design 3's finding-2 re-mint question and is explicitly unit
  5.5's;
- giving after start unfreezes the boot set, whose freeze IS the
  soundness argument for the launch flow.

**Resolution, and it is a DEVIATION for the lead to rule on: the
transfer happens AT the barrier.** `BootTagForm` has a third case,
`Donate`, and `start(donating: tag)` moves the handle the op was
invoked through into the child under `tag`, putting the child-side
word in a0. That instant is the only one that is both *before the
child's first instruction* and *while the launcher still holds the
handle*. Nothing else about a give changes — it goes through the same
`give_faults` and `move_handle` helpers `ProcessOp.Give` uses, so the
fault set, the unbind-and-rebind, the rights-verbatim rule and the
tagged record are literally the same code. D-3's "ONE handle, ONE
choice" is unchanged and is now made at ONE call site: `start()` keeps
it and supervises, `start(donating:)` hands it over.

Consequences worth seeing together:

- `ProcessOp.Give` is unchanged and is what carries every OTHER
  capability; the give order in proof case 1 is therefore 3, 5, 7
  rather than 7, 3, 5 (the two regions, then the donation-at-start).
- There is deliberately NO `Process.give(process:tag:)` funnel. The op
  supports it, but the only Process handle a v1 launcher holds is the
  one whose give it cannot survive, so a funnel for it would be a
  method with no caller. The absence is documented at the site.
- The alternative resolutions were weighed and rejected in the brief's
  own terms: a special case that does not unbind on a self-give (two
  handles onto one process by the back door, and "give is a move"
  broken); a carve-out permitting a give while `Running` but not yet
  scheduled (too clever, and racy the moment a tick exists); moving
  the freeze (a ruling).

### The no-tag encoding as landed (D-4)

**A FLAG ARGUMENT, spelled as a backed enum: `BootTagForm: UInt {
Absent = 0, Present = 1, Donate = 2 }` in `sosabi`.** `Start` takes
the form in `arg0` and the tag in `arg1`.

The all-ones sentinel was refused for a sharper reason than the
brief's own (zero is a legitimate tag — root's region ordinals start
at 0 — and that alone settles it against reusing `NO_HANDLE`): **a
tag is the giver's own word handed back unread, so a kernel that
reserved any value would be interpreting the vocabulary it exists not
to interpret.** A flag costs one register `Start` was not using and
reserves nothing. It is an enum rather than a bare 0/1 for the
house rule (a closed named set is an enum), which also makes the
decode `from(raw:)` — total, so `arg0 = 3` is the ordinary `BadArg`
fault rather than a number the kernel trusts. Cases are
`Absent`/`Present` rather than `None`/`Tag` so nothing in a `match`
reads like an `Optional`.

The C floor takes the form as a NUMBER
(`sos_process_start(handle, boot_tag_form, boot_tag)`), which is the
documented difference in kind the sysapi docstring already describes
for `event_create`'s mode; the typed layer never writes one and offers
`start()`, `start(boot_tag:)`, `start(donating:)` instead. The single
export replaced unit 2's one-argument `sos_process_start`.

### The boot-queue shape as landed (D-2)

The handle table's own idiom, so the file has ONE shape for
per-process storage:

```saw
BOOT_HANDLES: [BootHandleSlot; MAX_PROCESSES * MAX_HANDLES]   // p * MAX_HANDLES + i
BOOT_HANDLE_COUNT: [Int; MAX_PROCESSES]
BOOT_HANDLE_NEXT:  [Int; MAX_PROCESSES]
```

`MAX_HANDLES` is the bound D-2 asked for and it is not arbitrary:
every record names a handle in that process's table, so a boot set
larger than the table it fills could never be drained into existence.
Five functions in `kcore.objects` are the whole surface —
`boot_handle_room`, `boot_tag_used` (the duplicate scan),
`boot_handle_word` (the tag resolver, which does NOT consume),
`queue_boot_handle` and `clear_boot_handles`.

**`clear_boot_handles` at the teardown is load-bearing, not tidiness.**
A process slot is reclaimable since design 3 D-3, so a stale count and
cursor would give the next occupant a stranger's boot set to drain.
Nothing is released there — every record names an entry the close-all
already unbound — which is exactly D-2's "the records are bookkeeping
and clear with the slot".

Root's boot minting goes through the same producer
(`queue_boot_handle(ROOT_PROCESS, ordinal, …)`), which is what makes
"the kernel is the giver nobody gave to" a fact about the code rather
than a metaphor.

### The funnel shapes as landed (D-4)

An OVERLOAD SET on one verb, label-distinguished, each taking its
wrapper BY VALUE and disarming before the syscall (design 3 D-5):

| spelling | receiver | wrapper |
|---|---|---|
| `child.give(memory: move r, tag: 3)` | `&self` | consumed |
| `child.give(system: move s, tag: 1)` | `&self` | consumed |
| `child.start(donating: 7)` | `&var self` | DISARMED, not consumed |

The consuming shape is `var w = move <param>` — a by-value parameter
is a `let`, and the disarm is a write. The third row is the exception
and the reason is a language fact rather than a choice: **Saw has no
consuming `self` receiver**, and `child.give(process: move child, …)`
would move a value the same call is borrowing as its receiver. So the
donation takes `&var self`, writes `NO_HANDLE` into the field, and
leaves a HUSK whose drop does nothing and whose later use is the
ordinary `BadHandle` — the generations backstopping the discipline
exactly as D-5 said they would.

**Two funnels and not nine**, per the rights audit below: a funnel for
a kind whose default set withholds `Transfer` would be a method whose
every call is an `AccessDenied` fault. `give(system:)` is the
deliberate exception — the refusal is a claim worth being able to
WRITE, and `give_no_transfer` is the case that writes it.

The a0-adoption crossing is `Process(boot_handle:)`, the
`System(boot_handle:)` precedent with no side effects (the System one
parks the panic path's handle; a Process one has nothing to park).

`BootHandle` gained a second private slot and a second accessor —
`process: Process?` + `take_process()` — beside the existing
`memory`/`take_memory`. Two optionals of which exactly one is ever
occupied, with `kind` saying which: asking the wrong question answers
`None` and DISTURBS NOTHING, where an enum payload would have had to
be taken out of the record to be matched and then dropped on a
mismatch.

### The rights-audit table (D-3's rider)

Which `*_rights()` default sets mint the universal `Transfer` bit,
BEFORE and AFTER this unit. **Exactly one changed**, and it is the one
D-3 re-ruled.

| default set | Transfer before | Transfer after | note |
|---|---|---|---|
| `root_system_rights()` | NO | NO | the deliberate M2 "nobody to transfer to" choice; root's System handle stays ungivable and `give_no_transfer` is its proof |
| `root_process_rights()` | NO | NO | nobody to give root's own Process handle to |
| `child_process_rights()` | NO | **YES** | **the one change** — plus the full self-management set and `Start` |
| `memory_rights()` | YES | YES | minted in unit 2 for exactly this unit |
| `thread_rights()` | NO | NO | |
| `event_rights()` | NO | NO | |
| `waiter_rights()` | NO | NO | |
| `interrupt_rights()` | NO | NO | |
| `clock_rights()` | NO | NO | |
| `timer_rights()` | NO | NO | |

So **two kinds are givable in v1**: Memory, and a child's own Process
handle. Everything else is refused at the `Transfer` check, which is
why `BootHandleKind` declares two cases and `boot_kind_of` is
deliberately PARTIAL — the day a kind becomes givable, the compiler
asks for its record spelling rather than letting one arrive mis-tagged.

`child_process_rights()` is DERIVED (`root_process_rights() | Start |
Transfer`) rather than respelled, because that function IS this v1's
"what a process may do to itself" vocabulary and a second copy of the
list is a second place to forget a bit.

### The numbers as landed

| What | Value | Where |
|---|---|---|
| `ProcessOp.Give` | 9 | `kernel/abi/src/lib.saw` |
| `BootTagForm.Absent` / `.Present` / `.Donate` | 0 / 1 / 2 | same |
| `BootHandleKind.Process` | 1 | same |
| `rights_allow_transfer(rights:)` | bit 0, kind-independent | same |

**No new rights, no new statuses, no new fault reasons** — the unit
reuses `DuplicateKey`, `BadState`, `BadArg`, `AccessDenied`,
`BadHandle` and `NoResource`, exactly as D-4 required.

### The order of checks, as landed

`give_faults` raises every fault before anything is touched, and
`move_handle` performs the two-table edit; `ProcessOp.Give` and
`Start`'s donating form both call them, which is what makes "the
donation is an ordinary give" a fact rather than two implementations
that agree today.

1. `Manage` on the receiver's handle (in the dispatch arm, like every
   other op) — `AccessDenied`
2. the given handle resolves — `BadHandle`
3. it carries `Transfer` — `AccessDenied`
4. its kind has a boot-record spelling — `BadArg` (unreachable today;
   3 refuses everything 4 could)
5. the child is still `Created` — `BadState`
6. the tag is unused in the child's set — `DuplicateKey`
7. room in the child's boot set — `NoResource` STATUS
8. mint into the CHILD — `NoResource` STATUS, caller's entry untouched
9. unbind the giver's entry (generation bump), append the record

Steps 8 and 9 are in that order deliberately: the child-side binding
is made FIRST, so a full table is a give that did not happen rather
than a capability lost between two tables.

In `Start`'s donating form the fault set runs before `alloc_thread`,
which is a pure scan that reserves nothing — so an early `NoResource`
there leaks neither a thread slot nor a capability.

### One new kernel report line, and why it moves no shipped row

```
SOS: process exit: code=0x00000003 process=0x00000001
```

**A donated child has no other voice.** It holds no System handle
(nothing in v1 mints a givable one), so it cannot print; and its
launcher gave the Process handle away, so there is no §8 status word
left for anyone to read. The brief asks the transcript to show the
exit code, and this is the minimum that makes that true.

It is a SEPARATE line rather than a field appended to the teardown,
because appending would rewrite a line every process that ever died
has printed. It is gated on `p != ROOT_PROCESS && kind == Exited`, and
**no case written before this unit can reach it**: root takes the
other arm of §8's fork, and no child before now could exit at all
(both existing children fault). Verified against the transcript diff —
the 108 pre-existing rows are byte-identical.

### The proof, and exactly what was diffed

Six all-arch cases, seven new Blade packages (six root servers and one
CHILD — `child-drain`, the first SOS process that is neither root nor
a sandboxed compute process).

| case | claim | verdict |
|---|---|---|
| `give_boot_drain` | the money shot: three records in give order, `Drained` stays `Drained`, exit code == drain count | clean exit |
| `give_duplicate_tag` | a reused tag is `DuplicateKey` | root faults |
| `give_after_start` | the set freezes at start — `BadState` | root faults |
| `give_no_transfer` | root's own System handle withholds `Transfer` — `AccessDenied` | root faults |
| `start_bad_tag` | a tag naming no record is `BadArg` | root faults |
| `give_word_dead` | the giver's word is stale afterwards — `BadHandle` | root faults |

The money shot's transcript, riscv32 (arm64 identical but for word
width):

```
SOS: boot regions=0x00000002
SOS: console handover
SOS givedrain: created
SOS givedrain: gave regions 3 5
SOS givedrain: donated and started
SOS: process exit: code=0x00000003 process=0x00000001
SOS: process teardown handles=0x00000001 threads=0x00000001 ... process=0x00000001
SOS givedrain: root survived
SOS givedrain: done
```

`code=3` is the child counting its own drain — three records, tags
`357` accumulated as digits so the number carries the ORDER, kinds
`001`, and a second ask that answered `Drained`. A mismatch on any of
those exits `99` instead. `handles=1` is the child's whole estate: it
declined both regions at the drain (their records dropped and released
them) and kept only the Process handle it exited through.

`give_word_dead` works at the C altitude, and that is half the claim
rather than a shortcut: the typed funnel CONSUMES its wrapper, so "use
the word you gave away" is not a sentence the typed layer can express.
`handle_remint` reaches the same floor for the mirror reason.

**ACCEPTANCE.** The suite was run TWICE under the machine-wide lock:
once at the merge base (`546d0de`, unmodified) and once with the change.

- **The 108 pre-existing case rows are BYTE-IDENTICAL** in name,
  verdict and ORDER on both architectures with the `[i/N]` denominator
  stripped (`diff` reports no difference at all). The only row-level
  change is six new rows appended per architecture.
- **The only other differences are sosimg build-info lines and the
  total.** Seven new package lines; every riscv32 image grew 56–872
  bytes, which is the `sos` module gaining `sos_process_give` and a
  wider `sos_process_start` as `@export`ed C-ABI seams that
  `--gc-sections` keeps in every image linking the module — the same
  reason design 2 recorded. On arm64 32 of 33 images did not move at
  all: that profile page-aligns its sections, so the growth is
  absorbed by padding, and only `process-lifecycle` crossed a boundary.
- Each new case was additionally booted by hand on both machines and
  its console transcript read line by line, not merely matched.

### Findings

1. **A DONATION CANNOT BE A GIVE** — the design gap above. It is the
   headline finding and the one thing the lead must rule on.
2. **A give whose tag is also the boot tag hands the child ONE word
   through TWO doors**, and sysapi cannot detect it. Resolving a tag
   consumes no record (D-2's rule), so a donated child meets its own
   Process handle as a register AND as an iterator entry; two live
   wrappers would mean two releases and the second is a `BadHandle`
   the program did nothing to deserve. It is sound in practice because
   a child's last act is `exit`, which never returns, so no deinit runs
   — but "exactly one owner" is the child program's obligation here
   rather than the type system's, which is the one place in the `sos`
   module where that is true. Recorded at `Process(boot_handle:)`, at
   `BootHandle.take_process` and in `child-drain`'s header. A unit that
   wants it closed should consider whether `start(boot_tag:)` ought to
   CONSUME the record it resolves.
3. **`FaultReason.DuplicateKey`'s text still names attachments.** "an
   attachment already uses that key" was written for §2.2's keys and
   now covers a give's tag as well. The string is ASSERTED by a shipped
   transcript (`event_dupkey`) and this unit authorised no expectation
   changes, so it did not move; the generalisation is recorded at the
   declaration and `give_duplicate_tag` asserts the same sentence. A
   later unit that touches that row should widen it to name the key
   rather than the attachment.
4. **No compiler defect was hit.** One ordinary Saw limitation was met
   and is the documented one: DF-172d — a binary expression does not
   wrap unless brackets enclose it — so a four-clause condition in
   `child-drain` is bound to a parenthesised `let` first. Everything
   else the unit needed worked as documented on the pinned toolchain:
   an `&var self` method disarming its own field, a by-value NoCopy
   parameter moved into a `var` local for the same purpose, a second
   `Optional<NoCopy>` slot on a `NoCopy` record, `Optional.take`
   through a new accessor, a three-case `match` on a raw-backed enum
   inside a `let`, and a `&ProcessObject` forwarded from a match arm.

### Docs updated

spec §2's Process row (`Give`, the `boot_tag` start, the per-process
drain, the re-ruled child mint); §3's universal-low-byte bullet (the
`Transfer` bit's first consumer and which defaults mint it), the
no-amplification amendment (a give is a move, not a mint), and the
transfer-funnel paragraph (its first consumers, and the one wrapper
that is disarmed rather than consumed); §11's launch-flow entry
flipped to BUILT with the three things still absent named; §12 gained
the per-process boot set, the tag-is-the-identity rule, the freeze and
the `start(boot_tag:)` sentence. `sosabi`: `ProcessOp.Give`,
`BootTagForm`, `BootHandleKind.Process`, `rights_allow_transfer`,
`child_process_rights`'s re-rule, `ProcessRight`'s second meaning for
`Manage`, and a note at `DuplicateKey`. `kcore.objects`' boot-queue
section rewritten for the per-process shape; `kcore.dispatch`'s
`process_start`, `process_create` mint and `boot_handle_next`
docstrings — every unit-2 "unit 3 revisits" note now resolves;
`kcore.sched`'s teardown. `sysapi`: the give funnels, the three `start`
forms, `Process(boot_handle:)`, `BootHandle`'s second slot, and the C
floor's two new/changed exports. Design 3's finding 2 gained unit 5.5
as its owner. Tracker closed in place.
