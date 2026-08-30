# SawOS design 9 — the driver child (M3 unit 6)

Status: AUTHORED Aug 30 2026 (lead; drafted while unit 5.5 was in
flight, committed at its integration). Implements sawlang#232's finale: **the child echo driver from config,
both arches, plus the shared-memory demonstration** — and folds in
the exec-gating scope the user ruled Aug 30: mapped-executable is a
HANDLE right, not a free choice.

## The ruled surface

- The last ladder unit. Everything it spends already exists: give
  (unit 3), boot drain (`ProcessRight.BootHandles`), IoMemory +
  Carve + Map (unit 4), the migrated uart-echo drivers
  (`tests/uart-echo-ns16550`/`-pl011` — their own header promises
  this unit), the runner's `"device": True` / `"stdin"` /
  `ECHO_INPUT` / `SERIAL_TYPE_DELAY_S` machinery.
- **Exec gating (ruled Aug 30)**: X stays a property of the MAPPING
  (design 6's decision is untouched), but which access bits a handle
  may *request* becomes a right on that handle. Capability flow, not
  identity: "only root maps executable" because root never grants
  the bit, not because the kernel asks who is calling.

## D-1: `MemoryRight.MapExecute` — executable is an authority

- **`MemoryRight.MapExecute = 1 << 10`** (kind-specific tier, the
  usual static_assert). `MemoryOp.Map` refuses
  `MapAccess.Execute` unless the Memory handle carries it — the
  same refusal every unspent right answers, checked beside the
  existing `MemoryRight.Map` gate.
- **`memory_rights()` grows the bit** — boot pool roots carry it,
  necessarily: attenuation is monotonic, a bit not minted at boot
  could never appear later (the function's own one-set-two-minters
  doctrine). Root withholds it at mint/give; derived handles
  (Split) inherit the parent mask as they already do.
- **Companion invariant: `Write|Execute` in ONE mapping is refused**,
  caller-checkable `BadArg`, landing beside the W-without-R check in
  `dispatch.saw`'s access validation. It costs nothing expressible:
  double-map is already sanctioned (design 6 agenda-7), so the
  JIT-shaped pattern — RW row here, RX row there — remains available
  while every individual protection row is W^X.
- **IoMemory is untouched**: execute-on-device is already refused
  vocabulary, RW fixed. No `IoMemoryRight` change.
- **Stated boundary**: the gate governs the dynamic map op. A child
  image's own X segments arrive through `process_create`'s loader
  (SegFlag from the image), which is `SystemRight.ProcessCreate`
  authority — a right driver children are not granted.
- **Stated honestly** (the aliasing stance stands): this is per-
  handle, not "this region is never X anywhere" — a second handle
  still carrying the bit can map the same bytes X. Design 6 accepted
  no aliasing bookkeeping; a region-level immutable flag would be a
  second mechanism doing overlapping work. REJECTED.

## D-2: the driver child — the money shot

- Two new child packages named for the chip (the uart-echo doctrine:
  a driver IS its device): the SAME driver cycle 0–6 as the migrated
  roots — ideally near-byte-identical program text — but built as a
  CHILD image, launched by a new root.
- The root's flow, per arch case: boot set carries the board's UART
  window (device region-table row) + RAM pool. Root creates the
  child, then GIVES it:
    - the UART **IoMemory** (Transfer spent — THE capability story:
      the window changes hands; the child maps it into ITSELF, as
      the handoff's lean ruled — give-of-IoMemory demonstrated, the
      map done by the child);
    - a masked **System** handle: `Debug|ProcessSelf|ClockGet` only
      (fail-closed; no ProcessCreate, no Shutdown — the child can
      print and know itself, nothing more);
  and the child's Process-self path arrives via boot drain. The
  child's Process handle carries `InterruptBind` + `Map` (bind its
  line, install its own window) — per-handle, recorded at the site.
- Child binds the UART IRQ, enables the device through its own
  window, parks, echoes — the harness's stdin bytes come back after
  the handover marker, which can only happen if give, drain, map,
  bind, and wake all worked across a process boundary.

## D-3: the shared-memory demonstration

- Root SPLITS the RAM pool, maps one region into ITSELF (RW) and
  into the CHILD (RW) — the first cross-process double map; root
  installs the child's row (`ProcessRight.Map` on the child spent),
  so the child holds NO Memory handle at all: access without
  possession of the region object. Contrast with D-2's child-maps-
  itself, deliberately — the two installation directions, one unit.
- Transcript proves shared bytes: root writes a pattern, child reads
  it back and prints; child writes, root reads. No copy exists that
  could fake it on a no-translation machine.
- The child's absent `MapExecute` is part of this demo's story: the
  memory root gives/mints toward the child (if any) omits the bit.

## The proof (harness)

1. **`child-echo-ns16550` / `child-echo-pl011`** — the money shot,
   `"device": True` + `"stdin": ECHO_INPUT` cases beside the
   root-as-driver twins (which stay, unmoved).
2. **`share-double-map`** — D-3's transcript, its own case so the
   echo transcript stays pure.
3. **`map-exec-gated`** — three arms: mint a Memory handle WITHOUT
   `MapExecute`, map-X refused (right unspent); map `W|X` in one
   row refused (`BadArg`, beside W-without-R); root maps X through
   its full pool handle, succeeds (nothing need execute from it —
   the row installing is the proof).

Transcript expectation: existing rows UNMOVED unless an existing
case prints a Memory rights word — `memory_rights()` grows a bit, so
any such row moves authorized-with-cause. Account it explicitly.

## Docs owed

spec §2.5 (MapAccess amendment: X spends `MemoryRight.MapExecute`;
the W|X refusal joins W-without-R; the boundary sentence), §9/§12
(the driver story gains its child chapter — the uart-echo header's
promise flips to BUILT), §11 rows, `memory_rights()` docstring,
tracker closed in place, As-built (deviations argued, findings,
SL-10+ if met).

## Out of scope

Attenuate-at-give keep-mask (backlog, M4 seed — composes with
MapExecute but is not needed by it); SegFlag.Device retirement
(sawlang-side); region-level no-exec flag (rejected above); kill;
pipes / anything M4; unit 7's docs sweep.

## As built

(Implementer: flows as landed, the two installation directions as
landed, exec-gate refusal wording, findings.)
