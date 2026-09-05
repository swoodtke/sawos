# SawOS

SawOS (SOS) is a capability-based microkernel for embedded systems. It runs
on riscv32 and arm64 — the gate is QEMU's `virt` boards for both, plus a
FLAT BUILD PROFILE of the riscv32 board that turns its protection off, and
there is a non-gating smoke target for the ESP32-C3 — with real hardware as
the goal.

Those three runs are the system's PROTECTION TIERS, one story told on each:
arm64 translates (per-process page tables, and a mapping's address is the
kernel's answer), riscv32 does not (numbered protection regions, and an
address is the same number in every process), and the flat profile cannot
deny an access at all. The platform ADVERTISES which it is — `Isolated` or
`Flat`, two words — so a program that needs isolation asks once and refuses
to run; everything else is written against one idiom set. What the flat tier
disclaims is DENIAL and nothing else: handles, rights, quotas and lifetimes
are enforced identically on all three, which the gate demonstrates by running
the whole object-model suite unchanged on each (`spec.md` §5b.1).

Every kernel resource is an object reached through a handle that carries
rights: processes, threads, memory regions and their mappings, events, IRQ
lines, clocks, timers, and the two ends of a pipe. A process holds handles,
not addresses; the kernel checks the rights on every operation. Processes
talk over pipes — bounded synchronous messages with request/reply built in,
carrying handles as well as bytes, so a capability can be sent. Userspace enters through a typed `sos` module rather than raw
syscall numbers, so the numbers are not ABI and can change without breaking
programs (`spec.md` §5.7).

The kernel is written in Saw, and two properties of the language are
properties of the system: destruction is deterministic (a dropped object is
reclaimed at a known point, with no garbage collector to pause the kernel),
and bounds, overflow, and shift checks stay on in kernel code. The kernel
builds with `--no-hidden-alloc`, which rejects any allocation the source
does not name.

## Building and running

The build resolves a Saw toolchain in this order: a `SAWLANG_ROOT`
environment variable naming a sawlang checkout, a `sawc` on `$PATH` whose
`--version` matches `sawlang.pin`, then a cached fetch of the pinned
commit. Building the kernel needs a checkout (the first or third option):
the runtime and image-format packages are compiled from source.

Host tools: `clang`, `ld.lld`, `qemu-system-riscv32`, `qemu-system-aarch64`.
The harness probes for them and prints install hints if one is missing.

```sh
SAWLANG_ROOT=/path/to/sawlang make sos-test
```

This builds the kernel and the root server, stitches the boot image, and
boots all three profiles under QEMU, asserting each console transcript and
exit status. 382 runs: 129 cases on riscv32, 129 on arm64, and 124 on the
flat profile — four fewer there, each excluded BY NAME with its reason,
because its assertion is that the hardware DENIED something.

## Layout

```
kernel/     the arch-free kernel: drivers, trap handling, object dispatch
kernel/abi/     op numbers, rights, statuses (kernel-internal)
kernel/sysapi/  the public `sos` module userspace compiles against
hal/arm64/      the arm64 architecture and board: boot, trap entry, linking
hal/riscv32-common/  the riscv32 architecture, shared by its boards
hal/riscv32/         the riscv32 `virt` board
hal/riscv32-flat/    the flat build PROFILE of that board (a facade, not a fork)
hal/riscv32-esp32c3/ the ESP32-C3 board (smoke target, not part of sos-test)
rt/         the runtime the kernel and every process share
root/       the root server, a normal package emitting a boot image
tests/      kernel test entries and hand-assembled payloads
tools/      the QEMU harness and the toolchain resolver
```

## Documentation

`spec.md` is the authoritative specification: the object model, rights,
syscall surface, memory layout, and the scheduling and interrupt rules.

## License

Apache-2.0 WITH LLVM-exception. See `LICENSE`.
