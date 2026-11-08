# arm64 user HAL — the seam

The entire architecture-dependent surface of an SOS process on Profile B is
`syscall.c`, which defines two symbols, `sos_syscall1` and `sos_syscall3`.
`root/src/` builds for both profiles with only its `[sos.<triple>]` manifest
sections differing (the linker script and the `native` line).

The other symbols a process needs name no architecture, so they are Saw, once,
in `kernel/sysapi/`, for the reason set out in `hal/riscv32/user/ABI.md`.

## The linker script: one

The other file in this directory is `user.ld`, and it is the only user linker
script on this board.
Every process on this board runs under its own translation-table set over a low
half that belongs entirely to whichever process is current, so every image
links at `hal.USER_IMAGE_BASE` = `0x4020_0000` and the kernel maps its frames —
root's from a HAL constant, a child's from the `Memory` capability its
launcher supplied — at that same canonical address. `hal.image_link_base` is
the seam that answers "linked where"; `user.ld` is its arm64 answer written in
the other language, and nothing at build time compares the two (a Saw static's
value is not a linker symbol). The loader's `check_segment` catches a
disagreement with a named diagnostic rather than a silent misplacement.

`hal/riscv32/user/` carries four scripts: `root.ld` plus
`child.ld`/`child2.ld`/`child3.ld`. That is the shape of the cost: on a tier
that does not translate, a script per resident image; here, one script whatever
the process count.

## Which altitude is supported for whom

As in `hal/riscv32/user/ABI.md`: typed Saw (`system.shutdown(0)`) for Saw
processes, typed C (`sos_system_shutdown(h, 0)`) for other languages, and the
raw `sos_syscall1` / `sos_syscall3` for the HAL and the kernel package only. The
first two are the kernel package's (`kernel/sysapi/`), not this directory's;
this directory supplies only the bottom of the chain. The typed C row is out of
reach of a genuinely non-Saw image: those symbols are compiled from the `sos`
Saw module, which depends on `sosrt`, so linking them makes the image a Saw
image with a C `main` in it. A C program that wants to call the kernel today has
to hardcode op numbers out of kernel-internal `kernel/abi/`. The riscv32 twin
states the whole finding and what would fix it; spec §5.7 scopes its "an op
number is not ABI" claim to the altitude that can honour it.

## Provided to a process

| Symbol | Contract |
|---|---|
| `sos_syscall1(handle, op, arg0) -> status` | Perform one object op that answers with a status alone. |
| `sos_syscall3(handle, op, arg0, arg1, arg2, value_out) -> status` | The same, for ops that take up to three arguments and answer with a value. The value comes back through a pointer — the C ABI the Saw side declares against has no aggregate return. Same contract as the riscv32 twin, which states the reasoning. |

The raw forms are these two and no more. An op whose answer does not fit the
value register takes a buffer argument and the kernel copies its answer out
(`Waiter.Wait` is the first, spec §2.2), so the `svc` operand list keeps its
shape. Same contract as the riscv32 twin, which states the reasoning.

The runtime's two hooks (`sos_rt_write`, `sos_rt_abort`) and the parked boot
handle are part of a process's contract but are not this directory's. See
`kernel/sysapi/src/lib.saw`.

## Required of a process

Read this before writing a crt0. Same list as the riscv32 twin, which states
the reasoning; the four facts below the first are things the kernel already
does.

- **An entry point taking the boot handle as its first argument.** The kernel
  places it in x0 before `eret`ing to EL0, so a Saw
  `@export("_start") func _start(boot_handle: UInt)` — or a C
  `void _start(unsigned long h)` — receives it directly.
- **The stack pointer is already set**, to the top of the region the image was
  linked into (`stack_top = dest.link_top()` at create, into `sp` at
  `frame_init`). It is 16-byte aligned, which is what this architecture's `sp`
  rule requires. A crt0 sets up no stack.
- **`.bss` is already zero**: the loader zero-fills each segment's `mem_len`
  tail past its `file_len`. No startup zeroing loop.
- **`.data` is already in place**: segments are copied to their link addresses,
  which is the canonical base every image on this board shares
  (`hal.USER_IMAGE_BASE`) — the kernel maps that image's own frames there. No
  startup copy loop, and nothing to relocate.
- **x30 is zero, deliberately.** `frame_init` zeroes the whole trap frame before
  writing elr / spsr / sp / x0, so the link register is 0 and falling off the
  end of `_start` faults — which is what makes a failed bootstrap show up as
  `Faulted` in a launcher's `get_status` instead of as a hang. Do not write a
  `_Noreturn` crt0 that spins.

The way out is the process's own job: `process_self` off the System handle it
was given, then `exit`. There is no other door.

## The syscall ABI (spec.md §5.7)

Every syscall is an object op — there are no bare numbered syscalls.

| Register | Meaning |
|---|---|
| `x0` | handle (in), status word (out) |
| `x8` | op — a method id on that object's table, not a global number |
| `x1`-`x3` | arguments; `x1` also carries the value half on return |

`svc #0` traps. A status of 0 is success; anything else is a `SosStatus` tag the
process may branch on. A caller error the process could have checked — an
invalid handle, an unknown op, a missing right — does not come back at all: the
kernel terminates the process and reports the reason. What is left as a status
is what the caller could not have known.

## A granted device window

A driver process reaches its device directly, through no stub in this directory
and no op in the kernel's fast path. From then on the register block is
ordinary memory to that process, and the driver is the MMIO idiom in plain
process Saw (`UnsafeMemory<Regs, Device>` over a register-block struct).
`tests/uart-echo-pl011/` is the worked example.

**Obtaining the window.** The build publishes a device row in the boot region
table, the kernel mints an `IoMemory` capability for it, and the driver drains
it from its boot set and installs it in itself:

    var record = proc.boot_handle_next()!          // kind == IoMemory
    let window = record.take_iomemory()!
    let mapping = proc.map(iomemory: &window)!     // EL0 RW, PXN + UXN, nGnRE

The call takes no access argument: an `IoMemory` has one map op and it installs
a device-attribute row, so the `Device-nGnRE` rule below is enforced by the
object's kind rather than by a flag the loader honours.

**The dormant manifest path.** The root image can declare a window in its own
manifest:

    [sos.aarch64-unknown-none-elf]
    device-window = "0x09000000"

which becomes a device record in its `sosimg`, which the kernel installs as one
EL0-readable, EL0-writable, never-executable page before entering user mode.
Only root's image may declare one: the loader refuses a declared window in a
child image. The path is dormant (spec §2.5); the flag and the loader's door
survive because the emitter lives in the sawlang toolchain.

**The page carries device memory attributes (`Device-nGnRE`), never
Normal-cacheable**, and on this profile that is the driver's problem as much as
the kernel's. A cached register mapping would let a read come out of a line
filled minutes ago; device attributes forbid the hardware from gathering,
reordering or splitting accesses. The half a descriptor cannot state is access
size: device memory permits no unaligned and no multi-register access, so a
driver reads and writes each register at its own width — 32 bits on this board's
console — and never copies a register block.

The window is authorized by the board: this HAL publishes one
(`DEVICE_GRANT_BASE` / `DEVICE_GRANT_LEN`), and that pair is what the build
mirrors when it emits the device row a driver obtains. `device_window_ok` is the
same rule as a runtime predicate, checked at `carve` and at `map`, because a
caller chooses the bounds. A device window is a handle derived from a device
pool root, installed by an op and revoked by unmapping the Mapping (spec §2.5).

**The console handover protocol** applies to whoever holds the console's
window: the kernel goes quiet at its `SOS: console handover` marker and writes
again only on a diagnostic path. A driver that owns the console owns its receive
side and its control register outright — the kernel writes only the data
register, and only to transmit, so a driver that wants to receive must enable
the device itself. See `kcore`'s `Console`.

## What is arm64-specific here

Only the register names and the `svc` instruction, which are what `syscall.c`
is made of. The handle/op/status *shape* is the architecture-neutral part and
belongs to the spec, not here.

Two choices worth their sentence:

- **The op goes in x8, not the first free argument register.** x8 is where
  AArch64 Linux puts a syscall number, so every disassembler, debugger script
  and reader who has seen one arm64 syscall reads this one correctly — and it
  leaves x0-x3 as a clean run of handle and argument registers. The RISC-V
  profile makes the same trade with a7.
- **The width is the register's.** `UInt` is 64-bit in an arm64 process, so the
  declarations Saw makes against these symbols line up with `unsigned long`
  here and with `unsigned int` in the riscv32 counterpart, with no truncation
  on either. The values that cross the boundary as data rather than as words —
  status tags, op numbers, rights bits — are fixed-width in `sosabi` and do not
  change size with the profile.
