# riscv32 user HAL — the seam

The entire architecture-dependent surface of an SOS process: ONE symbol.
`root/src/` builds for arm64 with only the manifest's `[sos] native` line
changing, and that line names one C file holding one function.

**WHERE THAT FILE LIVES, SINCE DESIGN 23: `hal/riscv32-common/user/syscall.c`.**
It names the ISA's syscall instruction and the ABI's registers and nothing about
a board, so it is ONE copy for every riscv32 board and every root and child
manifest points at that path whichever board it targets — this document is the
seam it implements, and none of it changed. What is left in THIS directory is
the QEMU `virt` board's own user-side linker scripts (`root.ld`, `child.ld`,
`child2.ld`, `child3.ld`), which genuinely differ per board because the load
addresses do.

**THERE ARE FOUR OF THEM, AND SINCE sawos design 33 THE COUNT IS A CLAIM RATHER
THAN AN ACCIDENT.** This profile does not translate, so a user address IS
its physical address and two resident images cannot share a base — each process
must be linked where its own frames are, and **A SCRIPT PER RESIDENT CHILD IS
THEREFORE THE COST OF THE TIER**: design 36 needed a third child alive at once
(root plus three, to witness a process slot created past the compiled floor) and
that meant a fourth script, `child3.ld`, mechanically. The arm64 board collapsed
its three scripts into one `user.ld` in design 33's own unit, because there the
kernel places each image's frames under a canonical virtual base and a fourth
child costs no file at all. Design 25 ruling 11 punted the
riscv Sv32 climb to the backlog deliberately so that this board keeps giving
§5.5's original answer — "an address is the same number in every process" — every
gate run; §5.5 now scopes that answer to the tiers that keep it. So the two
directories, side by side, ARE the tier split: four scripts here and growing with
the process count, one there whatever the count.

Design 172 part 2 shrank this from four symbols to one. The three that left —
`sos_set_system_handle`, `sos_rt_write` and `sos_rt_abort` — named no
architecture: a byte reaches the console through a System op, which is the same
op on both profiles, so two per-arch C copies were two copies of one thing. They
are Saw now, once, in `kernel/sysapi/`, beside the System object whose
authority they use. What kept them here was DF-172e (a `noreturn` panic sink Saw
could not type), which design 177 closed.

## Which altitude is supported for whom

There are three ways to reach the kernel and they are ONE implementation chain,
not three (spec.md §5.7):

| Altitude | Spelling | For |
|---|---|---|
| typed Saw | `system.shutdown(0)` | **Saw processes. Use this.** Handles are typed, statuses are a `SosStatus`, no number appears. |
| typed C | `sos_system_shutdown(h, 0)` | **Non-Saw languages — WITH A CATCH, see below.** One `@export`ed function per op, named for the op; still no number. |
| raw | `sos_syscall1(h, op, a)` / `sos_syscall3(h, op, a, b, c, &v)` | **The HAL and the kernel package only.** They take an op NUMBER, which is the thing the arrangement above exists to keep out of callers. Not a supported application interface. |

The first two are the kernel package's (`kernel/sysapi/`), not this
directory's. This directory supplies only the bottom of the chain: the one
instruction that crosses the trap boundary. No op number appears in this HAL.

**A note on the typed C row, recorded rather than hidden (DF-172i).** It is a
SPECIFIED and linked interface with no in-tree caller since design 172 part 2 —
the process-side runtime sinks used to be C and were its only consumer. Its
BODIES run on every boot, because the Saw sinks call the same functions; what no
longer runs on every boot is a C caller crossing into them.

**AND THE ROW IS OUT OF REACH OF THE PROGRAMS IT NAMES, WHICH M5's C-LEG PROBE
FOUND BY BEING ONE** (sawos design 39, Sep 4). Those symbols are COMPILED FROM
THE `sos` SAW MODULE (`kernel/sysapi/src/floor.saw`), and `sos` depends on
`sosrt` — so linking them makes the image a Saw image with a C `main` in it:
roughly 8 KiB of runtime and, unless the arena knob is turned down, a 64 KiB
arena, for three calls. `tests/c-child/` is the first genuinely non-Saw process
this tree has had, and it could not use this row. What it did instead is what
any C program arriving from outside would do: hand-roll the trap stub from the
register table below (30 lines across both arches, correct first time — this
document is accurate) and `#define` three op NUMBERS out of `kernel/abi/`, a
module whose own header calls itself KERNEL-INTERNAL. **So spec §5.7's "an op
number is not ABI" holds for Saw and does not yet hold for C**, and the spec now
says so. What is missing is not more `@export`s — they are already written — but
(a) an `sos.h` this tree owns and keeps in step with `kernel/abi/src/ops.saw`,
and (b) a way to link the exported floor without the Saw runtime behind it.
That is design 31 part 1, and until it lands, read this row as "the supported C
interface for an image that is already linking `sos`".

## Provided to a process

| Symbol | Contract |
|---|---|
| `sos_syscall1(handle, op, arg0) -> status` | Perform one object op that answers with a status alone. The value half is not read. |
| `sos_syscall3(handle, op, arg0, arg1, arg2, value_out) -> status` | The same, for ops that take up to three arguments AND answer with a VALUE — a created thread's handle, a joined thread's exit code, a process's status word (design 178 M2 unit 2). The value comes back through a POINTER rather than in the return, because the Saw side declares these symbols against a C ABI whose whitelist has no aggregate return; one out-parameter is the shape that crosses. Ops with fewer arguments pass zeros. |

THE RAW FORMS ARE THESE TWO AND NO MORE. An op whose answer does not fit the
value register does NOT get a wider stub: it takes a BUFFER argument and the
kernel copies its answer out (`Waiter.Wait` is the first, spec §2.2 as amended
by design 178 M2 unit 3 rider 3). That keeps the syscall instruction's operand
list the same shape it has had since M1, which is the only part of a process
that has to know it is riscv32 — a per-answer register convention would have
made this file grow with the object model.

The runtime's two hooks (`sos_rt_write`, `sos_rt_abort`) and the parked boot
handle are still part of a process's contract; they are just not this
directory's any more. See `kernel/sysapi/src/lib.saw`, and
`rt/common/src/lib.saw` for what calls them.

## Required of a process

**READ THIS BEFORE WRITING A crt0.** For four milestones this section was one
bullet, and design 39's probe — the first freestanding C image in the tree —
had to read the other four facts out of `kernel/core/dispatch.saw`,
`kernel/core/loader.saw` and spec §8 instead. Every one of them is a thing the
kernel ALREADY DOES for you, so the list is mostly what a crt0 here does NOT
have to write:

- **An entry point taking the boot handle as its first argument.** The kernel
  places it in the first argument register (`a0`) before entering user mode, so
  a Saw `@export("_start") func _start(boot_handle: UInt)` — or a C
  `void _start(unsigned long h)` — receives it directly.
- **THE STACK POINTER IS ALREADY SET.** `process_create` records
  `stack_top = dest.link_top()` — the top of the region the image was linked
  into — and `frame_init` puts it in `sp` before the first instruction runs.
  A crt0 sets up no stack. Both profiles' region tops are 16-byte aligned,
  which is what AArch64's `sp` rule wants and what this one is happy with.
- **`.bss` IS ALREADY ZERO.** The loader zero-fills each segment's `mem_len`
  tail past its `file_len`, so there is no startup zeroing loop to write.
- **`.data` IS ALREADY IN PLACE.** Segments are copied to their LINK addresses;
  there is no separate load address and nothing to relocate from, so there is
  no startup copy loop either.
- **THE LINK REGISTER IS ZERO, DELIBERATELY.** `frame_init` zeroes the whole
  trap frame before writing the four fields it sets, so `ra` is 0 and FALLING
  OFF THE END OF `_start` FAULTS. That is the intended behaviour: a bootstrap
  that returns instead of calling `Process.exit` shows up as `Faulted` in the
  launcher's `get_status`, where a `_start` that spun would show up as a case
  timeout. Do not write a `_Noreturn` crt0 that loops.

The one thing that IS the process's own job is the way out: derive a Process
handle from the System handle you were given (`process_self`) and call `exit`.
There is no other door.

## The syscall ABI (spec.md §5.7)

Every syscall is an object op — there are no bare numbered syscalls.

| Register | Meaning |
|---|---|
| `a0` | handle (in), status word (out) |
| `a7` | op — a method id on that object's table, not a global number |
| `a1`-`a5` | arguments; `a1` also carries the value half on return |

`ecall` traps. A status of 0 is success; anything else is a `SosStatus` tag the
process may branch on. A caller error the process could have CHECKED — an
invalid handle, an unknown op, a missing right — does not come back at all:
design 178's faults ruling terminates the process for it, and the kernel reports
the reason. What is left as a status is what the caller could not have known.

## A granted device window (design 178 M2 unit 4, MIGRATED M3 unit 4)

A DRIVER PROCESS REACHES ITS DEVICE DIRECTLY, through no stub in this directory
and no op in the kernel's fast path. From the driver's side the device is
ordinary memory and the code is the design-112 MMIO idiom in plain process Saw
(`UnsafeMemory<Regs, Device>` over a register-block struct);
`tests/uart-echo-ns16550/` is the worked example. **WHAT CHANGED IN M3 unit
4 IS HOW THE WINDOW ARRIVES** (sawos design 6 D-6).

**NOW — OBTAINED.** The build publishes a DEVICE row in the boot region table,
the kernel mints an `IoMemory` capability for it, and the driver drains it from
its boot set and installs it in itself:

    var record = proc.boot_handle_next()!          // kind == IoMemory
    let window = record.take_iomemory()!
    let mapping = proc.map(iomemory: &window)!     // one NAPOT entry, RW, NX

The address the registers appear at is the hardware's own — SOS does not
translate — so nothing else in the driver changes. `carve(offset, len)` narrows
a window before mapping it and leaves the parent whole, which is what lets one
peripheral's page serve several drivers.

**BEFORE — DECLARED, and this path is DORMANT rather than deleted.** A driver
wrote one line in its own manifest:

    [sos.riscv32-unknown-none-elf]
    device-window = "0x10000000"

— which became a device record in its `sosimg`, which the kernel installed as
one page-aligned NAPOT protection entry before entering user mode. It was
AUTHORIZED by the board rather than merely declared (this HAL publishes one
window; an image naming anything else is refused at load), and spec §2.5 called
it a PLACEHOLDER for exactly the shape above. **No image in this tree uses it
any more.** The flag and the loader's door survive because the emitter is
sawlang-side (`blade/sosimg.saw`), so deleting them is a pin-bump event — a
backlog entry, not a live path. A CHILD image was always refused a declared
window and still is; a driver child gets its window because root maps it in or
gives it a carved `IoMemory`, which is what obtaining rather than declaring
bought.

**THE CONSOLE HANDOVER PROTOCOL** applies to whoever holds the console's window:
the kernel goes quiet at its `SOS: console handover` marker and writes again
only on a diagnostic path. A driver that owns the console owns its RECEIVE side
outright — the kernel never touches the receiver or the interrupt-enable
register — and shares only the transmitter, where a kernel byte lands BETWEEN
two of the process's rather than inside one. See `kcore`'s `Console`.

## What is riscv32-specific here

Only the register names and the `ecall` instruction — which, since design 172
part 2, is the whole of this directory. The handle/op/status *shape* is the
architecture-neutral part and belongs to the spec, not here; the arm64 HAL
implements the same one function over `svc`.
