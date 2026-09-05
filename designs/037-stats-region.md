# SawOS design 37 — M5 unit 7: the shared stats region (the userspace top)

**Status: BUILT, Sep 4 2026 (As-built at the end) — dispatched under
`designs/025` (RULED: seed 1 IN as a late unit) and design 16's recorded
future shape.** The milestone's own demo, the way the uart service was
M4's. Gate 374/374 across three profiles (was 369); the normalised
transcript diff is nine hunks and every one is an addition — not one
pre-existing case row and not one image size moved. No SL entry owed
(highest remains SL-24).

## The one-sentence goal

The per-process trap counters live in ONE kernel-owned page a
process maps READ-ONLY — a purely-userspace `top`, reads free — with
the record layout published in `sosabi`, the op-shaped `Stats` v1
kept as the compatibility floor, and the counters updated by the
SAME increments that exist today (one storage, no mirror to skew).

## The shape

- **The storage moves, the counting sites do not.** The three
  columns (`syscalls`/`interrupts`/`faults`) relocate from
  `ProcessSlot` into a kernel-owned, page-aligned static region
  (`STATS_REGION`), laid out as a published record: a header (slot
  count, layout version word) + one row per process slot. Trap-entry
  attribution writes there; `ProcessOp.Stats` READS there — v1's
  surface answers identical numbers from the new home, so no
  transcript row moves for the relocation.
- **The capability**: a `SystemOp` getter (`StatsRegionMap`-shaped,
  its own `SystemRight`, root set) that INSTALLS an RO row of the
  region into the CALLER and answers `(va, Mapping)` in `map`'s
  existing shape. RO is enforced where the tier can (Isolated); on
  Flat the word already disclaims denial — nothing new to say.
  Attenuation: the right travels by `give`/mint like any bit; a
  process without it simply cannot map the region.
- **Donated process slots** (unit 6b) must be covered: decide the
  region's row capacity honestly — the compiled floor's rows in the
  static page, with rows-beyond-floor either (a) a second donated
  page recorded as a follow-on, or (b) the region sized for
  `PROT_DOMAIN_SLOTS`-scale counts up front. Scout, pick, record.
- **32-bit tearing, stated not hidden**: counters are `UInt64`; a
  32-bit reader can tear a word. v1's answer is DOCUMENTATION plus
  monotonicity (a torn read is between two true values of the low
  word; the columns are printed-not-asserted for exactly the
  volatile one). A seqlock is recorded as the refinement if a
  consumer ever needs exactness — do not build it.

## The proof

- **`stats-region`** (new): root maps the region, spins a known
  number of syscalls, and asserts its own row's `syscalls` moved by
  the region-read matching a `Stats`-op read (the two surfaces
  agree — the compatibility floor witnessed). A child with the
  right maps and READS root's row (cross-process visibility — the
  `top` shape). The RO half: the child attempts a write and faults
  — an ISOLATED-tier leg, excluded by name on flat per unit 4's
  machinery.
- Existing `process_stats` rows must not move (same numbers, new
  home).

## Authorized transcript motion

New-case rows only, all three profiles' sections (with the RO-write
leg Isolated-only). Everything else byte-identical against the
post-unit-4 baseline. No new floor `@export` (unit 4's finding
stands: it breaks the fence; the typed surface suffices).

## Out of scope

Per-thread stats (still waiting for a consumer); the seqlock; the
buffered `debug_print` backlog item (restate its interrupt-column
note here if touched); spec.md prose (unit 8 — design 16's
"recorded future shape" paragraph is located for it).

## Recording duties

As-built here: the layout as published, the capacity decision, the
tearing statement's wording, gate evidence, findings. SL entries if
the language bites (verify the highest — SL-25 exists). Close the
tracker entry in place.

# As-built (agent, Sep 4 2026)

Gate **374/374** across riscv32 + arm64 + riscv32-flat, from a baseline
of 369/369 reproduced first at `a64d359`. Two cases added; one runs on
all three profiles, one is isolated-tier only.

| | before | after |
|---|---|---|
| riscv32 | 124 | 126 |
| arm64 | 124 | 126 |
| riscv32-flat | 121 runnable, 4 excluded | 122 runnable, 5 excluded |
| total | 369 | **374** |

**THE NORMALISED DIFF IS NINE HUNKS AND EVERY ONE IS AN ADDITION** — six
new image lines, five new case rows, the flat exclusion count 4 -> 5 with
its new reason line, and the totals line. **Not one pre-existing case row
and not one image size moved on any profile**, which is the whole
acceptance claim: the storage relocated and nothing observable did. (The
`[i/N]` prefixes change on every row because N changes; that is what the
normalisation removes, and it is the ordinary consequence of appending
cases to all three profiles, which the brief authorises.)

## 1. The layout, as published

`sosabi.records` gained two structs and one record, and the FIRST of them
is the load-bearing one:

```saw
public struct StatsRow {          // the three columns, ONE declaration
    public syscalls: UInt64,
    public interrupts: UInt64,
    public faults: UInt64,
}
public struct StatsRegionHeader { // self-describing, at the region's base
    public version: UInt64,
    public rows: UInt64,
    public row_bytes: UInt64,
    public rows_offset: UInt64,
}
```

**`ProcessStatsRecord` IS GONE, AND THAT IS THE COMPATIBILITY FLOOR MADE
STRUCTURAL.** `dispatch.saw` used to declare a kernel-private record with
these three fields and pin it to the published doubleword slots with a
`static_assert`. The region is an ARRAY of the published row, so the op
and the region are now the same declaration and there is nothing left to
pin — the two surfaces cannot drift, rather than being kept in step. The
`PROCESS_STATS_*_SLOT` constants survive unchanged and describe both.

The header carries `rows_offset` and `row_bytes` rather than letting a
reader derive them, so a v2 that grows the header moves a NUMBER the v1
reader already reads instead of moving the rows out from under its
arithmetic.

**The version is written at boot, not declared.** A non-zero static
initializer would move the whole region — every row of it — out of
`.bss` and into the image (design 149; design 36's `.bss` -> `.data`
finding is the same trap from the other side). Four stores in
`load_root_and_enter` buy that back, and the region is zerofill.

## 2. The capacity decision — and option (a) was not available

The brief offered two: floor-sized rows with a donated second page as a
follow-on, or `PROT_DOMAIN_SLOTS`-scale up front. **Scouting killed the
first outright.** Once the columns RELOCATE, a process slot with no row
has nowhere to count — so a floor-sized region would silently stop
counting exactly the donated slots M5 unit 6b exists to create, and
"follow-on" would mean shipping a kernel that miscounts.

**The second turned out to be EXACT rather than merely generous, which is
the finding worth carrying.** `alloc_process` scans to
`process_slots_usable()` = `min(PROCESSES.capacity(), hal.PROT_DOMAIN_SLOTS)`
— design 36's wall — so `PROT_DOMAIN_SLOTS` is a **ceiling on the slot
index the machine can ever hand out**. Donation raises the capacity and
cannot raise that. A region with `PROT_DOMAIN_SLOTS` rows therefore has a
row for every process that can ever exist on the build, and **the
follow-on the brief was willing to accept is not owed at all**.

`static STATS_ROWS: Int = hal.PROT_DOMAIN_SLOTS` folds as an array length
cross-module (design 185's qualified spelling + DF-232g). It is the first
`kcore` static derived from a HAL one; `limits.saw`'s
`static_assert(MAX_PROCESSES <= hal.PROT_DOMAIN_SLOTS)` was the precedent
that made it worth trying, and it worked first time on all three
profiles.

## 3. Measured cost

| | riscv32 / flat | arm64 |
|---|---|---|
| rows (`PROT_DOMAIN_SLOTS`) | 256 | 5 |
| region, `.bss` (`STATS_STORAGE`) | **12288 B** | **8192 B** |
| `PROCESSES`, `.data` | 2504 -> **2420 B** | 4408 -> **4336 B** |

Both region figures are `b` (zerofill) in `llvm-nm`, so the image carries
none of it — which the gate confirms independently, since no image-size
row moved.

**The `.data` shrink is not the same number on the two profiles, and the
difference is padding.** arm64 loses exactly 72 bytes = 3 slots x 3
`UInt64` columns. riscv32 loses **84**: the three doublewords were also
forcing 8-byte alignment on a struct the rest of which is 4-aligned, so
removing them recovered 4 bytes of tail padding per slot as well.

**The two region sizes are dominated by different things**, worth knowing
before anyone tunes it: riscv32's 12288 is mostly ROWS (256 of them at 24
bytes), while arm64's 8192 is almost entirely GRANULARITY — 152 live
bytes rounded up to one 4 KiB page plus one page of alignment slack.

**AND THE MCU-CLASS BOARD PAYS THE RISCV32 NUMBER, which is the one
figure here that could decide a port.** `kcore` is shared, so the
non-gating ESP32-C3 kernel compiles this region too (checked: it builds
clean, `--target-features +m,+c`). **CORRECTION (design 38's bisect,
Sep 5): that check stopped at COMPILE, and the board fails at LINK —
`.bss` overflows SRAM by 10,416 bytes, `make sos-smoke-esp32c3` 0/3
from this unit's first commit (`9026bf7`) onward. "It bites" arrived
the same week it was named. Resolved at the design-38 integration: the
arena unit's own probe (`ARENA_SIZE = 16K` in `esp32c3.ld`) frees
48 KiB and returns the smoke to 3/3; the `PROT_DOMAIN_SLOTS` lever
named below stays open for design 20's real port.** It takes `PROT_DOMAIN_SLOTS` and
`PROT_GRAIN` straight from `rv32core`, so it gets **256 rows and the same
12288 bytes of `.bss`** — on a part with a few hundred KiB of DRAM, which
is a different proposition from the same number on a board with 128 MiB.
Nothing is broken and nothing is changed here, because the C3 is
non-gating and this unit had no authorization to retune a board knob. But
the fix if it ever bites is small and worth naming now: `PROT_DOMAIN_SLOTS`
is a BOARD number, and a board that will never run 256 concurrent
processes should publish a smaller one — which shrinks the domain pool and
this region together, since they are now sized by the same constant.
Filed for design 20's port, not for this unit.

## 4. Alignment — the static has to carry its own slack

A protection row is granular, and on the coarser profile a row whose base
is not page-aligned would expose the whole page it starts in, including
whatever kernel data neighbours it. So the region is aligned at runtime
to `hal.PROT_GRAIN` and its span rounded up to it.

Saw has no alignment attribute for a `static`, so the backing array
carries `REGION_ALIGN` (4096, the coarsest grain any profile in the tree
publishes, asserted) bytes of slack and `stats_first()` measures the
distance to the boundary. The rounded-up tail is the region's OWN
storage, so rounding the span up leaks nothing.

## 5. The op, and the one deviation from the brief

`SystemOp.StatsRegionMap` (7) on `SystemRight.StatsRegionMap` (bit 15),
in the root set, ABSENT from the ordinary child mask. It installs a
read-only row over the region in the CALLER's own domain and answers a
`Mapping` in the value register — `map`'s shape exactly, with the address
coming from `MappingOp.Base` exactly as for `MemoryOp.Map`.

**THE DEVIATION: the op also takes a copy-out buffer and returns the
caller's own ROW INDEX in it.** The brief said "answers `(va, Mapping)`
in `map`'s existing shape" and said nothing about a row index. Building
it that way turned out to hand a caller a table it cannot find itself in:
rows are indexed by process SLOT, slot numbering is kernel-internal, and
nothing else in the ABI tells a process which slot it occupies. Without
the index the `stats-region` case could only have asserted "its own row"
by hard-coding 0, which is exactly the hidden assumption the acceptance
tradition exists to prevent. One word, one existing funnel (`copy_out`),
`ProcessOp.PipeCreate`'s precedent for an op with more than one thing to
say — and the `Mapping` still comes back the way `map`'s does, so the
shape the brief named is intact.

The index is written BEFORE the row is installed, and that is stated at
the ABI rather than glossed: the index is a FACT ABOUT THE CALLER
(process `p` occupies row `p`), not a product of the mapping, so it is
true on the refusal paths too.

**ONE RIGHT, NOT TWO.** Every other route to `install_row` spends two —
possession of the bytes and authority over the address space. This op
installs into the caller's own domain and takes no Process handle to name
another, so `SystemRight.StatsRegionMap` is the whole gate.

**WHY IT IS ITS OWN BIT RATHER THAN `ProcessRight.Stats`**, recorded
because it is the security-relevant half: the v1 op answers ONE process's
columns through a handle onto THAT process, so its gate is per-target by
construction. The region is every slot's columns in one page, so its gate
has to be a separately-strippable statement that this caller may see the
whole machine's traffic. It is consequently the one `SystemRight` in the
root set that leaks other processes' state, and it is out of the child
mask for that reason.

**No new floor `@export`**, per the brief and unit 4's finding: an
`@export` is a linker GC root, so it would have grown every riscv32 image
and moved every image-size row. The typed `System.stats_region_map()` is
the whole surface, which is the vDSO discipline working as intended.

## 6. The tearing statement, as it is worded

It is stated in three places — `sosabi.records`' region block (the long
form), `System.stats_region_map`'s docstring (what a caller reads) and
`StatsRegion.row`'s (at the point of use) — and the wording is:

> A column is 64 bits and one profile's registers are 32, so a reader can
> load a doubleword as two words and the kernel can bump the low word
> between them. What a torn read yields is therefore a value BETWEEN two
> true readings of that counter, never a value the counter never held —
> the columns only ever increase, so the low word carrying or not
> carrying is the whole of the hazard, and the high word moves once every
> 2^32 traps.

v1's answer is that paragraph plus monotonicity. **The seqlock is
recorded as the refinement and deliberately not built** — an even/odd
generation word per row, written around each bump — because no consumer
needs it and a lock in the trap path would cost every process to serve a
reader that does not exist. A caller needing exactness uses
`Process.stats()`, whose copy-out is made with interrupts masked.

## 7. The proof — two cases, because one of them dies

`tests/stats-region` (all three profiles, one child) asserts four things,
and the middle two are the unit:

1. **`self row 0`** — the op tells a caller which row is its own, and
   root asserting it is what makes the child's read of row 0 sound.
2. **`region delta 8`** — root makes exactly EIGHT syscalls between two
   reads of its own row and the column moves by exactly eight. An
   equality, not a range: `syscalls` is the deterministic column. Nothing
   prints inside that window, because printing is syscalls; the brackets
   are plain loads and cost nothing, which is the property under test.
3. **`op agrees`** — `Process.stats()` reads the same storage and answers
   exactly ONE higher, its own trap, charged before the answer is
   assembled. Asserting the `+ 1` is what proves the counting site is
   where it says it is.
4. **`statsreader: launcher busier`** — a CHILD with the right maps the
   same page and reads its LAUNCHER's row: another process's state, with
   no handle onto that process and no syscall per sample. A child minted
   without the bit could not have made the call.

Then `unmapped`: the access goes back, though nothing about it was owned.

`tests/stats-region-ro` is **isolated-tier only**, excluded by name on
flat with its reason, and it is a separate case for `map_basics` /
`map_unmap`'s reason — a program that dies proving a denial cannot go on
to assert anything else. It READS a column first, so the fault is a
statement about the access bits rather than about an address that was
never granted at all.

## 8. Findings

1. **The kernel's own address is not the frame's, and the gate found it
   the hard way.** The first arm64 run faulted on the very first read
   with `cause=0x92000003` — DFSC `0b000011`, an **address size fault**,
   not a translation fault. `stats_region_base()` was handing
   `install_row` the address the KERNEL uses, which on a tier whose
   kernel lives in the linear map (design 29) is not the physical frame a
   grant row must name. `hal.virt_to_phys` is the seam and it is identity
   where the two coincide, so the fix is one spelling on every profile —
   `stats_region_phys()`. **riscv32 passed throughout**, which is exactly
   what made this worth recording: an identity-mapped profile cannot
   witness the bug, so a unit that touches kernel storage and hands its
   address to hardware must be exercised on the translating profile
   before it is believed.
2. **A row index is not a name.** The op answers the CALLER's own row and
   nothing else. Learning which row ANOTHER process occupies has no
   answer in this ABI, which is why the child reads row 0 on the strength
   of root's assertion in the same transcript rather than on its own
   knowledge. A general `top` — one that maps a process to a row without
   a prior arrangement — needs either a per-row identity column (a
   generation, or the §8 status word) or an op that resolves a Process
   handle to its row. **Recorded as the follow-on; not built, because
   nothing in M5 consumes it.**
3. **`rows` is a capacity, not a census.** A row for a slot no process
   occupies reads whatever its last occupant left until a create zeroes
   it, and v1 publishes no liveness column. Stated in `StatsRegion.row`'s
   docstring so no reader infers a process list from a non-zero row.
4. **The `arm64`/`riscv` lint reaches comments.** `kcore` may not name an
   architecture even in prose (design 162 unit 1), and three explanatory
   lines in the new module tripped it. The rewrite is better text — it
   says "whatever this build's granularity is" instead of quoting two
   numbers that would go stale — so the lint earned its keep rather than
   merely being obeyed.
5. **SL-22 is still open and still bites.** A `static` initializer may
   not wrap after the `=`, which cost the region's size arithmetic a
   long line and a note. An assignment RHS wraps no better; two `let`s
   were the way out in `stats_publish`.

No SL entry is owed by this unit — everything the language refused was
already filed (SL-22), and nothing new bit. The highest remains SL-24.

## 9. What the lead should see

- **Files a parallel arena-sizing unit might also touch.** This unit
  touches NO linker script, NO board knob and nothing under `rt/`. Its
  only footprint that could collide is `kernel/core/lib.saw` (a
  module-order paragraph) and the new `.bss` consumer itself — which is
  the real contact: `kcore.stats` adds 12288 bytes of `.bss` on riscv32
  and 8192 on arm64, so a unit sizing arenas against remaining RAM should
  re-measure rather than reuse a pre-unit-7 number.
- **`hal.PROT_DOMAIN_SLOTS` now sizes a second thing.** It was the
  domain-pool count; it is also the stats region's row count. Raising it
  (design 36 raised it 3 -> 5 on arm64) now costs 24 bytes of `.bss` per
  slot on top of the pool's own cost. Cheap, but no longer free.
- **The op number and the right bit are the next free ones**
  (`SystemOp` 7, `SystemRight` bit 15). The `MINT_OP` assert was
  re-pointed at the new highest case, which is the one-line duty the
  comment beside it exists to make unmissable.
- **A stale suite lock was cleared during this unit.** The machine-wide
  lock had been held since 13:15 with no `sos_runner`, `qemu`, `sawc` or
  `blade` process alive and no file written anywhere under the repo for
  fifteen minutes; verified twice, forty-five seconds apart, before
  clearing. Flagged because the holder was not this agent.
