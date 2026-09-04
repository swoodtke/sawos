# SawOS design 34 — M5 unit 6a: the satellite slabs (threads and pipes donate)

**Status: BRIEF, Sep 3 2026 — dispatches under `designs/025` D-6 and
design 32's As-built.** Unit 6 delivered the mechanism and nine
plain-chain kinds, and honestly flagged that the user's three
motivating kinds all remained: Threads (one satellite, excluded on
risk), Pipes (ten satellites, excluded on shape), Processes (6b).
This unit converts THE FIRST TWO and closes unit 6's two recorded
test gaps. Arch-free.

## The one-sentence goal

`SlabKind` grows `Threads` and `Pipes`; a donated page can back
thread slots (frames included) and pipe slots (bodies and all ten
satellites included), proven by creating past both compiled floors
and exercising the objects in extent 1 exactly as extent-0 ones.

## Threads: one satellite, uniform stride

`THREAD_FRAMES` is a per-slot frame arena keyed by the same index.
The conversion: the frame storage becomes part of the donated
extent's geometry — a thread extent's slot stride is
`sizeof(ThreadSlot)` PLUS the frame stride, or frames ride a second
extent-parallel chain with identical extent boundaries (the agent
picks; the invariant is that slot i's frame is derivable from the
same (extent, offset) with no second lookup that can disagree).
**The context-switch path is why unit 6 declined this** — the trap
path reads a frame by index with interrupts implied off; whatever
shape lands must keep that read the same O(1) arithmetic it is
today, and the As-built restates the trap-path audit at the changed
lines. This is the unit's risk item: treat the switch path as a
reviewed surface, not collateral.

## Pipes: ten satellites on a derived index

`PIPE_BODIES` (8 KiB), `PIPE_LENS`, `PIPE_CALLER`, `PIPE_NEXT`, the
attach/refs arrays — all keyed by pipe index or a derived one. Two
honest shapes; the agent picks ONE and records why:
(a) FATTEN THE SLOT: fold every satellite's per-pipe data into
`PipeSlot` so the kind becomes a plain chain (largest slot in the
kernel, but one geometry, zero derived-index arithmetic);
(b) PARALLEL CHAINS: satellites grow extents in lockstep with the
slot chain, one shared extent table so boundaries cannot skew.
Either way `pipe_rendezvous`/`take`/`reply` paths must not grow a
second bounds check — the slab lookup already did it.

## Unit 6's two gaps, closed here

1. **The `maps == 0` refusal leg gets its test**: map a region into
   a child, then donate it — the caller must fault; the region must
   remain intact and mapped (nothing was absorbed).
2. **`slab_donate_free_nodes`** (the filed shape): donate Memories
   slots first so enough live regions can exist, then force >32
   non-adjacent free ranges, watch `dropped` stay zero where it
   would have counted, and see a release's range come back. This is
   the unit-5 + unit-6 composition witnessed end to end.

## Constraints carried forward

Extent memory converts ONLY in the two funnels 029's rebase left
(`Slab.extent_addr` reads, `slab_donate`'s zeroing write) — if the
satellite shape adds a third dereference of donated memory, it MUST
go through `extent_addr` or the As-built must name the new funnel
and its `phys_to_virt`; remember the lesson recorded in 029: a miss
is a hard fault on arm64 and INVISIBLE on riscv32. Zero at
donation. Bounded walks restated where loop bounds change. No new
ops — `SlabKind` gains two cases (facade lines updated per SL-20's
discipline: one name each on `sosabi.ops`'s facade line and `sos`'s
lib.saw).

## Authorized transcript motion

The new cases' rows only (`thread-donate`, `pipe-donate`, the two
gap-closers — final names the agent's). Everything else
byte-identical against the 0.5.0 baseline (495 lines, hash
`801a0f98…`), both arches; the +N-bytes-per-image pattern from a new
exported wrapper does NOT apply (no new floor export — SlabKind
cases ride the existing `slab_donate` wrapper).

## Out of scope

Processes/6b; donated-slab reclamation; the MAX_HANDLES naming
ceiling (6b's); any HAL/tier work; spec.md prose (unit 8).

## Recording duties

As-built here: the shape chosen per kind and why, the switch-path
audit restatement, geometry arithmetic, gate evidence
(new-rows-only diff, both arches), findings. SL entries if the
language bites. Close the tracker entry in place.

# As built (Sep 3 2026)

**Status: BUILT.** `SlabKind` grows `Threads = 9` and `Pipes = 10`; both of design
32's excluded kinds convert, and both of its recorded test gaps close. The two
kinds took TWO DIFFERENT SHAPES and the difference is the unit's substance — one
satellite became a column of the extent table, ten satellites became one record.
The gate is new rows only, both arches, with **no image-size drift at all**
(design 32's +40 bytes per riscv32 image does not recur: no new floor export).

## The shape per kind, and why each one

### PIPES — the ten satellites become ONE record (the brief's option (a), sharpened)

The brief offered FATTEN THE SLOT or PARALLEL CHAINS. **What landed is neither
exactly: the ten satellites fatten into one `Exchange` record, and that record is
its own chain in lockstep with the connection chain.** The reasoning, in the order
it forced itself:

- **FATTEN THE SLOT AS WRITTEN WAS THE WRONG TRADE, and the brief's own constraint
  is what ruled it out.** Folding the exchanges into `PipeSlot` as an inline
  `[Exchange; PIPE_INFLIGHT]` makes the kind a plain chain — but the flat exchange
  index `x` is not going away (see the boundary list below), so every satellite
  access becomes `PIPES[x / PIPE_INFLIGHT].ex[x % PIPE_INFLIGHT].field`: a slab
  bounds check AND a fixed-array bounds check where there was one array index, plus
  a division, on `pipe_post` / `pipe_take` / `pipe_resolve` / `pipe_reply_status` /
  `deliver_take`. That is exactly the "second bounds check on the rendezvous paths"
  the brief forbids. It would also have made two place windows on ONE root a
  routine spelling, which the language refuses in several of the expressions this
  code already has (`EXCHANGES[x].reply_refs != 0 || EXCHANGES[x].request_refs != 0`
  is fine as two sequential windows; `PIPES[c].ex[i]` twice in one call is not).

- **SO THE FATTENING HAPPENS ONE LEVEL DOWN.** `PIPE_BODIES`, `PIPE_LENS`,
  `PIPE_EX_STATE`, `PIPE_REPLY_REFS`, `PIPE_REQUEST_REFS`, `PIPE_REPLY_ATTACH`,
  `PIPE_REQUEST_ATTACH`, `PIPE_CALLER`, `PIPE_NEXT` and `PIPE_MSG_HELD` are fields
  of `Exchange`, and `EXCHANGES: Slab<Exchange, PIPE_EXCHANGES>` is an ordinary
  plain chain using the ordinary accessor. Every access site is a MECHANICAL
  rename on the SAME flat index — `PIPE_LENS[x]` becomes `EXCHANGES[x].len` — so
  the rendezvous paths keep their one bounds check, gain no division, and reach
  donated memory through `Slab.extent_addr` and nothing else. 82 sites, renamed by
  a bracket-matching script rather than by hand because several index expressions
  nest (`PIPE_CALLER[THREADS[slot].call_claim - 1]`).

- **AND THE TWO CHAINS CANNOT SKEW, BY ARITHMETIC RATHER THAN BY BOOKKEEPING.**
  This is the part the brief asked for as "one shared extent table", and the answer
  turned out to be better than a shared table. `x = c * PIPE_INFLIGHT + i` and
  `c = x / PIPE_INFLIGHT` are UNIFORM ACROSS THE CHAIN — `PIPE_INFLIGHT` is a
  compile-time constant that does not know where a connection lives — so if a
  donation appends `k` connections to `PIPES` and `k * PIPE_INFLIGHT` exchanges to
  `EXCHANGES`, then `PIPES` extent `e` holds connections `[S, S+k)` and `EXCHANGES`
  extent `e` holds exactly the exchanges those connections map to. The two walks
  land in the same extent for corresponding indices as a theorem, not as an
  invariant somebody maintains. `slab_install` checks BOTH chains for room before
  either donates, so the one way they could skew — one donation succeeding and the
  other failing — is unreachable, and it is `fatal_kernel` if it ever happens.

**THE FLAT INDEX HAD TO SURVIVE, which is why no shape that renumbers was
available.** A bare exchange number is STORED, not merely computed, in six places:
`HandleEntry.target` for `PipeReply`/`PipeRequest`, `ATTACHMENTS[a].target`,
`ThreadSlot.call_claim` (as `x + 1`), `Exchange.caller`'s mirror, the staged list's
`PIPES[c].staged` / `Exchange.next` links, and `StagedRef.target` when a one-shot
capability travels in a message. Design 32's objection — "the index is DERIVED from
`MAX_PIPES`, so growing the connection slab renumbers every satellite" — is exactly
right about ten arrays and simply false about one chain, because a chain does not
renumber: extent 0 keeps indices `0..PIPE_EXCHANGES` and donations append above.

### THREADS — the satellite becomes a COLUMN of the extent table

`THREADS` is a `Slab<ThreadSlot, MAX_THREADS>` and the frame arena rides
`Slab.ext_side`, a per-extent base read through the same `locate` walk the slot
lookup uses. `frame_slot` is the funnel.

**THE BRIEF'S FIRST OPTION IS UNSOUND, AND THE REASON IS THREE INSTRUCTIONS DEEP
IN ONE HAL.** "A thread extent's slot stride is `sizeof(ThreadSlot)` PLUS the frame
stride" means the frame is a field of the slot. It cannot be:

- One profile's user-return path loads the frame address **into the stack
  pointer** (`.Luser_return`: `mov sp, x0`). A frame therefore IS that register and
  must carry its 16-byte alignment — which is what
  `static_assert(THREAD_FRAME_STRIDE % 16 == 0)` in `kcore.limits` has always been
  about, though nothing in the kernel said WHY until now.
- A fattened `ThreadSlot` is `sizeof(ThreadSlot) + 512` at `alignof(Int)`:
  **592 on arm64 — a multiple of 16, so it would have passed — and 552 on riscv32,
  which is 8 mod 16.** Every other frame would have been misaligned ON THE PROFILE
  THAT DOES NOT CHECK, which is 029's lesson in a new costume.
- Saw has no alignment attribute, and no padding fixes both profiles at once: the
  headers are 40 and 80 bytes, so the parity the padding must correct is opposite
  on each.

So the arena stays separately aligned at the stride, and the chain carries a second
base per extent. `ext_side` is a COLUMN of the extent table rather than a parallel
array precisely to satisfy the brief's invariant — "slot i's frame is derivable
from the same (extent, offset) with no second lookup that can disagree". `locate`
answers `(extent, offset)` ONCE and both addresses are arithmetic on that one
answer. Cost: nine arena-less kinds pay `MAX_SLAB_EXTENTS` words of `.bss` each for
a column they never read (176 bytes riscv32, 352 arm64; zero image bytes by design
149's zerofill), and that buys structural impossibility rather than a convention.

## The switch-path audit, restated at the changed lines

`frame_slot` is the only function this unit changed on the context-switch path, and
it is read by `kcore.sched.run_thread` with interrupts implied off. The audit, at
the code as it now stands:

- **THE FLOOR ARM IS BYTE-FOR-BYTE THE OLD FUNCTION.** `index < MAX_THREADS` covers
  every thread on a machine that never donates — which is every case in the suite —
  and that arm is the same `align_up` plus multiply-add it always was, behind ONE
  compare against a compile-time constant. No walk, no read of the extent table, no
  branch on donated state. This is why the 238 pre-existing case rows are
  byte-identical rather than merely passing.
- **THE DONATED ARM IS ONE BOUNDED WALK.** `THREADS.locate` is at most
  `MAX_SLAB_EXTENTS` (4) iterations of an O(1) body over the same table the slot
  lookup uses; the address is then a multiply-add on that row's side base. Design
  1's verdict is unchanged and the argument is the same one with a different
  number: the bound is finite because extents are finitely many and each is
  finitely sized. **No preemption point, and none possible** — the body allocates
  nothing, takes no lock, and can fault on nothing, because an extent is validated
  and zeroed before it is installed and donation is permanent so a base is never
  retired.
- **THE TWO READS CANNOT DISAGREE.** Slot `i`'s storage and slot `i`'s frame come
  from one `locate` answer, so a donated thread cannot read another thread's frame
  however the chain was built.
- **THE WITNESS, NOT THE ARGUMENT, IS WHAT SETTLES IT.** `tests/thread-donate`
  starts the thread whose slot AND frame are both in donated memory, watches it
  yield three times (`TTT`) and reads its exit code back through a join
  (`code=33`). `frame_init` writes the entry, stack pointer and argument into that
  frame; the trap return resumes it; each yield saves the register file into it and
  restores it. A frame address computed even slightly wrong loses the 33 — and on
  the translating profile faults outright, which is 029's point.

## The funnels — the constraint the brief put first

Design 29's rebase note warned that a missed `phys_to_virt` on donated memory is a
hard fault on a translating tier and INVISIBLE on an MPU one. This unit was written
against that, and the accounting is:

| site | reads/writes donated memory | conversion |
| --- | --- | --- |
| `Slab.extent_addr` (`slab.saw`) | slot lookup, all eleven kinds | `hal.phys_to_virt`, unchanged |
| `slab_donate`'s `long_zero` (`dispatch.saw`) | the zeroing write | `hal.phys_to_virt`, unchanged |
| **`frame_slot` (`threads.saw`)** | **donated thread frames** | **`hal.phys_to_virt`, NEW — named and justified at the function** |

**THAT IS THE WHOLE LIST, AND `EXCHANGES` IS DELIBERATELY NOT ON IT.** The pipe
conversion adds NO funnel: an exchange is a slab slot, so it is reached through
`extent_addr` like every other slot, and `exchange_body_addr` is field arithmetic
on what the accessor already lent rather than a second dereference of a stored
base. That is a property to check rather than assume, which is why it is tabulated.
`ext_base` and `ext_side` both stay PHYSICAL — a base is addresses-as-data — and
only the moment of dereference converts.

## The geometry, measured

Probed against the pinned sawc (0.5.0) for both targets, by temporary
`static_assert` inside `kcore` and reverted:

| | riscv32 | arm64 |
| --- | --- | --- |
| `sizeof(ThreadSlot)` | 40 | 80 |
| `THREAD_FRAME_STRIDE` | 512 | 512 |
| **thread unit** (slot + frame) | **552** | **592** |
| `sizeof(PipeSlot)` | 24 | 48 |
| `sizeof(Exchange)` | 200 | 232 |
| `PIPE_INFLIGHT` | 16 | 16 |
| **pipe unit** (connection + its ring) | **3224** | **3760** |

- **A UNIT IS NOT ALWAYS A SLOT ANY MORE**, and `slab_geometry` is where that lives:
  nine kinds donate one array and a unit IS a slot; `Threads` and `Pipes` donate a
  slot AND the per-slot storage that belongs to it, so the stride is the pair's.
  One region, carved by `slab_install`, because asking a caller to donate two
  regions in the right ratio would be asking it to know two kernel-internal sizes
  and get their alignment right.
- **THREADS: frames first, at the extent's own base.** The base is aligned to
  `THREAD_FRAME_STRIDE` (the geometry says so), each frame is exactly a stride, so
  every frame in the extent is stride-aligned; the slots follow at
  `slots * THREAD_FRAME_STRIDE`, itself a multiple of the stride and therefore of
  any slot alignment. A 4 KiB page yields six or seven threads.
- **PIPES: connections first, then their exchanges.** `slots * sizeof(PipeSlot)` is
  a multiple of the alignment `Exchange` wants (the geometry takes the larger of the
  two for the base), so the second array lands with no pad between them. The 16 KiB
  `tests/pipe-donate` donates yields 5 connections on riscv32 and 4 on arm64 — which
  is why that case, like `slab-donate`, asserts that the ceiling MOVED and never
  what it moved to.
- **ONE SIGNATURE CHANGED: `slab_geometry`'s fourth number is HEADROOM, not the
  held count.** A two-chain kind has two ceilings and only the kind knows which
  binds; for `Pipes` it is the exchange chain, by a factor of `PIPE_INFLIGHT`.

## Loop bounds, and the ones deliberately left alone

Six thread bounds and three pipe bounds moved to `capacity()`: `alloc_thread`,
`live_threads`, `end_process`'s thread sweep, both passes of
`release_pending_calls`, `call_room_waiter`; `alloc_pipe`, `reap_unreferenced`'s
exchange pass and its connection pass. `ready_remove_process` is a LIST walk and
needed no bound change, only its audit sentence. Every remaining `MAX_THREADS` /
`MAX_PIPES` / `PIPE_EXCHANGES` use was read and justified: they are the `Slab` type
arguments, `PIPE_EXCHANGES`'s own derivation, `frame_slot`'s floor compare, and
`static_assert(DEFAULT_QUOTA_PIPES < MAX_PIPES)` — a compile-time POLICY assert
about the floor, which is what it should be about.

## Unit 6's two gaps, closed

### 1. The `maps == 0` refusal leg — `tests/slab-donate-mapped` + `tests/child-donor`

**THE DONOR HAD TO BE A CHILD, and that is the whole design of the case.** The
refusal is a FAULT, so whoever calls the op dies — and "the region was not
absorbed" cannot be read out of a dead process. So the roles split: ROOT maps the
page into its own space (that is what makes `maps` non-zero) and stamps a witness
byte through the mapping; the CHILD holds the region's ONLY handle, carries
`SystemRight.SlabDonate` minted down from root's set, donates, and dies with
`status=131077` (`Faulted << 16 | BadState`); ROOT then reads the witness back.

**THE LEG IS PINNED, NOT MERELY REACHED.** The kernel tests `refs != 1` BEFORE
`maps != 0`, so a case that left a second Memory handle around would have proved
`slab_donate_shared` a second time. Root GIVES the region away — a transfer, which
unbinds root's own entry — and keeps the MAPPING instead, which counts on `maps`
and never on `refs` (design 28). That distinction is the thing being tested.

**AND THE WITNESS IS A DIRECT STATEMENT, NOT AN INFERENCE FROM THE FAULT.**
`slab_donate` zeroes a region before installing it, so an absorbed page reads back
as zeroes; this one still reads 165 through the mapping that caused the refusal.
No existing case in the tree observes a refusal's ABSENCE of effect this way.

It is also the first case in the tree to give a child `SystemRight.SlabDonate` —
design 32 wrote that a deployment "strips the bit on the way down"; this is the
first program to hand it down instead.

### 2. `slab_donate_free_nodes` — the unit-5 composition, witnessed

**DESIGN 32's FINDING 6 NAMED THE WRONG WALL, and that is this unit's most useful
correction.** It says exhausting the node slab "needs more than 32 live regions
interleaved with them — and `MAX_MEMORIES` is 16", and prescribes donating to
`Memories` first. There are in fact TWO walls and `MAX_MEMORIES` is neither:

1. **`MAX_HANDLES` is 16, not `MAX_MEMORIES`.** A separator has to be bytes the
   pool has handed out and not got back, and the only ways to hold bytes out are a
   handle, a grant row, or a permanent donation — so a process cannot NAME 33 live
   regions however large its Memory slab is. Donating `Memories` alone would not
   have helped at all. (This is design 32's own finding 3 — "a root server can size
   the machine but not name what it sized" — biting the case that unit filed.)
2. **`pool_cut` IS FIRST FIT.** Cutting the holes one at a time does not work
   either: the next cut is served out of the hole just made, so the fragmentation
   never grows. Nothing in finding 6 anticipated this.

**BOTH ARE ANSWERED BY THE OP UNDER TEST, which makes finding 6's closing sentence
truer than it knew.** A DONATED region's bytes leave the pool permanently and its
Memory slot is retired, so a donation is a separator that costs NO handle and NO
slot — 35 of them fit where 33 live regions do not. And the holes are cut in
STRICTLY GROWING sizes, so no later cut can be served out of an earlier hole.
`SlabKind.Memories` is still donated to (four of the separators go there), but as
one separator among many rather than as the enabling step.

**HOW THE LEAK IS OBSERVED, GIVEN THAT `dropped` HAS NO OP** — a CONTROLLED PAIR.
Two identical 512-byte regions: R1 released with the node slab full (it leaks), R2
released after a page has been donated to `FreeRanges` (it gets a node). The pool's
cursor is spent to zero by construction and every hole is smaller than the pair, so
a `split` of that size can only be served out of one of those two ranges. The first
succeeds — "the range came back". The second cannot be served, so `pool_can_serve`
says no and `MemoryOp.Split` ends the caller: **the case EXPECTS root to fault, and
a kernel that had NOT leaked R1 would have served that split and ended cleanly.**
The fault is the assertion.

**IT IS riscv32 ONLY, and the arithmetic is why.** Growing sizes are forced by
first-fit and the step is one `hal.PROT_GRAIN` — 4 bytes on one profile, 4096 on
the other. At 4096 the 32 holes alone want `4096 * (1 + ... + 32)` = 2.1 MiB and
their separators as much again, against a 256 KiB pool that cannot grow (the other
profile's grant window caps it). So the construction is arithmetically impossible
there. This is the one place this unit's coverage is asymmetric, and it is recorded
rather than papered over; `"arches": ["riscv32"]` is existing house practice.

## The ABI, and what did NOT need doing

`SlabKind` gains two cases; `from(raw:)` is synthesized, so the decode stays exact.
**NO FACADE LINE MOVED, and the brief's expectation here was one increment off:**
it asks for "one name each on `sosabi.ops`'s facade line and `sos`'s lib.saw", but
`SlabKind` is a NAME and design 32 already put it on both. A case is not a name, so
SL-20's discipline is satisfied by the existing lines. No new op, no new right, no
new floor export — which is exactly why the +40-bytes-per-image motion design 32
reported does not recur.

## Findings

1. **DESIGN 32's FINDING 6 BLAMED `MAX_MEMORIES`; THE WALLS ARE `MAX_HANDLES` AND
   FIRST-FIT.** Recorded above in full because a later unit could inherit the
   mistake, exactly as design 32 recorded the ceiling premise for the same reason.
   Unit 6b's `MAX_HANDLES` work is what would let a straightforward version of this
   case exist.
2. **THE FRAME'S ALIGNMENT IS LOAD-BEARING AND NOTHING IN THE KERNEL SAID SO.**
   `THREAD_FRAME_STRIDE % 16 == 0` sat in `kcore.limits` with a comment about
   profiles wanting an aligned stack pointer; what it actually protects is one
   HAL's `mov sp, x0` on the frame address, and the obvious thread conversion
   violates it on the profile that does NOT check. `frame_slot`'s doc comment is
   now where that is written down.
3. **A DONATED REGION IS THE CHEAPEST WAY TO HOLD BYTES OUT OF A POOL**, costing
   neither a handle nor a Memory slot — a property of `SlabDonate` nobody designed
   in, and the one that made gap 2 reachable. Worth knowing before someone assumes
   donation is only about capacity.
4. **THE PIPE CONVERSION MADE THE MACHINE'S DENSEST HANDLE USER VISIBLE.**
   `tests/pipe-donate` failed its first run with `NoResource` from the HANDLE TABLE
   while every slab still had room: holding both ends of four filling connections
   plus the fifth pair plus a claim left no row for the obligation a take mints.
   The fix is a property worth stating — a connection frees only when BOTH columns
   reach zero, so ONE live end holds the slot. `MAX_HANDLES` is the limit this tree
   meets first, twice in one unit.
5. **TWO SL ENTRIES FILED** — SL-23 (`&var slab[i].field` is refused though the
   same field is addressable inside a `&var self` method; the workaround is a
   method on the element type and is arguably the better code) and SL-24 (a bare
   integer literal does not adopt a platform `UInt` at a call argument, though it
   does at a `static` and at every fixed width).
6. **`exchange_index` WAS DEAD BEFORE THIS UNIT AND STILL IS** — zero call sites
   tree-wide, found while mapping the satellites. Left alone: it is the honest
   inverse of `pipe_exchange_at` and removing it is not this unit's business.

## Gate

Both runs in this worktree, back to back under the machine-wide suite lock, same
toolchain (sawlang 0.5.0; `sawlang.pin` untouched).

**Baseline**, on the pristine tree at `340e4c5` before any edit: **238 passed
across riscv32 + arm64**, 119 cases, a 495-line transcript hashing to
`801a0f98fd521978ca2038db413404c9b3acab359b0629035255f63343a98228` — **exactly the
hash the brief records for main's 0.5.0 run**, so the baseline is the tree's
standard one.

**After: 245 passed across riscv32 + arm64**, 511 lines,
`8983d9a92bd69c8a7aa7ea562041bf52d525c8ad85b5752b074819f0118ea3ea`.

**THE DIFF, AND IT IS NEW ROWS AND NOTHING ELSE.** Normalising only the mechanical
`[n/N]` case ordinals, `diff` reports exactly five hunks:

1. **5 new riscv32 image lines** — `thread-donate`, `pipe-donate`,
   `slab-donate-mapped`, `child-donor`, `slab-donate-free-nodes`.
2. **4 new riscv32 case lines** — the four new cases.
3. **4 new arm64 image lines** — the same minus `slab-donate-free-nodes`.
4. **3 new arm64 case lines** — the three that run there.
5. **The pass-count line**, 238 -> 245 (+7 = 3 cases x 2 arches + 1 riscv32-only).

**NOT ONE PRE-EXISTING LINE MOVED — case rows and IMAGE SIZES alike.** Design 32's
one unanticipated motion (119 riscv32 images at +40 bytes) does NOT recur, and the
reason is structural rather than lucky: this unit adds no `@export` to the floor,
so nothing links into images that do not call it. Every one of the 238 pre-existing
case lines is byte-identical and in its original ordinal, which is what appending
the cases bought. The three documented timing-dependent rows never enter the
comparison at all — a case's console output is printed only on FAILURE, and nothing
failed.

### Rebase onto design 33 (placement), Sep 4

Design 33 merged to main (`4cb149c`) while this unit was in review, so unit 6a is
the SECOND REBASER and this section is the resolution.

**THE TEXTUAL MERGE WAS ONE CONFLICT AND IT WAS PURE ADJACENCY.**
`tools/sos_runner.py`: both units appended cases to the tail of `CASES`. Resolved
by keeping both, 033's `map_placed` FIRST so that its case ordinal is the one its
own As-built records and only this unit's four rows append after it.
`designs/todo.md`, `kernel/abi/src/ops.saw` and `kernel/core/dispatch.saw` all
auto-merged — the last one is worth naming, because 033 edited `install_row`, the
Memory/IoMemory/Mapping arms and `process_create` in that file while this unit
edited `slab_geometry`/`slab_install`/`slab_donate`, and the two never touched a
shared line.

**THE REAL CONTACT WAS NOT A CONFLICT, AND THAT IS THE FINDING.** 033 collapsed
the arm64 user linker scripts — `root.ld`, `child.ld`, `child2.ld` — into ONE
`hal/arm64/user/user.ld`, because placement means every user image links at one
canonical base. It updated ~50 existing `Saw.toml` files to match. This unit's
FIVE new packages named `root.ld` and `child.ld`, and **git had nothing to
conflict with, because those files are new on this side and untouched on the
other.** The merge was clean and the arm64 build then failed at the LINK step:
`ld.lld: error: cannot find linker script ../../hal/arm64/user/root.ld`.

So: **a new file naming a removed one is invisible to a three-way merge, and a
clean rebase is not evidence that a rebase is done.** Two units appending
packages to a tree whose shared scaffolding one of them is renaming will meet this
every time. Fixed by pointing all five at `user.ld`; riscv32's `root.ld`/`child.ld`
are untouched by 033 and stay.

**THE COMPOSITION CHECK, CONFIRMED RATHER THAN ASSUMED.** The question is whether
this unit's donated extents — KERNEL slab memory reached through the linear map —
need anything from 033's split of user addressing. They do not, and the reason is
that the two units are on opposite sides of design 29's seam:

- **033 changed the USER side.** A `GrantRow` gains a VA range beside the physical
  base it translates to; `image_link_base` and `map_place` are new, and
  `map_target_ok` splits into a virtual half plus a physical `map_source_ok`.
- **THIS UNIT ONLY EVER DEREFERENCES THROUGH `hal.phys_to_virt`**, which is 029's
  KERNEL-side seam and which 033 did not touch. All three funnels — `extent_addr`,
  `slab_donate`'s `long_zero`, and this unit's new `frame_slot` — go through it.
  Grep-verified: `slab.saw` and `threads.saw` name none of 033's changed seams.
- **`MEMORIES[slot].base` IS STILL PHYSICAL**, which is exactly what the donation
  path requires: `slab_donate` zeroes through `hal.phys_to_virt(start)` and records
  `start` itself, unconverted, in the extent table. 033 made the VA/PA distinction
  explicit on the row; it did not make a region's base mean something new.

**AND `tests/thread-donate` ON arm64 IS THE COMPOSITION WITNESS**, which is the
strongest form the check could take: a thread whose slot AND whose saved context
both live in donated memory reached through the linear map, started and switched
into three times, under a user image that 033 now loads at frames and maps at one
canonical link base. It passes. A frame address wrong in either unit's terms would
fault on this profile.

**ONE CASE NEEDED NO SOURCE CHANGE FOR A REASON WORTH STATING.**
`tests/slab-donate-mapped` stamps its witness byte through `sos_test_pool_base()`,
which was the pool's PHYSICAL base and is exactly the kind of address placement
invalidates. It keeps working because 033 retargeted `tests/poolbase_arm64.c` to
the VIRTUAL address a process's FIRST mapping now lands at (`0x4024_0000`), and
this case's root makes exactly one mapping — so it inherits 033's fix on the same
terms `share-double-map` does, rather than passing by accident. On riscv32 the
shim still returns the physical base and the two answers are one answer. The owed
migration onto `Mapping.base()` that 033 records for the ten older packages now
has an eleventh.

**RE-GATE ON THE COMBINED STATE.** Both runs back to back in this worktree under
the machine-wide suite lock, same toolchain (sawlang 0.5.0).

- **Baseline, main at `4cb149c`: 239 passed, 120 cases, 498 lines**,
  `2238afc852236605b6403368ed69fe0e37a96db5de032ee0c5d67a5c889daaf2` — the same
  239/120 the lead's own gate of main records.
- **After: 246 passed, 123 cases, 514 lines**,
  `c823c1b5cab6977ea7f57bee6d72091580e2998531487dc0f19378df39667034`.

**THE DIFF IS THE SAME FIVE HUNKS OF NEW ROWS IT WAS BEFORE THE REBASE** — 5 new
riscv32 image lines, 4 new riscv32 case lines, 4 new arm64 image lines, 3 new arm64
case lines, and the total. Proven per ARCH rather than only in aggregate, since
033's authorization and this unit's differ by profile: **with this unit's rows
removed, the riscv32 half is BYTE-IDENTICAL to main's**, and the arm64 half is too
apart from the whole-run total line. So neither unit's rows moved the other's, and
033's 120 cases and every one of its image sizes are untouched.

A detail that says the linker-script change was genuinely inert for these images:
all nine of this unit's image sizes are **identical to the pre-rebase run's**, arm64
included, so `user.ld` produces the same bytes for them that `root.ld`/`child.ld`
did.

**ONE NUMBER I COULD NOT REPRODUCE, reported rather than smoothed over.** The
dispatch quotes a riscv32-half hash of `1ca29323…` for main; splitting the
transcript at the `riscv32  (` / `arm64  (` banners gives me `45fc3897…` for the
same run. The totals, the case count and the line count all agree, so this is a
difference in where the half is cut rather than in what ran — but the boundary is
not written down anywhere, so the hash is not a check anyone else can repeat.
The per-arch DIFF above is definition-independent and is what this unit rests its
claim on.
