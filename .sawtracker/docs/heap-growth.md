# SO-142 phase 2: heaps from pools

Revision 3 of the design for the owner's review. Unit 1, the
allocation-free drain, has merged; nothing else here is built.

What a process allocates from:

- **a small built-in heap** in `.bss`;
- **a default heap** that grows up from the front of a pool, one contiguous
  region in one protection slot, as allocation needs it;
- **named heaps**, each the same over a pool of its own.

Shareable `Memory` objects come from the back of the same pools, growing
down. The document covers:

- the kernel ops this needs (an allocation from the back, and a `Memory`
  and its `Mapping` grown in place), with their spec text, in units of their
  own ahead of the runtime's;
- the runtime's heaps;
- the share API;
- the growth step per board;
- the declaration and image syntax;
- locks, the builder's checks and the units.

A bare section sign (§4) is this document's; the spec's sections are written
"spec §N", and SO-124's design (`.leadwork/SO-124/design-r5-live.md`) is
cited as "SO-124 §N". The replies to the review's threads on revision 2 are
in `heap-thread-replies-r3.md` beside this file.

Sources:

- the owner's rulings:
  - two heaps for small boards;
  - a small, configurable built-in heap of 4 KiB by default;
  - start-up allocates nothing;
  - a heap's pool owned entirely by the runtime;
  - "only map in memory as needed via allocation. some should remain to
    allocate sharable memory objects";
  - malloc memory from the pool's front and memory objects from its back,
    the heap's one slot extended as it grows;
  - heap memory is forever, and a shared object is an ordinary Memory
    returned when its last mapping and handle go;
  - a board-aware growth step;
  - the question of a heap alignment key;
- sawlang's allocator ABI as its lead states it:
  - a failure answers null and never panics, aborts or blocks;
  - alignment up to 4096;
  - `dealloc` gets the matching size and alignment;
  - any thread frees any block;
  - no allocation or suspension inside the allocator;
- SO-124 unit 1 and SO-142 unit 1 (`be9c9bd9`), both merged;
- spec §2.5, §2.7, §2.9, §2.10, §3, §5.5, §5.6, §5b.1, §5c, §11 and §12;
- the kernel and runtime:
  - `kernel/core/{objects,dispatch,process,pslot,limits}.saw`;
  - `kernel/abi/src/{ops,rights,status,boot}.saw`;
  - `hal/riscv32-common/kernel/lib.saw` (PMP),
    `hal/arm64/kernel/{lib.saw,ABI.md}`;
  - `rt/common/src/{heap,lib}.saw`;
  - `kernel/sysapi/src/{rt,floor,bootset,pool,memory,mapping,system}.saw`;
- the builder and root: `root/`, `tools/sosimage/src/*.saw`, and each
  board's `board.toml`;
- the S3: `hal/xtensa-esp32s3/ABI.md` §8 and §16, and SO-102's issue;
- sawlang at the pin (v0.24.3): `std/{alloc,vector,box,map,set,string}.saw`,
  `runtime/ABI.md` and `LANGUAGE_SPEC.md`;
- one `sawc mono --check` probe at the pin (§5.2).

## 0. The shape in one page

- **A pool has two ends.** A heap takes the pool's front and grows up:
  - it is one `Memory` that the kernel extends in place with the pool's
    next bytes (`MemoryOp.Extend`);
  - it has one `Mapping` whose row the kernel extends to cover them
    (`MappingOp.Grow`).

  Shares come from the back and grow down (`PoolOp.Allocate`'s new `Back`
  argument). The two meet in the middle, and whichever side cannot be served
  answers null or `NoMemory`.
- **One protection slot per heap, whatever its size.** On riscv32 the row
  is a TOR pair whose top moves; on arm64 the next pages are mapped at the
  next virtual addresses, which `map` reserves; on the S3 (Flat) no slot is
  spent at all. A heap costs one `Memory` row and one `Mapping` row for its
  life.
- **The heap only grows, and it is forever.** No trim, no region returned.
  Its memory goes home when the process ends. A share is an ordinary
  `Memory` and goes home under spec §2.5's refcount invariant, when its last
  handle and its last row are gone, wherever they are.
- **The growth step is the pool's grain:** a request's need, rounded up to
  the grain the kernel reports for the pool, and at least 1 KiB. That is a
  page on arm64 and four bytes on riscv32 and the S3. Doubling on riscv32
  (a NAPOT row) is weighed and not recommended: this HAL charges a NAPOT
  row the same slot as a TOR pair (Q1).
- **The runtime holds each heap's pool, its `Memory`, its `Mapping` and a
  Map-only Process sibling for the process's life.** The heap is one TLSF
  region, so `heap.saw` gains only `extend` and a class table sized for the
  pool.
- **The global allocator** serves from the default heap and then the
  built-in heap. `Heap<SLOT>`, an std `Allocator`, serves named heaps;
  `Heap<SLOT>().share(len:)` answers a `Memory` from the back with
  `Transfer | Map`.
- **Every refusal to allocate is null,** and each heap records why. The
  panics are a bad free and adoption's rights checks, before the program
  runs.
- **Syntax:**
  - the declaration's optional `arena` and a `[heap.NAME]` table per heap
    (`len`, `slot`, `ram`);
  - the image's `arena` and `heap-ram`;
  - a board range's `atomics = true`.

  The builder holds one `Memory` and one `Mapping` row per heap.
- **No existing process changes;** the kernel and root keep their built-in
  heaps.
- **Units:** two kernel units first (a pool's two ends; growing a `Memory`
  and a `Mapping`), then four runtime and builder units, then the S3's
  PSRAM.

## 1. What a process allocates from

### 1.1 The three kinds

| Kind | Memory | Sized by | Serves |
|---|---|---|---|
| built-in | `[_arena_start, _arena_end)` in `.bss` | `ARENA_SIZE` at link: the declaration's `arena`, else `[image] arena`, else 4 KiB | the global allocator, after the default heap |
| default | the front of a pool under `HEAP_TAG`, growing up | `[heap.default] len`, the pool | the global allocator, first; `Heap<0>` |
| named | the front of a pool under `HEAP_TAG + slot` | `[heap.NAME] len` | `Heap<slot>` |

The 4 KiB default is SO-142.p3's. The runtime needs none of it: start-up,
a `print` with `{}` arguments and the panic path allocate nothing.

### 1.2 Who supplies the memory, and the authority

The launcher gives one pool per declared heap, under the heap's reserved
tag, with the keep mask `PoolRight.Allocate | Map | Transfer | Extend`.
`Extend` is the new bit of §2.2, carried for allocations as `Map` is. An
allocation carries its pool handle's carried bits, capped by the process's
`Memory` ceiling (spec §2.5; `mint_capped`). So the heap's `Memory` can be
mapped and extended, a share can be mapped and given, and neither can be
executed (no `MapExecute`) or minted into siblings (no `Mint`).

**The pool is owned entirely by the runtime.** Its word sits in a static of
`sos.rt` with no typed way out. The program reaches it only through the heap
and `share` (§5). Like every handle guarantee of `sos`, this holds in the
typed layer: at the C altitude a program that guesses words could name the
pool, and the kernel cannot tell a process's runtime from its program.

### 1.3 The built-in heap stays separate

Two TLSFs, routed by address. The global allocator asks the default heap,
which may grow, and then the built-in heap. A free compares the block with
the arena's linker bounds and goes to the heap whose range holds it.

### 1.4 The kernel and root

The kernel keeps its built-in heap and never allocates at run time. Root has
no launcher and so no pools; its records carry region ordinals, never a
heap tag, and its heap is its built-in heap.

### 1.5 Lifetimes

- **A heap is forever.** Its `Memory` and `Mapping` are the runtime's for
  the process's life; nothing it grows into goes back. At teardown the
  process's handles go and its domain is cleared (spec §2.10), the row's
  count on the allocation drops, and its range returns to the pool.
- **A share is an ordinary `Memory`.** Spec §2.5's refcount invariant
  says it exactly: "a Mapping holds a reference to the physical pages (not
  merely to a MemoryObject handle). The pages are freed only when the last
  reference of either kind — any MemoryObject handle or any Mapping —
  drops", and "The range returns to its pool at the second zero". `maps`
  counts installed rows, so a row whose `Mapping` handle was released still
  holds the bytes. So a share's range returns to the pool's free list when
  its last handle and its last row are gone, in whichever process they
  were.
- **The pool** dies when its handle has gone (at teardown) and every share
  has come home, which may be after the process has ended (spec §2.5, "What
  holds a pool").

### 1.6 What a pool's bytes hold

`PoolOp.Allocate` writes none of the bytes it cuts (`pool_cut`), and spec
§2.5 says nothing of an allocation's contents. So the runtime zeroes the
control block's first word before it makes a heap, because
`UnsafeHeap.ready()` trusts that word to read zero until the heap is made.
`extend` writes every word it relies on. A share holds whatever its range
last held: the board's power-on contents, or a share this process gave and
got back. The heap never returns bytes, so no heap data reaches a share.

## 2. The kernel: a pool's two ends, and growing in place

### 2.1 Allocating from the back

Today an allocation is the front of the first free range that holds it, and
otherwise the front of the pool's unspent frontier (`pool_cut`, spec §2.5).
A returned range coalesces with its free neighbours, and one that ends where
the frontier begins folds into it (`pool_return`, `pool_fold_frontier`).

**`PoolOp.Allocate` gains a third argument, `from`.** It is today's unused
third word, so the op's number and its other answers stay:

- **`0`, `Any`:** today's rule.
- **`1`, `Front`:** the front of the frontier only. The allocation an
  extendable `Memory` starts from (§2.2).
- **`2`, `Back`:** the highest free range whose aligned run holds `len`,
  cut from its top (last fit); and only when none does, the top of the
  frontier. Cutting from a range's top shortens the range in place and needs
  no free-list node.
- **any other word** is the `BadArg` fault, as an unaccepted granularity is.

`NoMemory` means what it means today: nothing the argument allows can serve
`len`.

**The frontier folds at both ends.** Bytes are now cut from both ends of
the frontier, so a returned range may touch it from above as well as from
below:

- a range that begins where the frontier ends extends it upward, as one
  that ends where it begins extends it downward;
- the fold then takes in a listed range touching the new bound;
- `pool_fold_frontier`'s premise ("only the last node can touch the
  frontier: bytes are cut from the front") becomes "the nodes just below
  and just above the frontier".

**How freed shares are reused.** Shares freed out of order leave free
ranges above the frontier. A `Back` allocation takes the highest one that
holds it, so the back stays packed toward the pool's end. A freed range that
touches the frontier folds back into it, where it serves either side again.

**Rights.** A `Front` cut is minted with `MemoryRight.Extend` if the pool
handle carries the bit. An `Any` or `Back` cut never is: the bytes after a
back allocation belong to the back, and an `Any` cut's need not be free.
`available` does not change; its totals count the frontier once, whichever
end is spent.

`PoolOp` numbers 2 and 3 stay reserved for `Split` and `Revoke`; this adds
no op.

### 2.2 Growing a `Memory` and its `Mapping` in place

**`MemoryOp.Extend(len)`** (op 1), on `MemoryRight.Extend` (bit 11, the
next unassigned bit after `MapExecute`). A pool carries it for its
allocations at the same bit, as it carries `Map`. `pool_rights()` mints it,
because a bit not minted at boot never appears later. `memory_rights()`, a
boot allocation's set, does not: a boot allocation has no pool to extend
from. The answers, in order:

- without `Extend`, the `AccessDenied` fault;
- a `len` of zero, or one that is no whole number of the allocation's
  granularity, the `BadArg` fault;
- `NoMemory` unless the `len` bytes after the allocation are the front of
  its pool's frontier: either another `Front` allocation or the back side
  took them, or the pool has fewer free bytes there;
- then the allocation's `len` grows, the frontier's front moves up, and the
  pool's outstanding bytes grow by `len`.

No object is made and no quota is charged: bytes are the pool's ledger
(spec §2.9). Rows already granting the allocation keep their own bounds.

**`MappingOp.Grow(len)`** (op 2), on `MappingRight.Grow` (bit 10, after
`Base`), which `mapping_rights()` mints. The answers, in order:

- without `Grow`, the `AccessDenied` fault;
- a husk (unmapped) the `BadState` fault, as `Unmap` answers it;
- a device row, or a `len` that is zero or off the protection grain, the
  `BadArg` fault;
- a `Gone` target, `Revoked`;
- `NoMemory` when the row's new top would pass its allocation's end. The
  holder of a `Mapping` may not hold the `Memory` and cannot read its
  bounds, so this is a status;
- on a translating target, `NoResource` when the next addresses meet
  another row or leave the grant window, as `map` answers;
- then the row's top rises by `len` over the allocation's next bytes, and
  the domain is reloaded if it is the installed one (spec §2.5's
  live-domain rule).

The row is still one row, so the `Mapping` quota row does not move, and
`maps` on the allocation does not change.

**`MemoryOp.Map` gains a third argument, `room`,** today's unused word: the
address space to keep free above the row on a translating target.

- `free_user_va` treats a row's `[base, top + room)` as claimed, so a later
  `map` is placed past the room.
- `Grow` within the room cannot meet a row.
- The room is recorded in the row (`GrantRow.room`) and shrinks as the row
  grows.
- On an identity target `room` is recorded as zero, since a row's addresses
  are its bytes'.
- A `room` off the grain is the `BadArg` fault; one that takes the row past
  the window is `NoResource`.
- The typed form is `Process.map(memory:access:room:)` with `room`
  defaulting to zero, so every caller today is unchanged.

### 2.3 Per tier

- **riscv32 (PMP).** A RAM row is a TOR pair (`prot_region`): a lower bound
  and a top of any four-byte multiple. `Grow` moves the top and the domain
  replays it. One numbered region whatever its size.
- **arm64 (page tables).** `Grow` maps the next pages of the allocation at
  the next virtual addresses. Both runs are contiguous, so the row stays one
  `GrantRow` (base, top and physical base). `room` keeps the addresses
  free. The 4 MiB window bounds a heap's reach.
- **The S3 and riscv32-flat (Flat).** The row's top is recorded and
  `prot_*` installs nothing, so no protection entry is spent at any size.
  The grant record's row and the `Mapping` row are still counted.

### 2.4 The spec text

**Spec §2.5, `allocate`'s bullet,** after "Its answers, in the order they
are decided":

> `allocate(len, granularity, from)`: `from` says where the bytes come from.
> `Any` (0) is the rule below. `Front` (1) takes the front of the pool's
> unspent frontier and nothing else. `Back` (2) takes the top of the
> highest free range that holds `len`, and only when none does the top of
> the frontier. Any other word is the `BadArg` fault. A `Front` allocation
> is minted with `MemoryRight.Extend` when its pool handle carries it, and
> no other allocation ever is.

**Spec §2.5, "Where the bytes come from":** append

> A pool so has two ends: allocations from the front and from the back meet
> in its frontier, and a returned range that touches the frontier from
> either side folds into it.

**Spec §2.5, the `Memory` section:** "one op, `Map`" becomes "two ops,
`Map` and `Extend`", and after `map`'s bullets:

> - **`extend(len)` grows an allocation in place.** It spends
>   `MemoryRight.Extend` (bit 11, carried by a pool for its allocations) and
>   takes the `len` bytes after the allocation from the front of its pool's
>   frontier, answering `NoMemory` when they are not there. It makes no
>   object and charges no quota; rows that grant the allocation keep their
>   bounds. Only a `Front` allocation carries the right, and a boot
>   allocation never does.

**Spec §2.5, `map`:** "`map(process, access)`" becomes "`map(process,
access, room)`", with

> `room` is address space kept free above the row on a translating target,
> for the row to grow into; it is recorded as zero on an identity target.

**Spec §2.5, the `Mapping` section:** "Two ops" becomes "Three ops", adding

> `Grow` (on `MappingRight.Grow`) raises the row's top over its
> allocation's next bytes, which the allocation must already hold
> (`NoMemory` otherwise), and on a translating target into its room or
> other free addresses (`NoResource` otherwise). The row stays one row: the
> quota and the allocation's row count do not change.

**Spec §3's rights tables** gain the two bits; `pool_rights()` and
`mapping_rights()` gain them.

## 3. The runtime's heaps

### 3.1 Adoption

After the drain, `BootSet(process:)` takes every record under a heap tag
(`HEAP_TAG + slot`, slot 0 the default heap, 1 to `MAX_HEAPS - 1` named;
`HEAP_TAG` the reserved range's second tag after `CONSOLE_TAG`, SO-124
§3.1; `MAX_HEAPS` 4, a policy number; the constants in `bootplan`, on which
`sos` depends) and adopts it:

1. **The pool's rights,** read with `INFO_OP`: without `Allocate`, `Map`,
   `Transfer` or `Extend`, panic `sos: heap SLOT's pool lacks RIGHT`.
2. **The Process sibling,** once per process. Read the drain's Process
   handle's rights; without `Mint`, panic `sos: the Process handle lacks
   Mint, so no heap can be adopted`. Then mint a sibling with the keep mask
   `ProcessRight.Map`. A mint is capped by the Process ceiling (spec §2.7),
   so a sibling without `Map` panics `sos: the heaps' Process handle lacks
   Map (the Process ceiling)`, and a refused mint panics `sos: no handle for
   the heaps: DESCRIBE`.
3. **The pool's length and grain.** Read with `Pool.available(0)`, while
   nothing is taken: `total` is the pool's length, which sizes the class
   table; the lowest bit of `granularities` is its grain, the growth step's
   unit (§4).
4. **Install** the descriptor under the heap's lock, with no region yet.

`DESCRIBE` is the status's `describe()`. The texts are made in `sos`, and
each panic ends the process `Faulted (Panic)`. These are the heaps' only
panics. Each checks a right the launcher chose, before the program runs,
which would otherwise be a kernel fault with no text.

A heap maps nothing at adoption: a heap the program never allocates from
spends no `Memory`, no `Mapping` and no slot, and all of its pool is for
shares.

### 3.2 The first growth

The heap's first growth allocates `Front`, which on the runtime's own pool
is always the pool's first bytes, whatever shares have taken from the back:

1. **Allocate** the first size (§4) with `from = Front`.
2. **Read the Memory's rights;** without `Map` or `Extend` (the `Memory`
   ceiling), release it and refuse (`Rights`).
3. **Map** read-write through the Process sibling, with `room` the pool's
   length less the first size.
4. **Read the Mapping's rights;** without `Grow` (the `Mapping` ceiling),
   unmap, release and refuse (`Rights`). Then read `Base`.
5. **Zero the control block's first word** (§1.6) and make the heap over
   `[base, base + size)`.

The runtime keeps the Memory and the Mapping for the process's life.

### 3.3 Every later growth

1. `Memory.extend(n)`. `NoMemory` refuses (`Spent`): the back side or the
   pool's end is at the heap's front.
2. `Mapping.grow(n)`. A refusal here, which the room makes unreachable on
   arm64 and which the identity tiers never answer, leaves the `Memory`
   longer than its row. The heap still serves only from its row, so it is
   refused (`Rights` or `Spent`, the status saying which) and the bytes stay
   the heap's, unused.
3. `heap.extend(base + new_size)`: the old sentinel's header becomes a
   free block's over the new bytes, merged with a free block before it
   (`PREV_FREE`), with a new sentinel at the new end. Constant time.

All of it runs inside the heap's lock, as every op it makes answers at once
or faults the process inside the kernel. None suspends and none allocates.
The code is check-free Saw over the floor's raw calls, behind a per-side hook
`sos_rt_grow(slot, need, ...)` that the kernel answers "no".

### 3.4 `heap.saw`

The heap stays one region. It gains:

- **`largest`:** the class table is sized for the pool's length, so no
  block a growth makes outgrows it;
- **`extend(new_end)`:** as in §3.3 step 3, the region's `size` rising with
  it, so `holds` checks against the current end with no other change.

### 3.5 The front never moves down

The heap's front only rises. Once it meets the back's lowest live
allocation, the heap is spent, and it stays spent while that allocation
lives. A share freed higher up returns to the free list, where `Back`
allocations reuse it, but the heap never can: it grows only into the bytes
directly above its front. When the lowest share itself comes home, its
range folds into the frontier (§2.1), and the heap can grow again. This is
a known property of the shape, not a problem to solve. A product that
shares a lot sizes the pool for the heap's peak and the shares together.

### 3.6 Why a growth was refused

A refusal is a null, so each heap records its reason:

```saw
let info = Heap<DATA>().info()  // HeapInfo
```

`HeapInfo` carries:

- the heap's size and its pool's length;
- the last refusal: a `HeapRefusal` of `Never`, `Spent`,
  `Quota(QuotaKind)`, `NoResource` or `Rights`.

The cases assert it (§10).

## 4. The growth step, per board

### 4.1 The rule, and where its numbers come from

A growth takes the request's need (its block, plus the region's overhead
the first time), rounded up to the pool's grain, and at least
`MIN_GROWTH`. `MIN_GROWTH` is 1 KiB or one grain, whichever is larger.
The first growth is at least the control block and one block. The grain is
the pool's own: the lowest bit of `available`'s `granularities`
(spec §2.5).

- **arm64:** a page (4 KiB), so a growth takes whole pages, at least one.
- **riscv32:** four bytes, so a growth takes at least 1 KiB.
- **The S3:** four bytes, at least 1 KiB.

Nothing needs a board fact: the kernel already answers the grain, and
`rt/common` stays arch-free with no number of its own. `MIN_GROWTH` keeps a
program that allocates in small steps from making a syscall pair per
allocation; it costs at most 1 KiB of the pool to the heap that the shares
could have had.

### 4.2 Doubling on riscv32: weighed, not recommended

The owner asked whether riscv32's heap could keep doubling, so that its slot
is a NAPOT region. A NAPOT row is a power-of-two size on a base aligned to
it.

- **What it demands of placement.** The heap's base, the pool's front, must
  be aligned to the largest size the heap can reach: the largest power of
  two not above the pool's `len`, since the heap never outgrows its pool.
  The builder would derive that alignment from `len` and place the pool
  row on it, refusing by name a range that cannot place it ("the pool of
  heap `data` of `logger` needs a base aligned to 0x4_0000, which range
  `sram` cannot place"). No `align` key: `len` is always declared, so the
  maximum is always known, and a second number would only have to agree
  with the first.
- **How the last doubling ends.** A doubling takes as many bytes as the
  heap already holds. So the heap stops at the largest power of two that
  fits below the back side's lowest allocation, or the pool's end. The bytes
  between that and the back side are never the heap's; they stay for
  shares. A heap may use only a little over half of what its pool could
  give it.
- **What it would save:** nothing on this HAL. `prot_device` puts a NAPOT
  entry in "the pair's high one, where `prot_region` puts a TOR region's
  permissions, and the pair's low entry stays disabled": a NAPOT row spends
  the same numbered region, one of `GRANT_ROW_BUDGET`'s 8, as a TOR pair
  does (`hal/riscv32-common/kernel/lib.saw`). The budget is counted in
  pairs, so it would not rise.
- **TOR** takes any size, grows by the grain, wastes nothing and needs no
  alignment beyond the grain. It costs the same one region on this HAL.

**Recommendation: TOR**, as RAM rows are today (Q1). Doubling becomes worth
its alignment and its waste only if the HAL packs NAPOT rows one entry each
and the budget counts entries. That is a HAL change of its own, and with it:

- a board fact `heap-growth = "double"` in `board.toml`, held by `sosimage
  board` to a HAL constant compiled as its other numbers are;
- the runtime told through a link symbol in every script that links
  `support.c` (the user scripts, the four kernel scripts, and the eight
  test scripts the review's t3 lists);
- the builder's alignment rule above.

## 5. Choosing a heap, sharing, and refusals

### 5.1 The global allocator

- **alloc:** the default heap if adopted, growing as it needs; then the
  built-in heap. Each step holds one lock and gives it back before the
  next. The answer is null when neither serves.
- **dealloc:** to the built-in heap when the block lies in the arena's
  bounds, otherwise to the default heap. A bad free panics, after the lock
  is given back, with `bad free at ADDRESS`.

### 5.2 Named heaps: `sos.Heap<SLOT>`

```saw
public struct Heap<const SLOT: Int> {}
// static_assert(SLOT >= 0 && SLOT < MAX_HEAPS), checked per instance

extension Heap<SLOT>: Allocator { ... }
extension Heap<SLOT> {
    public func share(&self, len: UInt) -> Result<Memory, SosStatus>
    public func info(&self) -> HeapInfo
}
```

```saw
import sos.{Heap}

static DATA: Int = 1           // [heap.data] slot = 1

var log = Vector<UInt8, Heap<DATA>>()
try log.push(byte) catch { drop_oldest(&var log) }
```

- **What the pin offers.**
  - `Vector<T, A>`, `Box<T, A>`, `Map<K, V, A>` and `Set<T, A>` take an
    allocator parameter.
  - A const generic argument may be a named constant.
  - One `sawc mono --check` probe at the pin, declaring `Heap<const SLOT:
    Int>` and instantiating `Vector<UInt8, Heap<DATA>>` and `Box<Int,
    Heap<DATA>>`, passed.
- **A slot outside `0 .. MAX_HEAPS - 1`** fails to build at the
  `static_assert`, naming the bound.
- **A slot in range that was not given** is a heap with no pool. It answers
  null, and its `info()` reads `Spent` with a length of zero.
- **`Heap<0>`** is the default heap without the built-in fallback.
- **A block freed through the wrong allocator** is refused by the heap that
  receives it: `bad free at ADDRESS`.

### 5.3 Sharing

`Heap<SLOT>().share(len:)` allocates `len` bytes from the heap's pool with
`from = Back`.

- **`len` must be a whole number of the pool's grain.** An off-grain `len`
  answers `NoResource` without asking the kernel, which would fault it
  `BadArg`.
- **Its rights** are `Transfer | Map` at most: the pool's carried bits
  without `Extend`, which a `Back` cut never carries, capped by the
  `Memory` ceiling.
  - The program may map it into itself, give it
    (`Process.give(memory:tag:keep:)`, narrowing the receiver's copy), or
    send it in a message.
  - It may not map it executable or mint siblings of it: one share reaches
    one other holder at a time. To keep a view and give one, the program
    maps the share, then gives the handle; its row keeps its view.
- **It competes with the heap** for the middle of the pool. A share the
  pool cannot serve answers `NoMemory`; a heap that cannot grow answers
  null with `Spent`.
- **It costs the caller** a `Memory` row while it lives (creator-pays,
  spec §2.9) and a `Handle` row while it holds the handle. A share over the
  `Memory` row answers `QuotaExceeded`. Those counts are the program's to
  manage.
- **It goes home** under spec §2.5's refcount invariant (§1.5), and a later
  share reuses its range from the free list (§2.1).
- **Share-only:** a heap the program never allocates from maps nothing, so
  all of its pool is for shares. No key says so. The default heap cannot be
  share-only, since the global allocator uses it.

### 5.4 Which refusals panic

| Path | A refusal |
|---|---|
| `__saw_rt_alloc`, `Heap<SLOT>.alloc`, a growth inside either | null, its reason in `info()` |
| `share` | `Err(NoMemory)`, `Err(QuotaExceeded)`, `Err(NoResource)` |
| a bad free | a panic, `bad free at ADDRESS` (the owner's ruling) |
| adoption (§3.1) | a panic per missing right or refused mint, before the program runs |
| `Heap<N>` past the table | a build error |

With no heap at all (a zero built-in heap and no default heap), every global
allocation is null. What the program sees then depends on who allocated:

- an operation with an error channel answers `Err(AllocError)`;
- an interpolation panics `try! failed: allocation of N bytes (align 1)
  failed`;
- `String`'s own operations panic `String: allocation of N bytes (align A)
  failed`;
- the other no-channel places panic with their own texts (sawlang's spec,
  "Where a refusal still panics").

A `main` that returns `Err` with no heap panics too, since the program entry
renders the `Err` through the global allocator unless the compile has
`--no-hidden-alloc` (sawlang's `runtime/ABI.md`, "The program entry"). The
4 KiB default heap avoids it; SO-124's unit 2 holds the heapless case.

### 5.5 The allocator ABI

- **A failure answers null.** No heap's seam panics, aborts or blocks on
  one.
- **Alignment up to 4096** is honoured. A heap answers null when it cannot
  place a block so aligned, after growing.
- **`dealloc`'s size and alignment** are not read: the block's header holds
  its size, and the heap is chosen by allocator or address.
- **Any thread frees any block,** under each heap's lock.
- **Nothing in the allocator allocates or suspends.** A window holds
  check-free calls and, while it grows, syscalls that answer at once.

### 5.6 What needs sawlang

`String`, `StringBuilder` and `Arc` take no allocator parameter. The ask is
`String<A: Allocator = GlobalAllocator>`, formatting into a
`StringBuilder<A>`, and `Arc<T, A>`.

## 6. Declaring heaps, and limits

### 6.1 The syntax

The naming rule:

- **`arena`** is the built-in heap.
- **`heap`** is a pool heap.
- **`len`** is a pool's size.
- **`slot`** is the program's number for a named heap.

The declaration (`process.toml`):

```toml
format = 1
arena = 0x2000                # optional: the built-in heap

[heap.default]                # optional: the global allocator's heap
len = 0x2_0000                # the pool: the heap and its shares together
ram = "fast"                  # optional: it must be fast memory

[heap.data]                   # a named heap
slot = 1
len = 0x4_0000
```

The image:

```toml
[image]
arena = 0x1000                # optional: every process's built-in heap,
                              # unless its declaration states its own
heap-ram = "sram"             # optional: where every heap's pool comes from

[[process]]
name = "logger"
package = "sawos:drivers/logger"
heap-ram = { data = "psram" } # optional: one heap's range, by its name
```

A board range states whether word atomics work there (`atomics = true` in
its `[ram.NAME]`).

- **`arena` becomes optional in a declaration.** Today `Declaration.read`
  requires it (`size`, not `optional_size`), and every `process.toml` in
  the tree states one. The built-in heap's size is the declaration's
  `arena`, else `[image] arena`, else 4 KiB, written as `ARENA_SIZE` in the
  process's script. If SO-142.p3 makes the key optional, this order stands;
  otherwise unit 6 makes it so.
- **A pool's range:** the process's `heap-ram` entry for the heap, else the
  image's `heap-ram`, else the board's `[ram.default] pool`.
- **No growth keys.** The step is the pool's grain (§4.1), and a heap maps
  nothing until it is used, so there is no up-front size to declare.

### 6.2 What the builder checks

In the declaration reader:

- a heap name that is no Saw identifier;
- a `slot` on `default`; a named heap without a `slot`, with a slot of 0
  or of `MAX_HEAPS` or more, or with a slot given twice;
- a `ram` other than `fast`;
- a declared `[[boot]]` tag in the reserved range.

Where the image is read or planned, with the board's grain. `board.toml`
carries no grain, so `Declaration.check_against` cannot make these checks:

- a `len` the word cannot hold, or that is no whole number of the grain;
- a `len` below the heap's first growth: its control block, sized for
  `len`, and one block;
- a `heap-ram` entry naming no heap of the declaration, or a range that is
  not a process range;
- a pool in a range that is not fast, for a heap that declares
  `ram = "fast"`;
- **the atomics fact:** a pool in a range without `atomics = true`. The
  gate boards' process ranges and the S3's SRAM state it: riscv32 and arm64
  under QEMU have atomic instructions, and `ABI.md` §8 shows `s32c1i` at a
  DRAM address on silicon. The S3's PSRAM states it only after a board stage
  shows `s32c1i` there.
- **arm64's window:** a child links 2 MiB into the 4 MiB grant window, and
  every row with its room lies above the image region. So the process's
  region plus every heap's `len` must fit in the 2 MiB above it.

The rows, held where `check_child` holds a child's, with `H` the number of
heaps:

- **`Mapping`:** at least the windows plus `H`. This is a new lower bound;
  `check_child` holds only the upper one today. A heap holds one row
  whatever its size, so on riscv32 the 4 regions an image leaves are shared
  by windows and heaps.
- **`Memory`:** at least `H`.
- **`Handle`:** the floor `check_child` holds as SO-124 raises it, plus
  three per heap (its pool, its `Memory`, its `Mapping`, all kept) and one
  for the Process sibling.
- **The image's Memory slab:** besides the boot rows, which `limits.saw`
  already counts (the heap pools among them), one slot per heap. This is new
  plumbing: `check_plan`'s objects-of-a-kind walk counts Process, IoMemory,
  Pipe and Interrupt objects only.
- **Root's handle table at boot:** each heap's pool is a boot row root
  holds until it gives it, and root's step count meets it as it meets every
  pool row.

Shares are not budgeted: their bytes compete with the heap inside the
declared `len`, and their rows are the program's.

The plan gives each heap's pool as `Source.Heap`: the pool row `from`,
after the declared records, with the keep mask `PoolRight.Allocate | Map |
Transfer | Extend`.

## 7. Locks and threads

- **One `SpinLock` per heap:** the built-in heap's (`ARENA`), and one per
  slot. No path holds two.
- **The panic path stays off every lock.** Windows hold check-free calls; a
  bad free panics after the release; adoption's panics are raised with no
  heap lock held.
- **The lock is what the table's safety rests on,** not a single thread. A
  `_start` package can make threads before it drains (`tests/arena-race`
  never drains) and may drain late. So adoption stores each descriptor under
  its lock, a growth runs under it, and the routing reads only the arena's
  fixed bounds. In a SO-124 `main` program adoption also runs on the only
  thread.
- **A growth holds its heap's lock across a few syscalls** that answer at
  once. A spinner meanwhile spins as it does while a holder is preempted,
  and the hazard `ARENA`'s doc states applies to each lock.
- **`share` takes no heap lock:** the pool's ops are the kernel's and
  atomic, and the heap's front and a share's back are cut by the kernel
  from the one frontier.
- **Two heaps never contend.**

## 8. Boards

- **riscv32 (`virt`, PMP).** One TOR region per heap, whose top rises with
  each growth (§2.3). An image leaves 4 regions for windows and heaps.
- **arm64 (`virt`, MMU).** One row per heap. Its `room` keeps the next
  addresses free, and growth takes whole pages. The window's bound is the
  builder's.
- **riscv32-flat and the S3 (Flat).** One recorded row, no protection
  entry, and growth at the grain. The process could reach a pool's bytes
  unmapped, but no op says where they are. No heap case asserts a hardware
  denial, so the flat profile excludes none.
- **The S3's PSRAM.** From SO-102:
  - PSRAM at `0x3D00_0000`, 8 MiB, through the cache MMU, on a board
    `esp32s3-r8` with a slow process range `[ram.psram]`, once SO-102's
    units C and D land;
  - no `atomics = true` there until a board stage shows `s32c1i` on a PSRAM
    address under the ROM's `ATOMCTL`;
  - the copy doors read a buffer there with interrupts masked, a serial
    transfer per cache miss, measured in unit 7.

  The owner's example is `[image] heap-ram = "sram"` with the logging
  process's `heap-ram = { data = "psram" }`.

## 9. Migration and spec text

**No existing process changes.**

- Each process keeps its built-in heap, at the 4 KiB default once
  SO-142.p3 has migrated the tree, or at its declared size.
- `map`'s new argument defaults to zero, and `Allocate`'s third word was
  zero already, so every caller keeps today's behaviour.
- Every image that links `sosrt` or the kernel moves. No transcript moves
  but the new cases' rows.

**The runtime's spec text,** beside §2.4's kernel text:

**Spec §5c, the paragraph "Where the floor is":**

> A process may also have heaps over pools its launcher gives it, which the
> runtime holds for the process's life: a default heap, which serves the
> global allocator ahead of the arena, and named heaps a program allocates
> from explicitly. A heap grows up from its pool's front, one allocation and
> one row extended in place, and never gives memory back; the program takes
> shareable memory from the pool's back. A heap that cannot grow answers
> null. The runtime allocates nothing before the program runs.

**Spec §12, the launch convention:**

> A process that declares heaps gets one pool for each, under the runtime's
> heap tags, given with `PoolRight.Allocate | Map | Transfer | Extend` and
> nothing else.

## 10. Units of work, in order

Each unit is one patch, gated by `make sos-test sos-smoke-esp32s3`; the
ESP32-C3 builds nothing now. Each lists its mutants, the review standard:
every check and refusal goes red when removed or inverted, or is named
equivalent with the reason.

**Unit 1: the allocation-free drain.** Merged (`be9c9bd9`).

**Unit 2 (kernel): a pool's two ends.** Needs nothing.

- The changes:
  - `Allocate`'s `from`;
  - the last-fit cut from a range's top and the frontier's top;
  - the frontier's fold at both ends;
  - `Extend` withheld from `Any` and `Back` cuts (the bit itself arrives in
    unit 3);
  - spec §2.4's `allocate` and "Where the bytes come from" text.
- Cases (root packages on riscv32, arm64, riscv32-flat and the S3):
  - **`pool_back`:** a fresh pool. A `Back` allocation and a `Front` one,
    each mapped and written with its own pattern, sit at the pool's two
    ends: on the identity tiers `Mapping.base()` reads the pool's last
    bytes and its first; on arm64 `available` reads the middle.
  - **`pool_back_reuse`:** three `Back` allocations freed second, first,
    third. A fourth of the first's length lands in the highest hole, read by
    its contents' absence of the old pattern on identity, and by
    `available`'s `largest` everywhere. With all freed, `available` reads
    the pool whole.
  - **`pool_meet`:** `Front` and `Back` until the frontier is spent.
    `Front` answers `NoMemory` while `Any` still serves from a freed hole.
  - **`pool_from_bad`:** a `from` of 3 is the `BadArg` fault.
- Mutants:
  - first fit for `Back` (`pool_back_reuse` lands low);
  - no fold from above (`pool_back_reuse`'s whole pool reads short);
  - `Front` falling back to the list (`pool_meet`);
  - the `from` check dropped (`pool_from_bad`).

**Unit 3 (kernel): growing in place.** Needs unit 2.

- The changes:
  - `MemoryOp.Extend` and `MemoryRight.Extend` (carried by pools,
    `pool_rights()`);
  - `MappingOp.Grow` and `MappingRight.Grow`;
  - `map`'s `room` and `GrantRow.room` in placement;
  - per tier, the row's top raised and the domain reloaded;
  - spec §2.4's `Memory`, `map` and `Mapping` text, and spec §3's rights.
- Cases (the same four boards):
  - **`memory_extend`:** a `Front` allocation mapped with `room`, extended
    and grown three times. The process writes and reads the last byte each
    time, and its `Mapping` used row stays one.
  - **`memory_extend_met`:** `Extend` answers `NoMemory` once a `Back`
    allocation sits at the front's end. When that allocation is freed, it
    succeeds.
  - **`mapping_grow_past`:** `Grow` past the allocation answers `NoMemory`.
  - **`mapping_grow_room`** (arm64): a second `map` lands past the first's
    room, and a `Grow` past the room meets it with `NoResource`.
  - **The faults:** `Extend` on a `Back` allocation (no right), `Extend` of
    zero, `Grow` on a device row and `Grow` on a husk.
  - **`extend_isolation`** (riscv32 and arm64, isolated only): a read one
    grain past the grown top faults.
- Mutants:
  - `Extend` not checking that the frontier starts at the end
    (`memory_extend_met`);
  - `Grow` not checking the allocation's end (`mapping_grow_past`);
  - the row's top raised without the reload (the last byte faults on the
    isolated tiers);
  - `room` ignored in placement (`mapping_grow_room`);
  - the `Mapping` quota charged on a `Grow` (`memory_extend`'s row count).

**Unit 4: `sosrt.heap`'s `largest` and `extend`** (hosted). It can run
beside units 2 and 3.

- Tests:
  - a table sized for 64 times the first region serves, after extensions,
    a block of half of it;
  - `extend` merges with a free last block and appends after a used one;
  - `holds` takes the new last block, a region ending on the granule
    included, and refuses past the new end;
  - 200,000 random steps with an `extend` every thousand.
- Mutants:
  - the table sized by the first region;
  - `extend` not merging with a free last block;
  - the old sentinel left as a header of size zero;
  - `size` not raised (`holds` refuses the new block).

**Unit 5: the heap table, adoption, growth and shares.** Needs units 3 and
4.

- The changes:
  - the table and its locks;
  - the global seams' order and routing;
  - `BootSet`'s adoption;
  - `sos_rt_grow`;
  - `Heap<SLOT>` with its assert, `info()` and `share`;
  - `bootplan`'s heap tags.
- Cases (hand-written launchers on the four boards, giving children pool
  rows under heap tags with the keep mask):
  - **`heap_grow`:** arena 0. The child takes growing blocks; `info()`
    reads its size rising, and its `Mapping` and `Memory` used rows read
    one each throughout.
  - **`heap_named`:** arena 0, a default and a data heap. `Vector<UInt8,
    Heap<1>>` fills until `push` answers `Err(AllocError)`; `info()` reads
    `Spent`. A `Vector` built meanwhile comes from the default heap, whose
    `info()` shows it.
  - **`heap_fallback`:** a default heap whose pool holds one growth and an
    8 KiB arena. The next block comes from the arena; both are freed and
    the child exits clean.
  - **`heap_dirty_pool`:** the launcher maps the pool's front, writes
    `0xFF` over it, unmaps and gives the pool. The child's heap serves and
    frees cleanly (the zeroed first word).
  - **`heap_share`:** a 4 KiB share with rights `Transfer | Map`. The child
    maps and writes it and gives it to a sibling, which reads it. The
    child's heap then grows up to the share's front and `info()` reads
    `Spent`. When the sibling drops the share, the child's next growth
    succeeds (§3.5).
  - **`heap_share_reuse`:** two shares, the higher freed. A third of its
    length reuses its range, and the heap's room is unchanged.
  - **`heap_share_only`:** a data heap the child never allocates from;
    `info()` reads size 0 after two shares, and its `Mapping` used row is
    unchanged.
  - **`heap_share_grain`:** an off-grain `len` answers `NoResource`.
  - **`heap_refusals`, one child per reason, read from `info()`:**
    - a `Mapping` row at the windows only (`Quota(Mapping)`);
    - a `Memory` row of zero (`Quota(Memory)`);
    - a full `Handle` row (`Quota(Handle)`);
    - a `Memory` ceiling restricted before the first growth (`Rights`).
  - **`heap_rights`, one child per adoption panic:** the pool without
    each of its four bits, a Process handle without `Mint`, a Process
    ceiling without `Map`.
  - **`heap_missing_slot`:** `Heap<2>` with no slot 2 given.
  - **`heap_slot_bound`:** `Heap<4>`, a build error.
  - **`heap_wrong_free`:** a block freed through the wrong allocator.
  - **`heap_zero_spent`:** a heapless child whose push answers
    `AllocError` and whose interpolation panics with `try! failed:
    allocation of`.
  - **`heap_race`** (riscv32, the short tick): `arena_race`'s workers on a
    default heap, holding a lock mutant by `arena_race`'s argument.
- Mutants:
  - adoption dropped;
  - each rights check dropped (a growth's `Rights` null or the kernel's
    fault replaces the named text);
  - `Front` replaced by `Any` (`heap_share`'s growth after the share is
    freed lands elsewhere and the next `Extend` fails);
  - the growth's `extend` not merging;
  - the control word not zeroed (`heap_dirty_pool`);
  - the fallback or the routing dropped (`heap_fallback`);
  - `Back` replaced by `Any` in `share` (`heap_share`'s heap meets the
    share at once);
  - each refusal folded into another (`heap_refusals`);
  - the lock not taken (`heap_race`).
- Equivalent on the gate:
  - the handling of a `Grow` refusal after an `Extend`, which the room
    (arm64) and the identity tiers make unreachable;
  - the order of the two locks in the fallback.

**Unit 6: the builder and the stock root.** Needs unit 5.

- The changes:
  - the declaration's `[heap.NAME]` and an optional `arena`;
  - the image's `arena` and `heap-ram`;
  - the boards' `atomics`;
  - one pool per heap;
  - `Source.Heap` and its keep mask in root;
  - every check of §6.2, including the new `Mapping` floor and the Memory
    slab's plumbing;
  - §9's spec text.
- Tests:
  - each refusal by name, the plan's records and keep mask, the pool rows
    and the script's `ARENA_SIZE` (builder tests);
  - every description reading clean;
  - described images on the four boards whose child, declared with
    `arena = 0`, a default and a data heap, fills, shares and exits clean.
- Mutants:
  - the keep mask narrowed or widened (the share's rights, or an adoption
    panic);
  - each floor term dropped;
  - the atomics check dropped;
  - the image's `arena` read above the declaration's.

**Unit 7: a data heap in the S3's PSRAM.** Needs unit 6, SO-102's units C
and D, and a board stage, run on the part with the owner's word, showing
`s32c1i` on a PSRAM address.

- `atomics = true` on `esp32s3-r8`'s PSRAM range.
- A smoke case with a data heap in PSRAM and the default heap in SRAM.
- The masked-latency measurement on the part.

Order:

- units 2 and 4 can land now, beside each other;
- unit 3 follows unit 2;
- unit 5 follows units 3 and 4;
- unit 6 follows unit 5;
- unit 7 follows unit 6 and SO-102.

## Decided

1. **A pool has two ends:** `Allocate`'s `from` (`Any`, `Front`, `Back`),
   last fit for `Back`, the frontier folding at both ends; no new `PoolOp`.
2. **Growing in place:**
   - `MemoryOp.Extend` (bit 11, carried by pools, never on a boot or
     non-`Front` allocation);
   - `MappingOp.Grow` (bit 10);
   - `map`'s `room`, so a translating target keeps the next addresses free.
3. **A heap is one region in one slot, and forever;** no trim, no return.
4. **The growth step is the pool's grain,** at least `MIN_GROWTH`; no board
   fact on TOR.
5. **A heap maps nothing until it is used,** and its first growth is a
   `Front` cut.
6. **Shares are `Back` cuts with `Transfer | Map`,** ordinary `Memory`
   under spec §2.5's invariant; `share` takes no heap lock.
7. **The runtime keeps each heap's pool, `Memory` and `Mapping`, and one
   Map-only Process sibling.**
8. **Every refusal to allocate is null,** its reason in `info()`; the panics
   are a bad free and adoption's checks.
9. **`Heap<SLOT>` past the table is a build error.**
10. **Names:** `arena`, `[heap.NAME]`, `len`, `slot`, `ram`, `heap-ram`,
    `atomics`; no growth keys and no `align` key.
11. **The builder** holds one `Memory` and one `Mapping` row per heap and
    makes the grain checks at the image.
12. **The control block's first word is zeroed** before a heap is made.

## Where this departs from the lead's reading

- **"Once the heap's front meets the back's lowest allocation, the heap is
  spent for good, even if shared objects are freed later."** For good only
  while that lowest share lives. When it comes home, its range folds into
  the frontier, which begins at the heap's front, and the heap grows again.
  A share freed higher up helps only later shares (§3.5).
- **The doubling the owner asked about** is specified (§4.2) and not
  recommended: on this HAL a NAPOT row spends the same numbered region as a
  TOR pair, so doubling would cost alignment and up to half the pool for no
  saving in slots.
- **The alignment is derived, as you ruled,** and only for a doubling
  board: the largest power of two not above `len`. That is enough, since
  the heap cannot outgrow its pool; aligning to the power of two at or above
  `len` would also work, and asks more of the range.
- **"Growing a Memory and its Mapping in place" is two ops, not one.**
  Taking the pool's bytes spends authority over the allocation, and widening
  a row spends authority over an address space, as `map` already splits
  them (spec §2.5). `room` is the third piece: without it, arm64 places the
  next `map` directly above the heap and its growth meets it.
- **`Front` is a new `from`,** not today's rule. Today's first fit takes a
  freed hole before the frontier, and a heap that started in a hole could
  never extend.

## Open questions

**Decided by the owner: TOR on riscv32, growing at the grain.** On this
HAL a NAPOT row spends the same numbered region as a TOR pair, so doubling
saves no slot, and it would cost a pool base aligned to a large power of two
and up to half the pool. Doubling becomes worth it only if the HAL packs
NAPOT rows one entry each, a change of its own.

**Decided by the owner: a share carries `Mint` and `Transfer`.** A program
may fan a share out to any number of processes; the share lives until its
last handle and last mapping are gone (spec §2.5). The pool is granted with
`Mint` so its allocations carry it. That also lets code that finds the
pool's word mint pool siblings, which is no new authority: every handle in a
process's table is the whole process's, and "the pool is the runtime's" is
the runtime's convention, not a kernel boundary.
