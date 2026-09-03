# SawOS design 27 — M5 unit 1: arm64 translation-as-isolation

**Status: BUILT Sep 3 2026 (see "As built" below) — dispatches under `designs/025` (RULED,
the M5 plan of record): D-1 step one + D-2's seam edit pair + D-3's
static pools, on arm64-virt only.** riscv32 is UNTOUCHED in behavior
this unit — its HAL absorbs the seam rename mechanically and keeps
replaying PMP exactly as today.

## The one-sentence goal

Every process gets its OWN persistent translation-table set; a
process switch becomes a TTBR0+ASID write instead of a ~1.6k-store
window rewrite; a process's tables map ONLY its own grants, so peer
isolation is by ABSENCE of a translation rather than by permission
bits in a shared window — with VAs identity throughout (§5.5
survives verbatim, D-1 step one) and the console transcript
BYTE-IDENTICAL on both architectures.

## What exists (verified at `56ad00e`)

- One table set built at boot, shared by all processes:
  identity 2 MiB EL1-only blocks over RAM; a 4 MiB grant window
  (`0x4000_0000..0x4040_0000`) of level-3 pages; a device level-3
  window for the console page (`hal/arm64/kernel/lib.saw`).
- `load_domain(p)` (kernel/core/process.saw) = `hal.prot_reset()` +
  replay `p`'s grant rows through `hal.prot_region`/`prot_device` +
  `hal.prot_commit()`; full-window reset + TLB flush per switch
  (cost recorded at design 2 D-5). `run_thread` skips equal domains;
  `domain_changed(p)` reloads when the live domain's record is
  edited (the LIVE-DOMAIN RULE); `remove_grant` compacts by index.
- No ASIDs anywhere.

## D-1 — the seam, as ruled (025 D-2/D-3)

The kernel-side grant RECORD stays authoritative (teardown,
compaction, and the quota walks read it; nothing about
`GrantRow`/`remove_grant`/`compact_mapping_rows` changes shape).
What changes is WHEN hardware learns about it:

- **The seam grows an edit pair** — spelled by this unit, proposed
  `hal.prot_install(p, idx, row)` / `hal.prot_remove(p, idx, row)`
  — called wherever the record changes (install_row, remove_grant,
  and teardown's clears), for ANY process, current or not.
- **`load_domain` becomes a switch**: on arm64, write TTBR0_EL1
  (table base + ASID) and nothing else — no descriptor stores, no
  full flush. On riscv32 the function keeps its exact current body
  (reset + replay + commit IS the MPU's switch, per the ruling:
  replay-at-switch stays MPU-only).
- **The live-domain rule becomes TLB maintenance** on arm64: an
  edit to the CURRENT domain's tables is followed by
  invalidate-by-VA/ASID (or by-ASID; the agent picks the narrower
  one that is provably sufficient and says why at the site). An
  edit to a NON-current domain needs only the table store — its
  ASID's stale entries are handled at (a) nothing, if entries are
  ASID-tagged and the edit added; (b) invalidate-by-ASID at the
  edit, if it removed. Removal invalidates eagerly: an unmap that
  waited would be a revocation that did not revoke (the same
  sentence `domain_changed` carries today).
- **How the two HALs split**: the riscv32 HAL implements the edit
  pair as a NO-OP (its replay at switch reads the record; the
  live-domain reload path stays, now expressed through the seam the
  same way). The exact seam spelling that keeps both HALs honest
  without a tier conditional in kernel/core is this unit's main
  design surface — bring the shape, record it in the As-built.

## D-2 — the table sets (D-3's static pools)

- **Per-process sets from a static HAL pool**: MAX_PROCESSES ×
  (L1 + L2 + grant L3s + device L3) — same species of constant as
  today's single set, same 4 MiB grant span, same device window.
  Order-of-magnitude ~20 KiB/process, ~60 KiB total at
  MAX_PROCESSES 3; the .bss growth is expected and recorded in the
  As-built (the measured-RAM section of 025 is the context: virt
  has room, MPU boards are untouched).
- **Kernel placement is HAL-internal (ruled)**: the EL1 identity
  blocks appear in EVERY set (the simple v1), or TTBR1 carries them
  once — the agent's call, invisible above the HAL. NOTE the
  cross-process copy funnels rely on it: pipe/give copies touch the
  OTHER process's RAM through EL1 mappings while the CURRENT set is
  loaded, so whatever placement is chosen must keep all-RAM EL1
  reachability in every configuration. Say so at the definition.
- **ASID = process slot index** (proposed; 16-bit field, 3 slots —
  no allocator needed). Slot REUSE must invalidate the ASID's TLB
  entries: hang it on `clear_domain`/teardown, where
  `LAST_PROT_PROCESS` is already invalidated today.
- **Boot**: root's set is built where the shared set is built today;
  a created process's set starts EMPTY-of-grants (EL1 halves
  present) and fills through the edit pair as the loader installs
  rows. The boot door's ordering (design 2: `load_domain(ROOT)`
  before entering user mode) is unchanged.

## The transcript hazard, named

Under the shared window, a stray user access faults as a PERMISSION
fault; under per-process tables the same access is a TRANSLATION
fault (the descriptor is absent, not deny-marked). The trap decode
must map both ESR classes onto the SAME §5.7 fault reason it
reports today, or fault-case rows move. The isolation proofs
(`process-isolation` et al.) and every fault case are the witnesses;
byte-identical is the requirement, and a row that moves is a
finding to bring back, not to authorize locally (unit 2 holds the
only move authorization, and it is for placement, not for this).

## Gate

`make sos-test`, suite lock protocol, transcript diffed against a
pre-change baseline run in the same worktree: BYTE-IDENTICAL, both
architectures, the three documented timing rows excepted. The
riscv32 half of the diff proves the seam rename cost nothing; the
arm64 half proves the mechanism swap is invisible, which is the
design-23 tradition this unit inherits.

## Out of scope

VA placement, link-base collapse, `map` answering an address (unit
2 — its transcript-move authorization does NOT cover this unit);
riscv Sv32 (unit 3); the tier word (unit 4); any allocator/donation
work (units 5–6); spec.md prose beyond doc comments at edited
declarations (unit 8's sweep owns the sections).

## As built (Sep 3 2026)

**Status: BUILT.** Gate green and the transcript byte-identical on both
architectures; riscv32's behavior is unchanged by construction.

### The seam, as landed (THREE names — user-ruled Sep 3, reworked before merge)

The unit first landed a five-name edit surface
(`prot_install` + `prot_remove` as a pair). **The user ruled it down to
three before merge**, and the collapse is recorded here rather than
hidden in a diff because the reasoning is the interesting part:

```saw
PROT_REPLAY_AT_SWITCH: Bool          // arm64 false, riscv32 true
prot_switch(p: UInt)
prot_update(p: UInt, base: UInt, top: UInt, perms: UInt8, device: Bool)
prot_clear(p: UInt)
```

**`prot_update` replaces the pair: `perms == 0` MEANS REVOKE.** This is
a real domain value, not a mode flag, so it does not reopen the Optional
discipline (a `Bool` "install-or-remove" argument would have been exactly
the mode flag that ruling exists to refuse). The unit's own structural
finding is what licenses it — a non-granted window page is present and
EL0-denied anyway, so **removal IS the installation of deny**, and the
two directions were never two operations on a translating tier.

What makes zero SAFE to spend, verified at both producers rather than
assumed:

- an image segment must be readable or executable
  (`imgformat.has_sane_perms` ends `is_readable() || is_executable()`),
  so `flags == 0` never reaches the loader's `record_grant`;
- `map_access` refuses NO ACCESS AT ALL by name, as a caller-visible
  `BadArg` — "a row that permits nothing spends a protection slot to say
  what default-deny already says".

So the grant vocabulary already had no use for zero. It cannot collide
with a live row, which is what turns "a grant of nothing is a
revocation" from a pun into an encoding.

The DEVICE window takes the same path, `device` picking which window
rather than which operation: the grant direction ignores `perms` exactly
as `prot_device` always has (a device page is user read/write and never
executable — a property of registers, not of the image's flag bits), and
the revoke direction restores `kernel_device_page`, the deny spelling
that window already resets to. Said at the site.

**`prot_clear` STAYS a separate name, deliberately.** It is domain
RETIREMENT, not a range edit with wide bounds, and it carries two things
`prot_update` cannot: it is the FAIL-CLOSED MASK (a whole-set reset,
correct even if record and hardware have skewed — which is exactly the
moment you cannot trust the record to enumerate what is live), and it
ANCHORS THE ASID-REUSE INVALIDATION to one named lifecycle event, since
a slot and its address-space identifier become reusable here and nowhere
else. Recorded at the definition so nobody re-asks.

**`PROT_REPLAY_AT_SWITCH` and the MPU replay quartet
(`prot_reset`/`prot_region`/`prot_device`/`prot_commit`) are unchanged.**
The advertised constant is there because a genuine tier conditional is
unavoidable: the replay must read `PROCESSES[p].grants`, and a HAL cannot
import the kernel to read it, so the LOOP cannot move into the HAL. What
CAN move is the DECISION — the kernel asks the HAL which tier it is on,
and the loop folds away at compile time on arm64 and stays whole on
riscv32. Design 19's "difference ADVERTISED, never faked", rather than a
conditional the kernel invented.

**CONSIDERED AND NOT TAKEN: collapsing the replay quartet into
`prot_update` too.** Declined (user, Sep 3): it touches the staged
reset/commit PUBLISH contract — `prot_reset` and `prot_commit` bracket a
sequence that must never be half-live, and the four calls are the MPU's
staging protocol, not four spellings of one edit — for zero behavioral
gain. The quartet keeps its own shape and its own audit note.

**Where the calls hang — the record's own funnels, not the op sites.**
Both directions go into the record's own funnels rather than into
`install_row`/`Unmap` as the brief sketched: `record_grant` calls
`prot_update` with the row's real permissions, `remove_grant` calls it
with zero. `record_grant` is the single funnel every row is appended
through (the loader's three at boot, `install_row`'s one from a `map`),
so record and tables move together by construction instead of by two
call sites agreeing to stay in step — and it is what makes the BOOT path
work at all, since `place_image` records root's rows and deliberately
"commits nothing to hardware", so an edit hung on `install_row` alone
would have left root's tables empty. `remove_grant` reads the row's
bounds BEFORE its own compaction moves them.

One departure from the brief survives the rework unchanged: **the edit is
VA-KEYED, not `idx`-keyed** (`prot_install(p, idx, row)` was proposed). A
`GrantRow` is a `kernel/core` type the HAL cannot see, so the row arrives
as its fields; and `idx` is the asymmetry the seam carries badly — the
numbered PMP region on riscv32, *unused* on arm64. A translation HAL keys
tables on the VA range, so the index stays where it means something: in
the kernel's record.

`load_domain` gained the guard and one trailing `prot_switch`.
`domain_changed` and `run_thread` are UNCHANGED: on arm64 `load_domain`
is now four folded-away no-ops plus a register write, so the live-domain
rule costs a redundant TTBR0 write and stays correct without knowing why.

**The legacy path survives untouched.** Six harness kernels in `tests/`
(`umode`, `extirq`, `timer`, `timer_mask`, `preempt_tick`,
`preempt_extirq`) call `prot_reset`/`prot_region`/`prot_commit` directly,
hold no grant record and never switch a domain. Making those no-ops on
arm64 would have broken all six. They keep their exact meaning — "edit
the LIVE set" — because `tables_base()` now answers `set_base(CURRENT_SET)`
and `CURRENT_SET` starts at 0, the set the machine boots on.

### Table-pool arithmetic and the measured .bss delta

One set is unchanged in shape: L1 512 + L2 512 + RAM L3 1024 + device L2
512 + device L3 512 = **3072 descriptors = 24 KiB**. The pool is
`PT_SET_DESCRIPTORS * PROT_DOMAIN_SLOTS + 512` (one table of alignment
slack for the run-time round-up, since Saw has no alignment attribute on
a static and sawc emits every static into one plain `.bss` input section
— so the linker script cannot align it either).

| | descriptors | bytes |
|---|---|---|
| before (one shared set + slack) | 3584 | 28,672 (`0x7000`) |
| after (3 sets + slack) | 9728 | 77,824 (`0x13000`) |
| **delta** | +6144 | **+49,152 (`0xC000`, 48 KiB)** |

Measured with `llvm-nm --print-size` on `saw.static.PAGE_TABLES$m$hal`,
and the whole-`.bss` delta is the SAME `0xC000` (`0x2DBB0` → `0x39BB0`
on `trap_fault.elf`) — nothing else grew. `.bss` is `NOLOAD`, so this
costs zero image bytes; it costs ~6k more doublewords in `boot.S`'s
zerofill and two more set-builds at boot (~7680 descriptor stores, still
pre-IRQ).

Headroom: `_bss_end` is now `0x400B_5BB0` against `ROOT_LOAD_BASE`
`0x4020_0000` — **1.35 MiB spare**, room for ~57 more sets. Nothing
checked that ceiling before; `virt.ld` now carries an
`ASSERT(_bss_end <= 0x40200000)`, because overrunning it would have
corrupted root's image at load with no diagnostic anywhere.

### Kernel placement: EL1 blocks in EVERY set, and why (the brief asks this be said at the definition — it is, at `build_set`)

TTBR1 was never an option: `sink.c` sets `TCR_EL1.EPD1`, so the high half
is not walked at all, and every address this kernel uses is identity in
the LOW half — TTBR0's range. Kernel mappings have nowhere else to live.

The cross-process copy funnels then force the rest. `place_image` writes
a child's segments while another set is live, and `copy_in`/`copy_out`
touch a peer's buffer the same way; those go through whatever set
`TTBR0_EL1` holds, so **every set maps all of RAM at EL1**. The kernel's
`.text` and this very table pool sit INSIDE the grant window (`.bss` at
`0x4005_2000`, window `0x4000_0000..0x4040_0000`), so a window page that
was merely absent rather than EL1-only would have unmapped the kernel
from itself. (PAN is not enabled, so EL1 reads of EL0 pages work as
before.)

### The fault-class question — and why no row could move

**The brief's hazard did not materialize, for a better reason than luck:
a non-granted window page is still PRESENT and EL0-denied, so the fault
class never changed.** That is not a choice made to protect the
transcript; it is forced by the paragraph above — those pages must stay
EL1-mapped for the kernel to reach its own text and its peers' memory.
Isolation here is the absence of an EL0 PERMISSION in a table only this
process's ASID selects, which is a per-process property, not a shared
window. So "isolation by absence" holds at the level that matters (the
tables are per-process) while the descriptor stays present.

Two further findings, recorded because they change the risk the brief
priced:

- **The tag was already class-blind.** `cause_tag` decodes ONLY the ESR
  `EC` field (bits 31:26); a translation fault and a permission fault
  are both `EC=0x24` and both render through `abort_direction(iss)` to
  the same `store-access-fault`. There is no DFSC/IFSC decode anywhere
  in the HAL. So even a class change could not have moved the tag.
- **The raw ESR is printed but is not in the gated transcript.**
  `fault_trap` writes `cause=<hex>` of the whole ESR to the console, and
  the DFSC bits do live in it — but the 483-line gate transcript holds
  build sizes and pass/fail rows, not per-case console text, and all
  three fault assertions in the runner are the bare prefix
  `"SOS: fault "`. A class change would therefore have been INVISIBLE to
  the gate. Worth knowing for unit 3: the riscv32 climb gets no warning
  from this gate if it changes a fault class.

ASID = process slot index, no allocator. `TCR_EL1.AS` is 0, so the field
is 8 bits and three slots use three of 256 (the brief said 16-bit; the
register as configured says 8 — corrected here, and it binds nothing).
Every window descriptor carries `ATTR_NG` so entries are ASID-tagged and
a switch invalidates nothing; the 2 MiB EL1 blocks outside the windows
stay GLOBAL, since they are identical in every set and sharing one TLB
entry for them is free and correct.

**TLB maintenance: by ASID, on every edit.** `tlbi aside1is`, one
instruction, in `invalidate_domain`. By-VA is narrower per page but a
grant row spans a whole region (root's is 64 pages, a mapped Memory can
be more), so by-VA is one `tlbi` per page against this one and buys
nothing: the edit is confined to one process's set, every descriptor
there is `ATTR_NG`, and no other domain's entries can carry the tag — so
by-ASID is both provably sufficient and strictly cheaper. It runs on
non-current domains too, deliberately: `p` may have stale entries from
when it last ran, and one instruction is cheaper than being wrong about
which case we are in. Removal invalidates eagerly, which is the
revocation-that-did-not-revoke sentence `domain_changed` has always
carried. The full-flush `sos_prot_commit` (`tlbi vmalle1`) is untouched
and now serves only the legacy harness path and riscv32.

### What the switch costs now

`load_domain` on arm64 was a window reset (1024 + 512 descriptor stores)
plus a regrant walk plus a full TLB flush — the order of 1.6k stores per
process switch, the cost design 2 D-5 recorded and accepted. It is now
**one `msr ttbr0_el1` plus an `isb`**, and no TLB work at all. The
descriptor stores did not move elsewhere: they happen once per `map`, at
the edit, instead of once per switch.

### Gate evidence

- Baseline `make sos-test` at the merge-base (`bc451cb`) in this
  worktree, BEFORE any edit: **116/116 cases, 232 passed across riscv32
  + arm64**.
- After the change, same command: **116/116, 232 passed**, both
  architectures.
- **Re-gated after the three-name seam rework**: **116/116, 232 passed**,
  both architectures, transcript byte-identical again and to the same
  hash. So the collapse is witnessed as behavior-preserving in its own
  right, not merely inherited from the first gate.
- `diff baseline after` → **BYTE-IDENTICAL**, 483 lines, no exceptions
  claimed — the three documented timing rows did not move either.
- Both transcripts hash to
  `3f6dde15f0f9e3f3cea88bcd3976902414028054c323c96afea74b0460add307`,
  which is **the same `3f6dde15` design 24's As-built recorded** — so
  this is byte-identical to the milestone's established transcript, not
  merely to a baseline taken for this unit.
- The riscv32 half proves the seam rename cost nothing: five new names,
  four of them empty bodies, and its half of the diff is unchanged.
- The arm64 half proves the mechanism swap is invisible from above.
  Note what makes that a real proof rather than a weak one: the replay
  is compiled OUT on arm64 (`PROT_REPLAY_AT_SWITCH` is `false`), so
  `prot_update` is the ONLY path by which a grant reaches arm64
  hardware. Every one of the 116 cases boots root, loads images and
  runs children through it; `process_isolation` and `child_oversteps`
  re-witness peer isolation under the new mechanism specifically, and
  `map_unmap` / `iomemory_carve` witness the revoke direction and the
  device window through the collapsed call.

### Findings

1. **No SL entry owed.** The language did not bite once in this unit —
   no workaround written, no idiom refused.
2. **The `idx` parameter is now dead weight on both profiles' new
   calls** and remains live on the old four. Not cleaned up here: the
   brief forbids surface churn beyond the unit, and riscv32's
   `prot_region` genuinely means it.
2b. **THE ARCH-FREE LINT READS PROSE, not just code** — and it caught a
   comment during the seam rework: `sos-test` refused the build with
   "architecture names in the arch-free kernel" over the word `EL0` in a
   `kernel/core/process.saw` COMMENT (design 162 unit 1: kcore reaches
   the machine through `hal` only). Reworded to "denied to user mode".
   Worth knowing before writing kernel-side commentary about a HAL
   mechanism: the rule governs what the file SAYS as well as what it
   calls, which is stricter than it first reads and is the right
   strictness — a kernel comment that names EL0 is a kernel that has
   quietly learned which machine it is on.
3. **Unit 3 (riscv Sv32) inherits an unlit gate for fault classes** —
   see the second bullet under the fault-class question above. If that
   unit wants the class witnessed, it has to add the assertion first.

## Recording duties

As-built in this file: the seam spelling as landed, the table-pool
arithmetic and measured .bss delta, the ASID/TLB-maintenance
choices with their reasons, the fault-class mapping, gate evidence
(case counts, both arches, byte-identical or the finding). New SL
entries to `designs/todo.md` [SAWLANG] in the established format if
the language bites. Close the tracker entry in place; the lead
moves it at integration.
