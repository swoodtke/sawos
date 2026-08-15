# arm64 user HAL — the seam

The entire architecture-dependent surface of an SOS process on Profile B: ONE
symbol. `sos/root/src/` builds for both profiles with only its manifest changing
— design 162 is the milestone that proved it rather than claiming it.

Design 172 part 2 shrank this from four symbols to one, for the reason set out
in `sos/hal/riscv32/user/ABI.md`: the three that left named no architecture, so
two per-arch C copies were two copies of one thing. They are Saw now, once, in
`sos/kernel/sysapi/`.

## Which altitude is supported for whom

Unchanged from `sos/hal/riscv32/user/ABI.md`: typed Saw (`system.shutdown(0)`)
for Saw processes, typed C (`sos_system_shutdown(h, 0)`) for other languages,
and the raw `sos_syscall1` / `sos_syscall3` for the HAL and the kernel package
only.
The first two are the kernel package's (`sos/kernel/sysapi/`), not this
directory's; this directory supplies only the bottom of the chain. The typed C
row's in-tree caller went away with the C sinks — see DF-172i, recorded there.

## Provided to a process

| Symbol | Contract |
|---|---|
| `sos_syscall1(handle, op, arg0) -> status` | Perform one object op that answers with a status alone. |
| `sos_syscall3(handle, op, arg0, arg1, arg2, value_out) -> status` | The same, for ops that take up to three arguments AND answer with a VALUE (design 178 M2 unit 2). The value comes back through a pointer — the C ABI the Saw side declares against has no aggregate return. Same contract as the riscv32 twin, which states the reasoning. |

The runtime's two hooks (`sos_rt_write`, `sos_rt_abort`) and the parked boot
handle are still part of a process's contract; they are just not this
directory's any more. See `sos/kernel/sysapi/src/lib.saw`.

## Required of a process

- An entry point taking the boot handle as its first argument. The kernel places
  it in x0 before `eret`ing to EL0, so a Saw
  `@export("_start") func _start(boot_handle: UInt)` receives it directly.

## The syscall ABI (sos/spec.md §5.7)

Every syscall is an object op — there are no bare numbered syscalls.

| Register | Meaning |
|---|---|
| `x0` | handle (in), status word (out) |
| `x8` | op — a method id on that object's table, not a global number |
| `x1`-`x5` | arguments; `x1` also carries the value half on return |

`svc #0` traps. A status of 0 is success; anything else is a `SosStatus` tag,
and the process keeps running — a bad call is an error, not a fault.

## What is arm64-specific here

Only the register names and the `svc` instruction — which, since design 172
part 2, is the whole of this directory. The handle/op/status *shape* is the
architecture-neutral part and belongs to the spec, not here.

Two choices worth their sentence (design 162 decision 1, ratified):

- **The op goes in x8, not the first free argument register.** x8 is where
  AArch64 Linux puts a syscall number, so every disassembler, debugger script
  and reader who has seen one arm64 syscall reads this one correctly — and it
  leaves x0-x5 as a clean run of argument registers. The RISC-V profile makes
  the same trade with a7.
- **The width is the register's.** `UInt` is 64-bit in an arm64 process, so the
  declarations Saw makes against these symbols line up with `unsigned long`
  here and with `unsigned int` in the riscv32 counterpart, with no truncation
  on either. The values that cross the boundary as DATA rather than as words —
  status tags, op numbers, rights bits — are fixed-width in `sosabi` and do not
  change size with the profile.
