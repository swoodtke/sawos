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

Status: BUILT Aug 30 2026. Gate: 158 passed across both profiles
(79 cases each). Kernel/ABI delta is small — one enum case, one default
set, two comparisons in `dispatch.saw` — and the rest is surface
(`give(iomemory:)`, `Memory.mint`, `MemoryRight` re-exported) plus seven
packages and five harness cases.

### The gate, accounted

Whole transcripts were captured at the merge base (2839b10, 150/150) and
at this branch's tip, and every one of the 150 baseline rows is bucketed:

| bucket | rows | which, and why |
| --- | --- | --- |
| byte-identical | 146 | |
| address-only | 1 | `arm64/trap_fault` — the kernel's own trapping PC, `elr=0x…8f68` -> `0x…8f84`. `kcore.dispatch` grew by the W^X check and the exec gate, so the kernel image moved 0x1c. STABLE across two runs of this branch, and the two things the case asserts (`ec=` and a non-zero exit) are untouched |
| documented-nondeterministic | 3 | `riscv32/thread_preempt` (the tick's interrupted-ROOT PC and where the tick's line cuts into root's — the two runs of THIS branch disagree with each other, which is the proof it is timing and not the change); `arm64/thread_preempt` (the interleaving string, which the case asserts as DIRECTION CHANGES rather than as a sequence, by its own recorded reasoning); `riscv32/timer_interval` (`tick one fires=2` -> `1`, the coalescing count the case deliberately does NOT assert — its comment says 1, 2 or 3 are all correct and that the number has moved before for exactly this reason) |
| authorized-with-cause | 0 | **THE BUCKET THE BRIEF ANTICIPATED IS EMPTY.** `memory_rights()` grew a bit and no transcript noticed, because no program in the tree prints a Memory rights word — checked by grep over `tests/*/src` and `root/src`, not assumed. A rights word has never been an observable |
| new | 8 | four per profile: `child_echo_*`, `share_double_map`, `map_exec_gated`, `map_wx_refused` |

146 + 1 + 3 = 150 accounted, + 8 new = 158.

### D-1 — `MemoryRight.MapExecute`, as landed

`MemoryRight.MapExecute = 1 << 10`, with the kind-specific
`>= (1 << 8)` assert its four siblings carry. `memory_rights()` grew the
bit (its docstring now carries the monotonicity argument for WHY a
permissive default is forced). `MemoryOp.Map`'s arm spends it, and the
one placement decision worth recording is that the check does NOT sit
beside `obj.allows(MemoryRight.Map)` where the brief put it: it reads
the ACCESS WORD, and the access word does not exist until `map_access`
has refused every shape that is not a grant. So the order is every
word-shaped `BadArg` first, then the authority that word turns out to
require — written out at the site.

The W|X refusal landed exactly as ruled, beside write-without-read in
`map_access`, with the same `BadArg` fault and the same
caller-checkable argument. `IoMemory` is untouched: its `map` takes no
access argument, so there was nothing to gate and no
`IoMemoryRight.MapExecute` to add — recorded in §2's `IoMemoryObject`
row as a property of the kind rather than an omission.

The LOADER BOUNDARY is stated at three sites and they agree: the
`MapAccess` docstring in `sosabi`, `map_access`'s own docstring in
`kcore.dispatch`, and spec §2.5. One fact was checked rather than
assumed while writing them — `imgformat.has_sane_perms` refuses
write-without-read and X-on-device and does NOT refuse W|X, so the map
door's W^X rule is genuinely NEW law rather than the image rule
restated. The sentence says so.

### D-2 — the driver child, as landed

**ONE LAUNCHER, TWO DRIVERS.** `tests/driver-child` is arch-free and is
built for both profiles; `tests/child-echo-ns16550` and
`tests/child-echo-pl011` are per-CHIP, exactly as the root-as-driver
twins beside them are. That asymmetry was not in the brief and is worth
the sentence it got in the manifest: what a launcher moves is a
capability, and a capability has no datasheet.

The child driver bodies are the twins' bodies. Three things differ, all
about being a child: the window arrives by `give` (the drain call is
byte-identical), the program ends with `Process.exit` because a driver
child holds no `SystemRight.Shutdown`, and it is linked at the child
base. The `System.shutdown` calls on the error paths became
`Process.exit` for the same reason, and the ONE error path that cannot
report — a failed `process_self`, since `exit` is an op on the handle
that just failed to arrive — returns, which is `child-quota`'s
established shape.

**THE PARK IS THE DEATH, NOT A DEADLINE.** Every pre-5.5 launcher armed
a 20 ms timer while its child ran; a driver waits on a real serial port
at 0.15 s per byte, so a deadline would have to be guessed — and §9a's
one bounded exception to the console handover is a kernel whose armed
tick narrates over a process that owns the device. Unit 5.5's Process
waitable is what made "arm nothing" writable, and this case is the one
that needed it most.

**THE MASK IS THE BRIEF'S**, `Debug | ProcessSelf | ClockGet | Transfer`.
`ClockGet` is granted and never spent by this driver, deliberately and
noted at the site: the mask is what a launcher grants A DRIVER, and
narrowing it to what one program happens to call would make the policy a
transcript of the program.

### D-3 — the shared page, as landed

Root splits one page off its pool, maps it into ITSELF (RW) and into the
CHILD (RW), writes `0xA5`, starts the child and parks on its death. The
child reads `165`, writes `0x5A`, and exits carrying what it SAW — so
the first direction arrives at root twice, once on the console and once
as the §8 status word `65701`, and the second arrives as a plain load
out of the same page. The child's teardown line reads
`handles=2 ... process=1`: the masked System and the Process handle it
derived, and NOTHING ELSE. No Memory handle, no Mapping. That count is
the "access without possession" claim in the kernel's own accounting,
and it is what the case asserts.

**THE TWO INSTALLATION DIRECTIONS, AS LANDED**, side by side in one
unit:

  - `driver-child`: root GIVES the capability (spending
    `IoMemoryRight.Transfer`), the child installs its own row (spending
    `IoMemoryRight.Map` on the window and `ProcessRight.Map` on its own
    Process handle).
  - `share-double-map`: root KEEPS the capability and installs the
    child's row (spending `MemoryRight.Map` on the region and
    `ProcessRight.Map` on the CHILD's handle — the doctrine's
    no-possession-implies-authority).

Neither needed a mode flag anywhere, which is the object model doing its
job. The child's row is `Read | Write`, so the exec gate is never
consulted on this path; the case says so by omission and points at
`map-exec-gated` for the arranged version.

### Deviations, argued

**1. THE EXEC-GATE PROOF IS TWO CASES, NOT ONE.** The brief's proof
section sketches `map-exec-gated` with three arms — root maps X and
succeeds, a mint-without-`MapExecute` is refused, `W|X` in one row is
refused. Two of those three are FAULTS, and a fault ends the process:
the arithmetic design 6's own case split turned on, and which the runner
has stated at every split since ("a second probe after one would never
run"). Three arms therefore do not fit in one transcript. The options
were (a) put one refusal in a CHILD, which buys the capability-flow
story but costs a package and a create/give/start/park dance around a
one-comparison claim, or (b) split into two root-only cases. (b) was
taken, and it turned out to read BETTER than the sketch rather than
merely cheaper: each case's positive arm now motivates its own refusal.
`map-exec-gated` is about the RIGHT — root maps X through its full
handle, mints a sibling without the bit, proves that sibling is a
working handle by mapping RW through it, and is refused
`AccessDenied` on X. `map-wx-refused` is about the WORD — one region
double-mapped RW here and RX there (which is exactly why W^X-per-row
costs nothing expressible), then `R|W|X` in one row refused `BadArg`
THROUGH ROOT'S FULL HANDLE, so the refusal provably is not the exec gate
wearing a different hat. Cost: one extra case, two extra suite rows.

**2. THE READ-WRITE CONTROL ARM IS NEW.** `map-exec-gated` maps RW
through the narrowed sibling before asking for X. The brief did not ask
for it; without it the refusal is consistent with the sibling being
broken in any of a dozen ways, and one handle / two asks / one refusal
is what makes the bit the only variable.

**3. `Memory.mint(rights:)` AND THE `MemoryRight` RE-EXPORT ARE NEW
SURFACE.** The brief says "Root withholds it at mint/give" and there was
no way to spell either half: the sysapi had `System.mint` only, and
`MemoryRight` was kernel-internal. Both landed on `System.mint`'s exact
terms and under the facade's own stated rule for which enums are public
(a value a PROCESS CHOOSES and passes as a syscall argument). The
docstring on `Memory.mint` is where a launcher's memory policy is now
written down.

**4. `Process.give(iomemory:)` IS NEW SURFACE TOO**, and it is the only
thing D-2 was actually missing. `iomemory_rights()` has minted
`Transfer` since unit 4 and `boot_kind_of` has had its `IoMemory` arm
since then, both written for this flow; the typed funnel was the gap.
The give-funnel comment's "TWO FUNNELS AND NOT NINE" rights audit was
also STALE — it listed three minters of `Transfer` and there have been
four since unit 4 — and is corrected in the same edit.

### Findings

**1. A LAUNCHER CANNOT ATTENUATE WHAT A CHILD MAY DO TO ITSELF.** The
driver child spends `ProcessRight.InterruptBind` and `ProcessRight.Map`,
and neither is its launcher's to withhold: they arrive on the handle
`SystemOp.ProcessSelf` mints, and that op mints the ONE Process default
set (design 4 D-3). A launcher's keep mask reaches the child's SYSTEM
handle, not the Process handle the child derives from it — so
`process_rights()`'s note that "a launcher that spawns something which
is not [a driver] hands over a handle its keep mask left the bit out of"
is true of a Process handle a launcher GIVES and false of the one every
child derives. Withholding would need `ProcessSelf` to take a keep mask
of its own, which is a ruling rather than an edit. Nothing in v1 needs
it — a driver child is exactly the process that should hold both bits —
and it is recorded at `driver-child`'s start call, in the child's
`interrupt_bind` comment, and in spec §9.

**2. NO EXISTING CASE PRINTS A MEMORY RIGHTS WORD**, so the
authorized-with-cause bucket the brief anticipated for `memory_rights()`
growing a bit is EMPTY. Checked rather than assumed: no `tests/*` or
`root/` program formats a rights value, and the only mint of a Memory
sibling in the tree is this unit's own. A wider default set is invisible
to every shipped transcript because a rights word has never been an
observable.

**3. A CONSOLE LINE MUST BE ASCII.** `map-wx-refused` first shipped an
em dash in a `debug_print` line and it reached the transcript as `?`
(`ascii_byte` maps every non-ASCII scalar to one). Caught in the first
dev run; the line is plain ASCII now and says why at the site. Worth
knowing for anyone writing the next console line: the prose above the
code is where punctuation belongs.

**4. NO NEW SAWLANG DEFICIENCY.** Nothing in this unit met a language
wall — SL-9's lone-raw-backed-enum-case asymmetry was avoided rather
than met (every mask this unit writes combines two or more cases, which
folds), and SL-7's method-placement constraint is gone, which is why
`Memory.mint` and `give(iomemory:)` could each be written on the
receiver that reads best without a word about the module graph. The
`[SAWLANG]` section is unchanged.
