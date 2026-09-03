# SawOS design 32 — M5 unit 6: slab donation (capacity becomes policy)

**Status: BRIEF, Sep 3 2026 — dispatches under `designs/025` (RULED):
D-6 as ruled.** Arch-free (kernel/core + abi + sysapi + tests).
Converts every donatable kind EXCEPT processes; the process-slot
restructure is unit 6b, its own rung, its own brief. The user's
motivation, recorded in the ruling: the compiled limits are
suite-sized and forbid a real system — many processes, pipes,
threads — and the (post-M5) namespace vision needs that ceiling gone.

## The one-sentence goal

Root donates a Memory region to the kernel to back a named kind's
slab — `SystemOp.SlabDonate(kind, memory)` on its own
`SystemRight`, consuming, permanent in v1 — so a kind's capacity is
decided by the deployment that boots the machine, not by the
compile, while the kernel still boots (and the whole suite still
passes byte-identical) with no donation at all.

## The ruled mechanics (025 D-6, not reopenable here)

- The op CONSUMES the capability (a move, like `give`); donation is
  PERMANENT in v1 — the mapping-leak stance: safe, never reclaimed;
  pool returns for donated slabs are recorded-not-built.
- Refusal (`BadState`) for a region the kernel cannot safely absorb.
  **Unit 5 made the condition precisely expressible**: the donated
  handle must be the region's ONLY reference (`refs == 1`) and no
  row may be installed (`maps == 0`) — the kernel never shares slab
  backing with user-reachable memory.
- A kind's slab becomes a CHAIN OF EXTENTS; the static array is
  EXTENT 0, so boot minimums stay compiled and the kernel boots
  before any donation exists.
- Quotas stay per-process authority; donation bounds the MACHINE.
  `NoResource` keeps its meaning ("the slab is full") — what changes
  is who decides how big the slab is.

## Design points

- **ABI motion, named**: one op (`SystemOp.SlabDonate`), one right
  (`SystemRight.SlabDonate`, minted in the root set of necessity —
  §3 attenuation is monotonic), one `kind` argument. The kind
  vocabulary is this unit's call — `ObjType` is the tempting reuse
  but not every ObjType is donatable (Mapping? the free-range
  nodes have no ObjType at all); a small dedicated enum in `sosabi`
  that names exactly the donatable slabs is probably more honest.
  Numbers are not ABI (the vDSO discipline); the typed surface is
  `System.slab_donate(kind:, memory:)` with the NoCopy wrapper
  moved in (the transfer-funnel contract; `consumes` if the
  receiver position allows, else the dissolve discipline).
- **Which slabs convert**: enumerate in the As-built. Candidates:
  THREADS, EVENTS, WAITERS, INTERRUPTS, TIMERS, MEMORIES,
  IOMEMORIES, MAPPINGS, PIPES (+ their per-pipe parallel arrays —
  a PipeSlot's satellite arrays like `PIPE_BODIES`/`PIPE_CALLER`
  must grow WITH the slot chain or the kind cannot convert;
  say which shape each kind takes), ATTACHMENTS (derives from
  MAX_ATTACHMENTS — decide whether it converts or derives from
  converted counts), and the FREE-RANGE NODE slab —
  **unit 5's named first customer**: when `alloc_free_range` finds
  no node it currently leaks honestly into `dropped`; after this
  unit a deployment can donate the nodes back into existence.
  HANDLES/BOOT_HANDLES/quota tables are `MAX_PROCESSES ×` arrays —
  unit 6b's territory, explicitly OUT here.
- **Extent addressing**: a handle word's index bits span the chain;
  lookup is index → (extent, offset). Keep the arithmetic simple
  and uniform (the agent's design; fixed slots-per-extent derived
  from the donated length is fine). `HANDLE_INDEX_BITS` bounds
  total capacity — static_assert the relationship and record the
  ceiling honestly.
- **ONE ACCESS FUNNEL for extent memory**: every dereference of a
  donated extent goes through a single helper (e.g.
  `slab_extent_base(kind, ext) -> UInt`). This is a COORDINATION
  SEAM with unit 1.5 (the linmap): if 1.5 has landed, the helper
  routes through `hal.phys_to_virt`; if not, it returns the
  physical address raw and 1.5's sweep converts exactly one site.
  Whichever unit rebases second resolves this consciously.
- **Slot reuse, generations, refcounts, teardown walks**: unchanged
  in MEANING, re-plumbed to walk extents. The teardown and census
  walks that iterate `MAX_<KIND>` today iterate the chain; keep the
  bounded-walk property (extents are finitely many, each finitely
  sized) so the design-1 no-preemption audit arguments survive —
  restate them where the loop bounds changed.
- **Zeroing**: a donated range becomes slab slots; slots must start
  Free/zeroed. Zero at donation (bounded, one-time, inside the
  syscall — same cost class as `load_domain`'s old replay), never
  lazily.

## The proof (new cases; the suite is the fence)

- **`slab_donate`** (new): donate a split region to a small kind
  (PIPES is ideal: floor is 4), create PAST the compiled floor,
  exercise the new objects (a pipe made in extent 1 must carry
  messages like any other), and show `NoResource` moved to the new
  ceiling. Negative legs: donate with a live mapping →
  `BadState`; donate a region with a sibling handle → `BadState`;
  the consumed handle is dead after (`BadHandle` via generations).
- **`slab_donate_free_nodes`** (new, or a leg of the first): the
  unit-5 composition — exhaust free-range nodes (the `dropped`
  counter path), donate nodes, show a release that would have
  leaked now returns its range.
- Transcript motion: THE NEW CASES ONLY. Every existing case
  byte-identical, both arches — donation changes no path a
  non-donating program takes.

## Gate

`make sos-test` under the suite lock, transcript diffed against
main's baseline (234/234 at dispatch): new-case rows only, both
architectures, timing rows excepted.

## Out of scope

Processes and everything `MAX_PROCESSES ×` (unit 6b); donated-slab
reclamation (recorded-not-built); any HAL/tier work; byte quotas;
spec.md prose beyond doc comments (unit 8 sweeps — the §2 quota
column and §12's capacity sentences are located-not-edited in the
As-built for it).

## Recording duties

As-built here: the kind vocabulary as landed, the converted-kinds
table with each kind's shape (plain chain vs satellite arrays), the
extent arithmetic and its ceiling, the 1.5-coordination outcome,
gate evidence (case counts, both arches, new-rows-only diff),
findings. SL entries to [SAWLANG] if the language bites. Close the
tracker entry in place; the lead moves it at integration.
