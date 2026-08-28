# SawOS design 2 — CreateProcess: the second address space (M3 unit 2)

Status: AUTHORED Aug 28 2026 (lead), implementing sawlang#232 unit 2
(the two-phase lifecycle ruled Aug 16; agenda item 2's hybrid ruled
Aug 16) as AMENDED BY TWO USER RULINGS of Aug 28:

1. **THE KERNEL COPIES NO IMAGE BYTES.** "This might be where we push
   back against the kernel doing the memory copying and just map in
   the memory provided by root." The hybrid's authority structure
   stands — the kernel is the ONLY runtime sosimg parser, root never
   interprets image bytes — but `process_create` validates, records
   and PROTECTS memory whose bytes are already in place. The
   CreateProcess copy loop that fired design 178's D2 tripwire is
   never born.
2. **THE STACK IS MEMORY ROOT PROVIDES** (ruled today over the three
   options): `process_create(image: Memory, stack: Memory)`. This
   amends the Aug-16 letter ("the sosimg declares both") — under
   zero-copy the image declares the ENTRY and the stack has no bytes
   to declare. The ruling's spirit survives exactly: root supplies no
   numbers, only capabilities it was handed at boot. sosimg stays v3;
   no sawlang-side change, no pin bump. (Unit 4 re-sources the stack
   Memory from the RAM pool with no signature change.)

Two pieces of later units arrive EARLY, subtracted from their homes
rather than re-added there: `boot_handle_next` (unit 3's iterator —
root needs it to receive the boot-minted Memory handles) and a
MINIMAL Memory object kind (unit 4's §2.5 object, first slice: sealed
boot regions, no pools, no map, no derivation).

## D-1: Zero-copy is LINK-IN-PLACE, and the stitcher flattens

The two zero-copy shapes were weighed against the census:

- **Root copies opaquely** — refused: root writing a child's RAM
  needs write grants over foreign ranges, which is unit 4's map()
  machinery dragged forward whole.
- **Link-in-place (execute in place)** — taken: the build places each
  child's bytes AT their load addresses inside the boot image; the
  kernel grants over bytes already where they belong. The precedent
  already runs on both arches: `tests/umode.saw:27-38` grants and
  enters the kernel-image payload with no copy anywhere.

Mechanics, all build-time and sawos-owned (`tools/sos_runner.py`):
the child is a REAL Blade package built to a sosimg exactly as root
is; the STITCHER then FLATTENS it — rewrites the blob so every
segment's file bytes sit at `load_addr - region_base`, `.bss` tails
become explicit zeros (`file_len == mem_len`), header preserved — and
emits it as a section placed at the child's config-assigned base
(own output-section entry per the linker scripts' own note,
riscv virt.ld:8-9). The flattener is a small Python reader of
sosimg v3 (24+24-byte fixed records); build tooling parsing a format
is fine — ROOT still never does, and the KERNEL still re-validates
everything at `process_create`. Blob growth from zero-padding is
agenda item 2's lean (ii) accepted waste, restated: static systems,
small images. Restart-dirty `.data` is accepted v1 and noted (static
children start once; restart is M4's dynamic-loading problem).

Placement constraints per arch (the census's hard facts): arm64
child image AND stack must lie inside the 4 MiB grant window
(`RAM_BASE .. +GRANT_PAGES*4K` = 0x4000_0000–0x4040_0000, above
root's top 0x4024_0000); riscv32 anywhere in RAM above root's region
(PROT_GRAIN=4). The implementing agent picks canonical v1 bases and
records them where the runner assigns them.

## D-2: The region table, and the minimal Memory kind

The stitcher records WHERE it put things in a REGION TABLE — offsets
and lengths, nothing more (the ruled hybrid) — as its own section
with ONE new fixed symbol pair (`_region_table_start/_end`) crossing
to Saw through both sink.c's, the existing DF-172a pattern. Format
(sawos-owned, fixed-width LE): magic, version, count, then
count × {base: u64, len: u64}. A missing/empty table is ZERO regions
— every existing case boots byte-identically, which is what keeps
the 84 oracle rows unchanged.

At boot the kernel parses the table and MINTS ONE Memory PER REGION:
`ObjType.Memory = 9`, a sealed {base, len} — no ops in this unit,
rights `Transfer | Manage` minted (Transfer is unit 3's give
consuming it; nothing here reads either). A new slab
(`MAX_MEMORIES = 8`) with the usual per-slot `process` field and
teardown arm. §2.5 is not contradicted, it is STARTED: these are the
"pool roots given to the root server at boot" in their v1 static
form; unit 4 adds pools, derivation, map(), and the attribute
vocabulary.

## D-3: Delivery is `boot_handle_next`, pulled forward

Root receives the minted Memory handles through unit 3's ruled
iterator, arriving early because root is its first consumer:
`ProcessOp.BootHandleNext` on root's own Process handle — one
`{tag, kind, handle}` record per call through the EXISTING copy-out
funnel (3 words, the wait-record shape), CONSUMED on delivery,
exhaustion a status that stays exhausted (a loop's termination
check, never a fault). Tags here are REGION ORDINALS (table row
index); root correlates ordinals to its config. The boot set still
enters user mode ONE handle wide (§12's promise) — the register
carries System, the iterator delivers the rest. Unit 3 reuses this
op unchanged for the child's drain; the record layout is agenda
item 5, settled here (three machine words, tag/kind/handle).
Status vocabulary for "drained" is the implementer's to pick and
document in sosabi (the one-place rule); lean: a new `SosStatus`
case, not a magic record.

## D-4: The surface — ops, rights, and the two-phase lifecycle

Per the Aug-16 ruling, built as ruled (inert until started), minus
the copy:

- **`ProcessOp.ProcessCreate = 7`** on ROOT'S OWN Process handle
  (creation authority is rights bits on Process — §12), gated on NEW
  `ProcessRight.ProcessCreate = 1 << 14`, granted in
  `root_process_rights()`. Args: image Memory handle, stack Memory
  handle. VALIDATES EVERYTHING (D-7) with no side effect on failure;
  on success: allocates the process slot (`NoResource` when full),
  records entry (from the header) + stack top (stack region top) +
  the protection record (D-5), and MINTS the child's Process handle
  to the CALLER with `Start | Wait | Manage` (no Transfer minted
  here; unit 3 revisits). Returns the handle. A malformed image is a
  **`BadImage` STATUS, not a fault** (ruled lean (i): image bytes
  are DATA on the `from(raw:)` precedent). New `SosStatus` case,
  value the implementer picks beside the documented 1-3 gap.
- **`ProcessOp.Start = 8`** — receiver-verb (the receiver IS the
  child), gated on NEW `ProcessRight.Start = 1 << 15`. Mints the
  first thread INTERNALLY (no handle — nobody holds the child's
  thread in this unit; unit 3 decides what the child receives),
  `frame_init(entry, stack_top, a0 = NO_HANDLE)` — the no-boot-tag
  form: this unit's every child is the ruled "legal-but-doomed"
  sandboxed compute process, started handle-less — and
  `ready_push`es it. Root KEEPS RUNNING; the child runs at the
  scheduler's pleasure. A SECOND start is a `BadState` FAULT
  (ruled). `boot_tag:` arrives with give in unit 3.
- **`ProcessOp.BootHandleNext = 9`**, gated on NEW
  `ProcessRight.BootHandles = 1 << 16`, granted to root; what a
  child gets is unit 3's question.
- sysapi: typed `Memory` wrapper (opaque, NoCopy, the Process
  pattern), `Process.process_create(image:stack:)`, `.start()`,
  `.boot_handle_next()`, with the matching `sos_process_*` C
  exports. Naming per the Aug-17 rider throughout.

## D-5: Protection is RELOADED AT THE SWITCH — both arches, forced

The census closes this: riscv32 has FOUR TOR regions and a 3-segment
root spends all four — two processes cannot share the live set. And
arm64's grant window is ONE shared EL0 permission map, so grants
left installed across a switch are the other process's to touch —
same conclusion. So:

- `ProcessSlot` grows the GRANT RECORD: up to `MAX_ROOT_SEGMENTS + 1`
  rows of {base, top, perms, device}, written by whoever grants —
  the loader records root's rows as it places (including the device
  window row for driver images); `process_create` records the
  child's (image segments + stack).
- `pick_next` reloads AT THE MARKED POINT (sched.saw:49-52) when the
  incoming thread's process differs from the last loaded domain
  (a `LAST_PROT_PROCESS` beside the scheduler state):
  `prot_reset` + replay rows + `prot_commit`. Cost, measured
  honestly: ~19 CSR writes on riscv32 (no TLB to touch); on arm64 a
  window reset + regrant walk + the full `tlbi vmalle1` commit —
  ~1.6k stores v1, accepted at M3's switch rates and RECORDED (a
  targeted clear of the outgoing rows is a permitted implementation
  improvement; ASIDs/per-process tables are SMP-era). Same-process
  switches — every switch every existing case makes — reload
  NOTHING, which is what keeps the oracle safe.
- Harness kernels with no Thread objects hit `pick_next`'s
  `NO_THREAD` early return and are untouched; `tests/umode.saw`
  keeps driving `prot_*` directly.

## D-6: N processes — lifecycle, teardown, deadlock

`MAX_PROCESSES = 2` (limits.saw's "raising this number is the only
edit" claim gets its first exercise; quotas at unit 5 revisit).
`HANDLES` scales by the existing arithmetic. The two hardcoded
one-process sites the census found (`start_process`'s `let p = 0`,
`calling_process`'s fallback `0`) stay correct as written — slot 0
IS root — and gain the comment saying so.

`end_process` grows the fork its own docstring promised ("with M3's
several it becomes a return to the scheduler, and nothing above this
line changes"):

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
  machine.
- **Deadlock cascades**: `has_external_wake_source` stays GLOBAL and
  conservative; nothing-runnable-no-source faults the CURRENT
  process exactly as today. With two processes that kills the
  current one, reschedules, and — if the survivor is also stuck —
  reports again and stops when root dies. Each step is today's rule
  applied to N; no new mechanism, one harness case shows the
  cascade. (Death notifications, unit 5.5, are what turn a child's
  death into something root can WAIT on; nothing here pre-builds
  that.)
- `fault_trap`/`fault_process` route through the same fork.
- **The copy doors**: a child's writable window (`rw_base/rw_top`)
  is its STACK REGION v1 — the one certainly-writable range — and
  this unit's children make no syscalls (no handles), so the doors
  never open toward one. The one-window model generalizes to the
  grant record when children get syscalls (unit 3); flagged, not
  built.

## D-7: What `process_create` validates (all before any effect)

Reusing the loader's validators (`check_segment`'s checks and
imgformat's predicates) against the IMAGE REGION instead of the root
region — the loader's own phases A/B split cleanly; the placement
phase C is what this unit deletes from the child path:

- header: magic, version, arch vs `hal.arch_tag()`, `seg_count > 0`,
  `seg_count + 1 <= PMP_REGIONS` on the region-budget seam
  (segments plus stack must fit the reload set; the hal budget
  constant crosses the seam for both arches).
- per segment: wellformed, sane perms, **`Device` REFUSED v1**
  (`BadImage` — a child with MMIO waits for unit 4's IoMemory; the
  echo-driver money shot rides that), alignment to `PROT_GRAIN`,
  segment within the image region, and THE IN-PLACE CONDITION:
  `load_addr == image.base + file_off` and `file_len == mem_len` —
  the flattener's output is the only accepted shape, and the check
  is one sentence per field.
- entry inside an executable segment; stack region disjoint from the
  image region; stack length nonzero.
- Same Memory passed twice / overlapping regions across children:
  NOT globally policed v1 — regions are capabilities, the table is
  build-emitted disjoint, and root double-spending what it holds
  corrupts only what it holds. Noted in the brief and at the site;
  quotas + real pools (units 4/5) are where global accounting
  arrives.

## The proof (harness; all children are real Blade packages)

New all-arch cases, existing 84 rows byte-identical (empty region
table everywhere else). Case names and exact transcripts are the
implementer's per the house pattern; the CLAIMS, each inverted
somewhere today:

1. **Lifecycle**: root creates + starts a child; the child (handle-
   less, deliberately faulting) dies with a fault report naming
   PROCESS 1 and a teardown that does NOT stop the machine; root
   resumes and exits clean. Root parks on a generous timer while the
   child runs, so the ordering is deterministic.
2. **Isolation — the unit's money proof**: the child stores to an
   address in ROOT'S region; the access faults (per-process reload
   did its job), the child dies, ROOT SURVIVES and says so. The
   same store from root's own code succeeds today, which is what
   makes this the claim.
3. **BadImage is a status**: root hands a garbage region to
   `process_create`, gets `BadImage` back as a VALUE, reports it,
   exits clean. No fault, no machine stop.
4. **Double start faults**: second `start()` is the caller's
   `BadState` fault — root dies, transcript shows the sharpened
   line.
5. **The drain**: root iterates `boot_handle_next` to exhaustion,
   asserts count and tags against its config, and exhaustion stays
   exhausted.

## Docs owed

- spec §2 Process row, §8 (create/start BUILT, two-phase, the fault
  fork), §11 rows (second process; Memory's first slice), §12
  (loader-above-boot EXERCISED — the kernel still has no second
  code path: `process_create` reuses the loader's validate phase and
  deletes the place phase), §2.5 (the minimal kind as the v1 pool
  root, unit 4's runway).
- `kernel/core/preempt.saw:6-7`: the stale "unit 2's CreateProcess
  image copy next" sentence — the copy is never born; rewrite to
  name pipes as the future consumer.
- sosabi docstrings for every new number; the ProcessOp "absent,
  with reasons" note retires its CreateProcess line.
- Tracker entry closed in place; this brief's As-built filled.

## Out of scope

give/tags/`start(boot_tag:)`/child-side drain (unit 3); handle
lifecycle (2.75); Memory pools, derivation, map(), IoMemory,
device children (unit 4); quotas (unit 5); death notifications
(5.5); priorities (§7 bands); any sawlang/imgformat change.

## As built

(To be completed by the implementing agent: numbers as landed, the
region-table format as frozen, canonical child bases per arch, the
reload implementation per arch as measured, ready-queue removal
mechanics, case names + counts, findings.)
