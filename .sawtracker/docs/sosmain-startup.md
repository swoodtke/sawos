# SO-124: processes written with an ordinary `main`

A design for the owner's review. Nothing here is built. It settles how a
process's start-up works once the runtime owns `_start`: the start sequence,
the boot context `main` reads, the console that `print` writes to, the panic
path, threads, the windowed S3, the exit status, migration, rights, and the
units of work.

Sources: the owner's rulings in SO-124's comments c1 to c5; sawlang's
`runtime/ABI.md` at the pin (v0.24.3), section "The program entry"; spec §8 and
§12; `rt/common/src/lib.saw`; `kernel/sysapi/src/{rt,floor,system,bootset,
thread}.saw`; `root/src/main.saw`; `tools/sosimage/src/{declaration,
imagefile}.saw`; `hal/xtensa-esp32s3/ABI.md` §7d; SO-137 (revision 2's
`uartproto` with `Uart<T>` and the `uartpipe` transport, read in its
unmerged worktree), SO-138, SO-139 and SO-141.

## 0. The shape in one page

- **A new package, `sosmain` (`rt/main/`), is the process half of the
  runtime.** It exports `_start`, declares `extern "C" { func __saw_main() ->
  Int32 }`, holds the boot context, takes the console record, and owns the
  process's end. A program opts in by depending on it and importing it. A
  package that exports its own `_start` and never imports `sosmain` links
  exactly as today.
- **`_start` is written in Saw** and does this: adopt the System handle, derive
  the Process handle, drain the whole boot set, take the console record under
  a reserved tag, point the write seam at it, call `__saw_main`, send a `Sync`,
  and exit through the Process exit op with `main`'s status.
- **`main` borrows the boot context:** `sosmain.system()`,
  `sosmain.process()` and `sosmain.boot_set()` lend the runtime's own
  wrappers. The runtime keeps them for the process's life, so the write seam
  and the exit path never use a handle `main` has released.
- **An image names its console once:** `[image] console = "PROCESS.RECORD"`,
  a `uartproto` serve record. The builder adds a client record of that
  connection under `CONSOLE_TAG` to every other process. To the stock root it
  is an ordinary inlet record, so root's code does not change for it.
- **`print` goes over `uartproto`:** one `Write` message per line of up to
  `MAX_PAYLOAD` bytes, sent as a tell, a full ring falling back to a call that
  parks for room, and a `Sync` before the process ends. With no console
  record, `print` uses the kernel's debug print if the System handle holds
  `Debug`, and is dropped otherwise. It never faults.
- **A panic goes over the same connection:** check-free, no allocation, a body
  on the panicking thread's own stack, the connection already open. Then a
  `Sync` and an exit with a status in a runtime band above 255 (`0x140` for a
  panic), so a supervisor can tell a panic from `main` returning 64.
- **Threads get a real entry:** `Process.thread_create(run:arg:stack_top:)`
  takes a `FuncPointer<(UInt) sync -> UInt>`. A trampoline in `sos` runs it
  and exits the thread with its result. This is where SO-141's close hooks in.
- **On the S3, the windowed entry needs nothing extra.** `_start` and the
  trampoline are Saw functions compiled in the program's own module graph, so
  each opens with `entry`, and §7d's start state already makes that correct.

The rest of this document gives the reasons, the alternatives, and the edges.

## 1. The start sequence

### 1.1 What the kernel already does

The kernel loads the image, zero-fills `.bss`, and starts the first thread at
the image entry. Its stack pointer is the top of the process's region and its
first argument register holds the boot word: `a0` on riscv32, `x0` on arm64,
`a2` and `a6` on the S3 (§7d). The boot word is the child's System handle,
resolved from the tag the launcher started it with, or `NO_HANDLE` for a
child started with no tag. So `_start` has no stack to set up, no data to copy
and no `.bss` to clear. The start-up is entirely Saw and has no per-arch
assembly.

### 1.2 `_start`'s steps

```saw
@export("_start")
func _start(boot: UInt) unsafe -> Never
```

1. **Re-entry guard.** If the `STARTED` word is set, this is a second thread
   made at `ENTRY_IMAGE` (§5). The start-up writes `sos: a thread entered the
   image entry; make threads with thread_create(run:)` and aborts with
   `REENTERED`. Otherwise it sets the word.
2. **Adopt the System handle.** If the word is not `NO_HANDLE`,
   `System(boot_handle:)` adopts it, which parks the word for the seams as it
   does today. The start-up then reads the handle's rights with `INFO_OP`,
   which needs no right, and records whether it holds `Debug`. The write seam
   uses that record in §3.4.
3. **Derive the Process handle.** `system.process_self()`. The start-up
   publishes the word to the abort hook, so from here on a panic exits the
   process instead of attempting a `shutdown` (§7). If this step is refused,
   the start-up writes `sos: no process handle: STATUS` and aborts with
   `START_FAILED`. Without a Process handle the abort falls back to today's
   `shutdown`. For a child that `shutdown` is an `AccessDenied` fault, which
   is the only end left to it.
4. **Drain the boot set.** `BootSet(process:)` drains every record. A drain
   that ends in anything other than `Drained` is `START_FAILED`, with the same
   kind of message. The drain allocates from the arena; arena exhaustion is
   the runtime's existing `NoMemory` abort.
5. **Take the console record.** `set.take(CONSOLE_TAG)`. If the record exists
   it must be an inlet. Any other kind is a skew between the builder and the
   runtime: `sos: the console record is not an inlet`, `START_FAILED`. The
   inlet goes into a `Uart<PipeTransport>`, which is SO-137's typed client
   over its pipe transport. The start-up parks the inlet's word for the write
   seam and installs the console as the seam's target (§3.3).
6. **Publish the boot context.** The start-up keeps the System, Process,
   BootSet and console wrappers in one `Boot` value in its own frame and
   stores that value's address for the accessors (§2). A static cannot hold
   them: statics must be trivially destructible, and each wrapper has a
   `deinit`. `_start`'s frame outlives `main`, because `main` runs inside the
   call in the next step.
7. **Run the program.** `let status = __saw_main()`. The result is 0 to 255.
   An `Err` has already been written as `error: ...` and comes back as 1. An
   out-of-range `Int` has already panicked and did not return.
8. **Flush.** If the console holds a write posted after the last `Sync`, send
   `Uart.sync()`. A failure here is ignored, because a driver that is gone
   leaves nothing to flush to.
9. **Exit.** `process.exit(status)`, through the runtime's own Process handle.
   This does not return. If the exit is refused, the process faults: a
   missing `ProcessRight.Exit` is the `AccessDenied` fault, which ends it
   (§8). The function ends in `while { }` so that the type checker's `Never`
   is honest.

Every step before 7 that fails writes through the seam and aborts, and the
seam is already pointed at the best target it has at that moment. A failure in
step 3 or 4 can reach only the debug print. A failure in step 5 or later also
reaches the console.

### 1.3 What a `main` program writes

```saw
import sosmain
import uartproto.{...}

func main() -> Result<Void, SosStatus> {
    let proc = sosmain.process()
    var record = sosmain.boot_set().take(TAG_UART)!
    ...
    print("served {} requests", n)
    return
}
```

Its `Saw.toml` adds `sosmain = { path = ".../rt/main" }` beside `sos`, and
keeps `runtime = true` because the image's compile is still where the seams
land.

### 1.4 Where `_start` lives: rejected alternatives

- **In the `sos` facade.** Every package imports `sos`, so every package would
  get a `_start`, and each of the existing `_start` packages would fail to
  link with a duplicate symbol. That breaks the ruling that old packages keep
  working.
- **In a module of `sos` that the facade does not import.** Only an import
  compiles a module into the unit (`lib.saw`'s note on `import sos.rt`), so
  this would be opt-in too. But the console needs `uartproto` and `uartpipe`,
  and `uartpipe` depends on `sos`. That makes a package cycle unless the pipe
  transport moves into `sos`, which would make the kernel's userspace API
  package depend on a driver protocol.
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
`sosmain` without a `main` fails at link on `__saw_main`, and importing it
while also exporting `_start` fails at link on the duplicate. Both errors are
already clear.

## 2. The boot context

### 2.1 The accessors

```saw
public func system() borrows -> &System
public func process() borrows -> &Process
public func boot_set() borrows -> &var BootSet
public func flush()                       // the console's Sync, if there is one
public func exit(code: UInt) -> Never     // flush, then Process exit
```

Each accessor lends a place out of the `Boot` value in `_start`'s frame,
through the address step 6 stored. The runtime keeps the wrappers for the
whole life of the process.

- **System.** `main` can print through it, read the clock, and mint siblings.
  Root mints each child's System this way already. `main` cannot give the
  original away, because `give(system:)` takes it by value. That restriction
  is deliberate: the write seam and root's abort path use that word.
- **Process.** Exit, `thread_self`, waiters, give, map: everything works
  through `&Process`. A second, owned handle is still available from
  `system().process_self()`, which mints a fresh one.
- **BootSet.** `main` takes records from it by tag. A record nobody takes is
  released when the process ends. A program that wants its records back
  sooner, to stay under its handle row, takes them and drops them.

A child started with no System has no Process either. `system()` and
`process()` then panic with a message that names the cause. This is the one
case where the accessors are not total. It is rare, so making every caller
unwrap an Optional for it would be the trade the Optional discipline rules
out.

### 2.2 Lent, not taken: the alternative

The rejected shape is a take-once `sosmain.boot() -> Boot` that moves the
wrappers into `main`. `main` could then drop or give its System, and the
runtime's parked word for that handle would go stale. A stale word does not
resolve, and every later print or abort through it would be a `BadHandle`
fault. The runtime would have to mint its own Process separately, and it could
not mint its own System at all, since a child's System has no `Mint`. Instead,
`System.deinit` and `give(system:)` would have to clear the parked word. That
is the same hazard today's `_start` packages carry silently: drop the
`System`, then panic, and you get a `BadHandle` fault.

The lending shape depends on sawc at the pin lending a place through a raw
pointer from a free `borrows` function. The spec shows a lend through
`UnsafePointer` subscripting. Unit 1's first task is to confirm the whole form
compiles; if it does not, see open question 2.

### 2.3 `System(boot_handle:)`

It stays public and unchanged. It is the typed layer's one crossing (the `sos`
module docstring), and the start-up is its caller under `main`. Old packages
and the raw-altitude tests keep writing it. Its doc gains a line: a `main`
program never writes it, and `sosmain`'s start-up adopts the handle. Nothing
retires it, since hand-built `_start` tests will always exist.

## 3. The console

### 3.1 How an image names it (recommended)

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
- The builder's handle checks count the record. A process now holds one more
  handle when it starts, and a declared `handles` row that sat exactly at the
  floor is refused by name in a console image. Root's per-step handle check
  sees one more inlet minted per child. Both come from the existing checks
  once the record is in the plan.
- The console driver is the image's choice: SO-137's `svc-console` on the S3,
  or `svc-uart-ns16550` and `svc-uart-pl011` on the gate boards. A client
  cannot tell which one it got.

**`CONSOLE_TAG`.** The runtime reserves a range of tags, `0xFFFF_FF00` to
`0xFFFF_FFFF`, and `CONSOLE_TAG` is the first of them. The values fit every
board's word, and the rest of the range is spare for later runtime records,
such as SO-139's. The declaration reader refuses, by name, a declared tag
inside the range. The constants live in `bootplan`, the dependency-free
package that the builder and root already share for the launch convention,
and `sosmain` depends on `bootplan` for them. `sosabi` is the wrong home: the
kernel assigns tags no meaning (§12), so a reserved tag in the kernel's ABI
module would be a category error.

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
  word, which is exactly what a tag is (§12).
- **No opt-out yet.** A `console = false` in a declaration would serve a
  process too tight to hold one more handle. No such process exists, so it
  waits for one (open question 5).

### 3.2 How the runtime takes it

The start-up takes it in step 5, before `main` can see the boot set. `main`'s
`BootSet` never contains it. `main` cannot reach the console client either
(open question 7).

### 3.3 What `print` becomes

Today `rt/common` calls the per-side hook `sos_rt_write(ptr, len)` once per
part and once more for the newline, and the process side turns each byte into
a `DebugPrint` trap. The new path works like this:

- **One message per line.** `rt_write_line` in `rt/common` assembles the line
  in a buffer on its own stack, in chunks of at most `LINE_CHUNK` bytes, and
  hands each chunk to `sos_rt_write` once. `LINE_CHUNK` is a constant equal
  to `uartproto.MAX_PAYLOAD`, and a static assert in `sosmain` holds the two
  equal. A line that fits is one message, and a longer line becomes
  consecutive messages. The buffer is on the calling thread's stack, so two
  threads printing share no buffer. The kernel's `print` sites pay the same
  stack.
- **The writer.** On the process side, `sos_rt_write` sends each chunk to the
  console when one is installed. It builds a `Write` body on the stack, an op
  byte of `UartOp.Write` followed by the bytes, and posts it through the
  floor's `sos_pipe_inlet_post`, a tell. It then releases the claim, so a
  line costs two traps and never waits for the driver. If the ring is full
  (`WouldBlock`), the same body goes through `sos_pipe_inlet_call`, which
  parks until there is room and then until the driver answers. That is
  back-pressure, and it needs no tick: a client that fills the ring blocks,
  which is when the driver runs. A driver that is gone (`PeerClosed`) means
  the bytes are dropped.
- **Ordering.** A connection's ring is FIFO, and it is shared by every client
  of the console, so lines from one process arrive in order, and lines from
  different processes interleave at message boundaries.
- **`Sync` at the end.** The start-up's step 8, `sosmain.exit` and the abort
  path (§4) each send `Uart.sync()` before the process ends. The reason is
  the transcript, not delivery: a posted message outlives its sender (§2.1),
  so the bytes would come out anyway. Without the `Sync`, though, they could
  come out after the kernel's `SOS: process exit` line and root's `ended`
  line, and the order would move between runs. The `Sync` is skipped when
  nothing has been posted since the last one. `sosmain.flush()` exposes the
  same call to `main`.
- **`__saw_rt_write`** is the no-newline piece that task dumps use. It goes
  out as its own message or messages, unbuffered.
- **Non-ASCII.** The bytes go out as given, as today's debug path sends them.
  `UartBody.text`'s `?` substitution applies only to a `String` handed to
  the typed client.

**Why the bytes skip `Uart.write`.** The ruling says `print` writes through
the typed client. In this design the client carries the `Sync` and its reply
decode, and the wire's constants come from `uartproto`, but the bytes go
through the floor. Three reasons:

- `sos_rt_write` is the panic path's writer, and `rt/common` has only that one
  hook. One writer that is check-free, allocation-free and stack-only is a
  single path to review.
- `Uart.write` takes a `String`, so `print` would have to build one, which
  allocates.
- The client keeps its body inside itself, so two threads printing at once
  would write the same buffer.

The alternative was a typed path for `print` with a separate raw path for
panics: two writers to keep in step, and the typed one not check-free.

**Interleaving inside a line.** Until a thread can name itself, the output
lock (`__saw_rt_output_lock`/`unlock`) is a no-op, so a line longer than
`LINE_CHUNK` can interleave with another thread's line at a chunk boundary.
SO-141's per-thread word supplies an owner for a real lock (open question 6).

### 3.4 No console record

The seam falls back in this order: the console; else the kernel's debug print,
if step 2 found `Debug` on the System handle; else the write is dropped.
Today a print without `Debug` is an `AccessDenied` fault that ends the
process. The rights check turns it into a dropped write. Root, which creates
the console, and the console driver have no console record, so they print
through the debug path as now. The check also covers old packages, because
the seam is shared: once SO-138 withholds `Debug`, an old package's `print`
is dropped instead of being fatal. Its explicit `system.debug_print` calls
still fault.

## 4. The panic path over the console

The abort path itself never allocates. The abort hook (`sos_rt_abort`) does
this on the process side:

1. **The text** has already been written through the §3.3 writer:
   `rt_panic` calls `sos_rt_write(ptr, len)` and then `sos_rt_abort(code)`.
   The writer reads the bytes with `UnsafeMemory` and advances with `&+` and
   `&-`. It never indexes and never builds a `String`. It builds each body on
   the panicking thread's stack, in a fixed array of `BODY_BYTES`, plus the
   post's argument record and, for the fallback, a reply buffer of the same
   size. It uses the connection the start-up already opened, by its parked
   word. The writer is check-free by construction, held to the standard
   `rt/common`'s header sets: read the `-Oz` disassembly of the hook on each
   architecture. `__saw_rt_write_error`, which writes `main`'s `error:` line,
   uses the same writer and does not abort.
2. **`Sync`.** The abort sends a `Sync` as a call, using the same raw floor
   call, not the typed client, so the text is out before the death is
   recorded.
3. **Exit.** `sos_process_exit(word, RUNTIME_BAND | code)` (§7). If no
   Process word was ever published (a failure before step 3), the abort uses
   today's `shutdown` through the System word. Root holds `Shutdown`, so the
   machine stops with `code`. For a child it is the `AccessDenied` fault.

The text has no static buffer and the path has no `panicking` flag. Two
threads panicking at once each send their own text, and the first to exit ends
the process. Corrupted statics cannot touch a stack buffer. Only the parked
words can be corrupted, and the table below covers that.

**What is lost, and what the status still says:**

| Condition | The text | The status word (child) |
|---|---|---|
| Console present, driver alive | written, before the exit | `Exited`, `0x140` |
| Ring full | the chunk parks for room, then goes out | `Exited`, `0x140` |
| Driver gone (`PeerClosed`) | lost | `Exited`, `0x140` |
| Driver alive but wedged | the process parks in the call and never ends; its supervisor sees no death until it kills it (`Killed`) | n/a |
| No console, `Debug` held | through the debug print | `Exited`, `0x140` |
| No console, no `Debug` | lost | `Exited`, `0x140` |
| Parked console or Process word corrupted | lost; the post or exit is a `BadHandle` fault | `Faulted` (`BadHandle`) |
| Process ceiling lacks `Exit` | written | `Faulted` (`AccessDenied`) |
| Panic before the start-up's step 3 | debug path only | `Faulted` (`AccessDenied`) |

For root the machine's status is the low byte of the code, so a root panic
still reads 64 (`abort_status`).

The wedged-driver row is the cost of the `Sync` and of the full-ring
fallback. Every ordinary print from every process already depends on the same
driver, so an image whose console driver is wedged is already broken; the
`Sync` buys deterministic transcripts. Open question 4 offers the other choice
for the panic path alone: post without `Sync`. The text then still arrives,
because messages outlive their sender, but after the death lines.

What a corrupt process cannot report at all is SO-139's job: a separate
process with the authority to read another's state.

## 5. A thread's entry

### 5.1 The API

```saw
extension Process {
    /// Make a thread that runs `run(arg)` on the stack below `stack_top`
    /// and exits with what it returns. Not started: `start()` it.
    public func thread_create(run: FuncPointer<(UInt) sync -> UInt>,
                              arg: UInt,
                              stack_top: UInt) -> Result<Thread, SosStatus>
}
```

This is in `sos`, not `sosmain`, because a `_start` package can use it too.
It is buildable now: `FuncPointer` and `address()` are implemented at the pin.
So the doc of the `ENTRY_IMAGE` overload, which says Saw cannot name a
function's address, is stale, and the thread unit corrects it.

### 5.2 How it works

1. **A start record.** It is written just below `stack_top` in the caller's
   memory: the entry's address, `arg`, and the thread's own handle word. The
   stack given to the kernel starts at the record's base, rounded down to the
   16-byte grain. The record lies inside the stack row, so the kernel's
   stack-top checks are unchanged.
2. **The thread's own handle** is a `Control`-only sibling minted from the
   created handle, at create time. A quota refusal is therefore reported to
   the creator as a status, and the thread never needs `thread_self` at its
   end, where a refusal would have nobody to report to. The handle is
   released by the thread's exit. If the creator drops the thread without
   starting it, the sibling keeps the record alive until the process ends:
   the same as a created, unstarted thread today.
3. **The trampoline**, `sos.thread`'s `thread_entry(record: UInt) sync ->
   Never`, is created at `FuncPointer(thread_entry).address()`. It reads the
   record into locals, calls `run(arg)`, then calls `sos_thread_exit(self,
   result)` and `while { }`. Its address comes from Saw, not a C leaf.
4. **A panic on any thread** ends the whole process through the abort path
   (§8's fault rule: there is no per-thread recovery).
5. **The ordering of ends.** The process ends when `main` returns, whatever
   its other threads are doing, which matches the hosted rule. If the threads
   all end first, the last one out ends the process with its own code (§8),
   as today.

### 5.3 Where SO-141 hooks in

- **The per-thread word** lives in the architecture's thread register. Each
  frame starts zeroed: riscv32's `tp` and the S3's `THREADPTR` are cleared by
  `frame_init`'s loop. arm64's `TPIDR_EL0` must join the frame, which is
  SO-141's kernel change. Neither `_start` nor the trampoline writes the
  word, because a zero word is "no executor yet".
- **The close.** The trampoline calls `__saw_background_close()` between
  `run(arg)` and the thread exit. That is SO-141's line to add, once a
  sawlang carrying SL-1150 is pinned. `_start` needs no close, because
  `__saw_main` closes the main thread's group before it returns (ABI). Exit
  and abort do not close, since the teardown frees everything (SO-141).
- **The drive** goes in `sos`'s blocking wrappers (SO-141). The console
  writer and the `Sync` use the floor directly, not those wrappers, so a
  `print` never runs a background task in the middle of a line.

### 5.4 `ENTRY_IMAGE`

The overload stays for `_start` packages. In a `main` program a thread made at
the image entry would re-run the start-up, so the start-up's step 1 aborts the
process with a message naming the fix (`REENTERED`). The overload is removed
once no package in the tree uses it. Raw-altitude tests pass `ENTRY_IMAGE`
through the floor, which keeps it.

## 6. The windowed entry on the S3

`_start`, the trampoline and `main` are compiled in one module graph. Blade
hands sawc the package's `--target-features +windowed` for the whole graph:
the program, `sos`, `sosrt`, `sosmain`, and `support.c` beside it (§7d).
Under that build:

- **Every compiled function opens with `entry`,** `_start` included. §7d's
  start state (`a2` and `a6` carrying the argument, `a4` = `0x7FFF_FFFC`,
  CALLINC = 1, pane 0 live) makes the entry a function reached by `call4`,
  with the boot word in its `a2`. The call4 rule for hand-written entries
  does not bind.
- **The call into `__saw_main`** is an ordinary compiled call in the windowed
  convention, because the caller and callee come from the same compile. That
  is the ABI's "windowed convention on windowed Xtensa", met by construction.
  A call0 `sosmain` calling a windowed `main` cannot happen, because the
  graph has one feature list.
- **The trampoline** calls the `FuncPointer` indirectly, also windowed. Its
  target comes from the same graph, or from a C unit compiled with the same
  list.
- **Neither ever returns.** Both end in an exit op and `while { }`, so the
  `retw` to `0x7FFF_FFFC` in §7d is never taken.

So the S3 needs no S3-specific code. It needs cases: `windowed_saw_main`
(deep recursion in `main`, a `thread_create(run:)` thread deep in its own
chain, a print over `svc-console`, the status read by root) and a call0 twin.

## 7. The exit status

- **From `main`:** 0 to 255, per the ABI. A `Void` main gives 0, an `Err`
  gives 1, and an `Int` outside the range is a panic.
- **The runtime's own ends:** codes in a band above 255, `RUNTIME_BAND =
  0x100` plus the code: `0x140` panic, `0x141` arena exhausted, `0x142`
  start-up failed, `0x143` image entry re-entered. The process-side abort
  hook adds the band, and the kernel's own aborts are untouched. A child's
  status word keeps 16 bits (§8), so a supervisor reads `0x140` and can
  never confuse it with a `main` that returned 64. Root's machine status is
  the low byte (`abort_status`), so a root panic reads 64 as it does now. The
  low bytes, 64 to 67, are clear of the kernel's `ExitCode`s (2 to 8).
- **Today's panic status changes for the better.** A child's panic today
  aborts through `shutdown`, which a child's System handle lacks, so its
  status word reads `Faulted (AccessDenied)`. Under `sosmain` it reads
  `Exited`, `0x140`. `_start` packages keep the old behaviour, because they
  never publish a Process word to the abort hook.
- **A program that wants more than 0 to 255** calls `sosmain.exit(code)`,
  which flushes and exits with any word. What a supervisor can read of it is
  the low 16 bits (§8). A program using the runtime band in this way can
  impersonate a runtime end. The design documents the band instead of
  refusing it (open question 10).
- **Root** keeps ending with an explicit `system().shutdown(code)`. Returning
  from `main` would exit root through `ProcessOp.Exit`, which stops the
  machine with the same status but narrates root's teardown, and that would
  add a line to every stock-root transcript.

## 8. Migration

`_start` packages keep working unchanged: they never import `sosmain`, so they
get no `_start` of the runtime's, no console hook, and today's abort path.
Every image's hash moves once, when `sos.rt` and `rt/common` change, but no
transcript does. That is the oracle for units 1 and 4.

The order in which packages move:

1. **The drivers**, after SO-137 merges: `svc-console`, `svc-uart-ns16550`,
   `svc-uart-pl011`, then `svc-gpio-esp32s3` and `svc-pwm-esp32s3`. Each
   `_start` becomes `main`. Each `proc.exit(EXIT_*)` becomes a returned
   status. Each `boot_handle_next` sequence becomes `boot_set().take(TAG)`,
   since each driver's records are declared with their tags. Their
   transcripts must not move: in the existing images they have no console
   record and print through the debug path as before.
2. **The stock root.** Its `derive` step becomes the start-up's: `run` takes
   `&var BootSet` from `sosmain.boot_set()`. It keeps `shutdown`. The plan,
   the give order and every transcript are unchanged.
3. **The product template** (`hello`, `greeter`). Each becomes a `main`. The
   images gain `console = ...`, and `hello` drops its hand-written
   `uartproto` frames for `print`. The template is the user's first contact,
   so it shows the new shape.
4. **The box (SO-78).** It is written on `main` from the start: `console =
   "console.uart"` with `svc-console`, and its processes are console clients
   without `Debug`.
5. **Tests** convert when they are touched, not in one patch. New tests use
   `main` unless they are about the raw entry.

**The builder** (unit 3) gains the image's `console` key, the reserved range,
the plan record and the handle count. **The stock root** changes only when it
moves to `main` (step 2 above). The console record needs no root change,
because it is an inlet record.

## 9. Rights

With `print` over the console, a process needs `Debug` only if it has no
console: root, and the driver serving the console. That decides SO-138's
default. The proposal is an amendment to SO-138's brief, landing after unit
3:

- **In an image that names a console**, a process whose `system` record
  names no rights gets `ProcessSelf | ClockGet | Transfer`. Debug only by
  declaring it, as `svc-console` declares `rights = ["debug"]`. The real
  UART drivers declare it too, since they serve the console and print
  through the debug path.
- **In an image with no console**, the default stays the four, so every
  existing image is unchanged.
- **This issue makes the withholding safe** for `print`: the seam's rights
  check (§3.4, unit 1) drops writes instead of faulting. An old package that
  calls `system.debug_print` directly still faults without `Debug`, so such a
  package moves to `main`, or declares `debug`, before it goes into a console
  image.
- **spec §12's launch convention** names the default set. The SO-138 unit
  that changes the rule changes that sentence, and so does unit 3 for the
  console record.

## 10. Units of work, in order

Each unit is one patch. Gate cases run on riscv32, arm64 and riscv32-flat,
plus the S3 smoke where noted. Each unit lists its mutants, the review
standard: every check and refusal goes red when removed or inverted.

**Unit 1: `sosmain`'s start-up and the boot context, with no console.** The
new `rt/main` package: `_start`, the `Boot` value, the accessors, `exit`,
`flush` (a no-op until unit 4), and the re-entry guard. `sosrt` exports
`__saw_rt_write_error` and the output lock pair, both no-ops for the lock.
`sos.rt` gets the `Debug` rights check and the abort through a published
Process word with the runtime band. Each `user.ld` gets `ASSERT(DEFINED(_start))`.
Its tests prove:

- `main` returning `Void`, `7`, `Err` (the `error:` line, status 1) and `300`
  (a panic, `0x140`) each reach the status word exactly, as root's `ended`
  line prints it;
- the accessors' rights and records, taking by tag, and untaken records
  released at the end (the Handle row read by a supervisor);
- a thread made at `ENTRY_IMAGE` stopping the process with its message;
- a launcher withholding `ProcessSelf` (`START_FAILED`, named);
- a child printing with no `Debug` (dropped, not faulted);
- a windowed and a call0 `main` on the S3;
- the full suite's transcripts unchanged.

The first task in the unit is confirming that sawc at the pin compiles the
lend through the frame's address (open question 2).

**Unit 2: a thread's real entry.** `thread_create(run:arg:stack_top:)`, the
trampoline, the start record, the self handle minted at create, and the stale
doc corrected. Its tests prove:

- threads with different entries and arguments join with their results;
- a thread's panic ends the process `0x140`;
- a quota refusal at create returns the status and leaks nothing (the Thread
  and Handle rows read back);
- the same on the S3, windowed.

**Unit 3: the builder's console.** `[image] console`, the reserved range in
`bootplan` and its refusal in declarations, the inlet record appended per
process, the handle count, and the refusals named in §3.1, with spec §12's
lines. Its tests prove each refusal (builder tests, `blade test`); the plan's
records for a console image (builder tests); a described image on riscv32
and arm64 whose `_start` client finds an inlet under `CONSOLE_TAG` and writes
a line through it to the real UART driver; and every existing description
reading clean.

**Unit 4: `print` over the console.** It needs SO-137 merged (`Uart<T>`,
`uartpipe`) and units 1 and 3. `sosmain` takes the record, `rt_write_line`
chunks, the raw writer falls back from tell to call, and the `Sync` lands in
step 8, `exit` and `flush`. Its tests prove, on described images on each gate
board and the S3 with `svc-console`:

- lines of 0, 1, `LINE_CHUNK` and `LINE_CHUNK + 1` bytes arrive whole and in
  order, before the kernel's exit line;
- a no-tick image whose client prints more lines than the ring holds before
  it blocks, so the full ring is reached by construction and not by timing,
  with every line arriving in order;
- root and the console driver still printing through the debug path;
- a client with no console and no `Debug` printing nothing and ending clean.

**Unit 5: the panic path and `error:` over the console.** The abort's `Sync`,
the runtime band through the console, and `__saw_rt_write_error` through the
writer. The disassembly of the writer and the abort hook is read at `-Oz` on
riscv32, arm64 and xtensa, and the patch note says so. Its tests prove:

- a `main` panic's text before root's `ended` line, status `0x140`;
- an `Err` main's `error:` line, status 1;
- a panic after the console driver has ended (no text, status `0x140`);
- a panic with the ring full;
- a root panic's machine status of 64.

**Unit 6 (SO-138, amended):** the rights default in §9.

**Unit 7: the real UART drivers and `svc-console` move to `main`.**
Transcript-neutral; image hashes listed.

**Unit 8: the S3 drivers move.** The same terms, on the S3 smoke.

**Unit 9: the stock root moves.** Transcript-neutral across every
stock-root case.

**Unit 10: the product template moves**, with its images naming the console.

**After this issue:** the box on `main` (SO-78); SO-141 (the close and the
executor set, which a suspending `main` needs; open question 11); test
conversions as they are touched.

Units 1 and 2 can run in parallel, and so can 3. Unit 4 waits on SO-137's
merge. Units 7 and 9 need only unit 1.

## Open questions

1. **The package's name and home.** `rt/main/` named `sosmain`, or `rt/proc/`
   named `sosproc`? *Recommend `rt/main` and `sosmain`.* It names what a
   program imports it for, and it sits beside `rt/common` in the runtime's
   tree, which is what "sosrt owns `_start`" means in a tree where `sosrt`
   proper is shared with the kernel.
2. **If sawc at the pin cannot lend through the frame's address.** *Recommend
   falling back to take-once* (`sosmain.boot() -> Boot` moved into `main`),
   with the runtime minting its own Process and with `System.deinit` and
   `give(system:)` clearing the parked word when it is theirs. That fixes
   today's latent `BadHandle`-at-panic hazard for `_start` packages as well.
   If that is the outcome, file a sawlang issue for the lend.
3. **The runtime band** (`0x100 + code`) for a child's runtime ends. It
   changes the status a child's panic reports, from `Faulted (AccessDenied)`
   to `Exited 0x140`, for `main` programs only. *Recommend yes.*
4. **The panic path's `Sync`.** Keep it (ordered transcripts; a wedged driver
   hangs the panicking process until its supervisor kills it), or post the
   text and exit at once (the text arrives anyway, after the death lines)?
   *Recommend keeping it.* The same dependency already holds for every
   print.
5. **A declaration opt-out** (`console = false`). *Recommend not adding it*
   until a process needs the handle back.
6. **Long lines from two threads** can interleave at `LINE_CHUNK` boundaries
   until the output lock has an owner identity, which arrives with SO-141's
   per-thread word. *Recommend accepting it now, and making the lock real in
   SO-141.*
7. **Exposing the console client to `main`** (`sosmain.console()`, for reads
   from a real UART driver). *Recommend not now.* A program that reads takes
   its own client record of the driver, as `hello` does. A shared client
   would also need the lock in question 6.
8. **A `main` program started with no System.** It runs, the accessors panic,
   and it ends by a fault, because it holds no handle to exit through.
   *Recommend documenting this rather than designing around it.* A sandboxed
   process that wants a status reported gets a System record.
9. **SO-138's default tied to the console's presence** (§9). *Recommend yes,
   as an amendment to SO-138's brief* before that unit is dispatched.
10. **Refusing runtime-band codes in `sosmain.exit`.** *Recommend
    documenting the band and not refusing.* A program can misreport its end
    in other ways, and `exit` stays one call.
11. **A suspending `main`.** `__saw_main` drives one through the entry
    executor, which reaches the freestanding executor set, and `sosrt`
    exports none of it today. *Recommend leaving it out of SO-124:* a
    suspending `main` fails at link naming the seam until SO-141 exports the
    set. SO-141's brief should name this as one of its cases.
12. **The reserved range's values** (`0xFFFF_FF00` up, `CONSOLE_TAG` first).
    *Recommend these.* They are the same on every board and far from every
    tag in the tree, which counts up from 0.
13. **Where the line chunking lives.** In `rt/common`'s `rt_write_line`,
    which costs the kernel's `print` sites a chunk of stack, or as a third
    per-side hook, `sos_rt_write_line`, which leaves the kernel's path
    alone? *Recommend `rt/common`.* `sosrt` keeps its two hooks, and the
    kernel prints through its own console writer almost everywhere.
