# riscv32 user HAL — the seam

The entire architecture-dependent surface of an SOS process is one C file
defining two symbols, `sos_syscall1` and `sos_syscall3`. `root/src/` builds for
arm64 with only its `[sos.<triple>]` manifest sections differing (the linker
script and the `native` line).

That file is `hal/riscv32-common/user/syscall.c`. It names the ISA's syscall
instruction and the ABI's registers and nothing about a board, so it is one
copy for every riscv32 board and every root and child manifest points at that
path whichever board it targets. This document is the seam it implements. What
is in this directory is the QEMU `virt` board's own user-side linker scripts
(`root.ld`, `child.ld`, `child2.ld`, `child3.ld`), which differ per board
because the load addresses do.

There are four scripts, and the count is a property of the tier. This profile
does not translate, so a user address is its physical address and two resident
images cannot share a base — each process must be linked where its own frames
are, and a script per resident child is the cost of the tier. Root plus three
children alive at once takes four scripts. The arm64 board has one `user.ld`,
because there the kernel places each image's frames under a canonical virtual
base and another child costs no file at all. The riscv Sv32 climb is
deliberately not taken, so that this board keeps giving §5.5's original answer
("an address is the same number in every process"); §5.5 scopes that answer to
the tiers that keep it. The two directories side by side are the tier split:
four scripts here and growing with the process count, one there whatever the
count.

The other symbols a process needs — `sos_rt_write` and `sos_rt_abort`, and the
System handle's parking — name no architecture: a byte reaches the console
through a System op, which is the same op on both profiles, so per-arch C copies
would be two copies of one thing. They are Saw, once, in `kernel/sysapi/`,
beside the System object whose authority they use.

## Which altitude is supported for whom

There are three ways to reach the kernel and they are one implementation chain,
not three (spec.md §5.7):

| Altitude | Spelling | For |
|---|---|---|
| typed Saw | `system.shutdown(0)` | **Saw processes. Use this.** Handles are typed, statuses are a `SosStatus`, no number appears. |
| typed C | `sos_system_shutdown(h, 0)` | **Non-Saw languages — with a catch, see below.** One `@export`ed function per op, named for the op; still no number. |
| raw | `sos_syscall1(h, op, a)` / `sos_syscall3(h, op, a, b, c, &v)` | **The HAL and the kernel package only.** They take an op number, which is the thing the arrangement above exists to keep out of callers. Not a supported application interface. |

The first two are the kernel package's (`kernel/sysapi/`), not this
directory's. This directory supplies only the bottom of the chain: the one
instruction that crosses the trap boundary. No op number appears in this HAL.

**The typed C row.** It is a specified and linked interface. Its bodies run on
every boot, because the Saw sinks call the same functions.

**The row is out of reach of the programs it names.** Those symbols are compiled
from the `sos` Saw module (`kernel/sysapi/src/floor.saw`), and `sos` depends on
`sosrt` — so linking them makes the image a Saw image with a C `main` in it,
carrying the runtime and, unless the arena knob is turned down, the default
arena, for a handful of calls. `tests/c-child/` is the first genuinely non-Saw
process this tree has had, and it could not use this row. What it does instead
is what any C program arriving from outside would do: hand-roll the trap stub
from the register table below and `#define` the op numbers it needs out of
`kernel/abi/`, a module whose own header calls itself kernel-internal. So spec
§5.7's "an op number is not ABI" holds for Saw and does not yet hold for C. What
is missing is not more `@export`s — they are already written — but (a) an
`sos.h` this tree owns and keeps in step with `kernel/abi/src/ops.saw`, and (b)
a way to link the exported floor without the Saw runtime behind it. Until then,
read this row as "the supported C interface for an image that is already linking
`sos`".

## Provided to a process

| Symbol | Contract |
|---|---|
| `sos_syscall1(handle, op, arg0) -> status` | Perform one object op that answers with a status alone. The value half is not read. |
| `sos_syscall3(handle, op, arg0, arg1, arg2, value_out) -> status` | The same, for ops that take up to three arguments and answer with a value — a created thread's handle, a joined thread's exit code, a process's status word. The value comes back through a pointer rather than in the return, because the Saw side declares these symbols against a C ABI whose whitelist has no aggregate return; one out-parameter is the shape that crosses. Ops with fewer arguments pass zeros. |

The raw forms are these two and no more. An op whose answer does not fit the
value register does not get a wider stub: it takes a buffer argument and the
kernel copies its answer out (`Waiter.Wait` is the first, spec §2.2). That
keeps the syscall instruction's operand list the same shape, which is the only
part of a process that has to know it is riscv32 — a per-answer register
convention would make this file grow with the object model.

The runtime's two hooks (`sos_rt_write`, `sos_rt_abort`) and the parked boot
handle are part of a process's contract but are not this directory's. See
`kernel/sysapi/src/lib.saw`, and `rt/common/src/lib.saw` for what calls them.

## Required of a process

Read this before writing a crt0. Apart from the entry point, every item is
something the kernel already does for you, so the list is mostly what a crt0
here does not have to write:

- **An entry point taking the boot handle as its first argument.** The kernel
  places it in the first argument register (`a0`) before entering user mode, so
  a Saw `@export("_start") func _start(boot_handle: UInt)` — or a C
  `void _start(unsigned long h)` — receives it directly.
- **The stack pointer is already set.** `process_create` records
  `stack_top = dest.link_top()` — the top of the region the image was linked
  into — and `frame_init` puts it in `sp` before the first instruction runs.
  A crt0 sets up no stack. Both profiles' region tops are 16-byte aligned,
  which is what AArch64's `sp` rule wants and what this one is happy with.
- **`.bss` is already zero.** The loader zero-fills each segment's `mem_len`
  tail past its `file_len`, so there is no startup zeroing loop to write.
- **`.data` is already in place.** Segments are copied to their link addresses;
  there is no separate load address and nothing to relocate from, so there is
  no startup copy loop either.
- **The link register is zero, deliberately.** `frame_init` zeroes the whole
  trap frame before writing the three fields it sets (`mepc`, `sp`, `a0`), so
  `ra` is 0 and falling off the end of `_start` faults. That is the intended
  behaviour: a bootstrap that returns instead of calling `Process.exit` shows up
  as `Faulted` in the launcher's `get_status`, where a `_start` that spun would
  show up as a case timeout. Do not write a `_Noreturn` crt0 that loops.

The one thing that is the process's own job is the way out: derive a Process
handle from the System handle you were given (`process_self`) and call `exit`.
There is no other door.

## The syscall ABI (spec.md §5.7)

Every syscall is an object op — there are no bare numbered syscalls.

| Register | Meaning |
|---|---|
| `a0` | handle (in), status word (out) |
| `a7` | op — a method id on that object's table, not a global number |
| `a1`-`a3` | arguments; `a1` also carries the value half on return |

`ecall` traps. A status of 0 is success; anything else is a `SosStatus` tag the
process may branch on. A caller error the process could have checked — an
invalid handle, an unknown op, a missing right — does not come back at all: the
kernel terminates the process and reports the reason. What is left as a status
is what the caller could not have known.

## A granted device window

A driver process reaches its device directly, through no stub in this directory
and no op in the kernel's fast path. From the driver's side the device is
ordinary memory and the code is the MMIO idiom in plain process Saw
(`UnsafeMemory<Regs, Device>` over a register-block struct);
`tests/uart-echo-ns16550/` is the worked example.

**Obtaining the window.** The build publishes a device row in the boot region
table, the kernel mints an `IoMemory` capability for it, and the driver drains
it from its boot set and installs it in itself:

    var record = proc.boot_handle_next()!          // kind == IoMemory
    let window = record.take_iomemory()!
    let mapping = proc.map(iomemory: &window)!     // one NAPOT entry, RW, NX

The address the registers appear at is the hardware's own — SOS does not
translate — so nothing else in the driver changes. `carve(offset, len)` narrows
a window before mapping it and leaves the parent whole, which is what lets one
peripheral's page serve several drivers.

**The dormant manifest path.** The root image can declare a window in its own
manifest:

    [sos.riscv32-unknown-none-elf]
    device-window = "0x10000000"

which becomes a device record in its `sosimg`, which the kernel installs as one
page-aligned NAPOT protection entry before entering user mode. The board
authorizes it rather than the image merely declaring it (this HAL publishes one
window; an image naming anything else is refused at load), and spec §2.5 calls
the path dormant. The flag and the loader's door survive because the emitter
lives in the sawlang toolchain (`blade/src/sosimg.saw`). Only root's image may
declare a window: a child image is refused one, and a driver child gets its
window because root maps it in or gives it a carved `IoMemory`.

**The console handover protocol** applies to whoever holds the console's
window: the kernel goes quiet at its `SOS: console handover` marker and writes
again only on a diagnostic path. A driver that owns the console owns its receive
side outright — the kernel never touches the receiver or the interrupt-enable
register — and shares only the transmitter, where a kernel byte lands between
two of the process's rather than inside one. See `kcore`'s `Console`.

## What is riscv32-specific here

Only the register names and the `ecall` instruction, which are what
`hal/riscv32-common/user/syscall.c` is made of. The handle/op/status *shape* is
the architecture-neutral part and belongs to the spec, not here; the arm64 HAL
implements the same two functions over `svc`.
