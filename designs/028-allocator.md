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
