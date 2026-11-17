# SO-124: processes written with an ordinary `main`

A design for the owner's review. Nothing here is built. It settles how a
process's start-up works once the runtime owns `_start`: the start sequence,
the boot context `main` reads, the console that `print` writes to, the kernel
op a panic ends through, threads, the windowed S3, the exit status, migration,
rights, and the units of work.

A bare section sign (§4) is this document's; the spec's sections are written
"spec §8", and the S3 board's facts "the S3 ABI's §7d"
(`hal/xtensa-esp32s3/ABI.md`).

Sources: the owner's rulings on SO-124 (its comments, and the rulings of
Nov 17 on the open questions); the review threads on the earlier revisions;
sawlang's `runtime/ABI.md` at the pin (v0.24.3), sections "The program
entry", "The output lock", `__saw_rt_panic` and `__saw_rt_lock_owner`;
sawlang's `std/spinlock.saw` and `std/panic.saw` at the pin; spec §2.2, §2.8,
§3, §5c, §5.7, §8, §9a and §12; `kernel/abi/src/{ops,status,records}.saw`;
`kernel/core/{dispatch,sched,diag}.saw`; `rt/common/src/lib.saw`;
`kernel/sysapi/src/{rt,floor,system,bootset,pipe,thread}.saw`;
`root/src/main.saw`; `tools/sosimage/src/{declaration,imagefile,plan}.saw`;
`tools/sos_runner.py`'s panic and stats cases; the S3 ABI's §7d; SO-137 (the
console driver `svc-console`, `uartproto`'s `Uart<T>` and the `uartpipe`
transport) and the allocator (SO-142), each read in its unmerged worktree;
SO-138, SO-139 and SO-141.

## 0. The shape in one page

- **A panic ends the process through a kernel op that takes the text.**
  `PANIC_OP`, made with `NO_HANDLE` in the handle register, ends the caller
  as `Faulted` with a new `FaultReason.Panic`, and the kernel writes the text
  on its own fault line. It needs no handle and no right. Every runtime end
  that cannot go on (a panic, an exhausted arena, a refused free, a failed
  start-up) goes through it, in every process, root included, so a
  supervisor reads one word for one kind of end. No part of a panic goes
  through `DebugPrint` or the console.
- **A new package, `sosmain` (`rt/main/`), is the process half of the
  runtime.** It exports `_start`, declares `extern "C" { func __saw_main() ->
  Int32 }`, holds the boot context, hands the console record to the write
  seam, and owns the process's end. A program opts in by depending on it and
  importing it. A package that exports its own `_start` and never imports
  `sosmain` builds as today.
- **`_start` is written in Saw** and does this: check that the boot word is a
  System and adopt it, derive the Process handle, drain the whole boot set,
  give the console record to the write seam, call `__saw_main`, send a
  `Sync`, and exit through the Process exit op with `main`'s status.
- **`main` borrows the System and Process, and is given the boot set.**
  `sosmain.context()` answers a one-word view of the start-up's frame whose
  `system()` and `process()` lend the runtime's own wrappers, which the
  runtime keeps for the process's life. `sosmain.take_boot_set()` hands the
  drained set over once.
- **An image names its console once:** `[image] console = "PROCESS.RECORD"`,
  a `uartproto` serve record. The builder adds a client record of that
  connection under `CONSOLE_TAG` to every other process and raises its handle
  floor by one. To the stock root it is an ordinary inlet record, so root's
  code does not change for it.
- **`print` goes over `uartproto`:** one `Write` message per line of up to
  `MAX_PAYLOAD` bytes, posted under a real output lock, falling back to a
  call (which parks and mints nothing) when the post is refused for room. With
  no console record, `print` uses the kernel's debug print if the System
  handle holds `Debug`, and is dropped otherwise, in every process. It never
  faults.
- **A panic flushes, then ends.** The panic hook claims the output lock,
  sends a `Sync` if anything was posted since the last one, so every earlier
  line is out before the kernel's line, and calls the op with the text.
- **Threads get a real entry:** `Process.thread_create(run:arg:stack_top:)`,
  on the caller's own Process only, takes a `FuncPointer<(UInt) sync ->
  UInt>`. A trampoline in `sos` runs it and exits the thread with its result.
  This is where SO-141's close hooks in.
- **On the S3, the windowed entry needs nothing extra.** `_start` and the
  trampoline are Saw functions compiled in the program's own module graph, so
  each opens with `entry`, and the S3 ABI's §7d start state already makes
  that correct.

The rest of this document gives the reasons, the alternatives, and the edges.

## 1. The start sequence

### 1.1 What the kernel already does

The kernel loads the image, zero-fills `.bss`, and starts the first thread at
the image entry. Its stack pointer is the top of the process's region and its
first argument register holds the boot word: `a0` on riscv32, `x0` on arm64,
`a2` and `a6` on the S3 (the S3 ABI's §7d). The boot word is the handle under
the tag the launcher started the child with, of whatever kind that record
is, or `NO_HANDLE` for a child started with no tag. The launch convention
(spec §12) makes it a System sibling, and the stock root follows it, but the
kernel does not require it: `stock_shutdown_faulted` starts a child on a
Memory record on purpose. So `_start` has no stack to set up, no data to copy
and no `.bss` to clear, and it cannot assume the word's kind. The start-up is
entirely Saw and has no per-arch assembly.

### 1.2 `_start`'s steps

```saw
@export("_start")
func _start(boot: UInt) unsafe -> Never
```

Every step that fails panics with a message naming the cause. The panic ends
the process through `PANIC_OP` (§4), which needs no handle, so a start-up
failure is reported at any step. A missing right would be a kernel fault
inside the call that needs it, with the kernel's own words and no message
from the runtime, so the start-up reads each handle's rights with `INFO_OP`
(ungated) before it spends them, and panics by name instead.

1. **Re-entry guard.** If the `STARTED` word is set, this is a second thread
   made at `ENTRY_IMAGE` (§5.4). The start-up panics with `sos: a thread
   entered the image entry; make threads with thread_create(run:)`.
   Otherwise it sets the word.
2. **Check and adopt the System handle.** If the word is `NO_HANDLE`, skip to
   step 6: the process has no System, no Process and no boot set. Otherwise
   the start-up asks `INFO_OP` for the word's kind; anything but a System
   panics with `sos: the boot word names a KIND, not a System`. It then
   adopts the word with `System(boot_handle:)`, which parks it for the write
   seam as today, and reads its rights with `INFO_OP`.
3. **Derive the Process handle.** A System without `ProcessSelf` panics with
   `sos: the System handle lacks ProcessSelf`. Otherwise
   `system.process_self()`; a refusal, which can only be the `Handle` quota
   or a full table, panics with `sos: no process handle: STATUS`.
4. **Drain the boot set.** The start-up reads the derived handle's rights; a
   ceiling that took `BootHandles` away panics with `sos: the Process handle
   lacks BootHandles`. Otherwise `BootSet(process:)` drains every record. A
   drain that ends other than in `Drained` panics with its status. The drain
   allocates its two vectors from the arena's heap.
5. **Give the console record to the write seam.** `set.take(CONSOLE_TAG)`. If
   the record exists it must be an inlet. Any other kind is a skew between the
   builder and the runtime, and panics with `sos: the console record is not
   an inlet`. The inlet goes to `sos.rt.adopt_console` (§3.3), which takes it
   for the rest of the process's life.
6. **Publish the boot context.** The start-up keeps the System and Process
   wrappers, and the boot set until `main` takes it, in one `Boot` value in
   its own frame, and parks that value's address for `sosmain.context()`
   (§2). A static cannot hold them at the pin: statics must be trivially
   destructible, and each wrapper has a `deinit`. `_start`'s frame outlives
   `main`, because `main` runs inside the call in the next step.
7. **Run the program.** `let status = __saw_main()`. The result is 0 to 255.
   An `Err` has already been written as `error: ...` and comes back as 1. An
   out-of-range `Int` has already panicked and did not return.
8. **Flush.** `sos.rt.console_sync()`, which sends a `Sync` if a write was
   posted after the last one. A failure here is ignored, because a driver that
   is gone leaves nothing to flush to.
9. **Exit.** `process.exit(status)`, through the runtime's own Process handle.
   This does not return. A Process ceiling without `Exit` (read in step 4)
   panics with `sos: main returned STATUS and the Process handle lacks Exit`,
   so the status is at least on the line. A process with no Process handle
   panics with `sos: main returned STATUS with no Process handle to exit
   through`. The function ends in `while { }` so that the type checker's
   `Never` is honest.

### 1.3 What a `main` program writes

```saw
import sosmain
import uartpipe

func main() -> Result<Void, SosStatus> {
    var set = sosmain.take_boot_set()
    var record = set.take(TAG_UART)!
    let uart = uartpipe.uart(record.take_inlet()!)
    borrow let proc = sosmain.context().process() {
        ...
    }
    print("served {} requests", n)
    return
}
```

A lent place is named by a block that opens its own window (`borrow let`),
or used for one statement (`sosmain.context().process().exit(3)`); a `let`
cannot hold it.

Its `Saw.toml` adds `sosmain = { path = ".../rt/main" }` beside `sos`, and
keeps `runtime = true` because the image's compile is still where the seams
land.

### 1.4 Where `_start` lives: rejected alternatives

- **In the `sos` facade.** Every package imports `sos`, so every package would
  get a `_start`, and each of the existing `_start` packages would be refused
  at compile time: sawc rejects a second `@export` of one symbol across the
  program (`export.duplicate`). That breaks the ruling that old packages keep
  working.
- **In a module of `sos` that the facade does not import.** Only an import
  compiles a module into the unit (`lib.saw`'s note on `import sos.rt`), so
  this would be opt-in too. But the start-up reads `uartproto`'s op bytes and
  payload size, and `bootplan`'s reserved tag. Putting it in `sos` would make
  the kernel's userspace API package depend on a driver protocol and on the
  builder's plan types.
- **In `rt/common` (`sosrt`).** That package is role-free and is linked into
  the kernel, whose `boot.S` defines `_start`.
- **The linker picks the entry** (`ENTRY(sos_start)` for a `main` program).
  `sos` would then always define a start-up that references `__saw_main`, and
  an old package defines no `__saw_main`. Whether a garbage-collected section
  can keep an undefined reference is the linker's choice, and nothing here
  should rest on it.

A separate package has one cost: a `main` program that touches no accessor
still has to write `import sosmain`. If it forgets, the link has no `_start`,
and `ld.lld` only warns about a missing entry symbol. So each HAL's `user.ld`
gains `ASSERT(DEFINED(_start), "no _start: a main program imports sosmain")`.
That turns the warning into a link error that names the fix. Importing
`sosmain` without a `main` fails at link on `__saw_main`. Importing it while
also exporting `_start` is refused earlier, by sawc (`export.duplicate`,
naming both modules). Both errors are clear.

## 2. The boot context

### 2.1 The accessors

```saw
public func context() -> Context          // one word: the frame value's address
extension Context {
    public func system(&self) borrows -> &System
    public func process(&self) borrows -> &Process
}
public func take_boot_set() -> BootSet    // once; a second call panics
public func flush()                       // the console's Sync, if there is one
public func exit(code: UInt) -> Never     // flush, then Process exit
```

`Context` is a copyable one-word view of the `Boot` value in `_start`'s
frame. Each of its accessors is safe, and lends through a private `unsafe`
accessor that casts the word to a pointer and lends the field: a lend rooted
in an indirection out of the receiver, which the language admits. The
runtime keeps the wrappers for the whole life of the process.

- **System.** `main` can print through it, read the clock, and mint siblings.
  Root mints each child's System this way already. `main` cannot give the
  original away, because `give(system:)` takes it by value and a lent place
  cannot be moved. That restriction is deliberate: the write seam's debug
  path uses that word.
- **Process.** Exit, `thread_self`, waiters, give, map: everything works
  through `&Process`, `thread_create(run:)` included (§5.1). A second, owned
  handle is still available from `system().process_self()`, which mints a
  fresh one.
- **BootSet.** Handed over, not lent. The runtime needs nothing from the set
  once step 5 has taken the console record, so `take_boot_set()` moves it out
  of the `Boot` value, and `main` owns it: it may store it in a structure and
  drop it when it chooses, as the stock root does (§8). `main` takes records
  from it by tag. A record nobody takes is released when the set is dropped,
  or when the process ends if `main` never took the set. A second
  `take_boot_set()` panics.

A child started with no System has no Process and no boot set either.
`system()`, `process()` and `take_boot_set()` then panic with a message that
names the cause. This is the one case where the accessors are not total. It
is rare, so making every caller unwrap an Optional for it would be the trade
the Optional discipline rules out.

### 2.2 What the pin compiles, and what changes later

Probed with sawc 0.24.3 by a hosted build and run of the shape above: the
`Context` form compiles, builds and runs, a safe accessor forwarding the
lend of an `unsafe` one included. A receiverless `borrows` function
(`sosmain.system()` lending through a parked address) panics sawc's MIR stage
at the pin when it is called, which a typecheck alone does not show, and the
fix sawlang has since made refuses it (`lend.root`): a lend must be rooted in
the receiver's own storage. A method that lends a static is refused the same
way. So the boot context is a frame value plus its parked address, reached
through `Context`'s own field.

When sawlang lets a `borrows` accessor lend a static whose type has a
`deinit` (SL-1156), the `Boot` value moves from `_start`'s frame into a
`static BOOT: Boot?` that `_start` writes once, the parked address goes, and
`Context`'s accessors lend out of the static. Callers do not change.

### 2.3 Lent, not taken: the alternative

The rejected shape is a take-once `sosmain.boot() -> Boot` that moves the
System and Process into `main`. `main` could then drop or give its System,
and the runtime's parked word for that handle would go stale; every later
print through the debug path would be a `BadHandle` fault. The runtime would
have to mint its own Process separately, and it could not mint its own
System at all, since a child's System has no `Mint`. Lending keeps the word
valid by construction. The panic path reads no parked handle (§4), so a
stale word cannot turn a panic into a `BadHandle` fault in any process.

### 2.4 `System(boot_handle:)`

It stays public, with its signature and behaviour unchanged. It is the typed
layer's one crossing (the `sos` module docstring), and the start-up is its
caller under `main`. Old packages and the raw-altitude tests keep writing
it. Its doc gains a line: a `main` program never writes it, and `sosmain`'s
start-up adopts the handle. The comment on the parked word changes too,
since the panic path does not print through it. Nothing retires it, since
hand-built `_start` tests will always exist.

## 3. The console

### 3.1 How an image names it

```toml
[image]
name = "box"
board = "esp32s3"
console = "console.uart"       # PROCESS.RECORD: a serve record of uartproto
```

- The value names a `serve` record whose protocol is `uartproto`, using the
  grammar `clients` already uses. The builder refuses, each by name: a
  reference to no such process or record; a record that is not a serve; a
  protocol other than `uartproto`; and a `console` key in an image with a
  hand-written root. The last is refused because a hand-written root gives
  its own records, and its description says what it gives.
- Every stock-root process except the one serving that record gets one more
  record: a client inlet of that connection under `CONSOLE_TAG`. It is
  appended after the process's declared records, so their order is
  unchanged. In `imageplan` it is an ordinary `Record` with `source: Inlet`
  and the connection's index. Root mints it with `CLIENT_INLET`, exactly as
  it mints a declared client, so **the stock root does not change**.
- A process that also declares its own client of the console driver, such as
  the product template's `hello`, holds two inlets of one connection. That is
  harmless.
- **The handle counts.** A child's floor is its declaration's
  (`d.handle_floor()`, `tools/sosimage/src/plan.saw`), which knows nothing of
  the appended record, so the builder adds one to the floor of every process
  that gets it. A declared `handles` row that sat exactly at the old floor is
  then refused by name in a console image. A row sized to a process's peak
  use rather than its start, as the S3 drivers' are (`svc-gpio-esp32s3`'s
  17, `svc-pwm-esp32s3`'s 14, each counted in its `process.toml`), passes the
  floor and still has no room for the record at its peak, so each adds one
  when it goes into a console image. Root's per-step check already counts
  the inlet it mints per child (the `Inlet` step). `print` needs no further
  row: the writer never holds a claim (§3.3).
- The console driver is the image's choice. SO-137's `svc-console` serves the
  kernel console on every board, through its own `Debug`; on a gate board an
  image may name `svc-uart-ns16550` or `svc-uart-pl011` instead. A client
  cannot tell which one it got.

**`CONSOLE_TAG`.** The runtime reserves a range of tags, `0xFFFF_FF00` to
`0xFFFF_FFFF`, and `CONSOLE_TAG` is the first of them. The values fit every
board's word, and the rest of the range is spare for later runtime records,
such as SO-139's. The declaration reader refuses, by name, a declared tag
inside the range. The constants live in `bootplan`, the dependency-free
package that the builder and root already share for the launch convention,
and `sosmain` depends on `bootplan` for them. `sosabi` is the wrong home: the
kernel assigns tags no meaning (spec §12), so a reserved tag in the kernel's
ABI module would be a category error.

**Alternatives rejected:**

- **A `kind = "console"` record in each `process.toml`.** Every package would
  repeat the same boilerplate, and the owner wants the console on by default.
  Whether a process has a console is also a fact about the image, not the
  program: one package can run in an image with a console and in one without.
- **A board-level default console driver** that the builder adds to every
  image. That would be a process nobody wrote down. The S3 has four child
  slots, and the box fills them, so a hidden fifth would not fit.
- **A reserved record kind instead of a tag.** Record kinds are the kernel's
  `BootHandleKind`. The record is an inlet, and its role is the launcher's
  word, which is exactly what a tag is (spec §12).
- **An opt-out** (`console = false` in a declaration) would serve a process
  too tight to hold one more handle. No such process exists, so it waits for
  one.

### 3.2 How the runtime takes it

The start-up takes it in step 5, before `main` can see the boot set, so
`main` cannot take it or reach the console client. The set's listing still
names it: `entries()` and `len()` report every record the drain met, taken
or not (`kernel/sysapi/src/bootset.saw`), so `main` sees an entry under
`CONSOLE_TAG`, already taken, and `take(CONSOLE_TAG)` answers `None`. A tag
in the reserved range is the runtime's, which is how a program listing its
set tells it apart. A program that reads from a real UART driver takes its
own client record of the driver, as `hello` does.

### 3.3 What `print` becomes

Today `rt/common` calls the per-side hook `sos_rt_write(ptr, len)` once per
part and once more for the newline, and the process side turns each byte into
a `DebugPrint` trap. The new path works like this:

- **The seam owns the console.** `sosmain` lives outside `sos` and cannot
  read an inlet's word (`PipeInlet.handle` is `public(package)`), and the
  writer at the floor needs one. So `sos.rt` gains two calls:
  - `adopt_console(inlet: PipeInlet, write_op: UInt8, sync_op: UInt8)`
    consumes the inlet, parks its word for the writer, and leaves nothing
    to release it: the runtime holds the handle until the process ends.
    The op bytes come from the caller, `sosmain`, which reads them from
    `uartproto.UartOp`, so `sos` names no driver protocol. `sosmain` holds
    `uartproto.BODY_BYTES` equal to `PIPE_BODY_BYTES` with a static assert,
    as `uartpipe` does.
  - `console_sync() -> Result<Void, SosStatus>` sends a `Sync` if a write was
    posted since the last one, and answers at once otherwise.

  The runtime does not use the typed client `Uart<T>`: it speaks
  `uartproto`'s wire, with `uartproto`'s constants, at the floor. A program
  that wants the typed client takes its own record.
- **One message per line, under the output lock.** `rt_write_line` in
  `rt/common` takes the output lock (§3.5), assembles the line in a buffer on
  its own stack, in chunks of at most `LINE_CHUNK` bytes, hands each chunk to
  `sos_rt_write` once, and gives the lock back. `LINE_CHUNK` is a constant
  equal to `uartproto.MAX_PAYLOAD`, and a static assert in `sosmain` holds
  the two equal. A line that fits is one message, a longer line consecutive
  messages, and no other thread's line lands between them. The buffer is on
  the calling thread's stack, so two threads printing share no buffer. The
  kernel's `print` sites pay the same stack.
- **The writer.** On the process side, `sos_rt_write` sends each chunk to the
  console when one is adopted. It builds a `Write` body on the stack, the
  write op byte followed by the bytes, and posts it through the floor's
  `sos_pipe_inlet_post`, a tell. It then releases the claim, so a line costs
  two traps and never waits for the driver. The post mints its claim into the
  caller's table, so it can be refused for room (`NoResource`,
  `QuotaExceeded`) as well as for a full ring (`WouldBlock`). On any of the
  three the same body goes through `sos_pipe_inlet_call` instead, which mints
  no claim: it parks until there is room and then until the driver answers,
  and its reply carries no handles. That is back-pressure, and it needs no
  tick: a client that fills the ring blocks, which is when the driver runs.
  So no process needs a `Handle` row for printing. A driver that is gone
  (`PeerClosed`) means the bytes are dropped.
- **The call's reply buffer** is a whole delivered-message record
  (`PipeMsgRecord`) on the stack, because the kernel checks the reply buffer
  against `pipe_msg_record_bytes()` of the caller's memory and writes that
  much: 160 bytes on a 32-bit profile and 176 on a 64-bit one, not a body's
  128. The `Sync` uses the same record.
- **Ordering.** A connection's ring is FIFO, and it is shared by every client
  of the console, so lines from one process arrive in order, and lines from
  different processes interleave at line boundaries.
- **`Sync` at the end.** The start-up's step 8, `sosmain.exit` and the panic
  hook (§4) each call `console_sync()` before the process ends. The reason is
  the transcript, not delivery: a posted message outlives its sender (spec
  §2.1), so the bytes would come out anyway. Without the `Sync`, though, they
  could come out after the kernel's exit or fault line and root's `ended`
  line, and the order would move between runs. `sosmain.flush()` exposes the
  same call to `main`. A driver that has stopped serving parks the `Sync`
  forever, at a normal exit as at a panic: §3.6.
- **`__saw_rt_write`** is the no-newline piece that task dumps use. It goes
  out as its own message or messages, under the lock, unbuffered.
- **`__saw_rt_write_error`** goes through the same writer and takes no lock
  (ABI): the entry writes `main`'s `error:` line through it under the lock,
  and the executor's panic report writes the message and the task dump
  through it under a lock the report already holds.
- **Non-ASCII.** The bytes go out as given, as today's debug path sends them.
  `UartBody.text`'s `?` substitution applies only to a `String` handed to
  the typed client.

**Why the bytes skip `Uart.write`.** The ruling says `print` writes through
the typed client. In this design the wire and its constants are
`uartproto`'s, and the bytes go through the floor. Three reasons:

- The writer runs inside a panic: the executor's panic report writes the
  message and the task dump through `__saw_rt_write_error` before the
  process ends, and a check that tripped there would re-enter the panic. One
  writer that is check-free, allocation-free and stack-only is a single path
  to review.
- `Uart.write` takes a `String`, so `print` would have to build one, which
  allocates. The heap is real now, but an allocation inside the panic report
  is still out, and `print` would take the heap's lock inside the output
  lock on every line.
- The client keeps its body inside itself, so every write would have to hold
  the one client for the body's whole use. A body on each caller's stack
  shares nothing.

The alternative was a typed path for `print` with a separate raw path for the
report: two writers to keep in step, and the typed one not check-free.

### 3.4 No console record

The seam falls back in this order: the console; else the kernel's debug
print, if the System handle holds `Debug`; else the write is dropped. The
check is in `sos.rt`, shared by every process, `_start` packages included,
so it does not depend on `sosmain`'s start-up having run:

- At its first write with no console, the seam asks `INFO_OP` (ungated) for
  the parked word's kind and rights, and keeps the answer: `Debug` is held
  only when the word names a System whose rights carry it. A parked word of
  `NO_HANDLE` answers "no" without a call. Two threads that ask at once ask
  twice and keep the same answer.
- The question costs two system calls, once, in a process that prints. No
  asserted transcript counts them: the cases that assert a child's syscall
  count (`process_stats`, `process_isolation`, `death_fault`,
  `process_records`, the mapping husk cases) are of children that never
  `print`, and `process_stats`' own measured window is print-free.

Today a print without `Debug` is an `AccessDenied` fault that ends the
process. The check turns it into a dropped write. So once SO-138 withholds
`Debug`, an old package's `print` is dropped instead of being fatal; its
explicit `system.debug_print` calls still fault, since they are the typed
op. Its panics need no `Debug` at all (§4). Root, which creates the console,
and the console driver have no console record, so they print through the
debug path as now.

### 3.5 The output lock

`runtime/ABI.md` at the pin promises that a `print` line is never split by
another `print` at any length, and lets the lock be a no-op only for a
runtime with one thread. `main` programs have threads (§5), so the lock is
real.

`sosrt` exports `__saw_rt_output_lock` and `__saw_rt_output_unlock` as the
ABI states them: one process-wide lock, recursive, taken and given back by two
separate calls, and taken by the panic sink, which never gives it back.

It is a recursive lock whose state, an owner and a depth, is kept in a
`static SpinLock<OutputState>`. `lock` takes the `SpinLock` for a few
compares and stores: if the depth is zero or the owner is the caller
(`__saw_rt_lock_owner`), it records the caller and adds one to the depth;
otherwise it gives the `SpinLock` back and tries again, yielding between
tries through `__saw_rt_thread_yield` once std's spin-then-yield lands
(SL-1159) and spinning until then. `unlock` takes one from the depth and
clears the owner at zero. Nothing in the `SpinLock`'s window can panic or
allocate, so the window is never re-entered.

`SpinLock<T>` cannot be the output lock itself, for three reasons: its window
is one block, so it cannot be taken in `__saw_rt_output_lock` and given back
in `_unlock`; it is not reentrant, and the executor's panic report takes the
lock its own thread already holds; and a window always gives the lock back,
where the panic path keeps it. So std's `SpinLock` and the lock-owner seam
are the primitive, and the recursion is `sosrt`'s.

On the kernel side the owner is the kernel's one constant (the allocator's
kernel answer), and nothing contends.

**One stated departure from the ABI.** Its claim priority (a claim is served
before plain writes) and its one-second bound on a claim's wait are not
kept. The only claimer is the panic path, which writes nothing through the
stream: it waits only for a line already being written, and a holder parked
on a wedged driver hangs it, as §3.6 says.

The yield needs the calling thread's own Thread handle, because
`ThreadOp.Yield` is an op on a handle naming the caller; §5.3 says where it
lives.

### 3.6 A driver that stops serving

A `Sync` is a call, and a call parks until it is answered; it never answers
`WouldBlock` (`sos_pipe_inlet_call`). So a console driver that has stopped
serving, without its connection going (which would answer `PeerClosed` at
once), parks every `Sync` for good: the panic hook's, and the one at a
normal exit, step 8 and `sosmain.exit`. The process never ends, and its
supervisor sees no death until it kills it.

The owner kept the `Sync` on the panic path, trading this case for ordered
transcripts. The normal exit makes the same trade on the same terms: every
process that prints more lines than the ring holds already parks on the same
driver, so an image whose console driver is wedged is already broken, and a
supervisor's `kill` is the way out in both cases. So the `Sync` stays at a
normal exit too. A process that never posted since its last `Sync` sends
none and is not exposed.

## 4. A panic ends the process through the kernel

### 4.1 The op

`PANIC_OP` (`0xFFFC`), made with `NO_HANDLE` in the handle register, is
intercepted by the dispatch before the table lookup. It takes the text's
address and its length, and ends the calling process as `Faulted` with
`FaultReason.Panic` (14, the next free number; tag text `the program
panicked`). The kernel writes the text on its fault line, then tears the
process down as at every fault. It does not return.

- **Which object.** None. The owner's words name the System object; this
  design recommends the op name no object, for four reasons, and open
  question 2 puts the choice to the owner.
  - **Reach.** Every process can end itself this way: a child started with no
    tag, one started on a word that is not a System, and a `_start` package
    that released its System, included. A System op leaves them to a
    `BadHandle` or `BadOp` fault with no text.
  - **Robustness.** The panic path reads no parked word, so damaged statics
    cannot turn a panic into a `BadHandle` fault.
  - **No rights exception.** Every System op today is gated on a right of its
    own. A panic must be reachable whatever a launcher's mask withheld, so a
    System op would be the first one no mask can remove.
  - **It grants nothing.** A process can already end itself without any
    authority, by naming a handle it does not hold. This op ends it the same
    way, with a reason a supervisor can tell apart and a line a reader can
    use.

  The cost is one stated exception to "every syscall is an object op"
  (spec §5.7) and one test in the dispatch before the lookup. As a System op
  (`SystemOp.Panic`, the next number, ungated) everything else in this
  section holds as written, except the rows of §4.4 a process without a
  System reaches.
- **Rights.** None. It ends the caller, and only the caller. It is no print
  channel: it is made once, its line is bounded, and the caller does not
  survive it. A process with neither `Debug` nor a console gains exactly one
  line, at its death, naming itself.
- **No code.** The text says why. A status word has one code per end, and
  the code is the reason, `Panic`; a second number the runtime chose would
  be a second vocabulary for the same fact.
- **What a panic does is the kernel's.** Today a line and a death. A later
  design can hold the process for an inspecting process (SO-139) instead of
  tearing it down, with no runtime change.

### 4.2 The text and the line

- **The bound.** The kernel reads at most `PANIC_TEXT_BYTES` (256, in
  `sosabi`, republished by `sos`) and cuts a longer text there. The kernel
  writes its console with interrupts masked, so the bound caps the latency
  one panic adds; 256 bytes is about 22 ms at 115200 baud.
- **The door.** The text is read through the copy-in door on its terms
  (spec §2.2, §5.7): word-aligned, wholly inside one non-device row of the
  caller that permits read, no byte of it executable; a zero length is legal
  anywhere and reads nothing. The whole buffer is checked before a byte of
  the line is written, then copied in chunks through the door into a small
  kernel stack buffer.
- **A buffer the door refuses** still ends the caller as `Faulted (Panic)`.
  The line says the text could not be read and why, in the door's words, in
  place of the text: `text unreadable: buffer is outside the process's
  memory`. The op's contract is that the process ends as a panic, so the
  runtime's last act has one outcome; the door's reason is still on the line
  for whoever debugs the runtime.
- **Sanitising.** The bytes are untrusted and land in a kernel line. One
  trailing line break is dropped (the compiler's messages end with one), and
  every other byte outside printable ASCII (0x20 to 0x7E) is written as `?`:
  line breaks, escape sequences and every byte of a multi-byte character. The
  compiler's cut marker `…` therefore reads `???`; the runtime marks its own
  cut with `...`.
- **The line.** The fault line every other reason prints, with the text as its
  last field:

  ```
  SOS: process fault: the program panicked process=0x00000001 text=panic at …
  SOS: process teardown handles=… process=0x00000001
  ```

  The text is last, so nothing it holds can move a field before it, and with
  its line breaks replaced it cannot start a line of its own. Every existing
  fault-line expectation keeps its shape.

### 4.3 The runtime's side

`rt/common`'s per-side abort hook gains the text:

```saw
extern "C" {
    func sos_rt_write(ptr: UnsafePointer<Int8>, len: UInt)
    func sos_rt_abort(ptr: UnsafePointer<Int8>, len: UInt,
                      code: UInt32) -> Never
    func sos_rt_lock_owner() -> Int
}
```

`rt_panic` claims the output lock, as the ABI's panic sink does, and calls
`sos_rt_abort(ptr, len, AbortCode.Panic)`. The allocator's two refusals call
it with their own line and code: `sos: out of arena memory` and `sos: bad
free`. None of them writes through `sos_rt_write` any more.

- **The kernel's answer** (each board's `hal/*/kernel/lib.saw`) writes the
  text to the UART and stops the machine through `exit_fail(code)`, which
  applies `abort_status`, as it does now in two calls. The kernel's own
  panics, and their transcripts, do not change.
- **The process's answer** (`sos.rt`) does three things, and reads no handle
  but the adopted console's:
  1. `console_sync()`, a raw floor call on its own stack;
  2. it copies at most `PANIC_TEXT_BYTES` of the text into a word-aligned
     buffer on its stack, ending a cut copy with `...`, so the door's
     alignment and execute rules hold whatever the text's source (the
     compiler's stack scratch, or a literal in read-only data);
  3. it calls `sos_panic(buf, n)`, the floor's export of the op, and ends in
     `while { }`.

  `code` is not read: the kernel's line and reason say it all. The copy uses
  `UnsafeMemory` and wrapping arithmetic, check-free on the standard
  `rt/common`'s header sets: read the `-Oz` disassembly of the hook on each
  architecture. A panicking thread needs the compiler's 512 bytes of message
  scratch, this 256-byte buffer and, with a console, the `Sync`'s message
  record (§3.3) on its stack; a thread that has less takes a hardware fault
  on its stack instead.

`DebugPrint` has no part in the panic path any more, in any process. It
stays `print`'s fallback when a process has no console.

The executor's panic report (std's, once SO-141 runs tasks) writes the
message and the task dump over the console through `__saw_rt_write_error`
and then calls `__saw_rt_panic` with a length of zero. The kernel's line then
ends at `text=`, after the report, which the `Sync` puts ahead of it.

### 4.4 What the status says, and what the text reaches

The text does not depend on the console's state. Every row ends in the
kernel's line unless it says otherwise.

| Condition | The text | The status word (child) |
|---|---|---|
| Console present, driver alive | the line, after every line posted before the panic | `Faulted (Panic)` |
| Ring full | the `Sync` parks for room, then the line | `Faulted (Panic)` |
| Driver gone (`PeerClosed`) | the `Sync` answers at once; the line | `Faulted (Panic)` |
| Driver alive but wedged | the process parks in the `Sync` and never ends (§3.6) | `Killed`, once killed |
| No console, any rights | the line | `Faulted (Panic)` |
| Started with no System, or on a word that is not one | the line | `Faulted (Panic)` |
| Text buffer refused by the door | `text unreadable: …` | `Faulted (Panic)` |
| Parked console word corrupted | none: the `Sync` is the `BadHandle` fault | `Faulted (BadHandle)` |
| Arena exhausted, bad free, start-up failure | the line, with the runtime's message | `Faulted (Panic)` |
| Root, any of the above | the line, then the teardown | the machine stops with 64 |

**Root.** A root that panics stops the machine after its teardown, with the
kernel's new `ExitCode.Panic`, whose number is `sosrt`'s `AbortCode.Panic`,
64, read from there so the two cannot drift: a panic stops the machine with
64 whichever side raised it. Arena exhaustion and a bad free in root stop it
with 64 too, where they stopped it with 65 and 66; the line says which. The
teardown line is the one a root's `Exit` prints, which is why root's normal
end keeps `shutdown` (§7); at a panic the line is wanted.

What a corrupt process cannot report at all is SO-139's job: a separate
process with the authority to read another's state.

### 4.5 The spec text

Ready to paste, each block with where it goes. Section signs inside the
quoted blocks are the spec's own.

**Spec §8, a new block after the paragraph "A process can also be ended by
another", before "What is built":**

> **A process can end itself as a panic.** `PANIC_OP`, made with `NO_HANDLE`
> in the handle register, ends the calling process as `Faulted` with the
> reason `Panic`, and the kernel writes the text the caller hands it on its
> fault line. It is how a runtime ends a process that cannot go on (a panic,
> an exhausted arena, a refused free, a failed start-up), so a supervisor
> reads one word for every such end, and what a panic does is the kernel's.
>
> - **It names no object and needs no right.** The dispatch intercepts it
>   before the table lookup (§5.7), so every process can reach it, one
>   started with no tag and one that holds no handle included. It grants
>   nothing: a process can already end itself by naming a handle it does not
>   hold, and this op ends it the same way with a reason a supervisor can
>   tell apart. It is no print channel: it is made once, its line is bounded,
>   and the caller does not survive it.
> - **Its arguments** are the text's address and its length. The kernel reads
>   at most `PANIC_TEXT_BYTES` and cuts a longer text there. The text is read
>   through the copy-in door on that door's terms (§2.2): the whole buffer is
>   checked before any of the line is written, and a zero length reads
>   nothing at any address. A buffer the door refuses ends the caller the
>   same way, and the line carries `text unreadable:` and the door's reason in
>   place of the text.
> - **The line** is the fault line with the text last:
>   `SOS: process fault: the program panicked process=N text=TEXT`. The text
>   is untrusted: one trailing line break is dropped, and every other byte
>   outside printable ASCII (0x20 to 0x7E) is written as `?`, so the text can
>   neither start a line nor move a field before it. The teardown line
>   follows, as at every death.
> - **What a supervisor reads** is `Faulted` with `FaultReason.Panic` (14) in
>   the code, through `get_status` and the wait record alike. The text is not
>   in the word.
> - **Root.** A root that panics stops the machine after its teardown with
>   the kernel's `Panic` exit status, the one a panic in the kernel stops it
>   with, so a panic reads the same whichever side raised it.
> - **Its answers, in order.** `PANIC_OP` with any word but `NO_HANDLE` in the
>   handle register is looked up as every op is: a word that names nothing is
>   the `BadHandle` fault, and a handle naming an object is the `BadOp` fault,
>   since no kind's table reaches the number. The op itself has no refusal.
> - **Cost.** A fault's: one teardown inside the caller's trap, and a line no
>   longer than `PANIC_TEXT_BYTES` beyond the fault line's own fields, written
>   with interrupts masked (§9a's reclaim).

**Spec §5.7, the "Dispatch" paragraph, after "Process exit is a Process
op.":**

> A panic names no object: `PANIC_OP` with `NO_HANDLE` in the handle register
> is intercepted before the table lookup and ends the caller (§8).

**Spec §5.7, the paragraph "Rights-gated ops are the whole of the surface",
at its end:**

> The one op that names no object, `PANIC_OP` (§8), is not a facility: it
> ends its caller, which a process can already do by naming a handle it does
> not hold, and it reaches nothing else.

**Spec §5.7, the paragraph "Growing an op costs no register and no ABI":**
each table's assert becomes `< PANIC_OP`, the lowest number of the band.

**Spec §3, after the `INFO_OP` bullets:**

> - **`PANIC_OP` sits below `INFO_OP`, at `0xFFFC`.** It is not a universal op:
>   it names no object and is intercepted before the lookup, with `NO_HANDLE`
>   in the handle register (§8). It takes its number from the band so that no
>   per-kind table can grow into it, and every table stays below it.

**Spec §2.8, after the bullet "`exit` is not here":**

> - **Nor is a panic**: a process ends itself as a panic with `PANIC_OP`,
>   which names no object (§8), so a process holding no System handle can
>   still report one.

**Spec §5c, the paragraph "The panic path is check-free by construction",
replace from "A child's panic ends in a `shutdown` request" to "dies by
`BadHandle`." with:**

> A process's panic ends in `PANIC_OP` (§8): the runtime hands the kernel the
> message, the kernel writes it on its fault line and ends the process
> `Faulted (Panic)`, root included, whatever handles the process holds.
> Nothing of a panic goes through `DebugPrint`.

Spec §9a needs nothing: its reclaim list already names a panic.

### 4.6 The code

- `kernel/abi`: `PANIC_OP = 0xFFFC` in `ops.saw`, each table's assert
  against it, and `PANIC_OP < INFO_OP`; `PANIC_TEXT_BYTES` beside it;
  `FaultReason.Panic = 14` in `status.saw`.
- `kernel/core`: the interception at the head of `dispatch`; the line and the
  chunked copy beside it, since the copy doors live above the scheduler; the
  end through `end_process(p, Faulted, Panic, ExitCode.Panic)`;
  `ExitCode.Panic` in `diag.saw`, which makes `sosrt`'s `AbortCode` public.
- `kernel/sysapi`: `sos_panic` in the floor, the C altitude's export, and
  `sos.rt`'s new abort hook. No typed wrapper: a Saw program panics with
  `panic`.
- `rt/common`: the hook's shape, `rt_panic`, and the allocator's two
  refusals. Each HAL's kernel abort hook takes the text.
- `tools/sos_runner.py`: `EXIT_ARENA_EXHAUSTED` and `EXIT_BAD_FREE` retire;
  `EXIT_PANIC` is the kernel's `ExitCode.Panic`.

### 4.7 The transcripts that move

The kernel's own panic cases do not move: `panic_seam`, `panic_from_check`,
`task_dump` and `task_dump_empty` are kernel entries, and the kernel's hook
writes and stops as before.

Every case that ends in a root's panic moves. Root's message, which it
printed through `DebugPrint` before stopping the machine with 64 through
`shutdown`, is now the text of the kernel's fault line, followed by root's
teardown line, which `shutdown` never printed; the machine still stops with
64, and the asserted substrings sit inside the text, so the expectations hold
unchanged:

- `stock_skew_no_row`, `stock_skew_not_allocation`, `stock_skew_not_device`,
  `stock_skew_outlet_twice`, `stock_skew_used_after_given`.

The runtime's other root ends move, and their status with them:

- `arena_small`: `sos: out of arena memory` becomes the fault line's text and
  the status 65 becomes 64 (`expect_status` changes);
- the allocator's `double_free` and `stray_free`, once it merges: `sos: bad
  free` likewise, and 66 becomes 64.

No case asserts a child's panic: every child `panic` in the tree is
defensive and never reached on a passing run. A child's panic, which today
reads `Faulted (AccessDenied)` (or `BadHandle` with no tag), reads
`Faulted (Panic)` in every process from this unit on, `_start` packages
included, because the abort hook is shared.

## 5. A thread's entry

### 5.1 The API

```saw
extension Process {
    /// Make a thread of the calling process that runs `run(arg)` on the
    /// stack below `stack_top` and exits with what it returns. Not started:
    /// `start()` it. Only through the caller's own Process handle.
    public func thread_create(run: FuncPointer<(UInt) sync -> UInt>,
                              arg: UInt,
                              stack_top: UInt) -> Result<Thread, SosStatus>
}
```

This is in `sos`, not `sosmain`, because a `_start` package can use it too.
It is buildable now: `FuncPointer` and `address()` are implemented at the pin.
So the doc of the `ENTRY_IMAGE` overload, which says Saw cannot name a
function's address, is stale, and the thread unit corrects it.

**Self-only.** Today's factories act for whatever process the handle names
(`kernel/sysapi/src/system.saw`, `Process`'s doc). This one cannot: the start
record is written in the caller's memory, the thread's own handle lands in
the caller's table, and the trampoline's address is the caller's image's.
Through a child's handle each would be the wrong process's, and on a
translating profile the address can even be executable in the child, so the
kernel's entry check would not catch it. So `Process` gains a private flag
saying the wrapper names the caller: set by `System.process_self()`, carried
by a `mint`, and set on the start-up's own handle; a wrapper that arrives any
other way (`process_create`, a give, a message) has it clear. Through a
wrapper without it, `thread_create(run:)` panics with a message naming the
rule: which process a handle names is its holder's to know, the reason
`thread_self` and `boot_handle_next` fault on another's handle.

### 5.2 How it works

1. **A start record.** It is written just below `stack_top` in the caller's
   memory: the entry's address, `arg`, the thread's own handle word, and an
   abandon flag. The stack given to the kernel starts at the record's base,
   rounded down to the 16-byte grain. The record lies inside the stack row,
   so the kernel's stack-top checks are unchanged.
2. **The thread's own handle** is a `Control`-only sibling minted from the
   created handle, at create time. A quota refusal is therefore reported to
   the creator as a status, and the thread never needs `thread_self` at its
   end, where a refusal would have nobody to report to. The handle is
   released by the thread's exit.
3. **A refused mint is unwound by running the thread.** The mint can only
   happen once the kernel has made the thread, and a thread that has not
   exited is never freed by losing its handles (spec §2.10), so dropping the
   created handle would leave its Thread row and slot charged until the
   process ends. Instead the creator writes the created handle's word into
   the record as the thread's own, sets the abandon flag, and starts the
   thread; the trampoline sees the flag and exits at once through that word,
   which releases it, and the exited thread with no handle left is freed.
   The creator gives up its wrapper without a release and answers the
   mint's status. The Thread and Handle rows then read back as before the
   call. A refusal by the kernel's create itself leaves nothing to unwind.
4. **The trampoline**, `sos.thread`'s `thread_entry(record: UInt) sync ->
   Never`, is created at `FuncPointer(thread_entry).address()`. It reads the
   record into locals, calls `run(arg)`, then calls `sos_thread_exit(self,
   result)` and `while { }`. Its address comes from Saw, not a C leaf.
5. **A panic on any thread** ends the whole process through `PANIC_OP`
   (spec §8's fault rule: there is no per-thread recovery).
6. **The ordering of ends.** The process ends when `main` returns, through the
   start-up's Process exit, whatever its other threads are doing, which
   matches the hosted rule. The kernel's last-thread rule
   (`kernel/core/sched.saw`: the last thread out ends the process with its
   own exit code) cannot fire in a `main` program: the main thread never
   thread-exits, since it stays in `_start` until the Process exit, so other
   threads ending first leave the process running. It still governs `_start`
   packages.

### 5.3 The thread register, and where SO-141 hooks in

- **The thread register.** The allocator (SO-142) makes the architecture's
  thread register each thread's own on every profile, arm64's `TPIDR_EL0`
  included, which was the kernel change SO-141 expected to make, and keeps a
  lazily numbered lock owner there. SO-141's executor word and the yield's
  own handle (§3.5) need a per-thread place too. So the register holds the
  address of a per-thread block: the start record (§5.2) for a
  `thread_create(run:)` thread, and a block in `_start`'s frame for the main
  thread. The block holds the thread's own Thread handle and SO-141's word.
  The address is word-aligned, nonzero and the thread's alone, so it is the
  lock-owner identity as it stands. A thread with no block, a raw
  `ENTRY_IMAGE` thread, keeps the allocator's lazy number, which sets the
  low bit so it never equals an address. The main thread's own handle is
  minted at start-up (`thread_self`, one handle row); without
  `ProcessRight.ThreadSelf` its spins do not yield. SO-141's brief takes
  this layout.
- **The close.** The trampoline calls `__saw_background_close()` between
  `run(arg)` and the thread exit. That is SO-141's line to add, once a sawlang
  carrying SL-1150 is pinned. `_start` needs no close, because `__saw_main`
  closes the main thread's group before it returns (ABI). Exit and a panic do
  not close, since the teardown frees everything.
- **The drive** goes in `sos`'s blocking wrappers (SO-141). The console
  writer and the `Sync` use the floor directly, not those wrappers, so a
  `print` never runs a background task in the middle of a line.
- **The panic report.** With tasks, std's panic sink writes the message and
  the task dump before the op, and the kernel's line carries no text (§4.3).

### 5.4 `ENTRY_IMAGE`

The overload stays for `_start` packages. In a `main` program a thread made at
the image entry would re-run the start-up, so the start-up's step 1 panics
with a message naming the fix. The overload is removed once no package in the
tree uses it. Raw-altitude tests pass `ENTRY_IMAGE` through the floor, which
keeps it.

## 6. The windowed entry on the S3

`_start`, the trampoline and `main` are compiled in one module graph. Blade
hands sawc the package's `--target-features +windowed` for the whole graph:
the program, `sos`, `sosrt`, `sosmain`, and `support.c` beside it (the S3
ABI's §7d). Under that build:

- **Every compiled function opens with `entry`,** `_start` included. The S3
  ABI's start state (`a2` and `a6` carrying the argument, `a4` =
  `0x7FFF_FFFC`, CALLINC = 1, pane 0 live) makes the entry a function reached
  by `call4`, with the boot word in its `a2`. The call4 rule for
  hand-written entries does not bind.
- **The call into `__saw_main`** is an ordinary compiled call in the windowed
  convention, because the caller and callee come from the same compile. That
  is the ABI's "windowed convention on windowed Xtensa", met by construction.
  A call0 `sosmain` calling a windowed `main` cannot happen, because the
  graph has one feature list.
- **The trampoline** calls the `FuncPointer` indirectly, also windowed. Its
  target comes from the same graph, or from a C unit compiled with the same
  list.
- **Neither ever returns.** Both end in an exit op and `while { }`, so the
  `retw` to `0x7FFF_FFFC` is never taken. The panic hook ends the same way.

So the S3 needs no S3-specific code. It needs cases: `windowed_saw_main`
(deep recursion in `main`, a `thread_create(run:)` thread deep in its own
chain, a print over `svc-console`, the status read by root) and a call0 twin.

## 7. The exit status

- **From `main`:** 0 to 255, per the ABI. A `Void` main gives 0, an `Err`
  gives 1, and an `Int` outside the range is a panic.
- **The runtime's own ends are faults.** A panic, an exhausted arena, a bad
  free, a failed start-up, the image entry re-entered and a `main` with no
  way to exit each end the process through `PANIC_OP`: the status word reads
  `Faulted` with `FaultReason.Panic`, `0x2000E` as a word, which root prints
  as `ended status=131086`. The line's text tells them apart. No code travels
  with the op, and no band of exit codes is reserved.
- **The kernel's own aborts are untouched.** A kernel panic writes its
  message and stops the machine with `abort_status(code)`.
- **A child's panic status changes for the better,** in every process, not
  only `main` programs: from `Faulted (AccessDenied)`, the refused `shutdown`
  a child's abort makes today (or `BadHandle` for a child with no tag), to
  `Faulted (Panic)`.
- **A program that wants more than 0 to 255** calls `sosmain.exit(code)`,
  which flushes and exits with any word. What a supervisor can read of it is
  the low 16 bits (spec §8), under the kind `Exited`, so no exit code can
  pass for a panic.
- **Root** keeps ending with an explicit `context().system().shutdown(code)`.
  Returning from `main` would exit root through `ProcessOp.Exit`, which stops
  the machine with the same status but prints root's teardown line, and that
  would add a line to every stock-root transcript. A root that panics stops
  the machine with 64 and prints it (§4.4).

## 8. Migration

`_start` packages keep working: they never import `sosmain`, so they get no
`_start` of the runtime's and no console. They do get the new panic path,
from the kernel op's unit on, and the debug path's rights check, from the
start-up's unit on, because both live in the shared `sos.rt`; §4.7 lists what
the first moves, and the second moves no transcript (§3.4). After that, every
image's hash moves once more when `sos.rt` and `rt/common` change for the
console, and no transcript does. That is the oracle for units 2 and 5.

The order in which packages move:

1. **The drivers**, after SO-137 merges: `svc-console`, `svc-uart-ns16550`,
   `svc-uart-pl011`, then `svc-gpio-esp32s3` and `svc-pwm-esp32s3`. Each
   `_start` becomes `main`. Each `proc.exit(EXIT_*)` becomes a returned
   status. The two UART drivers' `boot_handle_next` sequences become
   `take_boot_set()` and takes by tag, as the others already take theirs.
   Their transcripts must not move: in the existing images they have no
   console record and print through the debug path as before.
2. **The stock root.** Its `derive` step becomes the start-up's. `run` takes
   the `BootSet` by value from `sosmain.take_boot_set()` and moves it into
   `Rows` as it does today, so `supervise` still drops it where it does now
   and the release order is unchanged; the System and Process come from
   `sosmain.context()`. It keeps `shutdown`. The plan, the give order and
   every transcript are unchanged.
3. **The product template** (`hello`, `greeter`). Each becomes a `main`. The
   images gain `console = ...`, and `hello` drops its hand-written
   `uartproto` frames for `print`. The template is the user's first contact,
   so it shows the new shape.
4. **The box (SO-78).** It is written on `main` from the start: `console =
   "console.uart"` with `svc-console`, and its processes are console clients
   without `Debug`. The allocator lands before unit 1, so a `main` that runs
   forever may interpolate in its loop: a freed block is served again.
5. **Tests** convert when they are touched, not in one patch. New tests use
   `main` unless they are about the raw entry.

**The builder** (unit 4) gains the image's `console` key, the reserved range,
the plan record and the floor's extra handle. **The stock root** changes only
when it moves to `main` (step 2 above). The console record needs no root
change, because it is an inlet record.

## 9. Rights

With `print` over the console, a process needs `Debug` only if it has no
console: root, and the driver serving the console. A panic needs no right at
all (§4). That decides SO-138's default, as an amendment to its brief landing
after unit 4:

- **In an image that names a console**, a process whose `system` record
  names no rights gets `ProcessSelf | ClockGet | Transfer`, and `Debug` only
  by declaring it, as `svc-console` declares `rights = ["debug"]`. The real
  UART drivers declare it too when they serve the console, since they print
  their own lines through the debug path.
- **In an image with no console**, the default stays the four, so every
  existing image is unchanged.
- **This issue makes the withholding safe** for `print` and for a panic, in
  every process: the seam's rights check (§3.4) drops writes instead of
  faulting, and a panic's text reaches the kernel's line whatever the
  rights. A package that calls `system.debug_print` directly still faults
  without `Debug`, so such a package moves to `main`, or declares `debug`,
  before it goes into a console image.
- **Spec §12's launch convention** names the default set. The SO-138 unit
  that changes the rule changes that sentence, and so does unit 4 for the
  console record.

## 10. Units of work, in order

Each unit is one patch. The gate is `make sos-test`, which boots riscv32,
arm64 and riscv32-flat, and `make sos-smoke-esp32s3`, which runs every
patch's S3 cases; the ESP32-C3 smoke no longer gates. A start-up that every
process links reaches the S3's images and its link script, so every unit
here runs both. Each unit lists its mutants, the review standard: every
check and refusal goes red when removed or inverted.

**The kernel op comes first.** Every later unit ends through it: the
start-up's failures, the accessors' panics with no System, a thread's panic.
It also removes the exit-code band from the start-up's design, and it is the
one unit whose transcripts move, so landing it alone keeps every later unit
transcript-neutral, which is their oracle. It needs neither the console nor
the lend. It rewrites the lines of `rt/common` the allocator rewrites, so it
lands after the allocator (SO-142) merges.

**Unit 1: `PANIC_OP`.** The spec text of §4.5; `FaultReason.Panic`;
`PANIC_OP` and `PANIC_TEXT_BYTES` in `sosabi`; the dispatch's interception,
the line and the end; `ExitCode.Panic`; `sos_panic` in the floor; the abort
hook's new shape on both sides, without the console's `Sync`; the runner's
statuses (§4.6). Its tests prove:

- a child's `panic` reaches its supervisor as `ended status=131086`, with the
  fault line carrying its text and the teardown line after it, on every gate
  profile and the S3;
- the same for a child started with no tag, for one started on a Memory
  record, and for one whose System lacks `Debug`;
- a child's arena exhaustion ends the same way, with `sos: out of arena
  memory` as its text;
- hand-assembled payloads: a text holding a line break, an escape byte and a
  byte above 0x7E, ending in a line break (one line, `?` for each, the last
  break dropped); a length past `PANIC_TEXT_BYTES` (cut at the bound); a
  length of zero; a buffer outside the process (`text unreadable: buffer is
  outside the process's memory`, still `Faulted (Panic)`); a misaligned one
  (the door's alignment words);
- `PANIC_OP` on a live handle is `BadOp`, and on a stale word `BadHandle`;
- a Saw panic whose message passes the bound arrives cut, ending in `...`;
- root's panic: the stock-skew cases with the new lines and status 64, and
  `arena_small` at 64 (§4.7).

Mutants: the `NO_HANDLE` test dropped (a live handle's `PANIC_OP` panics
instead of faulting `BadOp`); the bound dropped; the sanitiser dropped; the
trailing-break rule dropped; the door's refusal reported as its own reason
instead of `Panic`; root's status; the runtime's alignment copy dropped.

**Unit 2: `sosmain`'s start-up and the boot context, with no console.** It
needs unit 1 and the allocator's lock-owner seam. The new `rt/main` package:
`_start` with its kind and rights checks, the `Boot` value, `Context` and its
accessors, `take_boot_set`, `exit`, `flush` (a no-op until unit 5), and the
re-entry guard. `sosrt` exports `__saw_rt_write_error` and the output lock
(§3.5). The task-dump kernel entries (`tests/taskdump.saw`,
`tests/taskdump_empty.saw`) export both seams themselves today, and drop
their copies, which would now be duplicates. `sos.rt` gets the debug path's
rights check (§3.4). Each `user.ld` gets `ASSERT(DEFINED(_start))`. Its tests
prove:

- `main` returning `Void`, `7`, `Err` (the `error:` line, status 1) and `300`
  (a panic, `Faulted (Panic)` with its text) each reach the status word
  exactly, as root's `ended` line prints it;
- the accessors' rights and records, taking by tag, a second
  `take_boot_set()` panicking, and untaken records released at the end (the
  Handle row read by a supervisor);
- a thread made at `ENTRY_IMAGE` ending the process with its message;
- each start-up refusal by name, `Faulted (Panic)`: a boot word that is a
  Memory, a System without `ProcessSelf`, a Process ceiling without
  `BootHandles`, and one without `Exit` (the status on the line);
- a child printing with no `Debug`, both a `main` program and a `_start`
  package (dropped, not faulted);
- on a no-tick image, a thread holding the output lock across a yield keeps
  another thread's line out of its own: the order is made by the lock, not
  by the clock;
- a windowed and a call0 `main` on the S3;
- the full suite's transcripts unchanged.

Mutants: each start-up check dropped (the kernel's fault replaces the named
panic); the seam's kind check dropped (a non-System word's rights read as a
System's); the lock's owner test dropped (re-entry deadlocks the report).

**Unit 3: a thread's real entry.** It needs unit 1. `thread_create(run:arg:
stack_top:)`, the self flag on `Process`, the trampoline, the start record,
the self handle minted at create with its unwind, and the stale doc
corrected. Its tests prove:

- threads with different entries and arguments join with their results;
- a thread's panic ends the process `Faulted (Panic)`;
- a quota refusal of the create, and one of the self handle's mint (a
  `Handle` row with room for the created handle and not its sibling), each
  return the status and leak nothing (the Thread and Handle rows read back);
- through a child's handle, `thread_create(run:)` panics with its rule;
- the same on the S3, windowed.

Mutants: the unwind dropped (the rows do not read back); the self flag's
check dropped.

**Unit 4: the builder's console.** `[image] console`, the reserved range in
`bootplan` and its refusal in declarations, the inlet record appended per
process, the floor raised by one for it, and the refusals named in §3.1,
with spec §12's lines. Its tests prove each refusal (builder tests, `blade
test`); the plan's records and floors for a console image (builder tests),
with a `handles` row at the old floor refused; a described image on riscv32
and arm64 whose `_start` client finds an inlet under `CONSOLE_TAG` and writes
a line through it to the real UART driver; and every existing description
reading clean.

**Unit 5: `print` over the console.** It needs SO-137 merged (`uartproto`'s
revision and `svc-console`) and units 2 and 4. `adopt_console` and
`console_sync` in `sos.rt`, `sosmain` giving the record over,
`rt_write_line` chunking under the lock, the raw writer falling back from
post to call on a full ring or a full table, and the `Sync` in step 8,
`exit` and `flush`. Its tests prove, on described images on each gate board
and the S3 with `svc-console`:

- lines of 0, 1, `LINE_CHUNK` and `LINE_CHUNK + 1` bytes arrive whole and in
  order, before the kernel's exit line;
- a no-tick image whose client prints more lines than the ring holds before
  it blocks, so the full ring is reached by construction and not by timing,
  with every line arriving in order;
- a client whose `Handle` row is full prints every line (the call path, which
  mints no claim);
- two threads' lines longer than `LINE_CHUNK` each arrive whole;
- root and the console driver still printing through the debug path;
- a client with no console and no `Debug` printing nothing and ending clean.

**Unit 6: the panic hook's `Sync`, and `error:` over the console.** It needs
unit 5. The hook's flush before the op, and `__saw_rt_write_error` through
the writer. The disassembly of the writer and the hook is read at `-Oz` on
riscv32, arm64 and xtensa, and the patch note says so. Its tests prove:

- a `main` that prints and then panics: every line before the kernel's fault
  line, status `Faulted (Panic)`;
- an `Err` main's `error:` line before root's `ended` line, status 1;
- a panic after the console driver has ended (the `Sync` answers at once;
  the line, `Faulted (Panic)`);
- a panic with the ring full.

**Unit 7 (SO-138, amended):** the rights default in §9. It needs unit 4.

**Unit 8: the real UART drivers and `svc-console` move to `main`.** It needs
unit 2 and SO-137 merged, since `svc-console` exists only then.
Transcript-neutral; image hashes listed.

**Unit 9: the S3 drivers move.** It needs unit 2. The same terms, on the S3
smoke.

**Unit 10: the stock root moves.** It needs unit 2. Transcript-neutral across
every stock-root case.

**Unit 11: the product template moves**, with its images naming the console.
It needs units 4 and 5: its images name the console, and its `print` goes
over it.

**After this issue:** the box on `main` (SO-78); SO-141 (the close and the
executor set, which a suspending `main` needs); test conversions as they are
touched.

Units 2 and 3 can run in parallel once unit 1 lands, and unit 4 needs
nothing before it. Unit 5 waits on SO-137's merge and units 2 and 4.

## Decided

1. **The package:** `rt/main`, named `sosmain`.
2. **Lending:** the frame value and its parked address, through the
   one-word `Context` whose accessors lend (it builds at v0.24.3, and the
   receiverless form does not); it moves to a static with no caller change
   when SL-1156 lands. The boot set is handed over, not lent (§2).
3. **A runtime's end:** a fault of its own, `Faulted (Panic)`, through a
   kernel op that takes the text; no band of exit codes (§4, §7).
4. **The panic path's `Sync`:** kept, and kept at a normal exit on the same
   terms (§3.6).
5. **A declaration opt-out** (`console = false`): not until a process needs
   the handle back.
6. **Interleaving:** not accepted; the output lock is real in SO-124, a
   recursive lock over std's `SpinLock` and the lock-owner seam, with the
   claim priority and bound the one stated departure (§3.5).
7. **The console client:** not exposed to `main`; the runtime owns the
   console's inlet at the floor (§3.3).
8. **A `main` program started with no System:** documented, not designed
   around; its accessors panic, and every end it reaches is `Faulted
   (Panic)` with a message naming the cause.
9. **SO-138's default:** `Debug` is dropped only in images with a console.
10. **Refusing runtime-band codes in `exit`:** gone with the band.
11. **A suspending `main`:** out of SO-124; it fails at link naming the seam
    until SO-141 exports the executor set, and SO-141's brief names it.
12. **The reserved range:** `0xFFFF_FF00` up, `CONSOLE_TAG` first.
13. **The line chunking:** in `rt/common`'s `rt_write_line`.
14. **A text buffer the copy-in door refuses:** `Faulted (Panic)`, with
    `text unreadable:` and the door's reason on the line (§4.2).
15. **The text's bound and the line's shape:** 256 bytes, printable ASCII
    passed and every other byte written as `?`, one trailing line break
    dropped, the text the line's last field (§4.2).
16. **Root's machine status for a runtime end:** 64, the kernel's
    `ExitCode.Panic`, for every one of them (§4.4).
17. **The thread register:** the address of a per-thread block holding the
    thread's own handle and SO-141's word; the allocator's lazy numbers set
    the low bit (§5.3).
18. **The debug path's rights check:** in `sos.rt`, asked once at the first
    console-less write, for every process (§3.4).
19. **`thread_create(run:)`:** self-only, by a flag the caller's own Process
    wrappers carry, and a refused self-handle mint unwound by running the
    thread to an immediate exit (§5).

## Open questions

1. **Every runtime end is `Faulted (Panic)`, wherever its text goes.** A
   panic, an exhausted arena, a bad free, a failed start-up and the image
   entry re-entered all end through the op, whether the process has a
   console, `Debug`, or neither. *Recommend yes.* Tying the reason to where
   the text could go would make one bug read differently in two images.
2. **The op's object.** Handle-free (`PANIC_OP` with `NO_HANDLE`), or an
   ungated System op, as the owner's words put it? *Recommend handle-free*
   (§4.1): it reaches a process with no System or with a boot word of
   another kind, reads no parked word, and puts no unremovable op on an
   object whose every op is gated. The System op differs only in the rows of
   §4.4 that a process without a System reaches.
