# SO-142 phase 2: more memory when the arena runs out

A design for the owner's review. Nothing here is built. It settles what a
process does when its `.bss` arena is spent: who gives it more memory and
through which objects, when it asks, how the new memory joins the heap, what
bounds the growth, what a refusal looks like, what differs per board, what
two threads see, and the units of work.

A bare section sign (§4) is this document's; the spec's sections are written
"spec §N", and SO-124's design (`.leadwork/SO-124/design-r5.md`) is cited as
"SO-124 §N".

Sources: the issue's phase 2 text and the owner's direction ("first just
using the predefined bss defined in the image config and then adding a way to
get more memory from somebody (the launcher?) when the bss preallocated mem
runs out"); spec §2.5, §2.7, §2.9, §2.10, §5.5, §5.6, §5b.1, §5c, §11 and
§12; `rt/common/src/heap.saw` and `lib.saw`; `rt/common_c/support.c`;
`kernel/sysapi/src/{rt,floor,bootset,memory,pool,system}.saw`;
`kernel/core/limits.saw`; `root/src/main.saw` and `root/bootplan`;
`tools/sosimage/src/{declaration,imagefile,plan,layout}.saw`;
`hal/riscv32/user/user.ld`; `hal/xtensa-esp32s3/ABI.md` §8 and §16, the
board's `board.toml`, and SO-102's issue; SO-124 §1 to §4, §7 and §10.

## 0. The shape in one page

- **The launcher gives a growing process a `Pool` at start, and the process
  draws on it itself.** A process that declares `heap = SIZE` in its
  `process.toml` gets one more boot record, under a reserved tag
  (`HEAP_TAG`): a pool of at least that many bytes, given with only
  `PoolRight.Allocate | PoolRight.Map`. Nothing new in the kernel: the pool,
  `allocate`, `map` and `Mapping.base` are spec §2.5 as built. No request
  goes to the launcher at run time.
- **The pool is exclusive by default.** The builder makes one image pool per
  growing process (or the image binds one with `heap = "POOL"`), in the
  board's default pool range or a range the image names, so the S3's PSRAM
  can hold it once SO-102 maps it. Sharing one pool between processes is an
  open question (Q2).
- **The runtime asks lazily, once, for everything.** When the arena cannot
  serve a request, `rt_alloc` calls a fourth per-side hook, `sos_rt_grow`,
  inside the lock. The process side allocates the whole heap from the pool,
  maps it read-write into itself, reads the base, releases the three
  handles, and the heap adds the region and serves the request again. One
  growth per life: one `Memory` row, one `Mapping` row, one protection row.
- **Nothing in the window parks, panics or allocates.** The hook is
  check-free Saw over the floor's raw calls, and every op it makes answers
  at once. So the spin lock's window grows by a few syscalls once, and two
  threads that exhaust together grow once.
- **The heap keeps a list of regions.** The control block stays at the
  arena's front, sized for the larger of the arena and the heap (a link-time
  number, `HEAP_LEN`), and gains a list head. Each grown region begins with
  a two-word link and ends in its own sentinel, so regions never merge.
  `holds` accepts a block in any region past that region's first block.
- **A refusal ends the process `Faulted (Panic)`** through SO-124's
  `PANIC_OP`, with a text that names the cause: no heap, the heap spent, or
  the op that refused and its status.
- **The builder checks** the heap's size against the board's word and
  grain, an image pool's length against the declaration, a fast process's
  heap against a fast range, the arena against the control block, and the
  rows the growth spends: handles, `Memory`, `Mapping`, and the image's
  Memory slab.
- **Five units:** the heap's regions (hosted); the hook and the adoption
  API; the builder and the stock root; `sosmain`'s adoption; the S3's PSRAM.

## 1. Who supplies the memory

### 1.1 What exists

- **A pool is a byte budget** (spec §2.5): its holder can allocate no more
  than it holds. `allocate(len, granularity)` answers a new `Memory`, charged
  to the caller's `Memory` quota row; the bytes are the pool's outstanding
  bytes until the allocation comes home. A pool's refusal for want of bytes
  is the status `NoMemory`, not a fault, because the caller could not have
  checked it.
- **An allocation's rights are its pool handle's,** intersected with the
  bits a `Memory` has. A pool sibling given without `MapExecute` allocates
  memory that can never be mapped executable; one given without `Transfer`
  allocates memory that cannot be given on (spec §2.5).
- **`map(process, access)`** spends `MemoryRight.Map` on the allocation and
  `ProcessRight.Map` on the target. The Process default set, which a
  process's own handle from `ProcessSelf` carries, includes `Map`
  (spec §2.7), so a process can map into itself. On the identity tiers the
  row sits at the allocation's own address; on arm64 the kernel places it at
  a fresh address in the 4 MiB grant window, above the image region
  (spec §2.5, §5b.1). `Mapping.base()` is the portable spelling of where.
- **A mapped allocation outlives its handles.** Releasing the `Memory`
  handle leaves the allocation alive while a row grants it; releasing the
  `Mapping` handle leaves the row ("safe-but-leaked", spec §2.5). The row
  goes, and the bytes go back to the pool, at the process's teardown
  (spec §2.10). The `Mapping` quota row counts installed rows, so it stays
  charged while the row lives (`kcore.limits`' `DEFAULT_QUOTA_MAPPINGS`,
  spec §2.10's "its charge, which counts the row, comes back when the row
  goes"). The `Memory` row stays charged too: an allocation is a shared
  kind, refunded when it frees.
- **A child can already hold a pool.** A declaration's `kind = "memory"`
  record is bound by the image to a `[[pool]]`, and the stock root gives the
  pool row whole (`tools/sosimage/src/plan.saw`, `root/src/main.saw`'s
  `give_row`); `tests/stock-server` allocates from one.
- **Quotas count objects, not bytes** (spec §2.9). "A pool held by a process
  is the byte bound." The child defaults are 4 `Memory`, 2 `Mapping` and 12
  `Handle` rows (`kcore.limits`), and a child's `Mapping` row is clamped to
  the grant rows its domain has left after its image: 8 less 4 on riscv32,
  16 less 4 on arm64 and the S3.
- **The boot set is drained by the program** (`sos.BootSet`), allocating as
  it goes, and SO-124's `_start` drains it before `main` and takes the
  runtime's own records (the console, under `CONSOLE_TAG`) from the reserved
  range `0xFFFF_FF00` up (SO-124 §3.1, §3.2).

### 1.2 Option A: a pool the launcher gives at start (recommended)

The launcher gives the process a pool under `HEAP_TAG`, minted with
`PoolRight.Allocate | PoolRight.Map` and nothing else. The runtime takes it
at start and, when the arena is spent, allocates from it and maps the
allocation into itself.

- **Authority.** The process may take, once, up to the pool's bytes, as
  read-write memory it can never execute (no `MapExecute`), never give on (no
  `Transfer`) and never narrow for anyone else (no `Mint`). The launcher
  decided the budget at launch and keeps no run-time role.
- **Cost.** One boot record, one handle until the growth; at the growth one
  `Memory` row and one `Mapping` row for the rest of the process's life. No
  connection, no root code at run time, no kernel change.
- **What it needs from the launcher** is what the launch convention already
  carries: a record under a tag, with a keep mask on the give
  (`Process.give(pool:tag:keep:)`).

### 1.3 Option B: a request to the launcher over a connection

The process holds an inlet of a connection its launcher serves. On
exhaustion the runtime calls; the launcher allocates from a pool it keeps,
mints the allocation down to `Map` and replies with it; the runtime maps it.

- **Authority.** The process holds no pool, only an inlet; it gets exactly
  what the launcher answers. The launcher can apply any run-time policy: a
  per-child byte cap out of one shared reserve, refusals by system state, a
  ledger it settles at each child's death, which it already watches.
- **Cost.**
  - **The request parks inside the allocator's lock.** A fused call waits
    for the launcher's reply. Every other thread of the process that
    allocates meanwhile spins on std's `SpinLock`, which never yields. On
    an image with no tick, a spinner keeps the processor and the launcher
    never runs, so the process hangs. Moving the call out of the lock
    window needs a retry loop and a lock that yields (SO-124's
    `sos_rt_yield` hook), and two threads that exhaust together then both
    ask.
  - **Every root must serve it.** Root is to be replaceable (the owner's
    packaging ruling), so the protocol becomes part of every root's
    interface; a hand-written root that does not serve it leaves its
    children unable to grow, silently until they run out.
  - **The stock root becomes a server:** its supervise loop gains an
    outlet, a per-child ledger keyed by the sender's pid, and the code to
    allocate, mint and reply, in the one process whose failure stops the
    machine.
  - **The child holds one more handle (the inlet), and the image one more
    connection** (a `Pipe` slab slot, one per image, with an inlet per
    growing child).
- **What it buys over A** is an enforced per-child bound out of a reserve
  shared by several processes. §1.5 says how A gets the same later.

### 1.4 Option C: a kernel op

A `ProcessOp.Grow(len)` that allocates from a pool bound to the process and
maps the result, answering the base.

- **Authority.** The same as A's: whatever the bound pool holds.
- **Cost.** A new binding (process to pool) in the process record, a new op
  folding `allocate`, `map` and `Base`, and policy (how much, from where) in
  the kernel, which spec §12 keeps in userspace ("the kernel interprets
  nothing"). It saves three syscalls once per process life.
- **Rejected:** it is option A with the mechanism moved into the kernel and
  nothing gained.

### 1.5 Exclusive and shared pools

Option A's mechanism is the same whether the pool is the process's alone or
shared; only the image's binding differs.

- **Exclusive** (recommended now). The pool is the process's own, bound
  once, as every image pool is today. Its bytes are committed at build time,
  so it is a second arena with two differences that matter: it can be
  placed in another RAM range than the process's own region (the S3's PSRAM,
  §5.3), and a process that never runs out never spends the `Mapping` row
  or the protection row a mapping costs.
- **Shared.** Several processes hold siblings of one pool, and each draws on
  it when it runs out: memory is committed only when used. The kernel
  already treats a shared pool as shared state, answering `NoMemory` to
  whoever finds it empty (spec §2.5). What it lacks is a per-holder byte
  bound: any holder can take all of it, and only its own runtime's cap
  (`HEAP_LEN`, §2.3) stops it. An enforced bound needs either a byte quota
  row (a new `QuotaKind`, charged at `allocate`, credited when the
  allocation comes home; spec §2.9 counts objects only "in v1") or the
  launcher service of option B. Q2 asks whether the owner wants sharing.

### 1.6 Recommendation

Option A with an exclusive pool. It needs no kernel change, no run-time
role for root and no blocking, it is the same code on every tier, and the
launcher's whole policy is the pool's size and placement, written in the
image. It answers "from the launcher" in the launcher's own terms: the
launcher furnishes the memory at launch, and the process takes it only when
the arena runs out. Q1 asks the owner to confirm that reading over a live
request (option B).

## 2. When it asks

### 2.1 Lazily, once, for the whole heap

- **Lazily.** The owner's words are "when the bss preallocated mem runs
  out", and a process that never runs out should not spend a `Mapping` row
  or a protection row. On riscv32 a row is a PMP entry replayed at every
  switch into the process.
- **Once, for everything.** The first growth allocates the pool's whole
  length (`HEAP_LEN`, the builder's number, §4.2). With an exclusive pool
  there is nobody to leave bytes for, and one growth spends one `Memory` row
  and one `Mapping` row. Growing in steps would spend a `Mapping` row per
  step, against a default of two that a mapped window already halves, and
  would leave regions that never merge (§3.3). A step size is the knob a
  shared pool needs, and it waits for Q2.
- **Eagerly at start** was weighed: it would keep every syscall out of the
  lock window, and it is the same as a larger arena placed elsewhere. It
  spends the rows whether or not the process ever needs them, and it is not
  what the owner asked for.

### 2.2 Why the request fits the lock window

`ARENA`'s doc says nothing in the window can panic or allocate, so the panic
path never meets the lock held by its own thread. The growth keeps both
rules:

- **It allocates nothing and cannot panic.** The hook is Saw in `sos.rt`,
  written as `rt_write` is: the floor's raw calls, words on the stack,
  wrapping arithmetic, no indexing, no `String`, no `Result` unwrapped with
  `!`. Its `-Oz` disassembly is read on riscv32, arm64 and xtensa, as
  `rt/common`'s header asks of the panic path.
- **A syscall is a trap, and it comes back or ends the process in the
  kernel.** Each op the hook makes answers a status and returns, or faults
  the caller, which ends the process inside the kernel without running a
  line of the process's panic path. Neither meets the lock.
- **Nothing parks.** `PoolOp.Allocate`, `MemoryOp.Map`, `MappingOp.Base`
  and a handle release each answer at once; none waits on another process
  (spec §2.5, §3). So the window grows by seven syscalls (three ops and four
  releases), once in the process's life, and a spinner meanwhile spins
  exactly as it does while a holder is preempted today. The hazard
  `ARENA`'s doc states (a spinner above a preempted holder under fixed
  priorities) is unchanged.
- **The message is composed after the lock is given back,** as today's
  exhaustion line is.

### 2.3 The growth's steps

`rt_alloc`, holding the lock, finds `heap.alloc` refusing. It calls the
per-side hook

```saw
extern "C" {
    func sos_rt_grow(base_out: UnsafePointer<UInt>,
                     len_out: UnsafePointer<UInt>) -> UInt
}
```

which answers a cause word (zero for a region given). The kernel's answer, in
`kcore` beside its `sos_rt_lock_owner`, is "no heap": the kernel never
allocates at run time (`rt/common/src/lib.saw`'s arena section). The process
side (`sos.rt`):

1. **No heap adopted** (the parked pool word is zero): answer "no heap".
2. **Grown already** (a parked flag): answer "spent".
3. **Allocate** `HEAP_LEN` bytes from the parked pool at granularity zero.
   A refusal is answered as the cause, with its status.
4. **Map** the allocation into the process through the parked Process word,
   `MapAccess.Read | MapAccess.Write`. On a refusal, release the
   allocation's handle (its bytes go home), and answer the cause.
5. **Base**: `MappingOp.Base` on the new Mapping. `sos.rt` is inside the
   `sos` package and reaches it as `Mapping.base()` does, through the
   package's `call_value`.
6. **Release** the Mapping handle, the Memory handle, the pool handle and
   the Process sibling. The row keeps the bytes; the pool lives until its
   outstanding bytes come home at teardown (spec §2.5's "what holds a
   pool"). Set the flag, write the base and `HEAP_LEN`, answer zero.

`rt_alloc` then gives `[base, base + len)` to the heap (§3) and asks again.
A second refusal is "spent". The handles peak at four during steps 3 to 6
(pool, sibling, Memory, Mapping) and are zero after.

`HEAP_LEN` is a link-time number, as `ARENA_SIZE` is. The builder writes
`HEAP_LEN = 0x...;` into each growing process's script beside
`ARENA_SIZE` (`process_script`, `tools/sosimage/src/layout.saw`), each user
script `PROVIDE`s it as zero, and `support.c` reads it back. It reads it as
the distance between two section-relative symbols (`_arena_start` and
`_arena_start + HEAP_LEN`), the idiom it already uses for the arena: the
same file is linked into the kernel, and the arm64 kernel's text runs in the
high half, out of `adrp`'s reach of a small absolute symbol.

### 2.4 How the runtime gets the pool: adoption

`sos.adopt_heap(pool: Pool, process: &Process) -> Result<Void, SosStatus>`,
public, in `sos.rt`:

- reads the pool's rights with `INFO_OP` and answers `AccessDenied` unless
  it carries `Allocate` and `Map`;
- mints a sibling of `process` narrowed to `ProcessRight.Map`, answering the
  mint's status on a refusal (`QuotaExceeded`, `NoResource`);
- parks the pool's word and the sibling's, taking ownership of both, for
  the growth to use and release;
- panics with `sos: a heap was adopted twice` on a second call, and with
  `sos: this image was linked with no heap` when `HEAP_LEN` is zero: both
  are build or program errors, not conditions a caller handles.

The sibling decouples the runtime from the lifetime of the program's own
Process handle, at the cost of one handle until the growth. A
`ProcessRight.Map`-only handle is the least authority `map` needs on the
target.

- **A `main` program** (SO-124): `sosmain`'s `_start` takes `HEAP_TAG` in a
  step after the console's (SO-124 §1.2 step 5), and adopts it with its own
  Process wrapper. A refusal panics by name, as every start-up step does:
  `sos: the heap pool lacks Allocate or Map`, `sos: no handle for the heap:
  STATUS`. `main` sees the entry in the set's listing, already taken, as it
  sees the console's.
- **A `_start` package** takes the record from its own `BootSet` and calls
  `adopt_heap`. One that never does keeps the arena alone, and its record is
  released with the set.
- **Before adoption the heap cannot grow.** The start-up's own allocations
  (the drain's two vectors) come from the arena, so the arena must hold
  them; that is true today and the builder cannot check it.

## 3. How a region joins the heap

### 3.1 The class table

`ready()` sizes the control block "for a block as large as the whole
region", the arena. A later region whose blocks outgrow that class corrupts
the heap: `class_of` answers an index past the table, and `insert` writes a
list head and a bitmap word past the control block. So the table is sized
for the larger of the arena and `HEAP_LEN`. Each doubling of the largest
block adds one class, five words (20 bytes on a 32-bit profile, 40 on
arm64): a 32-bit control block for an 8 KiB arena is 52 words today, and
one for a 1 MiB heap would be 87. `ready()` takes the size as an argument
(`UnsafeHeap` gains a `largest` field beside `base` and `size`), so
`sosrt.heap` still names no linker symbol and its hosted tests give it any
number.

`alloc`'s early refusals (`size > self.size`, `align > self.size`) exist to
keep `round_up` from wrapping. They compare against `largest` instead, so a
request the arena cannot hold and the heap can is not refused before the
growth.

### 3.2 The region list, alignment and overhead

- **The control block gains a word,** the head of the list of grown regions
  (`CONTROL_FIXED` from two to three), zero until the first growth. The
  arena region is the descriptor's own (`base`, `size`) and is not on the
  list.
- **A grown region begins with its link:** word 0 the next region's start
  (zero for none), word 1 the region's end. `add_region` writes the link,
  pushes the region at the head, and then makes the region one free block
  as today: the first block's header on the word before the granule after
  the link, the sentinel's header on the region's last granule.
- **Alignment.** A pool serves whole grains at grain-aligned bases (spec
  §2.5): 4 bytes on riscv32 and the S3, a page on arm64. The heap's granule
  is two words, so on riscv32 and the S3 a region's base may lie four bytes
  off it; `add_region` already rounds the first block up and the end down,
  so nothing changes but where the link sits.
- **Overhead.** A grown region gives up at most its two link words, a
  header word, one granule of rounding at the front and the sentinel's word:
  under 24 bytes on a 32-bit profile.

### 3.3 Regions do not merge

Each region ends in its own sentinel, a header of size zero that is never
free, and its first block never carries `PREV_FREE`, so `free` never merges
across a boundary (`heap.saw`'s layout section; phase 1's review checked a
second region apart, adjacent above and below, too small and misaligned).
That stays. Merging adjacent regions would need the lower sentinel removed
and the list rewritten, for a case that cannot arise with one growth: the
arena is in `.bss` inside the process's region, and the heap is in a pool
row elsewhere, apart on every board. The cost is that no block spans the
arena and the heap, so a request is served from one or the other; the
growth is sized to the whole heap, which holds any request the arena could.

### 3.4 What `holds` accepts

`holds(block)` answers true only for a block that lies in one region, at or
after that region's first block and before its end, on the granule, whose
header is not free, and whose size is at least a block and no more than
the region's end less the block:

- **the arena:** as today, past the control block and the first block's
  header (`first()`);
- **a grown region:** past its link and its first header; the link's two
  words and the region's front are refused.

So a pointer between regions, into a region's link, or into a pool
allocation the heap was never given is refused, and `rt_dealloc` panics
with `bad free at ADDRESS` as it does now. The walk of the list is bounded
by `MAX_HEAP_REGIONS` (eight: one growth today, room for steps later), so a
list the program has overwritten into a cycle stops the walk instead of
hanging `free`; a block past the bound is refused. `holds`' misses are the
three its doc names, in each region.

## 4. Limits

### 4.1 What bounds a process's growth

| Bound | Where | What it limits |
|---|---|---|
| The pool's length | the image (the builder sizes it from the declaration, §4.2) | the bytes, enforced by the kernel's `NoMemory` |
| `HEAP_LEN` | the process's link | the bytes the runtime asks for, and the class table |
| `Memory` quota row | the declaration's `[quota]`, the image's raise | one allocation per growth |
| `Mapping` quota row | the same, clamped to the domain's free grant rows (spec §2.9) | one row per growth, beside each window the process maps |
| `Handle` quota row | the same | the pool record and the runtime's Process sibling, and two more during the growth (§2.3) |
| The image's Memory slab | the image's limits | one slot per growth, beside the boot rows |
| The launcher | the keep mask on the give | no execute, no give-on, no mint |

A byte quota row is not proposed: with an exclusive pool the pool's length
is the byte bound (spec §2.9), and the row is what sharing would need
(§1.5, Q2).

### 4.2 What the builder checks

The declaration gains a top-level size beside `arena`:

```toml
format = 1
arena = 0x2000
heap = 0x1_0000        # optional: the least the heap must hold
```

and the image's `[[process]]` an optional binding:

```toml
[[process]]
name = "logger"
package = "sawos:drivers/logger"
heap = "logbuf"        # a [[pool]] of this image, its length at least the
                       # declaration's `heap`

[[pool]]
name = "logbuf"
len = 0x4_0000
ram = "psram"
```

`heap` in a declaration is a size, as `arena` is; in an image it names the
pool that holds it, as `pools` names the pool a `memory` record binds to.
An image that binds none gets a pool the builder makes, of the
declaration's size, in the board's default pool range (`[ram.default]
pool`), or in a fast range for a process that declares `ram = "fast"`. The
builder writes `HEAP_LEN` as the bound pool's length.

Refused by name, in the declaration reader and against the board
(`check_against`):

- a `heap` the board's word cannot hold, or that is not a whole number of
  the board's protection grain (a pool hands out whole grains, spec §2.5);
- a `heap` whose arena cannot hold the control block sized for it: the
  builder computes the block's words from the board's word size as `ready()`
  does, plus the start-up's own needs are the program's (§2.4);
- a declared `[[boot]]` tag in the reserved range (SO-124 §3.1, which
  `HEAP_TAG` joins);

in the image and the plan (`tools/sosimage/src/plan.saw`):

- a `heap` binding on a process whose declaration states no `heap`, or that
  names no `[[pool]]`, or a pool shorter than the declaration's `heap`, or
  one bound elsewhere too ("a pool is bound once", as today);
- a heap pool in a range that is not fast, for a process that declares
  `ram = "fast"`;
- a `Handle` row below the floor plus four: the pool record, the Process
  sibling the adoption mints, and the Memory and Mapping handles the
  growth holds at its peak (§2.3; the floor is `handles_at_start`, which
  already counts one `Mapping` per window);
- a `Memory` row of zero;
- a `Mapping` row below the process's windows plus one (the floor already
  assumes each window is mapped);
- an image whose Memory slab cannot hold the boot rows and one slot per
  growing process (`check_plan`'s objects-of-a-kind check gains the kind).

The plan gives the record as a new `Source.Heap`: the row `from`, given with
the keep mask `PoolRight.Allocate | PoolRight.Map`. Root mints nothing for
it; the give's own mask narrows the child's entry (spec §2.5,
`Process.give(pool:tag:keep:)`).

### 4.3 What a refusal looks like

Every end goes through SO-124's `PANIC_OP` (SO-124 §4, §7): the process ends
`Faulted (Panic)`, `0x2000E` in the status word, which the stock root prints
as `ended status=131086`, and the kernel's fault line carries the text. A
root stops the machine with 64. The texts:

| Cause | Text |
|---|---|
| No heap adopted (none declared, or not taken) | `sos: out of arena memory` |
| The heap grown and spent | `sos: out of memory: the arena and the heap are spent` |
| `allocate` refused | `sos: out of memory: the heap's allocate answered STATUS` |
| `map` refused | `sos: out of memory: the heap's map answered STATUS` |
| `Base` refused | `sos: out of memory: the heap's base answered STATUS` |

The first is today's text, so `arena_small` keeps its line (its status moves
to 64 with SO-124 unit 1, not here). `STATUS` is `SosStatus`'s printed name.
Each text is formatted after the lock is released, on the compiler's stack
scratch, as `rt_dealloc`'s `bad free at` is. A refusal a process could
handle is not offered: `__saw_rt_alloc` has no failure return, and the
allocator is what every reporting path allocates through.

## 5. Boards

### 5.1 The isolated tiers

- **riscv32 (`virt`, PMP).** The allocation is mapped at its own address,
  inside the pool's row, which the builder places apart from every process
  region. The row is one more PMP region in the process's set (a TOR pair or
  a NAPOT entry, the HAL's choice under the conservative quota, spec
  §5b.1). The grant-row budget is 8, the image spends 4, and the clamp
  holds `Mapping` at 4; a process with two mapped windows and a heap needs
  3.
- **arm64 (`virt`, MMU).** The kernel places the mapping above the image
  region in the 4 MiB grant window, so the heap and the arena are never
  adjacent. A pool is allocated in whole pages, so `heap` is a page
  multiple there (the builder's grain check). A heap that, with the
  process's region, passes the window is refused at the `map` with
  `NoResource`, and the text names it; the builder does not check the
  window, which depends on where the kernel's placement cursor stands.
- **Both** enforce the access: the row is read-write and not executable,
  and the pool's keep mask means no other row over the same bytes can be.

### 5.2 The Flat tier

riscv32-flat and the S3 run the same code: `map` is "trivially true"
(spec §5b.1), and its row is recorded and counted against the `Mapping`
row. The process could reach the pool's bytes without mapping them, but it
cannot learn their address: no op reads a pool's or an allocation's bounds
(spec §2.5), and `Mapping.base()` is the only answer. No growth case asserts
a hardware denial, so the flat profile excludes none of them.

On the S3 the process range is the internal SRAM (`[ram.sram]`, 256 KiB,
`hal/xtensa-esp32s3/board.toml`), shared by root's region, the children's
and every pool, so a heap pool there is paid for out of the same bytes as
the arena. The value of the heap on this board is the PSRAM.

### 5.3 The S3's PSRAM

What this design assumes from SO-102 (its issue and `ABI.md` §16, which
builds the command layer only):

- PSRAM appears at `0x3D00_0000`, 8 MiB, through the cache MMU, on a board
  `esp32s3-r8`, a facade over `esp32s3` whose description names it as a
  process range (`[ram.psram]`, `speed = "slow"`), once SO-102's units C
  (the map and read-back) and D (a child's memory in PSRAM) land.
- The kernel treats that range as RAM a pool row can lie in: a heap pool
  there is an ordinary image pool (`ram = "psram"`), and `map` on the Flat
  tier records the row.

What it does not assume, and a later unit must prove:

- **Atomics on cached PSRAM.** `ABI.md` §8 probes `s32c1i` on DRAM and IRAM
  only, and SO-102 lists atomics on PSRAM as unverified. A heap block holds
  whatever the program puts there, `Atomic` words included. Until a board
  stage shows `s32c1i` works on a PSRAM address under the ROM's
  `ATOMCTL`, no heap pool goes in PSRAM: the builder refuses a heap pool in
  a range whose board does not state its atomics work (a range key, added
  with that unit).
- **Masked latency.** A buffer the program hands a syscall can now be in
  PSRAM, and the copy doors read it with interrupts masked; each cache miss
  is a serial transfer. This is SO-102's "the kernel's own data and anything
  touched with interrupts masked stays in SRAM" seen from the other side.
  The unit measures it on the part.
- **Placement policy.** Whether the r8 board's default pool range is PSRAM
  is Q5.

## 6. Threads

- **Two threads exhaust at once.** The first to take the lock finds the
  arena spent, grows inside the window, adds the region, is served and
  gives the lock back. The second spins meanwhile, then takes the lock and
  is served from the new region with no growth. Had the heap not held its
  request, it would find the flag set and end with "spent". There is never
  a second growth, because the flag and the region are written inside the
  window that tests them.
- **The lock across the request.** Held across non-parking syscalls only
  (§2.2), so the window is longer once and never waits on another process.
  No tick is needed for the holder to finish: it never leaves the
  processor voluntarily inside the window.
- **A thread killed in the window** is not possible apart from its process
  (spec §8); a process killed mid-growth releases the handles at teardown
  and its row, if installed, goes with the domain.
- **`free` on another thread** walks the region list under the same lock,
  so it never sees a half-linked region: `add_region` links the region
  before the first block is inserted, and both happen in one window.

## 7. The spec text

The kernel's contract does not change. Three paragraphs describe the
runtime's use of it; the owner reads them with the design.

**Spec §5c, the paragraph "Where the floor is", after "panics through the
ordinary panic path, naming the address; one it cannot tell corrupts the
heap.":**

> When the arena cannot serve a request, the runtime grows the heap once
> from a pool its launcher gave it: it allocates the pool's length, maps the
> allocation read-write into its own process, and adds the region to the
> heap, all inside the arena's lock, through ops that answer at once. A
> process with no such pool, or whose heap is spent, ends as a panic.

**Spec §12, the launch convention, after "It gives windows, lines and
endpoints as capabilities":**

> A process that declares a heap gets a pool under the runtime's heap tag,
> given with `PoolRight.Allocate | PoolRight.Map` and nothing else, so the
> memory it allocates can be mapped, read and written, and never executed or
> given on.

**Spec §11, beside the arena's line:** a heap grows once and never returns
its bytes before the process ends, and a heap in the S3's PSRAM waits on
atomics being shown to work there.

## 8. Units of work, in order

Each unit is one patch, gated by `make sos-test sos-smoke-esp32s3`; the
ESP32-C3 builds nothing now, and its user script gets the same lines as the
others only so it stays in step. Each lists its mutants, the review
standard: every check and refusal goes red when removed or inverted.

**Unit 1: the heap's regions** (`rt/common/src/heap.saw`, hosted). Needs
nothing. The `largest` field and the class table sized by it; `alloc`'s
bounds against it; the list head in the control block; the link in
`add_region`; `holds` over every region with the walk's bound. `sosrt`
passes `largest` equal to the arena for now, so every image's behaviour is
unchanged (images move, transcripts do not). Its hosted tests
(`rt/common/tests/heap.saw`, in `HOSTED_TESTS`) prove:

- a region four times the arena serves a block three times the arena, and
  frees it back whole;
- regions apart, adjacent above and adjacent below the arena: blocks from
  each, freed in every order, never merge across a sentinel (the largest
  free block of each region reads back as that region's whole block);
- `holds` accepts the first and the last block of each region (a region
  ending on the granule included) and refuses each link word, the front
  of each region, an address between regions, the control block, and a
  block past the last region;
- a list made cyclic stops at the bound and refuses;
- 200,000 random steps across three regions, every live block checked.

Mutants: the table sized by the arena alone; `holds` blind to grown regions
(a good free refused); `holds` accepting the link; the sentinel not written
(a merge across regions); `PREV_FREE` left on a region's first block; the
walk's bound dropped (the cyclic test hangs and the hosted run's timeout
reports it); `alloc`'s bound left on the arena; `<` for `<=` at a grown
region's last block.

**Unit 2: the growth and the adoption.** Needs unit 1 and SO-124 unit 1
(the abort hook takes the text). `sos_rt_grow` in `rt/common`'s hooks,
`kcore`'s answer, `sos.rt`'s growth and `adopt_heap`; `HEAP_LEN` in each
user script (`PROVIDE` zero) and `support.c`'s reader; `rt_alloc`'s growth,
second try and texts; `sosrt` passing `largest`. Its cases are root
packages (the runner's stitcher gives root a pool row; each package's
script sets `HEAP_LEN`, as `tests/arena-small/` sets `ARENA_SIZE`), on
riscv32, arm64 and riscv32-flat, and the S3:

- `heap_grow`: an 8 KiB arena and a 32 KiB heap; root adopts, takes a
  20 KiB block, writes and reads it, frees it, takes it again, then asks for
  more than both hold: the "spent" text, status 64;
- `heap_handles`: root's `Handle`, `Memory` and `Mapping` used rows,
  printed before the adoption, after it and after the growth: one handle
  more after the adoption (the sibling), one fewer than at the start after
  the growth (the pool's and the sibling's handles gone), and one `Memory`
  and one `Mapping` more;
- `heap_race` (riscv32, the short tick): two threads exhaust together; the
  `Mapping` used row reads one more after both are served, whichever went
  first, so the count is fixed by the window and not by timing;
- `heap_refused`: a pool shorter than `HEAP_LEN`, so `allocate` answers
  `NoMemory`, and the text names it;
- `heap_rights`: `adopt_heap` of a sibling minted without `Map` answers
  `AccessDenied`, and of a heap linked with `HEAP_LEN` zero panics with its
  text;
- `arena_small` and every other arena case unchanged.

Mutants: the second try dropped (`heap_grow` ends at its first big block);
the growth made outside the lock (`heap_race` reads two growths, or
corrupts); any release dropped (`heap_handles`); the flag not set (`heap_grow`
answers `allocate`'s `NoMemory` instead of "spent"); the map asked
read-only (the first write faults); the rights check dropped (the map
faults `AccessDenied` in the kernel instead of the named answer).

**Unit 3: the builder and the stock root.** Needs unit 2 and SO-124
unit 4's reserved range (whichever lands first defines it in `bootplan`,
and `HEAP_TAG` is `CONSOLE_TAG` plus one). The declaration's `heap`, the
image's `heap`, the made pool, `Source.Heap` and its keep mask in root,
`HEAP_LEN` in the process script, every refusal of §4.2, the spec text of
§7. Its tests prove:

- each refusal by name, and the plan's record, keep mask, pool row and
  script line for a growing process (builder tests, `blade test`);
- every description in the tree reading clean (`check-tree`);
- described images on riscv32, arm64 and riscv32-flat, and on the S3, whose
  `_start` child prints its heap record's rights (`Allocate | Map` and
  nothing else), adopts it, grows past its arena and exits clean;
- a child whose image raises its `Mapping` row to exactly its floor, and
  which maps one allocation of its own before it runs out: the growth's
  `map` answers `QuotaExceeded`, the child ends `ended status=131086`, and
  the fault line carries `the heap's map answered QuotaExceeded`.

Mutants: the keep mask widened to the row's rights (the printed rights
change); each floor term dropped (its builder test); `HEAP_LEN` not written
(the child's adoption panics with "linked with no heap"); the fast-range
check dropped.

**Unit 4: `sosmain` adopts.** Needs unit 3 and SO-124 unit 2. `_start`'s
step, its refusals by name, and `main`'s listing showing the entry taken.
Its tests: a `main` child that grows and exits; one whose pool record was
given without `Map` (a test plan), ending with its start-up text; a `main`
child with no heap, unchanged. The product template's `process.toml` gains
a commented `heap` line, nothing else. Mutants: the step dropped (the child
ends "out of arena memory"); the rights check dropped.

**Unit 5: a heap in the S3's PSRAM.** Needs SO-102's units C and D and a
board stage, run on the part with the owner's word, showing `s32c1i` on a
PSRAM address. The range key that states it, the builder's refusal without
it, and a smoke case on `esp32s3-r8` under the emulator's PSRAM model
growing a child's heap there; the masked-latency measurement on the part.

Unit 1 can land at once. Unit 2 follows SO-124 unit 1; unit 3 follows unit 2
and the reserved range; unit 4 follows unit 3 and SO-124 unit 2.

## Decided

1. **The runtime's end:** `Faulted (Panic)` through `PANIC_OP` for every
   refusal, with the texts of §4.3; today's text kept for a process with no
   heap.
2. **One growth per life,** of the whole pool, inside the lock (§2.1, §2.2).
3. **The hook:** a fourth per-side hook, `sos_rt_grow`, answering a cause
   word; the kernel's answer in `kcore` (§2.3).
4. **The sibling:** the runtime mints its own `ProcessRight.Map` handle at
   adoption and releases it after the growth (§2.4).
5. **Every handle released after the growth;** the row keeps the bytes
   until teardown (§2.3).
6. **`HEAP_LEN` at link,** read as a distance between section-relative
   symbols, as the arena's extent is (§2.3).
7. **The class table** sized by an explicit `largest` field, so
   `sosrt.heap` names no symbol (§3.1).
8. **The region list** in a control-block head and a two-word link per
   region; no merging; the walk bounded by `MAX_HEAP_REGIONS` (§3.2 to
   §3.4).
9. **Names:** `heap` as a size in a declaration (beside `arena`) and as a
   pool's name in an image (beside `pools`); `HEAP_TAG` in the reserved
   range after `CONSOLE_TAG`; `Source.Heap` in `bootplan`.
10. **The builder's floors:** handles plus four, `Memory` at least one,
    `Mapping` at least windows plus one, the Memory slab one slot per
    growing process (§4.2).
11. **A fast process's heap is fast** (§4.2).
12. **Root does not grow** in the stock image: it has no declaration. A
    hand-written root may adopt any pool row it holds, through the same API.
13. **The arm64 grant window** is not checked by the builder; the `map`
    refusal names it (§5.1).

## Open questions

**Q1. Is "more memory from the launcher" a pool the launcher gives at
start, or a request to the launcher at run time?** The design is the
first: the launcher furnishes a budget in the image, the process takes it
when the arena runs out, and no request travels. The second gives the
launcher run-time policy and per-child caps out of one reserve, and costs a
request that parks inside the allocator's lock, a protocol every root must
serve, and a server loop in the stock root (§1.3). *Recommendation:* the
pool at start.

**Q2. Should several processes share one heap pool?** Sharing commits
memory only when a process uses it, which is what makes growth worth more
than a larger arena on a small board. Its cost is that any holder can take
the whole pool: an enforced per-process bound needs a byte quota row, a
kernel change to spec §2.9, or option B. *Recommendation:* exclusive pools
now; when a product needs a shared reserve, add a growth step and a byte
quota row together.

**Q3. Should every process get a heap by default,** as SO-124 gives every
process the console? A heap pool commits RAM at build time, and a process
that states no `heap` keeps today's behaviour. *Recommendation:* no; a
process declares the heap it needs.

**Q4. Should the heap give memory back?** A grown region that is wholly
free could be unmapped and its bytes returned to the pool. With an
exclusive pool nobody else can use them. *Recommendation:* not until pools
are shared (Q2), and then with the growth step.

**Q5. On the `esp32s3-r8` board, is the default pool range the PSRAM?**
Heaps are where large, rarely touched memory goes, and the internal SRAM
holds every process's region. *Recommendation:* yes, once unit 5 shows
atomics work there; until then heaps stay in SRAM.
