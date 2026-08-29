# SawOS design 6 — Memory, IoMemory, Mapping (M3 unit 4)

Status: AUTHORED Aug 29 2026 (lead), implementing sawlang#232 unit 4
(§2.5 ratified Jul 29; agenda items 6/7/8 leans) on the no-translation
contract: **a Mapping is an INSTALLED GRANT ROW with its own handle.**
The M2 device-grant placeholder's migration clause (§2.5, and the
comments at loader.saw's `check_device_grant` and imgformat's
`SegFlag.Device`) comes due: "the same window, obtained rather than
declared."

## D-1: Three kinds, because the operations differ

- **Memory** (`ObjType.Memory = 9`, exists): RAM authority. Gains ops.
- **IoMemory** (`ObjType.IoMemory = 10`, new): device authority.
  Distinct KIND, not a flag — the §2.5 intent marker made a type: an
  IoMemory can only ever produce device-attribute rows (nGnRE/NAPOT
  discipline inside map()'s implementation), so a driver cannot get a
  cacheable view of a register block BY CONSTRUCTION; and its
  lifecycle differs everywhere (pinned forever, carved
  NON-exclusively, never freed, no contents). One dispatch arm is the
  entire cost; every flag-check that `check_device_grant` scattered is
  what it retires.
- **Mapping** (`ObjType.Mapping = 11`, new): one installed row. ONLY a
  Mapping unmaps (§2.5). Releasing a Mapping HANDLE frees the entry
  and never the row — the entries-never-touch-objects rule making
  §2.5's "dropped without unmap = permanent, safe-but-leaked" fall out
  of existing doctrine rather than being new law.

## D-2: Derivation — Split is exclusive, Carve is not

- **`MemoryOp.Split = 0`** (receiver-verb), gated NEW
  `MemoryRight.Split = 1 << 8`: **ONE CUT FROM THE FRONT** —
  `split(len)` mints a new Memory over `[base, base+len)` and the
  PARENT BECOMES the remainder `[base+len, top)`. Allocation is
  repeated front-splits; the parent IS the pool cursor, so the pool
  needs no second bookkeeping and every piece stays representable in
  the one-`{base,len}` slot shape. Alignment: `len` a multiple of
  `PROT_GRAIN`; zero or oversize is `BadArg` fault (caller-checkable);
  slab full is `NoResource`. Arbitrary-offset carving (which would
  fragment a parent into two remainders one slot cannot hold) is
  REFUSED BY THE SHAPE, not by a check — recorded.
- **`IoMemoryOp.Carve = 0`**, gated NEW `IoMemoryRight.Carve = 1 << 8`:
  `carve(offset, len)` mints a sub-window and the PARENT IS UNCHANGED
  — §2.5's "a fixed region may be handed out many times." riscv32's
  NAPOT reality is validated at the carve: `len` a power of two,
  `base % len == 0` (the existing hal static_assert's rule, now a
  runtime check on caller-chosen values; violation `BadArg`).
- **Free-on-last-reference is UNIT 5's** (the standing
  refcount/quota conversation): a split is permanent until teardown,
  and teardown returns slots, not ranges. Stated, not hidden.

## D-3: map / unmap — the grant record becomes dynamic

- **`MemoryOp.Map = 1` / `IoMemoryOp.Map = 1`**, gated NEW
  `MemoryRight.Map = 1 << 9` / `IoMemoryRight.Map = 1 << 9`, PLUS
  NEW **`ProcessRight.Map = 1 << 18`** on the Process handle passed
  as the target — installing rows into a domain is a specific
  authority over that process (the doctrine: no possession-implies-
  authority). Args: target Process handle, access bits. Returns a
  Mapping handle TO THE CALLER.
- **Access**: caller-chosen R/W/X for Memory (sanity per the existing
  loader rules: W-without-R refused, some access required); RW fixed
  for IoMemory (X-on-device already refused vocabulary). Access is a
  property of the MAPPING, not the region — MemoryRight deliberately
  has no R/W/X (its own docstring), and per-mapping access is how
  double-mapping one region RO here and RW there works. **Agenda
  item 7's lean lands: double-map is ALLOWED** — two rows, two
  Mappings, no aliasing bookkeeping owed on a no-translation machine.
- **`MappingOp.Unmap = 0`**, gated `MappingRight.Unmap = 1 << 8`:
  removes the row, COMPACTS the record (the census: nothing outside
  `load_domain`'s replay is index-sensitive; Mapping slots store a row
  index and compaction fixes up the bounded Mapping slab), frees the
  Mapping slot. Unmap of an already-unmapped Mapping: `BadState`
  fault.
- **THE LIVE-DOMAIN RULE (the census's hazard, promoted to law)**: any
  edit to `PROCESSES[p].grants` — map or unmap — RELOADS IMMEDIATELY
  when `LAST_PROT_PROCESS == p`, because `run_thread` skips equal
  domains and would otherwise run the target under the stale set
  forever. One helper (`domain_changed(p)`), called by both ops,
  asserted by the map_unmap case (touch after unmap FAULTS — which is
  only true if the reload happened).
- **The physical wall (agenda item 8)**: map checks the target's
  `grant_count` against the hal budget and answers `NoResource` when
  full. The PMP slot count is the physical cap; the quota (unit 5) is
  the policy cap layered above it later; "map() meeting the physical
  wall with quota headroom is a kernel bug to assert against" begins
  meaning something at unit 5, and the sentence is recorded at the
  check now.

## D-4: The PMP budget widens — 4 regions were never enough

A driver-shaped root (2 segments + stack + device window) spends all
four TOR regions before the first map(). The census inventory:
`sink.c` gains `case 8..15` pmpaddr arms; `sos_pmpcfg_write` widens to
four words (`pmpcfg2/3`) with its two callers updated; two more shadow
vars and a four-way `stage_cfg`; `PMP_REGIONS = 8`;
`MAX_ROOT_SEGMENTS = 7`; the stale "see sink.c" cross-reference fixed
in passing. `MAX_GRANT_ROWS` stays 9 (arm64's segments+stack already
set it) — map headroom comes from images using FEWER than
`MAX_ROOT_SEGMENTS` rows, which every real image does (root: 2+1,
children: 2+1; a map has 5 free riscv32 rows and 4 arm64 policy rows
to draw on... arm64's wall is `MAX_GRANT_ROWS` itself; per-arch, the
hal exports its row budget and map checks THAT, not a shared
constant). virt.ld's "eight of sixteen" prose updates to sixteen of
sixteen. arm64: no widening — the 4 MiB grant window stands, map
targets validate inside it exactly as prot_region already enforces
(`grant_outside_window` stays a kernel-bug stop for the loader path;
a MAP to an address outside the window must be a caller-visible
refusal instead — `BadArg` fault at the op, checked kernel-side
against a NEW hal predicate rather than by dying inside the HAL;
riscv32's predicate accepts everything in RAM).

## D-5: Pools are region-table rows — the table learns kinds

**Region table VERSION 2**: row = `{base: u64, len: u64, kind: u8,
reserved × 7}` (24 bytes, size-pinned), kind ∈ {Ram = 0, Device = 1}.
The kernel accepts EXACTLY version 2 (the format's own refuse-unknown
rule; v1 never shipped past this tree, and stitcher + kernel move in
one commit). The `len == 0` empty-table short-circuit still precedes
every header read, so tableless images stay byte-identical — the
compatibility rule holds. Ram rows mint Memory (rights
`Transfer | Mint | Split | Map`); Device rows mint IoMemory
(`Transfer | Mint | Carve | Map`). Tags stay row ordinals; the kernel
still interprets nothing else.

The stitcher grows two case keys beside `children`: a RAM-POOL row
(a per-arch free window above the child regions, runner constants
mirroring the hal facts) and a DEVICE row (the board's UART window,
`DEVICE_GRANT_BASE/LEN` mirrored per arch). Root's config knows which
ordinal is which, as ever.

## D-6: The uart-echo migration — obtained, not declared

Both driver cases move to the new path: `device-window` leaves their
manifests, the sosimg carries TWO segments, and the driver (root)
receives the board window as an IoMemory boot record, maps it into
ITSELF, and drives the UART exactly as before. **Authorized transcript
changes, the only two existing cases that move**: `segments={three}` →
`{two}` plus the boot-regions line (and any root-side print the
package adds); the echo behavior lines are UNCHANGED — same bytes,
same line number, which is the migration proving "the same window,
obtained rather than declared."

**What cannot retire yet**: the `SegFlag.Device` flag, the emitter
that writes it, and the boot loader's `allow_device: true` door — the
emitter is SAWLANG-SIDE (blade/sosimg.saw), so deleting the flag is a
pin-bump event. The path goes DORMANT (no image in this tree uses
it), the migration-complete note lands at `check_device_grant`, and
retirement is a BACKLOG entry naming the sawlang side. Children still
cannot declare windows (`allow_device: false` unchanged) — a child
driver gets its window because root MAPS it in (or gives a carved
IoMemory), which is unit 6's flow.

## D-7: Slabs, sysapi, numbers

- `MAX_MEMORIES` 8 → 16 (splits mint slots); NEW `MAX_IOMEMORIES = 8`,
  `MAX_MAPPINGS = 8`, per-slot `process` fields, teardown arms
  (mapping teardown frees SLOTS; the rows died with `clear_domain`).
- `BootHandleKind.IoMemory = 3`; the drain delivers all four kinds.
- sysapi: `iomemory.saw` and `mapping.saw` (the split's order grows
  two files below `system`); `Memory.split/map`, `IoMemory.carve/map`,
  `Mapping.unmap` — map's funnels take `&Process` (borrow, nothing
  consumed) and return the Mapping wrapper; BootHandle grows the
  IoMemory arm (it lives in system.saw's knot; IoMemory sits below).
- Numbering: ops/rights as above; no new SosStatus (NoResource/BadArg
  cover it); no new FaultReason.

## The proof (harness)

1. **`memory_split`** — front-cut arithmetic, parent-as-remainder,
   exhaustion → `NoResource`, misaligned/oversize → fault.
2. **`map_basics`** — root splits the pool, maps RW into itself,
   WRITES AND READS the range (an address that faulted yesterday is
   memory today — the inverse of every protection proof so far), and
   double-maps it RO as a second Mapping (agenda 7 shown).
3. **`map_unmap`** — unmap, then the touch FAULTS: the live-domain
   reload proven end to end. The fault is the CHILD's... (shape:
   map into a child, child touches, unmap mid-flight? v1: root maps
   into itself, unmaps, root's own touch faults — root dies, machine
   status per fault; or the child shape if cleaner. Implementer
   picks the deterministic one, records it.)
4. **`map_into_child`** — pre-start map into a Created child; the
   child touches memory its image never declared; give-freeze is
   untouched (mapping is not a boot record — say so in the case).
5. **`iomemory_carve`** — carve bounds, NAPOT alignment refusals
   (riscv arm), X-on-device impossible by vocabulary.
6. **`uart_echo_ns16550` / `uart_echo_pl011`** — migrated per D-6.

## Docs owed

spec §2 table rows (Memory grows ops; IoMemory + Mapping arrive), §2.5
(BUILT block: the no-translation reading — a Mapping is a grant row;
free-on-last-ref deferred to unit 5 with the §2.5 refcount clause
QUOTED so the deferral is visible), §11 rows, §12 (pool roots
delivered), the `check_device_grant`/imgformat migration notes come
true (in-tree half), hal ABI.md both arches (PMP budget; the new
window predicate), limits/abi docstrings, tracker close, As-built
(split/carve/map/unmap as landed, the v2 table, the widened PMP
inventory as executed, per-case accounting for the two migrated
transcripts, findings). BACKLOG entries: SegFlag.Device retirement
(sawlang-side, pin-bump event); event-wake mint rewrite (carried).

## Out of scope

Free-on-last-reference + pool returns (unit 5, with quotas +
refcounts); attenuation of access on derived Memory (access is
per-mapping); page-fault-driven anything (no translation); giving
IoMemory to children / the driver child (unit 6's money shot);
sawlang-side Device-flag deletion (pin-bump backlog); AddressSpace as
its own object (map targets a Process; §5b says the domain IS the
process on both profiles).

## As built

(Implementer: everything delegated above, the accounting for the two
migrated cases, findings.)
