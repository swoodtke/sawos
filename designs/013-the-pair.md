# SawOS design 13 — the pair: PipeInlet / PipeOutlet (M4 unit 1)

Status: AUTHORED Aug 31 2026 (lead). Ladder unit 1 of the ruled plan
of record (designs/010): **the two endpoint kinds exist, messages
flow one direction, and everything polls.** No reply path (the
one-shot pair is unit 2's), no handles in messages (unit 4's), no
waitability and no parking (unit 3's) — unit 1 is the connection as
an OBJECT, with the refcount, quota and peer-gone machinery every
later unit stands on.

## The ruled surface (all from designs/010, cited per item)

- **Kinds: `PipeInlet` (client — requests flow IN here) and
  `PipeOutlet` (server — they come out here)** [ruling 2]. Two
  `ObjType` cases, two handle kinds, two rights enums named for
  their ops (Aug-29 doctrine): `PipeInletRight { Transfer, Mint,
  Post = 1<<8 }`, `PipeOutletRight { Transfer, Mint, Take = 1<<8 }`.
  Wait rights arrive with waitability (unit 3).
- **The factory: `ProcessOp.PipeCreate` on `ProcessRight.PipeCreate`**
  [ruling 3], the new bit joining the ONE Process default set. Both
  ends are minted into the CREATOR's table and wired outward with
  `give`. Creator-pays: one `QuotaKind.Pipe` row, charged at create,
  credited when the CONNECTION frees (below).
- **Limits as build defines** [ruling 6]: `PIPE_BODY_BYTES = 128`
  and `PIPE_INFLIGHT = 2 * MAX_THREADS` land in `kcore.limits` as
  named statics with static_asserts pinning §2.1's ranges.
  `PIPE_MSG_HANDLES = 4` is DECLARED WITH UNIT 4, which is the first
  unit that reads it — an unused constant is a promise, not a record.
- **The staging is a per-connection RING of fixed slots** [ruling 6's
  amendment to §2.1's "one fixed slot"]: `PIPE_INFLIGHT` deep, each
  slot `PIPE_BODY_BYTES` wide, inline in the connection slab — zero
  dynamic kernel allocation, the property the ratified sentence
  existed for.

## D-1: one slab, two counted kinds

One `PIPES` slab holds the CONNECTION: state, the ring
(head/count/slots), and **TWO reference columns — `inlet_refs` and
`outlet_refs`** — each maintained by its own kind's handle entries
exactly as every counted kind's column is (design 7's sites). This
is D-1's "roles are objects" made mechanical without a second slab:
the two `ObjType` kinds TARGET THE SAME PIPE SLOT, and which column
a handle counts on is decided by its kind, which dispatch establishes
before anything else (§3 order).

- **A side reaching ZERO raises PEER-GONE on the other** [D-1]: the
  slot's state records which side died. In unit 1 (nothing parks)
  that is visible only as STATUS ANSWERS — see D-2's zero-arms.
- **The connection slot frees when BOTH columns are zero**, and that
  is when the quota row credits. The counted-kinds table gains two
  rows; the compiler enumerates the arms (the design-8 precedent —
  say in the As-built what it caught).
- Attach of either kind is `NotWaitable` in unit 1 — the refusal arm
  states that unit 3 removes it (the §8-thread-deferral pattern).

## D-2: the two data ops, and the two new statuses

- **`PipeInletOp.Post`** (`PipeInletRight.Post`): copy the caller's
  body (addr + len, len ≤ `PIPE_BODY_BYTES`, len 0 LEGAL — a
  data-free message is §2.1's option) IN through the one funnel,
  into the next ring slot. Ring full answers **`WouldBlock`** —
  caller-checkable, transient, never a park [ruling 4: post never
  suspends]. NOTE: in unit 1 `Post` answers plain status — the
  `PipeReplyHandle` it will return is UNIT 2's, and the op growing a
  return value between units is what §5.7's renumberable-ops
  discipline makes free. Say so at the op.
- **`PipeOutletOp.Take`** (`PipeOutletRight.Take`): nonblocking —
  copy the oldest staged message OUT through the funnel (caller
  buffer ≥ `PIPE_BODY_BYTES`; answers the length), or **`WouldBlock`**
  when the ring is empty. The typed wrapper surfaces design 234 §4's
  two-channel shape: `Ok(Some(msg)) / Ok(None) / Err(...)` — a
  would-block is NOT an error at the typed tier.
- **Two new `SosStatus` cases, appended**: `PeerClosed = 9` (§2.1's
  own word, the peer-gone doctrine's second instance) and
  `WouldBlock = 10` (one transient for both directions: post-full and
  take-empty; `describe()` worded for the condition, not the op).
  Lean recorded, agent may argue the naming on the design-234
  narrowest-type doctrine.
- **The zero-arms** [D-1 + design 230's drain precedent]:
  - outlet_refs hits zero → a later `Post` answers `PeerClosed`
    (nobody can ever take), and the ring's staged bodies are DROPPED
    at that transition (data was copied, nothing owns anything —
    state it at the site).
  - inlet_refs hits zero → **staged messages DRAIN FIRST**: `Take`
    keeps answering messages until the ring is empty, THEN
    `PeerClosed` — design 230's close-drains-first rule, terminal
    thereafter (no new sender can appear: attenuation is monotonic
    and the count is zero). This is the message-model reading of
    D-1's peer-gone; the "unwound" clause in 010 D-1 is about
    HANDLES-in-flight, which unit 1 does not carry.
- Faults stay faults: bad handle, unspent right, malformed
  word/length — the existing vocabulary, no new cases.

## D-3: creation, give, and the boot drain

- `PipeCreate` answers BOTH handles through a COPY-OUT RECORD (two
  words, offsets published as constants beside the wait record's) —
  the one-op-one-return convention cannot carry two words in a0, and
  the copy funnel is the precedented door (Clock's `Now`). Lean
  recorded; the agent may argue an alternative shape with the §5.7
  doctrine in hand.
- **`give` carries both kinds** (kernel give is kind-generic
  already): sysapi grows `Process.give(inlet:)` / `give(outlet:)`
  funnels beside the five that exist, `BootHandleKind` grows two
  cases, and `BootHandle` grows the matching `take_*` accessors.
  The sysapi endpoint modules sit BELOW `system` (like
  memory/iomemory); the facade re-exports `PipeInlet`/`PipeOutlet`.
  (Extension lookup follows `public import` since 0.2.0 — placement
  is unconstrained; put methods where they read best.)
- Typed wrappers: `NoCopy`, drop-is-release, the NO_HANDLE disarm
  sentinel — the nine siblings' exact shape.

## D-4: what composes, untouched

Teardown: close-all releases entries, the columns fall, the
zero-arms fire — NO new teardown arm (the fused-call claim arm is
unit 3's, with `Call`). `has_external_wake_source`: untouched,
nothing parks. Interruptibility: the two copies are bounded by
`PIPE_BODY_BYTES`; note whether the preemption-point rule wants a
point (M3 unit 1.5's threshold decides — record the answer either
way).

## The proof (harness)

1. **`pipe-basics`** (root-only, both ends held): round trip
   (post → take, bytes identical, transcript proves the payload);
   FIFO across several messages; zero-length message; ring depth
   COUNTED (post until `WouldBlock` — the count printed must equal
   `PIPE_INFLIGHT`); take-empty answers the typed `Ok(None)`.
2. **`pipe-peer-gone`**: drop the outlet, post answers `PeerClosed`;
   fresh pair, stage messages, drop the inlet — takes DRAIN all
   staged messages then answer `PeerClosed` (the transcript shows
   drain-then-terminal); a second take still `PeerClosed` (nothing
   un-closes).
3. **`pipe-child`**: root creates the pair, `give`s the INLET to a
   child (boot drain), keeps the outlet; the child posts one
   message; root polls take until it arrives and prints the bytes —
   `give`/drain/`Transfer` proven for the new kinds, the seed of
   unit 5's client.
4. Negative arms where they are cheap: post through a mint lacking
   `Post` (refused); oversized len (`BadArg` fault); attach →
   `NotWaitable`.

Transcript expectation: existing rows byte-identical or address-only
(kernel and images grow); new rows only. Poll loops must be BOUNDED
(a counted retry with a timer-park between rounds, the existing
harness pattern) — never a spin the deadlock report can't see.

## Docs owed

spec §2 Pipe row (BUILT M4 unit 1: the slice, named honestly — no
reply path yet), §2.1 annotated (built-so-far markers; nothing
ratified reopens), the counted-kinds table (+2 rows), quota table
row, §11; tracker entry closed in place; As-built here (what the
compiler enumerated, deviations argued, findings); SL-13+ only if a
genuine deficiency is met. CLAUDE.md's idiom ruling applies to all
new Saw (statement-position checks stay `match` per SL-12).

## Out of scope

The one-shot pair and abandonment (unit 2); waitability, peer-gone
LEVELS, `Call`/`ReplyRecv`, the send wrappers (unit 3); handles in
messages (unit 4); the keep-mask rider (2.5/4); the money shot
(unit 5); kill; namespaces.

## As built

**LANDED Aug 31 2026.** Suite 170/170 (85 cases/arch, riscv32 +
arm64) from 160/160; FIVE new cases across six new packages, and no
existing transcript row moved.

**VALIDATED ON sawlang 0.2.1**, which landed in the toolchain checkout
before the first build of this unit — so the baseline and the gate were
compiled by the SAME compiler and the transcript diff below is
attributable to this change alone. The tree's `sawlang.pin` still reads
0.2.0; the bump is the lead's at integration. Nothing here depends on
anything 0.2.1 added: the one language fact this unit leans on is
0.2.0's extension lookup following `public import` (SL-7), which is what
lets `pipe.saw` hold two types and lets the facade carry their methods.

### Transcript accounting

Whole-transcript diff, merge base af3c948 vs the gate, ANSI stripped and
bucketed:

| bucket | rows | what |
|---|---|---|
| byte-identical | 160 case rows | every pre-existing case, same text, same order. `thread_preempt` and `timer_interval` — the two known timing rows — included |
| address-only | 0 | nothing printed an address that moved |
| authorized-with-cause | 160 case-row indices | the DENOMINATOR alone, `[n/80]` -> `[n/85]`, because five cases were appended. The `✓ name` half of every row is unchanged |
| authorized-with-cause | 78 image-size rows | every `.sosimg` that links the `sos` facade grew, because the facade now compiles `sos.pipe` into the unit and `sosabi` carries two more kinds. +88 B to +4096 B; the arm64 spread is page granularity (55 arm64 rows did not move at all, and the ones that did moved by exactly one page) |
| authorized-with-cause | 1 image-size row | `child-quota` on riscv32 SHRANK 80 bytes. Code-layout noise from `QuotaKind`/`SosStatus` gaining cases; the case's own expectations are unchanged and still exact |
| authorized-with-cause | 1 summary row | `160 passed` -> `170 passed` |
| documented-nondeterministic | 0 | none met |
| new | 10 case rows + 12 image rows | the five cases and six packages, on both profiles |

No row is unaccounted for. The two behavioural claims worth stating:
EVERY pre-existing case's expectations — teardown handle counts, fault
tags, status words — still match EXACTLY, which is what says the
two new `ObjType` kinds, the two new statuses and the tenth quota column
moved nothing; and no existing `.saw` file's behaviour changed, only its
size.

### The slab, as landed

`kcore.objects` holds `PIPES: [PipeSlot; MAX_PIPES]` beside the three
memory slabs, and the slot is SIX fields:

```saw
PipeSlot { state, process, head, count, inlet_refs, outlet_refs }
```

Three things about it are worth reading:

- **THE TWO COLUMNS ARE THE PEER-GONE STATE, and no flag beside them
  says so.** "The inlet side is gone" IS `inlet_refs == 0`, because a
  column can only fall and can never rise — what would raise it is a
  handle onto that end, and a mint needs a source there is none of. So
  the terminal level is a fact about the count rather than a bit
  somebody must remember to set, and the two can never disagree. That
  is a simplification the brief did not ask for and D-1's monotonicity
  argument hands over for free.
- **THE STAGING IS SEPARATE STORAGE**, not a field. `PIPE_INFLIGHT ×
  PIPE_BODY_BYTES` is 2 KiB per connection, and a slot struct holding
  one would make every `PIPES[slot] = PipeSlot(...)` a multi-kilobyte
  memset inside a syscall. So the bodies live in a flat
  `PIPE_BODIES: [UInt8; MAX_PIPES * PIPE_INFLIGHT * PIPE_BODY_BYTES]`
  with a parallel `PIPE_LENS`, indexed the handle table's own way. Both
  are all-zero initializers, so they cost NO IMAGE BYTES (design 149)
  and land in `.bss`; the whole slab is 8 KiB + 128 B at the numbers
  below.
- **NO TAIL INDEX.** A ring is `head` plus `count`; the tail is
  `(head + count) % PIPE_INFLIGHT`, derived at the one site that needs
  it, because a second index is a second thing to keep in step.

Numbers: `MAX_PIPES = 4`, `PIPE_INFLIGHT = 2 * MAX_THREADS = 16`,
`PIPE_BODY_BYTES = 128`, `DEFAULT_QUOTA_PIPES = 2`.

### The zero-arms, as landed

Both live in `kcore.refs`' `drop_reference`, which is the only function
that observes a transition — `free_object` runs only when BOTH columns
are zero, so peer-gone has no other site it could be written at. Each
arm decrements ITS column and then answers about the PAIR, which is
what makes `unref_object`'s "zero frees" mean "both-zero frees" without
that function knowing two kinds are involved.

- **outlet → 0**: `pipe_drop_staged(slot)` right there. Nobody can ever
  take again, so messages that could never be delivered are dropped —
  two assignments, because a staged message is BYTES THAT WERE COPIED
  and nothing owns anything. `Post` then reads the column and answers
  `PeerClosed`; it never has to look at the ring.
- **inlet → 0**: NOTHING. The absence is design 230's close-drains-first
  rule made mechanical: `Take` hands over everything staged whatever
  became of the sender, and only a dry ring consults the column. So
  drain-then-terminal is an ORDER OF TWO COMPARISONS in `pipe_take`
  rather than any state anybody maintains.

`free_object` gains two arms that are one act written twice
(`free_pipe`), and they have to be: the matrix is over `ObjType`, and
reaching it through either kind means both columns are already zero.

### What the compiler enumerated

Adding two `ObjType` cases broke, and would not build until answered:

1. `kcore.refs.ref_object` — 2 arms (which column a bind counts on).
2. `kcore.refs.drop_reference` — 2 arms. **This is where the design
   actually got decided**: the matrix demanded an answer to "was that
   the last" for a kind whose object has two names, and writing
   `(inlet_refs == 0 && outlet_refs == 0)` is what made both-zero-frees
   a property of the ledger rather than a rule beside it.
3. `kcore.refs.free_object` — 2 arms.
4. `kcore.dispatch.dispatch` — 2 arms (the kind → op-table match).
5. `kcore.dispatch.waitable_slot` — 2 arms. The design-8 precedent
   collected a third time: a kind moving from "not waitable" to
   "waitable" is not a new case, so the `NotWaitable` refusals are
   written out with the note that unit 3 removes them.
6. `kcore.dispatch.boot_kind_of` — reached through the `_` arm, so the
   compiler did NOT ask; the two arms were added because
   `pipe_*_rights()` mints `Transfer` and a givable kind with no record
   spelling would arrive mis-tagged. That `_` arm is the one place in
   this unit's blast radius where the enumeration property is off, and
   it is deliberate (the arm exists for the kinds nothing can give).
7. `sosabi.SosStatus.describe` — 2 arms, from the two new statuses.
8. `sos.system.decode_boot_handle` — 2 arms, from the two new
   `BootHandleKind` cases; and `BootHandle`'s literal grew two fields at
   all six arms, which is the compiler asking the same question a
   sixth and seventh time.

`QuotaKind` did the same one level down: adding `Pipe` broke
`install_quota_limits` until both policies said what a child may hold,
which is the property that enum's docstring claims for it.

### Deviations from the brief, argued

1. **`PIPE_BODY_BYTES` LIVES IN `sosabi`, NOT `kcore.limits`** (the
   brief, on ruling 6, asks for both limits in `kcore.limits`).
   `PIPE_INFLIGHT` did land there. The cut is that the two numbers are
   different KINDS of thing: a ring's depth is a slab dimension nothing
   outside the kernel needs (a caller meeting a full ring is told
   `WouldBlock` and never has to know how deep it was), while the body
   maximum is A SIZE THE TWO HALVES MUST AGREE ON — the largest `len` a
   `Post` may name, the smallest buffer a `Take` may be handed, and the
   length of the `&[UInt8; PIPE_BODY_BYTES]` the typed methods take. It
   is exactly what `wait_record_bytes()` is, and that lives in `sosabi`
   for exactly this reason. The alternative was to state the number
   twice and pin the two with an assert, which is the skew `sosabi`'s
   own header exists to prevent. Ruling 6's substance — a named build
   define with static_asserts pinning §2.1's range — is executed either
   way; only the module moved.
2. **NO `pipe-not-waitable` CASE** (the brief lists attach →
   `NotWaitable` among the cheap negative arms). It is not cheap: the
   typed `Waiter.add` has no overload for either kind, and the wrappers'
   handle fields are `public(package)`, so a test package cannot reach a
   word to attach. Making it reachable would mean either publishing a
   handle word (against the module's no-raw-word guarantee) or adding an
   `add(inlet:)` overload whose every call is a fault — dead surface, on
   the rule that has `ClockType` declare one domain. **THE TYPED LAYER
   REFUSES IT AT COMPILE TIME**, which is strictly stronger than a
   runtime fault, and it is how every other non-waitable kind in this
   tree is handled: `NotWaitable` has SEVEN arms today and NO test in
   the tree exercises any of them, for this same structural reason. The
   kernel arms exist so unit 3 has a place to remove them from.
3. **`Post` ANSWERS `Result<Bool, SosStatus>`**, not
   `Result<Void, SosStatus>`. The brief specifies the two-channel shape
   for `Take` and states the principle — a would-block is NOT an error
   at the typed tier — as a general one; applying it to `Post` needs a
   success type with room for an absence, and `Result<Void?, _>` is not
   a type worth writing under the visible-`Void` rule. `Ok(true)`
   staged / `Ok(false)` ring full, with the transient out of the error
   channel on both ops.

### Findings

1. **CLOSE IS NOT NEEDED, AND THAT IS A FINDING RATHER THAN A
   DEFERRAL.** §11 has said since M3 unit 2.75 that `close` "arrives
   with Pipe in M4 under a per-kind right". It did not, and should not:
   closing a connection END is releasing the LAST handle onto it, which
   IS the reference column reaching zero — a fact the ledger already
   maintains at every bind and unbind. A `close` op would be a second
   way to reach that state, reachable while a sibling handle is live,
   and would owe an answer for what the sibling then names. So
   drop-is-release IS the close, `let _ = move inlet` is how a program
   says it, and the per-kind close right has nothing left to gate. §11
   is amended to say so.
2. **THE `process` FIELD IS THE CHARGED PROCESS, NOT THE OWNER, AND THE
   TEARDOWN SWEEP INHERITS THAT.** `end_process` frees `PIPES` rows by
   `process == p`, exactly as the Memory and IoMemory sweeps do — so a
   connection whose creator dies while another process still holds an
   end has its slot freed under that holder. This is the EXISTING shape
   (a region a launcher gave a child has had this property since M3 unit
   2, because `give` does not move a slot's `process` field) and it is
   unreachable today for the reason `sched.saw`'s attachment sweep
   records: with `MAX_PROCESSES` at two the only process that can
   outlive a peer is root, and root's death stops the machine. Pipes
   make it slightly sharper — a stale decrement onto a REUSED pipe slot
   would also drop that connection's staged messages, where a stale
   decrement onto a reused Memory slot only miscounts — so it is worth
   naming now. The honest fix is per-object ownership transfer at
   `give`, which is a ruling no unit has asked for.
3. **NO PREEMPTION POINT IS WANTED** (D-4 asks for the answer either
   way). The one unbounded-looking thing in either data op is the body
   copy, and it is bounded by `PIPE_BODY_BYTES` = 128 bytes — an order
   of magnitude under the arena zero design 1's point map exists for,
   and comparable to a boot-handle record's three words. `alloc_pipe`
   scans `MAX_PIPES` = 4. The audit note in `kcore.limits` covers the
   new constants without amendment; recorded at the op section in
   `dispatch.saw`.
4. **THE TYPED `pipe_create` RETURNS A TUPLE, and that is the first
   multi-wrapper answer in the tree.** `BootHandle` needed an
   `Optional` per slot and a `take_*` per Optional because it carries at
   most ONE of six kinds and asking the wrong question must be harmless;
   a pipe-create answer always carries EXACTLY TWO and the caller always
   wants both, so a destructuring `let (inlet, outlet) = move pair`
   moves each into its own binding with no husk left behind. It
   compiles, it is `NoCopy`-correct, and it is the shape to reach for
   when a future op answers with a fixed set of wrappers.
5. **`PipeCreate` TAKES TWO PROCESSES, AND THE ASYMMETRY IS INHERITED.**
   The op mints into `obj.slot` (the process the HANDLE names, where
   every other `*Create` op on this object puts its object) and copies
   the record into the CALLER's memory (the only memory `copy_out` may
   write). They are the same process for every call anything makes
   today. A launcher creating a pipe through a CHILD's Process handle
   would get child-relative words in its own buffer — which is exactly
   what `ThreadCreate` through a child's handle already does. Named
   rather than fixed: closing it wants a ruling about what the `*Create`
   family's receiver means, and that ruling is bigger than this unit.
6. **SL-12 HELD THROUGHOUT.** Every bind-or-bail on a Result in the five
   new packages is the inline `try … catch` guard form; every
   statement-position check on a `Result<Void, E>` stayed `match`, and
   so did the four negative-test matches asserting a specific error
   value. No new sawlang deficiency was met — nothing in this unit
   needed a language feature that is not there.
