# SawOS design 6 — Memory, IoMemory, Mapping (M3 unit 4)

Status: AUTHORED Aug 29 2026 (lead); **BUILT Aug 29 2026** — see "As
built" at the end, which records the two shapes that changed and why.
Implementing sawlang#232 unit 4
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

Status: BUILT Aug 29 2026. Every D above landed; two shapes the brief
sketched were changed and both are recorded below with their reasons.

### D-1 — three kinds

`ObjType.IoMemory = 10`, `ObjType.Mapping = 11`, beside `Memory = 9`.
Each has its own handle alias, its own rights enum with the universal
low byte pinned by the same two asserts every kind repeats, its own
validated wrapper in `kcore.objects`, and its own dispatch arm. The
Memory arm's `BadOp` fault became `memory_op`.

`IoMemory` earns its kind exactly as D-1 argued: `Process.map(iomemory:)`
takes **no access argument at all**, so a device region cannot produce
anything but a device-attribute row and the guarantee is which method
EXISTS rather than a flag anybody checks.

`Mapping` records `{owner, target, row}` and nothing else — no base, no
length, no access word, because the grant row already holds all three and
a copy would be a second place for them to disagree.

### D-2 — Split vs Carve

`MemoryOp.Split = 0` on `MemoryRight.Split = 1 << 8`: one cut from the
front, the parent becomes the remainder. `len == 0`, `len > parent.len`
and `len % PROT_GRAIN != 0` are `BadArg` faults; a full slab is
`NoResource`. A parent may be split down to zero and then refuses every
later split, which is the honest end of a cursor.

`IoMemoryOp.Carve = 0` on `IoMemoryRight.Carve = 1 << 8`:
`carve(offset, len)`, parent unchanged, the same range carvable twice.
The NAPOT/page rule is a NEW per-arch predicate `hal.device_window_ok`
checked at the carve AND again at the map.

### D-3 — map / unmap

`MemoryOp.Map = 1` / `IoMemoryOp.Map = 1` on `.Map = 1 << 9` per kind,
PLUS `ProcessRight.Map = 1 << 18` on the target — two rights on two
objects. `MappingOp.Unmap = 0` on `MappingRight.Unmap = 1 << 8`.

`MapAccess` is public API (`Read`/`Write`/`Execute`, `UInt32`-backed so a
combination folds at an annotated slot), validated by `map_access`:
a bit outside the three, write-without-read, and no-access-at-all are
each `BadArg`. `MAP_ACCESS_MASK` is named so the declaration and the
check cannot drift, and `grant_perms` TRANSLATES to `SegFlag` rather than
casting — the two enums have the same numbers on purpose and live in two
packages that must be free to version apart.

**THE LIVE-DOMAIN RULE** is `kcore.process.domain_changed(p)`, one
helper, called by both ops, asserted end to end by `map_unmap`.

**THE PHYSICAL WALL** is checked in `install_row` against
`hal.GRANT_ROW_BUDGET` before anything is written, which is what keeps
`record_grant`'s `fatal_image` overflow arm a BOOT-door vocabulary a
syscall can never reach. D-3's quota sentence is recorded at the check.

Unmap removes the row, compacts (`kcore.process.remove_grant`), fixes up
the Mapping slab's stored indices (`compact_mapping_rows`) and reloads.
`load_domain`'s replay-by-index was indeed the only index-sensitive site
the census named, and the slab scan is the whole fixup.

**DEVIATION 1 — UNMAP DOES NOT FREE THE MAPPING SLOT.** D-3 said it
should; it must not, and the reason is soundness rather than taste. A
slab slot freed while a handle still names it is reachable through that
handle the moment the next `map` reuses it — the caller could unmap a
mapping it does not own — and design 3's generations cannot cover it,
because a generation lives in the handle ENTRY and the entry is not what
went away. So an unmapped Mapping keeps its slot with `row = NO_ROW`,
answers `BadState` forever, and comes back at its OWNER's teardown —
which is not a concession, it is the rule EVERY other kind already
follows (design 3 D-2: an object whose last handle is gone is
unreachable-but-live until its process tears down). The one slab that
reclaims earlier is a `Gone` PROCESS's, and it earns that with a
reader-count scan nothing here has an equivalent of. Refcounted early
reclamation is unit 5's, for this slab and for the others together.

**THE TEARDOWN COMES APART FROM BOTH ENDS**, which D-3 did not name and
the census implied: a Mapping names two processes. The OWNER dying frees
the slot and leaves the row (§2.5's permanent-but-safe stance, bounded
here by the target's own death); the TARGET dying takes the row with its
domain and leaves the slot LIVE with `row = NO_ROW`, because its owner
still holds the handle. Two arms over one slab in `end_process`.

### D-4 — the PMP budget

Executed as inventoried. `sink.c`: `sos_pmpaddr_write` cases 8..15 (all
sixteen the part implements), `sos_pmpcfg_write(w0, w1, w2, w3)` writing
`pmpcfg0..3`. `lib.saw`: `PMP_REGIONS = GRANT_ROW_BUDGET = 8`,
`MAX_ROOT_SEGMENTS = 7`, four shadow words, four-way `stage_cfg` (an
`if/else` chain, because a `static` is not an array element),
`prot_reset` zeroing four cfg words and sixteen addresses, `prot_commit`
passing four. The stale "see `sink.c`'s `PMP_REGIONS`" cross-reference at
`MAX_ROOT_SEGMENTS` is corrected. `virt.ld`'s prose says sixteen of
sixteen and names the run-time rows.

`MAX_GRANT_ROWS` stayed 9 with its docstring rewritten to say what D-4
required: the two profiles' budgets now MEAN different things (hardware
on one, policy on the other), the HAL exports `GRANT_ROW_BUDGET`, and
`map` checks the HAL's number. A `static_assert(hal.GRANT_ROW_BUDGET <= 9)`
holds the array against it.

`map_target_ok` is the new per-arch predicate: riscv32 accepts anything
in RAM (`RAM_BASE`/`RAM_TOP` declared, since nothing on that profile
publishes them), arm64 accepts the 4 MiB grant window.
`grant_outside_window` is untouched and still stops the machine for the
loader path, which is the whole point of the split.

### D-5 — region table v2

Row = `{base: u64, len: u64, kind: u8, reserved × 7}`, 24 bytes,
size-pinned; header unchanged at 8; version exactly 2 with no v1 arm; the
`len == 0` short-circuit still ahead of every header read, which is why
96 of the suite's 134 rows link no `.regions` section and never see the
format at all — the compatibility that mattered for a bump with no v1
arm. (38 rows carry a table: the unit-2 and unit-3 cases with children,
and unit 4's five.) `RegionKind` is
raw-backed and read through `from(raw:)`, refusing an unknown byte with
`fatal_image` like every other malformation the build could write.

Stitcher: `_stitch_children` became `_stitch_regions` (it no longer emits
only children), `_region_rows` states the ROW ORDER contract in one place
— blobs, destinations, pool, device — and `_emit_row` writes the v2 row.
Case keys `"pool"` and `"device"`; per-arch `pool_base`/`pool_len` and
`device_base`/`device_len` mirroring the HAL facts (riscv32
0x8028_0000/256 KiB and 0x1000_0000/0x1000; arm64 0x4028_0000/256 KiB —
inside the 4 MiB window, which constrains it as much as tidiness does —
and 0x0900_0000/0x1000).

### D-6 — the uart-echo migration

Both manifests lost `device-window`; both drivers gained the same six
lines (drain the boot record, take the `IoMemory`, `map(iomemory:)`, one
console line) and are otherwise untouched. Accounting is below.

`check_device_grant`'s migration note and the two user ABI.md files now
say the migration HAPPENED and that the declare path is DORMANT rather
than deleted. `SegFlag.Device`, its emitter and `allow_device: true` are
unchanged; the backlog entry names the sawlang side and the four in-tree
consumers a pin bump would let go.

### D-7 — slabs, sysapi, numbers

`MAX_MEMORIES` 8 → 16, `MAX_IOMEMORIES = 8`, `MAX_MAPPINGS = 8`, all with
`process` fields and teardown arms. `BootHandleKind.IoMemory = 3`;
`boot_kind_of` gained the arm and `BootHandle` a fourth slot with
`take_iomemory`. Numbering exactly as ruled; no new `SosStatus`, no new
`FaultReason`.

`iomemory.saw` and `mapping.saw` are new modules — placed BELOW `system`
in the order, not above, because `BootHandle` carries both region kinds.

**DEVIATION 2 — THE MAP FUNNELS ARE `Process` METHODS.** D-7 said
`Memory.map(into: &Process)`; that does not compile. `sos.memory` and
`sos.iomemory` sit below `sos.system` (BootHandle needs both), so a `map`
written in either and naming `Process` is the import cycle DF-232e
diagnoses — and a type's extension methods must live in its declaring
module, so no arrangement of files fixes it. The funnels are
`Process.map(memory:access:)` and `Process.map(iomemory:)`, which read
exactly like the `give` overloads beside them. The brief's substantive
point holds and is written at the funnels: **nothing is consumed and
there is no disarm, because a map MOVES NO HANDLE WORD** — the region is
borrowed, the target handle stays the caller's, and what comes back is a
new capability. `Memory.split` likewise consumes nothing (the parent
mutates kernel-side).

### The proof — six cases, ten new rows

| case | arches | claim |
|---|---|---|
| `memory_split` | both | front-cut arithmetic (0xaa/0xbb/0xaa proves disjointness through two mappings), exhaustion → `NoResource` after 13 cuts, oversize → `BadArg` fault |
| `map_basics` | both | map into self, write and read; double-map RO as a second Mapping; the read still answers |
| `map_unmap` | both | map, write, read, unmap, touch → hardware fault. The live-domain reload, end to end |
| `map_into_child` | both | map into a `Created` child pre-start; the child touches memory its image never declared; `status=65596` carries the byte back |
| `iomemory_carve` | both | the same window carved TWICE (non-exclusive), mapped, then an ungrantable length → `BadArg` fault |
| `uart_echo_ns16550` / `uart_echo_pl011` | one each | migrated per D-6 |

**`map_unmap`'s SHAPE, the delegated pick: ROOT INTO ITSELF.** It is the
deterministic one — no scheduling race, the fault lands on a known
instruction in a known process — and it exercises the live-domain rule in
its HARDEST case. The child variant would have reloaded nothing (a
child's domain is not the installed one) and so would have proven nothing
about the reload at all.

**HOW A TEST KNOWS AN ADDRESS.** A region has no bounds reader and unit 4
deliberately did not add one, so the four packages that touch pool memory
— `memory-split`, `map-basics`, `map-unmap`, `map-into-child` — and the
child `child-touch` beside them carry the pool's base as a one-line C
constant per architecture (`tests/poolbase_<arch>.c`, two files, five
manifests, mirroring the runner). That is root's config
— the same class of fact as "which tag means what", and the same
arrangement the uart-echo driver has always had with `UART_BASE` — and it
is C because Saw cannot name an address (DF-172a). `iomemory_carve` needs
none: `carve` takes an OFFSET, so a driver names a sub-window without
learning where it is.

Three of the six end in a FAULT and do their positive work first, which
is `irq_early_ack`'s shape and is forced by the same fact: a `BadArg`
fault ends the process, so a second probe would never run.

### The gate

`SAWLANG_ROOT=$HOME/Projects/sawlang make sos-test`, under the mkdir lock,
baseline at the merge base (c2bbd06) and again with the change; full
console transcripts diffed case by case.

- BASELINE: 124 rows, all green.
- AFTER: 134 rows, all green (124 + 10 new: five cases × two arches).
  Nothing removed.

**THE 124 PRE-EXISTING ROWS, BUCKETED BY CAUSE** — mechanically, not by eye
(the classifier normalizes exactly three fields and nothing else):

| bucket | rows | cause |
|---|---|---|
| byte-identical | 37 | — |
| ADDRESS ONLY | 82 | the only differing fields are addresses the LINKER or the kernel chose: the loader's `entry=`, a fault report's `epc=`/`elr=`, a tick report's `at 0x…`. The `sos` module grew by two files and five ops, so every root image's entry moved; the kernel grew, so the `.payload` section and the kernel's own code moved with it. **`tval=` is NOT normalized** — the address a program reached for is a behavioural fact, and it is unchanged in every one of these — and neither is any other field: `segments=`, `prio=`, every teardown count, every status word and every program's own output are byte-identical |
| AUTHORIZED | 2 | `uart_echo_ns16550`, `uart_echo_pl011` — accounted line by line above |
| DOCUMENTED NON-DETERMINISM | 3 | values the harness deliberately does not assert, with the reason written at each case |

The three in the last bucket, each checked against what its case claims:

- `thread_preempt` (both arches) — the spinner INTERLEAVING (arm64:
  `BAABBABAABBAAB` at the baseline, `ABABBABABABAAB` at one run of the change
  and `ABABABBABAABAB` at another — it differs between two runs of the SAME
  build, which is the point) and, on riscv32, where root's banner interleaves
  mid-word with the tick narration. The case asserts the alternation as
  DIRECTION CHANGES rather than as a sequence, and its comment says why: which
  worker runs first depends on where the first tick lands relative to two
  `start` calls. A kernel that grew moved the code the tick lands in. `joined
  a=33 b=44` is unchanged.
- `timer_interval` (riscv32) — `tick one fires=2` → `fires=1`. The coalesced
  fire count, which the case's comment says in capitals is NOT asserted,
  because a fire delivered more than a period late coalesces BY DESIGN and
  pinning the number would make the feature itself the flake. It would have
  flaked here: a bigger kernel changed how much of the first period elapsed
  before the wait. `tick two fires=3 ackfree=1` — the deterministic half — is
  unchanged. The runner's observation note is updated to record the new
  reading rather than leaving a stale one.

**PER-CASE ACCOUNTING, `uart_echo_ns16550` (riscv32):**

| baseline | after | why |
|---|---|---|
| `segments=0x00000003` | `segments=0x00000002` | the third segment WAS the device-window declaration. THE authorized change |
| `entry=0x802006a6` | `entry=0x80200870` | the driver gained the six-line obtain sequence, so its entry moved. A consequence of the source edit, on the same authorized line |
| — | `+ SOS: boot regions=0x00000001` | the case now publishes a DEVICE row; the kernel mints one `IoMemory` and says so, exactly as every region-table case has since unit 2 |
| — | `+ SOS echo: window mapped` | the root-side print D-6 authorizes: the driver saying it obtained what it used to be handed |
| `SOS echo: driver up` … `done 4 bytes on line 10 after 4 wakes` | UNCHANGED, byte for byte | **the migration's whole claim.** Same bytes, same line number, same wake count |

**`uart_echo_pl011` (arm64):** the same four changes, same causes —
`segments=0x…03` → `…02`, `entry=0x…4020076c` → `0x…402009a4`, the two
new lines — and `SOS echo: done 4 bytes on line 33 after 4 wakes`
unchanged.

### Findings

1. **A LONE RAW-BACKED ENUM CASE DOES NOT ADOPT A FIXED-WIDTH SLOT,
   THOUGH A COMBINATION OF THEM DOES.** ``static `RO` has type `UInt32`
   but its initializer has type `MapAccess` ``, while
   `static RW: UInt32 = MapAccess.Read | MapAccess.Write` compiles and
   folds. So ONE bit costs an `as UInt32` and TWO bits do not, which
   teaches that adding a flag REMOVES a cast. Minimal example and the
   resolution are filed as **SL-9** in `designs/todo.md`. Worked around
   at the one site with the asymmetry noted at the line.
2. **DESIGN 5's FINDING 1 DECIDES METHOD PLACEMENT, not just file
   layout** — deviation 2 above. Filed as a second site under **SL-7**.
3. **DF-172d hit a third time** (`map_access`'s two-line condition),
   filed under **SL-8**.
4. **`fatal_image` IS NOT `-> Never`**, so a `guard ... else` that ends
   in it still owes a `return`. Noted at the one new site rather than
   changing the signature, which is a real change nobody has ruled on.
5. **NOTHING ABOUT MEMORY IS RECLAIMED, AND THE CASE SAYS SO.**
   `memory_split` asserts `pool exhausted after 13 cuts` — the SLAB
   running out (16 slots: one boot pool plus fifteen cuts) with 49 of
   the pool's 64 chunks still unallocated, because free-on-last-reference
   is unit 5's and a dropped piece returns a slot to nobody. That number
   pins `MAX_MEMORIES` in a transcript deliberately: raising the slab
   should move a line somebody reads.
6. **THE TEARDOWN LINE DID NOT GROW A FIELD.** Adding `iomemories=` /
   `mappings=` to `SOS: process teardown …` would have moved every
   existing transcript row, and this unit authorized exactly two. The
   two new slabs are reclaimed silently; a unit that authorizes the rows
   can add the columns.
7. **A REAL HOLE, FOUND IN REVIEW AND CLOSED: `map` INTO A DEAD
   PROCESS.** `give` and `Start` both require the target to be
   `Created`, so neither had ever needed to think about a `Gone` slot.
   `map` is the first op that reaches into another process's slot
   WITHOUT that requirement — mapping into a running child is
   legitimate — and the first cut simply did not check. The sequence
   that breaks: a child dies (teardown empties its grant record and
   leaves the slot `Gone`, because its supervisor's handle outlives it
   by design 3 D-3); the supervisor maps into that handle, appending a
   row to the dead slot's record; the supervisor releases the handle,
   which RECLAIMS the slot; the next `process_create` lands there and
   `place_image` appends its rows on top of the stranger's — a
   brand-new process holding a protection row granting somebody else's
   memory. Closed by a state check in `map_target` (`Created` or
   `Running`, anything else `BadState`), documented at the check and at
   the sysapi funnel. Not covered by a case: reaching it needs a
   third process slot to observe the reuse with, and `MAX_PROCESSES` is
   2 — recorded here so the unit that raises it writes the case.
8. **EIGHTY-TWO ROWS MOVED FOR A REASON NO UNIT CAN AVOID**, and it is
   worth naming as a property of the oracle rather than of this unit:
   the loader REPORTS the image entry it read, and the entry moves
   whenever the shared `sos` module changes size — which every unit that
   touches the userspace API does. Diffing raw transcripts therefore
   reports a large, uninformative delta on any such unit, and the useful
   question ("did any ASSERTED field move?") needs the addresses
   normalized. The classifier that does it normalizes exactly three
   fields (`entry=`, `epc=`/`elr=`, `at 0x…`) and deliberately leaves
   `tval=` alone, since the address a faulting program reached FOR is a
   claim rather than a placement. A future unit doing this accounting
   should expect the same shape.
