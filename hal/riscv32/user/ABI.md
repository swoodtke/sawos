# riscv32 user HAL — the seam

The entire architecture-dependent surface of an SOS process. A process names
these four symbols and nothing else about its machine, which is what should let
`sos/root/src/` build for arm64 (design 79) with only the manifest's
`[sos] native` line changing.

## Which altitude is supported for whom

There are three ways to reach the kernel and they are ONE implementation chain,
not three (sos/spec.md §5.7):

| Altitude | Spelling | For |
|---|---|---|
| typed Saw | `system.shutdown(0)` | **Saw processes. Use this.** Handles are typed, statuses are a `SosStatus`, no number appears. |
| typed C | `sos_system_shutdown(h, 0)` | **Non-Saw languages.** One `@export`ed function per op, named for the op; still no number. |
| raw | `sos_syscall1(h, op, a)` | **The HAL and the kernel package only.** It takes an op NUMBER, which is the thing the arrangement above exists to keep out of callers. Not a supported application interface. |

The first two are the kernel package's (`sos/kernel/sysapi/`), not this
directory's. This directory supplies only the bottom of the chain — the one
instruction that crosses the trap boundary — plus the two runtime sinks, which
themselves call the typed C surface rather than the raw form, so no op number
appears in this HAL either.

## Provided to a process

| Symbol | Contract |
|---|---|
| `sos_syscall1(handle, op, arg0) -> status` | Perform one object op. Returns the status word; the value half is discarded. |
| `sos_syscall1_value(handle, op, arg0, &value) -> status` | The same, keeping the value. No M1 op returns one; it exists so the seam is complete. |
| `sos_set_system_handle(handle)` | Remember the boot handle so the runtime's own sinks can use it. Must be called before anything can print or panic. |
| `sos_rt_write(ptr, len)` | Write bytes to the debug console, via the System object. Called by `sos/rt/common_c/support.c`. |
| `sos_rt_abort(code)` | Stop the machine, via the System object. Never returns. |

## Required of a process

- An entry point taking the boot handle as its first argument. The kernel places
  it in the first argument register before entering user mode, so a Saw
  `@export("_start") func _start(boot_handle: UInt)` receives it directly.

## The syscall ABI (sos/spec.md §5.7)

Every syscall is an object op — there are no bare numbered syscalls.

| Register | Meaning |
|---|---|
| `a0` | handle (in), status word (out) |
| `a7` | op — a method id on that object's table, not a global number |
| `a1`-`a5` | arguments; `a1` also carries the value half on return |

`ecall` traps. A status of 0 is success; anything else is a `SosStatus` tag, and
the process keeps running — a bad call is an error, not a fault.

## What is riscv32-specific here

Only the register names and the `ecall` instruction. The handle/op/status
*shape* is the architecture-neutral part and belongs to the spec, not to this
directory; an arm64 HAL implements the same five functions over `svc`.
