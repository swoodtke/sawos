#!/usr/bin/env python3
"""SOS QEMU test harness.

Builds the freestanding SOS kernel (and, for the cases that need one, a root
server image), runs it under QEMU `virt` on every profile SOS targets, and
asserts the console transcript and the emulator's exit status. `make sos-test`
green means sawc-built code boots, crosses into user mode, and gets the right
answer or the right diagnostic on every profile; any profile failing is red.

One arch-free kernel (`kernel/core/`) plus one directory per machine
(`hal/<arch>/kernel/`) is what makes a new target a table entry and a
directory. `_check_arch_free` keeps that true: an architecture name in the
arch-free kernel would still compile, and would only be wrong on the profile
nobody happened to be building.

Every kernel source builds under `--no-hidden-alloc`, so any compiler-inserted
allocation the source does not name breaks the build here.

Pipeline per test case, per architecture:
  1. sawc   : <src>.saw  -> <name>.o   (--freestanding --no-hidden-alloc
              --target <triple>, --module-path kcore=… hal=… — the arch-free
              kernel and the HAL it reaches the machine through)
  2. clang  : boot.S     -> boot.o     (kernel HAL; assembled once)
  3. clang  : sink.c     -> sink.o     (kernel HAL: board hooks + protection)
     clang  : support.c  -> support.o  (the arena accessors and the word
                                        atomics — the C that must stay C,
                                        compiled once)
  4. the `.payload` blob, if the case has one — EITHER a hand-written `.S` from
     `tests/<arch>/` (user-mode code, hand-assembled sosimgs) OR a root
     package built by Blade and pulled in through kernel/rootimg.S's `.incbin`
  5. ld.lld : link with the HAL's virt.ld --gc-sections -> <name>.elf
  6. qemu   : run with a hard timeout; capture console stdout + exit status

A root package (`root_pkg`) is a real Blade package with `[sos] emit =
"sosimg"` in its manifest and a `[sos.<triple>]` section per machine, so the
two-image cases exercise the same build path any SOS process uses, and one
unchanged `src/` builds for every profile.

QEMU, ld.lld and clang are host prerequisites, not Blade-managed; the harness
probes for them up front and fails with an install hint.

The sawlang side (the compiler, Blade, and the `imgformat`/`toml`/`semver`
packages) is not a host prerequisite and no path to it is computed here: it
comes from `tools/toolchain.py`, the one place allowed to locate a sawlang
artifact.

`--arch <name>` runs one architecture, for development; the gate runs all.

`-j N` runs N package builds, and then N cases, at a time. It defaults to one
job per performance core (`_default_jobs`): the parallelism tracks the P-cores
rather than the core count, because a QEMU boot parked on an efficiency core is
the slowest thing in the run. `SOS_JOBS` overrides the detection and `-j`
overrides both. The count is never printed, so it cannot move a transcript.
`-j 1` is the serial harness, for reading a transcript against a serial run's.

Parallelism must not move a case's transcript, its assertions, or the order
the report prints in. Workers return text and a verdict; the caller prints,
walking the case list in definition order, so a parallel run's output is
byte-identical to a serial one's. The isolation that makes the transcripts
identical is stated at the three places it is needed: `_stitch_root_image`'s
per-case staging directory, `_run_qemu`'s own stdin, and `main`'s eager `tc()`.
"""

import argparse
import concurrent.futures
import os
import re
import shutil
import subprocess
import sys
import threading
import time

import toolchain

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KERNEL_DIR = os.path.join(REPO_ROOT, "kernel")
CORE_DIR = os.path.join(KERNEL_DIR, "core")
TESTS_DIR = os.path.join(REPO_ROOT, "tests")
HAL_DIR = os.path.join(REPO_ROOT, "hal")

# The sawlang side comes from the resolver, not from `REPO_ROOT`: the compiler,
# Blade, and the `imgformat`/`toml`/`semver` packages live in the language
# repository, so each is a `tools/toolchain.py` call and none is a path computed
# here. SOS's own sources are computed off `REPO_ROOT`.
_TOOLCHAIN = None


def tc():
    """The resolved sawlang toolchain, resolved once.

    Lazy so that importing this file cannot exit: the refusal is a message for
    an operator running the harness, and it is printed once, here.
    """
    global _TOOLCHAIN
    if _TOOLCHAIN is None:
        try:
            _TOOLCHAIN = toolchain.resolve()
        except toolchain.ToolchainError as e:
            print(f"\033[1;31merror\033[0m: {e}", file=sys.stderr)
            sys.exit(1)
        for note in _TOOLCHAIN.notes:
            print(note)
    return _TOOLCHAIN


# The arch-free kernel module every image shares. It carries the trap handler
# the HAL's boot code calls, so it is required for every case.
CORE_MODULE = f"kcore={CORE_DIR}"

# Arch-free, role-free Saw runtime helpers, shared with every process build.
SOSRT_DIR = os.path.join(REPO_ROOT, "rt", "common", "src")
SOSRT_MODULE = f"sosrt={SOSRT_DIR}"

# The call numbers. Kernel-internal (spec.md §5.7's vDSO discipline): the
# dispatch tables here and the `sos` module the kernel exports to userspace
# share it, and nothing else does. A process links `sos` and never sees a
# number, so it never needs this path.
SOSABI_DIR = os.path.join(KERNEL_DIR, "abi", "src")
SOSABI_MODULE = f"sosabi={SOSABI_DIR}"

RT_COMMON_C_DIR = os.path.join(REPO_ROOT, "rt", "common_c")

# The shared riscv32 HAL (`hal/riscv32-common`): `kernel/sink.c`,
# `user/syscall.c` and the architectural half of `kernel/lib.saw` (trap frame,
# cause decoding, syscall accessors, PMP staging, payload/region seams) exist in
# one copy that both boards use. Each board's build entry points at it: the C
# files by path (`hal_native` below, and every Saw root/child manifest's
# `native` line; `tests/c-child` names its own C files), the Saw half as one
# more `--module-path` beside the board's own `hal=`.
#
# `kernel/core` imports a module named `hal` and the runner maps
# `hal=<board>/kernel`; the board's `hal` module `public import`s from
# `rv32core` and re-exports what `kcore` consumes.
RV32_COMMON_DIR = os.path.join(HAL_DIR, "riscv32-common")
RV32_CORE_MODULE = f"rv32core={os.path.join(RV32_COMMON_DIR, 'kernel')}"

# The virt board under a second module name.
#
# The directory the isolated profile maps as `hal=` is mapped as `rv32virt=` for
# the flat profile, whose own `hal` module re-exports the board half from it and
# defines only the protection surface. A Saw module's name comes from its
# `--module-path` mapping, so one directory can be the board's `hal` in one
# build and a component of another `hal` in a second, with no second copy of
# the UART, CLINT, PLIC and memory map.
RV32_VIRT_MODULE = f"rv32virt={os.path.join(HAL_DIR, 'riscv32', 'kernel')}"

# `ExitCode.ProcessFault` in kernel/core/diag.saw: what the machine exits with
# when the kernel terminates a process for a caller error it could have
# checked. Kept in step with that enum.
EXIT_PROCESS_FAULT = 5

# `AbortCode.NoMemory` in rt/common/src/lib.saw: what the runtime stops the
# machine with when the bump arena cannot serve a request. Deliberately
# different from the kernel's exit codes, so a transcript can tell "the program
# panicked" from "the arena ran dry" by the status alone. Kept in step with that
# enum.
EXIT_ARENA_EXHAUSTED = 65

# Root-server packages: real Blade packages built through the same pipeline any
# SOS process uses. (Blade and its packages come through `tc()` above.)
ROOT_PKG = os.path.join(REPO_ROOT, "root")
FAULT_ROOT_PKG = os.path.join(TESTS_DIR, "faulting-root")
# Thread and Process objects. Test processes reach the kernel through the `sos`
# module and never through an op number.
THREAD_BASICS_PKG = os.path.join(TESTS_DIR, "thread-basics")
THREAD_PREEMPT_PKG = os.path.join(TESTS_DIR, "thread-preempt")
# Event and Waiter objects.
EVENT_BASICS_PKG = os.path.join(TESTS_DIR, "event-basics")
EVENT_WAKE_PKG = os.path.join(TESTS_DIR, "event-wake")
# Event consume: a wait delivery takes the Event's word. Two packages, split by
# door: one where every wait finds something already ready (the waiting thread
# reads its own record), and one where both takes are real wakes (a signalling
# thread writes into a parked thread's frame). Each path needs its own case.
EVENT_CONSUME_PKG = os.path.join(TESTS_DIR, "event-consume")
EVENT_CONSUME_WAKE_PKG = os.path.join(TESTS_DIR, "event-consume-wake")
# A Waiter fault a Saw process can reach (a duplicate attachment key), so it is
# written as a real process rather than a payload.
EVENT_DUPKEY_PKG = os.path.join(TESTS_DIR, "event-dupkey")
# Interrupt object. The echo packages are per device rather than per
# architecture (a driver names its device, and the profiles have different
# UARTs), so each case names the architecture it applies to and only that
# one's packages are built. The two fault packages are arch-free.
UART_ECHO_NS16550_PKG = os.path.join(TESTS_DIR, "uart-echo-ns16550")
UART_ECHO_PL011_PKG = os.path.join(TESTS_DIR, "uart-echo-pl011")
IRQ_BADLINE_PKG = os.path.join(TESTS_DIR, "irq-badline")
IRQ_EARLYACK_PKG = os.path.join(TESTS_DIR, "irq-earlyack")
# With no line bound, "nothing runnable" is still reported as a deadlock.
WAIT_DEADLOCK_PKG = os.path.join(TESTS_DIR, "wait-deadlock")
# The process cases use one claim per image, so a bug in the reload cannot hide
# behind a load that happened to work. The child packages are Blade packages
# built as a root is. On riscv32 each concurrent child links at its own base
# (`hal/riscv32/user/child.ld`, `child2.ld`, ...), because that profile does not
# translate and every process shares one physical address space; arm64
# translates and links every image from `user.ld`. Neither child here holds a
# handle: `start()` hands a child `NO_HANDLE`, so its observable behaviour is
# faulting.
PROCESS_LIFECYCLE_PKG = os.path.join(TESTS_DIR, "process-lifecycle")
PROCESS_ISOLATION_PKG = os.path.join(TESTS_DIR, "process-isolation")
PROCESS_BADIMAGE_PKG = os.path.join(TESTS_DIR, "process-badimage")
PROCESS_DOUBLESTART_PKG = os.path.join(TESTS_DIR, "process-doublestart")
PROCESS_BOOTDRAIN_PKG = os.path.join(TESTS_DIR, "process-bootdrain")
CHILD_FAULT_PKG = os.path.join(TESTS_DIR, "child-fault")
CHILD_POKE_PKG = os.path.join(TESTS_DIR, "child-poke")

# Handle lifecycle, one claim per image. Four of the seven end in a fault, and a
# fault ends the process, so a second probe after one would never run. Three of
# them (`nothing`, `twice`, `malformed`) are the three shapes of "this word
# resolves to no object": one answer from the kernel, three different mistakes
# from a caller.
#
# Four reach the C altitude: the typed Saw surface has no word extractor or
# constructor, so a handle word cannot be compared, re-used after release, or
# forged there at all. `handle-drop-release` is the same mechanism seen from the
# typed side, where release is a value going out of scope and no word appears.
HANDLE_REMINT_PKG = os.path.join(TESTS_DIR, "handle-remint")
HANDLE_RELEASE_UNGATED_PKG = os.path.join(TESTS_DIR, "handle-release-ungated")
HANDLE_RELEASE_NOTHING_PKG = os.path.join(TESTS_DIR, "handle-release-nothing")
HANDLE_RELEASE_TWICE_PKG = os.path.join(TESTS_DIR, "handle-release-twice")
HANDLE_MALFORMED_WORD_PKG = os.path.join(TESTS_DIR, "handle-malformed-word")
HANDLE_DROP_RELEASE_PKG = os.path.join(TESTS_DIR, "handle-drop-release")
PROCESS_RECLAIM_PKG = os.path.join(TESTS_DIR, "process-reclaim")
GONE_RECLAIM_TEARDOWN_PKG = os.path.join(TESTS_DIR, "gone-reclaim-teardown")
# A process is a record and a body. `process-records` holds dead children until
# the record table refuses and then gives them back; `process-records-donate`
# moves that wall by donation; the two husk packages map into a child that dies
# and probe the Mapping once another child lives in the dead one's body.
PROCESS_RECORDS_PKG = os.path.join(TESTS_DIR, "process-records")
PROCESS_RECORDS_DONATE_PKG = os.path.join(TESTS_DIR, "process-records-donate")
MAPPING_HUSK_BASE_PKG = os.path.join(TESTS_DIR, "mapping-husk-base")
MAPPING_HUSK_UNMAP_PKG = os.path.join(TESTS_DIR, "mapping-husk-unmap")
CHILD_SUPERVISOR_PKG = os.path.join(TESTS_DIR, "child-supervisor")
# Mint, give, and the boot drain. The children here receive a masked system
# handle, so they can print, name themselves, drain and exit.
GIVE_BOOT_DRAIN_PKG = os.path.join(TESTS_DIR, "give-boot-drain")
GIVE_DUPLICATE_TAG_PKG = os.path.join(TESTS_DIR, "give-duplicate-tag")
GIVE_AFTER_START_PKG = os.path.join(TESTS_DIR, "give-after-start")
GIVE_NO_TRANSFER_PKG = os.path.join(TESTS_DIR, "give-no-transfer")
PROCESS_EXIT_NOT_SELF_PKG = os.path.join(TESTS_DIR, "process-exit-not-self")
START_BAD_TAG_PKG = os.path.join(TESTS_DIR, "start-bad-tag")
GIVE_WORD_DEAD_PKG = os.path.join(TESTS_DIR, "give-word-dead")
MINT_REVOKED_PKG = os.path.join(TESTS_DIR, "mint-revoked")
CHILD_NO_SHUTDOWN_PKG = os.path.join(TESTS_DIR, "child-no-shutdown")
CHILD_DRAIN_PKG = os.path.join(TESTS_DIR, "child-drain")
CHILD_OVERSTEPS_PKG = os.path.join(TESTS_DIR, "child-oversteps")

# Memory, IoMemory and Mapping. Three of the five root servers end in a fault,
# so each does its positive work first and meets its refusal last: the
# transcript reads as everything that worked, then the one thing that must not.
#
# Three of them name an address, through a one-line C constant per architecture
# (`tests/poolbase_<arch>.c`). That is root's config, not kernel state leaking:
# a region has no bounds reader, so where a pool is, is a fact the build
# publishes and the program expects, as the uart-echo driver does with
# `UART_BASE`.
MEMORY_SPLIT_PKG = os.path.join(TESTS_DIR, "memory-split")
MAP_BASICS_PKG = os.path.join(TESTS_DIR, "map-basics")
MAP_UNMAP_PKG = os.path.join(TESTS_DIR, "map-unmap")
MAP_INTO_CHILD_PKG = os.path.join(TESTS_DIR, "map-into-child")
IOMEMORY_CARVE_PKG = os.path.join(TESTS_DIR, "iomemory-carve")
CHILD_TOUCH_PKG = os.path.join(TESTS_DIR, "child-touch")

# Quotas and the reference count. The three refcount cases are root's, because
# free-on-zero is about objects and root may hold any number of them. The two
# quota cases need a child: root is init, its policy cap is the machine, so its
# ledger rows are unlimited and only the slab can refuse it. A budget belongs to
# a launched process, so proving one costs a launcher and a child.
QUOTA_EXCEEDED_PKG = os.path.join(TESTS_DIR, "quota-exceeded")
QUOTA_VS_WALL_PKG = os.path.join(TESTS_DIR, "quota-vs-wall")
REFCOUNT_FREE_PKG = os.path.join(TESTS_DIR, "refcount-free")
INTERRUPT_UNBIND_PKG = os.path.join(TESTS_DIR, "interrupt-unbind")
MAPPING_SLOT_FREE_PKG = os.path.join(TESTS_DIR, "mapping-slot-free")

# The allocator. It borrows `child-touch` as its child: leg 2 needs a child that
# marks a page it was mapped and dies.
MEMORY_RECYCLE_PKG = os.path.join(TESTS_DIR, "memory-recycle")

# Root donates a page to the Waiter slab and creates past the compiled floor. No
# child, no device; only a pool to cut from.
SLAB_DONATE_PKG = os.path.join(TESTS_DIR, "slab-donate")

# The negative half: a region with a sibling handle is refused, by ending the
# caller. See the package header for why that is a fault and not a status.
SLAB_DONATE_SHARED_PKG = os.path.join(TESTS_DIR, "slab-donate-shared")

# Donation of the other object kinds, one package each. `thread-donate` starts,
# yields and joins a donated thread, so the context-switch path runs on a frame
# in donated memory. `pipe-donate` carries a real message over a connection
# whose slot and staging ring are both in extent 1.
THREAD_DONATE_PKG = os.path.join(TESTS_DIR, "thread-donate")
PIPE_DONATE_PKG = os.path.join(TESTS_DIR, "pipe-donate")

# `slab-donate-mapped` covers the `maps == 0` half of the donation safety
# condition; `slab-donate-free-nodes` composes donation with the node slab,
# which needs a Memories donation before the pool can be fragmented far enough
# to reach the node slab's wall.
SLAB_DONATE_MAPPED_PKG = os.path.join(TESTS_DIR, "slab-donate-mapped")
SLAB_DONATE_MAPPED_CHILD_PKG = os.path.join(TESTS_DIR, "child-donor")
SLAB_DONATE_NODES_PKG = os.path.join(TESTS_DIR, "slab-donate-free-nodes")

# `process-donate` fills the body table at `MAX_PROCESSES` (root plus two
# children), watches the next create answer `NoResource`, donates, then creates,
# starts and reaps a third child, and re-creates one that two fillers leave no
# room for but the donated extent.
#
# There are three echo children for riscv32's sake: it places by identity, so
# three concurrent children need three load addresses and therefore three
# linker scripts, and a package's manifest picks a script. The three packages
# differ only in that line and in an exit code. On aarch64 all three name one
# `user.ld`.
PROCESS_DONATE_PKG = os.path.join(TESTS_DIR, "process-donate")
ECHO_CHILD_PKG = os.path.join(TESTS_DIR, "echo-child")
ECHO_CHILD2_PKG = os.path.join(TESTS_DIR, "echo-child2")
ECHO_CHILD3_PKG = os.path.join(TESTS_DIR, "echo-child3")
CHILD_QUOTA_PKG = os.path.join(TESTS_DIR, "child-quota")
CHILD_MAPWALL_PKG = os.path.join(TESTS_DIR, "child-mapwall")

# Death notifications. The two notify cases differ only in how the child dies:
# the same attachment and park deliver `Exited` for one and `Faulted` for the
# other, so a §8 status word arrives through one mechanism either way.
# `death_late_attach` is the terminal-level case and covers the refcount
# interplay, since "which reference was holding the dead record open" only has
# an answer once there are two.
#
# `child-bye` is the smallest program that can die observably: derive a Process
# object, say one line, `exit(5)`. `child-fault` is reused for the fault arm.
DEATH_NOTIFY_PKG = os.path.join(TESTS_DIR, "death-notify")
DEATH_FAULT_PKG = os.path.join(TESTS_DIR, "death-fault")
DEATH_LATE_ATTACH_PKG = os.path.join(TESTS_DIR, "death-late-attach")
CHILD_BYE_PKG = os.path.join(TESTS_DIR, "child-bye")

# The driver child, shared memory, and the exec gate. A rights refusal and a
# bad-argument refusal are both faults, so the two exec-gate claims cannot share
# an image.
#
# `driver-child` names no device (what it moves is a capability, and a
# capability has no datasheet), so it is arch-free; the two echo children are
# per chip, like the root-as-driver twins beside them. The twins stay: the same
# driver body running as root and as a child, in two transcripts, shows that the
# difference is the launcher rather than the program.
DRIVER_CHILD_PKG = os.path.join(TESTS_DIR, "driver-child")
CHILD_ECHO_NS16550_PKG = os.path.join(TESTS_DIR, "child-echo-ns16550")
CHILD_ECHO_PL011_PKG = os.path.join(TESTS_DIR, "child-echo-pl011")
SHARE_DOUBLE_MAP_PKG = os.path.join(TESTS_DIR, "share-double-map")
# Placement's own pair: one region, two address spaces, two different addresses.
# aarch64 only; riscv32 keeps identity placement, so there the two addresses
# would be one and the case would assert nothing.
MAP_PLACED_PKG = os.path.join(TESTS_DIR, "map-placed")
CHILD_PLACED_PKG = os.path.join(TESTS_DIR, "child-placed")
CHILD_SHARE_PKG = os.path.join(TESTS_DIR, "child-share")
MAP_EXEC_GATED_PKG = os.path.join(TESTS_DIR, "map-exec-gated")
MAP_WX_REFUSED_PKG = os.path.join(TESTS_DIR, "map-wx-refused")

# Waiter revocation: one root server, because its three claims (a revocation is
# delivered, the walk is the whole blocked list, the count gates it rather than
# the release) are one story about one release, and splitting them would need
# the same three threads on the same Waiter in each image.
#
# It reaches the C altitude: the count claim needs two names for one Waiter, and
# the typed `sos` surface publishes `mint` on `System` and `Memory` alone while
# a `Waiter` keeps its handle word private, so the reference arithmetic is
# written where a handle is a word.
WAITER_REVOKED_PKG = os.path.join(TESTS_DIR, "waiter-revoked")

# The pipe. A rights refusal and a bad-argument refusal are both faults, so the
# two negative arms cannot share an image with each other or with anything that
# has work left to do.
#
# `pipe-basics` and `pipe-peer-gone` are one process each: neither waits, so the
# whole data path (round trip, FIFO, the empty message, the ring's depth, both
# zero-arms) is provable inside one address space with both ends in one hand.
# That leaves `pipe-child` a proof about give and the boot drain: it moves one
# message.
PIPE_BASICS_PKG = os.path.join(TESTS_DIR, "pipe-basics")
PIPE_PEER_GONE_PKG = os.path.join(TESTS_DIR, "pipe-peer-gone")
PIPE_CONSUME_PKG = os.path.join(TESTS_DIR, "pipe-consume")
CHILD_PARKED_PKG = os.path.join(TESTS_DIR, "child-parked")
PIPE_CHILD_PKG = os.path.join(TESTS_DIR, "pipe-child")
CHILD_POST_PKG = os.path.join(TESTS_DIR, "child-post")
PIPE_NO_POST_PKG = os.path.join(TESTS_DIR, "pipe-no-post")
PIPE_OVERSIZED_PKG = os.path.join(TESTS_DIR, "pipe-oversized")

# The one-shot pair. A rights refusal, a bad-argument refusal and a dead-handle
# refusal are all faults, so none of the three negative arms can share an image.
#
# `pipe-oneshot` is one process playing both sides: the whole exchange (claim,
# obligation, would-block, reply, out-of-order replies, the ring counted around
# one unsettled exchange) is provable inside one address space. That leaves
# `pipe-delegate` free to prove the obligation crossing a boundary.
PIPE_ONESHOT_PKG = os.path.join(TESTS_DIR, "pipe-oneshot")
PIPE_ABANDON_PKG = os.path.join(TESTS_DIR, "pipe-abandon")
PIPE_DELEGATE_PKG = os.path.join(TESTS_DIR, "pipe-delegate")
CHILD_REPLY_PKG = os.path.join(TESTS_DIR, "child-reply")
PIPE_NO_REPLY_PKG = os.path.join(TESTS_DIR, "pipe-no-reply")
PIPE_BIG_REPLY_PKG = os.path.join(TESTS_DIR, "pipe-big-reply")
PIPE_DEAD_CLAIM_PKG = os.path.join(TESTS_DIR, "pipe-dead-claim")
PIPE_REPLY_SELF_PKG = os.path.join(TESTS_DIR, "pipe-reply-self")
PIPE_REPLY_WAIT_SELF_PKG = os.path.join(TESTS_DIR, "pipe-reply-wait-self")
PIPE_SELF_OUTLET_PKG = os.path.join(TESTS_DIR, "pipe-self-outlet")
PIPE_PARKED_REWRITE_PKG = os.path.join(TESTS_DIR, "pipe-parked-rewrite")

# Waitability. A rights refusal and a bad-argument refusal are faults, so
# neither negative arm can share an image with anything that has work left.
#
# The four arm cases are one process each and nothing in them parks. Root plays
# both sides of every connection, so each level is raised before it is waited
# on (spec.md §2.2's level-triggering), which lets one thread assert what a
# delivery contains. They prove the four arms' payloads and ordering rules.
# `pipe_send_manual` proves the wake, since a genuine park needs something else
# to be runnable, and it uses the composition a caller writes: the surface is
# the primitives, not a library `send`.
PIPE_WAIT_REPLY_PKG = os.path.join(TESTS_DIR, "pipe-wait-reply")
PIPE_WAIT_GIVE_PKG = os.path.join(TESTS_DIR, "pipe-wait-give")
PIPE_WAIT_ROOM_PKG = os.path.join(TESTS_DIR, "pipe-wait-room")
PIPE_WAIT_SERVER_PKG = os.path.join(TESTS_DIR, "pipe-wait-server")
PIPE_SEND_MANUAL_PKG = os.path.join(TESTS_DIR, "pipe-send-manual")
CHILD_SERVER_PKG = os.path.join(TESTS_DIR, "child-server")
PIPE_NO_WAIT_PKG = os.path.join(TESTS_DIR, "pipe-no-wait")
PIPE_BAD_MODE_PKG = os.path.join(TESTS_DIR, "pipe-bad-mode")

# Exact trap introspection. The child prints nothing: every printed byte is an
# `ecall`, so a talking child's syscall count would be a hash of its own prose,
# and its launcher asserts the exact number.
PROCESS_STATS_PKG = os.path.join(TESTS_DIR, "process-stats")
CHILD_STATS_PKG = os.path.join(TESTS_DIR, "child-stats")

# The fused paths. `pipe-pingpong` asserts both trap counts exactly: root serves
# with `reply_wait`, a silent child clients with `send`. The three abandonment
# cases are the peer-gone rule reaching a park that is on no list, and the
# three negative arms are the refusals of the fused `send` and `reply_wait`.
PIPE_PINGPONG_PKG = os.path.join(TESTS_DIR, "pipe-pingpong")
CHILD_PINGPONG_PKG = os.path.join(TESTS_DIR, "child-pingpong")
PIPE_CALL_ABANDON_PKG = os.path.join(TESTS_DIR, "pipe-call-abandon")
CHILD_CALLER_PKG = os.path.join(TESTS_DIR, "child-caller")
PIPE_CALL_ORPHAN_PKG = os.path.join(TESTS_DIR, "pipe-call-orphan")
CHILD_ORPHAN_PKG = os.path.join(TESTS_DIR, "child-orphan")
PIPE_RESOLVE_ORPHAN_PKG = os.path.join(TESTS_DIR, "pipe-resolve-orphan")
CHILD_RESOLVER_PKG = os.path.join(TESTS_DIR, "child-resolver")
PIPE_NO_CALL_PKG = os.path.join(TESTS_DIR, "pipe-no-call")
PIPE_CALL_OVERSIZED_PKG = os.path.join(TESTS_DIR, "pipe-call-oversized")
PIPE_REPLY_WAIT_DEAD_PKG = os.path.join(TESTS_DIR, "pipe-reply-wait-dead")

# Handles in messages, the rendezvous ledger, the completion queue and the keep
# mask.
PIPE_HANDLES_PKG = os.path.join(TESTS_DIR, "pipe-handles")
PIPE_DELEGATE_MSG_PKG = os.path.join(TESTS_DIR, "pipe-delegate-msg")
CHILD_HANDLES_PKG = os.path.join(TESTS_DIR, "child-handles")
CHILD_SENDER_PKG = os.path.join(TESTS_DIR, "child-sender")
PIPE_SEND_EXIT_PKG = os.path.join(TESTS_DIR, "pipe-send-exit")
PIPE_TABLE_FULL_PKG = os.path.join(TESTS_DIR, "pipe-table-full")
PIPE_CQ_PKG = os.path.join(TESTS_DIR, "pipe-cq")
PIPE_RESOLVE_PARK_PKG = os.path.join(TESTS_DIR, "pipe-resolve-park")
GIVE_KEEP_MASK_PKG = os.path.join(TESTS_DIR, "give-keep-mask")
GIVE_FULL_RETURNS_PKG = os.path.join(TESTS_DIR, "give-full-returns")
CHILD_NARROW_PKG = os.path.join(TESTS_DIR, "child-narrow")

# Driver-as-service.
#
# `uartproto` is a library package rather than an image: the protocol is one
# declaration the driver, the client and both launchers compile against. It has
# no constant here because nothing builds it directly; Blade pulls it in as a
# path dependency.
#
# The driver is per chip and the client and launchers are arch-free: a driver
# names its device, a launcher moves a capability, and a capability has no
# datasheet.
#
# On riscv32 `svc-client` is linked at the second child base
# (`hal/riscv32/user/child2.ld`), and because `_region_rows` assigns
# destinations by index, a package linked there must be listed second in its
# case.
UART_SERVICE_PKG = os.path.join(TESTS_DIR, "uart-service")
UART_CANCEL_PKG = os.path.join(TESTS_DIR, "uart-cancel")
SVC_UART_NS16550_PKG = os.path.join(TESTS_DIR, "svc-uart-ns16550")
SVC_UART_PL011_PKG = os.path.join(TESTS_DIR, "svc-uart-pl011")
SVC_CLIENT_PKG = os.path.join(TESTS_DIR, "svc-client")
DELEGATE_3P_PKG = os.path.join(TESTS_DIR, "delegate-3p")
CHILD_FORWARD_PKG = os.path.join(TESTS_DIR, "child-forward")
CHILD_FAR_PKG = os.path.join(TESTS_DIR, "child-far")

# Clock and Timer objects. What an object answers and whether a wait parks are
# separate questions, so they get separate packages: a scheduling bug cannot
# hide behind an arithmetic one.
CLOCK_BASICS_PKG = os.path.join(TESTS_DIR, "clock-basics")
TIMER_ONESHOT_PKG = os.path.join(TESTS_DIR, "timer-oneshot")
TIMER_INTERVAL_PKG = os.path.join(TESTS_DIR, "timer-interval")
TIMER_DEADLOCK_PKG = os.path.join(TESTS_DIR, "timer-deadlock")
TIMER_BADCLOCK_PKG = os.path.join(TESTS_DIR, "timer-badclock")
TIMER_BADRECORD_PKG = os.path.join(TESTS_DIR, "timer-badrecord")

# The tier word.
TIER_WORD_PKG = os.path.join(TESTS_DIR, "tier-word")

# The shared stats region: the launcher, and the child that reads its own row
# out of the same page. The read-only proof is its own package because a
# program that dies cannot go on to assert anything.
STATS_REGION_PKG = os.path.join(TESTS_DIR, "stats-region")
STATS_READER_PKG = os.path.join(TESTS_DIR, "stats-reader")
STATS_REGION_RO_PKG = os.path.join(TESTS_DIR, "stats-region-ro")

# The package that chooses its own arena. Its `linker-script` is not one of the
# shared `hal/*/user/` scripts: it names a two-line file of its own that sets
# `ARENA_SIZE` and then INCLUDEs the shared one.
ARENA_SMALL_PKG = os.path.join(TESTS_DIR, "arena-small")

# A freestanding C child. `c-child` is a `main.c`, a `crt0.c` and a hand-rolled
# `syscall.c`, with no `sos` dependency, no `sosrt` and no arena; `c-hello` is a
# Saw launcher that spawns it and asserts its lines and exit code. A C package
# reaches Blade's sosimg emit through the same `[sos] native` line the HAL stubs
# use, so the harness needs no separate build leg for it. It does need a stub
# `src/main.saw`, which Blade's `find_source_file()` demands; a retain-marked
# backtrace table survives `--gc-sections` because of it (see that file's
# header).
C_HELLO_PKG = os.path.join(TESTS_DIR, "c-hello")
C_CHILD_PKG = os.path.join(TESTS_DIR, "c-child")

# A dying process disowns the shared objects charged to it. Each case has one
# child image beside a pool and a device window: `child-disown` plays both the
# charger and the probe.
CHARGE_DISOWN_PKG = os.path.join(TESTS_DIR, "charge-disown")
CHILD_DISOWN_PKG = os.path.join(TESTS_DIR, "child-disown")
CHARGE_REAP_PKG = os.path.join(TESTS_DIR, "charge-reap")
CHILD_KEEPER_PKG = os.path.join(TESTS_DIR, "child-keeper")

# What the harness types at the guest's serial port for the echo cases, and what
# it then expects to read back.
#
# Four bytes, none of them special. They avoid the emulator's escape character
# (a control byte) and appear in no other transcript, so finding them in the
# output means the driver put them there: nothing echoes them on the way in,
# since the guest's serial input is a pipe rather than a terminal.
ECHO_INPUT = "Zq7#"

# How long the harness waits before each byte it types.
#
# The delay is the test. Handed the whole string up front, the emulator buffers
# it before the guest's driver exists and every byte is already waiting when the
# driver looks, which proves the echo and not the interrupt path (a driver that
# simply polled would pass). Withholding each byte forces the driver to park,
# leaving the kernel with nothing runnable until a wake from outside the
# runnable set: one wake per byte.
#
# It costs `len(ECHO_INPUT) * this` per echo case, well inside the QEMU timeout,
# and a slower machine only makes the guest wait longer.
SERIAL_TYPE_DELAY_S = 0.15

QEMU_TIMEOUT_S = 10

# =============================================================================
# The architectures
# =============================================================================
#
# One entry per machine SOS targets (spec §5b). Everything that differs between
# them is here, in data: a second architecture is a table row and a directory.
#
# `hex_width` is how many digits the kernel's `write_hex` prints: one per nibble
# of a machine word, so eight on a 32-bit profile and sixteen on a 64-bit one.
# Expectations below are written with placeholders and formatted per
# architecture, so a case asserts the same fact on both and the widths follow
# the target.

# =============================================================================
# The protection tiers
# =============================================================================
#
# Two words, because the kernel advertises two: `Isolated` means the hardware
# refuses an access a process was not granted, `Flat` means it does not. They
# are the runner's own constants rather than read out of the kernel, because the
# harness decides which cases to run before it has booted anything; the
# `tier_word` case checks that the runner's claim and the kernel's answer agree.
#
# The word decides whether a case whose assertion is that the hardware denied
# something is meaningful on this profile. On the flat profile user mode is
# read/write/execute over the whole address space, so a process can touch any
# address, the kernel's memory and the test finisher included, and rights hold
# only for a process that reaches the kernel through syscalls. The checks on the
# syscall path (handles, rights, quotas and lifetimes) are identical on every
# tier. Only a case whose assertion is a hardware denial carries a tier; the
# rest run on every profile apart from the few that name their `arches`. The
# flat tier disclaims protecting memory, the kernel's included, from a process.
TIER_ISOLATED = "isolated"
TIER_FLAT = "flat"

ARCHES = [
    {
        "name": "riscv32",
        "triple": "riscv32-unknown-none-elf",
        "qemu": "qemu-system-riscv32",
        # PMP: an unmatched U-mode access faults whenever any entry is
        # implemented, which locks the kernel, the UART and the finisher away
        # from root by saying nothing about them.
        "tier": TIER_ISOLATED,
        # `-bios none`: no OpenSBI, the kernel IS the reset target.
        "qemu_args": ["-M", "virt", "-bios", "none"],
        # A triple names the architecture but not which optional extensions the
        # part has: without `+m` the Saw half is built for base rv32i and
        # formatting an integer emits `__divsi3` calls this link cannot satisfy.
        # The C half gets the same set through `-march`.
        "cc_args": ["-march=rv32imac_zicsr", "-mabi=ilp32"],
        "features": "+m,+a,+c",
        # This board's `sink.c` and the architectural half of its `hal` module
        # are the shared riscv32 ones, so both are named here rather than under
        # `hal/riscv32/`.
        "hal_native": os.path.join("riscv32-common", "kernel"),
        "hal_modules": [RV32_CORE_MODULE],
        "hal_asm": [os.path.join("riscv32-common", "kernel", "trap.S")],
        "hex_width": 8,
        "root_entry": 0x80200000,
        # Where a child process's memory is. The build assigns this destination
        # range: the kernel never knows it, the region table publishes it as an
        # ordinary free-RAM row, and root learns it as a capability. It is one
        # region above root's top, and the child linker script
        # (`hal/riscv32/user/child.ld`) is linked at exactly this base.
        "child_region_base": 0x80240000,
        # The line `hal.irq_raise_selftest_line()` raises. It is per-machine
        # because what a board can interrupt itself with is: this one has no
        # software trigger, so the HAL makes the console interrupt, on the line
        # this board wires it to.
        "selftest_line": 10,
        # The RAM pool a `"pool": True` case publishes: a free window above
        # every child region, so a case may have children and a pool at once
        # and neither moves. A runner constant mirroring a HAL fact: ordinary
        # RAM, past root's region (0x8024_0000) and the one child region above
        # it.
        #
        # The test packages know this number through a one-line C constant per
        # architecture (`tests/poolbase_<arch>.c`). Root's config says which
        # ordinal means what, and where a pool is belongs to the same class of
        # config (the uart-echo driver knows `UART_BASE` for the same reason).
        # Saw cannot name an address any other way, hence C.
        "pool_base": 0x80280000,
        "pool_len": 0x40000,
        # The device window a `"device": True` case publishes: the console
        # UART's page, mirroring `hal.DEVICE_GRANT_BASE` / `DEVICE_GRANT_LEN`.
        # The uart-echo cases obtain it rather than declaring it.
        "device_base": 0x10000000,
        "device_len": 0x1000,
    },
    {
        "name": "arm64",
        "triple": "aarch64-unknown-none-elf",
        "qemu": "qemu-system-aarch64",
        # Page tables: the kernel lives under TTBR1 and a process reaches
        # exactly what its own tables map (isolation by absence).
        "tier": TIER_ISOLATED,
        # `-cpu cortex-a53`: ubiquitous, EL1 well-exercised. `-semihosting` is
        # what makes SYS_EXIT carry a status code (see hal/arm64/kernel/sink.c
        # for why not PSCI). `gic-version=2` is pinned rather than defaulted:
        # the HAL programs a v2 controller, and a newer emulator changing its
        # default would swap the hardware under a kernel that cannot say so.
        "qemu_args": ["-M", "virt,gic-version=2", "-cpu", "cortex-a53",
                      "-semihosting"],
        # The base aarch64 triple already has everything this kernel uses, and
        # there is no ABI variant to select.
        "cc_args": [],
        "features": None,
        # There is one arm64 board, so its HAL is whole: its own `sink.c`, and
        # no shared module beside its `hal`.
        "hal_native": os.path.join("arm64", "kernel"),
        "hal_modules": [],
        "hal_asm": [],
        "hex_width": 16,
        # The one virtual base every image on this board is linked at,
        # mirroring `hal.USER_IMAGE_BASE` and `hal/arm64/user/user.ld`. It is
        # also root's physical destination: root is the one process whose two
        # addresses coincide.
        "root_entry": 0x40200000,
        # A child's destination frames: physical, and not an address any
        # program sees (the kernel translates, and a child links at
        # `root_entry`). One region above root's, as on riscv32. The build's
        # region table hands a launcher this range.
        #
        # The frames must be RAM the linear map covers (`map_source_ok`), while
        # the virtual range must land in the HAL's grant window
        # (`map_target_ok`).
        "child_region_base": 0x40240000,
        # This controller has a software trigger, so the selftest line is a
        # software-generated one and no device is involved.
        "selftest_line": 5,
        # The RAM pool, one region above the child region as on riscv32. Its
        # frames only have to be RAM the linear map covers (`map_source_ok`);
        # the grant window bounds the virtual range it can be mapped at
        # (`map_target_ok`).
        "pool_base": 0x40280000,
        "pool_len": 0x40000,
        # The console UART's page, mirroring `hal.DEVICE_GRANT_BASE` / `_LEN`.
        "device_base": 0x09000000,
        "device_len": 0x1000,
    },
    # =========================================================================
    # The flat build profile
    # =========================================================================
    #
    # The same board with the protection turned off and the tier word saying so.
    # Not a third architecture or board: the riscv32 `virt` machine, with the
    # same QEMU invocation, triple, toolchain, `boot.S`, linker scripts,
    # `tests/riscv32` payloads, and root and child images down to the byte. What
    # differs is one module, `hal/riscv32-flat/kernel/lib.saw`, whose `prot_*`
    # bodies are empty, whose PMP is opened once to permit everything, and whose
    # `prot_isolated()` answers `false`.
    #
    # It is last in this list deliberately. The architectures run in list
    # order and each prints a contiguous block, so appending here leaves the
    # riscv32 and arm64 sections where they were. The tier word and the case
    # sorting must cost the isolated profiles nothing, and a transcript diff
    # is how that is checked.
    #
    # Why a third profile rather than a build flag: the flat/isolated choice has
    # to change the values of constants that `kernel/core`'s `static_assert`s
    # and `load_domain`'s folded `if` read, and this toolchain has no
    # conditional compilation (no `--define`, no `cfg`; the only build-side
    # switches in this file are `--target-features` and `--module-path`). A
    # module swap is the mechanism the language gives, and the profile is a HAL,
    # so it is a HAL directory.
    {
        "name": "riscv32-flat",
        "tier": TIER_FLAT,
        # The same machine, so the same everything the machine decides.
        "triple": "riscv32-unknown-none-elf",
        "qemu": "qemu-system-riscv32",
        "qemu_args": ["-M", "virt", "-bios", "none"],
        "cc_args": ["-march=rv32imac_zicsr", "-mabi=ilp32"],
        "features": "+m,+a,+c",
        # `hal=` is this profile's own module; `rv32core` and `rv32virt` are
        # the arch half and the board half it re-exports from. `hal_native` and
        # `hal_asm` are the shared riscv32 ones: the trap entry and the CSR
        # sinks are facts about the ISA, which this profile does not touch.
        "hal_native": os.path.join("riscv32-common", "kernel"),
        "hal_modules": [RV32_CORE_MODULE, RV32_VIRT_MODULE],
        "hal_asm": [os.path.join("riscv32-common", "kernel", "trap.S")],
        # `boot.S` and `virt.ld` come from the board, because they are the
        # board's: where the stack is, where the payload section lands, where
        # RAM begins. There is deliberately no copy of either under
        # `hal/riscv32-flat/`.
        "hal_board": os.path.join("riscv32", "kernel"),
        # The hand-assembled payloads under `tests/riscv32/` are riscv32
        # instructions, so they are this profile's too.
        "tests_arch": "riscv32",
        # Must not be shared: two profiles on one triple would write their
        # kernel objects into the same `.build/<triple>/sos/`, and the second
        # would silently relink the first's. The esp32c3 section below avoids
        # the same hazard the same way.
        "build_tag": "riscv32-unknown-none-elf-flat",
        # The banner names the architecture, not the profile: the kernel prints
        # "riscv32 (QEMU virt, flat profile)" and this is matched as a prefix
        # of it, so a case asserts the same fact on every profile.
        "banner_arch": "riscv32",
        "hex_width": 8,
        "root_entry": 0x80200000,
        "child_region_base": 0x80240000,
        "selftest_line": 10,
        "pool_base": 0x80280000,
        "pool_len": 0x40000,
        "device_base": 0x10000000,
        "device_len": 0x1000,
    },
]

# How big a child's destination region is.
#
# The same size root gets, for the same reasons: its image plus the kernel's
# stack grant at the top (`hal.ROOT_STACK_LEN`, the one stack length). Keeping
# the two the same size lets one number here and one `ORIGIN` per child linker
# script express the whole layout.
CHILD_REGION_LEN = 0x40000

# The boot region table's wire format (version 2), frozen: an 8-byte header
# (magic, version u16, count u8, reserved u8) then `count` rows of {base: u64,
# len: u64, kind: u8, reserved x 7}, all little-endian. The header's size puts
# the first row on an 8-byte boundary and the row's padding keeps the next one
# there, which lets the kernel overlay a struct on the section instead of
# assembling bytes. Kept in step with `kernel/core/process.saw`'s
# `RegionTableHeader` / `RegionRow` / `RegionKind` and the two `static_assert`s
# beside them.
#
# The kernel accepts exactly version 2, so this constant and that file change
# together. An image with no table at all is unaffected: the kernel's `len == 0`
# short-circuit runs ahead of the header read.
REGION_TABLE_MAGIC = 0x4E475253          # 'S','R','G','N' read little-endian
REGION_TABLE_VERSION = 2

# The kind column's values, mirroring `kernel/core/process.saw`'s `RegionKind`.
# A Ram row mints a Memory and a Device row mints an IoMemory: kind, not a flag,
# at the one place a window enters the system.
REGION_KIND_RAM = 0
REGION_KIND_DEVICE = 1


def arch_dirs(arch):
    """The per-profile directories a build reaches into.

    `hal_kernel` is where the `hal` module lives. `hal_board` is where the
    board's native half lives (`boot.S` and the linker script); it is the same
    directory for every profile that is a board in its own right.
    `hal_native` is where this architecture's `sink.c` lives: the arm64
    directory, or the shared `riscv32-common` one for riscv32.

    The three `.get` defaults are what make a variant profile possible. A
    profile that is a variant of an existing board (riscv32-flat) shares that
    board's `boot.S`, its linker script and its `tests/` payloads, and differs
    only in the `hal` module it compiles and the `.build/` directory it
    writes into.

    `build` is keyed on `build_tag` rather than on the triple: two profiles
    can share a target triple, and a shared `.build/<triple>/` would let one
    silently relink the other's objects.
    """
    return {
        "hal_kernel": os.path.join(HAL_DIR, arch["name"], "kernel"),
        "hal_board": os.path.join(HAL_DIR,
                                  arch.get("hal_board",
                                           os.path.join(arch["name"], "kernel"))),
        "hal_native": os.path.join(HAL_DIR, arch["hal_native"]),
        "tests": os.path.join(TESTS_DIR, arch.get("tests_arch", arch["name"])),
        "build": os.path.join(REPO_ROOT, ".build",
                              arch.get("build_tag", arch["triple"]), "sos"),
    }


def expectations(arch):
    """The per-architecture substitutions a case's expected output is written in.

    A case says `entry={entry}`, not `entry=0x80200000`, because the fact
    under test is "the entry the image declared came through intact" and the
    digits are the target's word width.
    """
    width = arch["hex_width"]
    return {
        # `banner_arch` is the architecture's name: the profile's own name for
        # a profile that is a board in its own right, the board's for a variant
        # of one. The kernel prints `hal.arch_name()` and this is matched as a
        # prefix of it, so riscv32-flat's banner ("SOS M1: kernel up on riscv32
        # (QEMU virt, flat profile)") is asserted by the same string the
        # isolated profile asserts.
        "banner": f"SOS M1: kernel up on {arch.get('banner_arch', arch['name'])}",
        # The tier word as a case writes it: `tier={tier}` gives `Isolated` on
        # the two protected profiles and `Flat` on the flat one, for the same
        # reason as `{entry}` and `{banner}`: the fact under test is that the
        # platform advertised what it can actually deny, and the word is the
        # target's.
        "tier": "Isolated" if arch["tier"] == TIER_ISOLATED else "Flat",
        "entry": f"0x{arch['root_entry']:0{width}x}",
        "zero": f"0x{0:0{width}x}",
        "one": f"0x{1:0{width}x}",
        "two": f"0x{2:0{width}x}",
        "three": f"0x{3:0{width}x}",
        "four": f"0x{4:0{width}x}",
        "five": f"0x{5:0{width}x}",
        "six": f"0x{6:0{width}x}",
        "seven": f"0x{7:0{width}x}",
        # The getters mint, so handle counts exceed seven. `ten` is
        # thread-basics, where three threads each derive their own Process and
        # Thread handles.
        "eight": f"0x{8:0{width}x}",
        "nine": f"0x{9:0{width}x}",
        "ten": f"0x{10:0{width}x}",
        "prio": f"0x{0x01010100:0{width}x}",
        "irq_line": f"0x{arch['selftest_line']:0{width}x}",
        # The byte `child-touch` writes into the page its launcher mapped and
        # reads back; it is also its exit code, so `map_into_child` can assert
        # the round trip through the kernel's exit line as well as through the
        # child's own console line and root's §8 status word. Like the marks
        # below, it appears nowhere else in any transcript.
        "touch_mark": f"0x{0x3C:0{width}x}",
        # The exit codes the fused-path cases assert. Each is chosen to appear
        # nowhere else in any transcript, so that no other ending can produce
        # it.
        #
        #   pingpong_mark  every round trip's reply matched its own request
        #   caller_mark    BOTH parked fused calls came back `PeerClosed`
        #   orphan_mark    the thread whose call was answered ended the process
        "pingpong_mark": f"0x{0x21:0{width}x}",
        "caller_mark": f"0x{0x33:0{width}x}",
        "orphan_mark": f"0x{0x3D:0{width}x}",
        # The thread whose resolve was answered ended the process, leaving its
        # sibling parked in one.
        "resolve_orphan_mark": f"0x{0x47:0{width}x}",
        # The byte root writes into the shared page, also `child-share`'s exit
        # code, so `share_double_map` asserts the first direction of the round
        # trip through the kernel's exit line as well as through root's §8
        # status word. Its partner (0x5A, the byte the child writes) needs no
        # placeholder: it comes back as a decimal on a console line.
        "share_mark": f"0x{0xA5:0{width}x}",
        # `child-disown`'s charger exit code ("its objects went"), asserted
        # through the kernel's exit line in `charge_disown`.
        "charger_mark": f"0x{0x2C:0{width}x}",
        # `child-keeper`'s "everything is held" exit code, asserted the same
        # way in `charge_reap`.
        "keeper_mark": f"0x{0x2D:0{width}x}",
    }


# =============================================================================
# The seam check
# =============================================================================

ARCH_FREE_DIRS = [
    os.path.join(REPO_ROOT, "kernel", "core"),
    os.path.join(REPO_ROOT, "kernel", "main.saw"),
]
ARCH_WORDS = [
    "riscv", "rv32", "rv64", "aarch64", "arm64", "armv8",
    "mcause", "mepc", "mtval", "mstatus", "mscratch", "mtvec", "ecall", "pmp",
    "sifive", "ns16550", "csr", "m-mode", "u-mode",
    "esr_el", "elr_el", "far_el", "vbar", "ttbr", "sctlr", "tcr_el", "mair",
    "pl011", "psci", "semihost", " svc ", "el0", "el1",
    # The interrupt controllers and the timers are spelled to avoid English
    # where they can (`gic` alone would match "magic", which the loader's own
    # diagnostics say), but `plic` still occurs inside English words;
    # `_is_english_embedding` below is what keeps those from firing.
    "plic", "clint", "gicd", "gicc", "gicv", "sgir",
    "mtime", "cntp", "cntfrq", "sstc",
]

# A token buried in a longer English word is not a leak. `plic` is inside
# "duplicate", "explicit", "implicit", "complicated" and "replica", and the
# check must not dictate the kernel's vocabulary, since it exists to police the
# kernel's dependencies.
#
# A hit is suppressed only when the token has a letter on both sides. That is
# weaker than a word boundary because the real usages are prefixes and suffixes
# of longer identifiers (`mtimecmp`, `csrw`, `PLIC_BASE`, `gicd_ctlr`,
# `cntfrq_el0`), which a boundary on both sides would miss. An arch token in
# the middle of an alphabetic word is English; at either end it is an
# identifier.
def _is_english_embedding(line, token, at):
    before = line[at - 1] if at > 0 else ""
    after_index = at + len(token)
    after = line[after_index] if after_index < len(line) else ""
    return before.isalpha() and after.isalpha()

# ANSI colors (disabled when not a TTY).
_TTY = sys.stdout.isatty()
GREEN = "\033[92m" if _TTY else ""
RED = "\033[91m" if _TTY else ""
BOLD = "\033[1m" if _TTY else ""
RESET = "\033[0m" if _TTY else ""
CHECK = f"{GREEN}✓{RESET}"
CROSS = f"{RED}✗{RESET}"

# =============================================================================
# The cases
# =============================================================================
#
# Each test: the entry source, an optional payload assembled from the running
# architecture's own `tests/<arch>/` directory, one or more expected console
# substrings (or None), and whether the emulator should exit cleanly (True →
# status 0) or fail (False). Expected strings are `str.format`ed with
# `expectations(arch)`.

TEST_CASES = [
    {
        # The kernel exists to hand control to root. Built with no image
        # appended it must say so and fail, never exit quietly as if the
        # system had run.
        "name": "no_root_image",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "expect_out": ["{banner}", "bad root image: no root image appended"],
        "expect_clean_exit": False,
    },
    {
        "name": "trap_fault",
        "src": os.path.join(TESTS_DIR, "trap.saw"),
        "expect_out": None,             # a fault produces no banner
        "expect_clean_exit": False,     # the HAL's kernel-bug path
    },
    {
        "name": "panic_seam",
        "src": os.path.join(TESTS_DIR, "panic.saw"),
        "expect_out": "SOS M0: deliberate panic",
        "expect_clean_exit": False,     # __saw_rt_panic → console + abort
    },
    {
        # The panic-recursion pin. The message comes from the compiler's bounds
        # check, so the reporter is entered from the trap path, the one that
        # would recurse if the writer under it could panic. Asserting the
        # prefix and the reason catches a garbled report; asserting a non-zero
        # exit catches a hung one. The location is the trapping expression's
        # own, which for an indexed accessor is inside std, so this asserts
        # `vector.saw` and not the test's file. Three independent pieces
        # (prefix, location, reason) all arriving means nothing re-entered the
        # writer mid-report.
        "name": "panic_from_check",
        "src": os.path.join(TESTS_DIR, "panic_from_check.saw"),
        "expect_out": ["panic at ", "vector.saw:",
                       "Vector.[]: index out of range"],
        "expect_clean_exit": False,
    },
    {
        # The in-process task dump, freestanding. The kernel walks its own task
        # slots through the in-binary backtrace table and writes each parked
        # task's logical stack to the serial port. Both halves are asserted, in
        # order: the explicit `dump_tasks()` with its two-frame nest, then the
        # panic line, then the dump the panic path emits by itself. A kernel has
        # no debugger to attach and no core to open, so this is its way to show
        # where its tasks are.
        "name": "task_dump",
        "src": os.path.join(TESTS_DIR, "taskdump.saw"),
        "expect_out": ["saw tasks: 2 live (unsynchronized snapshot)",
                       "at taskdump.saw:91 in knap",
                       "at taskdump.saw:96 in ksleeper",
                       "panic at taskdump.saw:112: SOS task dump: deliberate panic",
                       "saw tasks: 1 live (as-of panic, unsynchronized)",
                       "at taskdump.saw:91 in knap",
                       "at taskdump.saw:96 in ksleeper"],
        "expect_clean_exit": False,
    },
    {
        # The dump path on every architecture: the table is in the image, the
        # walker links and runs freestanding, and a panic with no live task
        # still prints exactly its own message and nothing else.
        "name": "task_dump_empty",
        "src": os.path.join(TESTS_DIR, "taskdump_empty.saw"),
        "expect_out": ["saw tasks: none live",
                       "panic at taskdump_empty.saw:38: "
                       "SOS task dump: no tasks here"],
        "expect_clean_exit": False,
    },
    # --- the privilege split, without any image format ----------------------
    {
        "name": "umode_syscall",
        "src": os.path.join(TESTS_DIR, "umode.saw"),
        "asm": "payload_ok.S",
        # "SOS-U" is written one character at a time through `debug_print`, so
        # seeing it at all proves the syscall round trip resumed correctly.
        "expect_out": ["SOS M1: entering U-mode", "SOS-U"],
        "expect_clean_exit": True,      # shutdown(0)
    },
    {
        "name": "umode_access_fault",
        # Isolated-only. The payload stores into the kernel's own .text, which
        # it was never granted, and the assertion is the fault. A flat platform
        # permits the store, the payload runs on, and the expected line is
        # missing rather than wrong.
        "tier": TIER_ISOLATED,
        "tier_reason": "a U-mode store into kernel .text must FAULT; a flat "
                       "platform permits it and the line never appears",
        "src": os.path.join(TESTS_DIR, "umode.saw"),
        "asm": "payload_fault.S",
        # Both HALs report the same name for the same event, which is why one
        # string serves two architectures.
        "expect_out": "fault store-access-fault",
        "expect_clean_exit": False,
    },
    {
        # A caller's mistake is a fault, not a status. The payload makes a good
        # call (the '!' proves the round trip resumed) and then an op the
        # System object does not have, which terminates it. The transcript is
        # those events in order, and the exit status is the kernel's own
        # `ExitCode.ProcessFault` rather than anything the process chose. The
        # teardown line says the kernel stayed up to report.
        "name": "umode_bad_calls",
        "src": os.path.join(TESTS_DIR, "umode.saw"),
        "asm": "payload_badcall.S",
        "expect_out": ["SOS M1: entering U-mode", "!",
                       "SOS: process fault: bad op",
                       "SOS: process teardown handles={three} threads={one}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    # --- interrupts -----------------------------------------------------------
    # Three claims, one per case, each read from the order of the transcript
    # rather than any single line: a tick lands in the arch-free hook once user
    # mode is running; a tick that comes due in the kernel is not taken there;
    # and a device line goes round the interrupt controller's claim/complete
    # cycle. All three run the same spinning payload, which shuts the machine
    # down cleanly when done, so a kernel that wedged in its handler shows up
    # as a timeout, not a pass.
    {
        "name": "timer_tick",
        "src": os.path.join(TESTS_DIR, "timer.saw"),
        "asm": "payload_spin.S",
        # The tick count comes from the kernel's counter and the address from
        # the interrupted frame, so both halves of "a tick was taken from user
        # mode" are in the line. The address itself is the payload's and is not
        # asserted — what matters is that the kernel had already left.
        "expect_out": ["SOS M2: timer armed",
                       "SOS M2: entering U-mode",
                       "SOS: timer tick {one} at ",
                       "SOS: timer tick {two} at "],
        "expect_clean_exit": True,
    },
    {
        "name": "timer_masked_in_kernel",
        "src": os.path.join(TESTS_DIR, "timer_mask.saw"),
        "asm": "payload_spin.S",
        # The middle line is the whole case: the kernel spun until the timer
        # was due, and the tick counter is still zero, so the interrupt was
        # pending and untaken while the kernel ran. The tick arrives after the
        # entry to user mode, and ordered matching is what makes "after" an
        # assertion.
        "expect_out": ["SOS M2: kernel section begin",
                       "SOS M2: kernel section end, timer expired, "
                       "ticks taken={zero}",
                       "SOS M2: entering U-mode",
                       "SOS: timer tick {one} at "],
        "expect_clean_exit": True,
    },
    {
        "name": "external_irq",
        "src": os.path.join(TESTS_DIR, "extirq.saw"),
        "asm": "payload_spin.S",
        # The line is raised in the kernel and serviced in user mode, which is
        # the same masking claim the case above makes about the timer — and
        # the line number crossing from the raise to the hook is what says the
        # controller's claim gave back the line that was raised.
        "expect_out": ["SOS M2: raised external irq {irq_line}",
                       "SOS M2: entering U-mode",
                       "SOS: external irq {irq_line}"],
        "expect_clean_exit": True,
    },
    # --- kernel interruptibility -------------------------------------------
    # The cases above show that an interrupt that comes due while the kernel
    # runs is not taken there. The ones below invert those assertions rather
    # than replacing them: the kernel still takes no trap in kernel mode, but it
    # asks at named points inside long operations, so the same interrupt is
    # serviced there.
    #
    # Both run the shipped mover (`kcore.preempt.long_zero`, which the loader
    # places a root image with) over a kernel scratch buffer. A test-only loop
    # with a point in it would prove that a point can work, not that the one
    # the kernel ships does.
    {
        # timer_mask's mirror, over the comparator. The exit condition is the
        # proof: the section runs until the tick counter moves, and in kernel
        # mode nothing but a preemption point can move it (a hardware delivery
        # here is each HAL's kernel-bug path). A broken poll is a timeout, not
        # a wrong number, and the ticks-taken line asserts the count as well as
        # the order.
        #
        # `at {one}` is `PREEMPT_PC`, the sentinel a tick taken at a point
        # reports (the idle poll's is zero, and a real interrupted PC is even
        # on every profile). Asserting it says which of the three deliveries
        # ran.
        "name": "preempt_tick",
        "src": os.path.join(TESTS_DIR, "preempt_tick.saw"),
        "asm": "payload_spin.S",
        "expect_out": ["SOS M3: kernel section begin",
                       "SOS: timer tick {one} at {one}",
                       "SOS M3: kernel section end, ticks taken={one}",
                       "SOS M3: entering U-mode"],
        "expect_clean_exit": True,
    },
    {
        # extirq's mirror, over the interrupt controller. The order is the
        # inversion: that case asserts the report lands after the entry to user
        # mode, this one before it, from inside the kernel section. Ordered
        # matching is what makes "before" an assertion.
        #
        # It arms no timer, which is why both cases exist: on one profile the
        # comparator is not a controller source at all, so "the point delivers
        # what the controller has" and "the point delivers what the comparator
        # has" are two claims about two pieces of hardware.
        "name": "preempt_extirq",
        "src": os.path.join(TESTS_DIR, "preempt_extirq.saw"),
        "asm": "payload_spin.S",
        "expect_out": ["SOS M3: raised external irq {irq_line}",
                       "SOS M3: kernel section begin",
                       "SOS: external irq {irq_line}",
                       "SOS M3: kernel section end",
                       "SOS M3: entering U-mode"],
        "expect_clean_exit": True,
    },
    # --- the sosimg format and the kernel's loader -------------------------
    # These images are assembled by hand (tests/<arch>/payload_*.S), so they
    # pin the format independently of Blade's emitter: two producers, one
    # loader.
    {
        "name": "root_image_load",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "asm": "payload_sosimg.S",
        "expect_out": ["{banner}",
                       "root image ok segments={one} entry={entry} prio={prio}",
                       "SOS-R"],
        "expect_clean_exit": True,
    },
    {
        "name": "root_image_bad_magic",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "asm": "payload_badmagic.S",
        "expect_out": "bad root image: bad magic",
        "expect_clean_exit": False,
    },
    {
        # A version bump is a refusal boundary. The payload is a real v2 image
        # (correct magic, right arch, sane permissions) and a v3 loader must
        # stop at the version rather than guess at a record whose address field
        # is a different width in a different place.
        "name": "root_image_bad_version",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "asm": "payload_badversion.S",
        "expect_out": "bad root image: unsupported version",
        "expect_clean_exit": False,
    },
    {
        # One format, every profile. An image whose header is correct in every
        # other way but says it was built for the other machine is refused on
        # the tag, before a byte is copied.
        "name": "root_image_wrong_arch",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "asm": "payload_wrongarch.S",
        "expect_out": "bad root image: image is for another architecture",
        "expect_clean_exit": False,
    },
    {
        # The check that matters most: an image may not aim a segment at the
        # kernel. Rejected before a byte is copied.
        "name": "root_image_bad_segment",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "asm": "payload_badsegment.S",
        "expect_out": "bad root image: segment loads below the root region",
        "expect_clean_exit": False,
    },
    # --- the real two-image boot --------------------------------------------
    # Kernel and root are separate builds (separate linker scripts and load
    # addresses, root built by Blade from its own package manifest) and meet
    # only as an appended blob the kernel parses. The same root sources build
    # for every profile: only the manifest's `[sos.<triple>]` section differs.
    {
        "name": "root_server_boot",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": ROOT_PKG,
        "expect_out": ["{banner}",
                       "root image ok segments={two}",
                       "prio={prio}",
                       # The typed Saw altitude: a method on a handle.
                       "SOS root: hello from U-mode via a System op",
                       # The runtime seam altitude, end to end: `print` ->
                       # `__saw_rt_write` (sosrt) -> `sos_rt_write` (sysapi) ->
                       # `sos_system_debug_print` -> `sos_syscall1` -> the
                       # trap instruction (`ecall` on riscv32, `svc #0` on
                       # arm64). Every hop but the last is Saw. Also
                       # formatting with no allocator present.
                       "SOS root: boot handle 1"],
        "expect_clean_exit": True,
    },
    # --- Thread and Process objects + the scheduler --------------------------
    # Three claims, one per case. The root servers are real Blade packages that
    # reach the kernel through the `sos` module, so what is under test is the
    # object surface a process actually has, not an op number written into an
    # assembler payload.
    {
        # Create two threads, start them, and join them for their own exit
        # values, with cooperative alternation in between. The kernel here arms
        # no timer, so nothing can take the processor away and the
        # eight-character run is exactly eight `yield` calls in a row. That one
        # substring is the strongest form the claim has: any missed switch, any
        # extra one, and it is not `ABABABAB`.
        "name": "thread_basics",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": THREAD_BASICS_PKG,
        "expect_out": ["{banner}",
                       "SOS threads: two workers, status word 0",
                       "ABABABAB",
                       "SOS threads: joined a=11 b=22",
                       # Teardown handle counts. Ten: the three boot handles,
                       # the two Threads this process created, and five
                       # derivations (each of the three threads asks the
                       # System object which Process it is, and the two
                       # workers also ask the Process which Thread they are).
                       # Nothing is released before the exit, so every one is
                       # still bound at teardown. The two trailing counts say a
                       # process that made no Event and no Waiter reports
                       # none, the null row of the claim the Event cases below
                       # make with real objects.
                       "SOS: process teardown handles={ten} threads={three} "
                       "events={zero} waiters={zero}"],
        "expect_clean_exit": True,
    },
    {
        # Preemption. The same shape as thread_basics with the yields removed
        # and the timer armed: two workers that print and then spin, making no
        # call that could give the processor up. Every alternation in the
        # transcript is the scheduler taking it away at a tick, which a
        # cooperative test cannot show.
        #
        # The root's own banner is deliberately not asserted: it is written a
        # byte per syscall while the kernel is still narrating its first
        # ticks, so the two interleave mid-word on the console. That is
        # harmless (a shared serial port with no locking) but not something a
        # substring can match. The tick narration stops after four, so
        # everything below is written in the quiet that follows.
        "name": "thread_preempt",
        "src": os.path.join(TESTS_DIR, "threads_timer.saw"),
        "root_pkg": THREAD_PREEMPT_PKG,
        #
        # The alternation is asserted as direction changes, not as a fixed
        # sequence, which is not a weakening. Which worker runs first depends
        # on where the first tick lands relative to the two `start` calls, so a
        # sequence starting `A` fails half the time on a claim it was never
        # making. `AB` then `BA` then `AB` says the processor crossed between
        # the two threads at least three times, whoever went first; a run with
        # no preemption at all reads `AAAAAAAABBBBBBBB`, which has one `AB` and
        # no `BA` after it.
        #
        # The interrupt column is printed and not asserted. This case's point is
        # that ticks land in user mode, so the column has something to show
        # here, but a tick lands where the host puts it, so asserting the number
        # would be asserting the weather. The tick count is unasserted for the
        # same reason.
        #
        # The three claims are adjacency claims, and two matcher options make
        # them hold on a real run:
        #
        #   1. `strip_kernel_lines`: the kernel's own `SOS: timer tick …` lines
        #      go into the same serial stream the workers write letters into,
        #      and one can land between a `B` and an `A`, leaving no adjacent
        #      `BA`. The match runs against the letters-only projection
        #      instead. Nothing this case expects begins `SOS: `, so the
        #      projection drops the narration and keeps every claim.
        #   2. `overlapping_matches`: the three crossings can be consecutive
        #      (`…AB`, `BA`, `AB`), and a matcher that advances past each whole
        #      match would find `AB`, step over the `BA`, and weaken the claim
        #      to crossings arranged so as not to overlap. Advancing by one
        #      character is what "three direction changes" means when written
        #      as adjacent pairs.
        #
        # The no-preemption control `AAAAAAAABBBBBBBB` still fails, which is
        # the claim's other end.
        "strip_kernel_lines": True,
        "overlapping_matches": True,
        "expect_out": ["SOS M2: preemptive kernel up on",
                       "AB", "BA", "AB",
                       "SOS preempt: joined a=33 b=44"],
        "expect_clean_exit": True,
    },
    {
        # A caller's mistake on a handle the process never held ends the
        # process; the kernel reports the reason, reports the teardown, and
        # stops the machine with its own exit code.
        #
        # It is a payload rather than a root server: the `sos` module's typed
        # layer has no way to build a `Thread` from a word, so a handle the
        # process was never given is not a thing a Saw program can spell. The
        # kernel's validation is therefore tested where raw handles legitimately
        # live, beside `umode_bad_calls`.
        "name": "umode_bad_handle",
        "src": os.path.join(TESTS_DIR, "umode.saw"),
        "asm": "payload_badhandle.S",
        "expect_out": ["SOS M1: entering U-mode",
                       "SOS: process fault: bad handle",
                       "SOS: process teardown handles={three} threads={one}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    # --- the Event and Waiter objects ---------------------------------------
    # Three claims. What an Event accumulates and what a Waiter reports is one
    # question, and whether a wait parks is another; testing them together
    # would let a scheduling bug hide behind an accumulation bug and the other
    # way round.
    {
        # Level-triggered attach (§2.2), OR and saturating-sum accumulation
        # (§2.4), and the key identifying which of several attachments became
        # ready. One thread, no timer: every wait here answers immediately, and
        # a wait that did not would park the only thread in the system, which
        # the kernel reports as the deadlock it is, so a regression fails in
        # microseconds rather than at the timeout.
        "name": "event_basics",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": EVENT_BASICS_PKG,
        "expect_out": ["{banner}",
                       # The signal came before the wait and was not lost.
                       # `at-wait` is read out of the record the kernel copied
                       # into the process's memory, and it is the accumulated
                       # word rather than a readiness flag: 4, then 8, then
                       # 1|2|1, then a count of five.
                       #
                       # `after` is the `receive` that follows each wait, and
                       # it reads zero every time: the delivery took the word.
                       # The `detached` line below is the same call answering
                       # a real number when no wait took it first, which says
                       # these zeroes are a take rather than a broken drain.
                       "SOS events: signal-then-wait key=11 at-wait=4 after=0",
                       # A second attachment on the same Waiter, told apart by
                       # its key alone.
                       "SOS events: second key=22 at-wait=8 after=0",
                       # 1 | 2 | 1 is 3, which is what a flag set answers and a
                       # counter does not.
                       "SOS events: or key=11 at-wait=3 after=0",
                       # Removed from the wait set, still an Event.
                       "SOS events: detached word=8",
                       # Five signals of one, counted; then two of the largest
                       # word there is, which saturate instead of wrapping back
                       # through zero, the value that means "not ready".
                       "SOS events: counting key=33 at-wait=5 after=0 "
                       "saturated=1",
                       "SOS events: done",
                       # Teardown reports the event and waiter counts: three
                       # events and one waiter, beside eight handles and one
                       # thread. Teardown handle counts include the Process
                       # handle that `process_self()` mints, in cases that call
                       # it.
                       "SOS: process teardown handles={eight} threads={one} "
                       "events={three} waiters={one}"],
        "expect_clean_exit": True,
    },
    {
        # The park and the wake, read from the order: the middle line is
        # written by a thread that could only have run because the first one
        # blocked, since this kernel arms no timer and `start` does not switch.
        # The line after it carries the record back into the woken thread's own
        # stack buffer, written by the signalling thread through the copy-out
        # funnel: the block-on-wait substrate answering a syscall it did not
        # answer when the call was made.
        "name": "event_wake",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": EVENT_WAKE_PKG,
        "expect_out": ["{banner}",
                       "SOS wake: worker started, parking",
                       "SOS wake: worker signalling",
                       # One plus four is five, and that arithmetic is the
                       # third claim. The worker signals five times; the first
                       # wakes the parked thread and the delivery takes that 1,
                       # so the other four accumulate from zero and the
                       # `receive` after the wake reads 4. Nothing lost,
                       # nothing counted twice.
                       "SOS wake: woke key=77 at-wait=1 after=4",
                       "SOS wake: joined worker=99"],
        "expect_clean_exit": True,
    },
    {
        # Attaching something that is not a waitable. A payload rather than a
        # root server, for the reason `umode_bad_handle` is one: `Waiter.add`
        # takes an `&Event` and the typed layer has no way to build one from a
        # word. The teardown line is the second half: the Waiter the process
        # did make goes back to the kernel's slab.
        "name": "umode_not_waitable",
        "src": os.path.join(TESTS_DIR, "umode.saw"),
        "asm": "payload_notwaitable.S",
        "expect_out": ["SOS M1: entering U-mode",
                       "SOS: process fault: not a waitable",
                       "SOS: process teardown handles={four} threads={one} "
                       "events={zero} waiters={one}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # Keys are unique per Waiter. Two Events, one key, and the second
        # `add` terminates the process: the invariant that `remove(key)` and
        # the wait record both rest on.
        #
        # A root server rather than a payload, and the only fault case in this
        # group that can be one: the typed layer makes the others unspellable
        # (no handle you were not given, no buffer you name), while a duplicate
        # key is two real Events and two of the caller's own words, which no
        # type can catch. So the check is the kernel's, and the test lives
        # where a real process would hit it.
        "name": "event_dupkey",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": EVENT_DUPKEY_PKG,
        "expect_out": ["{banner}",
                       "SOS dupkey: first attach ok",
                       "SOS: process fault: an attachment already uses that key",
                       # Seven handles: the three it was given, the Process
                       # handle `process_self()` minted, a waiter and two
                       # events. Both events and the waiter still go back to
                       # their slabs.
                       "SOS: process teardown handles={seven} threads={one} "
                       "events={two} waiters={one}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    # The copy-out funnel's two rejection rows. `Waiter.Wait` answers by
    # writing a record into memory the caller supplies, and the funnel that
    # validates the destination is the only door. One case per row, because a
    # funnel with a matrix owes its tests row by row.
    #
    # Both are payloads for the reason `umode_bad_handle` is one: the typed
    # `Waiter.wait` supplies its own buffer out of its frame and never lets a
    # caller name one, so neither mistake is a thing a Saw process can spell.
    {
        # Row 1: outside the process's writable memory. This payload has no
        # writable grant at all (umode.saw gives it its own bytes read+execute),
        # so every address is outside its window and zero says so plainly.
        "name": "umode_bad_wait_buffer",
        "src": os.path.join(TESTS_DIR, "umode.saw"),
        "asm": "payload_badbuffer.S",
        "expect_out": ["SOS M1: entering U-mode",
                       "SOS: process fault: buffer is outside the process's "
                       "memory",
                       "SOS: process teardown handles={four} threads={one} "
                       "events={zero} waiters={one}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # Row 2: not word-aligned. The kernel writes machine words, so this is
        # refused before the range is even looked at; the cases are told
        # apart by that order, since this payload's address would fail the range
        # check too.
        "name": "umode_misaligned_wait_buffer",
        "src": os.path.join(TESTS_DIR, "umode.saw"),
        "asm": "payload_misalignbuf.S",
        "expect_out": ["SOS M1: entering U-mode",
                       "SOS: process fault: buffer is not word-aligned",
                       "SOS: process teardown handles={four} threads={one} "
                       "events={zero} waiters={one}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    # --- the Interrupt object + the userspace UART echo ----------------------
    # The harness types four bytes at the guest's serial port and asserts they
    # come back out of it after the kernel's handover marker. For that to
    # happen: the driver's device window was mapped (or its first register
    # read faults), the line was bound and unmasked (or nothing ever fires),
    # the kernel's idle path noticed an interrupt with no thread runnable (or
    # the kernel reports a deadlock in microseconds), the wait answer carried
    # the right key and line, the driver reached the receiver through the
    # window, and the ack unmasked so the next byte could arrive. A missing
    # byte leaves the driver parked and the case fails on the emulator's
    # timeout rather than quietly.
    #
    # One case per device. A driver names its device, so each profile gets a
    # package named for the chip; the line number in the last line is the
    # board's, and is asserted because it came back through the wait record's
    # payload rather than from the program's own constant.
    #
    # The device window is a device row of the build's region table, minted as
    # an `IoMemory`, drained from the boot set and mapped into the driver by
    # the driver (spec §2.5). So the transcript shows two segments (code and
    # data), one boot region, and the driver saying it obtained the window.
    #
    # These two stay alongside the child versions (`child_echo_ns16550` /
    # `child_echo_pl011` at the end of this file, launched by `driver-child`):
    # the same driver body running as root and as a child, in two transcripts,
    # shows that the difference is the launcher rather than the driver.
    {
        "name": "uart_echo_ns16550",
        # Also `riscv32-flat`, which is the same machine. An `arches` list
        # names the profiles a case applies to, and nothing about why each of
        # these cases is riscv32-only (a per-device driver package; a
        # `PROT_GRAIN` arithmetic) distinguishes the two riscv32 profiles: they
        # share the device, the grain and the triple. Naming both keeps the
        # variant from silently running fewer cases than the board it varies.
        "arches": ["riscv32", "riscv32-flat"],
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": UART_ECHO_NS16550_PKG,
        "device": True,
        "stdin": ECHO_INPUT,
        "expect_out": ["{banner}",
                       # Two segments: code and data. The device window is a
                       # region row, not a segment.
                       "root image ok segments={two}",
                       # One boot region: the device row. The kernel minted
                       # an IoMemory for it and queued the record the driver
                       # drains.
                       "SOS: boot regions={one}",
                       "SOS: console handover",
                       # The obtained path, said out loud: the driver drained an
                       # IoMemory and installed it in itself.
                       "SOS echo: window mapped",
                       "SOS echo: driver up",
                       ECHO_INPUT,
                       "SOS echo: done 4 bytes on line 10"],
        "expect_clean_exit": True,
    },
    {
        "name": "uart_echo_pl011",
        "arches": ["arm64"],
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": UART_ECHO_PL011_PKG,
        "device": True,
        "stdin": ECHO_INPUT,
        "expect_out": ["{banner}",
                       "root image ok segments={two}",
                       "SOS: boot regions={one}",
                       "SOS: console handover",
                       "SOS echo: window mapped",
                       "SOS echo: driver up",
                       ECHO_INPUT,
                       "SOS echo: done 4 bytes on line 33"],
        "expect_clean_exit": True,
    },
    # The Interrupt object's two caller-checkable refusals. Both are root
    # servers rather than payloads, for `event_dupkey`'s reason: a line number
    # and an ack are ordinary things a Saw driver writes, so no type can catch
    # either and the check has to be the kernel's, which puts the test at the
    # altitude a real driver would hit it from. Both line numbers mean the same
    # thing on either board, so one source serves every profile.
    {
        "name": "irq_bad_line",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": IRQ_BADLINE_PKG,
        "expect_out": ["{banner}",
                       "SOS badline: binding a line this board lacks",
                       "SOS: process fault: argument outside its domain",
                       "SOS: process teardown handles={four} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        "name": "irq_early_ack",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": IRQ_EARLYACK_PKG,
        # The teardown counts the Interrupt the process did bind, and masking
        # its line is part of the same reclaim: a device the dead process was
        # driving must not be able to interrupt a kernel with nothing to
        # deliver it to.
        "expect_out": ["{banner}",
                       "SOS earlyack: bound, acking without a fire",
                       "SOS: process fault: object in the wrong state",
                       "SOS: process teardown handles={five} threads={one} "
                       "events={zero} waiters={zero} interrupts={one}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # Nothing runnable, and nothing that could ever make something runnable.
        # With a bound IRQ line the kernel idles instead of reporting, so the
        # branch with no line bound is pinned here, beside the echo case that
        # proves the other. Without it, widening the idle rule could quietly
        # swallow a real deadlock and turn every such bug into a harness
        # timeout.
        #
        # It also protects an assumption `event_basics` relies on: a wait there
        # that stopped answering immediately would park the only thread in the
        # system, and this is the report that makes that fail in microseconds
        # rather than at the timeout.
        "name": "wait_deadlock",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": WAIT_DEADLOCK_PKG,
        "expect_out": ["{banner}",
                       "SOS deadlock: waiting for a signal nobody sends",
                       "SOS: process fault: every thread blocked",
                       "SOS: process teardown handles={six} threads={one} "
                       "events={one} waiters={one} interrupts={zero}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    # --- the Clock and Timer objects -----------------------------------------
    # A process can sleep: without a Timer, every wake comes from a thread or
    # from a device line. The cases are split so that what an object answers and
    # whether a wait parks cannot cover for each other.
    {
        # The Clock alone, with no waiting anywhere: every call answers
        # immediately, so a regression that parked would park the only thread
        # in the system and be reported as the deadlock it is, in microseconds
        # rather than at the timeout. (`event_basics` uses the same trick.)
        "name": "clock_basics",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": CLOCK_BASICS_PKG,
        "expect_out": ["{banner}",
                       # Asking twice mints twice. `minted=1` is the second ask
                       # succeeding and `differs=1` is its word being a
                       # different one: two capability instances onto the
                       # machine's single kernel-eternal Clock, each
                       # independently owned and releasable. It amplifies
                       # nothing: `SystemRight.ClockGet` gates the minting and
                       # every mint carries the kind's default rights. The
                       # teardown line carries no `clocks=` column, because a
                       # process owns no clock, so a dead one frees none.
                       "SOS clock: minted=1 differs=1",
                       "SOS clock: monotonic=1",
                       # `advanced` alone would pass on a counter of anything;
                       # `spanned` is what says the units are nanoseconds.
                       "SOS clock: advanced=1 spanned=1",
                       # The cancel idiom's precondition: disarming a Timer that
                       # was never armed must not fault, or a timeout that has
                       # already expired could not be cancelled safely.
                       "SOS clock: disarm_unarmed=1",
                       "SOS clock: done",
                       # Eight: the three boot handles, the Process handle
                       # `process_self()` minted, three Clock handles for three
                       # asks, and the Timer. Nothing here is released;
                       # `handle_drop_release` below proves the other direction.
                       "SOS: process teardown handles={eight} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={one}"],
        "expect_clean_exit": True,
    },
    {
        # A process sleeps. One thread, no scheduler tick, no bound line: at the
        # moment of the wait there is nothing runnable and nothing that could
        # ever make something runnable except the clock. So this transcript
        # existing at all is the idle rule's timer branch working, and
        # `timer_deadlock` below is the idle rule's other branch: a Timer that
        # exists but is never armed.
        "name": "timer_oneshot",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": TIMER_ONESHOT_PKG,
        "expect_out": ["{banner}",
                       "SOS oneshot: parking on a timer",
                       # `slept=1` is the clock agreeing with itself across the
                       # park, which is what a `wait` that wrongly returned
                       # immediately would fail even with the right count.
                       "SOS oneshot: woke key=55 fires=1 slept=1",
                       # A one-shot disarms itself, so the second wake can only
                       # be reached by arming again: no ack, no re-attach.
                       "SOS oneshot: rearmed woke fires=1",
                       "SOS oneshot: done",
                       "SOS: process teardown handles={seven} threads={one} "
                       "events={zero} waiters={one} interrupts={zero} "
                       "timers={one}"],
        "expect_clean_exit": True,
    },
    {
        # The periodic half. Nothing happens between the first two waits (no
        # ack, no re-arm), so a timer that did not re-arm itself would leave
        # the second wait parked with nothing to wake it, which this kernel
        # reports as a deadlock rather than hanging.
        "name": "timer_interval",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": TIMER_INTERVAL_PKG,
        "expect_out": ["{banner}",
                       # Neither periodic count is asserted, because of the
                       # feature under test. A fire delivered more than a
                       # period late coalesces, by design, so under an
                       # emulator whose timing is the host's business a correct
                       # kernel legitimately reports 1, 2 or 3 here, and the
                       # count moves with how much of the first period is spent
                       # before the wait. Pinning it would make coalescing
                       # itself the flake. What is asserted is that each wake
                       # happened, with no ack and no re-arm between them
                       # (`ackfree`); the two counts that are deterministic are
                       # pinned below.
                       "SOS interval: tick one fires=",
                       "SOS interval: tick two fires=",
                       "ackfree=1",
                       # Expiries that landed while nobody was waiting did not
                       # queue as separate wakes and were not dropped: they
                       # coalesced into one count above one.
                       "SOS interval: coalesced=1",
                       # The drain, proved without a stopwatch: cancel, re-arm
                       # one-shot, wait. A kernel that never reset the count
                       # would report the coalesced total here instead.
                       "SOS interval: drained fires=1",
                       "SOS interval: done",
                       "SOS: process teardown handles={seven} threads={one} "
                       "events={zero} waiters={one} interrupts={zero} "
                       "timers={one}"],
        "expect_clean_exit": True,
    },
    {
        # The idle rule's other branch: a Timer exists but is never armed, so it
        # can never fire and the system is as deadlocked as one with no Timer at
        # all. A kernel that counted Timers rather than armed ones would idle
        # here forever and fail at the harness timeout, the failure mode hardest
        # to read, which is why the report is pinned. (`wait_deadlock` pins the
        # same report with no Timer in the system.)
        "name": "timer_deadlock",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": TIMER_DEADLOCK_PKG,
        "expect_out": ["{banner}",
                       "SOS timerdead: waiting on a timer nobody armed",
                       "SOS: process fault: every thread blocked",
                       "SOS: process teardown handles={seven} threads={one} "
                       "events={zero} waiters={one} interrupts={zero} "
                       "timers={one}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # A clock domain this kernel cannot name. `ClockType` is raw-backed so
        # the domain space can grow without renumbering, and only one value is
        # declared, so any other number is a mistake the caller could have
        # checked, and the process is ended. It goes through the C altitude
        # because the typed Saw surface takes a `ClockType` and cannot express
        # the mistake at all, which is itself half the claim.
        "name": "timer_badclock",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": TIMER_BADCLOCK_PKG,
        "expect_out": ["{banner}",
                       "SOS badclock: asking for a domain that does not exist",
                       "SOS: process fault: argument outside its domain",
                       "SOS: process teardown handles={three} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # The copy-in door's first refusal. `Timer.Arm` reads a record out of a
        # process rather than writing one in, and the record's size is
        # published, so a wrong length is caller-checkable. The length is
        # checked for equality, not "at least": a copy-out capacity is a buffer
        # that may be over-provisioned, while a copy-in length is the caller's
        # statement of what it is handing over, and a number that is not the
        # record's size means the two sides disagree about the shape of the
        # thing being passed.
        "name": "timer_badrecord",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": TIMER_BADRECORD_PKG,
        "expect_out": ["{banner}",
                       "SOS badrecord: arming with a short record",
                       "SOS: process fault: argument outside its domain",
                       "SOS: process teardown handles={five} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={one}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    # --- Event consume: a wait delivery takes an Event's word ---------------
    # The cases split by door, and the split is not cosmetic: the delivery
    # runs a different path in each. A wait that finds something already ready
    # has the waiting thread read its own record, while a wake has the
    # signalling thread write into a parked thread's frame.
    # `deliver_attachment` is one funnel so those cannot diverge, and a case
    # per path is what that claim costs.
    {
        # The poll door, with nothing parking anywhere. A regression that
        # parked would park the only thread in the system and be reported as
        # the deadlock it is, in microseconds rather than at the timeout.
        "name": "event_consume",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": EVENT_CONSUME_PKG,
        "expect_out": ["{banner}",
                       # Two signals, one record: 1 and 2 land with nobody
                       # waiting, and the wait carries the merged word. Per
                       # signal it would read 1.
                       "SOS consume: or key=11 at-wait=3 after=0",
                       # A signal after the take carries only the new bits: 8,
                       # not 1|2|8. This is the number that separates a take
                       # from a copy.
                       "SOS consume: newbits key=11 at-wait=8 after=0",
                       # The same in the counting mode, so the take cannot be a
                       # per-mode accident: three signals of one, one record of
                       # three, and the count restarts from zero.
                       "SOS consume: sum key=22 at-wait=3 after=0",
                       "SOS consume: done",
                       # Seven handles: the three it was given, the Process
                       # handle `process_self()` minted, a waiter and two
                       # events; one thread, and both events back on the slab.
                       "SOS: process teardown handles={seven} threads={one} "
                       "events={two} waiters={one}"],
        "expect_clean_exit": True,
    },
    {
        # The wake door. Two threads, two parks, and the order is forced rather
        # than raced: this image's kernel arms no tick and a wake only makes a
        # thread runnable, so each thread runs until it blocks or exits.
        "name": "event_consume_wake",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": EVENT_CONSUME_WAKE_PKG,
        "expect_out": ["{banner}",
                       "SOS consumewake: initial parking",
                       # The worker could only have run because the initial
                       # thread parked.
                       "SOS consumewake: worker signalling",
                       # `after=0`: the `receive` following the wake finds
                       # nothing.
                       "SOS consumewake: woke first key=41 at-wait=5 after=0",
                       # Reached only because the second wait genuinely parked,
                       # which it can only do if the first wake emptied the
                       # event.
                       "SOS consumewake: worker second signal",
                       # Only the new bits: 2, not 5|2 = 7.
                       "SOS consumewake: woke second key=41 at-wait=2",
                       "SOS consumewake: joined worker=99",
                       # Both waiters and both events go back to their slabs.
                       "events={two} waiters={two}"],
        "expect_clean_exit": True,
    },
    {
        # The grant has to hold against a root that is merely wrong, not just
        # against one that behaves.
        "name": "root_server_oversteps",
        # Isolated-only: "the grant has to hold" is a claim about what the
        # hardware refuses, and a flat platform holds no grant against anybody.
        # Root reaches for the kernel and simply arrives.
        "tier": TIER_ISOLATED,
        "tier_reason": "root reaching into kernel memory must FAULT; a flat "
                       "platform lets the store land",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": FAULT_ROOT_PKG,
        "expect_out": ["SOS root: reaching for the kernel",
                       "fault store-access-fault"],
        "expect_clean_exit": False,
    },
    # =========================================================================
    # CreateProcess: the second address space
    # =========================================================================
    #
    # One claim per case. Every earlier case in this file runs in a system with
    # exactly one process, where a process's death was the machine's and a
    # protection domain never had to be put back. These cases append a child
    # image and publish a two-row region table.
    {
        # The headline. Root creates a process, starts it, and is still there
        # after it dies. Read the transcript as an order: the two-phase
        # lifecycle (created, then started), root printing after the start
        # (`start()` makes a child runnable, it does not hand the processor
        # over), the child's death naming process 1, and root's own line after
        # it.
        #
        # `handles={zero} threads={one}` in the teardown is the child's whole
        # estate: this child is handed nothing, so it owns one thread and no
        # handle. That number also asserts that the teardown ran against the
        # child's table and not root's, which holds several handles by then.
        #
        # `child status=131073` is the §8 status word root reads out of the
        # dead child through the handle it still holds: kind `Faulted` (2) in
        # the high half, reason `BadHandle` (1) in the low. It makes "the child
        # died, and this is how" a value rather than an inference from the
        # console.
        "name": "process_lifecycle",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PROCESS_LIFECYCLE_PKG,
        "children": [CHILD_FAULT_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS lifecycle: boot regions tag0=0 tag1=1",
                       "SOS lifecycle: created",
                       "SOS lifecycle: started",
                       "SOS: process fault: bad handle process={one}",
                       "SOS: process teardown handles={zero} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS lifecycle: root survived child status=131073",
                       "SOS lifecycle: done"],
        "expect_clean_exit": True,
    },
    {
        # The money proof. The child stores into root's region and the access
        # faults, which can only happen if the switch into the child reloaded
        # the protection domain: riscv32 spends all four PMP regions on one
        # image and arm64's grant window is a single shared EL0 permission
        # map, so neither profile can carry two processes' grants at once.
        #
        # The assertion is the presence of the fault. A kernel that left root's
        # rows installed would let the store land, the child would spin, and
        # the line would be missing rather than wrong.
        #
        # `child status=131072` is `Faulted` with code zero, because a hardware
        # fault carries no `FaultReason`: the cause is in the console line.
        # That is the one thing that differs from the lifecycle case's
        # `131073`, and it says the two children died in two different ways.
        "name": "process_isolation",
        # Isolated-only. This is the money proof above, and the property it
        # proves (a peer cannot reach another process's memory) is precisely
        # the one the flat tier disclaims. Running it there would test a
        # promise nobody made: the child's store lands in root's stack, the
        # child spins in the `while { }` its own source calls unreachable, and
        # the case runs out `QEMU_TIMEOUT_S` to discover the tier word was
        # telling the truth.
        "tier": TIER_ISOLATED,
        "tier_reason": "peer isolation is a property the Flat tier disclaims "
                       "— the child's poke would land",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PROCESS_ISOLATION_PKG,
        "children": [CHILD_POKE_PKG],
        "expect_out": ["{banner}",
                       "SOS isolation: started",
                       # The kernel's hardware-fault report. The tag and the
                       # cause differ per machine (a store access fault here, a
                       # data abort there), so what is asserted is that one was
                       # taken, and the teardown below says whose it was.
                       "SOS: fault ",
                       "SOS: process teardown handles={zero} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS isolation: root survived child status=131072",
                       # The sweep row: `child-poke`'s whole biography in two
                       # numbers. It holds no handles, so it never reached the
                       # kernel through the syscall door at all, and the one
                       # trap it took was the store the hardware refused. This
                       # is the only shape in which the fault column is
                       # observable: a faulting process cannot ask about
                       # itself, so the reader is the supervisor whose handle
                       # held the slot open. `death_fault` asserts the exact
                       # mirror.
                       "SOS isolation: child syscalls=0 faults=1",
                       "SOS isolation: done"],
        "expect_clean_exit": True,
    },
    {
        # Badimage is a status. Root hands `process_create` a region of zeros
        # (its two arguments swapped), gets a value back, reports it and exits
        # clean, which separates this from every other
        # you-handed-the-kernel-something-wrong case in this file, all of which
        # end with the process dead.
        #
        # `expect_clean_exit` is the load-bearing half: a kernel that faulted
        # here would still print a plausible-looking transcript and exit
        # non-zero.
        "name": "process_badimage",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PROCESS_BADIMAGE_PKG,
        "children": [CHILD_FAULT_PKG],
        "expect_out": ["{banner}",
                       "SOS badimage: create refused: "
                       "not a loadable process image",
                       "SOS badimage: still running, exiting clean"],
        "expect_clean_exit": True,
    },
    {
        # The line's other side: a second `start()` is the caller's fault, not
        # a status. `process=0` in both lines says root itself died, which
        # makes this transcript different from the lifecycle case's: the same
        # two report lines, a different process, and the machine stops.
        "name": "process_doublestart",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PROCESS_DOUBLESTART_PKG,
        "children": [CHILD_FAULT_PKG],
        "expect_out": ["{banner}",
                       "SOS doublestart: started once",
                       "SOS: process fault: object in the wrong state "
                       "process={zero}",
                       "SOS: process teardown handles=",
                       # ...and NOT the line root would print if the op had
                       # answered with a status instead of ending it. `_check`
                       # matches in order, so its absence is asserted by the
                       # clean-exit expectation below rather than by a pattern.
                       ],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # The boot-handle iterator, drained. Two rows in, two records out, in
        # ordinal order, and then `Drained`, twice, because the cursor only
        # advances. It creates no process: the claim is the delivery, and a
        # launch beside it would let an iterator bug hide behind a load that
        # happened to work.
        "name": "process_bootdrain",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PROCESS_BOOTDRAIN_PKG,
        "children": [CHILD_FAULT_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       # `tags` is the ordinals accumulated as digits, so it
                       # carries the delivery order and not only the set: 0 then
                       # 1 accumulates to 1, while 1 then 0 would print 10.
                       "SOS bootdrain: n=2 tags=1 kinds=0",
                       "SOS bootdrain: exhausted, and still exhausted",
                       "SOS bootdrain: done"],
        "expect_clean_exit": True,
    },
    # =========================================================================
    # The handle lifecycle
    # =========================================================================
    #
    # Cases covering release, stale words and drop-is-release. The three
    # mechanisms land together because each alone is broken. Mint-per-call
    # without release is a leak by design (`handle-drop-release` makes the
    # pairing visible). Release without generations is aliasing (`handle-remint`
    # is the proof that it is not). And a release op beside copyable wrappers is
    # a stale-fault factory, hence drop-is-release.
    {
        # The money proof: a released handle's word does not name its slot's
        # next occupant, it names nothing, and the kernel says so.
        #
        # `reused=1` is asserted without naming the split; see the source
        # header for the arithmetic. `HANDLE_INDEX_BITS` is configurable, and a
        # case that hardcoded `0xFF` would silently stop checking anything the
        # day it moved.
        "name": "handle_remint",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": HANDLE_REMINT_PKG,
        "expect_out": ["{banner}",
                       "SOS remint: differs=1 reused=1",
                       "SOS remint: using the word we gave back",
                       "SOS: process fault: bad handle process={zero}",
                       # Five bound at the fault: §12's three, plus the two
                       # Clock handles still held (the re-mint and the fresh
                       # one). The one that was released is not among them.
                       "SOS: process teardown handles={five} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={zero}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # The only clean exit in the family, and that is the assertion: a
        # release is an ordinary successful op. No right is checked, and the
        # slot it frees is available to the very next ask.
        "name": "handle_release_ungated",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": HANDLE_RELEASE_UNGATED_PKG,
        "expect_out": ["{banner}",
                       "SOS release: ungated=1 refilled=1",
                       "SOS release: done",
                       # Five: §12's three, the minted Process handle, and the
                       # one Clock left after a mint, a release and a re-mint.
                       "SOS: process teardown handles={five} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={zero}"],
        "expect_clean_exit": True,
    },
    {
        # `NO_HANDLE` names nothing, so releasing it is asking to destroy a
        # capability the caller never had: the ordinary `BadHandle` fault. The
        # teardown's three is §12's boot set and nothing else: this process
        # derives nothing before it dies, so the number is a statement about
        # the fault rather than about what it happened to hold.
        "name": "handle_release_nothing",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": HANDLE_RELEASE_NOTHING_PKG,
        "expect_out": ["{banner}",
                       "SOS releasenothing: releasing a word that names nothing",
                       "SOS: process fault: bad handle process={zero}",
                       "SOS: process teardown handles={three} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={zero}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # The same answer for a different mistake, and the first line is what
        # separates the two: the word released here was valid one instruction
        # earlier. What makes the second call a fault is the entry's life
        # having moved on, which is the generation doing the only job it has.
        #
        # This is also the shape a buggy transfer funnel would land in: a
        # diagnosed fault naming the process, never a silent alias.
        "name": "handle_release_twice",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": HANDLE_RELEASE_TWICE_PKG,
        "expect_out": ["{banner}",
                       "SOS releasetwice: first release ok=1",
                       "SOS releasetwice: releasing the same word again",
                       "SOS: process fault: bad handle process={zero}",
                       # Three: the Clock this program minted is the one it
                       # released, so §12's boot set is all that is left. Four
                       # would mean the first release did not unbind.
                       "SOS: process teardown handles={three} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={zero}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # A lookup regression, pinned. `0x100` is not equal to `NO_HANDLE`, so
        # a lookup that kept a whole-word test would take it for a handle and
        # index the table with a zero. The rule that refuses it is that the
        # index field is 1-based, so zero is not an index at any generation;
        # the probe stays valid at every split, since a wider index field makes
        # this word an out-of-range index instead.
        "name": "handle_malformed_word",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": HANDLE_MALFORMED_WORD_PKG,
        "expect_out": ["{banner}",
                       "SOS malformed: releasing a word whose index field is "
                       "zero",
                       "SOS: process fault: bad handle process={zero}",
                       "SOS: process teardown handles={three} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={zero}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # Drop is release, from the typed side: the one case in this family
        # that never names a handle word. Four Clock handles are minted; three
        # die in an inner scope and one is kept, and the teardown counts five
        # (§12's three, the minted Process handle, the kept Clock). Eight would
        # be mint-per-call without RAII, a leak by design.
        #
        # `scoped=3` is an assertion about the timing as well: the handle is
        # used inside the scope, so a release that ran too early would fault
        # the process there rather than quietly changing a count.
        "name": "handle_drop_release",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": HANDLE_DROP_RELEASE_PKG,
        "expect_out": ["{banner}",
                       "SOS droprelease: scoped=3 kept=1",
                       "SOS droprelease: done",
                       "SOS: process teardown handles={five} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={zero}"],
        "expect_clean_exit": True,
    },
    {
        # A dead process's body comes back at its teardown, while root still
        # holds its handle: only the record is held. With the dead child held,
        # an unstarted filler takes the lowest free body (the dead child's),
        # and a second create finds the last body free only because the dead
        # child's went back. The record half (held until
        # the last reference goes, then reclaimed) is `process_records`.
        #
        # It reuses the child-fault package, and every create loads the same
        # image into the same RAM; the first child's remains are nothing the
        # kernel tracks.
        "name": "process_reclaim",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PROCESS_RECLAIM_PKG,
        "children": [CHILD_FAULT_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS reclaim: started",
                       # The child dies first, and the teardown names process 1:
                       # the same two lines `process_lifecycle` asserts, here
                       # only as the precondition for what follows.
                       "SOS: process fault: bad handle process={one}",
                       "SOS: process teardown handles={zero} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS reclaim: body_back_while_held=1",
                       "SOS reclaim: done"],
        "expect_clean_exit": True,
    },
    {
        # A `Gone` record whose last reference is dropped by another process's
        # teardown is reclaimed. The supervisor makes a grandchild that faults,
        # waits for the death and exits; the first and third supervisors end
        # holding the grandchild's Process handle, the second holding only the
        # attachment. Root first holds two dead children of its own (records
        # 1 and 2), so every supervisor lands in record 3 and every grandchild
        # in record 4, past `MAX_PROCESSES`: free again only if the previous
        # supervisor's teardown scanned the whole record table and reclaimed
        # it. The fault lines' `process={four}` is the claim.
        # `spare=2` is the body table (root, a supervisor and a grandchild)
        # minus root: no body leaked. `status=65596` and `65597` are `Exited`
        # (1) << 16 with the supervisor's code in the low half; any other low
        # half is a step the supervisor could not take.
        "name": "gone_reclaim_teardown",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": GONE_RECLAIM_TEARDOWN_PKG,
        "children": [CHILD_FAULT_PKG, CHILD_SUPERVISOR_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={four}",
                       "SOS: process fault: bad handle process={one}",
                       "SOS: process fault: bad handle process={two}",
                       "SOS: process fault: bad handle process={four}",
                       "SOS gonereclaim: handle status=65596",
                       "SOS: process fault: bad handle process={four}",
                       "SOS gonereclaim: attachment status=65597",
                       "SOS: process fault: bad handle process={four}",
                       "SOS gonereclaim: again status=65596",
                       "SOS gonereclaim: spare=2",
                       "SOS gonereclaim: done"],
        "expect_clean_exit": True,
    },
    {
        # Dead processes hold records, not bodies. Root runs `child-fault` to
        # its death again and again, keeping every handle, until a create is
        # refused. `held=11` is the record table (`MAX_PROCESS_RECORDS`) less
        # root's own record, far past the body table, and `room=1` is a sibling
        # minted beside the refusal, so the wall is not root's handle table.
        #
        # The second line is every held handle read back on the way up:
        # `Faulted` (2) << 16 | `BadHandle` (1), and the one syscall the child
        # died making. The third is the reclaim: each level dropped its handle,
        # so a second pass holds as many again.
        "name": "process_records",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PROCESS_RECORDS_PKG,
        "children": [CHILD_FAULT_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS records: pass 1 held=11 room=1, then out of "
                       "kernel objects",
                       "SOS records: every held child answered status=131073 "
                       "syscalls=1 faults=0",
                       "SOS records: pass 2 held=11 room=1, then out of "
                       "kernel objects",
                       "SOS records: done"],
        "expect_clean_exit": True,
    },
    {
        # The record table is donatable on its own. Root holds dead children
        # (each handed to a Waiter it never waits on, so its table stays small)
        # until a create is refused at the same `held=11`; a page donated to
        # `SlabKind.Processes` changes nothing, because no live process is
        # waiting for a body, and a page donated to `SlabKind.ProcessRecords`
        # lets the next create through.
        "name": "process_records_donate",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PROCESS_RECORDS_DONATE_PKG,
        "children": [CHILD_FAULT_PKG],
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={three}",
                       "SOS recdonate: held=11, then out of kernel objects",
                       "SOS recdonate: after a body donation, out of kernel "
                       "objects",
                       "SOS recdonate: after a record donation, held one more",
                       "SOS recdonate: done"],
        "expect_clean_exit": True,
    },
    {
        # A Mapping whose target died names nobody. A held dead child takes
        # record 1, so the mapped child is record 2 in body 1. It dies with the
        # page mapped and stays held, and a second child lands in body 1 with a
        # page at the same row index. Three things are pinned:
        #
        #   counters syscalls=1   the dead mapped child's `Stats` after the
        #                         second create zeroed body 1's row: the
        #                         record's own copy, so its body link was
        #                         cleared at the teardown
        #   process={two} again   the mapped child released, a third child
        #                         takes record 2 and dies; its teardown stops
        #                         the kernel on a Mapping naming record 2 with
        #                         no row, so dying normally is the husk's
        #                         `target` disown
        #   the BadState fault    `Base` on the husk, before any address
        "name": "mapping_husk_base",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": MAPPING_HUSK_BASE_PKG,
        "children": [CHILD_FAULT_PKG],
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={three}",
                       "SOS: process fault: bad handle process={one}",
                       "SOS huskbase: mapped into the first child",
                       "SOS: process fault: bad handle process={two}",
                       "SOS: process teardown handles={zero} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={two}",
                       "SOS huskbase: mapped into the second child",
                       "SOS huskbase: the first child's counters syscalls=1 "
                       "faults=0",
                       "SOS: process fault: bad handle process={two}",
                       "SOS: process teardown handles={zero} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={two}",
                       "SOS huskbase: the third child is gone",
                       "SOS: process fault: object in the wrong state "
                       "process={zero}",
                       "SOS: process teardown handles="],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # The same husk, asked to `Unmap`: the same `BadState` fault, and the
        # second child's row stays where it is. The counters and the reused
        # record are pinned as in `mapping_husk_base`.
        "name": "mapping_husk_unmap",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": MAPPING_HUSK_UNMAP_PKG,
        "children": [CHILD_FAULT_PKG],
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={three}",
                       "SOS: process fault: bad handle process={one}",
                       "SOS huskunmap: mapped into the first child",
                       "SOS: process fault: bad handle process={two}",
                       "SOS: process teardown handles={zero} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={two}",
                       "SOS huskunmap: mapped into the second child",
                       "SOS huskunmap: the first child's counters syscalls=1 "
                       "faults=0",
                       "SOS: process fault: bad handle process={two}",
                       "SOS: process teardown handles={zero} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={two}",
                       "SOS huskunmap: the third child is gone",
                       "SOS: process fault: object in the wrong state "
                       "process={zero}",
                       "SOS: process teardown handles="],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    # =========================================================================
    # Mint, give, and the boot drain
    # =========================================================================
    #
    # Cases about the launch flow: narrow an authority, hand it across a
    # process boundary, name it with a word the kernel never reads, and let the
    # receiver find it.
    #
    # The three ops divide cleanly and the cases follow them. `MINT_OP`
    # narrows a capability inside one table (`mint_revoked`,
    # `give_no_transfer`); `Give` moves one between two (`give_duplicate_tag`,
    # `give_after_start`, `give_word_dead`); `Start` delivers one to a register
    # and freezes the set (`start_bad_tag`). `give_boot_drain` is all three at
    # once, and `child_no_shutdown` is what the narrowing is for.
    {
        # The money shot: mint, give, start. Three ordinary ops, and the
        # launcher keeps what it supervises with.
        #
        # Root narrows its own System authority into a sibling (`Debug |
        # ProcessSelf | ClockGet | Transfer`; `Shutdown`, `ProcessCreate` and
        # `Mint` absent by silence, the keep mask's fail-closed polarity),
        # gives the sibling under tag 7 with two Memory regions under 3 and 5,
        # and starts the child naming tag 7. It never gives away the child's
        # Process handle, so it still supervises. `Transfer` stays in the mask
        # because it is the bit `give` checks on the thing being given: a
        # launcher cannot narrow below the ticket that lets the handle arrive.
        #
        # `SOS childdrain:` is the child speaking. The child owns no device;
        # `Debug` travelled in the mask.
        #
        # `n=2` is deliberate, not an off-by-one: three capabilities were given
        # and the one named by the boot tag was consumed by `start`'s
        # resolution, because the register is its delivery. `tags=35` carries
        # the order as digits, so a kernel delivering out of order prints `53`.
        #
        # `code=2` and `status=65538` are the same fact from the two sides of
        # the boundary: the kernel's account of the child's exit, and root
        # reading `Exited`(1)<<16 | 2 through the Process handle it kept. Both
        # at once is what mint buys: a launcher can supervise and furnish.
        #
        # `handles={two}` in the teardown is the child's estate: the System
        # handle it printed through and the Process handle it derived from it.
        # It declined both regions at the drain, and their records dropped and
        # released them: drop-is-release showing up in a case that is not about
        # it.
        "name": "give_boot_drain",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": GIVE_BOOT_DRAIN_PKG,
        "children": [CHILD_DRAIN_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS givedrain: created",
                       "SOS givedrain: minted a masked System",
                       "SOS givedrain: gave 7 3 5",
                       "SOS givedrain: started",
                       "SOS childdrain: n=2 tags=35 kinds=0",
                       "SOS: process exit: code={two} process={one}",
                       "SOS: process teardown handles={two} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS givedrain: root observed child status=65538",
                       "SOS givedrain: done"],
        "expect_clean_exit": True,
    },
    {
        # The tag is the identity, so two handles under one tag is broken
        # config: caller-checkable, and it would make the boot record a
        # multimap and `start(boot_tag:)`'s lookup ambiguous.
        #
        # The fault tag is the Waiter's, word for word: `DuplicateKey` was
        # written for §2.2's attachment keys and says the same thing here. It
        # is asserted verbatim because `event_dupkey` asserts that string.
        "name": "give_duplicate_tag",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": GIVE_DUPLICATE_TAG_PKG,
        "children": [CHILD_FAULT_PKG],
        "expect_out": ["{banner}",
                       "SOS givedup: first give ok",
                       "SOS: process fault: an attachment already uses that key "
                       "process={zero}",
                       "SOS: process teardown handles="],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # The freeze. The give-before-start ordering is the soundness argument
        # for the launch flow (a child's boot handles must answer completely at
        # its first instruction), and `start` is the barrier that makes a
        # half-populated table unrepresentable with no synchronization
        # invented. `BadState` is the same fault a second `start` raises,
        # through the same process-state word, which says the two refusals are
        # one rule.
        "name": "give_after_start",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": GIVE_AFTER_START_PKG,
        "children": [CHILD_FAULT_PKG],
        "expect_out": ["{banner}",
                       "SOS giveafter: started",
                       "SOS: process fault: object in the wrong state "
                       "process={zero}",
                       "SOS: process teardown handles="],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # `Process.exit` is self-only. Root holds its child's Process handle,
        # which carries `ProcessRight.Exit`, so the rights check passes and the
        # target check refuses: a fault on the caller, the way `ThreadOp.Exit`
        # refuses a thread that is not the caller's own.
        "name": "process_exit_not_self",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PROCESS_EXIT_NOT_SELF_PKG,
        "children": [CHILD_FAULT_PKG],
        "expect_out": ["{banner}",
                       "SOS exitnotself: started",
                       "SOS: process fault: not the caller process={zero}",
                       "SOS: process teardown handles="],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # The attenuation proof, on §3's universal low byte: `Transfer` is
        # declared in every kind's rights enum, and `give` and sending a handle
        # in a message check it.
        #
        # One transcript, two claims. Root mints a sibling through a mask that
        # omits `Transfer` (the complement notation, the way to say "the same
        # handle, minus one bit") and the give of it is `AccessDenied`. So the
        # give's gate is real and a masked sibling is genuinely narrower than
        # its source, with no way back: a mint's result is a subset, so minting
        # again only narrows further.
        #
        # Root's own System handle carries `Transfer`, because giving a masked
        # System sibling to a child is the launch flow; the ungivable handle
        # here is one a launcher made ungivable.
        "name": "give_no_transfer",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": GIVE_NO_TRANSFER_PKG,
        "children": [CHILD_FAULT_PKG],
        "expect_out": ["{banner}",
                       "SOS notransfer: minted without Transfer",
                       "SOS: process fault: access denied process={zero}",
                       "SOS: process teardown handles="],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # A boot tag that names no given record is a fault. There is no honest
        # word to put in the child's first argument register, and a zero would
        # be indistinguishable from the deliberate no-tag form, so a typo'd tag
        # would silently produce a sandboxed process instead of a diagnosis.
        #
        # The give above it makes the case sharp: tag 3 is in the set, so
        # "resolved to nothing" cannot be confused with "nothing to resolve
        # against".
        "name": "start_bad_tag",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": START_BAD_TAG_PKG,
        "children": [CHILD_FAULT_PKG],
        "expect_out": ["{banner}",
                       "SOS badtag: gave tag 3",
                       "SOS: process fault: argument outside its domain "
                       "process={zero}",
                       "SOS: process teardown handles="],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # The giver's word is dead: generations composing with a give. The
        # unbind is `unbind_handle`, the same one release and the teardown
        # call, so the word the giver held names a live slot at the wrong life
        # and using it is the ordinary `BadHandle`. Without generations it
        # would be an alias instead: the freed slot is the lowest free index,
        # so the very next mint takes it back.
        #
        # It works at the C altitude, and that is half the claim:
        # `Process.give` consumes its wrapper and disarms it before the
        # syscall, so "use the word you gave away" is not a sentence the typed
        # layer can express.
        "name": "give_word_dead",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": GIVE_WORD_DEAD_PKG,
        "children": [CHILD_FAULT_PKG],
        "expect_out": ["{banner}",
                       "SOS worddead: gave the region, ok=1",
                       "SOS worddead: using the word we gave away",
                       "SOS: process fault: bad handle process={zero}",
                       "SOS: process teardown handles="],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # Proliferation is revocable. `MINT_OP` is gated on the universal
        # `Mint` bit and that bit is maskable, so a launcher hands out a handle
        # that can be used and not multiplied; otherwise one `Mint` anywhere
        # would be `Mint` everywhere downstream forever.
        #
        # The first mint succeeds, which makes the second refusal mean
        # something: the caller holds a perfectly good System handle and the
        # one thing it cannot do is the thing its mask took away.
        #
        # It also supplies a source lacking `Mint`, so this transcript is both
        # "the sibling cannot mint" and "a mint through a source without `Mint`
        # is `AccessDenied`".
        "name": "mint_revoked",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": MINT_REVOKED_PKG,
        "expect_out": ["{banner}",
                       "SOS mintrevoked: minted without Mint",
                       "SOS: process fault: access denied process={zero}",
                       "SOS: process teardown handles="],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # The mask holds at run time: what the narrowing is for, and a case
        # where a rights refusal falls on a process that is not root.
        #
        # The child prints first, which is half the claim: a keep mask is a set
        # rather than a wall, `Debug` was named, so the program has a voice and
        # can announce what it is about to try. Then it asks to stop the
        # machine, `Shutdown` is absent by silence, and the fault names process
        # 1 (the asker, not the launcher) while the machine keeps running.
        #
        # `status=131075` is that same fact as a value through root's retained
        # handle: `Faulted`(2)<<16 | `AccessDenied`(3). A masked bit is
        # enforced by exactly the path a never-minted one is.
        "name": "child_no_shutdown",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": CHILD_NO_SHUTDOWN_PKG,
        "children": [CHILD_OVERSTEPS_PKG],
        "expect_out": ["{banner}",
                       "SOS noshutdown: started a child that may print and not "
                       "stop the machine",
                       "SOS childover: I can print, so Debug travelled",
                       "SOS childover: now trying to stop the machine",
                       "SOS: process fault: access denied process={one}",
                       "SOS: process teardown handles={one} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS noshutdown: root survived child status=131075",
                       "SOS noshutdown: done"],
        "expect_clean_exit": True,
    },
    # =========================================================================
    # Memory, IoMemory, Mapping
    # =========================================================================
    #
    # A mapping is an installed grant row, and proving that takes derivation
    # (two verbs, because the kinds differ), installation, revocation, and
    # installation across a process boundary.
    #
    # Every protection case above asserts that an address a process was not
    # granted faults (`root_server_oversteps`, `process_isolation`). These
    # assert the other direction: an address that would have faulted is memory,
    # because somebody installed a row. Together they make a protection domain
    # data rather than a property of an image.
    #
    # Three of the five end in a fault and do their positive work first,
    # `irq_early_ack`'s shape: a `BadArg` fault ends the process, so a second
    # probe would never run.
    {
        # Derivation, and the arithmetic made observable. A region has no
        # bounds reader, so "the parent advanced" is proven by using both
        # pieces: 0xaa into the first, 0xbb into the second, and the first
        # still 0xaa.
        #
        # The last line is a death: an oversize cut is caller-checkable, so it
        # is a fault, so meeting it is the only way to assert it.
        "name": "memory_split",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": MEMORY_SPLIT_PKG,
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={one}",
                       "SOS split: a=170 b=187 a-again=170",
                       # Twenty cut-and-drop rounds, past `MAX_MEMORIES`,
                       # complete with nothing refused: free-on-last-reference
                       # returns a dropped piece's slot, so the Memory slab
                       # never runs out here.
                       "SOS split: cuts=20 refused=0",
                       "SOS split: asking for more than is left",
                       "SOS: process fault: argument outside its domain",
                       "SOS: process teardown"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # Installation. Root maps a page into itself and writes it, the inverse
        # of every protection proof above. It is also half the live-domain
        # rule: the scheduler skips equal domains, so a map into the running
        # process that did not reload would never be installed and the write
        # below would fault instead of printing.
        #
        # The double map shows two rows over one region with different access,
        # allowed. The case asserts that both install and that a read still
        # answers; deliberately not a write afterwards, since which row answers
        # is the hardware's own matching rule.
        "name": "map_basics",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": MAP_BASICS_PKG,
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={one}",
                       "SOS mapbasics: wrote 0x5a read 90",
                       "SOS mapbasics: double-mapped read-only",
                       "SOS mapbasics: still 90",
                       "SOS mapbasics: done"],
        "expect_clean_exit": True,
    },
    {
        # Revocation, and the live-domain rule end to end. Root maps, writes,
        # reads, unmaps, and touches again, and the touch has to die.
        #
        # The assertion is the presence of the fault: a kernel that edited the
        # grant record without reloading would let the touch land, the program
        # would print `UNREACHABLE` and exit non-zero on its own account, and
        # the missing line would be the tell. A revocation that does not revoke
        # until the next reschedule is not a revocation.
        #
        # The fault's tag and cause differ per machine (a load access fault
        # here, a data abort there), so what is asserted is that one was taken.
        "name": "map_unmap",
        # Isolated-only: the other property the tier word disclaims, `unmap`'s
        # revocation half. On this profile the row leaves the record, the
        # hardware forgets it, and the touch dies. On a flat profile the row
        # still leaves the record and the Mapping still dies (the kernel-side
        # bookkeeping does not depend on the tier), but the hardware goes on
        # permitting the access, so the program prints its own UNREACHABLE line
        # and exits non-zero on its own account.
        "tier": TIER_ISOLATED,
        "tier_reason": "unmap's revocation half is a property the Flat tier "
                       "disclaims — the touch after unmap would succeed",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": MAP_UNMAP_PKG,
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={one}",
                       "SOS mapunmap: wrote 0x77 read 119",
                       "SOS mapunmap: unmapped",
                       "SOS: fault "],
        "expect_clean_exit": False,
    },
    {
        # Installation across a process boundary: the launcher shape. Root maps
        # a page into a child it has created but not started, and the child
        # touches an address its image never declared. (`share_double_map`
        # carries this shape through to a conversation: the same install, plus
        # a row in root's own domain over the same region, plus bytes crossing
        # in both directions.)
        #
        # The map is before the start and is not a boot record, which is why
        # the give-freeze does not reach it: §12's set freezes at `start`
        # because a child's handles must be complete at its first instruction,
        # and a protection row is not a handle. The row reaches the child
        # through the ordinary switch-in replay, carrying a row that did not
        # come from an image.
        #
        # `status=65596` is `Exited`(1)<<16 | 60: the byte the child read back,
        # arriving as a value through the Process handle root kept. The same
        # fact as the child's console line, from the other side.
        "name": "map_into_child",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": MAP_INTO_CHILD_PKG,
        "children": [CHILD_TOUCH_PKG],
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={three}",
                       "SOS mapchild: created",
                       "SOS mapchild: mapped a page into it",
                       "SOS mapchild: started",
                       "SOS childtouch: wrote 60 read 60",
                       "SOS: process exit: code={touch_mark} process={one}",
                       "SOS mapchild: root observed child status=65596",
                       "SOS mapchild: done"],
        "expect_clean_exit": True,
    },
    {
        # Device authority. A window is carved non-exclusively (the same range
        # twice, which a split could not do), mapped, and then a window the
        # machine's protection hardware cannot express is a fault.
        #
        # The refusal is one line for two different rules: one profile needs a
        # naturally-aligned power of two (one protection entry), the other a
        # whole number of pages, and `0xC00` is neither. That is what a per-HAL
        # predicate buys: the case is arch-free and the rule is not.
        #
        # X-on-device needs no line: `map(iomemory:)` takes no access argument,
        # so an executable register block is not a thing a caller can ask for.
        "name": "iomemory_carve",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": IOMEMORY_CARVE_PKG,
        "device": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={one}",
                       "SOS carve: carved twice",
                       "SOS carve: mapped",
                       "SOS carve: asking for a window this board cannot grant",
                       "SOS: process fault: argument outside its domain",
                       "SOS: process teardown"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    # =========================================================================
    # Quotas and the reference count
    # =========================================================================
    #
    # With the reference count, an object's slot comes back when its last
    # reference goes, not only at its process's teardown; that is what makes
    # `refcount_free` observable, lets `interrupt_unbind` succeed rather than
    # fault with `LineBound`, and gives `mapping_slot_free` its behaviour. The
    # ledger gives the two quota cases a refusal to name: `QuotaExceeded`.
    #
    # The two mechanisms land together because the zero-crossing and the
    # credit are one event: a count without a ledger reclaims silently and a
    # ledger without a count can only ever be credited by a death.
    {
        # The money proof of the count: two ways an object outlives a release,
        # and a teardown that counts neither of the events this program made.
        #
        # `events={zero}` is the line to read. Two Events were created here;
        # one was held by a sibling handle after its first was released, the
        # other by an attachment after its only handle was released, and each
        # freed at its own zero: the first at the second release, the second at
        # the `Waiter.Remove` that dropped its attachment. Without the count,
        # this would be the number of events a program had ever made.
        #
        # `waiters={one}` beside it is the asymmetry that makes the cascade
        # terminate: an attachment references the waitable and never the Waiter
        # that holds it, so the Waiter survives to the teardown while the thing
        # it watched did not.
        "name": "refcount_free",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": REFCOUNT_FREE_PKG,
        "expect_out": ["{banner}",
                       "SOS refcount: sibling word=42",
                       "SOS refcount: attached key=77 word=9",
                       "SOS refcount: done",
                       "SOS: process teardown handles={five} threads={one} "
                       "events={zero} waiters={one} interrupts={zero} "
                       "timers={zero} process={zero}"],
        "expect_clean_exit": True,
    },
    {
        # A bound line comes back mid-life. Releasing the last Interrupt handle
        # masks the line and frees the slot, so the very next bind of the same
        # line succeeds. The second line existing at all is therefore the
        # claim; binding a live line again would be a `LineBound` fault, which
        # ends the process.
        #
        # `interrupts={one}` says the kernel is holding exactly one Interrupt
        # when the process ends, after this program bound the same line twice.
        # `handles={five}` is §12's three, the Process handle this program
        # derived, and the second interrupt handle; the first is not among
        # them.
        "name": "interrupt_unbind",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": INTERRUPT_UNBIND_PKG,
        "expect_out": ["{banner}",
                       "SOS irqunbind: bound line 5",
                       "SOS irqunbind: rebound=1",
                       "SOS: process teardown handles={five} threads={one} "
                       "events={zero} waiters={zero} interrupts={one} "
                       "timers={zero} process={zero}"],
        "expect_clean_exit": True,
    },
    {
        # Mapping slots recycle.
        #
        # `rounds=12` is past `MAX_MAPPINGS`, so the slab is demonstrably
        # recycling, and past root's own row allowance too (its free rows, five
        # on the smaller profile), so `Unmap` is demonstrably crediting the
        # ledger as well as returning the row. One number carries both halves.
        #
        # The other two lines are the second-zero condition from both sides.
        # `husk wrote 94 read 94`: every handle on the region is destroyed and
        # its bytes are still there, so the slot survived the handle drop; a
        # kernel counting handles alone would have freed it with a protection
        # row still reaching those bytes. `recycled read 94`: the unmap takes
        # the last row, the range folds back into the pool's frontier, and the
        # next split of the same size is the same page, proven by the marker
        # written through the first mapping and read through the second.
        "name": "mapping_slot_free",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": MAPPING_SLOT_FREE_PKG,
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={one}",
                       "SOS mapfree: rounds=12 refused=0",
                       "SOS mapfree: husk wrote 94 read 94",
                       "SOS mapfree: recycled read 94",
                       "SOS mapfree: done",
                       "SOS: process teardown"],
        "expect_clean_exit": True,
    },
    {
        # A process told "no" by policy rather than by the machine. The child
        # creates Events to its own limit, meets `QuotaExceeded`, releases one
        # and creates again successfully, which is the half `NoResource` can
        # never have, since a machine edge does not move because one process
        # let go of something.
        #
        # The slab is never the limit that trips, and the numbers say so: the
        # child's allowance is below `MAX_EVENTS` and root creates no events in
        # this case at all, so part of the slab was free at the moment of the
        # refusal. If the two were confused, the create after the allowance
        # would have succeeded and `made=` would read one higher.
        #
        # `events={three}` in the child's teardown is the ledger's other half
        # arriving as somebody else's count: five events made, one released,
        # one dropped, three still held at the end.
        #
        # `status=65540` is `Exited`(1)<<16 | 4, the child's own count, read
        # through the Process handle root kept.
        "name": "quota_exceeded",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": QUOTA_EXCEEDED_PKG,
        "children": [CHILD_QUOTA_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS quotaex: created",
                       "SOS quotaex: started",
                       "SOS childquota: made=4 quota=1 resumed=1",
                       "SOS: process exit: code={four} process={one}",
                       # The child's estate: the System handle it printed
                       # through, the Process handle it derived from it, and the
                       # three events it was still holding. The one it released
                       # and the one it dropped after the credit are not among
                       # them: the same fact `events={three}` states from the
                       # slab's side.
                       "SOS: process teardown handles={five} threads={one} "
                       "events={three} waiters={zero} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS quotaex: root observed child status=65540",
                       "SOS quotaex: done"],
        "expect_clean_exit": True,
    },
    {
        # The row-budget check's ordering, observable; a clean run is the
        # proof. The kernel's half of this claim is a `fatal_kernel` at the
        # row-budget check, which by construction never appears in a passing
        # transcript, so what a case can show is that the policy refusal
        # arrives first, and that the machine is still standing to say so.
        #
        # `maps=2` is the same on every profile, which is why this is a child
        # and not root. A child's allowance is `DEFAULT_QUOTA_MAPPINGS`,
        # clamped to the rows its own domain has left after its image spent
        # its own: the same number on each machine, whatever the free rows
        # there. Root's allowance is its free row count, so a root-side
        # version would print a different number per machine.
        #
        # Each Mapping object is dropped and each row stays, which makes this a
        # ledger test: the Mapping slab is untouched at the refusal, so only a
        # quota that counts installed rows could have refused the third.
        "name": "quota_vs_wall",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": QUOTA_VS_WALL_PKG,
        "children": [CHILD_MAPWALL_PKG],
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={three}",
                       "SOS quotawall: created",
                       "SOS quotawall: gave a page",
                       "SOS quotawall: started",
                       "SOS childmapwall: maps=2 quota=1",
                       "SOS: process exit: code={two} process={one}",
                       "SOS quotawall: root observed child status=65538",
                       "SOS quotawall: done"],
        "expect_clean_exit": True,
    },
    {
        # A supervisor woken by a death. The launcher attaches the child's own
        # Process handle to a Waiter and parks on it with no timer armed, so
        # the only thing in the machine that can make root runnable again is the
        # child ending. A kernel that failed to notify would leave nothing
        # runnable and the run would die with `every thread blocked`; the
        # failure is loud, which makes a plain `wait()` an assertion.
        #
        # The order of the last three lines is the claim. The kernel notifies
        # as soon as the §8 status word is recorded, before it closes a single
        # handle, but a notification only queues the woken thread, so the whole
        # teardown runs first and root is picked up afterwards. The wake line
        # therefore lands after the teardown line, and no supervisor observes a
        # half-dead process.
        #
        # `status=65541` is `Exited`(1) << 16 | 5, `child-bye`'s own exit code,
        # arriving in a wait record rather than through a `get_status` poll.
        # `key=44` is the word root chose at the attach and the kernel handed
        # back unread.
        #
        # `handles={two}` in the child's teardown is its whole estate: the
        # masked System handle it was given, and the Process handle it derived
        # from it to call `exit` through.
        "name": "death_notify",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": DEATH_NOTIFY_PKG,
        "children": [CHILD_BYE_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS deathnotify: started",
                       "SOS bye: exiting",
                       "SOS: process exit: code={five} process={one}",
                       "SOS: process teardown handles={two} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS deathnotify: woke key=44 status=65541",
                       "SOS deathnotify: done"],
        "expect_clean_exit": True,
    },
    {
        # The fault arm: the same program with one thing taken away. This
        # launcher gives its child nothing, so the child holds `NO_HANDLE` and
        # the kernel terminates it at its first `ecall`. The attachment, the
        # park and the wake are identical; what differs is the word that comes
        # back, which is the whole claim: a supervisor learns that a child died
        # and how through one mechanism, whether the child chose the ending or
        # the kernel did.
        #
        # `status=131073` is `Faulted`(2) << 16 | `BadHandle`(1), byte for byte
        # the word `process_lifecycle` reads through `get_status`, arriving
        # here as a wake instead of as a poll. §8's word has a second door and
        # no new vocabulary.
        "name": "death_fault",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": DEATH_FAULT_PKG,
        "children": [CHILD_FAULT_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS deathfault: started",
                       "SOS: process fault: bad handle process={one}",
                       "SOS: process teardown handles={zero} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS deathfault: woke key=45 status=131073",
                       # The sweep row, the mirror of `process_isolation`'s.
                       # `child-fault` made one `ecall`, naming handle zero, and
                       # the kernel ended it, so the trap is charged to the
                       # syscall column even though it killed the process. The
                       # columns are keyed by how the machine entered the
                       # kernel, not by how the kernel felt about it, which
                       # keeps `syscalls` predictable: a caller cannot know in
                       # advance which call gets refused.
                       "SOS deathfault: child syscalls=1 faults=0",
                       "SOS deathfault: done"],
        "expect_clean_exit": True,
    },
    {
        # Nothing un-dies: the terminal level, and the reference count observed
        # through the supervision flow.
        #
        # `first=65541 second=65541` is two claims in one line. Root sleeps on
        # a timer until the child is already dead and only then attaches, so an
        # edge-triggered design would have nothing left to report and this wait
        # would never return; it returns immediately, because readiness is
        # `state == Gone` and that is still true. And the second wait on the
        # same attachment answers the same word, because a death, unlike an
        # Event's word or a Timer's fire count, is not spent by the delivery
        # that reports it.
        #
        # `held=1` and the second child are which reference was load-bearing,
        # asked one at a time. With the Process handle released and only the
        # attachment left, a third wait still reads the same word (record 1 is
        # still `Gone`); remove the attachment and the count reaches zero, the
        # record frees inside that very syscall, and the next child takes it:
        # its exit line names process 1 again. A kernel that did not count the
        # attachment would stop at the release, reporting a record that
        # reached zero references while attached.
        "name": "death_late_attach",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": DEATH_LATE_ATTACH_PKG,
        "children": [CHILD_BYE_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS deathlate: started",
                       "SOS bye: exiting",
                       "SOS: process exit: code={five} process={one}",
                       "SOS: process teardown handles={two} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS deathlate: first=65541 second=65541",
                       "SOS deathlate: held=1",
                       "SOS bye: exiting",
                       "SOS: process exit: code={five} process={one}",
                       "SOS: process teardown handles={two} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS deathlate: done"],
        "expect_clean_exit": True,
    },
    # =========================================================================
    # The driver child, shared memory, and the exec gate
    # =========================================================================
    #
    # The driver-child cases are what the whole ladder was for: a second
    # process, a way to furnish one, a device window that arrives as a
    # capability, a budget and reference count, and a death a supervisor is
    # woken by add up to a launcher that hands a device to a driver.
    #
    # The split into five follows one claim per image plus the fact that a
    # fault ends the process, so two refusals cannot share a transcript. That
    # is why the exec gate is split across cases; see `map_exec_gated`.
    {
        # The driver child, from config, on riscv32.
        #
        # It is `uart_echo_ns16550` above with one thing changed, and it is not
        # the driver: the echoing process is no longer the one the kernel
        # loaded. Root drains the console's register page out of its own boot
        # set, gives it away, mints a masked System beside it, starts the child
        # and parks on its death. Everything after `gave the window` is a
        # process that root created, driving a device root no longer has.
        #
        # What each asserted line rules out, beyond what the root-as-driver
        # twin already ruled out:
        #
        #   boot regions={three}   the child's blob, its RAM, and the device
        #                          row
        #   gave the window        `Transfer` spent on an `IoMemory`; root's
        #                          wrapper is disarmed and root can map it
        #                          nowhere
        #   window mapped          the child called `Process.map(iomemory:)` on
        #                          its own Process handle: the give moved
        #                          authority, and where the row goes was then
        #                          the holder's business
        #   Zq7#                   give, drain, map, bind, enable, park, wake
        #                          and ack, all across a process boundary
        #   status=65536           `Exited`(1) << 16 | 0, read by root through
        #                          the Process handle it kept, so the driver's
        #                          clean ending is a fact root observed rather
        #                          than one the console merely showed
        #
        # There is no timer anywhere, which matters twice. A driver waits on a
        # human-scale serial port, so a deadline would have to be guessed; and
        # §9a's one bounded exception to the console handover is a kernel whose
        # armed tick narrates over a process that owns the device. A case whose
        # transcript matters after handover arms none.
        "name": "child_echo_ns16550",
        "arches": ["riscv32", "riscv32-flat"],
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": DRIVER_CHILD_PKG,
        "children": [CHILD_ECHO_NS16550_PKG],
        "device": True,
        "stdin": ECHO_INPUT,
        "expect_out": ["{banner}",
                       "root image ok segments={two}",
                       "SOS: boot regions={three}",
                       "SOS: console handover",
                       "SOS driverchild: created",
                       "SOS driverchild: gave the window",
                       "SOS driverchild: started",
                       "SOS echo: window mapped",
                       "SOS echo: driver up",
                       ECHO_INPUT,
                       "SOS echo: done 4 bytes on line 10",
                       # The driver's whole estate, counted by the kernel as it
                       # takes it back: the masked System its launcher gave it,
                       # the Process handle it derived, the IoMemory it was
                       # given, the Mapping that window produced, the Interrupt
                       # it bound and the Waiter it parked on. Six, and every
                       # one is a step of §9's cycle: a driver is exactly its
                       # handles.
                       "SOS: process teardown handles={six} threads={one} "
                       "events={zero} waiters={one} interrupts={one} "
                       "timers={zero} process={one}",
                       "SOS driverchild: root observed child status=65536",
                       "SOS driverchild: done"],
        "expect_clean_exit": True,
    },
    {
        # The same on the other machine, with the same package split the
        # root-as-driver twins have: a driver names its device, so there are two
        # child packages named for the chip, while the launcher above them is
        # arch-free and built for both, because a capability has no datasheet.
        "name": "child_echo_pl011",
        "arches": ["arm64"],
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": DRIVER_CHILD_PKG,
        "children": [CHILD_ECHO_PL011_PKG],
        "device": True,
        "stdin": ECHO_INPUT,
        "expect_out": ["{banner}",
                       "root image ok segments={two}",
                       "SOS: boot regions={three}",
                       "SOS: console handover",
                       "SOS driverchild: created",
                       "SOS driverchild: gave the window",
                       "SOS driverchild: started",
                       "SOS echo: window mapped",
                       "SOS echo: driver up",
                       ECHO_INPUT,
                       "SOS echo: done 4 bytes on line 33",
                       # The same six, on the other machine and the other chip:
                       # what differs between the two packages is the datasheet.
                       "SOS: process teardown handles={six} threads={one} "
                       "events={zero} waiters={one} interrupts={one} "
                       "timers={zero} process={one}",
                       "SOS driverchild: root observed child status=65536",
                       "SOS driverchild: done"],
        "expect_clean_exit": True,
    },
    {
        # Shared memory: one region, two rows, two address spaces. §2.5's own
        # words ("the same physical memory ... the shared-memory primitive") as
        # a transcript, and the first cross-process double map.
        #
        # Both directions, and each is proved twice. Root writes 0xA5 through
        # its own row; the child says it saw 165 and carries that byte out as
        # its exit code, which root reads back as the §8 status word
        # (`Exited`(1) << 16 | 165 = 65701). The child writes 0x5A; root loads
        # it back out of the same page. On a machine that does not translate
        # there is no copy step for either fact to hide in.
        #
        # Root installs the child's row, and the child holds no Memory handle,
        # in deliberate contrast with the two echo cases above, where root gave
        # the capability away and the child installed its own row. Access
        # without possession, beside possession-then-install: the object model
        # answers both without a mode flag anywhere.
        "name": "share_double_map",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": SHARE_DOUBLE_MAP_PKG,
        "children": [CHILD_SHARE_PKG],
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={three}",
                       "SOS sharemap: created",
                       "SOS sharemap: mapped into both",
                       "SOS sharemap: wrote 165",
                       "SOS sharemap: started",
                       "SOS childshare: saw 165 wrote 90",
                       "SOS: process exit: code={share_mark} process={one}",
                       # `handles={two}` is the claim of this case, in the
                       # kernel's own accounting. The child's entire estate is
                       # the masked System it was given and the Process handle
                       # it derived from it. There is no Memory handle among
                       # them and no Mapping either: the row it read and wrote
                       # through belongs to a region its launcher still holds,
                       # installed by that launcher into this process's domain.
                       # Access without possession, counted.
                       "SOS: process teardown handles={two} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS sharemap: root observed child status=65701",
                       "SOS sharemap: read back 90",
                       "SOS sharemap: done"],
        "expect_clean_exit": True,
    },
    {
        # Executable is an authority. Root maps a page read-execute through its
        # full pool handle and it installs; root then mints a sibling with
        # `MemoryRight.MapExecute` masked out, proves that sibling is a working
        # handle by mapping read-write through it, and asks for read-execute
        # through it, which ends the process.
        #
        # The read-write line is what makes the refusal mean anything: without
        # it, a sibling broken in any of a dozen ways would produce the same
        # `access denied`. One handle, two asks, one refusal; the difference is
        # the bit.
        #
        # This is split across cases rather than one. A single case with three
        # arms (root maps X successfully, a mint-without-`MapExecute` is
        # refused, `W|X` in one row is refused) cannot work: two of those arms
        # are faults, and a fault ends the process ("a second probe after one
        # would never run"). So the arms are split in two: this case, whose
        # positive arm and refusal are both about the right, and
        # `map_wx_refused` below, whose positive arm and refusal are both about
        # the word.
        "name": "map_exec_gated",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": MAP_EXEC_GATED_PKG,
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={one}",
                       "SOS execgate: mapped executable",
                       "SOS execgate: minted a sibling without MapExecute",
                       "SOS execgate: the sibling maps read-write",
                       "SOS execgate: asking for execute through it",
                       "SOS: process fault: access denied",
                       # Nine, and the arithmetic is the program: §12's three
                       # boot handles, the pool region, the Process handle
                       # `process_self` minted, the page the split cut off, the
                       # executable Mapping, the narrowed sibling, and the
                       # read-write Mapping it installed. The refused map
                       # minted nothing, which is what a fault before any
                       # allocation means.
                       "SOS: process teardown handles={nine} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={zero}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # W^X, per row. One region is mapped read-write here and read-execute
        # there (the sanctioned double map), so the JIT-shaped pattern survives
        # in full while every individual protection row is W^X.
        #
        # The refusal goes through root's full pool handle, which carries
        # `MemoryRight.MapExecute` and has just proved it. So this cannot be
        # the exec gate wearing a different hat: every right is present and the
        # word is what is wrong, which is what `argument outside its domain`
        # says and `access denied` would not.
        "name": "map_wx_refused",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": MAP_WX_REFUSED_PKG,
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={one}",
                       "SOS mapwx: read-write here, wrote 107 read 107",
                       "SOS mapwx: read-execute there: one region, two rows",
                       "SOS mapwx: asking for write and execute in one row",
                       "SOS: process fault: argument outside its domain",
                       # Eight: `map_exec_gated`'s nine minus the sibling it
                       # minted, since this case narrows nothing. Two Mappings
                       # are among them, over one region, which is the double
                       # map counted rather than merely printed.
                       "SOS: process teardown handles={eight} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={zero}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    # =========================================================================
    # Waiter revocation
    # =========================================================================
    {
        # Nothing parked can be silently doomed (the peer-gone doctrine).
        # Releasing the last handle to a Waiter a sibling thread is parked on
        # wakes that thread with a status that names what happened, rather than
        # leaving it Blocked forever.
        #
        # Three claims in one ordered transcript, and the order carries two of
        # them. Nothing in this image can move the processor except a thread
        # giving it up (the kernel booting a root server arms no timer, and
        # `start` only makes a thread runnable), so:
        #
        #   both `parking` lines      the two threads really are on one
        #                             Waiter's blocked list
        #   `sibling released, nobody woke`
        #                             printed after a yield that offered both
        #                             workers the processor, so its position is
        #                             the claim that the count gates the
        #                             revocation and not the release: 2 -> 1
        #                             frees nothing
        #   two `woke` lines, one release
        #                             the walk is the whole list, not the pop a
        #                             readiness does (§2.2's distribution,
        #                             which `event-wake` proves on the other
        #                             side)
        #
        # `b` before `a` is the blocked list being a stack (`block_on_wait`
        # pushes at the head), which this transcript happens to show and
        # nothing promises.
        #
        # The status is named, not numbered. The program decodes the raw word
        # through `SosStatus.from(raw:)`, so the words on the console are the
        # ABI enum's own `describe()` and a case added without a describe arm
        # would not compile.
        #
        # `waiters={zero}` in the teardown is the other half of "zero frees,
        # synchronously": the slab sweep found nothing left, because the
        # release freed it.
        "name": "waiter_revoked",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": WAITER_REVOKED_PKG,
        "expect_out": ["{banner}",
                       "SOS revoked: a parking",
                       "SOS revoked: b parking",
                       "SOS revoked: sibling released, nobody woke",
                       "SOS revoked: b woke: what this call was waiting for is gone",
                       "SOS revoked: a woke: what this call was waiting for is gone",
                       "SOS revoked: joined a=41 b=42",
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero}"],
        "expect_clean_exit": True,
    },
    # =========================================================================
    # The pair
    # =========================================================================
    {
        # The data path, in one process. Root holds both ends, so every claim
        # below is about the connection rather than about scheduling: nothing
        # parks, nothing is given, and the transcript is a list of properties.
        #
        # `round trip len=3 b=65,66,67` is the bytes, not a status: the copy-in
        # and copy-out funnels really carried the body, which a status alone
        # would not have shown.
        #
        # `fifo order=123` carries the order as digits (`child-drain`'s
        # `tags=35` idiom), so a ring that handed back the newest first would
        # print 321.
        #
        # `empty message len=0` and `take empty is none` are the two halves of
        # one distinction: a data-free message is a message (§2.1's own
        # option), and the typed tier's `Optional` is what keeps it apart from
        # "nothing staged". A shape that could not tell them apart would make
        # the poll loop below unwritable.
        #
        # `ring depth=16` is a claim about a build constant this program cannot
        # name. `PIPE_INFLIGHT` is `2 * MAX_THREADS` in `kcore.limits` and is
        # deliberately not published to userspace; root posts until the kernel
        # refuses and counts. `drained=16` beside it says the refusal was a
        # refusal: a ring that dropped the oldest to make room would drain
        # fewer than it staged.
        "name": "pipe_basics",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_BASICS_PKG,
        "expect_out": ["{banner}",
                       "SOS pipebasics: created",
                       "SOS pipebasics: round trip len=3 b=65,66,67",
                       "SOS pipebasics: fifo order=123",
                       "SOS pipebasics: empty message len=0",
                       "SOS pipebasics: ring depth=16",
                       "SOS pipebasics: drained=16",
                       "SOS pipebasics: take empty is none",
                       "SOS pipebasics: done"],
        "expect_clean_exit": True,
    },
    {
        # The peer-gone doctrine, and the two directions are not symmetric. Two
        # connections, killed from opposite ends, and the difference between
        # the two answers is the sharpest claim.
        #
        # The outlet goes: `post says …` at once. Nobody can ever take again,
        # so there is no future for a staged message and the refusal is
        # immediate.
        #
        # The inlet goes: `drained a=7 b=8` first, and only then `take says …`.
        # Everything the departed sender staged is handed over before the
        # server is ever told (close drains first), so a client exiting
        # mid-conversation loses nothing. A kernel that unwound the ring at the
        # inlet's zero would print the terminal line with no drain before it.
        #
        # `again says …` is terminal level: nothing un-closes, so a caller may
        # stop rather than retry. That separates `PeerClosed` from `WouldBlock`,
        # which `pipe_basics` meets on the same two ops.
        #
        # The statuses are named, not numbered: the program prints the ABI
        # enum's own `describe()`, so a case added without a describe arm would
        # not compile and a renumbering of the wire table would not move this
        # transcript.
        "name": "pipe_peer_gone",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_PEER_GONE_PKG,
        "expect_out": ["{banner}",
                       "SOS peergone: outlet dropped",
                       "SOS peergone: post says the other end of this "
                       "connection is gone",
                       "SOS peergone: staged 2",
                       "SOS peergone: inlet dropped",
                       "SOS peergone: drained a=7 b=8",
                       "SOS peergone: take says the other end of this "
                       "connection is gone",
                       "SOS peergone: again says the other end of this "
                       "connection is gone",
                       "SOS peergone: done"],
        "expect_clean_exit": True,
    },
    {
        # What a message's carried handles do on each outcome (spec 3). A
        # terminal refusal consumes them: each send op is aimed at a peer that
        # is gone, carrying the only outlet of a second connection, and the
        # case then asks that connection's inlet, which answers `PeerClosed`
        # because the outlet was released and not merely forgotten. A transient
        # refusal keeps them: a `post_with` into a full ring answers
        # `WouldBlock`, the same carrier posts once there is room, and the
        # inlet that arrives is live. The last arm parks a child's `send_with`
        # in a full ring with an outlet aboard and closes the server end under
        # it, which resumes the call with `PeerClosed` and releases the outlet.
        "name": "pipe_consume",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_CONSUME_PKG,
        "children": [CHILD_PARKED_PKG],
        "expect_out": ["{banner}",
                       "SOS consume: post_with released the carried outlet",
                       "SOS consume: send_with released the carried outlet",
                       "SOS consume: reply_with released the carried outlet",
                       "SOS consume: reply_wait_with released the carried "
                       "outlet",
                       "SOS consume: WouldBlock kept the carried inlet",
                       "SOS consume: the retry delivered a live inlet",
                       "SOS consume: NoResource kept the carried inlet",
                       "SOS consume: the table retry delivered a live inlet",
                       "SOS childparked: calling",
                       "SOS consume: closing the server end under a parked "
                       "call",
                       "SOS childparked: call said PeerClosed",
                       "SOS consume: a parked send_with released the carried "
                       "outlet",
                       "SOS consume: done"],
        "expect_clean_exit": True,
    },
    {
        # A connection that crosses a process boundary: the seed of the service
        # client. Root creates the pair, keeps the outlet, gives the inlet, and
        # the child posts through it.
        #
        # The launch flow is unchanged, because the pipe factory is a Process
        # op: mint a masked System sibling, give it, give the pipe end, start
        # naming the boot tag. The only new thing is the kind that travelled,
        # and `Transfer` is minted in `pipe_inlet_rights()` for exactly this.
        #
        # The order is the claim. `SOS childpost: posted`, the child's exit and
        # its teardown all come before root reads the bytes: root's first take
        # necessarily answered `Ok(None)` (the child was runnable and had not
        # run), so root parked on a timer and the child ran to completion. So
        # the message outlives its sender, which is `pipe_peer_gone`'s
        # drain-first rule proved across a real process boundary.
        #
        # `code={four}` and `len=4` are the same fact from the two sides of the
        # boundary: the child exits with its payload's length, and root reads
        # that many bytes. `b=80,73,80,69` is `PIPE` in ASCII: the bytes, so
        # that a status alone could not have passed this case.
        "name": "pipe_child",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_CHILD_PKG,
        "children": [CHILD_POST_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS pipechild: created",
                       "SOS pipechild: gave the inlet",
                       "SOS pipechild: started",
                       "SOS childpost: posted",
                       "SOS: process exit: code={four} process={one}",
                       "SOS: process teardown handles={two} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS pipechild: received len=4 b=80,73,80,69",
                       "SOS pipechild: done"],
        "expect_clean_exit": True,
    },
    {
        # An unspent right on a connection end (spec §3). An endpoint's default
        # set is permissive and has to be, since attenuation is monotonic and a
        # bit not minted at creation could never appear later, so the narrowing
        # is the holder's, written once at a `MINT_OP` keep mask.
        #
        # One transcript, two claims, exactly as `give_no_transfer` makes them:
        # a masked sibling of a pipe end is genuinely narrower than its source,
        # and the op's gate is real. The mask names `Transfer | Mint` and says
        # nothing about `Post`, which is what a keep mask's polarity means:
        # absent by silence, and no sequence of ops recovers it.
        "name": "pipe_no_post",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_NO_POST_PKG,
        "expect_out": ["{banner}",
                       "SOS nopost: minted without Post",
                       "SOS: process fault: access denied process={zero}",
                       "SOS: process teardown handles="],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # A body bigger than the slot (spec §2.1's fixed body). The maximum is
        # published in `sosabi` (a caller cannot even declare the buffer
        # without it), so overshooting it is a mistake the process could have
        # checked, which makes it a fault rather than a status.
        #
        # The check is before the copy, which makes the refusal safe rather
        # than merely correct: nothing is read out of the caller's memory and
        # no staging slot is touched, so a hostile length is a diagnostic and
        # never a partial write.
        "name": "pipe_oversized",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_OVERSIZED_PKG,
        "expect_out": ["{banner}",
                       "SOS oversized: asking for 129",
                       "SOS: process fault: argument outside its domain "
                       "process={zero}",
                       "SOS: process teardown handles="],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    # =========================================================================
    # The one-shot pair
    # =========================================================================
    {
        # The whole exchange, in one process. Root plays both sides, so every
        # row below is about the pair rather than about scheduling: nothing
        # parks, nothing is given.
        #
        # `request len=3 b=65,66,67` is the take answering two things: the
        # bytes, and (invisibly here, provably below) the obligation to reply
        # to them.
        #
        # `pending is not ready` is `PipeReply.ready()`, a non-consuming level
        # question. A `resolve` here would park; `ready()` consumes nothing, so
        # the same claim asks again and resolves afterwards, and readiness is
        # monotonic, so the `false` this row prints can only ever become a
        # `true`.
        #
        # `reply len=2 b=90,89` and `zero reply len=0` are the two halves of
        # one distinction on the way back: a reply that carries no data is an
        # answer, not an absence, mirroring the request side's `empty message
        # len=0`.
        #
        # `fifo replies a=11 b=22 c=33` is three exchanges in flight at once,
        # answered out of order (2, 3, 1) and resolved in order. A kernel with
        # one reply slot per connection rather than per exchange would print
        # the same digit three times; one that keyed replies by queue position
        # would print them permuted.
        #
        # `depth with one in flight=15` and `depth settled=16` are the sharpest
        # claim. A ring slot lives until its exchange settles, not until its
        # bytes are taken, so one unsettled exchange costs one slot, and
        # discharging it gives the slot back. The program never names
        # `PIPE_INFLIGHT`: it counts twice, around a held exchange, and the
        # difference is what a kernel that freed at the take could not produce.
        "name": "pipe_oneshot",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_ONESHOT_PKG,
        "expect_out": ["{banner}",
                       "SOS oneshot: created",
                       "SOS oneshot: request len=3 b=65,66,67",
                       "SOS oneshot: pending is not ready",
                       "SOS oneshot: reply len=2 b=90,89",
                       "SOS oneshot: zero reply len=0",
                       # The sweep row: the smallest complete exchange in the
                       # file, counted. Post, take, reply, resolve are four ops,
                       # plus one for the closing `stats()` call, which `ktrap`
                       # charges at trap entry before the op answers. The
                       # bracket contains no printing (a printed byte is an
                       # `ecall`) and no wrapper falling out of scope (a release
                       # is an `ecall` too), which makes five arithmetic rather
                       # than observation.
                       "SOS oneshot: the exchange cost 5 traps",
                       "SOS oneshot: fifo replies a=11 b=22 c=33",
                       "SOS oneshot: depth with one in flight=15",
                       "SOS oneshot: depth settled=16",
                       "SOS oneshot: done"],
        "expect_clean_exit": True,
    },
    {
        # Abandonment is information, not an imperative (spec §2.1). Three ways
        # an exchange ends with nobody on the other side, and every one is a
        # delivered answer:
        #
        #   `reply says …`         the client dropped its claim, and the
        #                          server's obligation is discharged all the
        #                          same: §2.1's "never a silent success"
        #   `resolve says …`       the server dropped its obligation, and the
        #                          client is told rather than left waiting
        #   `staged claim says …`  the connection's server end went while the
        #                          message was still staged, so the obligation
        #                          could never be minted
        #
        # The third is the derived one, and is why the columns are the state:
        # the kernel reads `Staged` plus an outlet column at zero, with no flag
        # anywhere saying "abandoned".
        #
        # The statuses are named, not numbered, as in `pipe_peer_gone`.
        "name": "pipe_abandon",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_ABANDON_PKG,
        "expect_out": ["{banner}",
                       "SOS abandon: created",
                       "SOS abandon: reply says the other end of this "
                       "connection is gone",
                       "SOS abandon: resolve says the other end of this "
                       "connection is gone",
                       "SOS abandon: staged claim says the other end of this "
                       "connection is gone",
                       "SOS abandon: done"],
        "expect_clean_exit": True,
    },
    {
        # The delegation primitive, across a real boundary: §2.1's zero-copy
        # example. Root asks its own question, takes the obligation off its own
        # outlet, and gives that obligation to a child that holds no end of the
        # connection at all.
        #
        # What the child cannot do is the proof. It cannot post, cannot take
        # and cannot name root's pipe; the obligation is the whole of its
        # authority, and it knows where the answer goes. That is the filesystem
        # forwarding a read to the flash driver, at its smallest.
        #
        # The order is the claim. `SOS childreply: replied`, the child's exit
        # and its teardown all come before root reads the bytes: root's first
        # look for the reply necessarily finds none (the child was runnable and
        # had not run), so root parked on a timer and the child ran to
        # completion. So a reply outlives the process that wrote it, which is
        # what putting it in the exchange's own ring slot buys.
        #
        # `code={four}` and `len=4` are the same fact from the two sides of the
        # boundary, and `b=68,79,78,69` is `DONE` in ASCII: the bytes, so that
        # a status alone could not have passed this case.
        "name": "pipe_delegate",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_DELEGATE_PKG,
        "children": [CHILD_REPLY_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS delegate: created",
                       "SOS delegate: gave the request",
                       "SOS childreply: replied",
                       "SOS: process exit: code={four} process={one}",
                       "SOS: process teardown handles={two} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS delegate: reply len=4 b=68,79,78,69",
                       "SOS delegate: done"],
        "expect_clean_exit": True,
    },
    {
        # An obligation has one name: the mirror of `pipe_dead_claim`'s probe
        # on the server half. `pipe_request_rights()` withholds the universal
        # `Mint` bit, so a sibling of an obligation cannot be made and asking
        # for one is `pipe_no_post`'s fault at a different kind.
        #
        # A one-shot is the exception to the uniform lean, and only because it
        # is spent by one op: a second name is not a second way to use the
        # object, it is a handle whose only possible answer is `PeerClosed`, and
        # it undermines the compile-time single use that `consumes` delivers on
        # all three one-shot ops.
        #
        # Delegation is untouched: §2.1's forwarding primitive is `Transfer`
        # moving the one name (`pipe_delegate`, `pipe_delegate_msg`), and
        # narrowing what a delegate may do is `give`'s keep mask
        # (`give_keep_mask`).
        #
        # The `Reply` gate is reachable only through a `give` keep mask into
        # another process, which is a two-process case; no case here covers it.
        "name": "pipe_no_reply",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_NO_REPLY_PKG,
        "expect_out": ["{banner}",
                       "SOS noreply: minting a sibling of an obligation",
                       "SOS: process fault: access denied process={zero}",
                       "SOS: process teardown handles="],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # A reply bigger than the slot (spec §2.1's fixed body):
        # `pipe_oversized`'s line running outward. The reply rides the same
        # staging slot the request came in on, so one published maximum governs
        # both directions and overshooting it is a mistake the process could
        # have checked.
        #
        # The check is before the copy and before the consume, which is the part
        # worth a case of its own: a refused reply leaves the obligation
        # undischarged, so a hostile length cannot quietly close somebody's
        # exchange.
        "name": "pipe_big_reply",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_BIG_REPLY_PKG,
        "expect_out": ["{banner}",
                       "SOS bigreply: asking for 129",
                       "SOS: process fault: argument outside its domain "
                       "process={zero}",
                       "SOS: process teardown handles="],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # A reply cannot carry the request handle it is invoked through
        # (spec.md §3). The reply consumes that handle, so a record naming it
        # would stage and consume one entry; the kernel refuses it as a caller
        # error before it touches the exchange.
        "name": "reply_carries_self",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_REPLY_SELF_PKG,
        "expect_out": ["{banner}",
                       "SOS replyself: replying with the request in its own "
                       "record",
                       "SOS: process fault: argument outside its domain "
                       "process={zero}",
                       "SOS: process teardown handles="],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # `reply_carries_self`'s rule at the fused op, whose message arrives
        # nested in a larger record and reaches the same check by a different
        # call. The fault comes before the wait leg, so nothing parks.
        "name": "reply_wait_carries_self",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_REPLY_WAIT_SELF_PKG,
        "expect_out": ["{banner}",
                       "SOS replywaitself: reply_wait with the request in "
                       "its own record",
                       "SOS: process fault: argument outside its domain "
                       "process={zero}",
                       "SOS: process teardown handles="],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # A connection's outlet cannot travel through its own inlet (spec.md
        # §3). A staged reference on the outlet would keep the connection from
        # ever freeing once the sender dropped its inlet and claim, so the
        # kernel refuses the post as a caller error before staging anything.
        "name": "pipe_self_outlet",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_SELF_OUTLET_PKG,
        "expect_out": ["{banner}",
                       "SOS selfoutlet: posting the outlet through its own "
                       "inlet",
                       "SOS: process fault: argument outside its domain "
                       "process={zero}",
                       "SOS: process teardown handles="],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # `pipe_self_outlet`'s rule at the resume of a parked call. The call
        # parks on a full ring; a sibling thread rewrites its args record to
        # carry the connection's own outlet; a take makes room and the kernel
        # re-reads the record. The caller is blocked, so nothing faults: the
        # slot arrives empty, the outlet stays in the caller's table, and the
        # connection frees once the ends are dropped.
        "name": "pipe_parked_rewrite",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_PARKED_REWRITE_PKG,
        "expect_out": ["{banner}",
                       "SOS parkedrewrite: the call is parked",
                       "SOS parkedrewrite: the record names the outlet",
                       "SOS parkedrewrite: the message arrived without it",
                       "SOS parkedrewrite: the connection was freed"],
        "expect_clean_exit": True,
    },
    {
        # A claim that can never be answered. A second `resolve` of a spent
        # claim is a compile error, because `resolve` consumes on every path and
        # says so in its effect slot; the kernel's `BadHandle` check stands for
        # a raw-altitude caller (see `pipe_reply_wait_dead`).
        #
        # The case proves the expected flow in three rows: the server drops its
        # obligation, the client's `ready()` answers the terminal on the error
        # channel, and the claim is dropped rather than resolved. A reply that
        # can never come needs no second call, so the terminal does not sit
        # where a caller's instinct is to ask once more.
        #
        # The mint probe ends it: a one-shot has one name, so
        # `pipe_reply_rights()` withholds the universal `Mint` bit and asking
        # for a sibling is `pipe_no_post`'s fault at a different kind. It is the
        # last act deliberately: everything above it is the flow, and the flow
        # reaches its own end before the probe spends the process.
        "name": "pipe_dead_claim",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_DEAD_CLAIM_PKG,
        "expect_out": ["{banner}",
                       "SOS deadclaim: the server let its obligation go",
                       "SOS deadclaim: ready says the other end of this "
                       "connection is gone",
                       "SOS deadclaim: claim dropped unresolved",
                       "SOS deadclaim: minting a sibling of a one-shot",
                       "SOS: process fault: access denied process={zero}",
                       "SOS: process teardown handles="],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    # =========================================================================
    # Waitability
    # =========================================================================
    {
        # The reply arm: the wait is the resolve. The claim is attached and the
        # bytes arrive in the wait record.
        #
        # `reply len=2 b=90,89` is the record's body region carrying a reply
        # out of the ring slot that held it: the second of the two copies
        # `deliver_wait_record` makes.
        #
        # `after delivery ...` is the sharper claim: the exchange was discharged
        # by the copy-out, so the claim the caller kept answers `PeerClosed`. A
        # kernel that left the slot `Replied` would hand the same answer out
        # twice, the duplicate §2.2's consume rule exists to make impossible.
        #
        # `keyed a=11 b=22` is two claims on one Waiter, asserted by key and
        # never by arrival order. A design with one reply slot per connection
        # would print one digit twice; one that keyed answers by queue position
        # would print them swapped.
        "name": "pipe_wait_reply",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_WAIT_REPLY_PKG,
        "expect_out": ["{banner}",
                       "SOS waitreply: created",
                       "SOS waitreply: reply len=2 b=90,89",
                       "SOS waitreply: after delivery the other end of this "
                       "connection is gone",
                       "SOS waitreply: keyed a=11 b=22",
                       "SOS waitreply: done"],
        "expect_clean_exit": True,
    },
    {
        # The consuming attach. `give` takes the wrapper by move and the
        # attachment owns the exchange; the caller holds nothing at all while
        # the answer is in flight.
        #
        # `depth after delivery=16` is the row that could not be faked.
        # `pipe_oneshot` showed a held exchange costing one ring slot and
        # settling giving it back; this counts the same ring after a delivery to
        # a consuming attachment and gets the whole depth, so the delivery ended
        # the attachment, the entry and the exchange in one act, with no
        # `resolve` anywhere in the program.
        #
        # `cancelled reply says ...` is §2.1's abandonment sentence: with the
        # claim given away there is no handle to drop, so cancelling is
        # destroying the subscription, and the server learns exactly what the
        # ratified text says it learns.
        "name": "pipe_wait_give",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_WAIT_GIVE_PKG,
        "expect_out": ["{banner}",
                       "SOS waitgive: created",
                       "SOS waitgive: given reply len=1 b=7",
                       "SOS waitgive: depth after delivery=16",
                       "SOS waitgive: cancelled reply says the other end of "
                       "this connection is gone",
                       "SOS waitgive: done"],
        "expect_clean_exit": True,
    },
    {
        # The inlet's room-to-post level. `post` can never block, so a refused
        # poster parks on the client end instead of polling, and the payload
        # word says which readiness woke it.
        #
        # `filled=16 then room says there is space` counts the ring around
        # itself: the fill runs until the kernel refuses (so the level is
        # provably false), one exchange is settled, and the level is provably
        # true. The program never names `PIPE_INFLIGHT`; it asserts the
        # difference.
        #
        # `room says the peer is gone` is the other reading, and the order is
        # the claim: the ring is empty there, so a kernel that answered "space"
        # would be telling a poster to write into a connection nobody can ever
        # read. `PipeInletOp.Post` asks the same two questions in the same
        # order.
        "name": "pipe_wait_room",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_WAIT_ROOM_PKG,
        "expect_out": ["{banner}",
                       "SOS waitroom: created",
                       "SOS waitroom: filled=16 then room says there is space",
                       "SOS waitroom: room says the peer is gone",
                       "SOS waitroom: done"],
        "expect_clean_exit": True,
    },
    {
        # The server's two arms (spec §2.2's readable level; §2.1's
        # request-abandoned early notice).
        #
        # The two `readable says` rows are one ordering claim. The message is
        # staged and the whole client end is then dropped, so both facts are
        # true before either wait runs, and the level chooses the message. Only
        # after the take empties the ring does the second wait say the peer is
        # gone. That is close-drains-first at the wait door, and a kernel that
        # reported the terminal level as soon as the sender's last handle went
        # would lose a message that had already been sent.
        #
        # One persistent attachment answers both waits, which is also the
        # one-shot footgun stated from the other side: a `OneShot` attachment
        # would have detached at the first delivery and the second wait would
        # have had an empty Waiter to park on forever.
        #
        # `abandoned` is the third arm; it carries no payload (the fact is the
        # notification) and it is terminal, so the attach happens after the
        # client gave up and is still told at once.
        "name": "pipe_wait_server",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_WAIT_SERVER_PKG,
        "expect_out": ["{banner}",
                       "SOS waitserver: created",
                       "SOS waitserver: readable says a message",
                       "SOS waitserver: readable says the peer is gone",
                       "SOS waitserver: abandoned",
                       "SOS waitserver: done"],
        "expect_clean_exit": True,
    },
    {
        # The blocking send written out of the primitives, with threads that
        # really park (spec §2.1's client API). Two processes, two blocked
        # threads, each waking the other exactly once.
        #
        # There is no library `send`. The composition is four calls this
        # program makes itself (post, attach, wait, and a Timer on the same
        # Waiter for the timeout leg), because a wrapper type would freeze a
        # shape the fused `Call` is meant to replace, and a timeout the kernel
        # ever genuinely needs arrives as an op. What the case proves is
        # therefore that the primitives suffice, a stronger claim than that a
        # wrapper works.
        #
        # The order is the proof. `SOS childserver: replied` comes before
        # root's reply row: root posted, attached its claim and parked, which
        # left the kernel nothing runnable but the child; the child parked on
        # its outlet and was woken by the post; its reply is what woke root. A
        # polling implementation of either half would print the same bytes with
        # none of that interleaving.
        #
        # `reply len=4 b=80,79,78,71` is `PONG` in ASCII, arriving in the wait
        # record's body region: there is no `resolve` anywhere on this path.
        #
        # `filled=16 then room says there is space` is the room leg, which a
        # library `send` would have hidden: the ring is filled first, so the
        # poster is refused and has to park on the inlet's level before it has
        # a claim at all. `pipe_wait_room` proves the level exists; this proves
        # a caller composing a blocking send has to use it.
        #
        # `timed out` is the same connection with nobody serving it: the Timer
        # on the same Waiter is what ends the wait, which is the whole of what a
        # timeout is here, and the kernel still knows nothing about durations.
        # The key is what resolves the race, and the bookkeeping after it
        # (remove the loser, keep the claim) is the caller's three lines. `after
        # cancel ...` is what the still-live claim being dropped means: §2.1's
        # cancellation primitive, and the server told rather than left to
        # succeed silently.
        "name": "pipe_send_manual",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_SEND_MANUAL_PKG,
        "children": [CHILD_SERVER_PKG],
        # The child serves two messages (the second is the fused turn below),
        # so it does not die at its first reply. It prints `replied` after its
        # first turn and exits with `code=4`, but root's report of the first
        # round precedes the child's death rather than following it.
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS sendmanual: gave the outlet",
                       "SOS childserver: replied",
                       "SOS sendmanual: reply len=4 b=80,79,78,71",
                       # The sweep's headline row. The composition a caller
                       # writes by hand is post, attach, wait (three ops), and
                       # the fourth trap is the closing `stats()` call, charged
                       # at trap entry before it answers. The number shows that
                       # the wait parked, that a whole cross-process round trip
                       # happened underneath it (the child was scheduled, woke
                       # on its outlet, took and replied), and that root paid
                       # one trap for all of it: a park is one `ecall` however
                       # long it parks, because the wake writes the answer into
                       # the parked frame rather than re-entering through the
                       # door.
                       "SOS sendmanual: the round trip cost 4 traps",
                       "SOS: process exit: code={four} process={one}",
                       "SOS: process teardown handles={four} threads={one} "
                       "events={zero} waiters={one} interrupts={zero} "
                       "timers={zero} process={one}",
                       # The companion rows: the same exchange, the same
                       # server, the same four bytes, done the other way and
                       # measured in the same boot by the same process.
                       # `ops manual=3 fused=1` is post + attach + wait against
                       # `send`. The fused call also mints nothing, which the
                       # numbers do not show: the composition holds a
                       # `PipeReply` handle for the length of the exchange and
                       # the fused path holds no table row at all.
                       "SOS sendmanual: fused reply len=4 b=80,79,78,71",
                       "SOS sendmanual: ops manual=3 fused=1",
                       "SOS sendmanual: filled=16 then room says there is space",
                       "SOS sendmanual: timed out",
                       "SOS sendmanual: after cancel the other end of this "
                       "connection is gone",
                       "SOS sendmanual: done"],
        "expect_clean_exit": True,
    },
    {
        # An unspent `Wait` right (spec §3): `pipe_no_post`'s claim at the wait
        # door. All four pipe kinds have a `Wait` bit in their default sets,
        # because attenuation is monotonic and a bit not minted at creation
        # could never appear later, so the narrowing is the holder's, written
        # once at a `MINT_OP` keep mask.
        #
        # The mask is the policy. `Transfer | Mint | Take` with `Wait` absent
        # describes a polling helper: a process handed work to drain and
        # deliberately not handed the authority to park on somebody else's
        # connection.
        "name": "pipe_no_wait",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_NO_WAIT_PKG,
        "expect_out": ["{banner}",
                       "SOS nowait: minted without Wait",
                       "SOS: process fault: access denied process={zero}",
                       "SOS: process teardown handles="],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # A flags word outside its domain (spec §5.7's faults rule).
        # `WaiterOp.Add` takes a flags argument and publishes exactly two
        # bits; a word carrying any other names a behaviour the kernel has no
        # reading of, and the set is published, so it is a mistake the caller
        # could have checked.
        #
        # It reaches the C altitude, because the typed surface cannot spell an
        # invalid mode (`AttachMode` has two cases and consuming is a verb), so
        # the four legal combinations are the only things a Saw caller can ask
        # for. The kernel's check is still real underneath, and this is the
        # altitude at which a raw word legitimately exists to test it.
        "name": "pipe_bad_mode",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_BAD_MODE_PKG,
        "expect_out": ["{banner}",
                       "SOS badmode: asking for flags 4",
                       "SOS: process fault: argument outside its domain "
                       "process={zero}",
                       "SOS: process teardown handles="],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # Exact trap introspection. The four rows below are the claims.
        #
        # `self delta=9 over 8 probes` is arithmetic, not observation. Root
        # brackets a window containing exactly eight `get_status` traps and
        # nothing else (no print, no mint, no allocation), and the delta is
        # nine because the closing `stats()` is itself a syscall and `ktrap`
        # charges it at trap entry, ahead of the dispatch. The window is
        # print-free on purpose: every printed byte is one `ecall`, so a
        # measured window containing a `print` would count the prose and the
        # assertion would break on a wording change rather than on a kernel
        # change.
        #
        # `child syscalls=8 faults=0` is read after the child is dead. The
        # handle root created the child with outlives it (the record is held
        # open while anything names it, with the counters the teardown copied
        # into it), which is the only way the fault column is ever observable,
        # since a faulting process is not around to ask about itself. Eight is
        # `child-stats`' whole life: one `process_self`, six probes, one `exit`,
        # and that child prints nothing precisely so the number is not a hash
        # of its own prose. Zero faults is the negative half of the same claim.
        #
        # `interrupts=` is the one timing-dependent row in this case and is
        # deliberately not asserted: a tick lands where the host puts it, so
        # asserting the value would be asserting the weather. It is printed
        # because the column is real and a reader wants to see what a userspace
        # `top` would read. The value is smaller than a reader expects: an
        # interrupt taken while the kernel is idle never reaches `ktrap`,
        # because a parked root idles in `idle_until_runnable`, which polls the
        # controller rather than taking a vector. The column counts interrupts
        # that preempted this process while it was running.
        #
        # The last two rows are the right being real. Root mints a sibling of
        # its own Process handle through a mask naming `Transfer | Mint | Wait`
        # and never `Stats`: "you may learn whether this process is alive, and
        # you may not watch it work", which is why the authority is a bit of
        # its own rather than a second use of `Wait`. The read through it is
        # `AccessDenied`, and it is a fault rather than a status because a
        # process knows which rights it holds. So this case ends the way
        # `pipe_no_post` and `pipe_no_wait` end.
        "name": "process_stats",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PROCESS_STATS_PKG,
        "children": [CHILD_STATS_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS stats: self delta=9 over 8 probes",
                       "SOS: process exit: code={seven} process={one}",
                       "SOS stats: child syscalls=8 faults=0",
                       "SOS stats: minted without Stats",
                       "SOS: process fault: access denied process={zero}",
                       "SOS: process teardown handles="],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # The ladder's performance claim, as a transcript row: a ping-pong case
        # that counts traps.
        #
        # Root serves and the child clients, and the topology is deliberate.
        # The client's measured window is its whole life, so the client has to
        # be silent (every printed byte is one `ecall`), and a silent process
        # cannot report its own number, so its launcher reads the column
        # through the handle that outlives it. Reversed, the exactly-asserted
        # count would have belonged to the process that also has to print the
        # report.
        #
        # The loop ends by itself, which is why the arithmetic is clean. The
        # child's exit takes the last inlet handle with it, the outlet's
        # readable level turns terminal, and root is already parked in the wait
        # that delivers `PeerGone`, so termination costs zero extra traps and
        # needs no counter, no timer and no sentinel message. The peer-gone
        # doctrine is the loop's exit condition.
        #
        # `client traps=11 for 8 round trips` is `process_self` + one
        # `boot_handle_next` + eight `send` calls + `exit`: one trap per RPC,
        # where the composed spelling `pipe_send_manual` writes out is three
        # (post, attach, wait). The fused client also holds zero handles for its
        # claims.
        #
        # `server traps=18 for 8 messages` is one opening `wait`, then a `take`
        # and a `reply_wait` per message, plus the closing read's own trap. The
        # server is two per message and not one, and the second is the `take`:
        # the readable level says "there is something", never "here it is", so
        # the take stays its own op. What the fusion removed is exactly one:
        # the composed loop is wait + take + reply, three. Delivery-as-take
        # removes the rest; see `pipe_cq`.
        #
        # `last request b=7,80,73,78` is round 7's `PIN` with its sequence
        # number, and `code=0x...21` is the child's verdict that every one of
        # the eight replies carried its own number plus one. The two together
        # are the payload round trip proved from both ends.
        "name": "pipe_pingpong",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_PINGPONG_PKG,
        "children": [CHILD_PINGPONG_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS: process exit: code={pingpong_mark} process={one}",
                       "SOS pingpong: served=8 last request b=7,80,73,78",
                       "SOS pingpong: client traps=11 for 8 round trips",
                       "SOS pingpong: server traps=18 for 8 messages",
                       "SOS pingpong: per RPC client=1 server=2"],
        "expect_clean_exit": True,
    },
    {
        # Nothing parked can be silently doomed, and a fused call is the
        # hardest case that rule has met. A thread inside `PipeInlet.send` is
        # on no list (no Waiter, no joiner chain), so the only thing that can
        # reach it is the exchange it is tied to, which is what
        # `Exchange.caller` and the one wake arm exist for.
        #
        # Two arms, two ways to kill an exchange, one answer:
        #
        #   `dropped the obligation`  the server took the message and then let
        #                             the request fall out of scope. The
        #                             exchange is `Taken` and the request
        #                             column reaches zero.
        #   `closed the server end`   the server end went away with the message
        #                             still staged and never taken at all. The
        #                             exchange is `Staged` and the connection's
        #                             outlet column reaches zero.
        #
        # Different states, different columns, and both have to produce
        # `PeerClosed` at the client, the same word `resolve` answers with,
        # which says the fused and composed paths report one fact.
        #
        # The second arm takes two steps, and the first is not tidiness: an
        # attachment is a counted reference on the end it watches, so root's
        # own subscription holds the server end open and the handle going is
        # not the column reaching zero. `remove` then drop. Hanging up means
        # destroying the subscription too.
        #
        # `child said both were closed` is read out of the §8 status word:
        # `code=0x...33` is reachable only if both calls came back with that
        # exact status, so root asserts a conjunction it could not have
        # observed itself.
        "name": "pipe_call_abandon",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_CALL_ABANDON_PKG,
        "children": [CHILD_CALLER_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS childcaller: calling",
                       "SOS callabandon: dropped the obligation",
                       "SOS callabandon: closed the server end",
                       "SOS: process exit: code={caller_mark} process={one}",
                       "SOS callabandon: child said both were closed"],
        "expect_clean_exit": True,
    },
    {
        # The orphaned-claim arm. A fused call's claim is the one reference in
        # the system no handle table holds, which is the saving, so
        # `end_process`'s close-all cannot reach it and the teardown needs an
        # arm of its own.
        #
        # A process is made to die with a thread parked, which nothing outside
        # it can arrange. SOS cannot kill a thread, so `child-orphan` is two
        # threads doing the same thing: one fused call each, and
        # `ProcessOp.Exit` if it is answered. Root answers exactly one, and the
        # symmetry makes that deterministic: root never has to know which
        # thread it is talking to.
        #
        # `two calls in flight` is both messages taken, which means both
        # threads are parked; the second take is separated from the first by a
        # park rather than a poll, because nothing is staged until the sibling
        # runs. `answered one` ends the process. `the orphaned claim answers
        # ...` is the arm: root still holds the second obligation, and its
        # `reply` is told `PeerClosed` (§2.1's abandoned-by-client answer),
        # with no new vocabulary, because the teardown dropped the claim and
        # the reply op reads the columns exactly as it does elsewhere.
        "name": "pipe_call_orphan",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_CALL_ORPHAN_PKG,
        "children": [CHILD_ORPHAN_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS callorphan: two calls in flight",
                       "SOS callorphan: answered one",
                       "SOS: process exit: code={orphan_mark} process={one}",
                       "SOS callorphan: the orphaned claim answers the other "
                       "end of this connection is gone"],
        "expect_clean_exit": True,
    },
    {
        # The fused call spends `PipeInletRight.Post`: `pipe_no_post`'s case at
        # the second op on the same object, and the pair says the two ops share
        # one gate. `Call` has no right of its own, deliberately: it submits a
        # message exactly as `post` does and adds a park, which needs no
        # authority because no Waiter is involved.
        #
        # It faults before it could ever park, which is worth a case of its own
        # for a blocking op: the check is in `pipe_inlet_op_decoded`, above
        # `pipe_call`, so nothing is staged and no thread is tied to an
        # exchange. A call that got past the gate here would park for ever
        # (nobody serves this connection) and the deadlock report would be the
        # answer instead of a fault.
        "name": "pipe_no_call",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_NO_CALL_PKG,
        "expect_out": ["{banner}",
                       "SOS nocall: minted without Post",
                       "SOS: process fault: access denied process={zero}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # A `send` longer than the published body is a fault:
        # `pipe_oversized`'s claim at the fused op. The maximum is a `sosabi`
        # constant the caller imports, so a length above it is a mistake it
        # could have checked.
        #
        # Both ends are held, so `PeerClosed` is not an answer this could have
        # got by accident: the length is refused ahead of the peer question and
        # ahead of the ring, which is the order `pipe_call` states.
        "name": "pipe_call_oversized",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_CALL_OVERSIZED_PKG,
        "expect_out": ["{banner}",
                       "SOS calloversized: calling with 129 bytes",
                       "SOS: process fault: argument outside its domain "
                       "process={zero}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # The reply leg fires, and it does not park (§2.1's abandonment
        # paragraph). `reply_wait` does two things in one trap, so the order of
        # its failures is a contract: a reply that cannot land returns at once
        # and the wait leg never runs, which keeps one abandoned exchange from
        # taking a server loop down with it.
        #
        # The empty Waiter is the assertion. Nothing is attached to the Waiter
        # the op is handed, so a park would have nothing to wake it and the
        # case would end `every thread blocked`, `wait_deadlock`'s own report.
        # Reaching `done` is the proof that no park happened.
        #
        # `reply` and `reply_wait` are `consumes` methods, so a second use
        # through a spent wrapper does not compile; a spent obligation
        # answering `BadHandle` is reachable only from a raw-altitude caller.
        "name": "pipe_reply_wait_dead",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_REPLY_WAIT_DEAD_PKG,
        "expect_out": ["{banner}",
                       "SOS replywaitdead: the client let its claim go",
                       "SOS replywaitdead: reply_wait says the reply did not "
                       "land: the other end of this connection is gone",
                       "SOS replywaitdead: done"],
        "expect_clean_exit": True,
    },
    {
        # A capability outlives the process that sent it. The child posts a live
        # connection end and exits at once; root parks on the death, then
        # takes, then posts through what arrived and reads its own byte back off
        # the far end. Every capability root spends there belongs to a process
        # that no longer exists.
        #
        # The sender's teardown count is asserted and is the write-off half of
        # the proof: two handles closed (its boot System and the Process it
        # derived). The gift is not among them, because it stopped being the
        # child's at the post (take-at-post); if the sender kept it, this row
        # would read three and the entry it closed would have been the one root
        # is about to be handed, so the case could not have finished at all.
        "name": "pipe_send_exit",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_SEND_EXIT_PKG,
        "children": [CHILD_SENDER_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS sendexit: created",
                       "SOS: process teardown handles={two} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS sendexit: the sender is gone",
                       "SOS sendexit: the dead sender's capability still works"],
        "expect_clean_exit": True,
    },
    {
        # The rendezvous refuses atomically. Three table states, and the middle
        # one (one free slot, still refused) is the row that separates an
        # atomic refusal from a partial delivery.
        "name": "pipe_table_full",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_TABLE_FULL_PKG,
        "expect_out": ["{banner}",
                       "SOS tablefull: probes=7 of 7",
                       "SOS tablefull: one free slot is not enough, two deliver "
                       "both"],
        "expect_clean_exit": True,
    },
    {
        # Handles in messages, both directions, with the ledger observed (spec
        # 2.1's send(data?, handles?)). The three free-slot readings are
        # printed and not asserted, because they are a property of root's own
        # table at three moments, while the verdict the case computes from them
        # is.
        "name": "pipe_handles",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_HANDLES_PKG,
        "children": [CHILD_HANDLES_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS handles: the post moved the entry and the take was "
                       "the child's",
                       "SOS handles: the answer came back through the "
                       "subscription"],
        "expect_clean_exit": True,
    },
    {
        # The delegation primitive, arriving as a message (spec 2.1's zero-copy
        # example). Root's own question is answered by a process that holds no
        # end of the connection it was asked on, and the four bytes prove the
        # payload never transited the middle.
        #
        # The round trip goes through two pipes rather than three processes:
        # the mechanism under test is a one-shot crossing a process boundary
        # inside a message, and the third party in 2.1's example differs from
        # this child in nothing the kernel can see. `pipe_delegate_3p` below is
        # the three-process spelling; the two prove different things: this one
        # that the mechanism is the message, that one that the middle is a
        # different principal.
        "name": "pipe_delegate_msg",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_DELEGATE_MSG_PKG,
        "children": [CHILD_HANDLES_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS delegatemsg: forwarded the obligation",
                       "SOS delegatemsg: reply len=4 b=68,79,78,69",
                       "SOS delegatemsg: the answer came from a process root "
                       "never told about the pipe",
                       "SOS delegatemsg: the reply carried an end back"],
        "expect_clean_exit": True,
    },
    {
        # The completion-queue server: one trap per message. The client is
        # `child-pingpong`, unchanged, so the server's trap count here and in
        # `pipe_pingpong` is measured over identical work.
        "name": "pipe_cq",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_CQ_PKG,
        "children": [CHILD_PINGPONG_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS cq: served=8 last request b=7,80,73,78",
                       "SOS cq: server traps=10 for 8 messages",
                       "SOS cq: per RPC client=1 server=1",
                       "SOS cq: the delivery was the take"],
        "expect_clean_exit": True,
    },
    {
        # Attenuate at give, and at the derive. The case ends on its own
        # negative arm (root reads stats through a handle it derived without
        # `Stats`), which is `pipe_no_post`'s shape and the only one available
        # when the fault kills the reader.
        "name": "give_keep_mask",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": GIVE_KEEP_MASK_PKG,
        "children": [CHILD_NARROW_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS childnarrow: posted through the wide end",
                       "SOS keepmask: a bit the source lacks was ignored, not "
                       "refused",
                       "SOS keepmask: the narrowed end could not post",
                       "SOS keepmask: derived a Process handle without Stats",
                       "SOS: process fault: access denied process={zero}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # A give refused for capacity hands the object back. Root gives masked
        # System siblings to a child it never starts until the child's handle
        # budget, `DEFAULT_QUOTA_HANDLES`, refuses one with `QuotaExceeded`;
        # `gave 12` is that budget. The refused sibling comes back in the error
        # payload and prints, which only a live handle can do.
        #
        # `handles={seven}` in root's teardown is the count the same program
        # ends with when it stops at the budget without meeting the refusal.
        # Root dropped the returned sibling, so its entry went too; a refusal
        # that left the entry bound with no wrapper naming it would read
        # `{eight}`.
        #
        # Only `QuotaExceeded` can be provoked through a give here, not
        # `NoResource`: the child's default handle budget is below its table
        # size, `MAX_HANDLES`, so the budget always refuses first.
        #
        # `child-bye` is only the image the child is created from; it never
        # runs, so no child line appears.
        "name": "give_full_returns",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": GIVE_FULL_RETURNS_PKG,
        "children": [CHILD_BYE_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS givefull: created",
                       "SOS givefull: gave 12 then refused: this process is "
                       "at its quota",
                       "SOS givefull: the refused System still speaks",
                       "SOS givefull: exiting",
                       "SOS: process teardown handles={seven} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={zero}"],
        "expect_clean_exit": True,
    },
    # =========================================================================
    # The one-shot discipline
    # =========================================================================
    {
        # Resolve parks. Root posts and then resolves, with no Waiter anywhere
        # in the process and no attachment at all: the resolve finds the
        # exchange pending and blocks the calling thread on the claim's own
        # ready level, and a child server's reply is what wakes it, inside its
        # own syscall, with the bytes in its own buffer.
        #
        # Reaching the end is the proof: a resolve that answered `WouldBlock`
        # on a pending exchange would have fallen through with no reply and
        # failed this program's own length check. There is nothing else in root
        # that could have produced the answer.
        #
        # The order is the second half of it, and it is `pipe_send_manual`'s:
        # `SOS childserver: replied` comes before root's row, because root
        # parked with nothing else runnable and the kernel had to go find the
        # child; the child parked on its outlet and was woken by root's post.
        #
        # `ops resolve=2 fused=1` is the sweep row: post plus resolve against
        # the fused `send`, measured by the same process against the same
        # server in the same boot, each delta less its own closing `stats()`
        # trap. The composed post, attach, wait is three for the same answer,
        # which is the trap this saves a client that wants a handle on its
        # exchange.
        "name": "pipe_resolve_park",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_RESOLVE_PARK_PKG,
        "children": [CHILD_SERVER_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS resolvepark: gave the outlet",
                       "SOS childserver: replied",
                       "SOS resolvepark: reply len=4 b=80,79,78,71",
                       "SOS resolvepark: fused reply len=4 b=80,79,78,71",
                       "SOS resolvepark: ops resolve=2 fused=1",
                       "SOS resolvepark: done"],
        "expect_clean_exit": True,
    },
    {
        # The orphaned-claim arm at a handle-backed claim: `pipe_call_orphan`
        # with the other spelling. The child's threads park inside a `resolve`
        # rather than inside a fused `send`, and the launcher answers one,
        # watches the process die, and asks the orphaned obligation what the
        # teardown decided.
        #
        # The row proves nothing was added. `park_resolve` unbinds the caller's
        # entry and keeps its reference, so from the park onward a parked
        # resolver and a parked fused caller are the same thing: one reference
        # on the claim column, `Exchange.caller` naming the thread.
        # `release_pending_calls` walks threads with a claim tie and never asks
        # which op put it there, so this case reaches the arm through the same
        # code.
        #
        # Why a sibling case and not an arm on `pipe_call_orphan`: the proof
        # needs the orphaned thread to be the shape under test, and one
        # two-thread child can orphan only one shape, whichever thread the
        # launcher answers being the one that exits. `child-orphan`'s two
        # threads are interchangeable on purpose, which makes that case
        # deterministic; making them differ would trade a fact for a scan
        # order.
        "name": "pipe_resolve_orphan",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_RESOLVE_ORPHAN_PKG,
        "children": [CHILD_RESOLVER_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS resolveorphan: two resolves in flight",
                       "SOS resolveorphan: answered one",
                       "SOS: process exit: code={resolve_orphan_mark} "
                       "process={one}",
                       "SOS resolveorphan: the orphaned claim answers the other "
                       "end of this connection is gone"],
        "expect_clean_exit": True,
    },
    # =========================================================================
    # Driver-as-service
    # =========================================================================
    #
    # The first pair is what the whole pipe ladder was for: one process owns
    # the UART and two others read it through a pipe, over a protocol none of
    # them got from the kernel.
    #
    # `uart_echo_*` (root as driver), `child_echo_*` (driver as a child) and
    # these are the same device and the same four bytes in three transcripts;
    # what changes between them is never the driver's device half.
    #
    # `uart_service_*` and `pipe_delegate_3p` run two children at once, which
    # `MAX_PROCESSES` allows. `_region_rows` assigns destinations by index, so
    # the order a case lists its children is a contract; on riscv32 each
    # position also has its own linker script (`hal/riscv32/user/child2.ld`
    # for the second).
    {
        # The money shot on riscv32. Read the transcript as an order:
        #
        #   read posted      root's `Read` is staged before the client process
        #                    exists, so it is first in the connection's FIFO
        #                    ring by construction rather than by timing, which
        #                    pins the order of the two outstanding reads before
        #                    a single input byte has arrived.
        #   client started   and now there are two, on one connection, from two
        #                    processes, through two sibling inlets.
        #   Zq7#             the harness's phrase, echoed: the first byte by
        #                    root through its own claim and the rest by the
        #                    client through its own. Two claims, two tables, one
        #                    driver, and each reply landed at the claim that
        #                    asked for it.
        #   root wrote ...   root's own round, after the client is dead: two
        #                    TELLs and a `Sync`, so a launcher's prose comes out
        #                    of a transmitter it does not own.
        #   served=13 ...    the driver's own accounting, and every number in it
        #                    is deterministic: nothing abandoned, nothing
        #                    dropped.
        #   after the mark   the measured window: `Mark` opens it and the
        #   ... traps=7      loop's end closes it, and there is no input left
        #                    by then so no interrupt can fall inside it. Two
        #                    answered messages at one trap each, two discarded
        #                    at two each, plus the closing `stats()` read.
        #
        # `Zq7#` rules out everything at once: the give, the map, the bind, an
        # IRQ waking a process that is not root, a request crossing a process
        # boundary, a one-shot reply pair carrying the answer back, and the
        # whole thing again through a second client's inlet. There is no way to
        # fake it on a machine with no translation and no driver in the kernel.
        "name": "uart_service_ns16550",
        "arches": ["riscv32", "riscv32-flat"],
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": UART_SERVICE_PKG,
        "children": [SVC_UART_NS16550_PKG, SVC_CLIENT_PKG],
        "device": True,
        "stdin": ECHO_INPUT,
        "expect_out": ["{banner}",
                       "root image ok segments={two}",
                       # Five rows: two image blobs, two destinations, the
                       # device window.
                       "SOS: boot regions={five}",
                       "SOS: console handover",
                       "SOS uartsvc: created",
                       "SOS uartsvc: driver started",
                       "SOS uartsvc: read posted",
                       "SOS uartsvc: client started",
                       ECHO_INPUT,
                       "SOS uartsvc: root wrote this line through the driver",
                       "SOS uartsvc: and root does not hold the device",
                       # 0x5A is 'Z', the first byte of the phrase, which is
                       # the one root's own claim was answered with.
                       "SOS uartsvc: root's claim answered byte 90",
                       "SOS uartsvc: client traps=13 for 3 reads and 3 writes",
                       "SOS uartsvc drv: served=13 abandoned=0 dropped=0",
                       "SOS uartsvc drv: after the mark answered=2 discarded=2 "
                       "traps=7",
                       "SOS uartsvc drv: one trap per answered message, two "
                       "per discarded",
                       "SOS uartsvc: the driver served two clients and ended "
                       "clean",
                       "SOS uartsvc: done"],
        "expect_clean_exit": True,
    },
    {
        # The same money shot on the other machine and the other chip. The
        # launcher, the client and the protocol are byte-identical packages;
        # what differs between the two driver children is a datasheet.
        "name": "uart_service_pl011",
        "arches": ["arm64"],
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": UART_SERVICE_PKG,
        "children": [SVC_UART_PL011_PKG, SVC_CLIENT_PKG],
        "device": True,
        "stdin": ECHO_INPUT,
        "expect_out": ["{banner}",
                       "root image ok segments={two}",
                       "SOS: boot regions={five}",
                       "SOS: console handover",
                       "SOS uartsvc: created",
                       "SOS uartsvc: driver started",
                       "SOS uartsvc: read posted",
                       "SOS uartsvc: client started",
                       ECHO_INPUT,
                       "SOS uartsvc: root wrote this line through the driver",
                       "SOS uartsvc: and root does not hold the device",
                       "SOS uartsvc: root's claim answered byte 90",
                       "SOS uartsvc: client traps=13 for 3 reads and 3 writes",
                       "SOS uartsvc drv: served=13 abandoned=0 dropped=0",
                       "SOS uartsvc drv: after the mark answered=2 discarded=2 "
                       "traps=7",
                       "SOS uartsvc drv: one trap per answered message, two "
                       "per discarded",
                       "SOS uartsvc: the driver served two clients and ended "
                       "clean",
                       "SOS uartsvc: done"],
        "expect_clean_exit": True,
    },
    {
        # One dead read cannot wedge a driver, and there is no stdin in this
        # case, which makes it exact. The device never raises its line, so the
        # whole run contains no interrupt and the driver's trap accounting is
        # arithmetic rather than a measurement.
        #
        # Root asks for a byte nobody will type, times out on its own Waiter
        # with its own Timer (the kernel still does not know what a timeout is)
        # and drops its claim. That release is the entire cancellation: no op,
        # no message, no correlation. The driver learns through the
        # request-abandoned level it was already watching because it was
        # already holding the obligation, and `abandoned=1` is that arm firing.
        #
        # The order is forced. When the timer fires the driver has been parked
        # in `wait` for twenty milliseconds, so root's release wakes it there
        # with that record, before root's next message is even posted. Twenty
        # milliseconds is three orders of magnitude more than the driver needs
        # to reach its park, which is `process-lifecycle`'s own argument.
        #
        # `served=5` is the read, the mark, two writes and the sync, and the
        # window numbers are the ones the money shot prints, from the same
        # driver program, which makes them about the loop rather than about a
        # case.
        "name": "uart_cancel_ns16550",
        "arches": ["riscv32", "riscv32-flat"],
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": UART_CANCEL_PKG,
        "children": [SVC_UART_NS16550_PKG],
        "device": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={three}",
                       "SOS: console handover",
                       "SOS uartcancel: driver started",
                       "SOS uartcancel: read posted",
                       # The timer, not the claim: on a console nobody is
                       # typing at, a claim that answered would mean the driver
                       # invented a byte.
                       "SOS uartcancel: timed out",
                       "SOS uartcancel: claim dropped",
                       "SOS uartcancel: root wrote this line through the driver",
                       "SOS uartcancel: one dead read did not wedge it",
                       "SOS uartsvc drv: served=5 abandoned=1 dropped=0",
                       "SOS uartsvc drv: after the mark answered=2 discarded=2 "
                       "traps=7",
                       "SOS uartsvc drv: one trap per answered message, two "
                       "per discarded",
                       "SOS uartcancel: done"],
        "expect_clean_exit": True,
    },
    {
        # The same cancellation against the other chip's driver.
        "name": "uart_cancel_pl011",
        "arches": ["arm64"],
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": UART_CANCEL_PKG,
        "children": [SVC_UART_PL011_PKG],
        "device": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={three}",
                       "SOS: console handover",
                       "SOS uartcancel: driver started",
                       "SOS uartcancel: read posted",
                       "SOS uartcancel: timed out",
                       "SOS uartcancel: claim dropped",
                       "SOS uartcancel: root wrote this line through the driver",
                       "SOS uartcancel: one dead read did not wedge it",
                       "SOS uartsvc drv: served=5 abandoned=1 dropped=0",
                       "SOS uartsvc drv: after the mark answered=2 discarded=2 "
                       "traps=7",
                       "SOS uartsvc drv: one trap per answered message, two "
                       "per discarded",
                       "SOS uartcancel: done"],
        "expect_clean_exit": True,
    },
    {
        # Delegation across three processes. `pipe_delegate_msg` proved the
        # mechanism with two pipes and one child; what three processes add is
        # that the middle is a genuinely different principal from both ends,
        # with its own table, its own quota and its own death.
        #
        #   reply len=4 b=68,79,78,69   `DONE`, written by a process that holds
        #                               no end of the connection root is parked
        #                               on, into a ring slot root owns both ends
        #                               of; the byte root posted never reached
        #                               it.
        #   middle=65607 far=65617      both §8 status words: `Exited` in the
        #                               high half, and codes that appear nowhere
        #                               else in any transcript.
        #
        # The middle is usually dead by the time the answer lands, which
        # take-at-post makes irrelevant; `pipe_send_exit` is where that
        # independence is the point.
        "name": "pipe_delegate_3p",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": DELEGATE_3P_PKG,
        "children": [CHILD_FORWARD_PKG, CHILD_FAR_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={four}",
                       "SOS delegate3p: created",
                       "SOS delegate3p: started far and middle",
                       "SOS delegate3p: reply len=4 b=68,79,78,69",
                       "SOS delegate3p: the answer crossed three processes",
                       "SOS delegate3p: middle=65607 far=65617",
                       "SOS delegate3p: done"],
        "expect_clean_exit": True,
    },
    # =========================================================================
    # The real allocator: spent bytes come back
    # =========================================================================
    #
    # `mapping_slot_free` above proves the second zero from the region's side
    # (its slot survives a handle drop while a row counts against it, and the
    # range returns at the last unmap). This case proves the pool itself.
    #
    # The cases from here on are appended rather than filed beside their
    # topic: the report prints in case-definition order, so appending leaves
    # every existing case's ordinal where it was.
    {
        # The proof that a pool is a pool. `quarters=4` then `whole=1` is the
        # allocator's whole arithmetic in two numbers: four equal pieces are
        # the entire pool, they are released out of order (which forces
        # the free list, two disjoint holes and a three-into-one merge rather
        # than a tidy LIFO fold), and what comes out afterwards is one region
        # the size of the pool. A cursor that had not taken all four back could
        # not serve that split, and a `split` too big is a `BadArg` fault, so a
        # kernel without pool returns dies on that line instead of printing a
        # smaller number.
        #
        # `reused read 60` is the second zero across a process boundary: root
        # maps a page into a child and then destroys both of its own objects
        # (the Memory handle and the Mapping), leaving the child's protection
        # row as the only reference to that region. The bytes come home at the
        # child's teardown, and the mark read back is the child's own
        # (`{touch_mark}`, the same byte `map_into_child` asserts from the other
        # side).
        #
        # The child is `child-touch`: it writes its mark at the pool base and
        # exits with it, which is exactly the witness this case needs.
        "name": "memory_recycle",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": MEMORY_RECYCLE_PKG,
        "children": [CHILD_TOUCH_PKG],
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={three}",
                       "SOS recycle: quarters=4",
                       "SOS recycle: whole=1",
                       "SOS childtouch: wrote 60 read 60",
                       "SOS: process exit: code={touch_mark} process={one}",
                       "SOS recycle: reused read 60",
                       "SOS recycle: done"],
        "expect_clean_exit": True,
    },

    # =========================================================================
    # Slab donation: capacity becomes policy
    # =========================================================================
    #
    # The case asserts that the ceiling moved and never what it moved to. A
    # donated extent holds `page / sizeof(WaiterSlot)` slots and that divisor
    # is per-profile, so the new ceiling is arch-dependent while every line
    # below is not; see the package's own header.
    {
        "name": "slab_donate",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": SLAB_DONATE_PKG,
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={one}",
                       "SOS slabdonate: floor=4 fifth refused: "
                       "out of kernel objects",
                       "SOS slabdonate: donated a page of waiters",
                       "SOS slabdonate: fifth waiter created",
                       "SOS slabdonate: extent waiter woke key=77 word=4",
                       "SOS slabdonate: done"],
        "expect_clean_exit": True,
    },

    # The negative, and it is a fault case: the safety condition's `refs == 1`
    # half. Root mints a sibling onto the region it then tries to donate, and
    # the kernel ends it; "object in the wrong state" is `FaultReason.BadState`,
    # the same wording `process_doublestart` above asserts. The `maps == 0`
    # half is `slab_donate_mapped` below.
    {
        "name": "slab_donate_shared",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": SLAB_DONATE_SHARED_PKG,
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={one}",
                       "SOS slabshared: minted a sibling",
                       "SOS: process fault: object in the wrong state "
                       "process={zero}",
                       "SOS: process teardown handles="],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },

    # Shared at per-process addresses: one page, two domains, two different
    # addresses, and both processes read and write the same bytes through
    # their own.
    #
    # The addresses are asserted as literals, deliberately. They are what the
    # kernel's VA policy answers (first fit at or above a process's own region
    # top, every image linked at `hal.USER_IMAGE_BASE` = 0x4020_0000, a region
    # `CHILD_REGION_LEN` long), so root's spacer and the child's only mapping
    # both land at 0x4024_0000 (1076101120) and root's second mapping one page
    # higher at 0x4024_1000 (1076105216). Pinning them makes the case an oracle
    # rather than a tautology: a policy change has to come here and say so.
    #
    # The two witnesses are independent. The console lines say the two
    # processes got different numbers; the status word (65731 = `Exited`(1) <<
    # 16 | 195) says the child really read the byte root wrote, through a path
    # no console line goes near.
    #
    # Aarch64 only: riscv32 keeps identity placement, so there the two
    # addresses would be one and the case would assert nothing.
    {
        "name": "map_placed",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": MAP_PLACED_PKG,
        "children": [CHILD_PLACED_PKG],
        "pool": True,
        "arches": ["arm64"],
        "expect_out": ["{banner}",
                       "SOS placed: created",
                       "SOS placed: spacer at 1076101120",
                       "SOS placed: root sees 1076105216",
                       "SOS placed: wrote 195",
                       "SOS placed: gave the region",
                       "SOS placed: started",
                       "SOS childplaced: mapped at 1076101120",
                       "SOS childplaced: saw 195 wrote 45",
                       "SOS placed: root observed child status=65731",
                       "SOS placed: read back 45",
                       "SOS placed: done"],
        "expect_clean_exit": True,
    },

    # Donation of the thread and pipe kinds.
    #
    # The risk first: a donated thread is not proven by a count, because what
    # matters is the context-switch path. So the case starts a thread whose slot
    # and frame are both in donated memory, watches it yield three times, and
    # reads its exit code back through a join. `TTT` is the three switches;
    # `code=33` is the register file surviving them.
    {
        "name": "thread_donate",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": THREAD_DONATE_PKG,
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={one}",
                       "SOS threaddonate: floor=8 ninth refused: "
                       "out of kernel objects",
                       "SOS threaddonate: donated a page of threads",
                       "SOS threaddonate: extent thread created",
                       "TTT",
                       "SOS threaddonate: extent thread joined code=33",
                       "SOS threaddonate: done"],
        "expect_clean_exit": True,
    },

    # A connection in extent 1 carries a real message end to end (post, take,
    # reply, resolve), so the staging ring that lives in donated memory beside
    # its connection slot is shown to work rather than merely to exist.
    {
        "name": "pipe_donate",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_DONATE_PKG,
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={one}",
                       "SOS pipedonate: floor=4 fifth refused: "
                       "out of kernel objects",
                       "SOS pipedonate: donated a region of pipes",
                       "SOS pipedonate: fifth connection created",
                       "SOS pipedonate: server took len=5 byte0=80",
                       "SOS pipedonate: client resolved len=3 byte0=90",
                       "SOS pipedonate: done"],
        "expect_clean_exit": True,
    },

    # The `maps == 0` half of the donation safety condition.
    #
    # The donor is the child, which is what makes the second half observable.
    # The refusal is a fault, so the donor dies, and "the region was not
    # absorbed" cannot be read out of a dead process. So root maps the page
    # into its own space, stamps a witness byte through the mapping, hands the
    # region to a child carrying `SlabDonate`, and reads the witness back after
    # the child has faulted. A donation that had been accepted would have
    # zeroed those bytes before installing them.
    {
        "name": "slab_donate_mapped",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": SLAB_DONATE_MAPPED_PKG,
        "children": [SLAB_DONATE_MAPPED_CHILD_PKG],
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={three}",
                       "SOS slabmapped: stamped witness=165",
                       "SOS donor: donating a mapped region",
                       "SOS: process fault: object in the wrong state "
                       "process={one}",
                       "SOS: process teardown handles={three} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={one}",
                       # Faulted(2) << 16 | BadState(5): the child died of the
                       # `maps` leg specifically, which is the claim.
                       "SOS slabmapped: child faulted status=131077",
                       "SOS slabmapped: witness still 165",
                       "SOS slabmapped: done"],
        "expect_clean_exit": True,
    },

    # The node-slab composition: a two-stage donation, and the first stage is
    # the very op under test. A free-range node is only ever spent on a hole,
    # so exhausting the node slab needs more non-adjacent free ranges than
    # `MAX_MEMORIES` can hold live regions to make; the pool physically cannot
    # be fragmented that far on a stock image. Donating to `Memories` first
    # lifts that wall.
    {
        "name": "slab_donate_free_nodes",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": SLAB_DONATE_NODES_PKG,
        "pool": True,
        # riscv32 only, and the package header carries the arithmetic: the
        # construction needs strictly growing cuts (first fit would otherwise
        # serve them out of the holes it just made), the step is one
        # `hal.PROT_GRAIN`, and at the other profile's grain the holes alone
        # would not fit in a pool that cannot exceed the grant window.
        "arches": ["riscv32", "riscv32-flat"],
        "expect_out": ["{banner}",
                       "SOS: boot regions={one}",
                       "SOS slabnodes: pool trimmed to the construction",
                       "SOS slabnodes: fragmented holes=32",
                       "SOS slabnodes: released one more past the node slab",
                       "SOS slabnodes: donated a page of free ranges",
                       "SOS slabnodes: the range came back",
                       # The assertion. R2's range came home and R1's did not,
                       # so the second ask of the pair cannot be served and the
                       # kernel ends the caller: `MemoryOp.Split`'s `BadArg`.
                       "SOS: process fault: argument outside its domain "
                       "process={zero}",
                       "SOS: process teardown handles="],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },

    # Process-body donation.
    #
    # It lists three children and asks for no pool, which is why the runner's
    # two-children-plus-a-pool refusal never comes up: the region root donates
    # is cut out of the third child's own destination row, after that child's
    # span has been carved off its front.
    {
        "name": "process_donate",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PROCESS_DONATE_PKG,
        "children": [ECHO_CHILD_PKG, ECHO_CHILD2_PKG, ECHO_CHILD3_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={six}",
                       # Claim 1: root plus two children is the body
                       # table.
                       "SOS procdonate: floor=3 third child refused: "
                       "out of kernel objects",
                       # Claims 2 and 3.
                       "SOS procdonate: donated a region of process bodies",
                       "SOS procdonate: third child created past the floor",
                       "SOS procdonate: started three children",
                       # Claim 4: each child is a whole process. The third
                       # one's line is a syscall made through a handle table
                       # that lives in donated memory.
                       "SOS echo0: alive",
                       "SOS echo1: alive",
                       "SOS echo2: alive",
                       "SOS procdonate: exits a=65577 b=65578 c=65579",
                       # Claim 5: the donated body came back at the third
                       # child's teardown and is spent again, the two below
                       # the floor being taken by fillers.
                       "SOS procdonate: donated slot reused d=65579",
                       "SOS procdonate: done"],
        "expect_clean_exit": True,
    },

    {
        # =====================================================================
        # The tier word itself
        # =====================================================================
        #
        # The platform is asked what it can deny and says so, twice with the
        # same answer; then the ask is made through a handle minted without the
        # right and the kernel ends the process.
        #
        # It runs on every profile. `{tier}` substitutes `Isolated` on the
        # two protected profiles and `Flat` on the flat one, so the one case
        # checks the runner's tier table against the HAL that implements it on
        # every profile the suite has.
        "name": "tier_word",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": TIER_WORD_PKG,
        "expect_out": ["{banner}",
                       # The word. `{tier}` is the harness's claim about this
                       # profile and the line is the kernel's answer, so this
                       # row is the two of them agreeing: the one place in the
                       # suite where the runner's tier table is checked against
                       # the HAL that implements it.
                       "SOS tier: {tier}",
                       "SOS tier: stable",
                       "SOS tier: kernel boundary intact",
                       # And this refusal holds on every tier. The sibling
                       # minted without `TierGet` is refused on the flat profile
                       # exactly as on hardware that can deny, because the
                       # refusal is the kernel's and not the platform's. The
                       # untiered object-model suite shows the rest of the
                       # syscall-path checks. What the flat tier disclaims is
                       # protecting memory, the kernel's included, from a
                       # process; the Isolated tier enforces both.
                       "SOS: process fault: access denied process={zero}",
                       "SOS: process teardown handles="],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },

    {
        # =====================================================================
        # The shared stats region
        # =====================================================================
        #
        # The trap counters live in one kernel-owned page, a process maps it
        # read-only, and a `top` costs no syscalls at all.
        #
        # Four claims, and each row below is one of them.
        #
        #   1. The op tells a caller which row is its own. Rows are indexed by
        #      process body and body numbering is kernel-internal, so without
        #      this number a caller holds a table it cannot find itself in.
        #      Root is the first process, so the answer is zero, and root
        #      asserting that is what makes the child's read of row zero sound.
        #   2. The region's numbers are the kernel's real ones. Root makes
        #      exactly eight syscalls between two reads of its own row and the
        #      column moves by exactly eight. `syscalls` is the deterministic
        #      column (`sosabi`'s record block), so this is an equality and not
        #      a range, and neither bracket costs a trap, which is the property
        #      the whole feature exists for.
        #   3. The v1 op is still the floor. `Process.stats()` reads the same
        #      storage, so it answers one higher (its own trap, charged before
        #      the answer is assembled), and the program asserts the `+ 1`.
        #   4. Cross-process visibility, the `top` shape. A child with the
        #      right maps the same page and reads its launcher's row: another
        #      process's state, with no handle onto that process and no syscall
        #      per sample. A child minted without `SystemRight.StatsRegionMap`
        #      could not have made the call.
        #
        # The region is unmapped at the end, which says the access can be
        # handed back even though nothing about it was ever owned.
        #
        # No pool: the page is the kernel's, granted by the op rather than
        # carved out of memory root possesses (access without possession),
        # which is why this case's boot set is two rows (the child's blob and
        # its destination) rather than three.
        #
        # It runs on every profile. Nothing it asserts is a hardware denial
        # (mapping, counting and reading are the kernel's own work), so the flat
        # tier witnesses it exactly as the isolated ones do. The read-only half
        # is the case below, and that one is tiered.
        "name": "stats_region",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": STATS_REGION_PKG,
        "children": [STATS_READER_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       # Claim 1. These row numbers are printed by the program
                       # and are therefore plain decimals; the `{zero}` and
                       # `{one}` placeholders are for words the kernel prints,
                       # which are fixed-width hex and word-width-sensitive.
                       "SOS statsregion: self row 0",
                       # Claim 2. Eight traps in, eight counted.
                       "SOS statsregion: region delta 8",
                       # Claim 3.
                       "SOS statsregion: op agrees",
                       # Claim 4: the child's own voice, from a page it was
                       # granted rather than given.
                       "SOS statsreader: my row 1",
                       "SOS statsreader: launcher busier",
                       # The round trip through the §8 status word, read by
                       # root through the Process handle it never gave away:
                       # `Exited` in the high half and the child's mark (42) in
                       # the low. The kernel's own exit line is not asserted
                       # beside it: it renders the code as a word-width hex
                       # word, so it would differ per profile for no gain here.
                       "SOS statsregion: child exit 65578",
                       "SOS statsregion: unmapped",
                       "SOS statsregion: done"],
        "expect_clean_exit": True,
    },

    {
        # The read-only half, its own case because it ends the process. A
        # program that dies proving a denial cannot go on to assert anything
        # else, which is why `map_basics` and `map_unmap` are separate cases
        # and why these are.
        #
        # The program maps the region, reads a column first (so that the fault
        # below is a statement about the access bits rather than about an
        # address that was never granted at all) and then stores into it.
        "name": "stats_region_ro",
        # Isolated-only. The assertion is denial by the hardware: the columns
        # are the kernel's account of what every process has cost the machine,
        # and a process that could write them could lie to every supervisor on
        # it. A flat platform lets the store land, the program runs on and
        # prints its UNREACHABLE line, so the case would fail by omission
        # rather than say anything true.
        "tier": TIER_ISOLATED,
        "tier_reason": "a store into the kernel's read-only counter page must "
                       "FAULT; a flat platform lets the write land",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": STATS_REGION_RO_PKG,
        "expect_out": ["{banner}",
                       # The row really is installed; without this the fault
                       # below would prove nothing.
                       "SOS statsro: read ok",
                       "SOS statsro: writing",
                       "SOS: fault "],
        "expect_clean_exit": False,
    },

    {
        # A package that chose its own arena. `sosrt`'s bump arena is a linker
        # region, `PROVIDE`d at a default size by every shared user script, and
        # this package's manifest names a two-line script of its own that
        # assigns `ARENA_SIZE = 8K` and then INCLUDEs exactly the `root.ld`
        # every other root package names. On a small part the arena size is the
        # biggest lever on how many processes fit.
        #
        # The case is the mechanism's only proof, and it is a pair of brackets.
        # There is no op that reports an arena size and there should not be, so
        # the program makes two requests whose answers together pin the size to
        # a range only the override can produce: a small request is served (so
        # the region is real, which rules out a build where the linker reserved
        # nothing) and a large one is not (so the region is smaller than that
        # request, which is impossible at the default). An image that missed the
        # override serves both and reaches the program's UNREACHABLE line, which
        # fails the case rather than passing by omission.
        #
        # The second bracket ends the machine, which is why this case asserts a
        # status rather than a clean shutdown. `__saw_rt_alloc` has nowhere to
        # report a refusal to (it is the allocator every reporting path would
        # allocate through), so it writes one line and aborts, and the exact
        # status tells "the arena ran dry" from "the program panicked".
        #
        # It runs on every profile. A link address and a bump cursor are
        # the same on a flat platform as on an isolated one, so nothing here is
        # tiered.
        "name": "arena_small",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": ARENA_SMALL_PKG,
        "expect_out": ["{banner}",
                       # Bracket one: reserved, written and read back.
                       "SOS arenasmall: 2 KiB ok",
                       # Printed before the ask, because the ask does not
                       # return.
                       "SOS arenasmall: asking 16 KiB",
                       # `sosrt`'s own words, from `rt/common/src/lib.saw`.
                       "sos: out of arena memory"],
        "expect_clean_exit": False,
        "expect_status": EXIT_ARENA_EXHAUSTED,
    },
    {
        # A freestanding C child, end to end. This case spawns a C image:
        # `tests/c-child/` is a `main.c`, a `crt0.c` and a hand-rolled
        # `syscall.c` compiled by the same clang that builds `sink.c` and
        # `support.c`, linked by the same `ld.lld` against the same user linker
        # script, emitted as the same sosimg and loaded by the same loader. It
        # has no `sos` dependency, so it links no `sosrt` and reserves no
        # arena; its `src/main.saw` is an empty file Blade's
        # `find_source_file()` demands, and all that survives it is a
        # retain-marked backtrace table.
        #
        # The launcher cannot tell, and that is the claim.
        # `tests/c-hello/src/main.saw` is `death-notify`'s program with the
        # strings changed: two Memory capabilities into `process_create`, a
        # masked System handle given under a tag, a Waiter on the child's
        # Process handle, and the §8 status word read out of the wake. Nothing
        # in it names C, and nothing in the kernel does either.
        #
        # The three assertions are the three things "a C process really ran"
        # means, and each rules out a different way of passing by accident:
        #
        #   hello line   the image loaded: `.rodata` reached its link address
        #                and the console op works.
        #   wrote 38     the image computed: 38 is counted by a loop, held in a
        #                stack frame the kernel's initial sp provides, and
        #                rendered by an integer division. A replay of a
        #                constant string cannot produce it.
        #   status       the image ended properly: `Exited`(1) << 16 | 55 =
        #                65591, a word no fault could produce, carrying
        #                `main`'s own return value through the crt0 into
        #                `Process.exit`.
        #
        # It runs on every profile. Nothing about a C image is tiered: the
        # entry contract, the initial stack pointer and the segment copy are
        # the same on a flat platform as on an isolated one.
        "name": "c_hello",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": C_HELLO_PKG,
        "children": [C_CHILD_PKG],
        "expect_out": ["{banner}",
                       # One child, no pool: the blob row and the destination
                       # row, and nothing else.
                       "SOS: boot regions={two}",
                       "SOS chello: started",
                       # The C image's own words.
                       "SOS cchild: hello from freestanding C",
                       "SOS cchild: wrote 38 bytes",
                       "SOS chello: woke status=65591",
                       "SOS chello: done"],
        "expect_clean_exit": True,
    },
    {
        # A dying process disowns the shared objects charged to it (spec
        # §2.10's refund rule), so a refund at a later free reaches nobody,
        # never the dead process's slot's next occupant.
        #
        # One child image plays both parts. The charger makes a connection,
        # splits two regions off a sibling of root's pool and carves a window
        # off a device view, posts all four to root and exits, disowning them.
        # Root drops the charger's Process handle and death attachment, and
        # record 1 is reclaimed although the four objects are live.
        #
        #   probe ... process={one}   the probe lands in the charger's record
        #   freed and donated         root gives the four objects back through
        #                             the four free paths: the connection's
        #                             free, a region's release, the other
        #                             region's donation, and the window's free
        #   pipes=2 regions=4         the probe's own allowances, untouched by
        #   windows=2 quota=1         those frees: every kind refuses at its
        #                             default with `QuotaExceeded`
        #   status=65544              `Exited` (1) << 16 | 8, the probe's total
        #
        # A free that credited a disowned object's original charge would credit
        # the probe, and that kind would count one more; one that indexed the
        # ledger with the no-owner value would stop the machine.
        "name": "charge_disown",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": CHARGE_DISOWN_PKG,
        "children": [CHILD_DISOWN_PKG],
        "pool": True,
        "device": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={four}",
                       "SOS chargedisown: started",
                       "SOS: process exit: code={charger_mark} process={one}",
                       "SOS: process teardown handles={two} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS chargedisown: the charger is gone",
                       "SOS chargedisown: freed and donated what it left",
                       "SOS probe: pipes=2 regions=4 windows=2 quota=1",
                       "SOS: process exit: code={eight} process={one}",
                       "SOS: process teardown handles={four} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS chargedisown: probe status=65544",
                       "SOS chargedisown: done"],
        "expect_clean_exit": True,
    },
    {
        # A process's own shared objects go with it. Root counts the
        # connections and windows it can make, launches the keeper with a pool
        # sibling and a device view, and hands the keeper's Process handle to a
        # Waiter as a one-shot subscription. The keeper makes two connections
        # (one with a message staged on it), splits a region, carves a window
        # and exits holding all of it (`handles={nine}`).
        #
        #   connections back=1   the same counts as before: the teardown freed
        #   windows back=1       the idle connection in the reap's connection
        #                        scan, the busy one by the cascade out of
        #                        settling its staged exchange, and the window
        #                        in the IoMemory scan
        #   region back=1        root donates its pool whole, which a pool with
        #                        a piece on loan refuses (as a fault)
        #
        # This pins the no-leak path, not the disown: everything is charged to
        # the keeper and freed in its own teardown, so the case passes on a
        # kernel that never disowns, by design.
        "name": "charge_reap",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": CHARGE_REAP_PKG,
        "children": [CHILD_KEEPER_PKG],
        "pool": True,
        "device": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={four}",
                       "SOS chargereap: started",
                       "SOS keeper: holding",
                       "SOS: process exit: code={keeper_mark} process={one}",
                       "SOS: process teardown handles={nine} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS chargereap: keeper status=65581",
                       "SOS chargereap: connections back=1 windows back=1 "
                       "region back=1",
                       "SOS chargereap: done"],
        "expect_clean_exit": True,
    },
]

INSTALL_HINTS = {
    "qemu-system-riscv32": "brew install qemu   (macOS)  |  apt install qemu-system-misc   (Debian/Ubuntu)",
    "qemu-system-aarch64": "brew install qemu   (macOS)  |  apt install qemu-system-arm    (Debian/Ubuntu)",
    "ld.lld": "brew install lld    (macOS)  |  apt install lld                (Debian/Ubuntu)",
    "clang": "install Xcode/`brew install llvm` (macOS)  |  apt install clang  (Debian/Ubuntu)",
}


class ToolError(Exception):
    pass


def _run(cmd, **kw):
    """Run a build command, raising ToolError with captured output on failure."""
    proc = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if proc.returncode != 0:
        out = (proc.stdout or "") + (proc.stderr or "")
        raise ToolError(f"command failed ({proc.returncode}): {' '.join(cmd)}\n{out}")
    return proc


def _map_ordered(jobs, items, work):
    """`work(item)` over `items`, `jobs` at a time, yielding in item order.

    The ordering is the whole point. A parallel suite that reported in
    completion order would print a different transcript every run, and the
    transcript has to be diffable, so the report has to be a function of the
    case list and nothing else. Submitting every item up front and then
    awaiting the futures in submission order gives exactly that: work
    overlaps, results arrive in the order the caller asked for them, and each
    one prints the moment the head of the queue is ready rather than at the
    end of the run.

    Threads, not processes: every unit of work below is a `subprocess.run`, so
    the GIL is released for the duration and a process pool would buy nothing
    but a pickling constraint on the case dictionaries.

    `jobs <= 1` runs inline, on this thread, not as a one-worker pool: the
    serial harness is the reference the parallel one is diffed against, so
    `-j 1` takes no code path that `-j 4` introduced.

    A consumer that breaks out early (the package phase does, on the first
    failure) cancels whatever has not started; the pool's own shutdown then
    waits only for what is already running.
    """
    if jobs <= 1 or len(items) <= 1:
        for item in items:
            yield work(item)
        return
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = [pool.submit(work, item) for item in items]
        try:
            for fut in futures:
                yield fut.result()
        finally:
            for fut in futures:
                fut.cancel()


def _default_jobs():
    """How many package builds, and then cases, run at once without `-j`.

    One per performance core. Apple silicon reports those as perflevel 0,
    read with `sysctl`; a host without that key has one kind of core and gets
    all of them from `os.cpu_count()`. `SOS_JOBS` overrides the detection; a
    malformed value is refused with exit 2 rather than ignored.
    """
    named = os.environ.get("SOS_JOBS")
    if named:
        if not named.isdigit() or int(named) < 1:
            print(f"{RED}SOS_JOBS must be a whole number, at least 1 "
                  f"(got {named!r}){RESET}", file=sys.stderr)
            sys.exit(2)
        return int(named)
    try:
        probe = subprocess.run(["sysctl", "-n", "hw.perflevel0.physicalcpu"],
                               capture_output=True, text=True)
    except OSError:
        probe = None
    if (probe is not None and probe.returncode == 0
            and probe.stdout.strip().isdigit()):
        return max(1, int(probe.stdout.strip()))
    return os.cpu_count() or 1


def _find_clang(arches):
    """Return the first clang that can assemble every target's boot code.

    macOS's Apple clang mis-drives the riscv integrated assembler, so a real
    LLVM clang (Homebrew `llvm`) is preferred there; on Linux CI plain `clang`
    (apt) works. `SOS_CLANG` overrides the search. The probe compiles each
    architecture's own `boot.S`, because "can target riscv32" and "can target
    aarch64" are separate questions and one harness needs both answered yes.
    """
    candidates = []
    if os.environ.get("SOS_CLANG"):
        candidates.append(os.environ["SOS_CLANG"])
    candidates += ["clang", "/opt/homebrew/opt/llvm/bin/clang", "/usr/local/opt/llvm/bin/clang"]
    for cand in candidates:
        path = shutil.which(cand) or (cand if os.path.exists(cand) else None)
        if not path:
            continue
        ok = True
        for arch in arches:
            dirs = arch_dirs(arch)
            os.makedirs(dirs["build"], exist_ok=True)
            # The board's boot.S: a profile that is a variant of an existing
            # board has none of its own.
            probe_src = os.path.join(dirs["hal_board"], "boot.S")
            try:
                _run([path, f"--target={arch['triple']}", *arch["cc_args"],
                      "-nostdlib", "-c", probe_src,
                      "-o", os.path.join(dirs["build"], "_probe.o")])
            except ToolError:
                ok = False
                break
        if ok:
            return path
    return None


def _probe_tools(arches):
    """Locate every qemu, ld.lld and a clang; print hints and exit on any miss."""
    missing = []

    qemus = {}
    for arch in arches:
        found = shutil.which(arch["qemu"])
        if not found:
            missing.append(arch["qemu"])
        qemus[arch["name"]] = found

    lld = shutil.which("ld.lld")
    if not lld:
        missing.append("ld.lld")
    clang = _find_clang(arches)
    if not clang:
        missing.append("clang")

    if missing:
        print(f"{RED}{BOLD}sos-test: missing host prerequisites{RESET}", file=sys.stderr)
        for tool in missing:
            print(f"  - {tool}: {INSTALL_HINTS[tool]}", file=sys.stderr)
        sys.exit(2)

    return qemus, lld, clang


def _sources_under(path):
    """Every `.saw` file at or under `path`."""
    if os.path.isfile(path):
        return [path]
    found = []
    for root, _dirs, files in os.walk(path):
        for name in sorted(files):
            if name.endswith(".saw"):
                found.append(os.path.join(root, name))
    return found


def _check_arch_free():
    """Fail the run if an architecture name appears in the arch-free kernel.

    The seam is as much a deliverable as the port. A kernel that names its
    architecture still compiles; the leak only shows up on the other target,
    whenever someone next ports, so the check that keeps the seam honest has
    to be mechanical. Comments count: a note that says `mepc` is a note that
    will be wrong on the profile that has no `mepc`.
    """
    bad = []
    for path in ARCH_FREE_DIRS:
        for src in _sources_under(path):
            with open(src, encoding="utf-8") as f:
                for lineno, line in enumerate(f, 1):
                    low = line.lower()
                    for word in ARCH_WORDS:
                        at = low.find(word)
                        while at >= 0:
                            if not _is_english_embedding(low, word, at):
                                rel = os.path.relpath(src, REPO_ROOT)
                                bad.append(f"{rel}:{lineno}: {word!r} in: "
                                           f"{line.strip()}")
                                break
                            at = low.find(word, at + 1)
    if bad:
        print(f"{RED}{BOLD}sos-test: architecture names in the arch-free kernel"
              f"{RESET}", file=sys.stderr)
        for line in bad:
            print(f"  {line}", file=sys.stderr)
        print("  (kcore reaches the machine through `hal` only)",
              file=sys.stderr)
        return False
    return True


def _build_shared(arch, clang):
    """Build one architecture's native objects once; return them as a list.

    A kernel image is that architecture's kernel HAL (boot + trap entry, the
    board sinks, the protection primitive) plus the shared C floor every SOS
    build links: the same `support.c` a root package names in its
    `[sos.<triple>] native`, which holds the arena accessors and the word
    atomics. The runtime seams are Saw and reach the image through
    `--module-path sosrt=` below.
    """
    dirs = arch_dirs(arch)
    build = dirs["build"]
    # Ensure our own output directory rather than relying on the tool probe
    # having made it: a cold tree with `SOS_CLANG` set skips that probe path
    # entirely, and a build step should not depend on a search for its side
    # effects.
    os.makedirs(build, exist_ok=True)
    boot_o = os.path.join(build, "boot.o")
    sink_o = os.path.join(build, "sink.o")
    support_o = os.path.join(build, "support.o")
    _run([clang, f"--target={arch['triple']}", *arch["cc_args"],
          "-nostdlib", "-c", os.path.join(dirs["hal_board"], "boot.S"),
          "-o", boot_o])
    # Any shared assembly this architecture's HAL splits out of its board
    # `boot.S`: the riscv32 trap entry and M -> U transition, which are the
    # ISA's rather than the board's. arm64 brings none.
    asm_objs = []
    for rel in arch["hal_asm"]:
        obj = os.path.join(build, os.path.basename(rel).replace(".S", ".o"))
        _run([clang, f"--target={arch['triple']}", *arch["cc_args"],
              "-nostdlib", "-c", os.path.join(HAL_DIR, rel), "-o", obj])
        asm_objs.append(obj)
    # `sink.c` comes from `hal_native`, which is this board's own directory on
    # arm64 and the shared `riscv32-common` one on riscv32.
    for src, obj in ((os.path.join(dirs["hal_native"], "sink.c"), sink_o),
                     (os.path.join(RT_COMMON_C_DIR, "support.c"), support_o)):
        # -fno-builtin: freestanding C must not be rewritten into calls to
        # memcpy and friends.
        _run([clang, f"--target={arch['triple']}", *arch["cc_args"],
              "-ffreestanding", "-fno-builtin", "-ffunction-sections",
              "-fdata-sections", "-nostdlib", "-O2", "-c", src, "-o", obj])
    return [boot_o, *asm_objs, sink_o, support_o]


def _build_blade(build_dir):
    """Get a Blade to drive: the resolver's, or one built from its sources.

    The SOS root packages are built by Blade, so the harness needs one. A
    resolved binary (`BLADE=…`, or one found on `$PATH`) is used as it is.
    Otherwise the toolchain came with a checkout and Blade is built out of it,
    which keeps a sawlang checkout testing its own package manager rather than
    whatever happens to be installed.
    """
    resolved = tc().blade_binary()
    if resolved:
        return resolved
    blade_bin = os.path.join(build_dir, "blade")
    _run(tc().sawc() +
         [os.path.join(tc().blade_package_dir(), "src", "main.saw"),
          "-o", blade_bin,
          "--module-path", "blade=" + os.path.join(tc().blade_package_dir(), "src"),
          "--module-path", tc().module_path_arg("toml"),
          "--module-path", tc().module_path_arg("semver"),
          "--module-path", tc().module_path_arg("imgformat")])
    return blade_bin


def _blade_env(clang):
    env = dict(os.environ)
    # The trailing -Oz rides into every blade-driven sawc invocation: the SAWC
    # value is a full command line split on spaces (blade's documented
    # `CC="ccache gcc"` convention), which carries -Oz on all freestanding
    # builds to the packages blade builds (root and every test sosimg).
    env["SAWC"] = tc().sawc_env_value() + " -Oz"
    # macOS's Apple clang mis-drives the riscv integrated assembler; hand Blade
    # the same clang this harness probed for.
    env["SOS_CLANG"] = clang
    return env


def _build_root_image(blade_bin, pkg_dir, arch, clang):
    """`blade build --target <triple>` a root package; return its sosimg path.

    Always `--force`: the harness's job is to prove the current tree boots,
    and Blade's build avoidance keys on content it cannot see change here (the
    kernel side, the linker script's meaning).
    """
    # Artifacts live under `<package>/.build/<target>/`, never beside the
    # source, which also lets one package hold two architectures' images at
    # once.
    out_dir = os.path.join(pkg_dir, ".build", arch["triple"])

    # Delete first: a build that fails must not leave the previous image lying
    # around to be booted as if it were current.
    if os.path.isdir(out_dir):
        for stale in os.listdir(out_dir):
            if stale.endswith(".sosimg"):
                os.remove(os.path.join(out_dir, stale))

    _run([blade_bin, "build", "--force", "--target", arch["triple"]],
         cwd=pkg_dir, env=_blade_env(clang))

    # The image is named for the package, which need not match its directory.
    images = []
    if os.path.isdir(out_dir):
        images = [f for f in os.listdir(out_dir) if f.endswith(".sosimg")]
    if len(images) != 1:
        raise ToolError(f"expected exactly one .sosimg in {out_dir}, found {images}")
    return os.path.join(out_dir, images[0])


def _stitch_root_image(image, arch, clang, name):
    """Assemble the `.incbin` stub that pulls `image` into `.payload`.

    The stub names `root.sosimg` and is assembled with `-I` pointing at a
    directory holding one, so one committed stub stitches whichever root image
    the case asked for.

    The staging directory is per case, which makes this step safe to run
    concurrently. `kernel/rootimg.S` is committed and names a fixed filename,
    so the case cannot be distinguished by the file's name the way every other
    artifact here is (`<case>.o`, `<case>.elf`, `<case>.regions.S`); it has to
    be distinguished by the directory the `-I` points at. Staged into one
    shared build directory, two cases running at once would each copy their
    own root image over `root.sosimg` and one of them would boot the other's
    image: a wrong-but-plausible transcript, which is the worst failure a test
    harness can have.
    """
    dirs = arch_dirs(arch)
    stage = os.path.join(dirs["build"], f"{name}.rootimg")
    os.makedirs(stage, exist_ok=True)
    staged = os.path.join(stage, "root.sosimg")
    shutil.copyfile(image, staged)
    stub_o = os.path.join(dirs["build"], f"{name}.rootimg.o")
    _run([clang, f"--target={arch['triple']}", *arch["cc_args"],
          "-nostdlib", "-I", stage, "-c",
          os.path.join(KERNEL_DIR, "rootimg.S"), "-o", stub_o])
    return stub_o


def _emit_word64(lines, arch, expr):
    """Emit one 64-bit little-endian field, however wide `.word` is here.

    The region table's fields are 64-bit on every profile, so both kernels read
    one layout. A 32-bit assembler has no 8-byte relocation to point at a
    symbol with, so a 32-bit target writes the low half and a zero high half,
    which is the same bytes little-endian. A 64-bit one writes the field.
    """
    if arch["hex_width"] == 8:
        lines.append(f"    .4byte {expr}")
        lines.append("    .4byte 0")
    else:
        lines.append(f"    .8byte {expr}")


def _emit_row(lines, arch, base_expr, len_expr, kind):
    """Emit one version 2 region row: {base u64, len u64, kind u8, 7 reserved}.

    24 bytes, which keeps the next row 8-aligned without a `.balign` the
    kernel's overlay would have to know about.
    """
    _emit_word64(lines, arch, base_expr)
    _emit_word64(lines, arch, len_expr)
    lines.append(f"    .byte  {kind}")
    lines.append("    .byte  0, 0, 0, 0, 0, 0, 0")


def _region_rows(case, arch):
    """Which rows this case's table carries, in order: the whole contract.

    Row order is the contract, and it is stated here because it is stated
    nowhere else. Root's config reads it as tags:

        every blob row       one per child, in the order the case lists them
        every destination    one per child, same order
        the RAM pool         if the case asks for one
        the device window    if the case asks for one

    Blobs and destinations come first so that a child's tags stay put whatever
    else the case asks for (tag 0 is child 0's image, tag N is child 0's RAM),
    and the pool and device rows append rather than insert. A case that wants
    only a pool gets tag 0 for it; that is not a special case, it is the same
    rule with no children in front.

    Each row is (comment, base-expression, length-expression, kind). A base may
    be a linker expression rather than a number: that is what makes a blob row
    work at all, since the address is whatever `ld.lld` decides.
    """
    children = case.get("children", ())
    # Two children and a pool cannot coexist, and the refusal is here rather
    # than in a comment because the collision is silent otherwise. The pool
    # base is one region above the one child region above root, so with two
    # children the second one's destination is the pool's window, and the
    # table would publish two rows naming the same bytes under two kinds.
    # Widening the layout is a decision for this file to make out loud.
    if len(children) > 1 and case.get("pool"):
        raise ToolError(
            f"case {case['name']!r}: a case with two children cannot also ask "
            f"for a pool — the second child's destination region and the pool "
            f"share a base (see hal/*/user/child2.ld)")
    rows = []
    for i, _pkg in enumerate(children):
        rows.append((f"child {i}'s image blob (linker-resolved)",
                     f"_sos_child{i}_start",
                     f"_sos_child{i}_end - _sos_child{i}_start",
                     REGION_KIND_RAM))
    for i, _pkg in enumerate(children):
        base = arch["child_region_base"] + i * CHILD_REGION_LEN
        rows.append((f"child {i}'s destination RAM", f"{base:#x}",
                     f"{CHILD_REGION_LEN:#x}", REGION_KIND_RAM))
    if case.get("pool"):
        rows.append(("the RAM POOL — free memory above every child region",
                     f"{arch['pool_base']:#x}", f"{arch['pool_len']:#x}",
                     REGION_KIND_RAM))
    if case.get("device"):
        rows.append(("the DEVICE window — the console UART's register page",
                     f"{arch['device_base']:#x}", f"{arch['device_len']:#x}",
                     REGION_KIND_DEVICE))
    return rows


def _stitch_regions(case, arch, clang):
    """Append this case's child images and emit the boot region table.

    The stitcher appends each child sosimg as it is (no flattening, no
    absolute placement, the blob lands wherever the linker puts it) and
    records offsets and lengths in a table. The kernel mints one object per row
    and interprets nothing; root's config says which ordinal is which.

    It emits three kinds of row: a case asks for `children`, a `pool`, a
    `device` window, or any combination, and `_region_rows` above is the order
    they land in. The kind column is what the kernel reads (a Ram row mints a
    Memory, a Device row mints an IoMemory) and it is the only thing in a row
    the kernel interprets at all.

    The stub is generated rather than committed, which is what buys the
    linker-resolved bases: a blob row names the child section's own symbols,
    so `ld.lld` fills in the address when it places the section and nothing
    here computes one. A committed stub would have had to be per-case anyway
    (the number of children is a property of the case), so generating it
    costs a file nobody has to keep in step and removes the one thing that
    could have gone wrong.
    """
    dirs = arch_dirs(arch)
    os.makedirs(dirs["build"], exist_ok=True)
    name = case["name"]
    children = case.get("children", ())

    lines = [
        "/* GENERATED by tools/sos_runner.py */",
        "",
    ]
    # A case with no children links no `.childimg` section at all, which is what
    # keeps a pool-only or device-only case from carrying an empty one.
    if children:
        lines += [
            "    .section .childimg, \"a\", @progbits",
            "    .balign 16",
        ]
        for i, _pkg in enumerate(children):
            staged = os.path.join(dirs["build"], f"{name}.child{i}.sosimg")
            shutil.copyfile(case["_child_images"][arch["name"]][i], staged)
            lines += [
                f"_sos_child{i}_start:",
                f"    .incbin \"{os.path.basename(staged)}\"",
                f"_sos_child{i}_end:",
                "    .balign 16",
            ]

    rows = _region_rows(case, arch)
    lines += [
        "",
        "    .section .regions, \"a\", @progbits",
        "    .balign 8",
        f"    .4byte {REGION_TABLE_MAGIC:#010x}",
        f"    .2byte {REGION_TABLE_VERSION}",
        f"    .byte  {len(rows)}",
        "    .byte  0",
    ]
    for i, (what, base_expr, len_expr, kind) in enumerate(rows):
        lines.append(f"    /* row {i}: {what} */")
        _emit_row(lines, arch, base_expr, len_expr, kind)
    lines.append("")

    stub_s = os.path.join(dirs["build"], f"{name}.regions.S")
    with open(stub_s, "w") as f:
        f.write("\n".join(lines))
    stub_o = os.path.join(dirs["build"], f"{name}.regions.o")
    _run([clang, f"--target={arch['triple']}", *arch["cc_args"],
          "-nostdlib", "-I", dirs["build"], "-c", stub_s, "-o", stub_o])
    return stub_o


def _build_elf(case, arch, shared_objs, lld, clang):
    """Compile + link one test case for one architecture; return its ELF path.

    A case may name an extra `.S` payload, taken from this architecture's test
    directory, which lands in the `.payload` section the HAL's linker script
    bounds: raw user-mode code or a hand-assembled sosimg fixture. A root
    image arrives through `root_pkg` and `kernel/rootimg.S`'s `.incbin`.
    """
    dirs = arch_dirs(arch)
    os.makedirs(dirs["build"], exist_ok=True)
    name = case["name"]
    obj = os.path.join(dirs["build"], f"{name}.o")
    elf = os.path.join(dirs["build"], f"{name}.elf")
    # `--no-hidden-alloc` rides along on every kernel build. The kernel logs
    # through the alloc-free formatting path, so this costs nothing and is what
    # keeps it that way: an interpolated log line or an escaping closure added
    # later fails the gate instead of quietly reaching for an allocator the
    # kernel may not have.
    #
    # `--runtime-provider` says this compile implements the frozen `__saw_rt_*`
    # seams rather than merely calling them. It has to be here rather than in a
    # manifest because a kernel image is not a Blade package: `sosrt` carries
    # the seam bodies and rides in on --module-path, so this is the compile that
    # defines them. Without it the `@export`s are refused by name; with it every
    # signature is checked against sawlang's runtime/ABI.md.
    #
    # Every freestanding Saw build asks for -Oz: size is the scarce resource
    # on target, and one configuration keeps one baseline. The C objects keep
    # their own -O2; blade-built packages get the same -Oz through the SAWC
    # env value in `_blade_env`.
    cmd = tc().sawc() + [case["src"], "-o", obj, "-Oz",
                         "--freestanding", "--no-hidden-alloc",
                         "--runtime-provider", "--target", arch["triple"]]
    if arch["features"]:
        cmd += ["--target-features", arch["features"]]
    cmd += ["--module-path", CORE_MODULE,
            "--module-path", f"hal={dirs['hal_kernel']}",
            "--module-path", tc().module_path_arg("imgformat"),
            "--module-path", SOSRT_MODULE,
            "--module-path", SOSABI_MODULE]
    # A riscv32 board's `hal` module re-exports the shared `rv32core`, so that
    # module has to be on the path beside it. arm64 brings none and this adds
    # nothing there.
    for extra in arch["hal_modules"]:
        cmd += ["--module-path", extra]
    _run(cmd)

    objs = list(shared_objs) + [obj]
    if case.get("asm"):
        payload_src = os.path.join(dirs["tests"], case["asm"])
        payload_o = os.path.join(dirs["build"], f"{name}.payload.o")
        _run([clang, f"--target={arch['triple']}", *arch["cc_args"],
              "-nostdlib", "-c", payload_src, "-o", payload_o])
        objs.append(payload_o)
    if case.get("root_pkg"):
        objs.append(_stitch_root_image(case["_root_image"][arch["name"]], arch,
                                       clang, name))
    # The child images and the boot region table that names them, the RAM pool
    # and the device window. A case that asks for none of the three links no
    # `.regions` section, so `_region_table_start` and `_region_table_end` come
    # out equal and the kernel sees zero regions.
    if case.get("children") or case.get("pool") or case.get("device"):
        objs.append(_stitch_regions(case, arch, clang))

    # The board's linker script, for `boot.S`'s reason: the memory map, the
    # payload section and the region table are the board's, and a protection
    # profile moves none of them.
    _run([lld, "-T", os.path.join(dirs["hal_board"], "virt.ld"), "--gc-sections",
          "-o", elf, *objs])
    return elf


def _run_qemu(qemu, arch, elf, feed=None):
    """Run the ELF under QEMU with a hard timeout.

    Returns (exit_status, stdout, timed_out). A timeout is a hang: the
    kernel-bug path exits the machine, so a fault never reaches the timeout.

    Serial input: `-nographic` wires the guest's console UART to this
    process's stdin and stdout both, so a case with a `stdin` key gets its
    bytes typed at the guest, which is the only way to test a receive driver,
    since nothing inside the guest can make its own UART receive.

    The bytes go one at a time behind `SERIAL_TYPE_DELAY_S`, for the reason
    stated there: withholding them is what makes the driver park and the
    kernel idle, and a burst handed over at once would test neither.

    The pipe is made here rather than by `Popen(stdin=PIPE)`, because
    `communicate()` closes the stdin it owns as soon as it is called, which
    would shut the port before the first delayed byte and leave the guest
    waiting forever for input that was never sent. That failure looks exactly
    like a kernel that cannot wake.

    A case with no input gets a port of its own anyway: an empty pipe this
    process holds the write end of, so the guest's console never sees a byte
    and never sees an EOF either. Inheriting the harness's stdin is wrong once
    cases run concurrently: `-nographic` on a TTY puts the terminal in raw
    mode and restores what it found, so several QEMUs saving and restoring each
    other's termios leave the operator's shell in whichever state the last one
    to exit happened to have seen. An unwritten pipe is what an idle console
    always was, without the shared device underneath it.
    """
    cmd = [qemu, *arch["qemu_args"], "-nographic", "-kernel", elf]
    if feed is None:
        read_fd, write_fd = os.pipe()
        try:
            proc = subprocess.run(cmd, stdin=read_fd, capture_output=True,
                                  text=True, timeout=QEMU_TIMEOUT_S)
            return proc.returncode, proc.stdout, False
        except subprocess.TimeoutExpired as e:
            return None, (e.stdout or (e.stdout and e.stdout.decode()) or ""), True
        finally:
            # The write end is held open for the child's whole life above and
            # closed only here: closing it earlier would EOF the guest's
            # console rather than leave it quiet.
            os.close(read_fd)
            os.close(write_fd)

    read_fd, write_fd = os.pipe()
    proc = subprocess.Popen(cmd, stdin=read_fd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    os.close(read_fd)

    def typist():
        # Owns the write end for its whole life, including the closing: a
        # second closer would be racing an fd number this process may already
        # have reused.
        try:
            for ch in feed:
                time.sleep(SERIAL_TYPE_DELAY_S)
                os.write(write_fd, ch.encode())
        except OSError:
            pass                    # the guest stopped first; nothing to say
        finally:
            os.close(write_fd)

    threading.Thread(target=typist, daemon=True).start()
    try:
        out, _err = proc.communicate(timeout=QEMU_TIMEOUT_S)
        return proc.returncode, out, False
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _err = proc.communicate()
        return None, out, True


# The letters-only projection.
#
# The kernel and a user process share one serial port with no locking, so the
# kernel's own narration lands inside a process's byte stream. That is harmless
# for a case asserting a line, and fatal for a case asserting adjacency:
# `thread_preempt` claims the processor crossed between two threads by matching
# `AB`, `BA`, `AB` as ordered substrings, and a `SOS: timer tick …` line landing
# between the `B` and the `A` breaks the match on a run that demonstrated the
# property perfectly.
#
# So a case may ask to be matched against the output with the kernel's own
# lines removed. The removal is exact rather than approximate: the kernel
# writes a diagnostic line from inside a trap handler, start to newline, with
# no preemption point in it, so `SOS: …\n` always appears whole and never has a
# user byte inside it. It is the user's stream that gets split, which is the
# thing being repaired. What is left is the process's bytes in the order the
# process wrote them.
#
# It changes what the harness matches and nothing about what the kernel prints.
# Only `thread_preempt` asks for it, and any case that does must not expect a
# line beginning `SOS: `.
_KERNEL_DIAGNOSTIC_LINE = re.compile(r"SOS: [^\r\n]*(?:\r?\n)?")


def _strip_kernel_diagnostics(out):
    return _KERNEL_DIAGNOSTIC_LINE.sub("", out)


def _check(case, arch, status, out, timed_out):
    """Return (ok, reason). Validates console output and exit expectations."""
    if timed_out:
        return False, f"QEMU hung (> {QEMU_TIMEOUT_S}s) — no clean exit"
    if isinstance(out, bytes):
        out = out.decode(errors="replace")
    # The raw output is what a failure reports, always: a reader diagnosing a
    # red row needs the console as the machine wrote it, ticks included.
    hay = out
    if case.get("strip_kernel_lines"):
        hay = _strip_kernel_diagnostics(out)
    expected = case["expect_out"]
    if expected is not None:
        # A single substring or a list of them — one boot can assert several
        # lines without rebuilding the kernel once per assertion.
        if isinstance(expected, str):
            expected = [expected]
        fmt = expectations(arch)
        # Matched in order: each expectation starts where the previous one
        # ended. A console transcript is a sequence, and a case like the task
        # dump asserts that the dump comes after the panic line, which an
        # unordered `in` cannot see.
        #
        # A case may ask for overlapping matches (`overlapping_matches`): the
        # cursor then advances by one character instead of past the whole
        # match, so the next expectation may begin inside the previous one. The
        # default stays non-overlapping and is right for every case asserting
        # lines: two expectations must not be satisfied by one line. It is
        # wrong for the case asserting adjacent pairs, where three crossings
        # can be consecutive; see `thread_preempt`.
        step_one = case.get("overlapping_matches", False)
        cursor = 0
        for want in expected:
            want = want.format(**fmt)
            at = hay.find(want, cursor)
            if at < 0:
                where = "out of order" if want in hay else "missing"
                return False, (f"{where} expected output {want!r} "
                               f"(got {out!r})")
            cursor = at + (1 if step_one else len(want))
    if case["expect_clean_exit"]:
        if status != 0:
            return False, f"expected clean exit (0), got status {status}"
    else:
        if status == 0:
            return False, "expected a failing (non-zero) exit, got 0"
    # An exact status, where the payload encodes its own verdict in one.
    want_status = case.get("expect_status")
    if want_status is not None and status != want_status:
        return False, f"expected exit status {want_status}, got {status}"
    return True, ""


def _run_arch(arch, qemu, lld, clang, blade_bin, selected_cases, jobs):
    """Build and run every case for one architecture. Returns (passed, failed).

    The architectures stay sequential. Two of them at once would build the
    same Blade package for two triples concurrently, and a package's `.build/`
    tree is per-package with the target only one level down: the artifacts
    would not collide, but the stale `.sosimg` sweep at the top of
    `_build_root_image` and the lock/stamp files beside them are one directory
    for both. `jobs` buys its parallelism inside an architecture, where every
    unit of work already has a name of its own, and an arch block's report
    stays contiguous.
    """
    dirs = arch_dirs(arch)
    print(f"{BOLD}{arch['name']}{RESET}  ({arch['triple']}, {os.path.basename(qemu)} `virt`)")

    # A case may name the architectures it applies to. The default is every
    # architecture; an arch list is a claim that the case is about something
    # arch-specific, or that it is blocked on one, and each one says which in a
    # comment beside it. It is decided first, so every "this whole architecture
    # failed" count below is the number of cases that would have run here.
    cases = [c for c in selected_cases
             if arch["name"] in c.get("arches", (arch["name"],))]

    # A case may name the tier it needs. The default is every tier: the object
    # model (pipes, waiters, events, timers, stats, quotas, handles, donation,
    # the allocator) is exercised through syscalls, where handles, rights,
    # quotas and lifetimes are checked identically on every tier, so most of
    # this table carries no `tier` key and runs unchanged on every profile. A
    # `tier` key is a claim that the case's assertion is denial by the
    # hardware, which is the one thing a flat platform cannot do.
    #
    # Nothing is skipped silently. Every excluded case is named, with its
    # reason, in the report below, so a green line means the same in each
    # section. That is why this is a two-list partition rather than one
    # comprehension.
    #
    # Partitioned in one pass and by identity rather than by filtering twice: a
    # case is a plain dict, so `in` would compare contents, and two cases that
    # happened to agree field for field would take each other's place in the
    # split.
    runnable, excluded = [], []
    for case in cases:
        need = case.get("tier")
        (runnable if need is None or need == arch["tier"] else excluded).append(case)
    cases = runnable

    try:
        shared_objs = _build_shared(arch, clang)
    except ToolError as e:
        print(f"{CROSS} failed to build the {arch['name']} kernel HAL / runtime support\n{e}",
              file=sys.stderr)
        return 0, len(cases)

    # Root packages are built by Blade, per architecture, so build them first,
    # but only if some case that runs here needs one. The arch filter is
    # applied first because some packages are per-device: a driver names its
    # device, so each echo package has a `[sos.<triple>]` section for one
    # machine only and building it for the other is a refusal rather than
    # wasted work.
    #
    # Child packages ride the same list. They are not root servers (nothing
    # loads one at boot) but they are built by exactly the same call: a child
    # image goes through the pipeline every SOS process goes through, and the
    # only thing that differs is the linker script its manifest names.
    root_pkgs = []
    for case in cases:
        if case.get("root_pkg") and case["root_pkg"] not in root_pkgs:
            root_pkgs.append(case["root_pkg"])
        for child in case.get("children", ()):
            if child not in root_pkgs:
                root_pkgs.append(child)
    if root_pkgs:
        # Each package is a separate directory and a separate Blade invocation,
        # so the only thing the pool has to preserve is the order these lines
        # print in, which `_map_ordered` does. The failure arm keeps the serial
        # shape too: the first package to fail in list order is the one
        # reported, and the run gives up on this architecture there.
        def build_pkg(pkg):
            try:
                image = _build_root_image(blade_bin, pkg, arch, clang)
                return (os.path.relpath(image, REPO_ROOT),
                        os.path.getsize(image), None)
            except ToolError as e:
                return (None, None, e)

        build_error = None
        for rel, size, err in _map_ordered(jobs, root_pkgs, build_pkg):
            if err is not None:
                build_error = err
                break
            print(f"  {rel}  ({size} bytes)")
        if build_error is not None:
            print(f"{CROSS} failed to build a {arch['name']} root image"
                  f"\n{build_error}", file=sys.stderr)
            return 0, len(cases)
        # On this thread, before any case starts: the case dictionaries are the
        # one table every worker reads, so the only writes into them happen
        # here, where there is exactly one writer.
        for case in cases:
            if case.get("root_pkg"):
                holder = case.setdefault("_root_image", {})
                holder[arch["name"]] = _root_image_path(case["root_pkg"], arch)
            if case.get("children"):
                holder = case.setdefault("_child_images", {})
                holder[arch["name"]] = [_root_image_path(c, arch)
                                        for c in case["children"]]

    # One case = one unit of work: compile it, link it, boot it, judge it. A
    # worker returns its verdict and the lines it owns and prints nothing, so
    # the console is written by one thread walking `cases` in order and a
    # parallel run's report is the serial run's report.
    def run_case(case):
        name = case["name"]
        try:
            elf = _build_elf(case, arch, shared_objs, lld, clang)
        except ToolError as e:
            return (False, f"{CROSS} {name}  (build error)",
                    [f"    {line}" for line in str(e).splitlines()])
        status, out, timed_out = _run_qemu(qemu, arch, elf, case.get("stdin"))
        ok, reason = _check(case, arch, status, out, timed_out)
        if ok:
            return True, f"{CHECK} {name}", []
        return False, f"{CROSS} {name}  ({reason})", []

    # The excluded cases, by name, with the reason. Printed here rather than
    # folded into the numbered rows because these cases did not run and did not
    # pass, and a `[i/N]` line saying either would be a lie. It sits immediately
    # above the rows so the relationship between this list and that `N` is on
    # the screen at once.
    #
    # The block is skipped entirely when the list is empty, which keeps the
    # riscv32 and arm64 sections free of it: on an isolated profile every
    # `tier` key matches and nothing is excluded. Only the flat profile's
    # section grows it.
    if excluded:
        print(f"  tier {arch['tier']}: {len(excluded)} isolation "
              f"{'proof' if len(excluded) == 1 else 'proofs'} excluded "
              f"(this platform cannot deny, and says so)")
        for case in excluded:
            print(f"    - {case['name']}: {case['tier_reason']}")

    passed = 0
    failed = 0
    for i, (ok, headline, detail) in enumerate(
            _map_ordered(jobs, cases, run_case), 1):
        print(f"[{i}/{len(cases)}] {headline}")
        for line in detail:
            print(line)
        if ok:
            passed += 1
        else:
            failed += 1
    print()
    return passed, failed


def _root_image_path(pkg_dir, arch):
    out_dir = os.path.join(pkg_dir, ".build", arch["triple"])
    images = [f for f in os.listdir(out_dir) if f.endswith(".sosimg")]
    return os.path.join(out_dir, images[0])


def main():
    parser = argparse.ArgumentParser(description="SOS QEMU test harness")
    parser.add_argument("--arch", metavar="NAME",
                        help="run one architecture (default: every one — the GATE is every one)")
    parser.add_argument("--case", metavar="NAME", action="append", dest="cases",
                        help="run only the named case(s) — repeatable, or one "
                             "comma-separated list; hyphens and underscores are "
                             "interchangeable. A DEVELOPMENT convenience: the "
                             "GATE is every case on every architecture")
    parser.add_argument("-j", "--jobs", metavar="N", type=int, default=None,
                        help="run N package builds, and then N cases, at a "
                             "time (default: this host's performance-core "
                             "count, or SOS_JOBS when set; `-j 1` is the "
                             "serial harness)")
    parser.add_argument("--board", metavar="NAME", default="virt",
                        help="board profile (default: virt, the GATE's). "
                             "`esp32c3` is the gating smoke target, run "
                             "after the suite: it needs the Espressif "
                             "toolchain on the gate's machine")
    args = parser.parse_args()

    if args.board != "virt":
        # ------------------------------------------------------------------
        # Board section: esp32c3. Everything board-specific lands here and only
        # here.
        # ------------------------------------------------------------------
        if args.board != "esp32c3":
            print(f"{RED}unknown --board {args.board!r}; known: virt, "
                  f"esp32c3{RESET}", file=sys.stderr)
            sys.exit(2)

        # ------------------------------------------------------------------
        # The ESP32-C3 smoke. Gating: the server runs it after `make
        # sos-test` (it is not part of that target) against the Espressif QEMU
        # on the machine that runs the gate.
        #
        # It shares no table, no build directory and no run path with the gate
        # above. The board is different in every way that matters (direct boot
        # from a flash image rather than `-kernel`, XIP text, no exit door).
        # The build directory has to differ even though the target triple does
        # not: both profiles are `riscv32-unknown-none-elf`, so a shared
        # `.build/<triple>/` would let `make sos-smoke-esp32c3` silently
        # clobber the objects `make sos-test` is about to link. And the verdict
        # is read differently: this part has no sifive_test finisher and
        # Espressif QEMU gives a guest no shutdown door, so the emulator never
        # exits on its own and its exit status carries nothing; the transcript
        # is the whole of the result, and the kernel's `exit_pass`/`exit_fail`
        # say which it was in words.
        #
        # Serial, not `-j`: a few cases, and a smoke whose job is to be
        # diffable has nothing to gain from overlapping them.
        #
        # Everything below was derived from the running emulator and is
        # recorded with its probe in hal/riscv32-esp32c3/ABI.md.
        #
        # The three imports are local on purpose: this section is the only
        # thing in the file that needs them.
        # ------------------------------------------------------------------
        import glob
        import select
        import struct

        C3_HAL = os.path.join(HAL_DIR, "riscv32-esp32c3")
        C3_BUILD = os.path.join(REPO_ROOT, ".build", "esp32c3", "sos")
        C3_TRIPLE = "riscv32-unknown-none-elf"      # what sawc/blade are told
        C3_FEATURES = "+m,+c"                       # RV32IMC — NO `+a`
        C3_ESP_TRIPLE = "riscv32-esp-unknown-elf"   # esp-clang's own row
        C3_MARCH = "-march=rv32imc_zicsr_zifencei"
        C3_MABI = "-mabi=ilp32"
        C3_FLASH_BYTES = 4 * 1024 * 1024
        C3_MAGIC = 0xAEDB041D
        C3_CHILD_BASE = 0x403C5000
        C3_CHILD_LEN = 0x17000
        C3_HALT = "SOS-C3: halt"
        C3_TIMEOUT_S = 20

        def c3_tool(pattern, what, hint):
            """Find a machine-local Espressif tool, newest version first."""
            hits = sorted(glob.glob(os.path.expanduser(pattern)), reverse=True)
            if not hits:
                print(f"{RED}no {what} found at {pattern}{RESET}",
                      file=sys.stderr)
                print(f"  {hint}", file=sys.stderr)
                sys.exit(2)
            return hits[0]

        qemu = c3_tool(
            "~/.espressif/tools/qemu-riscv32/*/qemu/bin/qemu-system-riscv32",
            "Espressif QEMU",
            "install it with the ESP-IDF tools installer; the gate "
            "needs it, so its absence fails the gate")
        clang = c3_tool(
            "~/.espressif/tools/esp-clang/*/esp-clang/bin/clang",
            "esp-clang",
            "the C3 build needs the no-A multilib row "
            "(rv32imc_zicsr_zifencei); see hal/riscv32-esp32c3/ABI.md §3")
        lld = os.path.join(os.path.dirname(clang), "ld.lld")
        objcopy = os.path.join(os.path.dirname(clang), "llvm-objcopy")

        os.makedirs(C3_BUILD, exist_ok=True)

        # The arch dict `_build_root_image` wants. Its `triple` is what blade
        # and sawc are told; a root package's own artifacts live under
        # `<package>/.build/<triple>/`, and the C3 packages are distinct
        # packages, so nothing collides with the gate there.
        c3_arch = {"name": "riscv32-esp32c3", "triple": C3_TRIPLE}

        def c3_cc(src, obj, extra=()):
            _run([clang, f"--target={C3_ESP_TRIPLE}", C3_MARCH, C3_MABI,
                  "-nostdlib", "-ffreestanding", *extra, "-c", src, "-o", obj])
            return obj

        def c3_payload_stub(name, sosimg):
            """The `.incbin` stub that pulls a root sosimg into `.payload`."""
            stub_s = os.path.join(C3_BUILD, f"{name}.rootimg.S")
            with open(stub_s, "w") as f:
                f.write("/* GENERATED by tools/sos_runner.py --board esp32c3 */\n"
                        "    .section .payload, \"ax\", @progbits\n"
                        "    .balign 16\n"
                        f"    .incbin \"{sosimg}\"\n")
            return c3_cc(stub_s, os.path.join(C3_BUILD, f"{name}.rootimg.o"))

        def c3_regions_stub(name, child_images):
            """The child blobs and the boot region table that names them.

            The virt path's `_stitch_regions` cannot be reused: it sizes a
            child's destination row from the module-level `CHILD_REGION_LEN`,
            which is too large for this board's SRAM. Row order is the same
            contract (every blob, then every destination) because root's config
            reads it as tags.
            """
            lines = ["/* GENERATED by tools/sos_runner.py --board esp32c3 */",
                     "    .section .childimg, \"a\", @progbits",
                     "    .balign 16"]
            for i, img in enumerate(child_images):
                lines += [f"_sos_child{i}_start:",
                          f"    .incbin \"{img}\"",
                          f"_sos_child{i}_end:",
                          "    .balign 16"]
            rows = [(f"_sos_child{i}_start",
                     f"_sos_child{i}_end - _sos_child{i}_start",
                     REGION_KIND_RAM) for i in range(len(child_images))]
            rows += [(f"{C3_CHILD_BASE + i * C3_CHILD_LEN:#x}",
                      f"{C3_CHILD_LEN:#x}", REGION_KIND_RAM)
                     for i in range(len(child_images))]
            lines += ["", "    .section .regions, \"a\", @progbits",
                      "    .balign 8",
                      f"    .4byte {REGION_TABLE_MAGIC:#010x}",
                      f"    .2byte {REGION_TABLE_VERSION}",
                      f"    .byte  {len(rows)}",
                      "    .byte  0"]
            for i, (base, length, kind) in enumerate(rows):
                # 64-bit fields on a 32-bit assembler: low half then a zero
                # high half, which is the same bytes little-endian.
                lines += [f"    /* row {i} */",
                          f"    .4byte {base}", "    .4byte 0",
                          f"    .4byte {length}", "    .4byte 0",
                          f"    .byte  {kind}",
                          "    .byte  0, 0, 0, 0, 0, 0, 0"]
            stub_s = os.path.join(C3_BUILD, f"{name}.regions.S")
            with open(stub_s, "w") as f:
                f.write("\n".join(lines) + "\n")
            return c3_cc(stub_s, os.path.join(C3_BUILD, f"{name}.regions.o"))

        def c3_flash_image(name, objs):
            """Link, flatten, and check the direct-boot magic is really there."""
            elf = os.path.join(C3_BUILD, f"{name}.elf")
            raw = os.path.join(C3_BUILD, f"{name}.bin")
            flash = os.path.join(C3_BUILD, f"{name}.flash.bin")
            _run([lld, "-T", os.path.join(C3_HAL, "kernel", "esp32c3.ld"),
                  "--gc-sections", "-o", elf, *objs])
            _run([objcopy, "-O", "binary", elf, raw])
            with open(raw, "rb") as f:
                image = f.read()
            # The one invariant the harness checks itself. Without these two
            # words the mask ROM does not enter the image at all and the run
            # fails as a silent timeout with no output, a failure mode that
            # says nothing. Checking here turns it into a sentence.
            m0, m1 = struct.unpack_from("<II", image, 0)
            if m0 != C3_MAGIC or m1 != C3_MAGIC:
                raise ToolError(
                    f"{name}: flash image lacks the direct-boot magic "
                    f"(read {m0:#010x} {m1:#010x}, want {C3_MAGIC:#010x} twice)"
                    " — check .magic in hal/riscv32-esp32c3/kernel/esp32c3.ld")
            if len(image) > C3_FLASH_BYTES:
                raise ToolError(f"{name}: image is {len(image)} bytes, "
                                f"flash is {C3_FLASH_BYTES}")
            with open(flash, "wb") as f:
                f.write(image + b"\xff" * (C3_FLASH_BYTES - len(image)))
            return flash, len(image)

        def c3_run(flash):
            """Boot the image and collect the console until it halts.

            The machine never exits on its own (there is no finisher and no
            shutdown door), so the run ends at the kernel's own halt line or at
            the timeout, and QEMU is killed either way. Stderr is discarded
            rather than mixed into the transcript: QEMU writes
            `Adding SPI flash device` there on every run, which is noise a
            diff should never see.
            """
            proc = subprocess.Popen(
                [qemu, "-machine", "esp32c3", "-nographic",
                 "-drive", f"file={flash},if=mtd,format=raw"],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL)
            out = b""
            deadline = time.time() + C3_TIMEOUT_S
            halted = False
            try:
                while time.time() < deadline:
                    ready, _, _ = select.select([proc.stdout], [], [], 0.2)
                    if ready:
                        chunk = os.read(proc.stdout.fileno(), 4096)
                        if not chunk:
                            break
                        out += chunk
                        if C3_HALT.encode() in out:
                            # Drain what is still in flight, polling rather
                            # than reading: the halt line is usually the last
                            # thing the machine ever writes, so a blocking read
                            # here waits on a guest that has stopped talking,
                            # which is a hang, not a timeout, because the
                            # deadline above is no longer being consulted.
                            drain_until = time.time() + 0.3
                            while time.time() < drain_until:
                                more_ready, _, _ = select.select(
                                    [proc.stdout], [], [], 0.05)
                                if not more_ready:
                                    continue
                                more = os.read(proc.stdout.fileno(), 4096)
                                if not more:
                                    break
                                out += more
                            halted = True
                            break
                    elif proc.poll() is not None:
                        break
            finally:
                proc.kill()
                proc.wait()
                proc.stdout.close()
            return out.decode("utf-8", "replace"), halted

        # The smoke list, in the order it is reported. `boot` needs no package
        # at all: a kernel with no root image appended must say so, which
        # proves direct boot, the XIP linker layout and the Espressif console
        # sink in one.
        C3_BANNER = "SOS M1: kernel up on riscv32 (ESP32-C3)"
        C3_ROM = "ESP-ROM:esp32c3-api1-20210207"
        c3_cases = [
            {
                "name": "boot",
                "root_pkg": None,
                "children": [],
                "expect": [C3_ROM, C3_BANNER,
                           "SOS: bad root image: no root image appended",
                           "SOS-C3: halt fail code=0x00000004"],
            },
            {
                "name": "timer",
                "root_pkg": os.path.join(TESTS_DIR, "c3-timer"),
                "children": [],
                "expect": [C3_ROM, C3_BANNER,
                           "SOS c3timer: arming",
                           "SOS c3timer: tick 1 key=37 fires=1",
                           "SOS c3timer: tick 2 key=37 fires=1",
                           "SOS c3timer: tick 3 key=37 fires=1",
                           "SOS c3timer: tick 4 key=37 fires=1",
                           "SOS c3timer: tick 5 key=37 fires=1",
                           "SOS c3timer: done ticks=5 slept=1",
                           "SOS-C3: halt pass"],
            },
            {
                "name": "isolation",
                "root_pkg": os.path.join(TESTS_DIR, "c3-isolation"),
                "children": [os.path.join(TESTS_DIR, "c3-child-poke")],
                "expect": [C3_ROM, C3_BANNER,
                           "SOS c3iso: started",
                           "SOS: fault ",
                           "SOS c3iso: root survived child status=131072",
                           "SOS c3iso: child syscalls=0 faults=1",
                           "SOS c3iso: done",
                           "SOS-C3: halt pass"],
            },
            {
                "name": "irq_timer_source_refused",
                "root_pkg": os.path.join(TESTS_DIR, "c3-irq-kernel-timer"),
                "children": [],
                "expect": [C3_ROM, C3_BANNER,
                           "SOS c3irq: binding the kernel timer's source",
                           "SOS: process fault: argument outside its domain"],
            },
            {
                "name": "cas",
                "root_pkg": os.path.join(TESTS_DIR, "c3-cas"),
                "children": [],
                "expect": [C3_ROM, C3_BANNER,
                           "SOS c3cas: start value=5",
                           "SOS c3cas: swap 5->9 ok=true value=9",
                           "SOS c3cas: swap 5->7 ok=false value=9",
                           "SOS c3cas: done",
                           "SOS-C3: halt pass"],
            },
        ]

        print(f"{BOLD}SOS ESP32-C3 board smoke{RESET} (GATING)")
        print(f"  qemu   {qemu}")
        print(f"  clang  {clang}")
        print(f"  target {C3_TRIPLE} --target-features {C3_FEATURES} "
              f"({C3_MARCH[len('-march='):]} — no A extension)")

        blade_bin = _build_blade(C3_BUILD)

        # The kernel is one compile for every case: they differ in what is
        # appended to it, never in the kernel itself. `boot.S` is this board's;
        # `trap.S` and `sink.c` are the shared riscv32 ones, out of
        # `hal/riscv32-common/kernel/`.
        shared = [c3_cc(os.path.join(C3_HAL, "kernel", "boot.S"),
                        os.path.join(C3_BUILD, "boot.o")),
                  c3_cc(os.path.join(RV32_COMMON_DIR, "kernel", "trap.S"),
                        os.path.join(C3_BUILD, "trap.o")),
                  c3_cc(os.path.join(RV32_COMMON_DIR, "kernel", "sink.c"),
                        os.path.join(C3_BUILD, "sink.o"), extra=("-O2",)),
                  c3_cc(os.path.join(RT_COMMON_C_DIR, "support.c"),
                        os.path.join(C3_BUILD, "support.o"), extra=("-O2",))]
        kobj = os.path.join(C3_BUILD, "kernel.o")
        _run(tc().sawc() + [os.path.join(KERNEL_DIR, "main.saw"), "-o", kobj,
                            "-Oz",
                            "--freestanding", "--no-hidden-alloc",
                            "--runtime-provider",
                            "--target", C3_TRIPLE,
                            "--target-features", C3_FEATURES,
                            "--module-path", CORE_MODULE,
                            "--module-path",
                            f"hal={os.path.join(C3_HAL, 'kernel')}",
                            # This board's `hal` re-exports the shared riscv32
                            # core, so `rv32core` rides beside it exactly as it
                            # does on the virt path.
                            "--module-path", RV32_CORE_MODULE,
                            "--module-path", tc().module_path_arg("imgformat"),
                            "--module-path", SOSRT_MODULE,
                            "--module-path", SOSABI_MODULE])

        failed = 0
        for i, case in enumerate(c3_cases, 1):
            name = case["name"]
            try:
                objs = list(shared) + [kobj]
                if case["root_pkg"]:
                    objs.append(c3_payload_stub(
                        name, _build_root_image(blade_bin, case["root_pkg"],
                                                c3_arch, clang)))
                if case["children"]:
                    objs.append(c3_regions_stub(
                        name, [_build_root_image(blade_bin, pkg, c3_arch, clang)
                               for pkg in case["children"]]))
                flash, size = c3_flash_image(name, objs)
                out, halted = c3_run(flash)
            except ToolError as e:
                print(f"[{i}/{len(c3_cases)}] {CROSS} {name}")
                print(f"    {e}")
                failed += 1
                continue

            missing = None
            cursor = 0
            for want in case["expect"]:
                at = out.find(want, cursor)
                if at < 0:
                    missing = want
                    break
                cursor = at + len(want)
            if missing is None and halted:
                print(f"[{i}/{len(c3_cases)}] {CHECK} {name}  "
                      f"({size} bytes of flash)")
            else:
                failed += 1
                print(f"[{i}/{len(c3_cases)}] {CROSS} {name}")
                if not halted:
                    print(f"    the kernel never halted within "
                          f"{C3_TIMEOUT_S}s")
                if missing is not None:
                    print(f"    expected and not found, in order: {missing!r}")
                print("    --- console ---")
                for line in out.splitlines():
                    print(f"    {line}")
                print("    --- end ---")

        print()
        print("=" * 60)
        if failed:
            print(f"{RED}SOS ESP32-C3 SMOKE FAILED{RESET} "
                  f"({failed} of {len(c3_cases)})")
        else:
            print(f"{GREEN}ESP32-C3 SMOKE PASSED{RESET} "
                  f"({len(c3_cases)} cases)")
        print("=" * 60)
        sys.exit(1 if failed else 0)

    if args.jobs is None:
        args.jobs = _default_jobs()
    elif args.jobs < 1:
        print(f"{RED}-j must be at least 1 (got {args.jobs}){RESET}",
              file=sys.stderr)
        sys.exit(2)

    arches = ARCHES
    if args.arch:
        arches = [a for a in ARCHES if a["name"] == args.arch]
        if not arches:
            names = ", ".join(a["name"] for a in ARCHES)
            print(f"{RED}unknown --arch {args.arch!r}; known: {names}{RESET}",
                  file=sys.stderr)
            sys.exit(2)

    # The case filter is applied to the one table everything downstream reads,
    # so a filtered run builds only what the named cases need (the blade build
    # below and the per-arch package list both see the narrowed list). Names
    # are matched with '-' and '_' interchangeable because the case names use
    # underscores while the tests/ directories use hyphens.
    selected_cases = TEST_CASES
    if args.cases:
        wanted = [w.strip().replace("-", "_")
                  for value in args.cases for w in value.split(",") if w.strip()]
        by_name = {c["name"].replace("-", "_"): c for c in TEST_CASES}
        unknown = [w for w in wanted if w not in by_name]
        if unknown:
            import difflib
            for w in unknown:
                close = difflib.get_close_matches(w, by_name, n=3)
                hint = f" (did you mean: {', '.join(close)}?)" if close else ""
                print(f"{RED}unknown --case {w!r}{hint}{RESET}", file=sys.stderr)
            sys.exit(2)
        seen = set()
        names = [w for w in wanted if not (w in seen or seen.add(w))]
        selected_cases = [by_name[w] for w in names]

    qemus, lld, clang = _probe_tools(arches)
    if not _check_arch_free():
        sys.exit(1)

    # Resolve the toolchain HERE, on this thread, before anything else touches
    # it. `tc()` is a lazy global with a one-time print, and a lazy global
    # first touched by several workers at once is two resolutions and a note
    # printed into the middle of the report. After this line every worker
    # reads a value that is already there.
    sawlang = tc()

    print(f"{BOLD}SOS QEMU tests{RESET}")
    print(f"  clang: {clang}")
    print(f"  lld  : {lld}")
    # One line per distinct emulator, in first-use order. This header names the
    # tools a run used, and two profiles of one architecture use one binary:
    # riscv32 and riscv32-flat are the same `qemu-system-riscv32`. Printing it
    # twice would say there were two.
    seen_qemus = []
    for arch in arches:
        binary = qemus[arch["name"]]
        if binary not in seen_qemus:
            seen_qemus.append(binary)
            print(f"  qemu : {binary}")
    # Which resolution step answered and what it pointed at. A build against
    # the pinned fetch names the fetch step; an env step (SAWLANG_ROOT, SAWC,
    # BLADE) builds against whatever it names and ignores the pin, so the
    # transcript has to show it.
    print(f"  sawlang: {sawlang.source}  "
          f"{sawlang.root or sawlang.sawc_env_value()}")
    print()

    # Blade is architecture-neutral (a host binary), so it is built once and
    # driven per target.
    blade_bin = None
    if any(case.get("root_pkg") or case.get("children")
           for case in selected_cases):
        shared_build = os.path.join(REPO_ROOT, ".build", "sos-host")
        os.makedirs(shared_build, exist_ok=True)
        try:
            print(f"{BOLD}building blade{RESET}")
            blade_bin = _build_blade(shared_build)
        except ToolError as e:
            print(f"{CROSS} failed to build blade\n{e}", file=sys.stderr)
            sys.exit(1)
        print()

    total_passed = 0
    total_failed = 0
    for arch in arches:
        passed, failed = _run_arch(arch, qemus[arch["name"]], lld, clang,
                                   blade_bin, selected_cases, args.jobs)
        total_passed += passed
        total_failed += failed

    print("=" * 60)
    if total_failed == 0:
        names = " + ".join(a["name"] for a in arches)
        print(f"{GREEN}{BOLD}ALL SOS TESTS PASSED{RESET} "
              f"({total_passed} passed across {names})")
        print("=" * 60)
        sys.exit(0)
    else:
        print(f"{RED}{BOLD}SOS TESTS FAILED{RESET} "
              f"({total_passed} passed, {total_failed} failed)")
        print("=" * 60)
        sys.exit(1)


if __name__ == "__main__":
    main()
