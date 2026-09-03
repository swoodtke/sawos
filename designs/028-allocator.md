# SawOS design 28 — M5 unit 5: the real allocator (pool returns + byte accounting)

**Status: BRIEF, Sep 3 2026 — dispatches under `designs/025` (RULED):
D-5 as ruled — return-to-root, release on the SECOND zero, first-fit,
§5.9/F2 closes here.** Arch-free (kernel/core + sysapi only, no HAL
edits); authorized to run in a parallel worktree beside the tier
climbs (025 ruling 8).

## The one-sentence goal

A freed region's RANGE returns to its pool root and can be split out
again — today it returns to no pool and a system with churn leaks
physical RAM permanently (§2.5's own honest deferral) — with the
release firing only when BOTH counts on the region are zero: handle
references AND installed mappings.

## The ruled mechanics (025 D-5, not reopenable here)

- **Return to ROOT**: every `Memory` carries a back-reference to its
  POOL ROOT (the boot-table-minted region it was ultimately split
  from), set at split, carried through re-splits. The derivation
  chain is a cursor artifact, not an ownership tree.
- **Release on the second zero**: the range goes onto the root's
  free list only when the region's handle refcount AND its mapping
  count both read zero. This builds §2.5's quoted invariant as
  written ("freed only when the LAST reference of either kind — any
  MemoryObject handle OR any Mapping — drops").
- **First-fit** from the free list at `split`, falling back to the
  existing front-cut cursor; ADJACENT free ranges COALESCE (with
  each other and with the cursor frontier where they touch it).
- **Byte accounting** is the pool's ledger (bytes outstanding vs
  free), kept internally; quotas STAY object-counted. NO new op in
  v1 — the observable proof is behavioral (below). The ledger is
  the §5.9 answer; if a later unit wants it readable, an op can
  front it without a surface break (the design-16 pattern).
- **`IoMemory` untouched**: carve leaves the parent whole, MMIO is
  pinned, its counts stay effectively pinned — §5.9's own carve-out,
  now stated at the mechanism.

## What this supersedes, named honestly

**Design 7 D-2's husk arm changes.** Today a mapped-but-unreferenced
Memory (every handle released, a row still installed) frees its
SLOT and leaves the row. Under the second-zero rule the slot
SURVIVES while any mapping counts against it — it is what carries
the root back-ref and the counts — and when the last row goes (an
`unmap`, or the target process's teardown clearing its rows), the
range returns to the pool. Consequences:

- "Dropped without unmap = permanent, safe-but-leaked" narrows to
  "leaked until the TARGET PROCESS's teardown" — teardown now
  RECYCLES, which is the point of the unit.
- `mapping_slot_free`'s proof (the husk arm) is superseded: ITS
  TRANSCRIPT ROWS ARE AUTHORIZED TO MOVE BY THIS BRIEF, by name, as
  the direct consequence of the ruled second-zero condition. The
  case retargets to the new invariant: the slot survives the handle
  drop, the range returns at the last unmap, and a fresh split can
  then produce the same bytes. No OTHER case's rows are authorized
  — anything else moving is a finding.
- The Mapping side needs a region back-reference (or the count kept
  on the MemorySlot, incremented at `map`, decremented at unmap and
  at teardown's row clears) — the agent picks the bookkeeping that
  keeps design 7's one-ledger doctrine and says why at the site.

## Design points for the implementing agent

- **Free-list storage**: a fixed slab of range nodes
  (`MAX_FREE_RANGES`, sized generously — nodes are two words plus
  links). EXHAUSTION MUST NOT FAULT A RELEASE (release is
  infallible doctrine): coalesce-first, and if a range still finds
  no node, LEAK IT HONESTLY — a per-pool dropped-bytes counter and
  a doc comment; never a silent loss, never a refused release. This
  slab is D-6 donation's named first customer when that unit lands.
- **Pool-root lifetime**: root servers hold the boot-minted roots
  for the machine's life today; if a root's OWN counts ever reach
  the second zero, its ranges and free list die with it — state the
  behavior at the site (the boot set makes it unreachable in v1,
  and a probe-comment beats an untestable branch).
- **Cursor interplay**: the front-cut cursor stays (it is the empty
  free-list fast path and the tier-2 story's simplicity); a free
  range ADJACENT to the unspent frontier folds back into it, which
  is what lets a fully-freed pool return to its boot state — the
  cleanest possible proof of "spent bytes come back".
- **No ABI motion**: no new ops, kinds, rights, or statuses. The
  entire unit is `kernel/core/objects.saw`-side bookkeeping plus
  whatever record fields it needs. (If the sosabi split — design 26
  — has landed, the records live where it put them.)

## The proof (new case, plus the retarget)

- **`memory_recycle`** (new): split a pool to `NoResource`, release
  the pieces, split again and SUCCEED — impossible at `56ad00e`
  (design 7's twenty-round case proved slot reuse; this proves
  RANGE reuse). A second leg maps a piece into a child, releases
  the handle, unmaps — and re-splits the same bytes, witnessing the
  second-zero rule end to end.
- **`mapping_slot_free`** retargeted as authorized above.
- The rest of the suite is the regression fence: BYTE-IDENTICAL
  everywhere else, both architectures.

## Gate

`make sos-test`, suite lock protocol, transcript diffed against a
pre-change baseline from the same worktree. Expected motion: the new
case's rows plus `mapping_slot_free`'s retargeted rows, NOTHING
else (timing rows excepted). Both architectures.

## Out of scope

Slab donation and extent chains (unit 6); any tier/HAL work; byte
QUOTAS (quotas stay object-counted, ruled); a byte-accounting op;
spec.md prose beyond doc comments (unit 8 sweeps §2.5/§5.9 —
including CLOSING the §5.9 pin and design 11 F2's nod — against
this unit's As-built).

## Recording duties

As-built here: the bookkeeping shape as landed (where the counts
live, node arithmetic, coalescing rules), the dropped-bytes stance,
gate evidence (case counts, both arches, the named-rows-only diff),
findings. SL entries to [SAWLANG] if the language bites. Close the
tracker entry in place; the lead moves it at integration.

---

# As built (Sep 3 2026)

**Status: BUILT.** The ruled mechanics landed as ruled — return to
ROOT, release on the second zero, first-fit with coalescing, byte
accounting as the pool's internal ledger. No new ops, kinds, rights or
statuses; no HAL edit; `IoMemory` untouched. §5.9 / design 11 F2 have
their answer BUILT and are unit 8's to close in prose.

## Where the counts live, and why there

Three facts had to go somewhere, and the choice of home is the only
real design decision in the unit:

| fact | home | why there |
| --- | --- | --- |
| the second count (installed rows) | `MemorySlot.maps` | it is a property OF the region |
| which region a row is | `GrantRow.region` | it is a property OF the row |
| the pool ledger + free list | the pool ROOT's own `MemorySlot` | a pool is not a fourth object kind |

**The count is over ROWS, not over `Mapping` OBJECTS, and that is the
load-bearing part.** The brief left the bookkeeping open ("a region
back-reference, or the count kept on the MemorySlot… say why at the
site"), and the two candidates are not equivalent. Design 7 D-2's husk
arm frees a `Mapping`'s slot at its last handle and LEAVES the row
installed, so a back-reference kept on the Mapping slot would be
destroyed exactly when the region still needed to know that something
was granting its bytes — and the range would then be unreturnable
forever, which is the leak this unit exists to bound. A row cannot
vanish unwitnessed: it is born in `record_grant` and dies in
`remove_grant` (an `Unmap`) or `clear_domain` (the target's teardown),
which is three sites, all in `kernel/core/process.saw`. So the row
carries the link and those three sites are where `maps` moves.

**Consequence, stated because it is a departure from the brief's
sketch: design 7 D-2's husk arm did NOT have to change.** A Mapping
dropped without an unmap still frees its slot and leaves its row — the
Mapping slab's lifetime is untouched by this unit. That shape kept its
coverage and changed cases: `mapping_slot_free`'s old husk leg was the
retarget's cost, and `memory_recycle` leg 2 now drops a Mapping object
with its row installed (`let _ = move page_map`), which is the same
arm under more pressure, since the row it leaves behind is in ANOTHER
process's domain. What changed is one level up: the REGION whose bytes
that row reaches now survives its own handle drop, and the range
returns when the row goes.
That is what the brief's "the slot SURVIVES while any mapping counts
against it — it is what carries the root back-ref and the counts"
describes, and it is the Memory slot, not the Mapping slot.

**The ORDER at both row-death sites is a contract, not a
convenience.** `remove_grant` reads the row's region, compacts the
record, and only then drops the count; `clear_domain` zeroes
`grant_count` first and walks the rows afterwards. Either way the
domain has stopped granting the bytes before they can reach a free
list, so no `Split` can ever hand a range to a new owner while an old
one's protection row still reaches it.

## The node arithmetic

`FreeRange { state, base, len, next }` in a slab of `MAX_FREE_RANGES`
(32, against a Memory slab of 16 — every live region could be released
out of order and the list would still be half empty). `next` is
`index + 1` with 0 for "end", which is the spelling every other list in
this kernel uses and makes a zeroed slab an empty list by
construction. One list per pool, headed by `MemorySlot.free_head`,
**kept sorted by base** — which is what makes coalescing two
comparisons against the immediate neighbours rather than a scan.

Per pool root, three ledger fields: `free_head`, `out_bytes` (lent and
not yet returned) and `dropped` (lost to node exhaustion). Free bytes
are not stored — `pool_free_bytes()` sums the cursor and the list —
because a second stored number is a second place to disagree.

- **`pool_can_serve(slot, len)`** — the `Split` refusal. One SERVING,
  not a total: the cursor, or (root only) one free node big enough.
- **`pool_cut(slot, len)`** — first fit off the list, cursor as the
  fallback, both taking the front of whatever range served them.
  `out_bytes` moves only when bytes leave the ROOT; a cut from a
  DERIVED region moves bytes that are already on loan, and the books
  still balance because each piece returns its own length and the
  parent returns whatever its cursor has left.
- **`pool_return(root, base, len)`** — decrement the loan, insert with
  coalescing, then fold the frontier.

**Only a pool ROOT draws on the free list**, and that is an authority
statement rather than an optimisation. A derived region names exactly
its own range; serving its `Split` out of the pool's list would hand
its holder bytes it never had. A root is different in kind — it names
the POOL, its cuts are loans, and a returned loan is the pool's again,
which is literally what the fold-back does to its `base`/`len`.

## Coalescing rules

1. **Insert is the coalesce.** A returned range that touches the free
   neighbour below grows that node; one that touches the neighbour
   above grows that one; one that touches BOTH merges three into one
   and gives a node back. Only a range with free space on neither side
   needs a node, so the list is as long as the pool is FRAGMENTED
   rather than as long as it is busy.
2. **The frontier fold.** A free range whose top meets the root's
   unspent cursor is absorbed INTO the cursor (`base` moves down,
   `len` grows) and its node is freed. Only the LAST node can touch it
   — bytes are cut from the front, so every range ever handed out lies
   below the frontier — and only one pass is needed, because rule 1
   leaves no two free ranges adjacent.
3. **The common shape therefore needs no node at all.** A
   cut-and-drop loop returns the piece it just took, which folds
   straight back; `tests/memory-split`'s twenty rounds allocate and
   free the SAME range twenty times and never touch the node slab.
   Fully-freed pools return to their boot state exactly
   (`memory_recycle` leg 1 proves it by splitting the whole pool out
   again in one call after four out-of-order releases).

## The dropped-bytes stance

Release is INFALLIBLE. Coalescing is attempted first; a range that
still finds no node is added to its pool's `dropped` and leaked —
never refused, never silently lost, with the reasoning at the
declaration. Zero in this tree, and reaching it takes a pool
fragmented into more than 32 holes. Design 25 D-6's `SlabDonate` names
this slab as its first customer.

**Pool-root lifetime.** A root's release needs a THIRD zero:
`out_bytes == 0`. A root whose handles and rows are gone cannot be
given away while pieces of it are on loan, because every one of those
pieces carries a back-reference to its slot and a slot handed to the
next `Split` would make those references name a stranger. The last
piece home releases the root through a one-level tail call (a root's
root is itself). Unreachable in this tree — root servers hold the
boot-minted regions for the machine's life — so it is stated at the
site rather than tested, and if a pool ever does go, its remaining
nodes and their bytes die with it.

## What else the unit had to close (finding)

**A child's image and stack rows named no region, and that was a hole
the moment ranges started coming back.** `place_image` recorded its
grants with no region link, so a launcher that released its handle on
a child's memory while the child was RUNNING would have put those
bytes on the pool's free list — and the next `Split` would have handed
a live process's image to somebody else. Under the old kernel the same
release merely leaked, which is why nothing was wrong before. Closed
by threading the destination region through `place_image` into the
segment and stack rows (`process_create` passes the destination
Memory's slot; the boot path and every device row pass `NO_MEMORY`),
so a child's own memory is now held by its rows exactly as a mapped
page is — and comes back at its teardown, which is the largest thing
this unit recycles. Invisible to every existing case: no test releases
a destination handle while its child lives.

**The `Split` refusal's argument narrowed and the answer deliberately
did not move.** With a free list a pool can hold the bytes and still
not serve them in one range — fragmentation, which the caller cannot
compute and which is therefore not strictly its mistake. `BadArg`
(a fault) stays in v1: `NoResource` exists for "the machine cannot
right now", and moving this refusal onto it is a behaviour change that
belongs to the unit that wants it rather than to the one that made
fragmentation possible. Recorded at the site. Keeping it is also what
keeps `memory_split`'s last three rows byte-identical.

## Files

- `kernel/core/limits.saw` — `MAX_FREE_RANGES` / `NO_FREE_RANGE`; the
  stale `MAX_MEMORIES` paragraph corrected.
- `kernel/core/objects.saw` — `MemorySlot`'s five new fields, the
  `FreeRange` slab, and the pool: `pool_can_serve`, `pool_cut`,
  `pool_return`, `pool_insert_range`, `pool_fold_frontier`,
  `pool_free_bytes`, `pool_drop_nodes`, `ref_region_row`,
  `unref_region_row`, `memory_release_if_idle`.
- `kernel/core/process.saw` — `GrantRow.region`; `record_grant` takes
  and counts it; `remove_grant` and `clear_domain` drop it; boot
  regions mint as pool roots naming themselves.
- `kernel/core/loader.saw` — `place_image` carries the destination
  region into the image and stack rows.
- `kernel/core/dispatch.saw` — `Split` allocates through the pool;
  `install_row` carries the region; `Memory.Map` passes it,
  `IoMemory.Map` passes `NO_MEMORY`.
- `kernel/core/refs.saw` — the Memory arm of `free_object` is one
  call to `memory_release_if_idle` and no longer always frees.
- `kernel/sysapi/src/memory.saw` — `split`'s doc comment: the range
  comes back now, and the last reference is not always the last
  handle.
- `tests/memory-recycle/` (new), `tests/mapping-slot-free/`
  (retargeted), `tools/sos_runner.py` (the new case, appended; the
  retargeted expectations).

## Gate

Baseline `make sos-test` in this worktree at `bc451cb`, before any
edit: **232 passed across riscv32 + arm64**, 116 cases, a 483-line
transcript hashing to `3f6dde15f0f9e3f3cea88bcd3976902414028054c323c96afea74b0460add307`
— the same hash design 24's As-built recorded, so the baseline is the
tree's standard one.

After: **234 passed across riscv32 + arm64**, 117 cases, 487 lines,
`d37d2dbdd686fd573c6c57550f71c107eddcd3e4d8bffc75835f3665f764c8b2`.

**THE DIFF, LINE BY LINE, AND NOTHING ELSE MOVED.** Normalising the
`/116` -> `/117` denominator (mechanical, from adding one case — every
existing case KEEPS ITS ORDINAL, which is why the case was appended
rather than filed beside the M3 memory cases), `diff` over the two
483/487-line transcripts is EIGHT lines and no others:

```
61c61   mapping-slot-free.sosimg riscv32  15592 -> 16760 bytes
127a128 + memory-recycle.sosimg    riscv32  24616 bytes
243a245 + [117/N] ✓ memory_recycle          riscv32
297c299 mapping-slot-free.sosimg arm64    16456 -> 20552 bytes
363a366 + memory-recycle.sosimg    arm64    28744 bytes
479a483 + [117/N] ✓ memory_recycle          arm64
482c486 ALL SOS TESTS PASSED (232 -> 234 passed)
```

Both changed lines are `mapping-slot-free`'s IMAGE SIZE — its program
grew the second-zero leg — and its `✓ mapping_slot_free` row is
unchanged, because a passing report prints a case's name and not its
console output. The retarget is in what the runner ASSERTS: the case
now demands `SOS mapfree: recycled read 94` beside the two lines it
always demanded. Every other case's row is byte-identical on both
architectures, and the three documented timing-dependent rows
(`thread_preempt`'s interleave/ticks/`interrupts=`, `timer_interval`'s
`fires=`, `process_stats`' `interrupts=`) are not printed by a passing
report at all. Authorized set: `memory_recycle` and
`mapping_slot_free`, by name. Observed set: exactly those two.

**One failure on the way, and it was the case's own, not the
kernel's.** The first full run was 233/1: `memory_recycle` passed on
riscv32 — `reused read 60`, the whole mechanism — and faulted on arm64
a few hundred bytes BELOW root's stack row (`tval=0x4023be50` against
a 16 KiB stack based at `0x4023c000`). Root had overflowed its stack:
written as one `_start`, the case had ~20 `print` sites and every
formatted `print` assembles its message in 508 bytes of STACK SCRATCH
(design 137) which does not overlap between sites in one frame. Split
into three functions whose frames pop, with every non-assertion
diagnostic a plain `debug_print` of a literal, it passes on both. The
reasoning is recorded IN the test file, because the shape looks like
taste and is not — and the same trap is waiting for the next case that
grows past a dozen formatted diagnostics in one frame.

## What unit 8 has to sweep (spec.md, deliberately untouched here)

Five places now describe a system that no longer exists. Located, not
edited — the brief puts spec prose in unit 8:

- **§2 table, `MemoryObject` row** (line 35): "a freed region returns
  its SLOT and no range to any pool (§2.5)".
- **§2 table, `Mapping` row** (line 37): §2.5's "dropped without unmap
  = permanent, safe-but-leaked" — still true of the ROW, no longer
  true of the BYTES.
- **§2.5** (lines ~894, ~903): "pool returns arrive with a real
  allocator (M4+)" and the leak sentence beside it. The quoted
  refcount invariant one paragraph up is now EXECUTED and should say
  so.
- **§5.9** (line ~2331): the byte-accounting pin — "It lands with a
  real allocator". It landed; the ledger is `MemorySlot.out_bytes`
  plus `pool_free_bytes()`, internal by ruling, with no op fronting it
  in v1. This is the pin design 25 D-5 says CLOSES with this unit.
- **design 11 F2**'s nod goes with it.

## SL entries

None. The language did not bite: every construct this unit needed —
a self-recursive `unsafe` function, sorted singly-linked list
manipulation over an `unsafe static var` slab, `break` out of a search
loop, a value `if` over an `Int` sentinel — compiled first try on both
targets under `--freestanding --no-hidden-alloc`.
