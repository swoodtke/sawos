# SawOS design 34 — M5 unit 6a: the satellite slabs (threads and pipes donate)

**Status: BRIEF, Sep 3 2026 — dispatches under `designs/025` D-6 and
design 32's As-built.** Unit 6 delivered the mechanism and nine
plain-chain kinds, and honestly flagged that the user's three
motivating kinds all remained: Threads (one satellite, excluded on
risk), Pipes (ten satellites, excluded on shape), Processes (6b).
This unit converts THE FIRST TWO and closes unit 6's two recorded
test gaps. Arch-free.

## The one-sentence goal

`SlabKind` grows `Threads` and `Pipes`; a donated page can back
thread slots (frames included) and pipe slots (bodies and all ten
satellites included), proven by creating past both compiled floors
and exercising the objects in extent 1 exactly as extent-0 ones.

## Threads: one satellite, uniform stride

`THREAD_FRAMES` is a per-slot frame arena keyed by the same index.
The conversion: the frame storage becomes part of the donated
extent's geometry — a thread extent's slot stride is
`sizeof(ThreadSlot)` PLUS the frame stride, or frames ride a second
extent-parallel chain with identical extent boundaries (the agent
picks; the invariant is that slot i's frame is derivable from the
same (extent, offset) with no second lookup that can disagree).
**The context-switch path is why unit 6 declined this** — the trap
path reads a frame by index with interrupts implied off; whatever
shape lands must keep that read the same O(1) arithmetic it is
today, and the As-built restates the trap-path audit at the changed
lines. This is the unit's risk item: treat the switch path as a
reviewed surface, not collateral.

## Pipes: ten satellites on a derived index

`PIPE_BODIES` (8 KiB), `PIPE_LENS`, `PIPE_CALLER`, `PIPE_NEXT`, the
attach/refs arrays — all keyed by pipe index or a derived one. Two
honest shapes; the agent picks ONE and records why:
(a) FATTEN THE SLOT: fold every satellite's per-pipe data into
`PipeSlot` so the kind becomes a plain chain (largest slot in the
kernel, but one geometry, zero derived-index arithmetic);
(b) PARALLEL CHAINS: satellites grow extents in lockstep with the
slot chain, one shared extent table so boundaries cannot skew.
Either way `pipe_rendezvous`/`take`/`reply` paths must not grow a
second bounds check — the slab lookup already did it.

## Unit 6's two gaps, closed here

1. **The `maps == 0` refusal leg gets its test**: map a region into
   a child, then donate it — the caller must fault; the region must
   remain intact and mapped (nothing was absorbed).
2. **`slab_donate_free_nodes`** (the filed shape): donate Memories
   slots first so enough live regions can exist, then force >32
   non-adjacent free ranges, watch `dropped` stay zero where it
   would have counted, and see a release's range come back. This is
   the unit-5 + unit-6 composition witnessed end to end.

## Constraints carried forward

Extent memory converts ONLY in the two funnels 029's rebase left
(`Slab.extent_addr` reads, `slab_donate`'s zeroing write) — if the
satellite shape adds a third dereference of donated memory, it MUST
go through `extent_addr` or the As-built must name the new funnel
and its `phys_to_virt`; remember the lesson recorded in 029: a miss
is a hard fault on arm64 and INVISIBLE on riscv32. Zero at
donation. Bounded walks restated where loop bounds change. No new
ops — `SlabKind` gains two cases (facade lines updated per SL-20's
discipline: one name each on `sosabi.ops`'s facade line and `sos`'s
lib.saw).

## Authorized transcript motion

The new cases' rows only (`thread-donate`, `pipe-donate`, the two
gap-closers — final names the agent's). Everything else
byte-identical against the 0.5.0 baseline (495 lines, hash
`801a0f98…`), both arches; the +N-bytes-per-image pattern from a new
exported wrapper does NOT apply (no new floor export — SlabKind
cases ride the existing `slab_donate` wrapper).

## Out of scope

Processes/6b; donated-slab reclamation; the MAX_HANDLES naming
ceiling (6b's); any HAL/tier work; spec.md prose (unit 8).

## Recording duties

As-built here: the shape chosen per kind and why, the switch-path
audit restatement, geometry arithmetic, gate evidence
(new-rows-only diff, both arches), findings. SL entries if the
language bites. Close the tracker entry in place.
