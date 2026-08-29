# SawOS design 2 — CreateProcess: the second address space (M3 unit 2)

Status: BUILT Aug 28 2026 (see "As built" at the end — two deviations
from the brief's letter and one structural finding, each with its
argument). AUTHORED Aug 28 2026 (lead), REVISED same day. Implements
sawlang#232 unit 2 (the two-phase lifecycle ruled Aug 16; agenda
item 2's hybrid ruled Aug 16). Ruling history of Aug 28, recorded in
order because the day held three:

1. Zero-copy was ruled ("push back against the kernel doing the
   memory copying"), and a link-in-place design was drafted.
2. **SUPERSEDED (user, same day): THE LOADER STAYS IN THE KERNEL
   ENTIRELY** — it does the copying from the image format into the
   process address space; splitting the loader into userspace comes
   later if/when necessary. Lead concurrence recorded: link-in-place
   was a second code path (§12 forbids exactly that), real stitcher
   complexity, one-shot children, and an arm64 layout constraint —
   buying latency that unit 1.5's points already bound. Its one
   enduring case (flash XIP on real hardware) returns additively as
   a unit-4 Memory-pool attribute when hardware makes it true.
3. **ROOT PROVIDES THE MEMORY** (ruled via the stack question, and
   composed with ruling 2): `process_create(image: Memory,
   memory: Memory)` — `image` is the read-only source blob region,
   `memory` is the child's destination RAM region. Segments are
   copied to their link addresses inside `memory`, and THE STACK IS
   THE KERNEL'S GRANT AT THE REGION'S TOP — root's own pattern
   (loader.saw: "the stack is the kernel's grant, not the image's").
   Root supplies no numbers, only capabilities it was handed at
   boot. sosimg stays v3; no sawlang change, no pin bump.

Two pieces of later units arrive EARLY, subtracted from their homes
rather than re-added there: `boot_handle_next` (unit 3's iterator —
root needs it to receive the boot-minted Memory handles) and a
MINIMAL Memory object kind (unit 4's §2.5 object, first slice:
sealed boot regions, no pools, no map, no derivation).

## D-1: One loader, second door — validate, COPY WITH POINTS, protect

`process_create` reuses the loader that loads root today, phase by
phase, against caller-provided regions instead of hal constants:

- Phase A/B (header + per-segment validation, all before any
  effect): the existing checks re-targeted — file extents against
  the IMAGE region, load addresses against the DESTINATION region
  (D-7).
- Phase C (placement): `long_copy`/`long_zero`, the strided movers —
  **the copy loop is born with its preemption points, which is what
  unit 1.5 landed first FOR** (sawlang#232 pin 1's own sequencing).
  `preempt.saw`'s header sentence naming "unit 2's CreateProcess
  image copy" as its next consumer comes true rather than stale.
- Phase D (protect + record): per-segment `prot_*` rows + the stack
  row written into the process slot's GRANT RECORD (D-5); nothing
  committed to hardware here — the reload at the switch is what
  installs a domain.

The loader refactor is a SPLIT INTO SHARED PHASES, not a copy of the
function: root's boot path and `process_create` call the same
validate/place/record code with different region parameters and
different failure vocabulary (boot: `fatal_image`, the machine has
nothing else to do; create: `BadImage` STATUS, D-4). §12's
"no second code path" is kept by construction and said at the seam.

The image blob is read by the KERNEL (EL1/M-mode) during the copy —
it needs no EL0 grant, so no grant-window constraint touches it on
arm64; only the DESTINATION region must be grantable (inside the
4 MiB window, above root's top). The blob region is dead bytes after
the copy — agenda item 2's lean (ii), accepted waste, noted not
engineered around.

## D-2: The region table, and the minimal Memory kind

The stitcher (tools/sos_runner.py) appends each child's sosimg to
the boot image AS-IS — no flattening, no absolute placement; the
blob lands wherever the linker puts it — and records regions in a
REGION TABLE: offsets and lengths, nothing more (the ruled hybrid).
Two kinds of row, indistinguishable to the kernel on purpose: blob
regions (where an appended image sits) and free-RAM regions (a
child's destination range, config-assigned, disjoint from
kernel/root/blobs). Root's config knows which ordinal is which; the
kernel never does.

Table mechanics: its own section with ONE new fixed symbol pair
(`_region_table_start/_end`) through both sink.c's — the existing
DF-172a pattern. Format (sawos-owned, fixed-width LE): magic,
version, count, count × {base: u64, len: u64}. Blob rows' bases are
LINK-RESOLVED (the stitcher can emit the table as a generated .S
referencing the blob sections' symbols, letting the linker fill the
addresses — mechanics are the implementer's; the table format is
frozen here). A missing/empty table is ZERO regions — every
existing case boots byte-identically, keeping the 84 oracle rows.

At boot the kernel parses the table and MINTS ONE Memory PER
REGION: `ObjType.Memory = 9`, a sealed {base, len} — no ops in this
unit, rights `Transfer | Manage` minted (Transfer is unit 3's give;
nothing here reads either). A new slab (`MAX_MEMORIES = 8`,
limits.saw) with the usual per-slot `process` field and teardown
arm. §2.5 is not contradicted, it is STARTED: these are the "pool
roots given to the root server at boot" in v1 static form; unit 4
adds pools, derivation, map(), attributes.

## D-3: Delivery is `boot_handle_next`, pulled forward

Root receives the minted Memory handles through unit 3's ruled
iterator, arriving early because root is its first consumer:
`ProcessOp.BootHandleNext` on root's own Process handle — one
{tag, kind, handle} record per call through the EXISTING copy-out
funnel (3 machine words, the wait-record shape), CONSUMED on
delivery, exhaustion a status that stays exhausted (a loop's
termination check, never a fault). Tags here are REGION ORDINALS
(table row index); root correlates ordinals to its config. The boot
set still enters user mode ONE handle wide (§12): the register
carries System, the iterator delivers the rest. Unit 3 reuses this
op unchanged for the child's drain; the record layout is agenda
item 5, settled here. The "drained" status value is the
implementer's to pick and document in sosabi (the one-place rule);
lean: a new `SosStatus` case, not a magic record.

## D-4: The surface — ops, rights, the two-phase lifecycle

Per the Aug-16 ruling, built as ruled (inert until started):

- **`ProcessOp.ProcessCreate = 7`** on ROOT'S OWN Process handle
  (creation authority is rights bits on Process — §12), gated on
  NEW `ProcessRight.ProcessCreate = 1 << 14`, granted in
  `root_process_rights()`. Args: image Memory handle, destination
  memory Memory handle. Validates everything (D-7); a malformed
  image is a **`BadImage` STATUS, not a fault** (ruled lean (i):
  image bytes are DATA on the `from(raw:)` precedent) and a failed
  create leaves the destination region's grant state untouched
  (bytes MAY have been partially copied before a later check only
  if any check is ordered after placement — DON'T: all validation
  completes before the first byte moves, exactly the boot loader's
  phase discipline). On success: allocate the process slot
  (`NoResource` when full), COPY (phase C), record entry (header) +
  stack top (destination region top) + the grant record, mint the
  child's Process handle to the CALLER with `Start | Wait | Manage`
  (no Transfer minted here; unit 3 revisits). Returns the handle.
- **`ProcessOp.Start = 8`** — receiver-verb (the receiver IS the
  child), gated on NEW `ProcessRight.Start = 1 << 15`. Mints the
  first thread INTERNALLY (no handle — nobody holds the child's
  thread in this unit; unit 3 decides what the child receives),
  `frame_init(entry, stack_top, a0 = NO_HANDLE)` — the no-boot-tag
  form: this unit's every child is the ruled "legal-but-doomed"
  sandboxed compute process — and `ready_push`es it. Root KEEPS
  RUNNING. A SECOND start is a `BadState` FAULT (ruled).
  `boot_tag:` arrives with give in unit 3.
- **`ProcessOp.BootHandleNext = 9`**, gated on NEW
  `ProcessRight.BootHandles = 1 << 16`, granted to root; what a
  child gets is unit 3's question.
- Stack length: the kernel grants `hal.ROOT_STACK_LEN` at the
  destination's top (one constant serves both processes v1; a
  per-create length is unit 4/5 vocabulary). Destination region
  must be big enough for segments + stack; checked.
- sysapi: typed `Memory` wrapper (opaque, NoCopy, the Process
  pattern), `Process.process_create(image:memory:)`, `.start()`,
  `.boot_handle_next()`, matching `sos_process_*` C exports.
  Naming per the Aug-17 rider throughout.

## D-5: Protection is RELOADED AT THE SWITCH — both arches, forced

The census closes this: riscv32 has FOUR TOR regions and a
3-segment root spends all four — two processes cannot share the
live set. arm64's grant window is ONE shared EL0 permission map, so
grants left installed across a switch are the other process's to
touch — same conclusion. So:

- `ProcessSlot` grows the GRANT RECORD: up to
  `MAX_ROOT_SEGMENTS + 1` rows of {base, top, perms, device},
  written by whoever grants — the boot loader records root's rows
  as it places (including the device-window row for driver images);
  `process_create` records the child's (segments + stack).
- `pick_next` reloads AT THE MARKED POINT (sched.saw:49-52) when
  the incoming thread's process differs from the last loaded domain
  (a `LAST_PROT_PROCESS` beside the scheduler state): `prot_reset`
  + replay rows + `prot_commit`. Cost, recorded honestly: ~19 CSR
  writes on riscv32 (no TLB); on arm64 a window reset + regrant
  walk + the full `tlbi vmalle1` commit — ~1.6k stores v1, accepted
  at M3 switch rates (a targeted clear of the outgoing rows is a
  permitted improvement; ASIDs/per-process tables are SMP-era).
  Same-process switches — every switch every existing case makes —
  reload NOTHING, which is what keeps the oracle safe.
- Harness kernels with no Thread objects hit `pick_next`'s
  `NO_THREAD` early return and are untouched; `tests/umode.saw`
  keeps driving `prot_*` directly. The two early returns in
  `pick_next` keep the SAME thread, so the domain cannot have
  changed there — say so at the site.

## D-6: N processes — lifecycle, teardown, deadlock

`MAX_PROCESSES = 2` (limits.saw's "raising this number is the only
edit" gets its first exercise; quotas at unit 5 revisit). `HANDLES`
scales by the existing arithmetic. The two hardcoded one-process
sites (`start_process`'s `let p = 0`, `calling_process`'s fallback
`0`) stay correct as written — slot 0 IS root — and gain the
comment saying so.

`end_process` grows the fork its own docstring promised:

- **Process 0 ending stops the machine**, exactly today's exit
  paths, byte-identical teardown report — root is init; children
  die with the machine, uncounted (v1, noted).
- **A child ending tears down and RESCHEDULES**: its threads leave
  the READY QUEUE BY REMOVAL (walk the links unlinking
  `process == p` entries) — the current wholesale
  `READY_HEAD = READY_TAIL = 0` is a one-process spelling that
  would zero ROOT's runnable threads. `CURRENT_THREAD` clears (the
  dying process is always the caller today), then the path ends in
  `pick_next` + `resume_frame` — still `-> Never`, never a stopped
  machine. The freed Memory-slab entries and the child's grant
  record clear with the rest.
- **Deadlock cascades**: `has_external_wake_source` stays GLOBAL
  and conservative; nothing-runnable-no-source faults the CURRENT
  process exactly as today — with two processes that kills the
  current one, reschedules, and stops when root dies. Today's rule
  applied to N; one harness case shows the cascade. (Death
  notifications, unit 5.5, are what let root WAIT on a child's
  death; nothing here pre-builds that.)
- `fault_trap`/`fault_process` route through the same fork.
- **The copy doors**: a child's writable window (`rw_base/rw_top`)
  is its writable segments' floor to its stack top, the same
  contiguous-window rule root's loader computes today — and this
  unit's children make no syscalls (no handles), so the doors never
  open toward one. The one-window model generalizes to the grant
  record when children get syscalls (unit 3); flagged, not built.

## D-7: What `process_create` validates (all before any byte moves)

The boot loader's checks, re-targeted; shared code, two failure
vocabularies (boot faults the machine, create returns `BadImage`):

- header: magic, version, arch vs `hal.arch_tag()`, `seg_count > 0`,
  `seg_count + 1 <= PMP_REGIONS`-equivalent on the region-budget
  seam (segments plus stack must fit the reload set on both
  arches).
- per segment: wellformed, sane perms, **`Device` REFUSED v1**
  (`BadImage` — a child with MMIO waits for unit 4's IoMemory; the
  echo-driver money shot rides that), `mem_covers_file`, alignment
  to `PROT_GRAIN`, file extents within the IMAGE region, load range
  within the DESTINATION region below its stack (the root-region
  checks with the constants swapped for the provided region's
  bounds).
- entry inside an executable segment's load range; destination
  large enough for segments + the stack grant; image and
  destination regions disjoint.
- Same Memory passed twice / overlapping regions across children:
  NOT globally policed v1 — regions are capabilities, the table is
  build-emitted disjoint, and root double-spending what it holds
  corrupts only what it holds. Noted at the site; real pools +
  quotas (units 4/5) are where global accounting arrives.

## The proof (harness; all children are real Blade packages)

New all-arch cases, existing 84 rows byte-identical (empty region
table everywhere else). Case names and exact transcripts are the
implementer's per the house pattern; the CLAIMS, each inverted
somewhere today:

1. **Lifecycle**: root creates + starts a child; the child (handle-
   less, deliberately faulting) dies with a fault report naming
   PROCESS 1 and a teardown that does NOT stop the machine; root
   resumes and exits clean. Root parks on a generous timer while
   the child runs, so the ordering is deterministic.
2. **Isolation — the unit's money proof**: the child stores to an
   address in ROOT'S region; the access faults (the per-process
   reload did its job), the child dies, ROOT SURVIVES and says so.
3. **BadImage is a status**: root hands a garbage region to
   `process_create`, gets `BadImage` back as a VALUE, reports it,
   exits clean. No fault, no machine stop.
4. **Double start faults**: a second `start()` is the caller's
   `BadState` fault — root dies, transcript shows the sharpened
   line.
5. **The drain**: root iterates `boot_handle_next` to exhaustion,
   asserts count and tags against its config, and exhaustion stays
   exhausted.

Child packages: a handle-less child cannot print or exit — its
`_start(a0)` receives `NO_HANDLE` and its observable behavior is
faulting (or spinning). Children link at their config-assigned
destination bases (generated or per-test link scripts — the
implementer's mechanics, recorded in the As-built).

## Docs owed

- spec §2 Process row, §8 (create/start BUILT, two-phase, the fault
  fork), §11 rows (second process; Memory's first slice), §12
  (loader-above-boot EXERCISED and the kernel still has ONE loader:
  the phases are shared, only the parameters and the failure
  vocabulary differ), §2.5 (the minimal kind as the v1 pool root,
  unit 4's runway).
- `kernel/core/preempt.saw:6-7`: the header's "unit 2's
  CreateProcess image copy next" sentence COMES TRUE — update it
  from future to present tense.
- sosabi docstrings for every new number; the ProcessOp "absent,
  with reasons" note retires its CreateProcess line.
- Tracker entry closed in place; this brief's As-built filled.

## Out of scope

give/tags/`start(boot_tag:)`/child-side drain (unit 3); handle
lifecycle (2.75); Memory pools, derivation, map(), IoMemory, device
children, flash-XIP create modes (unit 4+); quotas (unit 5); death
notifications (5.5); priorities (§7 bands); userspace loader split
(explicitly deferred by the Aug-28 ruling: "later if/when
necessary"); any sawlang/imgformat change.

## As built

Landed Aug 28 2026. `SAWLANG_ROOT=… make sos-test`: **94/94 across
riscv32 + arm64, 47 cases per architecture**, with the 84 pre-existing
rows byte-identical in name, verdict and order.

D-1 through D-7 landed as briefed. Two deviations from the brief's
LETTER, both argued below and neither touching a ruling: `sos.Memory`
is not `NoCopy` (D-4's parenthetical), and the loader MOVED in the
module order (D-1 did not anticipate that `process_create` being an op
puts the dispatch above it). One structural FINDING forced the
teardown to change altitude; it is the section after the numbers.

### The numbers as landed

| What | Value | Where |
|---|---|---|
| `ObjType.Memory` | 9 | `kernel/abi/src/lib.saw` |
| `ProcessOp.ProcessCreate` / `.Start` / `.BootHandleNext` | 7 / 8 / 9 | same |
| `ProcessRight.ProcessCreate` / `.Start` / `.BootHandles` | `1<<14` / `1<<15` / `1<<16` | same |
| `MemoryRight` | `Transfer = 1<<0`, `Manage = 1<<1` — the universal pair and nothing else | same |
| `SosStatus.BadImage` | **5** | same |
| `SosStatus.Drained` | **6** | same |
| `BootHandleKind.Memory` | 0 | same |
| boot-handle record | 3 machine words: tag, kind, handle | same |
| `MAX_PROCESSES` / `MAX_MEMORIES` / `MAX_GRANT_ROWS` | 2 / 8 / 9 | `kernel/core/limits.saw` |

The two new statuses continue past the documented 1-3 gap rather than
filling it, for the reason the gap exists: an old build's `BadHandle`
must not collide with a new build's status on the wire.

**`Drained` is a status and not a magic record**, as the brief leaned.
The alternative — a record with a sentinel kind — would have made the
loop's termination check a value comparison inside a successful call,
which is exactly the shape a caller forgets to write.

**Rights minted.** `root_process_rights()` gains `ProcessCreate` and
`BootHandles` and NOT `Start`: nobody starts root, and a child's
handle carries `Start | Wait | Manage` (`child_process_rights()`), so
the authority to run a child arrives WITH the child. Everything a
process may do to itself — make threads, make Events and Waiters, bind
a line, exit — is withheld from its creator.

**No `MemoryRight` is spent by anything.** `process_create`'s authority
is `ProcessRight.ProcessCreate` on the CALLER's own Process handle; the
two Memory handles are its ARGUMENTS. A handle that names nothing or
names a non-Memory is a `BadHandle` FAULT (the caller chose which
handles to pass); the BYTES behind a valid handle are what can be
`BadImage`. That line — authority is caller-checkable, data is not — is
what the whole status-versus-fault split rests on, and it is stated at
`memory_arg`.

### The region table, frozen

Eight-byte header then `count` rows, all little-endian (design 47's
discipline, the sosimg format's own):

```
  offset 0   magic     u32   0x4E47_5253   'S','R','G','N' read LE
  offset 4   version   u16   1
  offset 6   count     u8
  offset 7   reserved  u8    0
  offset 8   rows      count × { base: u64, len: u64 }
```

The header is EIGHT bytes so that every row lands 8-aligned, which is
what lets the kernel overlay `RegionRow` on the section instead of
assembling bytes; two `static_assert`s pin both sizes
(`kernel/core/process.saw`). Fields are 64-bit on both profiles for
sosimg v3's reason — one layout both kernels read — and a 32-bit kernel
REFUSES a row it cannot address rather than narrowing one.

**Emission mechanics: a GENERATED stub, not a committed one**
(`tools/sos_runner.py:_stitch_children`). Each child sosimg is
`.incbin`'d into its own `.childimg` section between local symbols, and
the table's blob rows name those symbols — so `ld.lld` fills in the
base when it places the section and nothing computes an address by
hand. A 32-bit target emits each 64-bit field as `.4byte sym` + a zero
high half (there is no 8-byte relocation to point at a symbol with); a
64-bit one emits `.8byte`. Generating rather than committing is what
the case shape asked for anyway — the number of children is a property
of the case — so it costs a file nobody keeps in step.

**Row order is the contract**: every blob row first, in the order the
case lists its children, then every destination row in the same order.
With one child that is tag 0 = image, tag 1 = destination, and root's
config (a comment and two `let`s) is the only thing that knows it.

**Sections and symbols.** ONE new fixed pair,
`_region_table_start`/`_region_table_end`, bounding a new `.regions`
output section in both `virt.ld`s, reached through new
`sos_region_table_start`/`_end` accessors in both `sink.c`s and
`region_table_start()`/`region_table_end()` in both HALs (both
`kernel/ABI.md`s updated). `.childimg` gets an output section and NO
symbol pair, which is the point: the blob bases are linker-resolved
into the table. Neither section is page-aligned, deliberately — a child
image is read by the KERNEL and is never granted to a process, so no
grant-window constraint reaches it.

A missing or empty section is `start == end`, which is ZERO REGIONS and
not an error. That is the branch every pre-existing case takes, and it
is why their transcripts are untouched.

### Canonical child bases

Both profiles: **one 256 KiB region immediately above root's top**, the
same size root gets, with the kernel's 16 KiB stack grant at its top.

| Profile | Child region | Committed link script |
|---|---|---|
| riscv32 | `0x8024_0000 .. 0x8028_0000` | `hal/riscv32/user/child.ld` (`ORIGIN 0x80240000`, `LENGTH 240K`) |
| arm64 | `0x4024_0000 .. 0x4028_0000` | `hal/arm64/user/child.ld` (`ORIGIN 0x40240000`, `LENGTH 240K`) |

**Committed per-arch scripts, not generated ones.** The brief left the
mechanics open; committed won because a link script is exactly the kind
of thing the tree already keeps per profile beside `root.ld`, and the
per-profile DIFFERENCE is real rather than a number substitution —
Profile B page-aligns `.text` and `.data` because its protection
granularity is the page, and Profile A does not. A generator would have
had to encode that difference anyway, in Python, away from the two
files that state it.

**On arm64 the base is CONSTRAINED, not merely tidy**: EL0 can only be
granted pages inside the HAL's grant window (the first 4 MiB of RAM),
so a child's destination must sit between root's top and
`0x4040_0000`. There is room for seven more regions; an eighth would
have to widen `GRANT_PAGES`, which is the honest place for that
decision. The child scripts say so.

The runner carries `child_region_base` per arch and one shared
`CHILD_REGION_LEN`; `child-poke` finds root's region by ROUNDING ITS
OWN ADDRESS DOWN to that length, which is what keeps the child's source
arch-free.

### The reload, per arch

`process.load_domain(p)` is the ONE place a domain is installed:
`hal.prot_reset()`, replay row `i` at index `i`, `hal.prot_commit()`.
Two callers — the boot path once, and `sched.run_thread` when the
incoming thread's process differs from `threads.LAST_PROT_PROCESS`.

- **riscv32**: `prot_reset` zeroes both config words and eight address
  registers, then each row is a TOR pair (or a NAPOT device entry), then
  one `pmpcfg` write publishes. About nineteen control-register writes
  and no translation-buffer work.
- **arm64**: `prot_reset` walks the 1024-entry RAM level-3 table and
  the 512-entry device level-3 table back to EL0-no-access, each row
  re-grants its pages, and `sos_prot_commit` is the existing full
  barrier + `tlbi vmalle1`. Order of 1.6k stores.

**The full window reset was taken over a targeted clear of the outgoing
rows**, which D-5 permits as an improvement. Reason: `load_domain` is
the same three steps the boot path already performed inline, so ONE
spelling installs a domain — a targeted clear would need the outgoing
process's rows as a second input and would make boot and switch two
different operations. At M3 switch rates the cost is invisible (the
suite's timings did not move), and the improvement stays available with
nothing above `load_domain` changing.

**`LAST_PROT_PROCESS` lives in `kcore.threads`**, beside `CURRENT_THREAD`
and the ready queue, and not beside the reload — because `process`
(below the scheduler) has to write it at boot, when the loader installs
root's domain directly. It is INVALIDATED by `clear_domain` at teardown,
which is what makes slot reuse safe without handle generations.

**The two early returns in `pick_next` keep the same thread**, so the
domain cannot have changed at either — which is why the reload is at the
tail, in `run_thread`, and the site says so.

### Ready-queue removal

`threads.ready_remove_process(p)`: a singly-linked walk with a trailing
`prev` in the list's own slot-plus-one encoding, unlinking every node
whose `process == p` and following `READY_TAIL` when the removed node
was the last. It runs BEFORE the thread slots are freed, because the
walk reads each thread's `process` field to decide.

It replaced `READY_HEAD = READY_TAIL = 0` for BOTH arms rather than only
the child arm: with one process the two are the same act (every runnable
thread belonged to the process that was ending), so one spelling covers
both and the wholesale version has no remaining caller to drift from.

### The loader phase split

`kernel/core/loader.saw`, and it is a split rather than a copy:

- `validate_image(img_base, img_len, dest: &LoadRegion, allow_device) -> ImageFault?`
  — phases A/B, and **not one effect happens inside it**.
- `place_image(p, img_base, dest: &LoadRegion) -> UInt` — phases C/D:
  `long_copy`/`long_zero` per segment, `record_grant` per row plus the
  stack row, returns the writable window's floor. It commits NOTHING to
  hardware.
- `image_entry` / `image_seg_count` / `image_prio_map` — header reads
  after validation.
- `LoadRegion { base, top, stack_len }` is the region parameter;
  `root_region()` builds root's from the HAL's constants.
- `ImageFault { reason: String, detail: UInt }` is the failure
  vocabulary, raised as `fatal_image(bad.reason, bad.detail)` at the
  boot door and as `SosStatus.BadImage` at the create door. The SENTENCE
  is the same in both mouths, which is what makes it one vocabulary.

**`allow_device: Bool` is the only per-door behaviour difference**, and
it is D-7's refusal: a child image declaring an MMIO window is
`BadImage`. The reason is written at the site — the image-declares /
board-authorizes grant is a boot-time placeholder about the one process
the KERNEL loads, and generalizing a placeholder outlives it.

**One check is new to both doors**: the entry must be inside an
EXECUTABLE segment's load range. It is ordered LAST so every earlier
diagnostic keeps the wording it has always had, and the existing
hand-assembled fixtures pass it unchanged.

**DEVIATION: the loader moved down the module order**, from last to
between `preempt` and `sched`. It has to: `process_create` is an op, so
`kcore.dispatch` needs the phases, and `dispatch` sits above `sched`.
Nothing in the loader reaches upward either way (it calls the HAL, the
movers and the process slot, all below), so the move is free. `lib.saw`'s
module-order list records it and why.

### THE FINDING: the teardown cannot stay in `kcore.process`

D-6 asked for `end_process` to grow the fork in place and end in
`pick_next` + `resume_frame`. **That is not expressible, and the
obstacle is the same one design 1 met**:

- A child's death RESCHEDULES, and choosing what runs next can IDLE.
- `irq.idle_until_runnable` DELIVERS: `deliver_line` →
  `on_timer_interrupt` → `expire_timers` → `fire_timer` →
  `wake.notify_ready` → `wake_one_waiter` → `deliver_attachment` →
  `process.copy_out`.
- So a reschedule written in `process` is `process` importing `irq` from
  above it — the import cycle DF-232e diagnoses. The call graph
  genuinely has the cycle; unlike design 1's, no runtime flag makes it
  safe, because the recursion is real.

**Resolution, and what it preserves.** `end_process`, `fault_process`
and `fault_trap` moved to `kcore.sched`, which is the altitude that can
also say what runs next. Nothing is duplicated and every piece stays
single-sited: the process slot and its grant record stay in `process`,
every slab stays where it is, and the one function that frees them sits
with the one function that picks the next thread. The module's identity
survives the move because a process's death IS a scheduling event now —
`sched.saw`'s header states the whole argument, and `lib.saw`'s
module-order list points at it.

**What it costs, and this is the visible half.** The copy doors sit
BELOW the scheduler, so they can no longer terminate: `copy_out_check`,
`copy_out` and `copy_in` now answer `FaultReason?` — `None` when the
buffer is good — and their callers fault. Three of the four callers are
in `dispatch` (above the switch point) and fault exactly as before. The
fourth is `wake.deliver_wait_record`, which cannot fault at all: its
buffer was validated at the PARK and a process's writable window is
fixed for its life, so a refusal there is a KERNEL invariant broken
rather than a process error, and it is a `fatal` stop. No check was
removed; only where the kill is spelled moved.

**Every `fault_process(` call site in `dispatch` is textually
unchanged** — the import moved, the name did not — which is why a
diff of this unit shows the ~45 fault sites untouched.

### DEVIATION: `sos.Memory` is a plain wrapper, not `NoCopy`

D-4's parenthetical asked for `NoCopy`. Two reasons it is not, both
about keeping ONE rule across nine handle types rather than nine rules:

1. **The owning tier is unit 2.75's whole subject.** `sosabi`'s
   `SystemHandle` docstring already says the `NoCopy` wrapper arrives
   when handles become CLOSEABLE — a move-only handle whose drop does
   nothing is a discipline with no enforcement behind it. Making one of
   nine types move-only a unit early would be the one handle that reads
   differently, for no property gained.
2. **It is not expressible with the ruled record shape.** D-3 rules that
   `boot_handle_next` hands back a `{tag, kind, handle}` VALUE. A
   move-only field inside a returned struct cannot be taken out again —
   `move h.memory` is the no-partial-moves error and `h.memory` is the
   NoCopy read error — so the record would need a consuming accessor
   that Saw cannot write against `self` either. The record shape is a
   ruling; the copy tier was a parenthetical.

When 2.75 makes handles closeable, all nine become `NoCopy` together and
the record becomes a consuming accessor. `Memory`'s docstring carries
this argument.

### The proof, and the transcripts

Five all-arch cases, seven new Blade packages (five root servers, two
CHILDREN — the first packages in the tree that are not root servers).
Case names as landed: `process_lifecycle`, `process_isolation`,
`process_badimage`, `process_doublestart`, `process_bootdrain`.

```
SOS: boot regions=0x00000002
SOS: console handover
SOS lifecycle: boot regions tag0=0 tag1=1
SOS lifecycle: created
SOS lifecycle: started
SOS: process fault: bad handle process=0x00000001
SOS: process teardown handles=0x00000000 threads=0x00000001 ... process=0x00000001
SOS lifecycle: root survived child status=131073
SOS lifecycle: done
```

```
SOS isolation: started
SOS: fault store-access-fault cause=0x00000007 epc=0x802403ce tval=0x8023fff0
SOS: process teardown handles=0x00000000 threads=0x00000001 ... process=0x00000001
SOS isolation: root survived child status=131072
```

`tval` IS the money proof: `0x8023fff0` is sixteen bytes below root's
region top, inside root's live stack, and the store was REFUSED because
the switch into the child reloaded the domain. A kernel with a broken
reload fails by that line being ABSENT — the child's store would land
and it would spin — which is the failure mode worth engineering for.
The arm64 twin reads `tval=0x000000004023fff0`.

`child status=131073` is `Faulted` (2) in the high half and `BadHandle`
(1) in the low, read through the handle root STILL HOLDS after the
child is gone (the teardown closes the dead process's table, not its
creator's). The isolation child's `131072` is the same kind with code
zero, because a HARDWARE fault carries no `FaultReason` — which is what
says the two children died in two different ways.

**Two report lines gained a trailing ` process=<hex>` field**, appended
rather than led with, so every transcript written before there were two
processes reads the same up to that point and no existing expectation
sees it. "Root survived" is exactly the claim that the teardown line
says 1 and the run continues.

`process_doublestart` is the one case that FAILS the machine
(`expect_status = 5`, the kernel's process-fault code), and `process=0`
in both report lines is what distinguishes its transcript from the
lifecycle case's identical pair.

### ACCEPTANCE, and exactly what was diffed

The suite was run TWICE under the machine-wide lock: once at the merge
base (`cba373e`, unmodified) and once with the change applied.

- **The 84 pre-existing case rows are byte-identical** in name, verdict
  and ORDER on both architectures, with the `[i/N]` denominator
  stripped. The only row-level difference is the five new rows per
  architecture, appended at the end.
- **The only other differences are sosimg build-info lines and the
  total.** Seven new lines for the new packages — and every EXISTING
  package's image grew, by 100 to 500 bytes. That is the `sos` module
  gaining a public API: `sos_process_process_create`,
  `sos_process_start` and `sos_process_boot_handle_next` are `@export`ed
  C-ABI seams, so `--gc-sections` keeps them in every image that links
  the module. It is the same reason every earlier unit's exports are
  there, and it is the first unit since the split to add to that
  surface.

### Two arms the cases do not reach, stated rather than glossed

`resume_after_death` has three ways out and the suite exercises one of them —
the IDLE one, which is the shape the lifecycle and isolation cases make (root is
parked on its timer when the child dies, so nothing is runnable and the clock is
what brings it back). Not reached:

- **Something already runnable.** It is `pick_next`'s own tail, shared as
  `run_thread` precisely so the two cannot drift, and `ready_pop` is exercised
  by every multi-thread case above.
- **Nothing runnable and no wake source**, which ends root and stops the
  machine. Reaching it needs a child dying while root is blocked on something
  no thread can deliver, which is a case about the deadlock rule rather than
  about this unit; the rule itself is covered by `wait_deadlock` and
  `timer_deadlock` through `pick_next`'s arm.

The ready-queue REMOVAL's matching branch is covered, and not by the new cases:
`thread_preempt` and its siblings end a process with several of its own threads
still queued, so the unlink, the head case and the tail-follow all run there —
byte-identically, which is what the unchanged rows say.

### Nothing left open

No compiler defect was met and none was worked around. One parse
limitation was hit and is ordinary Saw rather than a defect (DF-172d: a
binary expression does not wrap unless brackets enclose it, so an
assignment split after `=` is a parse error) — the fix was a `let`.

The arch-free scan (`_check_arch_free`) caught nine architecture names
in this unit's new COMMENTS and every one was rewritten in
profile-neutral language; the scan is doing exactly what design 162 unit
1 built it for, and the comments read better for it.

`MAX_PROCESSES = 2` is the ceiling, so a third `process_create`
answers `NoResource`. Raising it is still the one edit the constant
claims to be — this unit is the exercise that proves it — but two
v1 statements are worth carrying forward. **A DEAD SLOT IS NEVER SPENT
AGAIN**: the teardown leaves it `Gone` holding its §8 status word,
because the creator's handle outlives the child and reading how it died
is the whole of this unit's supervision story. So the constant bounds
how many processes a run may CREATE rather than how many may exist at
once, and reclaiming one waits on unit 2.75's handle close (which is
also what makes `LAST_PROT_PROCESS`'s invalidation load-bearing rather
than merely correct — `clear_domain` says so at the site). **And a
child holds no handle onto itself**: its table is empty, so
`ProcessSelf` has nothing to answer with, which is exactly the
"legal-but-doomed" sandboxed process the ruling asked for and what unit
3's `give` changes.
