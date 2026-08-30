# SawOS design 11 — the M3 docs sweep (M3 unit 7, the close)

Status: AUTHORED Aug 30 2026 (lead). The milestone's last unit —
sawlang#232 item 7 plus the debts individual units recorded and
deferred here. DOCS ONLY: spec.md and doc comments; no op, no right,
no behavior. Transcript expectation: **ZERO moved rows** — the gate
still runs (doc comments live in compiled files), and every row must
come back byte-identical.

## The owed list (each with its source)

1. **§3 FULL REWRITE** (owed since the Aug-17 handle-lifecycle
   ruling; sawlang#232 "THE §3 REWRITE THIS OWES"): the no-duplicate
   rule's real invariant was never uniqueness — it is **NO
   AMPLIFICATION**. A mint always carries the kind's DEFAULT set (or
   narrows it by keep mask); minting authority is itself rights-gated;
   attenuating a handle you give away is meaningful exactly when the
   receiver lacks its own minting authority. State it with the clock
   example so mint-per-call is never read as a hole in attenuation.
   Fold in what M3 actually built: MINT_OP universal, keep masks,
   give-moves-rights-verbatim, and unit 6's exec-gate as the worked
   example of policy-by-mask. §3 still describes the pre-2.75 world;
   this is a REWRITE, not an amendment.
2. **§2.3 Memory rename** (sawlang#232 item 7): the section still
   speaks the Jul-29 draft's vocabulary; align it with the built
   §2.5 surface (Memory/IoMemory/Mapping, split/carve/map/give) and
   point at the shared-memory demonstration that now EXISTS
   (share-double-map — two processes, one region, proven bytes).
3. **§5.7 amendment** (sawlang#232 item 7): rights-gated ops as
   built; the boot set stays ONE HANDLE WIDE at the entry register
   (everything else drains); the copy funnels as the only
   user-memory doors.
4. **The DMA-TCB note** (178 round 4, carried by 232): a DMA-capable
   device's driver is inside the TCB until an IOMMU exists — the
   sentence the M4+ IOMMU item hangs off. Place it where device
   grants are ruled (§2.5/§9 seam).
5. **§11 REFRESH for M3**: the full what-is-built ledger pass. Unit
   6 flipped its own rows and the roadmap line; the sweep OWNS the
   whole section — every M3 unit's rows present and accurate, counts
   current (158/79×2), stale "will"/"remains" sentences flipped or
   struck, M4 pointed at designs/010 (the ruled plan of record).
6. **The consistency pass the per-unit edits could not do**: grep
   for stale promises across spec.md and the tree's doc comments —
   "unit 5.5 will", "M3 will", "not yet built", the §8 kill/thread
   deferrals stated once and correctly, `Process.attach` mentions
   (all should be gone — the respell landed), `SegFlag.Device`
   dormancy stated where the loader documents it. Fix what the grep
   finds; list what was found in the As-built.

## What this unit does NOT do

No behavior, no renumbering, no test changes, no new sections for
M4 (design 10 is the M4 record; spec §2.1 gains nothing until M4
units land it), no HANDOFF/tracker archaeology beyond the entry this
brief adds.

## The proof

The gate, both arches, EVERY row byte-identical — a docs unit that
moves a transcript row has changed something it claimed not to.
The As-built lists every section touched and every stale sentence
the consistency grep caught.

## Docs owed (meta)

Tracker entry closed in place; As-built here filled; SL-11+ only if
a genuine language deficiency is met (unlikely in prose).

## As built

Status: BUILT Aug 30 2026. Gate: **158 passed across both profiles (79
cases each), and EVERY ROW BYTE-IDENTICAL** to a transcript captured at
the merge base (951c58d) before a character was edited. The check was the
whole 305-line transcript, not a row count: `diff` is empty, `cmp` says
identical, and both files hash to
`d8292bfbe2623c05f42150a1fe9d5246d0e90d5e0af25cf4b6068fc107798c34`.
`thread_preempt` and the two `preempt_*` cases ran on both profiles and
did not flap, so the one tolerated timing exception never had to be
invoked. Which is the claim this unit had to make: a docs unit that moves
a transcript row has changed something it said it would not.

A second, independent proof that nothing could have moved: `git diff --
kernel/ hal/` filtered to non-comment lines is EMPTY. Every changed line
in compiled source is a `///`, `//!` or `//` line, and every doc block
still documents the declaration it documented before — no marker moved,
no declaration moved, so design 121's placement rules were never in play.

### Sections as landed

**§3 — REWRITTEN, not amended** (the brief's item 1, owed since the
Aug-17 handle-lifecycle ruling). The section now leads with **THE
INVARIANT IS NO AMPLIFICATION — and it always was**, quotes the Jul-29
"no DUPLICATE right" rule verbatim, and kills it clause by clause: asking
twice gives two handles (mint-per-call), `MANAGE` does not exist so there
is no creator-holds-MANAGE to re-mint through, and attenuation is an
operation a holder performs. What survives is named as the property the
rule was protecting all along — *no sequence of asks yields authority the
asker did not already have* — with uniqueness demoted to the mechanism it
was. Under it, **three facts hold the invariant**, each a line of kernel:
a mint carries the kind's default set or a subset of the source; the
authority to mint is itself rights-gated (the five bits named); a give
moves rights VERBATIM. Then **the clock worked example**, written so
mint-per-call can never be read as a hole: the ask is gated too, so a
process with `ClockGet` masked out cannot ask at all and a narrowed Clock
handle it was given is the only clock it will ever have — attenuating a
handle you give away is meaningful precisely when the receiver lacks its
own minting authority, which is the launcher's ordinary case. Extra
instances buy OWNERSHIP, not authority. `MINT_OP`'s four properties are
kept intact and now read as the attenuate op §3 lacked; the monotonicity
bullet gains **unit 6's exec gate as the worked example of
policy-by-mask** — `MemoryRight.MapExecute` is minted at boot of
necessity and simply never granted, so "only root maps executable" is
capability FLOW and the kernel asks no question about who is calling.

**§2.5 — the Memory rename** (item 2). Two stale `§2.3` pointers fixed
(§2.1's shared-memory reference; the struck §2 row's own note already
explained itself and was left). A **NAMES AS BUILT** paragraph states the
rename once — `Memory` / `IoMemory` / `Mapping`, `ObjType` 9/10/11, and
the four verbs `split` / `carve` / `map` / `give` — and says the Jul-29
`MemoryObject` spellings elsewhere name the same kinds, so nothing was
renumbered and no wrapper changed. The section head no longer claims the
refcount is missing. The shared-memory demonstration was already named
(`share_double_map`) by unit 6 and is left where it is.

**§5.7 — amended for M3** (item 3), discharging the spec edit design 178
round 4 owed and deferred to this milestone. Three bullets, none of which
moved a register: rights-gated ops are the whole surface and every gate
is named for its op (with §12's "become real objects in M3" line named as
what this amends); the boot set is ONE HANDLE WIDE at the entry register
and four kinds have joined it since M1 without changing that; and the two
COPY FUNNELS are the only places the kernel dereferences a user address,
which keeps that a two-site audit rather than a property of dispatch.

**The DMA-TCB note** (item 4) placed at §2.5, where device grants are
ruled, immediately after the unit-6 give-a-driver-a-device paragraph. It
states the thing a rights mask cannot do: a `map` fences the DRIVER, not
the DEVICE, so a DMA-capable device's driver is a TCB member until an
IOMMU exists. It carries the ruled architecture (userspace IOMMU driver,
Memory handles over IPC), marks the first prerequisite LANDED (unit 5.5's
death notifications) and the second unbuilt (critical processes,
fail-stop, checked at the top of teardown ahead of any release), and says
why it costs the tree nothing today — both v1 consoles are
programmed-I/O. §11 gained a matching "what remains" row.

**§11 — refreshed whole** (item 5). "What the kernel does NOT have after
M2" → after M3. The M3 roadmap entry became a LEDGER: one line per rung,
each naming the sawos design that ran it (1, 1.5, 2, 2.75, 3, 4, 5, 5.5,
6, 7), ending in this unit and the statement that the gate at M3's close
is **79 cases per architecture, 158 runs**, eleven object kinds, Pipe the
one §2 row left. The settled-pins bullet notes that M3 renumbered inside
`kernel/abi/` twice with no userspace edit. The open-pins bullet is
restated: the physical-region refcount pin now has a caller AND a
neighbouring mechanism and still no home, because unit 5's count answers a
different question (slab slots, not bytes). Pipes point at
`designs/010-m4-pipes.md` as the ruled plan of record, naming what this
section will inherit (the `PipeInlet`/`PipeOutlet` pair, `post` as the one
kernel primitive with send/timeout as library compositions,
`ProcessOp.PipeCreate`, and the peer-gone doctrine) without moving any of
it into §2.1 — §2.1 gains nothing until M4 units land it, per the brief.
Priorities and the roadmap line both gained the M4 pointer.

### The consistency grep — the FULL catch list

Twenty-six stale sites, all fixed. Sixteen in spec.md:

1. §2.1's "bulk data goes through shared memory (§2.3)" — a pointer at a
   section number that does not exist.
2. §2's `MemoryObject` row: "Free-on-last-reference … is NOT built … it
   lands with unit 5's quotas." Unit 5 landed it.
3. §2.5's head: "everything this section describes exists now except the
   refcount".
4. §2.5's Deinit bullet: "Tying the row's lifetime to a wrapper wants the
   refcount immediately below, which is unit 5's." Unit 5 landed the
   refcount and RULED THE OTHER WAY (D-2) — now recorded as a ruling
   rather than a wait.
5. §2.5's "THE REFCOUNT INVARIANT IS NOT BUILT" header — scoped to unit 4,
   since the paragraph under it already says unit 5 built and narrowed it.
6. §3: "there is no attenuate op yet … building one is the unit that
   needs it" — written inside the very unit that built `MINT_OP`.
7. §3's Jul-29 no-duplicate tail (three false clauses) — the rewrite.
8. §8: `process.kill()`'s deferral stated TWICE with contradicting
   reasons, the second on the expired one-process argument. Now stated
   ONCE, with its current reason, and the M2-era sentence records that it
   was moved and why.
9. §11: "What the kernel does NOT have after M2."
10. §11: the physical-region refcount pin, written as though unit 5 had
    not run.
11. §11's unit-4 memory row: "FREE-ON-LAST-REFERENCE, which is unit 5's".
12. §11's priorities row: ruled to stay through M3, with nothing saying
    what follows M3.
13. §11's pin on §2.1's message limits (64-byte body / 4 handles) —
    superseded by designs/010's ruling 6 with nothing recording it.
14. **Twenty-one `sos/`-prefixed paths** across §2, §5b, §5c, §9, §9b and
    §11 — `sos/kernel/`, `sos/hal/`, `sos/rt/`, `sos/root/`. The tree
    FLATTENED at the split (sawlang#238 D-e, Aug 28) and the spec never
    caught up, so every directory the M1/M1b/M2 entries name was
    unfollowable. Corrected wholesale, with the flattening recorded once
    at the roadmap bullet so an M1-era reader knows what happened.
15. §12: "Both become real objects in M3" — half true. The pool roots did;
    the root IRQ TABLE deliberately did not (`ProcessRight.InterruptBind`
    stayed a right). Corrected at the site, with §5.7 carrying the rule.
16. §8's unit-2 paragraph: "observing a child's death as an event is unit
    5.5's … and until then a supervisor reads `get_status`".

Ten in doc comments:

17. `kernel/sysapi/src/system.saw` — `Process`'s docstring: "holding
    somebody else's is what supervision WILL look like when there is
    somebody else (M3)". There is; it now names the four things a
    launcher does through the handle, `Waiter.add(process:)` included.
18. `kernel/sysapi/src/memory.saw` — `split`'s docstring claimed a dropped
    piece gives up "not even the kernel slab slot, which returns at the
    owner's teardown", and that a splitting loop "runs out of SLOTS long
    before it runs out of pool". Both were reversed by unit 5, and
    `tests/memory-split`'s own transcript says so (twenty rounds past a
    slab of sixteen). The sharpest catch in the sweep: a shipped
    transcript and a doc comment in the same tree disagreeing.
19. `kernel/sysapi/src/mapping.saw` — "Tying the two lifetimes wants the
    refcount §2.5 describes, which is unit 5's". Now records D-2's ruling
    and that the Deinit is DECLINED, not blocked.
20. `kernel/core/dispatch.saw`, `process_create` — "real accounting
    arrives with unit 5's quotas and free-on-last-reference".
21. `kernel/core/dispatch.saw`, `MemoryOp.Split` — "Free-on-last-reference
    is unit 5's, so the slot comes back at teardown and not before".
22. `kernel/core/dispatch.saw`, `MappingOp.Unmap` — "Refcounted early
    reclamation is unit 5's, for this slab and for the others together".
    Rewritten to state both husks (unmapped-but-referenced,
    mapped-but-unreferenced) and that `Unmap` itself still frees no slot.
23. `kernel/core/limits.saw`, `MAX_MEMORIES` — "a split is permanent until
    its owner's teardown and a long-running allocator would meet this
    number". It bounds CONCURRENT regions now; the pool is what a loop
    meets.
24. `kernel/core/sched.saw`, teardown — "unit 5's free-on-last-reference
    is where returning a range to an owner becomes an operation". The
    loop is a BACKSTOP now; returning a RANGE is still nobody's operation.
25. `kernel/abi/src/lib.saw`, `process_rights` — "`InterruptBind` is the
    bit M3 will stop handing out freely". **A PROMISE THAT DID NOT COME
    TRUE**, and the reason is unit 6's recorded finding: `ProcessSelf`
    mints the one Process default set and takes no keep mask, so a
    launcher's mask reaches the child's SYSTEM handle and never the
    Process handle the child derives from it. Rewritten to record the
    miss, the reason, why nothing in v1 needs it, and where the fix is
    tracked (designs/010's agenda-7b keep-mask rider).
26. `hal/riscv32/kernel/lib.saw`, `GRANT_ROW_BUDGET` — "Today there is no
    quota, so meeting it is ordinary." The quota is built, so the
    kernel-bug assertion this note was holding a place for is now law;
    the note states what makes it unreachable (every process's mapping
    row is clamped to its free grant rows, root's included) and names
    `quota_vs_wall`. Profile B's twin carries no quota sentence and
    needed no edit.

CHECKED AND LEFT ALONE, so the reader knows they were looked at:
`Process.attach` (below); `SegFlag.Device`'s dormancy, already stated
completely and correctly at `kcore.loader`'s `check_device_grant`
docstring — dormant path, sawlang-side flag, the four in-tree consumers,
pin-bump retirement — so the brief's item was already satisfied;
`tests/memory-split`'s header, already flipped by unit 5;
`dispatch.saw`'s clock-quota comment; and roughly forty other `will` hits
in doc comments that are ordinary future tense about runtime behaviour
rather than stale promises.

### Deviations, argued

**`Process.attach` was NOT deleted from four doc comments, and the brief
said all mentions should be gone.** No LIVE surface survives — the API is
`Waiter.add(process:, key:)`, and the grep confirms there is no
`Process.attach` declaration, call site, or spec sentence anywhere. What
survives is four HISTORICAL mentions, each of which states the
retirement in the same breath: `waiter.saw`'s module header ("unit 5.5
shipped `Process.attach` in the interim"), `system.saw`'s comment above
the `extension Waiter` explaining why the overload lives in the wrong
file ("`Process.attach` was the workaround — SL-7's third site"), and two
in `lib.saw`'s facade header, which narrate SL-7's three sites and then
its close. Deleting them would erase the only explanation of a
non-obvious placement — an `extension Waiter` declared in `system.saw` —
and leave a future reader with no way to know why. They are argued at
their sites and left. The rule the sweep enforced instead: no
`Process.attach` may read as CURRENT API, and none does.

**Twenty-one path corrections are more than a docs sweep's usual
mandate.** They are in scope on the brief's own terms — the consistency
pass exists to catch what per-unit edits could not, and a directory the
document names that does not exist is a stale fact of exactly that kind.
The whole change is a prefix strip plus one recording sentence, and it
was diffed line by line before and after.

**§2.1 gained nothing**, though designs/010 ruled `post` the primitive
and superseded §2.1's own message limits. That is the brief's explicit
instruction (design 10 is the M4 record; §2.1 gains nothing until M4
units land it), so the supersession is recorded as a FORWARD POINTER in
§11's pin list, where a reader looking for what is unbuilt will find it,
and §2.1's ratified text is untouched.

### Findings for the lead

**F1 — a doc comment promised a rights change M3 did not make, and the
code is right.** `process_rights()`'s note said `InterruptBind` is "the
bit M3 will stop handing out freely". M3 did not, and cannot with the
surface it has: `SystemOp.ProcessSelf` mints the ONE Process default set
and takes no keep mask, so a launcher's attenuation reaches the SYSTEM
handle it gives a child and never the Process handle the child derives
from it. Unit 6 recorded this (design 9, §9's driver-child bullet) and
ruled that nothing in v1 needs it. Per the brief this was NOT fixed in
code — the prose now states what the code does, names the gap, and
points at designs/010's agenda-7b keep-mask rider as where it is
tracked. If the lead wants the gap closable, the shape is a keep mask on
`ProcessSelf`, and it is an M4 unit's call rather than a docs unit's.

**F2 — the physical-region refcount pin (§5.9) should probably be
re-scoped rather than left open.** It has been "open" since M1 and now
reads oddly: unit 5 built A refcount, just not that one (handle entries
naming a slab slot, versus references to a physical range). §11 now says
so explicitly, but the pin as WORDED still asks where a per-physical-
region count lives, and the honest answer after M3's object-counting
ruling is that v1 has no such count and will not until a real allocator
exists. Recommend the lead either re-word the pin to "byte accounting and
pool returns, M4+" or close it with that as the answer.

**F3 — `designs/010` has one wrong unit number, unedited here.** Its unit
1 bullet says "kernel interruptibility (M3 unit 0)"; interruptibility is
M3 unit 1.5 (`designs/001`). Left alone because this unit is spec.md and
doc comments only and 010 is a ruled design record — flagged so the lead
can correct it in place or let M4's first unit carry it.

**F4 — no SL entry was opened.** No language deficiency was met: this
unit wrote prose, and every doc-comment edit stayed inside an existing
`///` / `//!` / `//` block with its documented declaration unmoved, so
design 121's placement rules were never under pressure.
