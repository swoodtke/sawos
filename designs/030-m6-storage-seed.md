# SawOS design 30 — M6 SEED: storage & the userspace loader

Status: SEED, Sep 3 2026 — user-ruled as the M6 candidate during the
M5 run (the design-19 pattern: recorded ahead of the scoping session
it will anchor). Not a unit brief; nothing dispatches from this.

## The ruling (user, Sep 3)

M6 is the STORAGE milestone: a block store, a filesystem, and a
USERSPACE LOADER that loads binaries from it — with the namespace
(`open("/dev/uart0")`, `open("/bin/foo")` answering capabilities)
as the protocol that ties it together. The namespace was ruled out
of M5 (025 seed 3) precisely because it belonged here.

## The composition (verified against the tree, Sep 3)

Almost everything is existing machinery:

- **`ProcessCreate` already takes its image as a Memory handle** —
  the kernel accepts a user-supplied image capability today. A
  loader reads sosimg bytes into a split region and calls the ops
  that exist: create → give → start.
- **Bulk data rides shared memory, already sendable**: a client
  splits a region, sends the `Memory` handle in a pipe message (one
  of the six carried kinds since M4 unit 4), the driver maps it
  into itself, fills it, replies — §2.1's zero-copy fs example,
  finally executed.
- **The driver is a service** — M4 unit 5's pattern verbatim.

What is genuinely new: the block driver, the archive format, the
loader/namespace protocol.

## The tier of targets (QEMU virt, both arches)

- **FIRST: CFI NOR pflash** (two banks on both machines,
  memory-mapped). Programmed-I/O only — NO bus mastering — so its
  driver is NOT a TCB member under the ruled DMA doctrine
  (spec §2.5): it drops straight into driver-as-a-service with an
  `IoMemory` window and nothing else. Also matches the boot story's
  flash-partition framing and the ESP32 XIP orientation.
- **SECOND: virtio-blk over virtio-mmio** (8 slots riscv / 32
  arm64). The real block device — but a virtio device reads
  descriptors from guest RAM, which makes it a BUS MASTER, which
  makes that driver TCB until the IOMMU lands. Choosing flash first
  keeps the capability story honest; virtio-blk is the natural
  forcing function for the standing-tail IOMMU + critical-processes
  item (arm64 virt has an SMMUv3 when that day comes).
- Other peripherals on the shelf, for the record: PCIe ECAM both
  machines (NVMe, e1000, xhci), virtio-net/-rng/-9p, PL031/Goldfish
  RTC.

## Filesystem: read-only archive + a tmpfs for the write path

v1 is TWO small filesystems, one per path:

- **The FLAT ARCHIVE (read path)** — a table of contents (name →
  offset, len) on a flash partition; enough for "the loader loads
  binaries", trivial to emit from the build.
- **A SIMPLE TMPFS (write path — user-ruled IN, Sep 3)**: a
  RAM-backed fs service exercising create/write/read-back/delete
  through the SAME namespace protocol, so the protocol is proven
  read-write without touching flash programming at all. It is a
  pure userspace service over Memory splits — which makes it the
  first real out-of-tree consumer of M5's allocator (files come and
  go; spent bytes MUST come back) and a natural donation customer
  if its tables outgrow a compiled floor. One protocol, two
  backends, is also the §12 design test applied to filesystems:
  if the namespace protocol can't serve both, the protocol is
  wrong.

littlefs (NOR-native, tiny, journaled) is the step when PERSISTENT
writes matter; FAT only if outside-world interchange ever does. The
fs services speak the namespace protocol; whether fs and block
driver are one process or two is a scoping-session call (the design
test from §12: splitting must need zero kernel changes).

## The shell (user-ruled IN, Sep 3): poke at a running system

A VERY SIMPLE interactive shell-like utility on the UART — basic
builtins to list files/directories, read a file, write a file. The
point is OPERABILITY: a human at the console inspecting a live
system, which the suite's scripted transcripts never give. The
composition is all existing or in-milestone pieces: it is a CLIENT
of the M4 uart service (blocking read = the fused `Call`, write =
the TELL — no device authority held) and of the fs services through
the namespace (`ls` walks the archive's and tmpfs's names, `cat`
reads, `write` exercises the tmpfs path end to end from a
keyboard). No kernel surface: builtins only in v1 — RUNNING a named
binary from the archive is the loader's demo and may join late in
the milestone if the session wants it. It is also the seed of
`designs/031`'s "native shell that spawns toybox utilities": this
utility is where that launcher grows from.

Testing note for the scoping session: an interactive client wants a
scripted mode (feed a command list, assert the output) so the gate
can witness it the way it witnesses everything else; the QEMU
harness already types into the UART for the uart-echo cases.

## What M5 hands this milestone

The allocator (image-sized splits and their return — landed unit 5),
slab donation (more processes than the compiled floor), placement
(clean loading at kernel-chosen VAs), and the linmap (kernel access
to resolved user memory). The M5→M6 seam is deliberate.

## Adjacent

M7 is ruled as the POSIX compatibility layer (`designs/031`), which
consumes this milestone's loader and namespace directly.
