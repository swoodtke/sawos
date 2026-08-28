# SawOS design 2 — CreateProcess: the second address space (M3 unit 2)

Status: AUTHORED Aug 28 2026 (lead), REVISED same day. Implements
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

(To be completed by the implementing agent: numbers as landed, the
region-table format as frozen, canonical child bases per arch, the
reload implementation per arch, ready-queue removal mechanics, the
loader phase-split shape, case names + counts, findings.)
