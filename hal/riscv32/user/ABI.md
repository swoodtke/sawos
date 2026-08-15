# riscv32 user HAL — the seam

The entire architecture-dependent surface of an SOS process: ONE symbol.
`sos/root/src/` builds for arm64 with only the manifest's `[sos] native` line
changing, and that line names one C file holding one function.

Design 172 part 2 shrank this from four symbols to one. The three that left —
`sos_set_system_handle`, `sos_rt_write` and `sos_rt_abort` — named no
architecture: a byte reaches the console through a System op, which is the same
op on both profiles, so two per-arch C copies were two copies of one thing. They
are Saw now, once, in `sos/kernel/sysapi/`, beside the System object whose
authority they use. What kept them here was DF-172e (a `noreturn` panic sink Saw
could not type), which design 177 closed.

## Which altitude is supported for whom

There are three ways to reach the kernel and they are ONE implementation chain,
not three (sos/spec.md §5.7):

| Altitude | Spelling | For |
|---|---|---|
| typed Saw | `system.shutdown(0)` | **Saw processes. Use this.** Handles are typed, statuses are a `SosStatus`, no number appears. |
| typed C | `sos_system_shutdown(h, 0)` | **Non-Saw languages.** One `@export`ed function per op, named for the op; still no number. |
| raw | `sos_syscall1(h, op, a)` / `sos_syscall3(h, op, a, b, c, &v)` | **The HAL and the kernel package only.** They take an op NUMBER, which is the thing the arrangement above exists to keep out of callers. Not a supported application interface. |

The first two are the kernel package's (`sos/kernel/sysapi/`), not this
directory's. This directory supplies only the bottom of the chain: the one
instruction that crosses the trap boundary. No op number appears in this HAL.

**A note on the typed C row, recorded rather than hidden (DF-172i).** It is a
SPECIFIED and linked interface with no in-tree caller since design 172 part 2 —
the process-side runtime sinks used to be C and were its only consumer. It is
still exactly what a non-Saw process would use, and the Saw sinks call the same
functions, so its BODIES run on every boot; what no longer runs on every boot is
a C caller crossing into them.

## Provided to a process

| Symbol | Contract |
|---|---|
| `sos_syscall1(handle, op, arg0) -> status` | Perform one object op that answers with a status alone. The value half is not read. |
| `sos_syscall3(handle, op, arg0, arg1, arg2, value_out) -> status` | The same, for ops that take up to three arguments AND answer with a VALUE — a created thread's handle, a joined thread's exit code, a process's status word (design 178 M2 unit 2). The value comes back through a POINTER rather than in the return, because the Saw side declares these symbols against a C ABI whose whitelist has no aggregate return; one out-parameter is the shape that crosses. Ops with fewer arguments pass zeros. |

The runtime's two hooks (`sos_rt_write`, `sos_rt_abort`) and the parked boot
handle are still part of a process's contract; they are just not this
directory's any more. See `sos/kernel/sysapi/src/lib.saw`, and
`sos/rt/common/src/lib.saw` for what calls them.

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

Only the register names and the `ecall` instruction — which, since design 172
part 2, is the whole of this directory. The handle/op/status *shape* is the
architecture-neutral part and belongs to the spec, not here; the arm64 HAL
implements the same one function over `svc`.
