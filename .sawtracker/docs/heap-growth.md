# SO-142 phase 2: heaps from pools

Revision 2 of the design for the owner's review. Nothing here is built. It
settles what a process allocates from: a small built-in heap in `.bss`, a
default heap from a pool its launcher gives it, and named heaps from pools
of their own, each placed in a RAM range the image chooses. It covers a
start-up that allocates nothing, how the runtime maps the pools before
`main`, how a program chooses a heap, what a refusal is, the declaration and
image syntax, locks, the builder's checks, boards, the migration and the
units.

A bare section sign (§4) is this document's; the spec's sections are written
"spec §N", and SO-124's design (`.leadwork/SO-124/design-r5-live.md`) is
cited as "SO-124 §N". The replies to the review's threads on revision 1 are
in `heap-thread-replies-r2.md` beside this file.

Sources: the owner's answers to revision 1's five questions and the rulings
after them (two heaps for small boards; a small, configurable built-in heap
for every process; start-up allocates nothing); sawlang's allocator ABI as
its lead states it (a failure answers null and never panics, aborts or
blocks; alignment up to 4096; `dealloc` gets the matching size and
alignment; any thread frees any block; no allocation or suspension inside
the allocator); SO-124 unit 1 as it stands for review (`PANIC_OP`, and
`__saw_rt_alloc` answering null); spec §2.5, §2.7, §2.9, §2.10, §5.5, §5.6,
§5b.1, §5c, §11 and §12; `rt/common/src/{heap,lib}.saw`;
`kernel/sysapi/src/{rt,floor,bootset,pool,system}.saw`;
`kernel/abi/src/{status,boot}.saw`; `kernel/core/{dispatch,objects,limits}.saw`;
`root/src/main.saw`, `root/bootplan`;
`tools/sosimage/src/{declaration,imagefile,plan,layout,board}.saw`; each
board's `board.toml`; `hal/xtensa-esp32s3/ABI.md` §8 and §16 and SO-102's
issue; sawlang's `std/{alloc,vector,box,map,set,string,stringbuilder,panic}.saw`
and `LANGUAGE_SPEC.md` at the pin (v0.24.3); and one `sawc mono --check`
probe at the pin (§4.2).

## 0. The shape in one page

- **Start-up allocates nothing.** The boot set's drain stores its records
  in fixed storage inside `BootSet`, bounded by the handle table, so every
  step before `main` (SO-124's start-up, and a `_start` package's own
  drain) runs with no heap at all. A case holds the rule: a `main` program
  and a `_start` package with no heap start, reach their programs and exit
  clean.
- **Three kinds of heap, one TLSF each.** The *built-in heap* is today's
  `.bss` arena, sized at link time and the program's alone. The *default
  heap* is a pool the launcher gives the process. A *named heap* (`data`,
  say) is another pool. Each pool heap is mapped whole before `main`. Each
  heap is one region, one `UnsafeHeap` and one `SpinLock`; `heap.saw` does
  not change.
- **Saw's global allocator** (`String`, `Vector<T>`, interpolation) serves
  from the default heap, then from the built-in heap. A free goes to the
  heap whose range holds the block.
- **A named heap is an allocator type,** `sos.Heap<SLOT>`, conforming to
  std's `Allocator`. At the pin `Vector`, `Box`, `Map` and `Set` take an
  allocator parameter, so `Vector<UInt8, Heap<DATA>>` allocates from the
  named heap. `String` cannot; that is the sawlang ask (§4.4).
- **Every heap that cannot serve answers null.** std makes the null an
  `Err(AllocError)` where it has an error channel and panics where it has
  none (its `String` layer). A process with no heap, a spent heap and a
  named heap it was never given all answer the same way. The only panics are
  a bad free (the owner's ruling) and the start-up's mapping refusals that
  are not "the pool has no bytes" (§3.4).
- **The pools arrive as boot records under reserved tags,** given with only
  `PoolRight.Allocate | PoolRight.Map`. `BootSet(process:)` drains, then
  maps every heap record it holds: reads the pool's free length, allocates
  it all, maps it read-write, reads the base and releases every handle. The
  pool dies at the process's death, by the kernel's rule.
- **The declaration** keeps `arena` for the built-in heap and adds a
  `[heap.NAME]` table per pool heap (`default`, and named ones with a
  `slot`), each with a `len` and an optional `ram = "fast"`. **The image**
  sets every built-in heap's default size (`[image] arena`) and where heaps
  come from (`[image] heap-ram`, and a process's `heap-ram = { data =
  "psram" }`). **The board** states which ranges have working atomics, and
  the builder refuses a heap anywhere else.
- **Migration: no existing process changes.** Each keeps its built-in heap;
  the kernel and root keep only theirs.
- **Five units:** the allocation-free drain; the heap table; adoption and
  `Heap<SLOT>`; the builder and the stock root; a data heap in the S3's
  PSRAM.

## 1. What a process allocates from

### 1.1 The three kinds

| Kind | Memory | Sized by | Serves |
|---|---|---|---|
| built-in | `[_arena_start, _arena_end)` in `.bss`, in the process's own region | the link (`ARENA_SIZE`): the declaration's `arena`, else the image's `[image] arena`, else the package's script | the global allocator, after the default heap |
| default | a pool under `HEAP_TAG`, mapped before `main` | `[heap.default] len` | the global allocator, first |
| named | a pool under `HEAP_TAG + slot`, mapped before `main` | `[heap.NAME] len` | `Heap<slot>` only |

Every heap is one region, so each `UnsafeHeap` is made exactly as the
arena's is today: its control block at its front, sized for its own region
on its first call (`ready()`), its blocks never outgrowing its class table.
There is no region list, no `add_region` after the first and no link-time
length.

### 1.2 The built-in heap stays separate from the default heap

Two ways to relate them were weighed:

- **One heap, two regions.** The built-in heap is the default heap's first
  region and the pool joins it as a second, so blocks from either free into
  the same lists. The class table must then be sized for the larger region
  before the first allocation, `holds` must know both regions, and
  `heap.saw` gains the multi-region code revision 1 described.
- **Two heaps, routed by address** (recommended). Each is its own TLSF. The
  global allocator asks the default heap, then the built-in heap; a free
  compares the block with the arena's linker bounds and goes to the heap
  whose range holds it. Each heap sizes its own table, `heap.saw` is
  unchanged, and a built-in heap of zero bytes is a heap that serves
  nothing.

The routing costs one range compare per free. Since start-up allocates
nothing (§2), the built-in heap holds only the program's blocks.

### 1.3 The kernel and root

- **The kernel** keeps its built-in heap and nothing else. It never
  allocates at run time; its scripts keep placing the arena as now.
- **Root** has no launcher, so nobody gives it pools. Its boot records carry
  region ordinals (spec §12), never a reserved tag, so `BootSet` adopts
  nothing for it. Root's heap is its built-in heap, as today.

### 1.4 Who supplies the memory, and the authority

The launcher, at launch, as boot records (revision 1's option A, which the
owner's first answer confirms: "the launcher doesn't need an allocation
seam"). For each declared heap the process holds a pool it may allocate
from as read-write memory it can never execute (no `MapExecute`), give on
(no `Transfer`) or narrow for anyone else (no `Mint`). The kernel's
contract does not change: `Pool.available`, `allocate`, `map`,
`Mapping.base` and handle release are spec §2.5 as built.

**A heap's pool is the runtime's, used whole.** The owner's second answer
allows processes to pass pools between them. A heap's pool is not one of
those: the runtime allocates all of it before `main` and releases its
handle, so nothing is left to pass. A process that wants a pool to share or
give declares a `memory` record, which the stock root already gives as a
pool (`tests/stock-server`), and manages it and its quotas itself. Q1 asks
the owner to confirm.

### 1.5 Lifetime

Spec §2.5, "What holds a pool": its handles and its outstanding bytes; when
its last handle has gone and its last allocation has come home its slot
frees, and a pool minted at boot has no pool above it, so its bytes go
nowhere. The runtime releases the pool's handle once it has allocated from
it, so the pool is held only by that allocation. The allocation is held by
its grant row once its `Memory` handle is released (spec §2.5's refcount
invariant), and the row goes when the process's domain is cleared at
teardown (spec §2.10). So the pool dies at the process's death, the owner's
fourth answer, by the kernel's rule with nothing added. No region goes back
before then.

## 2. Start-up allocates nothing

### 2.1 The rule, and the case that holds it

Everything that runs before `main`, and before a `_start` package's own
code can allocate, is allocation-free: SO-124's start-up (§2.3) and the
`sos` calls a `_start` package makes to begin (`System(boot_handle:)`,
`System.process_self`, `BootSet(process:)`). So a built-in heap of zero
bytes is a real choice, and the heaps hold nothing the runtime put there.

**The case** (`start_no_heap`, on riscv32, arm64, riscv32-flat and the S3):
a `_start` package linked with `ARENA_SIZE = 0` and given no heap record,
but a System, an Event and a pool, adopts its System, derives its Process,
drains its set, takes each record by tag, prints what it took (a `print`
with `{}` arguments renders on the stack, `rt/common/src/lib.saw`), and
exits clean. A `main` program with the same set does the same; it lands with
SO-124 unit 2, which owns `sosmain`. A start-up step that allocates gets
null, and std turns that into an `Err` the step reports or a panic, so the
case fails at the step, by name.

**Its mutant:** put a `Vector` push back into the drain. The push answers
`Err(AllocError)`, the drain answers `NoMemory`, and the package's drain
fails: red.

### 2.2 The drain: fixed storage in `BootSet`

`BootSet(process:)` grows two vectors today
(`kernel/sysapi/src/bootset.saw`). Three ways to drain without allocating
were weighed:

- **Fixed storage bounded by the handle table** (recommended). Each boot
  record is a handle in the process's own table: a give mints the child's
  entry (spec §12), and the drain hands back its word. So the drain can
  never answer more records than the table holds, `MAX_HANDLES`, which is 32
  on every board (`hal/*/kernel/lib.saw`). `BootSet` keeps each record raw,
  as the kernel's record is (`BOOT_HANDLE_RECORD_WORDS`, three words: tag,
  kind, handle, `kernel/abi/src/boot.saw`), in an inline array of
  `BOOT_SET_CAPACITY` records and a count: 384 bytes on a 32-bit profile,
  768 on arm64. `take(tag)` wraps one record's word into a `BootHandle` (the
  wrapping `boot_handle_into_frame` already does) and clears the word to
  `NO_HANDLE`; dropping the set releases every word not taken.
- **A builder-known bound** (the declaration's record count, carried as a
  const generic `BootSet<N>`). Tighter, but `N` would ride in every
  signature that names the set, and a package the runner builds, or a root,
  has no declaration to take it from.
- **Take by tag without draining.** The kernel keeps the records until they
  are consumed (spec §12), so a `ProcessOp.BootHandleTake(tag)` would need
  no storage at all. It is a kernel op and a spec change for what a fixed
  array does in userspace.

**The bound.** `BOOT_SET_CAPACITY` is published in `sosabi` as 32, and
`kcore.limits` holds `hal.MAX_HANDLES <= BOOT_SET_CAPACITY` with a
`static_assert`, so a board that raises its table raises the capacity with
it. A drain that answers a record past the capacity is therefore
unreachable; `BootSet` still refuses it by name, `sos: the boot set holds
more than 32 records`, a panic, since it would mean a kernel built past the
assert.

**What changes for callers.** `len()` and `take(tag)` keep their meaning.
`entries()` still answers a `Vector`, for a program that lists its set, and
allocates as any program call may; an allocation-free `entry(at:)` sits
beside it. A `BootEntry` gains the record's rights word, read with
`INFO_OP` as the drain meets the record (one ungated call each), so a
program can see what it was given, the records the runtime takes
included. The set is a value of fixed size, moved into `main` once by
SO-124's `take_boot_set`; the copy is the array's size.

### 2.3 What SO-124's start-up does before `main`

Each step of SO-124 §1.2, with what it allocates:

| Step | Allocates | How it goes away |
|---|---|---|
| 1. Re-entry guard and the thread register | nothing | — |
| 2. Check and adopt the System handle (`INFO_OP`, `System(boot_handle:)`, its rights) | nothing; a refusal's panic text is formatted into std's fixed `StringBuilder` over the compiler's stack scratch (`std/stringbuilder.saw`: "caller storage, never grown") | — |
| 3. Derive the Process handle and the main thread's own | nothing | — |
| 4. Drain the boot set | two `Vector`s (the step's own text: "The drain allocates its two vectors from the arena's heap") | §2.2's fixed storage; the sentence goes |
| 5. Give the console record to the write seam (`set.take(CONSOLE_TAG)`, `adopt_console`) | nothing: `take` reads the fixed storage, and `adopt_console` parks a word | — |
| (new) Map the heaps (§3) | nothing: syscalls, `INFO_OP` and words on the stack | — |
| 6. Publish the boot context (`Boot` in the frame, its parked address, the taken word) | nothing | — |
| 8. Flush (`console_sync`'s `Sync`) and 9. exit, after `main` | nothing: the send record and the reply's record are on the stack (SO-124 §4.3) | — |

The heap mapping happens inside `BootSet(process:)`, so SO-124's step 4
gains it without a step of its own. SO-124 unit 2's tests gain the `main`
half of `start_no_heap` and its mutant.

### 2.4 A built-in heap of zero

With no default heap either, the process has no heap: every global
allocation answers null. A `Vector` push answers `Err(AllocError)` and the
program carries on; a `String` built or extended panics in std's `String`
layer with `String: allocation of N bytes (align A) failed`
(`std/string.saw`), which ends the process `Faulted (Panic)` with that text.
A `print` with `{}` arguments renders on the stack and still works. Such a
process is legitimate (a driver that never allocates), so the builder
refuses nothing for it.

## 3. Mapping the heaps before `main`

### 3.1 Where the records are

The launcher gives each heap's pool under a reserved tag, `HEAP_TAG + slot`,
slot 0 the default heap and slots 1 to `MAX_HEAPS - 1` the named ones, in
any order among the other records; the stock root gives them after the
declared records, beside SO-124's console record. `HEAP_TAG` is the
reserved range's second tag (`0xFFFF_FF00` up, `CONSOLE_TAG` first,
SO-124 §3.1). `MAX_HEAPS` is 4, a policy number: a static array of heap
descriptors and locks in every process, raised when a process needs more.

The constants live in `bootplan` beside `CONSOLE_TAG`, and `sos` gains
`bootplan` as a dependency so the builder, the stock root and the runtime
read one copy of the reserved range.

### 3.2 When

`BootSet(process:)` drains into its fixed storage (§2.2), then takes every
record whose tag is a heap tag and maps it, then returns. The set's listing
still names those records, taken, as it names the console's (SO-124 §3.2).
So by the time a `_start` package holds its set, or a `main` program runs,
every heap it was given is mapped, and nothing has allocated yet. Only one
thread exists then: a `_start` package makes threads through handles it has
not yet drained, and SO-124's start-up runs on the main thread alone.

### 3.3 The steps, per heap

With `pool` the record's handle, `slot` its slot, and `proc` the Process
handle the drain was given:

1. **Rights.** Read the pool's with `INFO_OP`: without
   `PoolRight.Allocate`, panic `sos: heap SLOT's pool lacks Allocate`;
   without `PoolRight.Map`, panic `sos: heap SLOT's pool lacks Map`. Read
   `proc`'s: without `ProcessRight.Map`, panic `sos: the Process handle
   lacks Map, so heap SLOT cannot be mapped`. `INFO_OP` is ungated and
   answers at once.
2. **Length.** `Pool.available(0)`. Its `largest` is the longest run an
   `allocate` at the pool's grain could take, a whole number of units by
   definition (spec §2.5). Zero leaves the heap empty (§3.4).
3. **Allocate** `largest` at granularity zero. `NoMemory` (another holder
   took the bytes between the two calls) leaves the heap empty; any other
   refusal panics `sos: heap SLOT: allocate: DESCRIBE`.
4. **The allocation's rights.** They are the pool's capped by the process's
   `Memory` ceiling (`mint_capped`, `kernel/core/objects.saw`): without
   `MemoryRight.Map`, panic `sos: heap SLOT's memory lacks Map (the Memory
   ceiling)`.
5. **Map** into `proc`, read and write. A refusal panics `sos: heap SLOT:
   map: DESCRIBE`.
6. **The mapping's rights,** capped by the `Mapping` ceiling: without
   `MappingRight.Base`, panic `sos: heap SLOT's mapping lacks Base (the
   Mapping ceiling)`.
7. **Base.** `Mapping.base()`; a refusal panics `sos: heap SLOT: base:
   DESCRIBE`.
8. **Release** the Mapping, the Memory and the pool, in that order.
9. **Install** `[base, base + largest)` as heap `slot`'s region, under that
   heap's lock (`sosrt`'s public `adopt_heap(slot, base, len)`, which
   panics `sos: heap SLOT was mapped twice` on a second call for one slot).

`DESCRIBE` is the status's printed form, which is its `describe()`
(`kernel/abi/src/status.saw`): `QuotaExceeded` prints `this process is at
its quota`, `NoResource` `no room for what was asked`. The texts are made in
`sos`, which can name `SosStatus`; `sosrt`, which cannot, makes none. Each
panic ends the process `Faulted (Panic)` through `PANIC_OP`.

Nothing here runs inside a heap's lock but step 9's store. The handles peak
at two more than the records the process holds (the Memory and the
Mapping) during steps 3 to 8 of one heap, and the heap's own record is gone
after step 8.

### 3.4 Which failures panic

The allocator's ABI forbids a panic on an allocation's failure, and every
allocation's failure here is a null (§4). The mapping is not an allocation:
it is the start-up taking what its launcher gave. Its failures split:

- **"The pool has no bytes"** (step 2's zero, step 3's `NoMemory`) is the
  plain no-memory case. The heap is left empty, its allocations answer null,
  and the process starts. The builder makes every pool exactly its heap's
  `len`, so only a hand-written launcher meets this.
- **Every other failure panics,** by name: a missing right (each would
  otherwise be a kernel fault with no text, as the review's t1 shows), the
  `allocate`'s `QuotaExceeded` or `NoResource`, any `map` refusal, and a
  `Base` refusal. Each is a launch or quota configuration the process cannot
  run under, found before `main`.

## 4. Choosing a heap, and what a refusal is

### 4.1 The global allocator

std's `GlobalAllocator` calls `__saw_rt_alloc` and `__saw_rt_dealloc`
(`std/alloc.saw`), and everything that names no allocator goes there:
`String`, `StringBuilder`'s growth, interpolation, `Vector<T>`, `Box<T>`,
`Arc<T>`. `sosrt`'s seams serve it:

- **alloc:** the default heap if mapped, else or then the built-in heap,
  each under its own lock, never both held; null when neither serves.
- **dealloc:** the built-in heap when the block lies in the arena's bounds,
  otherwise the default heap; that heap's `holds` check, then today's `bad
  free at ADDRESS` panic.

### 4.2 Named heaps: `sos.Heap<SLOT>`

std's `Allocator` asks for a field-less struct with `init() -> Self`, built
as `A()` at each call (`std/alloc.saw`), so a heap's identity rides in the
type:

```saw
public struct Heap<const SLOT: Int> {}

extension Heap<SLOT>: Allocator {
    public func alloc(&self, size: Int, align: Int) unsafe -> UnsafePointer<Int8>?
    public func dealloc(&self, ptr: UnsafePointer<Int8>, size: Int, align: Int) unsafe
}
```

A program names its heap by the slot its declaration gives it, as it names
a boot record by its declared tag:

```saw
import sos.{Heap}

static DATA: Int = 1           // [heap.data] slot = 1

var log = Vector<UInt8, Heap<DATA>>()
try log.push(byte) catch { drop_oldest(&var log) }
```

- **What the pin offers.** `Vector<T, A>`, `Box<T, A>`, `Map<K, V, A>` and
  `Set<T, A>` take an allocator parameter, `GlobalAllocator` by default. A
  const generic argument may be a named constant (`LANGUAGE_SPEC.md`, "Const
  generics"). One probe at the pin, `sawc mono --check` on a hosted file
  that declares `Heap<const SLOT: Int>` as above and instantiates
  `Vector<UInt8, Heap<DATA>>` and `Box<Int, Heap<DATA>>` with `static DATA:
  Int = 1`, passed.
- **A named heap's null** is an `Err(AllocError)` to the program with the
  container unchanged, so a log that fills its heap drops old entries.
- **A slot the process was not given** is a heap with no region: it answers
  null, as an empty heap does. The builder gives every declared slot (§5.2),
  so the null means a hand-written launcher left one out.
- `Heap<0>` names the default heap without the built-in fallback.
- **A block freed through the wrong allocator** reaches the heap its
  allocator or its address names, whose `holds` refuses it: `bad free at
  ADDRESS`.

### 4.3 The allocator ABI's constraints

- **A failure answers null,** and nothing in any heap's seam panics, aborts
  or blocks on one (§3.4, §4.1, §4.2). A bad free is not a failure to
  allocate: it panics, by the owner's ruling, after the lock is given back.
- **Alignment up to 4096** is honoured by every heap, as phase 1's TLSF
  honours any alignment. A heap too small to hold a block at the alignment
  asked answers null.
- **`dealloc`'s size and alignment** are not read: each block's header
  holds its size, and the heap is chosen by the block's address (§4.1) or
  by its allocator (§4.2).
- **Any thread frees any block:** each heap's lock serializes its callers,
  whichever thread allocated.
- **Nothing inside the allocator allocates or suspends:** each window holds
  the heap's check-free calls and nothing else, and no syscall runs in any
  window (the mapping is before `main`, §3).

### 4.4 What needs sawlang

- **`String` and `StringBuilder` take no allocator** (`std/string.saw`'s
  `String` has no parameter, and `StringBuilder` grows through
  `GlobalAllocator`, `std/stringbuilder.saw`), so text cannot live in a
  named heap, and interpolation always uses the global allocator. A log held
  as `Vector<UInt8, Heap<DATA>>` works today; one held as `String` does not.
- **`Arc` takes no allocator,** so shared ownership is global-only.

**The ask for the sawlang lead:** an allocator parameter on `String` and
`StringBuilder` (`String<A: Allocator = GlobalAllocator>`, as `Vector` has
it), with formatting into a `StringBuilder<A>`, so `"{}"` can render into a
named heap; and the same parameter on `Arc<T, A>`. Nothing here waits on it.

## 5. Declaring heaps, and limits

### 5.1 The syntax

One name per kind: `arena` is the built-in heap everywhere it appears (its
`.bss` region and its linker symbols already say so), and `heap` is a pool
heap everywhere. A size is `len`, as every range's and pool's is.

The declaration (`process.toml`):

```toml
format = 1
arena = 0x800                 # optional: the built-in heap; 0 for none

[heap.default]                # optional: the global allocator's pool
len = 0x8000
ram = "fast"                  # optional: it must be fast memory

[heap.data]                   # optional: a named heap
slot = 1                      # Heap<1> in the program
len = 0x4_0000
```

The image:

```toml
[image]
name = "logger"
board = "esp32s3-r8"
arena = 0x400                 # optional: every process's built-in heap,
                              # unless its declaration states its own
heap-ram = "sram"             # optional: where every heap comes from

[[process]]
name = "logger"
package = "sawos:drivers/logger"
heap-ram = { data = "psram" } # optional: one heap's range, by its name
```

The board (`board.toml`) states, per RAM range, whether word atomics are
known to work there:

```toml
[ram.sram]
use = "process"
speed = "fast"
atomics = true
base = 0x3FCB_0000
len = 0x4_0000
```

- **A built-in heap's size:** the declaration's `arena`, else the image's
  `[image] arena`, else what the package's script provides (the user
  scripts' `PROVIDE(ARENA_SIZE = 64K)`, or a package's own). The builder
  writes it as `ARENA_SIZE` in the process's script, as it writes a
  declaration's `arena` now (`process_script`). Zero links no arena.
- **A heap's range:** the process's `heap-ram` entry for that heap, else the
  image's `heap-ram`, else the board's `[ram.default] pool`.

### 5.2 What the builder checks

Refused by name, in the declaration reader and against the board:

- a heap name that is no Saw identifier; a `slot` on `default`; a named heap
  without a `slot`, or with a slot of 0 or of `MAX_HEAPS` or more; one slot
  given twice;
- a `len` the board's word cannot hold, or that is no whole number of the
  board's protection grain (a pool serves whole grains, spec §2.5);
- a `len` too small for the heap's control block and one block, computed
  from the board's word as `ready()` computes it;
- a `ram` other than `fast` (the existing rule, now also on a heap);
- a declared `[[boot]]` tag in the reserved range (SO-124 §3.1).

In the image and the plan:

- a `heap-ram` entry naming no heap of the process's declaration, or a range
  that is not a process range of the board;
- a heap whose range is not fast, for a heap that declares `ram = "fast"`;
- **the atomics fact:** a heap whose range does not state `atomics = true`.
  The gate boards' process ranges and the S3's SRAM state it (riscv32 and
  arm64 under QEMU have atomic instructions; `ABI.md` §8 shows `s32c1i` at
  a DRAM address on the S3, on silicon). A range that says nothing is
  refused, so the S3's PSRAM takes no heap until a board stage shows
  `s32c1i` there (§7.3);
- on arm64, a process whose region and heaps together pass the grant
  window: a child links 2 MiB into the 4 MiB window (spec §5b.1) and each
  heap is mapped above the image region, so the region plus every heap's
  `len` must fit in the 2 MiB above it (the review's t9);
- the rows the heaps spend, each held where the plan holds the child's rows
  (`check_child`, `tools/sosimage/src/plan.saw`):
  - **`Handle`:** the floor `check_child` holds, as SO-124 raises it (the
    console record and a `main` program's thread handle, SO-124 §3.1), plus
    one per heap record and two for the Memory and Mapping handles a mapping
    holds while it runs (§3.3);
  - **`Memory`:** at least one per heap, since each allocation stays charged
    for the process's life;
  - **`Mapping`:** at least the windows plus one per heap (the floor already
    assumes each window is mapped); the kernel clamps the row to the grant
    rows the domain has left (spec §2.9);
- an image whose Memory slab cannot hold the boot rows (the heap pools
  among them) and one allocation per heap (`check_plan`'s
  objects-of-a-kind check).

The plan gives each heap's pool as a new `Source.Heap`: the pool row
`from`, given with the keep mask `PoolRight.Allocate | PoolRight.Map`
(`Process.give(pool:tag:keep:)`), after the declared records. The pools are
rows of the region table like every image pool (spec §12), one per declared
heap, each placed in its range.

### 5.3 What a refusal looks like

| Cause | What the program sees |
|---|---|
| Any heap spent, a process with no heap, a slot not given, a pool with no bytes | null: `Err(AllocError)` from `Vector`, `Box`, `Map`, `Set`; std's `String` layer panics, `String: allocation of N bytes (align A) failed`, `Faulted (Panic)` |
| A bad free in any heap | `bad free at ADDRESS`, a panic (today's) |
| A mapping refusal other than "no bytes" | the start-up's named panic of §3.3, `Faulted (Panic)`, before `main` |
| More boot records than `BOOT_SET_CAPACITY` (unreachable, §2.2) | `sos: the boot set holds more than 32 records`, a panic |

A `Faulted (Panic)` end reads `ended status=131086` in the stock root's
report and stops the machine with 64 in root (SO-124 §4.4).

## 6. Locks and threads

- **One `SpinLock` per heap:** the built-in heap's (`ARENA`, as today) and
  one per slot. The global allocator takes the default heap's lock, and on
  a null gives it back before it takes the built-in heap's; it never holds
  two. `Heap<SLOT>` takes its slot's alone.
- **The panic path stays off every lock.** Each window holds only the
  heap's check-free calls, as `ARENA`'s does now; a bad free panics after
  the lock is given back; the start-up's panics are raised while no heap
  lock is held.
- **Each heap's descriptor is written once,** before `main`, on the only
  thread (§3.2), under its lock, and read under it after.
- **Two threads on one heap** contend as two threads on the arena do now;
  the hazard `ARENA`'s doc states (a spinner above a preempted holder under
  fixed priorities) applies to each lock, and no lock is held across a
  syscall.
- **Two threads on two heaps** never contend: a data heap filled by a
  logging thread does not slow the global allocator.

## 7. Boards

### 7.1 The isolated tiers

- **riscv32 (`virt`, PMP).** Each heap's allocation is mapped at its own
  address, inside its pool's row, apart from every process region, and is
  one more PMP region in the process's set. The budget is 8 rows and an
  image spends 4, so a process has 4 for its windows and heaps together.
- **arm64 (`virt`, MMU).** Each heap is placed by the kernel above the
  image region in the 4 MiB grant window; a pool serves whole pages, so
  every heap's `len` is a page multiple (§5.2's grain check), and the
  window's bound is the builder's (§5.2).
- **Both** enforce the access: each heap's row is read-write and not
  executable, and the keep mask means no other row over the same bytes can
  be.

### 7.2 The Flat tier

riscv32-flat and the S3 run the same code. `map` is "trivially true" there
and its row is recorded and counted (spec §5b.1). The process could reach a
pool's bytes without mapping them, but no op tells it where they are
(spec §2.5): `Mapping.base()` is the only answer. No heap case asserts a
hardware denial, so the flat profile excludes none of them.

On the S3 every process range today is the internal SRAM (256 KiB,
`hal/xtensa-esp32s3/board.toml`), shared by every process region and pool,
so a heap there costs the same bytes a built-in heap would. Two heaps pay
off with the PSRAM.

### 7.3 The S3's PSRAM

Assumed from SO-102 (its issue and `ABI.md` §16, which builds the command
layer only): PSRAM at `0x3D00_0000`, 8 MiB, through the cache MMU, on a
board `esp32s3-r8` whose description names it as a slow process range
(`[ram.psram]`) once SO-102's units C (the map and read-back) and D (process
memory in PSRAM) land.

- **Atomics.** `ABI.md` §8 probes `s32c1i` on DRAM and IRAM only, and SO-102
  lists atomics on PSRAM as unverified. A heap's blocks hold whatever the
  program puts there, `Atomic` words included, so the range carries no
  `atomics = true` until a board stage shows `s32c1i` works on a PSRAM
  address under the ROM's `ATOMCTL`; until then the builder refuses a heap
  there.
- **Masked latency.** A buffer in a heap can be handed to a syscall, and the
  copy doors read it with interrupts masked; in PSRAM each cache miss is a
  serial transfer. Unit 5 measures it on the part.
- **The owner's example** (operating memory in SRAM, logs and other
  generated data in PSRAM) is `[image] heap-ram = "sram"` with the logging
  process's `heap-ram = { data = "psram" }`.

## 8. Migration

No existing process changes. Each keeps its built-in heap, sized as now:
the stock root and every root package by their scripts, the harness's
children by the user scripts' default or their own scripts, and the
declared processes (`drivers/`, `tests/stock-*`, the product template) by
their declarations' `arena`. A process gains pool heaps by declaring them.

- **Transcripts** move only where a case is added. Every image that links
  `sos` moves with the drain's storage, and every image that links `sosrt`
  with the heap table.
- **`uart_service_ns16550`'s byte-for-byte check** (`FROM_FILES`) holds
  while its description declares no heap. A heap added to it later needs the
  runner's case to give the same pool rows and records, or the two ELFs
  differ.

## 9. The spec text

The kernel's contract does not change. Three paragraphs describe the
runtime's use of it; the owner reads them with the design.

**Spec §5c, the paragraph "Where the floor is", after "panics through the
ordinary panic path, naming the address; one it cannot tell corrupts the
heap.":**

> A process may also have heaps from pools its launcher gives it: a default
> heap, which serves the global allocator ahead of the arena, and named
> heaps a program allocates from explicitly. The runtime maps each whole
> before the program runs, and allocates nothing itself before then. A
> heap that cannot serve a request answers null.

**Spec §12, the launch convention, after "It gives windows, lines and
endpoints as capabilities":**

> A process that declares heaps gets one pool for each, under the runtime's
> heap tags, given with `PoolRight.Allocate | PoolRight.Map` and nothing
> else.

**Spec §11:** a heap's memory is the process's until it ends, and a heap
in the S3's PSRAM waits on atomics being shown to work there.

## 10. Units of work, in order

Each unit is one patch, gated by `make sos-test sos-smoke-esp32s3`; the
ESP32-C3 builds nothing now. Each lists its mutants, the review standard:
every check and refusal goes red when removed or inverted.

**Unit 1: the allocation-free drain** (`kernel/sysapi/src/bootset.saw`,
`kernel/abi`, `kcore.limits`). Needs nothing. `BOOT_SET_CAPACITY` and its
assert; `BootSet`'s fixed storage, `take`, `entry(at:)`, the release of
untaken words, the capacity's panic. Transcripts hold; images that drain
move. Its tests:

- `start_no_heap` (§2.1), the `_start` half, on riscv32, arm64,
  riscv32-flat and the S3;
- every case that drains a boot set, unchanged (the stock-root images,
  `uart_service_ns16550`, the drivers);
- untaken records released: a child that takes one of three records reads
  its `Handle` row before and after dropping the set, two fewer after.

Mutants: a `Vector` push in the drain (`start_no_heap` red, §2.1); the
release of untaken words dropped (the `Handle` row reads the same); `take`
not clearing its word (a record taken twice: the second take answers a
handle, where the case wants `None`).

**Unit 2: the heap table** (`rt/common/src/lib.saw`). Needs SO-124 unit 1
(the null seam). The built-in heap as one entry of a table of `MAX_HEAPS`
slots, one `SpinLock` each; the global seams' order and the free's routing
by the arena's bounds; `adopt_heap(slot, base, len)`, unused yet. No
process adopts anything, so every transcript holds. Its tests: the arena
cases on every profile and the S3 unchanged (`arena_small`, `arena_reuse`,
`arena_large`, `arena_churn`, `arena_race`, `double_free`, `stray_free`,
`lock_owner`); a root package with `ARENA_SIZE = 0` whose `Vector` push
answers `AllocError` and which exits clean. Mutant: the routing inverted
(every free of an arena block panics `bad free`). The default heap's order
and the built-in fallback have no heap to act on until unit 3, whose cases
hold them.

**Unit 3: adoption and `Heap<SLOT>`.** Needs units 1 and 2 and the reserved
range (SO-124 unit 4, or this unit defines it in `bootplan` if it lands
first). `BootSet`'s mapping of every heap record, §3.3's checks and texts,
`sos`'s dependency on `bootplan`, `sos.Heap<SLOT>`. Its cases are
hand-written launchers on riscv32, arm64, riscv32-flat and the S3, each
giving a child pool rows under heap tags with the keep mask:

- `heap_default`: a child with `ARENA_SIZE = 0` and a default heap takes a
  block larger than any arena, writes and reads it, frees it, and exits
  clean; it prints its `Handle`, `Memory` and `Mapping` used rows after the
  drain: one `Memory` and one `Mapping` more than at its start, and its
  handles one fewer (the pool's record gone);
- `heap_named`: a default heap and a data heap at slot 1; the child fills
  `Vector<UInt8, Heap<1>>` until `push` answers `Err(AllocError)`, prints
  the length it reached (fixed by the vector's doubling and the heap's
  length, not by time), drops it, fills it to the same length again, and a
  `String` built meanwhile comes from the default heap;
- `heap_spent`: the default heap and a small arena both spent: a `Vector`
  push answers `AllocError`, then a `String` panics with std's text,
  `ended status=131086`;
- `heap_fallback`: a default heap of one block's room and an 8 KiB arena;
  the child takes the default heap's block, then a second block, which the
  arena serves, then frees both and exits clean;
- `heap_empty_pool`: a launcher that took every byte of a pool before
  giving a sibling as the default heap: the child starts and its first push
  answers `AllocError`;
- `heap_missing_slot`: `Vector<UInt8, Heap<2>>` with no slot 2 given: the
  push answers `AllocError`;
- `heap_rights`, one child per refusal: a pool given without `Allocate`, one
  without `Map`, a child whose `Memory` ceiling lacks `Map`, one whose
  Process handle was derived without `Map`, one whose `Mapping` ceiling
  lacks `Base`, each ending before its program with its own §3.3 text;
- `heap_wrong_free`: a block from `Heap<1>` freed through the global
  allocator: `bad free at`;
- `heap_race` (riscv32, the short tick): `arena_race`'s two workers with
  their blocks from a default heap. Its verdict on a lock mutant rests on
  `arena_race`'s argument (many ticks over thousands of allocations).

Mutants: the mapping loop dropped (`heap_default` answers `AllocError` at
its first block); the built-in fallback dropped (`heap_fallback`'s second
block answers `AllocError`); the free's routing sending every block to the
default heap (`heap_fallback`'s free of the arena's block panics); each
rights check dropped (the kernel's fault, with no text, replaces the named
panic); a release dropped (`heap_default`'s rows); "no bytes" made a
panic (`heap_empty_pool` ends); `Heap<SLOT>`'s null made a panic
(`heap_named` ends); the default heap's lock not taken (`heap_race`, by
`arena_race`'s argument).

**Unit 4: the builder and the stock root.** Needs unit 3. The declaration's
`[heap.NAME]` tables and `arena` above the image's default; the image's
`arena` and `heap-ram`; the board's `atomics` key on every range that
states it; one pool per heap; `Source.Heap` in root; every refusal of §5.2;
the spec text of §9. Its tests prove:

- each refusal by name, and the plan's records (their tags and keep mask),
  the pool rows and the script's `ARENA_SIZE` (builder tests);
- every description in the tree reading clean (`check-tree`);
- described images on riscv32, arm64, riscv32-flat and the S3 whose
  `_start` child, built from a declaration with `arena = 0`, a default heap
  and a data heap, prints its heap records' rights from the set's listing
  (`BootEntry`'s rights, read at the drain, §2.2): `Allocate | Map` and
  nothing else; it runs `heap_named`'s fill and exits clean.

Mutants: the keep mask widened (the printed rights change); each floor term
dropped (its builder test); the atomics check dropped (its builder test);
`[image] arena` read above a declaration's `arena` (a builder test).

**Unit 5: a data heap in the S3's PSRAM.** Needs SO-102's units C and D and
a board stage, run on the part with the owner's word, showing `s32c1i` on a
PSRAM address. `atomics = true` on `esp32s3-r8`'s PSRAM range, and a smoke
case on that board under the emulator's PSRAM model in which a process's
data heap lies in PSRAM and its default heap in SRAM; the masked-latency
measurement on the part.

Unit 1 can land now. Unit 2 follows SO-124 unit 1; unit 3 follows units 1
and 2; unit 4 follows unit 3; unit 5 follows unit 4 and SO-102.

**For SO-124 unit 2** (the lead carries it there): step 4's sentence on the
drain's vectors goes; the `main` half of `start_no_heap` and its mutant
join its tests; the start-up's heap mapping needs nothing of its own, being
inside the drain (§2.3).

## Decided

1. **Start-up allocates nothing;** the drain keeps raw records in fixed
   storage bounded by the handle table (§2).
2. **Three kinds of heap,** each its own single-region TLSF and `SpinLock`;
   `heap.saw` unchanged (§1.1).
3. **The built-in heap stays separate** from the default heap; the global
   allocator asks the default first; a free is routed by the arena's bounds
   (§1.2).
4. **The heaps are mapped inside `BootSet(process:)`,** after the drain and
   before it returns, wherever their records lie (§3.2).
5. **A heap uses its whole pool,** read with `Pool.available`, and the
   runtime keeps no handle afterwards (§1.4, §3.3).
6. **Every refusal to allocate is null;** the start-up's mapping panics only
   for failures other than "no bytes", each with its own text (§3.4).
7. **Every right is read before it is spent** (§3.3).
8. **Names:** `arena` for the built-in heap, `[heap.NAME]` for a pool heap,
   `len` for a size, `slot` for the program's number, `heap-ram` in the
   image, `atomics` on a board's range (§5.1).
9. **`MAX_HEAPS` is 4** and `BOOT_SET_CAPACITY` 32, policy numbers raised
   on need (§2.2, §3.1).
10. **The reserved tags in `bootplan`,** and `sos` depends on it (§3.1).
11. **No link-time heap length:** a heap's length is read from its pool, so
    no script gains a symbol (§3.3).
12. **The builder's floors** are added to the floor `check_child` holds
    after SO-124 (§5.2).
13. **The kernel and root use their built-in heaps only** (§1.3).

## Where this departs from the lead's reading

- **"No heap declared: every allocation panics by name."** The later
  rulings keep a built-in heap for every process and make every failure a
  null, so a process with no heap at all gets null from every allocation,
  and std's `String` layer is what panics, with std's text (§2.4).
- **"Pools can be passed between processes."** A heap's pool is used whole
  by the runtime, so it is not one a process can pass; a pool to pass is a
  `memory` record, as today (§1.4, Q1).
- **"The tooling gives one heap pool per declaring process."** With named
  heaps it gives one pool per declared heap.
- **The drain's bound** is the handle table (`MAX_HANDLES`), not the
  declaration's record count or the `Handle` row: it holds for every
  process, root and the harness's children included, and needs no number
  from the builder (§2.2).
- **`sos` depending on `bootplan`** is a new edge between the kernel's
  userspace API and the launch convention's package, chosen so the
  reserved range has one copy. The alternative is a copy in `sos` with
  nothing to hold the two equal, since `bootplan` is dependency-free and
  the builder is hosted.

## Open questions

None. The owner ruled on both:

- **A heap's pool is owned entirely by the runtime.** The runtime uses the
  whole pool and keeps no handle, so it cannot be passed on; a process that
  wants memory to share declares a `memory` record and manages it.
- **The built-in heap defaults to 4 KiB on every board.** The runtime needs
  none (start-up, `print` and the panic path allocate nothing); a process
  that needs more raises it in its build config (`arena` in its declaration,
  or `[image] arena` for an image's processes), and a larger board's
  processes declare heaps or, later, ask a page-granular memory service.
