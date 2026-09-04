# SawOS design 29 — M5 unit 1.5: the higher-half kernel + the linmap seam

**Status: BUILT Sep 3 2026 (see "As built" below) — dispatches under `designs/025` (RULED;
this unit is its ruling 10, user-proposed and user-ruled the same
day).** arm64 only in behavior; every other HAL grows two identity
inline bodies. **After unit 1** (it edits the same tables and boot
path) — the dispatching lead rebases this brief's premises onto
unit 1's As-built, including the seam-collapse rework
(`prot_switch`/`prot_update`/`prot_clear`). **Before unit 2**, which
spends the low half this unit frees.

## The one-sentence goal

The arm64 kernel relinks into the high half under TTBR1, with a
LINEAR MAP of all RAM (Normal, PXN) and the board's usable MMIO
(Device, PXN) at a fixed offset, so TTBR0 becomes purely the user's
register — and the kernel's phys↔virt conversion is one HAL seam
pair that is bit arithmetic on MMU machines and the bare address on
MPU machines, with the console transcript BYTE-IDENTICAL.

## The ruled discipline (the whole design, in three sentences)

**Addresses-as-data stay PHYSICAL everywhere.** `GrantRow`s, sosimg
records, boot-handle records, region tables, Memory/IoMemory
capabilities, fault report words — none change meaning; a physical
address remains the one name for a location that crosses any API.
**Only the moment of kernel DEREFERENCE converts**, through
`hal.phys_to_virt(pa) -> UInt` / `hal.virt_to_phys(va) -> UInt` —
on arm64 an OR / AND-mask with the linmap offset; on riscv32 (both
boards) identity, compiling to nothing. `kernel/core` stays
arch-free: no tier conditional, no second address vocabulary.

## What it buys (recorded so the As-built can check itself)

1. TTBR0 is 100% user: the low half stops being shared territory,
   which is what unit 2's placement spends.
2. The copy funnels' unit-2 shape exists ahead of need: resolve a
   user VA to a PA through the target's grant records, dereference
   through the linmap.
3. Kernel table content exists ONCE (TTBR1's) instead of per-set;
   the per-process sets shrink to the user windows.
4. The linmap is PXN end to end and the kernel executes only its own
   high-half text — a W^X improvement over the identity blocks.

## Design points

- **The offset**: pick the canonical high-half base the configured
  VA size gives (TCR.T1SZ's choice is this unit's); the constant is
  HAL-internal, named once. Pure OR/AND must hold — assert at build
  time that RAM and the MMIO ranges fit under it.
- **Two attribute flavors, one arithmetic**: RAM maps Normal
  cacheable; each MMIO range the board publishes maps Device at the
  same offset. The HAL owns its board map; nothing above the seam
  knows which flavor a PA gets.
- **TTBR1 setup**: TCR.EPD1 opens; TTBR1 tables are built once at
  boot and never switched; kernel text/rodata/data/bss relink to
  high VAs (linker script + boot.S: MMU-off entry at physical,
  enable, jump high — keep the relocation window minimal and
  documented). `VBAR_EL1`, kernel SP, and every kernel-held pointer
  come out of the relink naturally.
- **The per-process sets lose their kernel blocks**: user sets carry
  only the user windows; EL0 translations stay `nG`/ASID-tagged
  exactly as unit 1 left them. The unit-1 finding that "window pages
  must stay EL1-mapped because kernel text sits inside the window"
  DISSOLVES — kernel access now rides TTBR1's linmap, so a
  non-granted user page may become genuinely ABSENT. If making it
  absent flips a fault from permission-class to translation-class
  anywhere, remember the gate does NOT witness fault class (unit 1
  finding 3) — normalize in the decode anyway, because unit 3 will
  light that assertion.
- **The sweep**: every kernel-side `UnsafeMemory`/`UnsafePointer`
  constructed from a stored physical address routes through
  `phys_to_virt` at the construction site. Bounded and greppable;
  the As-built lists the sites swept. The USER-facing funnels
  (copy-in/copy-out) convert the PA they already resolve; user VAs
  are still identity this unit (placement is unit 2's).
- **riscv32**: both HALs gain the two identity inlines and NOTHING
  else. (The Sv32 climb is PUNTED to backlog — 025 ruling 11 — but
  the note stands for whoever picks it up: virt's RAM at
  0x8000_0000 already sits in the upper 2 GiB, so an Sv32 kernel
  never moves and the seam stays identity there.)

## Gate

`make sos-test`, suite lock protocol, transcript diffed against a
pre-change baseline from the same worktree: BYTE-IDENTICAL, both
architectures, timing rows excepted. No user-visible address is a
kernel address — every address in the gated transcript is a
user-space address, which is what makes the ambition realistic. The
riscv32 half of the diff proves the seam's identity bodies cost
nothing.

## Out of scope

VA placement and the link-base collapse (unit 2); Sv32 (punted to
backlog, 025 ruling 11); any allocator/donation work; spec.md prose
beyond doc comments
(unit 8 sweeps; §5.5's user-address identity law is UNTOUCHED by
this unit — kernel-internal addressing was never its subject, say
so in a doc comment if a reader could wonder).

## Recording duties

As-built here: the offset and T1SZ choice, the linker/boot.S shape,
the set-shrink arithmetic (.bss delta, expected NEGATIVE), the sweep
site list, the fault-class note's outcome, gate evidence (hash,
both arches). SL entries to [SAWLANG] if the language bites. Close
the tracker entry in place; the lead moves it at integration.

## As built (Sep 3 2026)

**Status: BUILT.** The arm64 kernel runs in the high half under TTBR1
over a linear map of RAM and MMIO; TTBR0 holds only the running
process's windows. Gate green on both architectures with the console
transcript byte-identical.

### The offset and the VA size

| | |
|---|---|
| `VA_BITS` | **39** — `TCR_EL1.T0SZ` = `T1SZ` = **25**, both halves the same size |
| `LINMAP_OFFSET` | **`0xFFFF_FF80_0000_0000`** = `2^64 - 2^39`, the canonical high-half base for that VA size |
| `LINMAP_PHYS_MASK` | `0x0000_007F_FFFF_FFFF` |
| `phys_to_virt(pa)` | `pa \| LINMAP_OFFSET` |
| `virt_to_phys(va)` | `va & LINMAP_PHYS_MASK` |

T1SZ was chosen to MATCH T0SZ rather than independently, and the
reason is worth recording because it is what makes the whole unit
cheap: with the offset's low 39 bits zero, a kernel address and its
physical twin have the SAME low 39 bits — so they select the same
level-1 index. That is what lets `boot.S` install ONE two-descriptor
table in TTBR0 and TTBR1 at once and get the identity map and a
coarse linear map out of a single page (see the boot shape below),
and it is why the seam is an OR and an AND rather than an add.

Other consequences of the same fact, none of them designed for: the
conversion has no carry to worry about, `virt_to_phys` is IDEMPOTENT
on an already-physical address (which is what lets one call normalize
the region table's mixed-provenance rows), and page alignment survives
conversion in either order.

**The build-time bound the brief asked for** is five `static_assert`s
in `hal/arm64/kernel/lib.saw`, spelled against `LINMAP_PHYS_MASK`
rather than against the offset — every operand is then positive, which
matters because a const fold runs in the SIGNED platform-`Int` domain
and `LINMAP_OFFSET` has its top bit set:

- `RAM_BASE + RAM_LEN - 1 <= LINMAP_PHYS_MASK` (RAM: 128 MiB at
  0x4000_0000, `RAM_LEN` restated from `virt.ld`'s `MEMORY` LENGTH
  because the linear map has to know where to stop);
- `UART_BASE`, `GIC_DIST_BASE`, `GIC_CPU_BASE` each `<= LINMAP_PHYS_MASK`;
- and the pair's own consistency —
  `(LINMAP_OFFSET & LINMAP_PHYS_MASK) == 0` and
  `(LINMAP_OFFSET | LINMAP_PHYS_MASK) == UInt.max` — which is what
  ties the literal offset to `VA_BITS`. Both folded fine.

### The linear map

Three tables, 12 KiB, built once and never edited:

| table | contents |
|---|---|
| level 1 | two entries: the device gigabyte, RAM's gigabyte |
| device level 2 | 512 × 2 MiB **Device-nGnRnE** blocks, EL1 RW, **PXN + UXN** |
| RAM level 2 | 64 × 2 MiB **Normal** blocks over the RAM that exists, EL1 RW, UXN, **PXN except block 0** |

The RAM walk stops at `RAM_LEN` rather than filling the level-1
entry's whole gigabyte, so a kernel dereference past the end of memory
is a translation fault rather than a mapping of nothing. Every block
is GLOBAL (no `ATTR_NG`): these translations belong to the kernel, not
to an address space, so tagging them would cost a TLB entry per
process for a mapping every process shares.

**PXN is not quite end to end, and the exception is recorded rather
than glossed.** The kernel has to execute its own text, and its text
lives in the linear map — so RAM block 0 (0x4000_0000..0x4020_0000) is
EL1 **RWX**. What makes that exactly one block is an assert that
already existed: `virt.ld` requires `_bss_end` below `ROOT_LOAD_BASE`
(0x4020_0000) so the kernel cannot overrun root's image, and that
bound IS the first 2 MiB block's top. The linker ceiling and the
executable carve-out are one number, and both ends say so.

Still, the improvement is real and is what the brief priced: before
this unit every process set carried EL1 blocks over ALL of RAM with
PXN clear, so all 128 MiB was kernel-executable. Now 126 of 128 MiB
are PXN and the device space is PXN throughout.

**NOT TAKEN, as a named limit:** per-section W^X inside that block
(`.text` RX, `.rodata` RO, `.data`/`.bss` RW). It wants a level-3
table and page-aligned section bounds `virt.ld` does not publish
today — three linker symbols and one more table. The machinery is
here; nobody spent it this unit.

### The boot shape — where the relocation window is, and why it is that size

The order is forced, and every line of `boot.S` follows from it: the
kernel links high, so **no compiled code — C or Saw — may be called
until the MMU is on and TTBR1 is walked.** A `bl` reaches a high
address, and a compiled body reaches its globals through PC-relative
pairs that would resolve against whatever address it happens to be
running at. So the MMU has to come up on a table built by hand-written
assembly.

**Running the existing Saw/C at its physical address was considered
and rejected.** `adrp`/`add` is PC-relative and would have worked; what
would not is anything holding an ABSOLUTE address in `.rodata` or
`.data` — a vtable, a jump table, a `FuncPointer` static. Those hold
high addresses and reading one with the MMU off faults. "It happens to
have none today" is not a property to build a boot path on.

The window, in full:

1. **Physical, MMU off.** Mask `_stack_top` to physical and set SP;
   `CPACR_EL1.FPEN`; zero `.bss` (both bounds masked); build the boot
   table — TWO level-1 block descriptors, the device gigabyte and
   RAM's; load MAIR and TCR; `TTBR0 = TTBR1 = boot table`; enable
   `SCTLR_EL1.M|C|I`.
2. `ldr x0, =.Lhigh ; br x0` — **the one instruction the window exists
   for.**
3. **High.** Set the real SP and `VBAR_EL1` (which holds a virtual
   address, so it could not have been set earlier). Call
   `sos_mmu_tables` → the linear map's base, and `sos_page_tables_build`
   → set 0's base. **Both now run with translation already on**, which
   is the reversal of what `page_tables_build` used to do, and is why
   `put_desc` converts.
4. **Branch back to a physical trampoline**, install `TTBR1` = the real
   linear map, `isb ; tlbi vmalle1 ; dsb nsh ; isb`, branch high again.
   This is Linux's `idmap_cpu_replace_ttbr1` shape and it is here for
   the ordinary reason: code that changes the register its own PC
   translates through has nowhere to stand. It also sidesteps the
   TLB-conflict hazard of swapping a 1 GiB entry for a 2 MiB one under
   a live PC.
5. `TTBR0` = set 0, flush, `bl kmain`.

**No value is spelled twice.** `sos_boot_mair` and `sos_boot_tcr` are
`const u64`s in `sink.c` — the file that already owned the TCR
expression — and `boot.S` reads them out of `.rodata` through a masked
pointer, which works with the MMU off because `.rodata` is loaded at
that physical address. MAIR is the one word `sink.c` cannot own
(`lib.saw` builds it from the `MemoryType` cases, and a `const`
initializer cannot call a function), so it is copied and **checked
against `sos_mair_value()` on every boot** in `sos_mmu_tables`, exit
code 68. The descriptor bits in `boot.S` are the one duplication left,
and a wrong one is an immediate visible boot failure rather than a
silent one.

`sos_mmu_init` is gone; `sos_mmu_tables` replaces it and does strictly
less.

**The offset itself is written twice** — `virt.ld` and `lib.saw` — and
nothing at build time can compare them, because a Saw static's VALUE
is not a linker symbol. So they are compared at BOOT:
`linmap_offset_probe` runs first in `linmap_build`, while the boot
tables are still live and the console still answers, and checks that
the address the linker actually gave `PAGE_TABLES` carries the offset
and lands inside RAM. Exit code 67. `boot.S` needs no third copy —
`virt.ld` exports `LINMAP_PHYS_MASK` as an absolute symbol and the
assembly loads its value.

### The linker script

One VMA cursor at `0x40000000 + LINMAP_OFFSET`, and every section
carries `AT(ADDR(.x) - LINMAP_OFFSET)`. **Not a second `MEMORY`
region**, deliberately: with two regions the VMA and LMA cursors
advance independently, so a section whose alignment differs from its
predecessor's silently breaks the fixed delta the whole scheme rests
on. Spelling the load address from the virtual one makes the delta an
identity rather than a coincidence. (`AT()` precedes `ALIGN()` in the
grammar — the other order is a parse error.)

`ENTRY(_start_phys)`, where `_start_phys = _start - LINMAP_OFFSET`.
QEMU sets the reset PC from `e_entry` with the MMU off, so a high entry
would fault before the first instruction. Verified on the built ELF:

```
Entry point 0x40000000
LOAD 0xffffff8040000000  0x0000000040000000  R E
LOAD 0xffffff8040060310  0x0000000040060310  R
LOAD 0xffffff8040067140  0x0000000040067140  RW
```

— every `p_paddr` exactly where it was, which is what keeps QEMU's
load unchanged.

The `_bss_end` assert now compares LOAD addresses
(`_bss_end - LINMAP_OFFSET <= 0x40200000`) and carries a second job it
did not have: that bound is also the executable block's top.

### The set-shrink arithmetic — and **the brief's expectation was WRONG**

**The `.bss` delta is `+0x4000` — POSITIVE, +16 KiB.** The brief
expected it negative ("sets shrink"). Measured with
`llvm-readelf -S` on `trap_fault.elf`, both sides built in this
worktree with the pinned compiler:

| | before | after | delta |
|---|---|---|---|
| `.bss` | `0x39940` (235,840) | `0x3D940` (252,224) | **+16,384** |
| `PAGE_TABLES` pool | `0x13000` (77,824) | `0x16000` (90,112) | +12,288 |
| `_boot_l1` | — | 4,096 | +4,096 |
| `.text` | `0x6010C` | `0x60310` | +516 |
| `.rodata` | `0x6FB0` | `0x6E30` | -384 |
| `.data` | `0xAA0` | `0xAA0` | 0 |

The delta is **exactly** the linear map's three tables plus the boot
table. Nothing else in `.bss` moved, in either direction.

**Why the sets did not shrink, which is the finding.** They genuinely
lost their kernel content — every set is now the process's two windows
and nothing else, no EL1 RAM blocks, no EL1 device blocks. But that
content was ENTRIES INSIDE TABLES THE USER WINDOWS STILL NEED, not
tables of their own:

- the RAM level 2 had 510 kernel blocks and 2 window entries; it still
  exists, for the 2;
- the device level 2 had 511 kernel blocks and 1 window entry; it still
  exists, for the 1.

A translation table is a PAGE. Removing entries from one frees
descriptors, never a table, so a set is `512 + 512 + 1024 + 512 + 512
= 3072` descriptors before and after. The brief's arithmetic assumed
kernel content that could be deleted wholesale; at a 4 KiB granule
there is no such thing. The saving that DID happen is the one the
brief's point 3 names — kernel table content exists once instead of
three times — and it is worth 12 KiB against the 36 KiB three copies
would have cost, which is why the true comparison is "+12 KiB, not
+36 KiB" rather than a shrink.

`.bss` is `NOLOAD`, so this costs zero image bytes. Headroom:
`_bss_end` is `0x400A_5940` physical against `ROOT_LOAD_BASE`
`0x4020_0000` — **1.35 MiB spare, room for ~57 more sets**, unchanged
in character from what design 27 recorded.

### The sweep — 17 sites

Every kernel-side `UnsafeMemory`/`UnsafePointer` built from a stored
PHYSICAL address, and what it became. `kernel/sysapi/` is excluded by
definition: it is the userspace-facing module, not kernel-side.

**Converted with `phys_to_virt` (12):**

| site | what the address is |
|---|---|
| `kernel/core/loader.saw` `header_at` | a sosimg blob's base |
| `kernel/core/loader.saw` `seg_at` | a segment record inside a blob |
| `kernel/core/loader.saw` `place_image` — `long_copy` dst | the child's destination in RAM |
| `kernel/core/loader.saw` `place_image` — `long_copy` src | inside the sosimg blob |
| `kernel/core/loader.saw` `place_image` — `long_zero` dst | the destination's `.bss` tail |
| `kernel/core/process.saw` `region_header_at` | the `.regions` section |
| `kernel/core/process.saw` `region_row_at` | one row of it |
| `kernel/core/process.saw` `copy_out` — `dst` only | the PROCESS's buffer |
| `kernel/core/process.saw` `copy_in` — `src` only | the PROCESS's buffer |
| `hal/arm64/kernel/lib.saw` `put_desc` | a translation table |
| `kernel/core/slab.saw` `Slab.extent_addr` | a donated slab extent (**added at the design-32 rebase** — see below) |
| `kernel/core/dispatch.saw` `slab_donate`s `long_zero` | the same extent, zeroed before install (**same rebase**) |

**Converted, HAL-internal MMIO (5 constructions across 2 devices):**
`Pl011.init` (one site, both `console_byte` and `rt_write` reach it);
`Gic.init` (two views), `Gic.word_at`, `Gic.byte_at`, `Gic.reset`'s
inline disable view, `Gic.raise_to_self`'s trigger view.

**Converted with `virt_to_phys` (3) — the other direction, where a
kernel address must become hardware's or data's:**

| site | why |
|---|---|
| `hal/arm64/kernel/lib.saw` `pool_base` | a TTBR and every table descriptor hold PHYSICAL bases |
| `hal/arm64/kernel/sink.c` `sos_payload_start`/`_end` | granted to EL0 and read as an image base |
| `hal/arm64/kernel/sink.c` `sos_region_table_start`/`_end` | the same, one section over |
| `kernel/core/process.saw` `mint_boot_regions` — `row.base` | becomes a `Memory`/`IoMemory` capability's base |

**Deliberately NOT converted (4), and this is the half worth reading.**
An address taken from a Saw reference is already a kernel address — the
linker placed it — so converting it would be a bug:
`kernel/core/threads.saw` `frame_slot` (`&var THREAD_FRAMES`),
`kernel/core/objects.saw` `exchange_body_addr` (`&var PIPE_BODIES`),
`hal/arm64/kernel/lib.saw` `frame_init` and `frame_at` (a frame address
comes from `frame_slot`), and every
`(&var rec) as UnsafePointer<...> as UInt` in `refs.saw`/`dispatch.saw`
that feeds the kernel side of a copy door. That asymmetry is exactly
what makes `copy_out(dst, src)` convert `dst` and not `src`, and it is
said at both doors.

**The region-table row is the one genuinely subtle site.** Its bases
come from two producers: a BLOB row's base is a linker expression (the
child section's own symbol, which is the whole point of generating the
stub), so on a higher-half kernel it resolves HIGH; a destination, pool
or device row's base is a literal the build wrote, already physical.
One `virt_to_phys` answers both, because masking off bits that are not
set changes nothing — idempotent on the literals, corrective on the
symbols. It happens once, before the base is stored, because that base
becomes a user-visible capability. **The alternative — teaching
`tools/sos_runner.py` to emit `symbol - LINMAP_OFFSET` against a
`PROVIDE`d per-HAL symbol — was considered and not taken:** it would
have touched the runner and three linker scripts to move a conversion
the kernel can state in one line, at the one place rows are read.

### The fault class — it DID flip, and it was checked by probe

The brief's hazard materialized, exactly where it said it would. With
non-granted window pages now ABSENT rather than present-and-EL0-denied,
a stray EL0 access changes class. Measured on `umode_access_fault`,
same case, both sides built in this worktree, raw `ESR_EL1` read off
the console (which the gate does not assert on):

| | `ESR_EL1` | EC | WnR | **DFSC** |
|---|---|---|---|---|
| before | `0x9200004F` | 0x24 | 1 | `0x0F` — **permission fault, level 3** |
| after | `0x92000047` | 0x24 | 1 | `0x07` — **translation fault, level 3** |

Same `EC`, same direction bit, and `cause_tag` decodes only those two —
so both render `store-access-fault` and no transcript row could move.

**The normalization is now a decision rather than an omission**, and it
is written down at `abort_direction` in `hal/arm64/kernel/lib.saw`:
§5.7 reports whether the access was refused and in which direction;
which table level noticed is the kernel's business, and the same
vocabulary has to serve a PMP violation that has no class at all. The
brief was right that this needed saying even though nothing observable
changed — design 27's finding 3 stands, the gate cannot see a fault
class, and unit 3 (punted, not cancelled) would have found this
silently.

### Recorded for the record: what TTBR0 is now

A set is 3072 descriptors of which **two entries are live at rest** —
one level-1 entry per window's table chain — and everything else is
invalid. `load_domain(ROOT_PROCESS)` at the boot door is unchanged and
still installs set 0 by name. `boot.S` sets `TTBR0` to set 0 before
`kmain` for the six legacy harness kernels (`umode`, `extirq`, `timer`,
`timer_mask`, `preempt_tick`, `preempt_extirq`), which program the
protection surface directly, hold no grant record and never switch a
domain — design 27's survival argument for them is untouched, because
`tables_base()` still answers `set_base(CURRENT_SET)` and `CURRENT_SET`
still starts at 0.

`UNMAPPED_PROBE` (0x8000_0000) is still an address the kernel cannot
reach: its level-1 entry was invalid before and is invalid now, in
every set and in the linear map.

### riscv32

Two identity bodies in `hal/riscv32-common/kernel/lib.saw`, re-exported
by both boards' facades (`hal/riscv32/`, `hal/riscv32-esp32c3/`). No
behavior change of any kind — every `phys_to_virt` in `kernel/core`
folds away to its argument there, which is what the byte-identical
riscv32 half of the gate diff proves. The Sv32 note the brief asked for
is carried at the section header: this board's RAM at 0x8000_0000
already sits in the upper 2 GiB, so an Sv32 kernel never moves and the
seam stays the identity even after the punted climb.

### Rebase onto design 32 (slab donation), Sep 3

Design 32 merged to main (`d919aae`) while this unit was in review, so
this unit is the SECOND REBASER and the coordination seam both briefs
named is discharged here.

**The rebase itself was almost nothing.** One conflicting file,
`designs/todo.md`, and it is a pure adjacency — two units appending
their annotation to the same M5 entry, resolved by keeping both. Not a
single code file conflicted: 032 touched no HAL file and this unit
touched no allocator file, exactly as both reports predicted.

**THE SEAM WAS TWO SITES, NOT ONE — and that is the finding.** Both
briefs, both As-builts and the dispatch instruction all said the edit
was one line: `Slab.extent_addr`'s return. It is not. The donation path
touches a donated extent TWICE, and the second one is in a different
file:

1. `kernel/core/slab.saw` — `Slab.extent_addr`, the READ funnel. Every
   dereference of a donated slot goes through it, and it now returns
   `hal.phys_to_virt(base + offset)`. This is the site everyone
   anticipated, and 032's own module header is what made it findable.
2. `kernel/core/dispatch.saw` — `slab_donate`'s `long_zero(start, …)`,
   the WRITE that zeroes an extent before installing it (a slab slot
   must start `Free`, and `Free` is zero). It reaches the extent by its
   raw physical base and runs BEFORE the extent is in the table, so it
   never passes through the read funnel at all.

`start` and `ext_base[e]` both stay PHYSICAL — an extent's base is
addresses-as-data and is what the extent table records — and only the
two dereferences convert. Said at both sites.

**What found it was the witness, not review.** With only the funnel
flipped, `slab_donate` failed on arm64 with
`kernel fault ec=0x25 elr=0xffffff804000abdc far=0x0000000040280000` —
a kernel-mode data abort whose ELR is a high kernel address and whose
FAR is the raw physical extent base, which is precisely the shape a
missed conversion has. `slab_donate_shared` passed throughout, because
it never donates. So the case that proves the seam is the case that
catches it being half-done.

**This is why the coordination seam was worth naming in advance and
still not enough.** A missed site here is a hard fault on a translating
tier and completely invisible on an MPU one — riscv32 passed both cases
before and after the fix, at every stage. Anyone adding a THIRD
dereference of donated memory gets no warning from the riscv32 half of
the gate.

**Re-gate on the combined state: 119 cases, 238 passed across riscv32 +
arm64**, 495 lines, hashing to
`13b3214ce2b27aec793b189dcb1002a52bdd835ead664638947f3a354b8083b1` —
**exactly the hash design 32's As-built records for its own 238-run on
main**, so the combined tree reproduces main's transcript byte for byte
and this unit adds no row on top of 032's. The slab-donate cases now
execute over this unit's linear map on arm64, which makes their passing
the seam-flip's own witness.

### Findings

1. **THE `.bss` DELTA IS POSITIVE, +16 KiB, against a brief that
   expected negative.** Reason above: at a 4 KiB granule, removing
   entries frees descriptors and never tables, so the per-process sets
   are byte-for-byte the size they were. The honest framing of the win
   is "+12 KiB instead of +36 KiB", not a shrink.
2. **The fault class flipped, permission → translation**, and only a
   probe could see it. Syndromes recorded above.
3. **PXN is not end to end.** One 2 MiB block — the kernel's own image —
   is EL1 RWX, and per-section W^X is a named, unspent refinement.
4. **No SL entry owed.** The language did not bite. Two spellings cost
   a moment and both are documented rules rather than gaps: a `static`
   initializer does not wrap after `=` (parenthesize the expression),
   and `static_assert` is not a documentable declaration so a `///`
   block above one is a clean error. Neither is a workaround; both are
   the language saying what it says.
5. **`LINMAP_OFFSET` lives in two files with no build-time link between
   them.** Closed by a boot-time probe rather than left open, but a
   reader should know the check is dynamic. The same is true of
   `boot.S`'s descriptor bits against `lib.saw`'s, where the
   consequence of drift is an immediate failed boot.
6. **THE DESIGN-32 COORDINATION SEAM WAS TWO SITES, NOT THE ONE
   EVERYBODY WROTE DOWN.** The read funnel (`Slab.extent_addr`) was
   anticipated by both units; the write that zeroes an extent before
   installing it (`slab_donate`'s `long_zero`) was not, and it lives in
   another file and runs before the funnel exists. A test caught it, not
   a review — and only on the translating tier. Recorded above with the
   fault it produced, because the general lesson outlives this pair: a
   "one access funnel" claim covers reads, and memory has to be
   initialized before it can be read.
