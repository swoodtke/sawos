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

The images are built by the image builder, `tools/sosimage/` (a hosted Saw
program; see "The image builder" below). Per architecture:
  1. sosimage native : boot.S, any trap entry split out of it, sink.c (board
                       hooks + protection) and support.c (the arena accessors
                       and the word atomics — the C that must stay C), once
  2. blade           : every root and child package the cases name, once
  3. sosimage kernel : each kernel entry the cases boot, once (sawc
                       --freestanding --no-hidden-alloc --target <triple>,
                       --module-path kcore=… hal=… — the arch-free kernel and
                       the HAL it reaches the machine through)
Per case:
  4. sosimage link   : the `.payload` blob, if the case has one — EITHER a
                       hand-written `.S` from `tests/<arch>/` (user-mode code,
                       hand-assembled sosimgs) OR a root package's image —,
                       the child images and the boot region table; ld.lld
                       with the HAL's virt.ld --gc-sections -> <name>.elf
  5. qemu            : run with a hard timeout; capture console stdout + exit
                       status

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
identical is stated at the three places it is needed: the per-case staging
directory (`_image_description`, and `_stitch_root_image` on the runner's own
path), `_run_qemu`'s own stdin, and `main`'s eager `tc()`.
"""

import argparse
import ast
import concurrent.futures
import json
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
DRIVERS_DIR = os.path.join(REPO_ROOT, "drivers")
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

# `ExitCode.Deadlock` in kernel/core/diag.saw: what the machine exits with when
# every thread is blocked and nothing can wake one. Kept in step with that enum.
EXIT_DEADLOCK = 7

# `ExitCode.KilledRoot` in kernel/core/diag.saw: what the machine exits with
# when root is killed through `ProcessOp.Kill`. Kept in step with that enum.
EXIT_KILLED_ROOT = 8

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
# Two preempted threads of one process allocating from the bump arena.
ARENA_RACE_PKG = os.path.join(TESTS_DIR, "arena-race")
# User FP/SIMD state, arm64 only: two threads' patterns across yields, and a
# second process's first use after the first has filled the registers.
FP_THREADS_PKG = os.path.join(TESTS_DIR, "fp-threads")
FP_LEAK_PKG = os.path.join(TESTS_DIR, "fp-leak")
CHILD_FP_LEAK_PKG = os.path.join(TESTS_DIR, "child-fp-leak")
# The scheduler tick taking the processor from a process that never yields.
TICK_SPINNER_PKG = os.path.join(TESTS_DIR, "tick-spinner")
CHILD_SPIN_PKG = os.path.join(TESTS_DIR, "child-spin")
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
# With no line bound, "nothing runnable" is still a deadlock, and it stops the
# machine.
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
# A created process that is never started is torn down when the last name for it
# goes: `unstarted-released` releases the handle itself, `unstarted-launcher`
# has its launcher die holding it.
UNSTARTED_RELEASED_PKG = os.path.join(TESTS_DIR, "unstarted-released")
UNSTARTED_LAUNCHER_PKG = os.path.join(TESTS_DIR, "unstarted-launcher")
CHILD_LAUNCHER_PKG = os.path.join(TESTS_DIR, "child-launcher")
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
# Which process an op acts on (spec 2.7): the creation ops through another
# process's handle, and the caller errors that fault the caller. `child-xhost`
# is the process created through, `child-xcaller` the helper that makes the
# mistaken op, and the sink is a child that is created and never started.
CREATE_CROSS_PKG = os.path.join(TESTS_DIR, "create-cross")
CREATE_CROSS_FAULT_PKG = os.path.join(TESTS_DIR, "create-cross-fault")
CHILD_XHOST_PKG = os.path.join(TESTS_DIR, "child-xhost")
CHILD_XCALLER_PKG = os.path.join(TESTS_DIR, "child-xcaller")
CREATE_CROSS_QUOTA_PKG = os.path.join(TESTS_DIR, "create-cross-quota")
CREATE_BUDGET_PKG = os.path.join(TESTS_DIR, "create-budget")
CHILD_XBUDGET_PKG = os.path.join(TESTS_DIR, "child-xbudget")
CREATE_PRESTART_PKG = os.path.join(TESTS_DIR, "create-prestart")
START_BAD_TAG_PKG = os.path.join(TESTS_DIR, "start-bad-tag")
GIVE_WORD_DEAD_PKG = os.path.join(TESTS_DIR, "give-word-dead")
MINT_REVOKED_PKG = os.path.join(TESTS_DIR, "mint-revoked")
HANDLE_INFO_PKG = os.path.join(TESTS_DIR, "handle-info")
HANDLE_INFO_SELECTOR_PKG = os.path.join(TESTS_DIR, "handle-info-selector")
# Rights ceilings: `rights-ceiling` drives them through successive runs of one
# child image (`child-ceiling`, linked at the first child base) and a
# never-started second child that serves as a ceiling source (`child-hold2`,
# linked at the second). The `ceiling-*` roots are single-purpose:
# derived-object mints, a kind that is not one, and the two rights that gate
# the ops.
RIGHTS_CEILING_PKG = os.path.join(TESTS_DIR, "rights-ceiling")
CHILD_CEILING_PKG = os.path.join(TESTS_DIR, "child-ceiling")
CEILING_DERIVED_PKG = os.path.join(TESTS_DIR, "ceiling-derived")
CEILING_BADKIND_PKG = os.path.join(TESTS_DIR, "ceiling-badkind")
CEILING_GATE_RESTRICT_PKG = os.path.join(TESTS_DIR, "ceiling-gate-restrict")
CEILING_GATE_READ_PKG = os.path.join(TESTS_DIR, "ceiling-gate-read")
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
POOL_ALLOCATE_PKG = os.path.join(TESTS_DIR, "pool-allocate")
# The pool's own cases: its two ops read against each other, its free list
# against a model of first fit, the free-list nodes a pool gives back, its
# refusals and the rights it hands down (with a child that does one thing to
# the pool it is given), what holds it, and the typed rows it comes from.
POOL_OPS_PKG = os.path.join(TESTS_DIR, "pool-ops")
POOL_FIRST_FIT_PKG = os.path.join(TESTS_DIR, "pool-first-fit")
POOL_NODES_PKG = os.path.join(TESTS_DIR, "pool-nodes")
POOL_FAULTS_PKG = os.path.join(TESTS_DIR, "pool-faults")
CHILD_POOL_LEG_PKG = os.path.join(TESTS_DIR, "child-pool-leg")
POOL_LIFETIME_PKG = os.path.join(TESTS_DIR, "pool-lifetime")
POOL_BOOT_PKG = os.path.join(TESTS_DIR, "pool-boot")
POOL_SKEW_PKG = os.path.join(TESTS_DIR, "pool-skew")
POOL_FREED_PKG = os.path.join(TESTS_DIR, "pool-freed")
MAP_BASICS_PKG = os.path.join(TESTS_DIR, "map-basics")
MAP_UNMAP_PKG = os.path.join(TESTS_DIR, "map-unmap")
MAP_INTO_CHILD_PKG = os.path.join(TESTS_DIR, "map-into-child")
IOMEMORY_CARVE_PKG = os.path.join(TESTS_DIR, "iomemory-carve")
CHILD_TOUCH_PKG = os.path.join(TESTS_DIR, "child-touch")
DESTINATION_HELD_PKG = os.path.join(TESTS_DIR, "destination-held")
CHILD_LIVE_DATA_PKG = os.path.join(TESTS_DIR, "child-live-data")
MAPPED_BYTES_HELD_PKG = os.path.join(TESTS_DIR, "mapped-bytes-held")
DONATE_ROWS_HELD_PKG = os.path.join(TESTS_DIR, "donate-rows-held")

# Quotas and the reference count. The three refcount cases are root's, because
# free-on-zero is about objects and root may hold any number of them. The two
# quota cases need a child: root is init, its policy cap is the machine, so its
# ledger rows are unlimited and only the slab can refuse it. A budget belongs to
# a launched process, so proving one costs a launcher and a child.
QUOTA_EXCEEDED_PKG = os.path.join(TESTS_DIR, "quota-exceeded")
QUOTA_VS_WALL_PKG = os.path.join(TESTS_DIR, "quota-vs-wall")
REFCOUNT_FREE_PKG = os.path.join(TESTS_DIR, "refcount-free")
INTERRUPT_UNBIND_PKG = os.path.join(TESTS_DIR, "interrupt-unbind")

# The Interrupt object's lifetime and binding (spec 2.8, 2.10). An Interrupt has
# one name, its handle: bound through System, given or sent to a driver, and
# destroyed by the release of that handle (or the death of its holder) whether
# or not a Waiter is attached. `irq-lines` is not a package: it holds the board
# facts (a line number) the tests link per target.
IRQ_BIND_DENIED_PKG = os.path.join(TESTS_DIR, "irq-bind-denied")
IRQ_BIND_TWICE_PKG = os.path.join(TESTS_DIR, "irq-bind-twice")
IRQ_BIND_TIMER_PKG = os.path.join(TESTS_DIR, "irq-bind-timer")
IRQ_CONSUME_REFUSED_PKG = os.path.join(TESTS_DIR, "irq-consume-refused")
IRQ_MINT_DENIED_PKG = os.path.join(TESTS_DIR, "irq-mint-denied")
IRQ_ATTACHED_PKG = os.path.join(TESTS_DIR, "irq-attached")
IRQ_GIVE_PKG = os.path.join(TESTS_DIR, "irq-give")
GIVE_INTERRUPT_RETURNS_PKG = os.path.join(TESTS_DIR, "give-interrupt-returns")
CHILD_IRQ_PKG = os.path.join(TESTS_DIR, "child-irq")
IRQ_PEER_GONE_PKG = os.path.join(TESTS_DIR, "irq-peer-gone")
CHILD_HOLD_PKG = os.path.join(TESTS_DIR, "child-hold")
CHILD_HOLD2_PKG = os.path.join(TESTS_DIR, "child-hold2")
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
# A deadlock found after a process's death: root waits on an Event nobody
# signals while `child-bye` exits.
DEADLOCK_AFTER_DEATH_PKG = os.path.join(TESTS_DIR, "deadlock-after-death")
# A deadlock that arises while the kernel idles: the last wake source, a
# one-shot Timer nobody holds, runs down without waking anybody.
DEADLOCK_FROM_IDLE_PKG = os.path.join(TESTS_DIR, "deadlock-from-idle")

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
MAP_NEEDS_MAP_PKG = os.path.join(TESTS_DIR, "map-needs-map")
# An allocation or a window carries the rights of the handle it came
# through: a narrowed pool or parent never yields a wider one.
POOL_KEEPS_RIGHTS_PKG = os.path.join(TESTS_DIR, "pool-keeps-rights")
POOL_KEEPS_RIGHTS_TWICE_PKG = os.path.join(TESTS_DIR,
                                           "pool-keeps-rights-twice")
CARVE_KEEPS_RIGHTS_PKG = os.path.join(TESTS_DIR, "carve-keeps-rights")

# A typed `mint(rights:)` narrows, and the withheld right is refused. Each is
# its own image because the refusal is a fault, which ends the process.
MINT_EVENT_PKG = os.path.join(TESTS_DIR, "mint-event")
MINT_WAITER_PKG = os.path.join(TESTS_DIR, "mint-waiter")
MINT_TIMER_PKG = os.path.join(TESTS_DIR, "mint-timer")
MINT_THREAD_PKG = os.path.join(TESTS_DIR, "mint-thread")
MINT_CLOCK_PKG = os.path.join(TESTS_DIR, "mint-clock")
MINT_IOMEMORY_PKG = os.path.join(TESTS_DIR, "mint-iomemory")
MINT_MAPPING_PKG = os.path.join(TESTS_DIR, "mint-mapping")

# Waiter revocation: one root server, because its three claims (a revocation is
# delivered, the walk is the whole blocked list, the count gates it rather than
# the release) are one story about one release, and splitting them would need
# the same three threads on the same Waiter in each image.
#
# It is written at the C altitude, where a handle is a word: the count claim
# needs two names for one Waiter, and the reference arithmetic mints the second
# with `sos_handle_mint`.
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
PIPE_REPLY_NARROWED_PKG = os.path.join(TESTS_DIR, "pipe-reply-narrowed")
PIPE_REPLY_WAIT_NARROWED_PKG = os.path.join(TESTS_DIR,
                                            "pipe-reply-wait-narrowed")
PIPE_RESOLVE_NARROWED_PKG = os.path.join(TESTS_DIR, "pipe-resolve-narrowed")
CHILD_NARROWED_PKG = os.path.join(TESTS_DIR, "child-narrowed")
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

# Events are givable and shared: given, carried in a message, handed back by a
# refused give, and outliving their owner. `child-event` plays the peer and the
# owner.
EVENT_GIVE_PKG = os.path.join(TESTS_DIR, "event-give")
EVENT_OUTLIVE_PKG = os.path.join(TESTS_DIR, "event-outlive")
EVENT_KEEPER_PKG = os.path.join(TESTS_DIR, "event-keeper")
EVENT_NARROW_PKG = os.path.join(TESTS_DIR, "event-narrow")
GIVE_EVENT_RETURNS_PKG = os.path.join(TESTS_DIR, "give-event-returns")
CHILD_EVENT_PKG = os.path.join(TESTS_DIR, "child-event")

# Threads are givable and counted: given, carried in a message, handed back by
# a refused give, outliving their process as `Exited` or invalid, and freed
# when nothing names them. `child-thread` plays the far process.
THREAD_GIVE_PKG = os.path.join(TESTS_DIR, "thread-give")
THREAD_GIVE_RETURNS_PKG = os.path.join(TESTS_DIR, "thread-give-returns")
THREAD_SLOTS_PKG = os.path.join(TESTS_DIR, "thread-slots")
THREAD_EXIT_PKG = os.path.join(TESTS_DIR, "thread-exit")
CHILD_CHURN_PKG = os.path.join(TESTS_DIR, "child-churn")
CHILD_THREAD_PKG = os.path.join(TESTS_DIR, "child-thread")
THREAD_QUOTA_PKG = os.path.join(TESTS_DIR, "thread-quota")
THREAD_HANG_PKG = os.path.join(TESTS_DIR, "thread-hang")

# A dying server's close-all frees ring room that a parked call fills, while a
# thread of the server is parked on its completion queue.
TEARDOWN_MINT_PKG = os.path.join(TESTS_DIR, "teardown-mint")
CHILD_TEARDOWN_MINT_PKG = os.path.join(TESTS_DIR, "child-teardown-mint")
# A thread only a staged message names is invalidated before the close-all
# releases the message.
TEARDOWN_STAGED_THREAD_PKG = os.path.join(TESTS_DIR, "teardown-staged-thread")

# Waiters and Timers are givable and bound to their creator: given, carried in
# a message, handed back by a refused give, invalidated by their owner's death,
# and freed when nothing names them. `child-waitable` plays the far process.
WAITABLE_GIVE_PKG = os.path.join(TESTS_DIR, "waitable-give")
GIVE_WAITABLE_RETURNS_PKG = os.path.join(TESTS_DIR, "give-waitable-returns")
TIMER_OUTLIVE_PKG = os.path.join(TESTS_DIR, "timer-outlive")
WAITER_OUTLIVE_PKG = os.path.join(TESTS_DIR, "waiter-outlive")
TEARDOWN_DETACH_PKG = os.path.join(TESTS_DIR, "teardown-detach")
FREE_CASCADE_PKG = os.path.join(TESTS_DIR, "free-cascade")
WAITER_PARKED_PKG = os.path.join(TESTS_DIR, "waiter-parked")
# A dying process's Timer is invalidated before its close-all frees the Waiter
# it is attached to.
TEARDOWN_TIMER_FIRST_PKG = os.path.join(TESTS_DIR, "teardown-timer-first")
TIMER_DISARM_PKG = os.path.join(TESTS_DIR, "timer-disarm")
PIPE_REPLY_WAIT_WAITER_PKG = os.path.join(TESTS_DIR, "pipe-reply-wait-waiter")
CHILD_WAITABLE_PKG = os.path.join(TESTS_DIR, "child-waitable")

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
SVC_UART_NS16550_PKG = os.path.join(DRIVERS_DIR, "svc-uart-ns16550")
SVC_UART_PL011_PKG = os.path.join(DRIVERS_DIR, "svc-uart-pl011")
SVC_CLIENT_PKG = os.path.join(TESTS_DIR, "svc-client")
DELEGATE_3P_PKG = os.path.join(TESTS_DIR, "delegate-3p")
CHILD_FORWARD_PKG = os.path.join(TESTS_DIR, "child-forward")
CHILD_FAR_PKG = os.path.join(TESTS_DIR, "child-far")

# The pid cases (spec §2.1, §2.7). `child-pid-client` is one image that
# several cases launch under different boot tags; the far end of a forwarded
# request links at the second child address, as `child-far` does.
PID_BASICS_PKG = os.path.join(TESTS_DIR, "pid-basics")
PID_SENDER_DEAD_PKG = os.path.join(TESTS_DIR, "pid-sender-dead")
PID_SENDER_RIGHTS_PKG = os.path.join(TESTS_DIR, "pid-sender-rights")
PID_FORWARD_PKG = os.path.join(TESTS_DIR, "pid-forward")
PID_STATS_ROW_PKG = os.path.join(TESTS_DIR, "pid-stats-row")
CHILD_PID_CLIENT_PKG = os.path.join(TESTS_DIR, "child-pid-client")
CHILD_PID_SERVER_PKG = os.path.join(TESTS_DIR, "child-pid-server")
CHILD_PID_FORWARD_PKG = os.path.join(TESTS_DIR, "child-pid-forward")
CHILD_PID_FAR_PKG = os.path.join(TESTS_DIR, "child-pid-far")

# The process walk and the lookup by pid (spec §2.8). `child-walk-idle` links
# at the second child address, so it is listed second where it appears.
PROCESS_WALK_PKG = os.path.join(TESTS_DIR, "process-walk")
PROCESS_WALK_CEILING_PKG = os.path.join(TESTS_DIR, "process-walk-ceiling")
PROCESS_WALK_GATE_PKG = os.path.join(TESTS_DIR, "process-walk-gate")
CHILD_WALK_IDLE_PKG = os.path.join(TESTS_DIR, "child-walk-idle")
CHILD_WALK_FAULT_PKG = os.path.join(TESTS_DIR, "child-walk-fault")
PROCESS_WALK_START_PKG = os.path.join(TESTS_DIR, "process-walk-start")

# `ProcessOp.Kill` (spec §8). `child-kill` does one thing to another process,
# by role, and links at the first child address; `child-kill-caller` makes one
# call and links at the second, so the two can run together.
PROCESS_KILL_PKG = os.path.join(TESTS_DIR, "process-kill")
CHILD_KILL_PKG = os.path.join(TESTS_DIR, "child-kill")
CHILD_KILL_CALLER_PKG = os.path.join(TESTS_DIR, "child-kill-caller")
PROCESS_KILL_CALLS_PKG = os.path.join(TESTS_DIR, "process-kill-calls")
PROCESS_KILL_CREATED_PKG = os.path.join(TESTS_DIR, "process-kill-created")
PROCESS_KILL_ROOT_PKG = os.path.join(TESTS_DIR, "process-kill-root")
# `ProcessOp.Kill` on a child in a chosen state (spec §8). `child-kill-victim`
# is a role-driven target; `kill-churn` kills one per round over many rounds
# and `kill-runnable` kills one that is runnable and not running.
CHILD_KILL_VICTIM_PKG = os.path.join(TESTS_DIR, "child-kill-victim")
KILL_CHURN_PKG = os.path.join(TESTS_DIR, "kill-churn")
KILL_RUNNABLE_PKG = os.path.join(TESTS_DIR, "kill-runnable")
KILL_LATE_DOOR_PKG = os.path.join(TESTS_DIR, "kill-late-door")

# Executable memory is not a buffer, and a thread's entry must be executable
# (spec §2.2, §2.7). Both cases launch `child-exec-fault`, once per role.
COPY_FROM_CODE_REFUSED_PKG = os.path.join(TESTS_DIR, "copy-from-code-refused")
THREAD_ENTRY_NOT_EXEC_PKG = os.path.join(TESTS_DIR, "thread-entry-not-exec")
CHILD_EXEC_FAULT_PKG = os.path.join(TESTS_DIR, "child-exec-fault")
MAP_OVER_IMAGE_PKG = os.path.join(TESTS_DIR, "map-over-image")
WX_SEGMENT_REFUSED_PKG = os.path.join(TESTS_DIR, "wx-segment-refused")

# A child running out of its image, launched from a hand-assembled image used
# in place (`tests/<arch>/child_inplace.S`); and, on the ESP32-S3, what a
# region over flash may not be.
IMAGE_HELD_PKG = os.path.join(TESTS_DIR, "image-held")
FLASH_REGION_PKG = os.path.join(TESTS_DIR, "flash-region")
IMAGE_ROWS_HELD_PKG = os.path.join(TESTS_DIR, "image-rows-held")
INPLACE_LAUNCH_PKG = os.path.join(TESTS_DIR, "inplace-launch")
STACK_ROW_HELD_PKG = os.path.join(TESTS_DIR, "stack-row-held")
# Initialized writable data, which the loader copies with its bytes; and an
# empty pool donated.
DATA_INIT_PKG = os.path.join(TESTS_DIR, "data-init")
DONATE_EMPTY_PKG = os.path.join(TESTS_DIR, "donate-empty")
CHILD_WX_FAULT_PKG = os.path.join(TESTS_DIR, "child-wx-fault")

# A buffer that is gone when the answer to a parked call arrives (spec §2.2).
LATE_DOOR_GONE_PKG = os.path.join(TESTS_DIR, "late-door-gone")
CHILD_LATE_DOOR_PKG = os.path.join(TESTS_DIR, "child-late-door")
CHILD_LATE_DOOR2_PKG = os.path.join(TESTS_DIR, "child-late-door2")
# Any memory a row grants is a buffer, judged by the row (spec §2.2).
BUFFER_RULE_PKG = os.path.join(TESTS_DIR, "buffer-rule")
BUFFER_ALIAS_PKG = os.path.join(TESTS_DIR, "buffer-alias")
CHILD_BUFFER_RULE_PKG = os.path.join(TESTS_DIR, "child-buffer-rule")
CHILD_DATA_FIRST_PKG = os.path.join(TESTS_DIR, "child-data-first")
# A process's rows never overlap (spec §2.5, §5.6).
MAP_OVERLAP_PKG = os.path.join(TESTS_DIR, "map-overlap")
MAP_OVERLAP_CARVES_PKG = os.path.join(TESTS_DIR, "map-overlap-carves")
SEGMENTS_MEET_PKG = os.path.join(TESTS_DIR, "segments-meet")
DEST_GRAIN_PKG = os.path.join(TESTS_DIR, "dest-grain")
MAP_USER_ADDRESS_PKG = os.path.join(TESTS_DIR, "map-user-address")
ROOM_REWRITE_PKG = os.path.join(TESTS_DIR, "room-rewrite")
CHILD_ROOM_REWRITE_PKG = os.path.join(TESTS_DIR, "child-room-rewrite")
RO_DESTINATION_PKG = os.path.join(TESTS_DIR, "ro-destination")
CHILD_RO_DESTINATION_PKG = os.path.join(TESTS_DIR, "child-ro-destination")
RO_SEGMENT_PKG = os.path.join(TESTS_DIR, "ro-segment")
CHILD_RO_SEGMENT_PKG = os.path.join(TESTS_DIR, "child-ro-segment")
IMAGE_REREAD_PKG = os.path.join(TESTS_DIR, "image-reread")
DEVICE_FOUR_BYTES_PKG = os.path.join(TESTS_DIR, "device-four-bytes")
REPLY_DOOR_ORDER_PKG = os.path.join(TESTS_DIR, "reply-door-order")
CHILD_REPLY_DOOR_ORDER_PKG = os.path.join(TESTS_DIR, "child-reply-door-order")

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
        # The protection grain, mirroring `hal.PROT_GRAIN`: the one granularity
        # a pool accepts, which `available` answers as its set.
        "prot_grain": 4,
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
        # The kernel executes no FP/SIMD instruction, so its C is compiled
        # without them, and every image is then disassembled to check it
        # (`_check_kernel_fp_free`). Only the kernel's C takes the flag: the
        # HAL's assembly spells the user-state save and load with them, and a
        # user image's C is its own.
        "kernel_c_args": ["-mgeneral-regs-only"],
        "kernel_fp_free": True,
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
        # The protection grain, mirroring `hal.PROT_GRAIN`: the one granularity
        # a pool accepts, which `available` answers as its set.
        "prot_grain": 4096,
        # A mapping here takes an address of its own, so it meets no row of the
        # target's image.
        "map_places": True,
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
        # The protection grain, mirroring `hal.PROT_GRAIN`: the one granularity
        # a pool accepts, which `available` answers as its set.
        "prot_grain": 4,
        "device_base": 0x10000000,
        "device_len": 0x1000,
    },
]

# The ESP32-S3 board, Xtensa, as an arch entry. Not in `ARCHES`: the gate does
# not run it, `--board esp32s3` does (`_esp32s3_smoke`). It has the shape of an
# `ARCHES` entry so that the gate's own helpers serve it unchanged: `arch_dirs`,
# `expectations`, `_check`, Blade's image build, the profile and image
# descriptions the image builder reads, and the runner's own root-image and
# region-table stitchers. Every address mirrors
# `hal/xtensa-esp32s3/kernel/lib.saw` and the board's `user/` linker scripts.
ESP32S3_ARCH = {
    "name": "xtensa-esp32s3",
    "triple": "xtensa-esp-unknown-elf",
    # The core has no privilege at all (hal/xtensa-esp32s3/ABI.md §5e).
    "tier": TIER_FLAT,
    # Espressif's clang, after `--target=<triple>`: the chip, and the two cc1
    # features that make C and assembly call0 and FPU-free like sawc's objects
    # (ABI.md §1a; the driver has no `-mabi`). `-fintegrated-as` keeps `.S`
    # files off the external assembler, whose compressed debug sections this
    # board's ld.lld refuses.
    "cc_args": ["-mcpu=esp32s3",
                "-Xclang", "-target-feature", "-Xclang", "-windowed",
                "-Xclang", "-target-feature", "-Xclang", "-fp",
                "-fintegrated-as", "-ffreestanding", "-fno-builtin",
                "-ffunction-sections", "-fdata-sections"],
    "hal_native": os.path.join("xtensa-esp32s3", "kernel"),
    "build_tag": "esp32s3",
    "tests_arch": "xtensa",
    "banner_arch": "xtensa",
    "hex_width": 8,
    # Where root's code starts: the instruction-window address of its flash
    # slot plus the image's header and segment table (`user/root.ld`). The
    # entry is somewhere in that code and moves with it, so no case on this
    # board asserts `{entry}`.
    "root_entry": 0x42100060,
    # The child's writable segment and stack: one region above root's, in
    # DRAM; `hal/xtensa-esp32s3/user/child.ld` links there. The region is the
    # size of root's, smaller than `CHILD_REGION_LEN` because this part has
    # 400 KiB of SRAM in all.
    "child_region_base": 0x3FCC8000,
    "child_region_len": 0x18000,
    # UART0's matrix source, which `hal.irq_raise_selftest_line()` raises.
    "selftest_line": 27,
    # Above the one child region, to `hal.RAM_TOP`; `tests/poolbase_xtensa.c`
    # says the same.
    "pool_base": 0x3FCE0000,
    "pool_len": 0x10000,
    # The protection grain, mirroring `hal.PROT_GRAIN`: the one granularity
    # a pool accepts, which `available` answers as its set.
    "prot_grain": 4,
    # UART0's page, mirroring `hal.DEVICE_GRANT_BASE` / `DEVICE_GRANT_LEN`.
    "device_base": 0x60000000,
    "device_len": 0x1000,
}

# How big a child's destination region is.
#
# The same size root gets, for the same reasons: its image plus the kernel's
# stack grant at the top (`hal.ROOT_STACK_LEN`, the one stack length). Keeping
# the two the same size lets one number here and one `ORIGIN` per child linker
# script express the whole layout. A board with less RAM states its own
# (`child_region_len` in its arch entry); `_child_region_len` reads it.
CHILD_REGION_LEN = 0x40000



def _child_region_len(arch):
    return arch.get("child_region_len", CHILD_REGION_LEN)

# The boot region table's wire format (version 4), frozen: an 8-byte header
# (magic, version u16, count u8, boot flags u8) then `count` rows of {base: u64,
# len: u64, kind: u8, reserved x 7}, all little-endian. The header's size puts
# the first row on an 8-byte boundary and the row's padding keeps the next one
# there, which lets the kernel overlay a struct on the section instead of
# assembling bytes. Kept in step with `kernel/core/process.saw`'s
# `RegionTableHeader` / `RegionRow` / `RegionKind` and the two `static_assert`s
# beside them.
#
# The kernel accepts exactly version 4, so this constant and that file change
# together. An image with no table at all is unaffected: the kernel's `len == 0`
# short-circuit runs ahead of the header read.
REGION_TABLE_MAGIC = 0x4E475253          # 'S','R','G','N' read little-endian
REGION_TABLE_VERSION = 4

# The header's boot flags byte, mirroring `kernel/core/process.saw`'s
# `BOOT_FLAG_NO_TICK`. The kernel arms the scheduler tick unless this bit is
# set, so every image asks for none by default and a case that wants the tick
# says `"tick": True`. An image with no table gets the tick, which is why the
# default image carries a table even when it has no rows.
REGION_FLAG_NO_TICK = 0x01


def boot_flags(case):
    """The boot flags byte this case's region table carries."""
    return 0 if case.get("tick") else REGION_FLAG_NO_TICK


def needs_region_table(case):
    """Does this case's image carry a region table?

    A case with rows does. A case with none still does unless it wants the
    tick, because a table is the only way an image tells the kernel not to arm
    it, and the flags it would carry are zero.
    """
    return bool(case.get("children") or case.get("child_asm")
                or case.get("pool") or case.get("device")
                or not case.get("tick"))


def _child_count(case):
    """How many child images the case's region table names: its Blade-built
    `children`, or its one hand-assembled `child_asm`, which defines
    `_sos_child0_start` and `_sos_child0_len` itself and is linked in place
    of an appended image. A case names one kind or the other."""
    if case.get("child_asm"):
        if case.get("children"):
            raise ToolError(f"case {case['name']!r}: `child_asm` is the "
                            "case's one child; it cannot have `children` too")
        return 1
    return len(case.get("children", ()))

# The kind column's values, mirroring `kernel/core/process.saw`'s `RegionKind`.
# A Ram row mints a Memory (an allocation), a Pool row a Pool, and a Device row
# an IoMemory: kind, not a flag, at the one place a window enters the system.
REGION_KIND_RAM = 0
REGION_KIND_DEVICE = 1
REGION_KIND_POOL = 2

# The top of the pool window an `off_grain_destinations` case gives to its two
# allocations off the grain, half each.
OFF_GRAIN_SPAN = 0x2_0000


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
    meet_at = arch["root_entry"] + 2 * arch.get("prot_grain", 0)
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
        # What a `map` over the child's own region answers: where a mapping's
        # address is its memory's own it is refused over an image row, and where
        # a mapping has an address of its own it is taken.
        "own_map": ("taken at an address of its own"
                    if arch.get("map_places") else "refused with NoResource"),
        # How the child ends after its own region was mapped into it
        # read-execute: where the map is refused, its data is still no entry;
        # where it is taken, every byte of the region is one the child may
        # execute, so none of it is a buffer and its first record ends it.
        "own_rx_map_end": ("the child's own memory is no buffer"
                           if arch.get("map_places")
                           else "an entry in the child's data is refused"),
        # How a call through an allocation mapped twice into a child ends
        # (`late_door_buffer_gone`): where a mapping has an address of its own
        # both views are rows and the read-write one is no buffer beside the
        # read-execute one; where a mapping takes its memory's own address the
        # second map is refused and the call is judged by the first row alone.
        "late_rw_rx": ("BadBuffer" if arch.get("map_places")
                       else "the read-execute map refused, taken"),
        "late_rx_rw": ("BadBuffer" if arch.get("map_places")
                       else "the read-write map refused, BadBuffer"),
        # The address a refused root image's second meeting segment is named
        # by: `payload_segments_meet.S` links it two grains above the entry.
        "segments_meet_at": f"0x{meet_at:0{width}x}",
        # The device window's base, which `payload_devices_meet.S` declares.
        "device_grant_base": f"0x{arch.get('device_base', 0):0{width}x}",
        "entry": f"0x{arch['root_entry']:0{width}x}",
        # A pool's numbers as `Pool.available` reports them, in decimal: the
        # granularity set (one bit, the grain) and a fresh pool's bytes.
        "grain": str(arch.get("prot_grain")),
        "pool_bytes": str(arch.get("pool_len")),
        # A pool row moved off the grain (`pool_skew`) loses the rest of its
        # first grain, and one whose top is off it too (`pool_short_top`)
        # loses the part of its last grain as well.
        "pool_less_grain": str(arch.get("pool_len", 0)
                               - arch.get("prot_grain", 0)),
        "pool_less_two_grains": str(arch.get("pool_len", 0)
                                    - 2 * arch.get("prot_grain", 0)),
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
        "eleven": f"0x{11:0{width}x}",
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
        # `child-event`'s owner exit code, asserted the same way in
        # `event_outlive`.
        "owner_mark": f"0x{0x2C:0{width}x}",
        # `child-thread`'s "both threads were taken" exit code, asserted the
        # same way in `thread_give`.
        "sent_mark": f"0x{0x2E:0{width}x}",
        # `child-thread`'s other roles' exit codes: the leaver's and the quota
        # role's in `thread_quota`, the hanger's in `thread_hang`.
        "left_mark": f"0x{0x2F:0{width}x}",
        "quota_mark": f"0x{0x30:0{width}x}",
        "hung_mark": f"0x{0x31:0{width}x}",
        # `child-teardown-mint`'s exit code, asserted in `teardown_mint`.
        "ended_mark": f"0x{0x32:0{width}x}",
        # `child-waitable`'s exit codes: the peer's in `waitable_give`, the
        # owners' in `timer_outlive`, `waiter_outlive`, `teardown_detach` and
        # `timer_disarm`, the parker's in `waiter_parked`, and the attacher's in
        # `teardown_timer_before_release`.
        "served_mark": f"0x{0x33:0{width}x}",
        "owned_mark": f"0x{0x34:0{width}x}",
        "parked_mark": f"0x{0x35:0{width}x}",
        "attached_mark": f"0x{0x37:0{width}x}",
        # `child-churn`'s exit code, from its last thread's `Exit`, asserted in
        # `thread_exit`.
        "churn_mark": f"0x{0x36:0{width}x}",
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
    "xtensa", "exccause", "excvaddr", "excsave", "epc1", "vecbase", "simcall",
    "intenable", "scompare1", "windowbase",
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
        # a wrong number. The ticks-taken count is printed and not asserted:
        # ticks accrue with host time, and a delayed look at the counter can
        # find more than one.
        #
        # `at {one}` is `PREEMPT_PC`, the sentinel a tick taken at a point
        # reports (the idle poll's is zero, and a real interrupted PC is even
        # on every profile). Asserting it says which of the three deliveries
        # ran, for the first tick, which the loop's exit condition requires.
        "name": "preempt_tick",
        "src": os.path.join(TESTS_DIR, "preempt_tick.saw"),
        "asm": "payload_spin.S",
        "expect_out": ["SOS M3: kernel section begin",
                       "SOS: timer tick {one} at {one}",
                       "SOS M3: kernel section end, ticks taken=",
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
    # --- a segment already where it is linked -------------------------------
    # Each image states its segment's load address and its entry as the
    # address of its own bytes, through a relocation, so it is in place
    # wherever the linker puts the payload (spec §5.6). The riscv32 boards use
    # such a segment in place; arm64 copies every segment. The entry moves
    # with the build, so `entry=` is asserted up to its value.
    {
        # Used in place and running: root's code row names the payload's own
        # bytes, and only that row lets the program print and shut down.
        "name": "root_image_in_place",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "asm": "payload_inplace.S",
        "arches": ["riscv32", "riscv32-flat"],
        "expect_out": ["{banner}",
                       "root image ok segments={one} entry=",
                       "SOS-I"],
        "expect_clean_exit": True,
    },
    {
        # The same image on the board that uses nothing in place, its
        # addresses the physical ones the kernel holds the payload's bytes at:
        # copied, and judged against root's region, which the kernel's own
        # memory is below. Used in place, it would be granted the payload's
        # own page and run, printing `SOS-I`.
        "name": "root_image_in_place_copied",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "asm": "payload_inplace.S",
        "arches": ["arm64"],
        "expect_out": "bad root image: segment loads below the root region",
        "expect_clean_exit": False,
    },
    {
        # Two segments used in place, code and read-only data, and a Timer's
        # arm record in the read-only one handed to the kernel where it sits:
        # the copy-in door takes the read-only segments used in place
        # (spec §2.2), and root goes on to print `SOS-T`.
        "name": "root_image_in_place_source",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "asm": "payload_inplace_rodata.S",
        "arches": ["riscv32", "riscv32-flat"],
        "expect_out": ["{banner}",
                       "root image ok segments={two} entry=",
                       "SOS-T"],
        "expect_clean_exit": True,
    },
    {
        # The entry in the read-only data segment: judged by the executable
        # segments alone, it is refused, though the image grants its bytes.
        "name": "root_image_entry_not_exec",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "asm": "payload_entry_not_exec.S",
        "arches": ["riscv32", "riscv32-flat"],
        "expect_out": "bad root image: entry is not inside an executable "
                      "segment",
        "expect_clean_exit": False,
    },
    {
        # In place and writable: the grant would let root write the image.
        "name": "root_image_in_place_writable",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "asm": "payload_inplace_writable.S",
        "arches": ["riscv32", "riscv32-flat"],
        "expect_out": "bad root image: segment used in place is writable",
        "expect_clean_exit": False,
    },
    {
        # In place with a zero-filled tail: nothing is placed, so the tail
        # would be the image's next bytes rather than zeros.
        "name": "root_image_in_place_tail",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "asm": "payload_inplace_tail.S",
        "arches": ["riscv32", "riscv32-flat"],
        "expect_out": "bad root image: segment used in place has a "
                      "zero-filled tail",
        "expect_clean_exit": False,
    },
    {
        # In place off the grant grain: the row would grant the bytes before
        # the segment too.
        "name": "root_image_in_place_grain",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "asm": "payload_inplace_grain.S",
        "arches": ["riscv32", "riscv32-flat"],
        "expect_out": "bad root image: segment is not aligned to the grant "
                      "granularity",
        "expect_clean_exit": False,
    },
    {
        # Two segments whose rows would meet: the second, zero-filled and
        # read-write, is linked two grains into the code segment. No two rows
        # of one image meet (spec §5.6), so the boot stops, naming the second
        # segment's address.
        "name": "root_image_segments_meet",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "asm": "payload_segments_meet.S",
        "expect_out": "bad root image: two segments of the image meet "
                      "({segments_meet_at})",
        "expect_clean_exit": False,
    },
    {
        # The board's one device window declared twice: two device segments,
        # whose rows would grant the same page. The boot stops, naming the
        # second's address.
        "name": "root_image_devices_meet",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "asm": "payload_devices_meet.S",
        "expect_out": "bad root image: two segments of the image meet "
                      "({device_grant_base})",
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
                       "root image ok segments={three}",
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
                       # Teardown handle counts. Eight: the three boot handles,
                       # the two Threads this process created, and three
                       # Process derivations (each of the three threads asks
                       # the System object which Process it is). The two
                       # workers also ask the Process which Thread they are,
                       # and each releases that handle by exiting through it.
                       # The two trailing counts say a process that made no
                       # Event and no Waiter reports none, the null row of the
                       # claim the Event cases below make with real objects.
                       "SOS: process teardown handles={eight} threads={three} "
                       "events={zero} waiters={zero}"],
        "expect_clean_exit": True,
    },
    {
        # User FP/SIMD state is per thread. Two workers of one process fill
        # V0-V31, FPSR and FPCR with their own patterns and yield to each other
        # three times each, counting the registers that stop matching; the
        # initial thread uses none of it. The kernel's `SOS: fp first use` line
        # is the observable for the lazy enable, and the expectations are
        # adjacent runs so an extra trap anywhere in them breaks the match:
        #
        #   initial begin / created   the initial thread takes no trap
        #   A begin, first use, filled   each worker traps once, at its fill
        #   B filled, A bad=0         nothing between: A's three checks ran
        #                             after B's switches and took no trap, and
        #                             the count is zero, so each switch saved
        #                             and restored the pattern
        #
        # With no save at a switch, B's fill overwrites A's registers and A
        # reports every one wrong; with EL0 access left on at boot, no first
        # use line appears at all.
        "name": "fp_threads",
        "arches": ["arm64"],
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": FP_THREADS_PKG,
        "expect_out": ["{banner}",
                       "SOS fp: initial begin\nSOS fp: initial created\n",
                       "SOS fp: A begin\nSOS: fp first use\n"
                       "SOS fp: A filled\n",
                       "SOS fp: B begin\nSOS: fp first use\n"
                       "SOS fp: B filled\nSOS fp: A bad=0\n",
                       "SOS fp: B bad=0\n",
                       "SOS fp: joined a=0 b=0\nSOS fp: initial done\n"],
        "expect_clean_exit": True,
    },
    {
        # A first FP/SIMD use never sees another process's state. Root fills
        # every FP/SIMD register and parks on its child; the child's first
        # instruction reads them all and exits with the number that were not
        # zero. The hardware still holds root's pattern when the child starts,
        # so a first-use enable that did not load the child's own zeroed state
        # would report every register and flag. `status=65536` is
        # `Exited`(1) << 16 | 0.
        "name": "fp_leak",
        "arches": ["arm64"],
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": FP_LEAK_PKG,
        "children": [CHILD_FP_LEAK_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS: fp first use\nSOS fpleak: filled\n",
                       "SOS fpleak: started\n",
                       "SOS fpleak child: begin\nSOS: fp first use\n"
                       "SOS fpleak child: nonzero=0\n",
                       "SOS fpleak: woke key=44 status=65536\n",
                       "SOS fpleak: done\n"],
        "expect_clean_exit": True,
    },
    {
        # Preemption. The same shape as thread_basics with the yields removed
        # and the timer armed: two workers that print a letter and then spin
        # until the other worker has printed its own, making no call that
        # could give the processor up. A worker's spin ends only when the
        # processor has gone to the other, so every round crosses between the
        # two threads, and that crossing is the scheduler taking the processor
        # away at a tick, which a cooperative test cannot show. The handoff is
        # forced rather than timed: no number of ticks, and no delay in
        # delivering them, changes which letters are printed, and a kernel that
        # never preempted would leave the case at the harness timeout.
        #
        # The root's own banner is deliberately not asserted: it is written a
        # byte per syscall while the kernel is still narrating its first
        # ticks, so the two interleave mid-word on the console. That is
        # harmless (a shared serial port with no locking) but not something a
        # substring can match.
        "name": "thread_preempt",
        "src": os.path.join(TESTS_DIR, "threads_timer.saw"),
        "root_pkg": THREAD_PREEMPT_PKG,
        #
        # The alternation is asserted as direction changes, not as a fixed
        # sequence: which worker runs first depends on where the first tick
        # lands relative to the two `start` calls, so a sequence starting `A`
        # would fail half the time on a claim it was never making. `AB` then
        # `BA` then `AB` says the processor crossed between the two threads at
        # least three times, whoever went first.
        #
        # The interrupt column is printed and not asserted: a tick lands where
        # the host puts it, so asserting the number would be asserting the
        # weather. The tick count is unasserted for the same reason.
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
        "strip_kernel_lines": True,
        "overlapping_matches": True,
        "expect_out": ["SOS M2: preemptive kernel up on",
                       "AB", "BA", "AB",
                       "SOS preempt: joined a=33 b=44"],
        "expect_clean_exit": True,
    },
    {
        # The bump arena's cursor under preemption. Two workers of one process
        # take thousands of blocks each from `sosrt`'s arena under the
        # short-tick preemption kernel (`tests/threads_fast_tick.saw`), and the
        # initial thread then checks that no two blocks share a granule and
        # that every block still holds its worker's marker. A cursor update
        # that a switch can overwrite hands the same block to both workers and
        # fails the first check with a line naming the offset.
        #
        # The kernel's own tick narration lands in the same stream, so the
        # match runs on the projection `thread_preempt` uses; neither expected
        # line begins `SOS: `.
        #
        # It runs on the isolated riscv32 profile only, because that is the
        # only profile where it can fail. Against a cursor updated by a plain
        # read and store, a boot there reports an overlap and a boot on arm64
        # or riscv32-flat does not: those two run the workload so fast that
        # too few ticks land in it for one to fall inside a window of a few
        # instructions, and a shorter period does not add enough. A case that
        # cannot fail there would pass whatever the allocator did, so it is
        # not run there.
        "name": "arena_race",
        "src": os.path.join(TESTS_DIR, "threads_fast_tick.saw"),
        "root_pkg": ARENA_RACE_PKG,
        "arches": ["riscv32"],
        "strip_kernel_lines": True,
        "expect_out": ["SOS M2: preemptive kernel up on",
                       "SOS arenarace: two allocators, no yields",
                       "SOS arenarace: blocks disjoint, markers intact"],
        "expect_clean_exit": True,
    },
    {
        # The shipping kernel preempts a process that never yields. Root
        # starts a child that loops forever with no syscall, arms a Timer and
        # parks. The Timer wakes root, but only the scheduler tick running out
        # the child's slice puts root back on the processor; with no tick the
        # child holds the machine and the case runs into the harness timeout.
        #
        # `"tick": True` is the case key that leaves the image's boot flags
        # clear, which is what an image gets by default from the kernel. The
        # cases that boot the shipping kernel set the "no tick" flag; the
        # harness-kernel cases boot their own.
        "name": "tick_spinner",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "tick": True,
        "root_pkg": TICK_SPINNER_PKG,
        "children": [CHILD_SPIN_PKG],
        "expect_out": ["{banner}",
                       "SOS tickspin: child started",
                       "SOS tickspin: timer fired while the child spun"],
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
        # Row 1: in no row of the process that permits write. umode.saw
        # programs the hardware to give this payload its own bytes
        # read+execute and records no grant row for it, so no address is a
        # destination, and zero says so plainly.
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
    # the driver (spec §2.5). So the transcript shows three segments (code,
    # read-only data and data), one boot region, and the driver saying it
    # obtained the window.
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
                       # Three segments: code, read-only data and data. The
                       # device window is a region row, not a segment.
                       "root image ok segments={three}",
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
                       "root image ok segments={three}",
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
                       "SOS: process teardown handles={three} threads={one} "
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
        #
        # The report names no process and the machine stops with its own exit
        # status: a deadlock ends nothing.
        "name": "wait_deadlock",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": WAIT_DEADLOCK_PKG,
        "expect_out": ["{banner}",
                       "SOS deadlock: waiting for a signal nobody sends",
                       "SOS: deadlock: every thread is blocked and nothing "
                       "can wake one"],
        "expect_clean_exit": False,
        "expect_status": EXIT_DEADLOCK,
    },
    {
        # `wait_deadlock` with the scheduler tick armed. The tick is not a wake
        # source, so the state is still a deadlock with the tick running: the
        # same report and the same exit status. A kernel that counted the tick
        # as a wake source would idle here until the harness timeout.
        "name": "wait_deadlock_tick",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "tick": True,
        "root_pkg": WAIT_DEADLOCK_PKG,
        "expect_out": ["{banner}",
                       "SOS deadlock: waiting for a signal nobody sends",
                       "SOS: deadlock: every thread is blocked and nothing "
                       "can wake one"],
        "expect_clean_exit": False,
        "expect_status": EXIT_DEADLOCK,
    },
    {
        # A deadlock found after a process's death. Root attaches an Event
        # nobody signals to a Waiter, starts `child-bye` and waits on the
        # Waiter, not on the child, with no Timer armed and no line bound. The
        # child runs and exits, its process is torn down, and with no thread
        # current and none runnable the kernel stops at the deadlock from the
        # path that resumes after a death. Root's one line precedes the start,
        # so the order below is fixed.
        "name": "deadlock_after_death",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": DEADLOCK_AFTER_DEATH_PKG,
        "children": [CHILD_BYE_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS deadafter: waiting, nothing signals",
                       "SOS bye: exiting",
                       "SOS: process exit: code={five} process={one}",
                       "SOS: process teardown handles={two} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS: deadlock: every thread is blocked and nothing "
                       "can wake one"],
        "expect_clean_exit": False,
        "expect_status": EXIT_DEADLOCK,
    },
    {
        # `deadlock_after_death` with the tick armed. Root can now be preempted
        # between starting the child and waiting, so the child may exit while
        # root is still runnable, and the deadlock is then found when root
        # blocks rather than after the death. Either path may find the deadlock
        # in principle and nothing here asserts which one did; the transcript
        # is the same either way. `wait_deadlock_tick` is the case that
        # reaches `pick_next`'s arm under the tick.
        "name": "deadlock_after_death_tick",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "tick": True,
        "root_pkg": DEADLOCK_AFTER_DEATH_PKG,
        "children": [CHILD_BYE_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS deadafter: waiting, nothing signals",
                       "SOS bye: exiting",
                       "SOS: process exit: code={five} process={one}",
                       "SOS: process teardown handles={two} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS: deadlock: every thread is blocked and nothing "
                       "can wake one"],
        "expect_clean_exit": False,
        "expect_status": EXIT_DEADLOCK,
    },
    {
        # A deadlock that arises while the kernel idles. Root waits on a Waiter
        # holding an Event nobody signals, with a one-shot Timer armed that no
        # Waiter holds. The armed Timer is a wake source, so the kernel idles;
        # the Timer fires, disarms itself and wakes nobody, and nothing can wake
        # a thread any more. The idle asks again after the delivery and stops
        # the machine. A kernel that decided once, before idling, would sleep
        # until the harness timeout. The delay is short and only the arrival of
        # the report is asserted, never how long it took.
        "name": "deadlock_from_idle",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": DEADLOCK_FROM_IDLE_PKG,
        "expect_out": ["{banner}",
                       "SOS idledead: waiting while a timer nobody holds runs "
                       "down",
                       "SOS: deadlock: every thread is blocked and nothing "
                       "can wake one"],
        "expect_clean_exit": False,
        "expect_status": EXIT_DEADLOCK,
    },
    {
        # `deadlock_from_idle` with the tick armed: the tick is not a wake
        # source, so the ticks taken while idling change nothing and the same
        # report and status arrive.
        "name": "deadlock_from_idle_tick",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "tick": True,
        "root_pkg": DEADLOCK_FROM_IDLE_PKG,
        "expect_out": ["{banner}",
                       "SOS idledead: waiting while a timer nobody holds runs "
                       "down",
                       "SOS: deadlock: every thread is blocked and nothing "
                       "can wake one"],
        "expect_clean_exit": False,
        "expect_status": EXIT_DEADLOCK,
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
                       # Neither periodic count is asserted: a fire delivered
                       # late coalesces, so any count of one or more is
                       # correct, and no assertion here reads a duration. What
                       # is asserted is that each wake happened, with no ack
                       # and no re-arm between them (`ackfree`).
                       "SOS interval: tick one fires=",
                       "SOS interval: tick two fires=",
                       "ackfree=1",
                       # Fires that landed while nobody was waiting did not
                       # queue as separate wakes and were not dropped: they
                       # accumulated into one count above one. The program
                       # waits for two interrupts to be taken before it reads
                       # the count, so this holds however late the host
                       # delivers them.
                       "SOS interval: coalesced=1",
                       # One fire late by more than a one-nanosecond interval
                       # counts the periods it missed.
                       "SOS interval: late=1",
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
                       "SOS: deadlock: every thread is blocked and nothing "
                       "can wake one"],
        "expect_clean_exit": False,
        "expect_status": EXIT_DEADLOCK,
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
        # A process that is created and never started is torn down when its
        # last handle is released. Root gives such a child a pipe end, a region
        # and a bound Interrupt, makes a thread in it, and releases it; every
        # leg then reads one thing the teardown freed.
        #
        # The child's teardown line is the first `process={one}` line after the
        # release: `handles={three}` is the pipe end, the region and the
        # Interrupt, `interrupts={one}` the line it destroyed, and
        # `threads={zero}` because the thread made in it is still named by
        # root's handle, so it is invalidated and not freed (`join says` the
        # call is gone, `Revoked`). `bodies gained=1` is the body table: root
        # can create one more process than while the child was held. The
        # `handles={zero}` lines around it are the processes those counts made
        # and dropped, themselves never started.
        #
        # The last two legs are faults on a kernel that does not tear the child
        # down: the line is still bound, and a cut of the whole pool is
        # larger than the pool holds.
        "name": "unstarted_released",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": UNSTARTED_RELEASED_PKG,
        "children": [CHILD_XHOST_PKG],
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={three}",
                       "SOS unstarted: releasing the unstarted child",
                       "SOS: process teardown handles={three} threads={zero} "
                       "events={zero} waiters={zero} interrupts={one} "
                       "timers={zero} process={one}",
                       "SOS unstarted: bodies gained=1",
                       "SOS unstarted: post says the other end of this "
                       "connection is gone",
                       "SOS unstarted: thread join says what this call was "
                       "waiting for is gone",
                       "SOS unstarted: line bound again",
                       "SOS unstarted: region came back",
                       "SOS unstarted: done"],
        "expect_clean_exit": True,
    },
    {
        # A launcher that dies holding the only handle to a process it never
        # started: the teardown of the never-started process runs after the
        # launcher's own, and before root runs again. The launcher
        # (`child-launcher`) gives that process an Interrupt and exits; its
        # teardown line (`process={one}`) is followed by the grandchild's
        # (`process={two}`, `interrupts={one}`), and only then does root run:
        # the first thing it does after the launcher's death is bind the line
        # again, then it reads the launcher's status word (`Exited` (1) << 16 |
        # 70). `spare=2` is the body table less root's: the grandchild's body
        # is back.
        "name": "unstarted_launcher",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": UNSTARTED_LAUNCHER_PKG,
        "children": [CHILD_FAULT_PKG, CHILD_LAUNCHER_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={four}",
                       "SOS launcher: leaving the child unstarted",
                       "SOS: process teardown handles={five} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS: process teardown handles={one} threads={zero} "
                       "events={zero} waiters={zero} interrupts={one} "
                       "timers={zero} process={two}",
                       "SOS unstartedl: line bound again",
                       "SOS unstartedl: launcher ended status=65606",
                       "SOS unstartedl: spare=2",
                       "SOS unstartedl: done"],
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
                       "SOS records: pass 1 held=11 room=1, then "
                       "no room for what was asked",
                       "SOS records: every held child answered status=131073 "
                       "syscalls=1 faults=0",
                       "SOS records: pass 2 held=11 room=1, then "
                       "no room for what was asked",
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
                       "SOS recdonate: held=11, then no room for "
                       "what was asked",
                       "SOS recdonate: after a body donation, no room "
                       "for what was asked",
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
        #   the Revoked answers   `Base` on the husk, before any address, and
        #                         a `map` naming the dead third child
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
                       "SOS huskbase: the husk's base answered: what this call "
                       "was waiting for is gone",
                       "SOS huskbase: map into the gone child answered: what "
                       "this call was waiting for is gone"],
        "expect_clean_exit": True,
    },
    {
        # The same husk, asked to `Unmap`: `Revoked`, and the second child's
        # row stays where it is, so its holder's own unmap succeeds. A second
        # unmap of that Mapping is the `BadState` fault: unmapped by its
        # holder is a different state from target died. The counters and the
        # reused record are pinned as in `mapping_husk_base`.
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
                       "SOS huskunmap: the husk's unmap answered: what this "
                       "call was waiting for is gone",
                       "SOS huskunmap: the second child's mapping unmapped",
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
        # Creation ops act on behalf of the process the handle names, and the
        # new handle is a word in the caller's table. Root makes a Thread, an
        # Event, a Waiter and a pipe through a host child's Process handle and
        # uses each directly. The host's Thread quota is what refuses the
        # fifth thread, and each object goes on to a child that was never
        # started. Once the host has ended, the same four ops answer `Revoked`
        # (the thread op with an entry and stack in no region, so `Revoked`
        # comes before the address-space check).
        "name": "create_cross",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": CREATE_CROSS_PKG,
        "children": [CHILD_XHOST_PKG, CHILD_HOLD2_PKG],
        "expect_out": ["{banner}",
                       "SOS xcreate: thread created",
                       "SOS xcreate: thread joined=77",
                       "SOS xcreate: event received=5",
                       "SOS xcreate: waiter woke=91",
                       "SOS xcreate: pipe posted=3 first=65",
                       "SOS xcreate: thread quota refused after=2: "
                       "this process is at its quota",
                       "SOS xcreate: gave=5 to the sink",
                       "SOS xcreate: host ended=65576",
                       "SOS xcreate: thread: what this call was waiting "
                       "for is gone",
                       "SOS xcreate: event: what this call was waiting "
                       "for is gone",
                       "SOS xcreate: waiter: what this call was waiting "
                       "for is gone",
                       "SOS xcreate: pipe: what this call was waiting "
                       "for is gone",
                       "SOS xcreate: done"],
        "expect_clean_exit": True,
    },
    {
        # A caller error in an op through another process's handle faults the
        # caller and leaves the named process alone. Four helpers in turn are
        # handed a Process handle onto a live host: a bad entry (`BadEntry`), a
        # bad event mode (`BadArg`), and the two self-only ops `ThreadSelf` and
        # `BootHandleNext` (`NotSelf`). Each helper ends with the kernel's
        # fault line and its teardown line, root reads the helper's status word
        # (Faulted, 2 << 16, plus the reason) and the host's (Running, zero),
        # and root's later lines show nothing was left unscheduled.
        "name": "create_cross_fault",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": CREATE_CROSS_FAULT_PKG,
        "children": [CHILD_XHOST_PKG, CHILD_XCALLER_PKG],
        "expect_out": ["{banner}",
                       "SOS xfault: started",
                       "SOS: process fault: thread entry outside the process "
                       "process={two}",
                       "SOS: process teardown",
                       "SOS xfault: role 1 caller=131078 victim=0",
                       "SOS: process fault: argument outside its domain "
                       "process={two}",
                       "SOS: process teardown",
                       "SOS xfault: role 2 caller=131081 victim=0",
                       "SOS: process fault: not the caller process={two}",
                       "SOS: process teardown",
                       "SOS xfault: role 3 caller=131076 victim=0",
                       "SOS: process fault: not the caller process={two}",
                       "SOS: process teardown",
                       "SOS xfault: role 4 caller=131076 victim=0",
                       "SOS: process fault: thread entry outside the process "
                       "process={two}",
                       "SOS: process teardown",
                       "SOS xfault: role 5 caller=131078 victim=0",
                       "SOS xfault: host ended=40",
                       "SOS: process fault: argument outside its domain "
                       "process={two}",
                       "SOS: process teardown",
                       "SOS xfault: role 6 caller=131081",
                       "SOS: process fault: argument outside its domain "
                       "process={two}",
                       "SOS: process teardown",
                       "SOS xfault: role 7 caller=131081",
                       "SOS: process fault: buffer is not word-aligned "
                       "process={two}",
                       "SOS: process teardown",
                       "SOS xfault: role 8 caller=131082",
                       "SOS xfault: done"],
        "expect_clean_exit": True,
    },
    {
        # Creation ops through another process's handle charge that process's
        # quota rows: root, whose rows are unlimited, makes Events, Waiters
        # and pipes through the handle of a child that was never started, and
        # each kind stops at the child's own quota. Releasing the objects
        # credits the child again (the object's owner is the named process),
        # so the same number fits a second time.
        "name": "create_cross_quota",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": CREATE_CROSS_QUOTA_PKG,
        "children": [CHILD_FAULT_PKG],
        "expect_out": ["{banner}",
                       "SOS xquota: events made=4 then refused, made=4 again",
                       "SOS xquota: waiters made=2 then refused, made=2 again",
                       "SOS xquota: pipes made=2 then refused, made=2 again",
                       "SOS xquota: done"],
        "expect_clean_exit": True,
    },
    {
        # No thread of a process runs before its `ProcessOp.Start`. A thread
        # made through a `Created` child's handle is accepted, and starting it
        # after the child is started runs it in the child; starting a thread
        # of a child that was never started is a `BadState` fault on the
        # caller.
        "name": "create_prestart",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": CREATE_PRESTART_PKG,
        "children": [CHILD_XHOST_PKG],
        "expect_out": ["{banner}",
                       "SOS xprestart: created in the unstarted child",
                       "SOS xprestart: joined=77 after the child started",
                       "SOS xprestart: starting a thread of an unstarted "
                       "child",
                       "SOS: process fault: object in the wrong state "
                       "process={zero}",
                       "SOS: process teardown"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # The caller's Handle row governs a creation op. A child at one
        # handle short of its budget makes objects through another process's
        # handle: a pipe (two handles) is QuotaExceeded with nothing made or
        # leaked, so an Event then fits and a second does not. With its row
        # full and the Thread slab full, a thread create is QuotaExceeded (the
        # caller's row is policy, asked before the slab), and NoResource once
        # a handle is let go.
        "name": "create_budget",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": CREATE_BUDGET_PKG,
        "children": [CHILD_XBUDGET_PKG, CHILD_HOLD2_PKG],
        "expect_out": ["{banner}",
                       "SOS budget: thread slab filled",
                       "SOS xbudget: pipe with one handle free: this process "
                       "is at its quota",
                       "SOS xbudget: event made",
                       "SOS xbudget: second event: this process is at its "
                       "quota",
                       "SOS xbudget: thread with the row full: this process "
                       "is at its quota",
                       "SOS xbudget: thread with room: no room for "
                       "what was asked",
                       "SOS budget: child ended=40",
                       "SOS budget: done"],
        "expect_clean_exit": True,
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
        # `INFO_OP` reads a handle's rights and kind. The default sets of an
        # Event, a boot Memory and the Process handle are the bits their kinds
        # mint (1795 is Transfer, Mint and the three Event bits, 1539
        # Transfer, Mint and the two Memory bits, 4194051 the Process set); a
        # sibling minted through a keep mask
        # reports exactly the narrowed set and not the keep bit the source
        # never held; the floor's kind selector answers the published kind
        # numbers (System 0, Process 1) and its rights word equals the typed
        # one. The case ends on its own negative arm: a released word is the
        # ordinary `BadHandle` fault.
        "name": "handle_info",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": HANDLE_INFO_PKG,
        # The child supplies the boot regions the Memory is read from; it is
        # never created.
        "children": [CHILD_BYE_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS handleinfo: event rights=1795",
                       "SOS handleinfo: memory rights=1539",
                       "SOS handleinfo: process rights=4194051",
                       "SOS handleinfo: a narrowed sibling reports exactly "
                       "the narrowed set",
                       "SOS handleinfo: a keep bit the source lacks is not "
                       "reported",
                       "SOS handleinfo: kinds system=0 process=1",
                       "SOS handleinfo: the floor and the typed tier read "
                       "one rights word",
                       "SOS handleinfo: asking about a released word",
                       "SOS: process fault: bad handle process={zero}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # A selector `INFO_OP` does not know is a `BadArg` fault, on a handle
        # that is otherwise good.
        "name": "handle_info_selector",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": HANDLE_INFO_SELECTOR_PKG,
        "expect_out": ["{banner}",
                       "SOS handleinfo: asking with a selector that is not "
                       "one",
                       "SOS: process fault: argument outside its domain "
                       "process={zero}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # Rights ceilings (spec §2.7), end to end. The launcher narrows a
        # child's Event ceiling (Signal dropped, 1539 against the default
        # 1795) before it starts; the child's own Event carries 1539 while an
        # Event it was given and a sibling of that keep 1795, because a
        # ceiling caps what a process mints and not what it receives. The child
        # narrows its own Thread ceiling (513) and empties its Timer ceiling
        # (a Timer with no rights is still made), then signals its capped Event
        # and is ended with `AccessDenied`; the launcher reads the status word
        # (`Faulted` << 16 | `AccessDenied`'s number, 131075) and then asks
        # through the dead child's handle: a narrowing, a ceiling read and a
        # ceiling source each answer `Revoked`. Root then drops `Arm` from its
        # own Timer ceiling (515 against 771), and two children created
        # afterwards inherit it, the first also taking a never-started
        # process's Event ceiling as its ceiling source.
        "name": "rights_ceiling",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": RIGHTS_CEILING_PKG,
        "children": [CHILD_CEILING_PKG, CHILD_HOLD2_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={four}",
                       "SOS ceiling: root event ceiling=4294967295",
                       "SOS ceiling: child ceilings event=1539 "
                       "timer=4294967295",
                       "SOS ceilchild: event rights=1539",
                       "SOS ceilchild: timer rights=771",
                       "SOS ceilchild: given event rights=1795",
                       "SOS ceilchild: sibling of the given event rights=1795",
                       "SOS ceilchild: ceilings event=1539 timer=4294967295",
                       "SOS ceilchild: thread rights=513",
                       "SOS ceilchild: emptied timer ceiling, timer rights=0",
                       "SOS ceilchild: signalling the capped event",
                       "SOS: process fault: access denied process={one}",
                       "SOS: process teardown",
                       "SOS ceiling: child ended=131075",
                       "SOS ceiling: restrict through a gone handle: revoked",
                       "SOS ceiling: ceiling read through a gone handle: "
                       "revoked",
                       "SOS ceiling: a gone ceiling source: revoked",
                       "SOS ceiling: root timer ceiling=515",
                       "SOS ceilchild: event rights=1539",
                       "SOS ceilchild: timer rights=515",
                       "SOS ceiling: sourced child ended",
                       "SOS ceilchild: event rights=1795",
                       "SOS ceilchild: timer rights=515",
                       "SOS ceiling: plain child ended",
                       "SOS ceiling: done"],
        "expect_clean_exit": True,
    },
    {
        # Ceilings cap the handles minted from a held object. Root narrows its
        # Memory (no `MapExecute`), Mapping (no `Base`) and Process (no `Start`)
        # ceilings; the pool it already held reads 67075 (`pool_rights()`,
        # the Memory default set 1539 and `PoolRight.Allocate`), what it
        # allocates reads 515, the Mapping that installs that 258 (the
        # Mapping default set is 770), and the process it creates 4177667,
        # the Process default set without `Start`.
        "name": "rights_ceiling_derived",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": CEILING_DERIVED_PKG,
        "children": [CHILD_BYE_PKG],
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={three}",
                       "SOS ceilderived: pool rights=67075",
                       "SOS ceilderived: allocation rights=515",
                       "SOS ceilderived: mapping rights=258",
                       "SOS ceilderived: created process rights=4177667",
                       "SOS ceilderived: done"],
        "expect_clean_exit": True,
    },
    {
        # A kind past the last `HandleKind` is a `BadArg` fault; the last kind
        # itself narrows.
        "name": "rights_ceiling_badkind",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": CEILING_BADKIND_PKG,
        "expect_out": ["{banner}",
                       "SOS ceilbadkind: the last kind narrows",
                       "SOS ceilbadkind: restricting a kind past the last",
                       "SOS: process fault: argument outside its domain "
                       "process={zero}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # Narrowing a ceiling needs `ProcessRight.RestrictRights` on the handle
        # used: the full handle narrows, a sibling without the bit is refused.
        "name": "rights_ceiling_gate_restrict",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": CEILING_GATE_RESTRICT_PKG,
        "expect_out": ["{banner}",
                       "SOS ceilgate: narrowing through the full handle",
                       "SOS ceilgate: narrowing through a sibling without "
                       "RestrictRights",
                       "SOS: process fault: access denied process={zero}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # Reading a ceiling needs `ProcessRight.Stats` on the handle used.
        "name": "rights_ceiling_gate_read",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": CEILING_GATE_READ_PKG,
        "expect_out": ["{banner}",
                       "SOS ceilread: ceiling through the full handle="
                       "4294967295",
                       "SOS ceilread: reading through a sibling without Stats",
                       "SOS: process fault: access denied process={zero}"],
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
        # Allocation, and the arithmetic made observable. An allocation has
        # no bounds reader, so "the pool advanced" is proven by using two
        # allocations: 0xaa into the first, 0xbb into the second, and the
        # first still 0xaa.
        "name": "pool_allocate",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": POOL_ALLOCATE_PKG,
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={one}",
                       "SOS poolalloc: a=170 b=187 a-again=170",
                       # Twenty allocate-and-drop rounds, past `MAX_MEMORIES`,
                       # complete with nothing refused: free-on-last-reference
                       # returns a dropped allocation's slot, so the Memory slab
                       # never runs out here.
                       "SOS poolalloc: rounds=20 refused=0",
                       "SOS poolalloc: asking for more than is left",
                       # A length no free range of the pool can serve is a
                       # status, `NoMemory`, and the program carries on.
                       "SOS poolalloc: refused: this pool has no free range "
                       "that long"],
        "expect_clean_exit": True,
    },
    {
        # A pool's two ops read against each other (spec §2.5): what
        # `available` reports is what `allocate` serves. A fresh pool reports
        # its whole length twice and the grain's one bit as its set; the set's
        # lowest bit as the granularity answers what zero does. Four pages
        # allocated take four pages off both numbers, and an allocation's
        # rights are the pool's word (67075) less `Allocate` (1539) under
        # root's unrestricted ceiling. More than the pool holds
        # is `NoMemory` and changes nothing the next report sees. With the
        # frontier spent and the first and third page home, two pages are free
        # in two ranges: one unit past `largest` and the two pages in one piece
        # are `NoMemory`, exactly `largest` is served. Releasing the page
        # between them coalesces the three, releasing the rest folds the pool
        # whole with the same set, and a donated page stays out for good.
        "name": "pool_ops",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": POOL_OPS_PKG,
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={one}",
                       "SOS poolops: fresh total={pool_bytes} "
                       "largest={pool_bytes} set={grain}",
                       "SOS poolops: the lowest set bit as the granularity: "
                       "same=1",
                       "SOS poolops: four allocations: total fell by 16384 "
                       "largest fell by 16384",
                       "SOS poolops: rights pool=67075 allocation=1539",
                       "SOS poolops: more than the pool holds: this pool has "
                       "no free range that long unchanged=1",
                       "SOS poolops: fragmented total=8192 largest=4096",
                       "SOS poolops: one unit past largest: this pool has no "
                       "free range that long",
                       "SOS poolops: the total in one piece: this pool has no "
                       "free range that long unchanged=1",
                       "SOS poolops: exactly largest: ok",
                       "SOS poolops: coalesced total=12288 largest=12288",
                       "SOS poolops: all home: total={pool_bytes} set={grain}",
                       "SOS poolops: a donated allocation stays out: 4096",
                       "SOS poolops: done"],
        "expect_clean_exit": True,
    },
    {
        # The free list against a model of first fit (spec §2.5, §5.9), in
        # pages counted from the pool's base. Root labels every page with its
        # own number, lays out holes of one, two, three and four pages with a
        # page between each two and a cap spending the rest, and releases the
        # holes. Each allocation then lands where the model says (`at`, read
        # from the label its mapping sees), `available` agrees after each
        # allocation and release, and `intact=1` says every live allocation
        # still holds its own marks, so none shares a byte with another. The
        # remainder of a range longer than the ask is served next at the page
        # after the allocation, and a release beside it joins it again. The
        # releases reach a new node at the head and between two nodes, a range
        # joining the one above, the one below and both, and the fold into
        # the frontier with a node before it and with none. Then one page
        # between two live ones is released and allocated again forty times,
        # past `MAX_FREE_RANGES`, served every time. Then forty rounds of a
        # page released between two free pages, joining them with a third
        # range after them on the list, and the joined range cut again: the
        # third range stays listed and no node is lost. A page and a grain is
        # `NoMemory` while every free range is a page and the frontier is
        # spent, and comes off the frontier once it is back. Last, the whole
        # pool, its ledger back to nothing, is accepted as a donation.
        "name": "pool_first_fit",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": POOL_FIRST_FIT_PKG,
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={one}",
                       "SOS firstfit: labelled, then home: whole=1",
                       "SOS firstfit: four holes: free=10 largest=4 pages",
                       "SOS firstfit: five pages: this pool has no free range "
                       "that long",
                       "SOS firstfit: two pages, an exact fit between two "
                       "ranges: at 2 free=8 largest=4 intact=1",
                       "SOS firstfit: two pages from a range of three: at 5 "
                       "free=6 largest=4 intact=1",
                       "SOS firstfit: one page, an exact fit at the head: at 0 "
                       "free=5 largest=4 intact=1",
                       "SOS firstfit: one page, what the range of three kept: "
                       "at 7 free=4 largest=4 intact=1",
                       "SOS firstfit: one page from a range of four: at 9 "
                       "free=3 largest=3 intact=1",
                       "SOS firstfit: released, it joins what its range kept: "
                       "free=4 largest=4 pages",
                       "SOS firstfit: a page released at the head: free=5 "
                       "largest=4 pages",
                       "SOS firstfit: a page released between two ranges: "
                       "free=6 largest=4 pages",
                       "SOS firstfit: two pages released below a range they "
                       "touch: free=8 largest=4 pages",
                       "SOS firstfit: a page released between two ranges it "
                       "touches: free=9 largest=8 pages",
                       "SOS firstfit: two pages released between two ranges: "
                       "free=11 largest=8 pages",
                       "SOS firstfit: a page released between two ranges, "
                       "three into one: free=12 largest=11 pages",
                       "SOS firstfit: cap released, the range folds: out=1 "
                       "short=2 pages",
                       "SOS firstfit: the last page released: whole=1",
                       "SOS firstfit: forty exact fits of one page: served=40 "
                       "last at 1.",
                       "SOS firstfit: home again: whole=1",
                       "SOS firstfit: three into one: rounds=40 free=3 "
                       "largest=1 pages",
                       "SOS firstfit: a page and a grain, nothing that long: "
                       "this pool has no free range that long",
                       "SOS firstfit: a page and a grain, the frontier back: "
                       "at 7.",
                       "SOS firstfit: merged and home: whole=1",
                       "SOS firstfit: the whole pool donated: accepted",
                       "SOS firstfit: done"],
        "expect_clean_exit": True,
    },
    {
        # What a pool refuses, and how (spec §2.5, §2.7, §3). Root launches
        # `child-pool-leg` once per role with a pool sibling, narrowed by the
        # give's keep mask where the role needs it, and one allocation, and
        # prints the §8 word the child ends with: `Faulted` (2) in the high
        # half and the reason in the low, or `Exited` with 7 for a role that
        # ran to its end. No `Allocate` right refuses both ops
        # (`AccessDenied`); zero bytes, a length off the grain, a
        # granularity that is a bit the set lacks, and a record short of the
        # published size are `BadArg`; a record in read-only data is
        # `BadBuffer`; a pool where `process_create` wants
        # an allocation is `BadHandle`, and attached to a Waiter `NotWaitable`.
        # An allocation through a pool without `MapExecute` cannot be mapped
        # executable, and one through a pool without `Transfer` cannot be
        # given; the same give through a whole pool passes that test and is
        # refused for a different reason (`BadState`: a running process takes
        # no give). Then the roles that run to their end: `INFO_OP` names a
        # pool 15 and an allocation 8; a child's narrowed `Memory` ceiling caps
        # what it allocates (515) under a pool word of 67075; a child takes
        # all the pool holds and is refused one unit more; a child at its
        # `Memory` quota is answered `QuotaExceeded` before the pool is asked;
        # and a child at its handle budget with the Memory slab full (root
        # fills it with staged pages) is answered `NoResource`, the slab asked
        # before the mint. A granularity of the grain with bit 0 set is
        # `BadArg` at both ops: one of its bits is in the set, and it is not
        # one bit. Last, two donations a child is given the only handle for:
        # root's pool with a page of it out here, and the allocation the
        # child runs in, which its own rows grant. Both are `BadState`, and
        # root's page then goes home to the pool the donation did not take.
        # Each child's destination is allocated from a pool that is the
        # child's destination row.
        "name": "pool_faults",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": POOL_FAULTS_PKG,
        "children": [CHILD_POOL_LEG_PKG],
        "pool_destinations": [0],
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={three}",
                       "SOS poolfaults: allocate without Allocate: "
                       "status=131075",
                       "SOS poolfaults: available without Allocate: "
                       "status=131075",
                       "SOS poolfaults: allocate of zero bytes: status=131081",
                       "SOS poolfaults: allocate off the grain: status=131081",
                       "SOS poolfaults: allocate at another granularity: "
                       "status=131081",
                       "SOS poolfaults: available at another granularity: "
                       "status=131081",
                       "SOS poolfaults: allocate at the grain with bit 0 set: "
                       "status=131081",
                       "SOS poolfaults: available at the grain with bit 0 "
                       "set: status=131081",
                       "SOS poolfaults: available with a short record: "
                       "status=131081",
                       "SOS poolfaults: available into read-only data: "
                       "status=131083",
                       "SOS poolfaults: a pool as an image: status=131073",
                       "SOS poolfaults: a pool as a destination: "
                       "status=131073",
                       "SOS poolfaults: a pool attached to a Waiter: "
                       "status=131080",
                       "SOS poolfaults: execute, through a pool without "
                       "MapExecute: status=131075",
                       "SOS poolfaults: give, through a pool without "
                       "Transfer: status=131075",
                       "SOS poolfaults: give, through a whole pool: "
                       "status=131077",
                       "SOS poolleg: kinds pool=15 given=8 own=8",
                       "SOS poolfaults: kinds: status=65543",
                       "SOS poolleg: capped rights pool=67075 allocation=515",
                       "SOS poolfaults: a capped allocation: status=65543",
                       "SOS poolleg: all it holds: ok; one unit more: this "
                       "pool has no free range that long",
                       "SOS poolfaults: a budget: status=65543",
                       "SOS poolleg: at its quota, more than the pool holds: "
                       "this process is at its quota",
                       "SOS poolfaults: a quota: status=65543",
                       "SOS poolleg: at its handle budget (this process is "
                       "at its quota), the slab full: no room for "
                       "what was asked",
                       "SOS poolfaults: the slab full: status=65543",
                       "SOS poolfaults: donate a pool with an allocation out: "
                       "status=131077",
                       "SOS poolfaults: the allocation went home to the pool",
                       "SOS poolfaults: donate the allocation it runs in: "
                       "status=131077",
                       "SOS poolfaults: done"],
        "expect_clean_exit": True,
    },
    {
        # What holds a pool (spec §2.5, §2.10), read as room in the Memory
        # slab. The child's image blob is typed as a second pool and the child
        # never runs. Root keeps a page allocated out of the blob pool and
        # fills the slab with pages out of the RAM pool, staged in messages on
        # a connection nobody takes from, until an allocation is
        # `NoResource`; while the slab is full more than the pool holds is
        # still `NoMemory`, because the pool is asked first. Releasing the
        # blob pool's only handle frees nothing while its page is out (room
        # grew by 0); releasing the page frees the page's slot and the pool's
        # (room grew by 2).
        "name": "pool_lifetime",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": POOL_LIFETIME_PKG,
        "children": [CHILD_BYE_PKG],
        "pool_blobs": [0],
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={three}",
                       "SOS poollife: the slab filled: no room for "
                       "what was asked",
                       "SOS poollife: full, and too much: this pool has no "
                       "free range that long",
                       "SOS poollife: no handle, a page out: room grew by 0",
                       "SOS poollife: the page home: room grew by 2",
                       "SOS poollife: done"],
        "expect_clean_exit": True,
    },
    {
        # A pool with nothing out is held by what names it (spec §2.5,
        # §2.10), read as room in the Memory slab as `pool_lifetime` reads it.
        # The child's image blob is typed as a second pool and never
        # allocated from. Its only handle moved into a message nobody takes
        # holds its slot (room grew by 0); closing that connection releases
        # the message's handle, the last name, and the pool frees (room grew
        # by 1).
        "name": "pool_freed",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": POOL_FREED_PKG,
        "children": [CHILD_BYE_PKG],
        "pool_blobs": [0],
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={three}",
                       "SOS poolfreed: held only by a staged message: room "
                       "grew by 0",
                       "SOS poolfreed: nothing names it, nothing out: room "
                       "grew by 1",
                       "SOS poolfreed: done"],
        "expect_clean_exit": True,
    },
    {
        # A pool's free-list nodes come back to their slab when the pool goes
        # (spec §2.8, §5.9), read as the node slab's room: how many holes the
        # RAM pool's free list takes before a release is dropped for want of a
        # node. The child's image blob and its destination are typed as pools
        # and the child never runs. Each of the two is left with nothing out
        # and two ranges on its list, the slab one node short of full
        # (`nodes free=1`); the blob pool's last handle is then released and
        # the destination pool donated, and each time the slab has two nodes
        # free. The room for the two is made by exact fits off the RAM pool's
        # list, so the case holds those too: an exact fit gives its node
        # back.
        #
        # riscv32 only, for `slab_donate_free_nodes`' reason: filling the node
        # slab takes as many holes as it has nodes, each cut longer than the
        # last so first fit cannot serve it from a hole already made, and at
        # the other profile's grain, a page, they would not fit in the pool.
        "name": "pool_nodes_home",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": POOL_NODES_PKG,
        "children": [CHILD_BYE_PKG],
        "pool_blobs": [0],
        "pool_destinations": [0],
        "pool": True,
        "arches": ["riscv32", "riscv32-flat"],
        "expect_out": ["{banner}",
                       "SOS: boot regions={three}",
                       "SOS poolnodes: the node slab filled",
                       "SOS poolnodes: the blob pool, nothing out, two ranges "
                       "listed: lost=2 units",
                       "SOS poolnodes: while the blob pool has them: nodes "
                       "free=1.",
                       "SOS poolnodes: the blob pool released: nodes free=2.",
                       "SOS poolnodes: the destination pool, nothing out, two "
                       "ranges listed: lost=2 units",
                       "SOS poolnodes: while the destination pool has them: "
                       "nodes free=1.",
                       "SOS poolnodes: the destination pool donated: nodes "
                       "free=2.",
                       "SOS poolnodes: done"],
        "expect_clean_exit": True,
    },
    {
        # The region table's rows are typed (spec §12). The child's image blob
        # is typed as a pool by `pool_blobs`, so root drains a pool (13), an
        # allocation (0) and the RAM pool (13), and a pool's record answers
        # no allocation. Root allocates the blob pool's longest run, the whole
        # blob, and runs the child from that allocation, which exits with its
        # own code (`Exited` and 5).
        "name": "pool_boot",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": POOL_BOOT_PKG,
        "children": [CHILD_BYE_PKG],
        "pool_blobs": [0],
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={three}",
                       "SOS poolboot: row kinds 13 0 13",
                       "SOS poolboot: the pool row taken as an allocation: "
                       "none=1",
                       "SOS bye: exiting",
                       "SOS poolboot: run from its blob: status=65541",
                       "SOS poolboot: done"],
        "expect_clean_exit": True,
    },
    {
        # A pool row the build wrote off the protection grain (spec §2.5):
        # `pool_skew` moves its base one byte up and keeps its top. The kernel
        # starts the pool at the next grain boundary, so a fresh report is the
        # usual pool less one grain, and the first page allocated is on the
        # grain: `map` takes it, which it refuses for a base off the grain.
        "name": "pool_off_grain",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": POOL_SKEW_PKG,
        "pool": True,
        "pool_skew": 1,
        "expect_out": ["{banner}",
                       "SOS: boot regions={one}",
                       "SOS poolskew: total={pool_less_grain} "
                       "largest={pool_less_grain}",
                       "SOS poolskew: the first page mapped and written: 90",
                       "SOS poolskew: the rest in one piece: ok",
                       "SOS poolskew: done"],
        "expect_clean_exit": True,
    },
    {
        # A pool row off the grain at both ends (spec §2.5): `pool_skew` moves
        # its base one byte up and `pool_short_top` brings its top one byte
        # down. The kernel starts the pool at the next grain boundary and
        # serves only whole grains below the top, so a fresh report is the
        # usual pool less two grains, the first page maps, and what is left
        # after it is served in one allocation of exactly `largest`.
        "name": "pool_off_grain_top",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": POOL_SKEW_PKG,
        "pool": True,
        "pool_skew": 1,
        "pool_short_top": 1,
        "expect_out": ["{banner}",
                       "SOS: boot regions={one}",
                       "SOS poolskew: total={pool_less_two_grains} "
                       "largest={pool_less_two_grains}",
                       "SOS poolskew: the first page mapped and written: 90",
                       "SOS poolskew: the rest in one piece: ok",
                       "SOS poolskew: done"],
        "expect_clean_exit": True,
    },
    {
        # Installation. Root maps a page into itself and writes it, the inverse
        # of every protection proof above. It is also half the live-domain
        # rule: the scheduler skips equal domains, so a map into the running
        # process that did not reload would never be installed and the write
        # below would fault instead of printing.
        #
        # The second view shows two rows over one allocation with different
        # access, allowed: read-write in root and read-only in a child, created
        # and never started, since two rows of one process never overlap
        # (spec §2.5). The read afterwards says the second view changed
        # nothing.
        "name": "map_basics",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": MAP_BASICS_PKG,
        "children": [CHILD_FAULT_PKG],
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={three}",
                       "SOS mapbasics: wrote 0x5a read 90",
                       "SOS mapbasics: mapped read-only into a child",
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
        # A destination released while its child runs is held until the
        # child's death. Root allocates a destination from the front of the
        # pool that is the child's destination row, creates a child in it that
        # holds a four-word pattern in its data and parks, releases its only
        # handle on the destination, and allocates afresh from the pool and
        # looks for the pattern in what it gets.
        #
        # `found=0` is the line to read: while the child lives, the bytes of
        # its image are not on the pool's free list, so a fresh allocation is
        # somebody else's bytes. A kernel that freed the destination at the
        # release would serve its front again, first fit, and `found` would be
        # 1; root's write through its mapping would then change the child's
        # data and the child would say `pattern changed`. The case asserts the
        # child's own verdict and its status word, and that after its death a
        # fresh allocation of the same size is those bytes again (`found=1`):
        # the range comes home when the rows go.
        "name": "destination_held",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": DESTINATION_HELD_PKG,
        "children": [CHILD_LIVE_DATA_PKG],
        "pool_destinations": [0],
        "expect_out": ["{banner}",
                       "SOS desthold: child parked",
                       "SOS desthold: destination released",
                       "SOS desthold: next allocation taken and mapped",
                       "SOS desthold: while the child lives, witness found=0",
                       "SOS livedata: pattern intact",
                       "SOS desthold: child ended status=65607",
                       "SOS desthold: after its death, witness found=1",
                       "SOS desthold: done"],
        "expect_clean_exit": True,
    },
    {
        # Releasing a mapped allocation's handle does not free bytes the
        # mapping covers. Root maps a two-page allocation into itself, stamps
        # a mark and releases the allocation's only handle while it is mapped;
        # a fresh two-page allocation from the pool is written through a
        # second mapping and the first is read back.
        #
        # `shares the bytes=0` is the line to read: the released allocation
        # is a husk and not a free range, so the fresh one is other bytes. A
        # kernel that freed it at the release would serve its range again,
        # first fit, and `shares` would be 1. After the first mapping is
        # unmapped the husk's bytes come home: an allocation taken then
        # carries the mark.
        "name": "mapped_bytes_held",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": MAPPED_BYTES_HELD_PKG,
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={one}",
                       "SOS mapheld: wrote mark",
                       "SOS mapheld: released while mapped",
                       "SOS mapheld: fresh allocation shares the bytes=0",
                       "SOS mapheld: unmapped, a fresh allocation carries the "
                       "mark=1",
                       "SOS mapheld: done"],
        "expect_clean_exit": True,
    },
    {
        # An allocation a child's rows grant is no donation. Root creates a
        # child in its destination, a boot allocation, and leaves it
        # unstarted; then it donates the destination, whose only handle it
        # holds, to the kernel's slabs. The child's copied segments and stack
        # are rows counted on it, so the donation is a `BadState` fault (the
        # bytes are a child's), the case's last line.
        "name": "donate_rows_held",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": DONATE_ROWS_HELD_PKG,
        "children": [CHILD_LIVE_DATA_PKG],
        "expect_out": ["{banner}",
                       "SOS donaterows: a child created in the destination, "
                       "unstarted",
                       "SOS: process fault: object in the wrong state "
                       "process={zero}",
                       "SOS: process teardown handles="],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # Device authority. A window is carved non-exclusively (the same range
        # twice, which an allocation could not do), mapped, and then a window
        # the machine's protection hardware cannot express is a fault.
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
        # Binding a line is a System op on its own right (spec 2.8). A System
        # sibling whose keep mask leaves `InterruptBind` out cannot bind: the
        # rights check faults the caller, like any op's missing right.
        "name": "irq_bind_denied",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": IRQ_BIND_DENIED_PKG,
        "expect_out": ["{banner}",
                       "SOS binddenied: binding through a System handle "
                       "without the right",
                       "SOS: process fault: access denied",
                       "SOS: process teardown handles="],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # A line already bound is a `LineBound` fault on the caller. The first
        # Interrupt is destroyed by the teardown's close-all, so the teardown
        # line counts one: `handles={four}` is root's boot set (three) and that
        # Interrupt.
        "name": "irq_bind_twice",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": IRQ_BIND_TWICE_PKG,
        "expect_out": ["{banner}",
                       "SOS bindtwice: bound line 5",
                       "SOS bindtwice: binding it again",
                       "SOS: process fault: that interrupt line is already "
                       "bound",
                       "SOS: process teardown handles={four} threads={one} "
                       "events={zero} waiters={zero} interrupts={one}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # The kernel's own timer line is a `BadArg` fault. Its number is a board
        # fact, which the test links per target (`tests/irq-lines`).
        "name": "irq_bind_timer_line",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": IRQ_BIND_TIMER_PKG,
        "expect_out": ["{banner}",
                       "SOS bindtimer: binding the kernel timer's line",
                       "SOS: process fault: argument outside its domain",
                       "SOS: process teardown handles="],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # The consuming flag on an Interrupt target is a `BadArg` fault (spec
        # 2.2): its handle is its one name. The refused attach consumed
        # nothing, so the teardown still finds the Interrupt, the Waiter and the
        # Process handle beside root's boot set: `handles={six}`.
        "name": "irq_consume_refused",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": IRQ_CONSUME_REFUSED_PKG,
        "expect_out": ["{banner}",
                       "SOS irqconsume: attaching an Interrupt with the "
                       "consuming flag",
                       "SOS: process fault: argument outside its domain",
                       "SOS: process teardown handles={six} threads={one} "
                       "events={zero} waiters={one} interrupts={one}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # An Interrupt has one name (spec 2.10, 3): `Mint` is not in its default
        # set, so the raw `MINT_OP` is an `AccessDenied` fault from the rights
        # check. The teardown destroys the one Interrupt: `handles={four}` is
        # root's boot set and that handle.
        "name": "irq_mint_denied",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": IRQ_MINT_DENIED_PKG,
        "expect_out": ["{banner}",
                       "SOS irqmint: minting a sibling of an Interrupt",
                       "SOS: process fault: access denied",
                       "SOS: process teardown handles={four} threads={one} "
                       "events={zero} waiters={zero} interrupts={one}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # An attachment does not extend an Interrupt's life (spec 2.10).
        # Releasing the handle destroys the Interrupt: the line is bindable at
        # once (`rebound line 5`; a line still bound would end the process),
        # while the attachment holds the invalid slot (`invalid=3` of the four
        # slots) and fires with the no-line value, `line=0`, not the old line.
        # The level is terminal (`wait 2` reads the same) until the attachment
        # is removed by key, which frees the slot (`capacity=4`).
        "name": "interrupt_attached_release",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": IRQ_ATTACHED_PKG,
        "expect_out": ["{banner}",
                       "SOS irqattached: capacity with it live=3",
                       "SOS irqattached: released it, rebound line 5",
                       "SOS irqattached: capacity with it invalid=3",
                       "SOS irqattached: wait 1 key=61 line=0",
                       "SOS irqattached: wait 2 key=61 line=0",
                       "SOS irqattached: removed it, capacity=4",
                       "SOS irqattached: done"],
        "expect_clean_exit": True,
    },
    {
        # An Interrupt root binds is givable and sendable (spec 3, 2.1), usable
        # where it lands, and destroyed by its holder's death (spec 2.10). The
        # child attaches the given one and the sent one, then acks with no
        # fire outstanding: `BadState`, which shows the `Ack` right travelled
        # (`AccessDenied` would not). The child's teardown destroys both
        # (`interrupts={two}`), and root, which gave away the only name of each,
        # binds both lines again. A real fire serviced across a boundary is
        # `child_echo_*`.
        "name": "interrupt_give",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": IRQ_GIVE_PKG,
        "children": [CHILD_IRQ_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS irqgive: started",
                       "SOS childirq: attached the given and the sent "
                       "Interrupt",
                       "SOS childirq: acking with no fire outstanding",
                       "SOS: process fault: object in the wrong state "
                       "process={one}",
                       "SOS: process teardown handles={seven} threads={one} "
                       "events={zero} waiters={one} interrupts={two}",
                       "SOS irqgive: the holder ended status=131077",
                       "SOS irqgive: capacity after the holder's death=4",
                       "SOS irqgive: both lines bound again",
                       "SOS irqgive: done"],
        "expect_clean_exit": True,
    },
    {
        # A parked Waiter is woken when the holder of an attached Interrupt
        # dies (spec 2.2, 2.10). Root attaches the Interrupt, gives it to a
        # child that sleeps, and parks: the Interrupt's destruction wakes root
        # with its key and `line=0`, the no-line value. Round 0 is a
        # persistent attachment and the child exits holding the Interrupt, so
        # its teardown destroys it: the level is terminal
        # (`wait 2` reads the same), the invalid slot is held (`invalid=3`) and
        # freed by `remove`. Round 1 is one-shot and the child releases the
        # Interrupt itself: the delivery detaches inside the destruction and
        # drops the last reference, so the slot is free at once
        # (`capacity=4`).
        "name": "interrupt_peer_gone",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": IRQ_PEER_GONE_PKG,
        "children": [CHILD_HOLD_PKG, CHILD_HOLD2_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={four}",
                       "SOS irqpeergone: round 0 wait 1 key=91 line=0",
                       "SOS irqpeergone: round 0 wait 2 key=91 line=0",
                       "SOS irqpeergone: persistent, capacity with it "
                       "invalid=3",
                       "SOS irqpeergone: round 0 done, capacity=4",
                       "SOS irqpeergone: round 1 wait 1 key=91 line=0",
                       "SOS irqpeergone: round 1 done, capacity=4",
                       "SOS irqpeergone: done"],
        "expect_clean_exit": True,
    },
    {
        # A give refused for capacity hands an Interrupt back usable, as
        # `give_waitable_returns` does a Waiter. `gave 12` is the child's handle
        # budget. The refusal released nothing, so the returned Interrupt is
        # the live one: it attaches, and releasing it frees the line.
        "name": "give_interrupt_returns",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": GIVE_INTERRUPT_RETURNS_PKG,
        "children": [CHILD_BYE_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS irqreturns: gave 12 siblings then refused: this "
                       "process is at its quota",
                       "SOS irqreturns: the Interrupt give was refused: this "
                       "process is at its quota",
                       "SOS irqreturns: the returned Interrupt attached",
                       "SOS irqreturns: released it, rebound the line",
                       "SOS irqreturns: done"],
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
        # next allocation of the same size is the same page, proven by the
        # marker written through the first mapping and read through the second.
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
        # quota that counts installed rows could have refused the third. Each
        # row is a page of its own, one per round, since two rows of one
        # process never overlap (spec §2.5).
        "name": "quota_vs_wall",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": QUOTA_VS_WALL_PKG,
        "children": [CHILD_MAPWALL_PKG],
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={three}",
                       "SOS quotawall: created",
                       "SOS quotawall: gave a page per round",
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
        # runnable and the run would end at the deadlock stop; the
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
        # set and binds the console's line, gives both away, mints a masked
        # System beside them, starts the child and parks on its death.
        # Everything after `gave the line` is a process that root created,
        # driving a device and servicing a line root no longer has.
        #
        # What each asserted line rules out, beyond what the root-as-driver
        # twin already ruled out:
        #
        #   boot regions={three}   the child's blob, its RAM, and the device
        #                          row
        #   gave the window        `Transfer` spent on an `IoMemory`; root's
        #                          wrapper is disarmed and root can map it
        #                          nowhere
        #   gave the line          root bound the line
        #                          (`SystemOp.InterruptBind`) and the Interrupt
        #                          crossed with `Transfer`; root cannot ack it,
        #                          and the child holds no authority to bind
        #                          another line
        #   window mapped          the child called `Process.map(iomemory:)` on
        #                          its own Process handle: the give moved
        #                          authority, and where the row goes was then
        #                          the holder's business
        #   Zq7#                   give, drain, map, enable, park, wake and
        #                          ack, all across a process boundary
        #   status=65536           `Exited`(1) << 16 | 0, read by root through
        #                          the Process handle it kept, so the driver's
        #                          clean ending is a fact root observed rather
        #                          than one the console merely showed
        #
        # There is no timer anywhere, which matters twice. A driver waits on a
        # human-scale serial port, so a deadline would have to be guessed; and
        # §9a's one bounded exception to the console handover is a harness
        # kernel whose tick report narrates over a process that owns the
        # device. A case whose transcript matters after handover boots an
        # image that asks for no tick.
        "name": "child_echo_ns16550",
        "arches": ["riscv32", "riscv32-flat"],
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": DRIVER_CHILD_PKG,
        "children": [CHILD_ECHO_NS16550_PKG],
        "device": True,
        "stdin": ECHO_INPUT,
        "expect_out": ["{banner}",
                       "root image ok segments={three}",
                       "SOS: boot regions={three}",
                       "SOS: console handover",
                       "SOS driverchild: created",
                       "SOS driverchild: gave the window",
                       "SOS driverchild: gave the line",
                       "SOS driverchild: started",
                       "SOS echo: window mapped",
                       "SOS echo: driver up",
                       ECHO_INPUT,
                       "SOS echo: done 4 bytes on line 10",
                       # The driver's whole estate, counted by the kernel as it
                       # takes it back: the masked System its launcher gave it,
                       # the Process handle it derived, the IoMemory it was
                       # given, the Mapping that window produced, the Interrupt
                       # it was given and the Waiter it parked on. Six, and
                       # every
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
                       "root image ok segments={three}",
                       "SOS: boot regions={three}",
                       "SOS: console handover",
                       "SOS driverchild: created",
                       "SOS driverchild: gave the window",
                       "SOS driverchild: gave the line",
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
        # full allocation handle and it installs, and unmaps it again; root
        # then mints a sibling with `MemoryRight.MapExecute` masked out, proves
        # that sibling is a working handle by mapping read-write through it,
        # and asks for read-execute through it, which ends the process. The
        # unmap is there because two rows of one process never overlap
        # (spec §2.5).
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
                       # `process_self` minted, the page allocated, the
                       # executable Mapping (unmapped before the control maps
                       # the page, and still a handle), the narrowed sibling,
                       # and the read-write Mapping it installed. The refused
                       # map minted nothing, which is what a fault before any
                       # allocation means.
                       "SOS: process teardown handles={nine} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={zero}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # W^X, per row. One allocation is mapped read-write into root and
        # read-execute into a child, created and never started (two rows of one
        # process never overlap, spec §2.5), so the JIT-shaped pattern survives
        # while every individual protection row is W^X.
        #
        # The refusal goes through root's full allocation handle, which carries
        # `MemoryRight.MapExecute` and has just proved it. So this cannot be
        # the exec gate wearing a different hat: every right is present and the
        # word is what is wrong, which is what `argument outside its domain`
        # says and `access denied` would not.
        "name": "map_wx_refused",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": MAP_WX_REFUSED_PKG,
        "children": [CHILD_FAULT_PKG],
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={three}",
                       "SOS mapwx: read-write here, wrote 107 read 107",
                       "SOS mapwx: read-execute in a child: one allocation, "
                       "two rows",
                       "SOS mapwx: asking for write and execute in one row",
                       "SOS: process fault: argument outside its domain",
                       # Eleven: `map_exec_gated`'s nine minus the sibling it
                       # minted, since this case narrows nothing, plus the
                       # child's image, its destination and the child's
                       # Process handle. Two Mappings are among them, of one
                       # allocation, which is the second view counted rather
                       # than merely printed.
                       "SOS: process teardown handles={eleven} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={zero}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # Installing a region is `Memory.map`'s own right (spec §2.5, §3).
        # Root maps a page read-write through its full handle and unmaps it,
        # then mints a sibling keeping every right but `Map`: its word is
        # `Transfer | Mint | MapExecute` (1027). Read-write through it asks
        # no execute, so the one right missing is `Map`, and the ask is an
        # `AccessDenied` fault, the case's last line.
        "name": "map_needs_map",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": MAP_NEEDS_MAP_PKG,
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={one}",
                       "SOS mapright: the full handle maps read-write",
                       "SOS mapright: a sibling without Map: rights=1027",
                       "SOS mapright: asking for read-write through it",
                       "SOS: process fault: access denied",
                       "SOS: process teardown"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # An allocation carries the bits of the pool handle it came through.
        # A pool sibling keeps `Allocate` and `Map` and drops `MapExecute`; an
        # allocation through it maps read-write (the control) and is refused
        # execute.
        "name": "pool_keeps_rights",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": POOL_KEEPS_RIGHTS_PKG,
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={one}",
                       "SOS poolkeep: minted a pool sibling without "
                       "MapExecute",
                       "SOS poolkeep: its allocation maps read-write",
                       "SOS poolkeep: asking for execute through the "
                       "allocation",
                       "SOS: process fault: access denied",
                       "SOS: process teardown"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # The narrowing survives a second mint: a sibling of the narrowed
        # pool sibling, minted with a keep mask of every bit, still allocates
        # memory that is refused execute.
        "name": "pool_keeps_rights_twice",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": POOL_KEEPS_RIGHTS_TWICE_PKG,
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={one}",
                       "SOS pooltwice: minted a pool sibling without "
                       "MapExecute",
                       "SOS pooltwice: a sibling of that, asking for every "
                       "bit",
                       "SOS pooltwice: its allocation maps read-write",
                       "SOS pooltwice: asking for execute through it",
                       "SOS: process fault: access denied",
                       "SOS: process teardown"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # A `Carve` window carries its parent handle's rights. The parent keeps
        # `Carve` and drops `Map`; a window carved from it, and one carved from
        # that, are live (the second carve) and are refused a map.
        "name": "carve_keeps_rights",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": CARVE_KEEPS_RIGHTS_PKG,
        "device": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={one}",
                       "SOS carvekeep: minted a sibling without Map",
                       "SOS carvekeep: the window carves again",
                       "SOS carvekeep: asking to map the carved window",
                       "SOS: process fault: access denied",
                       "SOS: process teardown"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # `Event.mint(rights:)` narrows. The sibling works (the line before
        # the refusal), and `EventRight.Signal` is refused through it.
        "name": "mint_narrow_event",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": MINT_EVENT_PKG,
        "expect_out": ["{banner}",
                       "SOS mintevent: the sibling receives",
                       "SOS mintevent: asking to signal through it",
                       "SOS: process fault: access denied",
                       "SOS: process teardown"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # `Waiter.mint(rights:)` narrows. The sibling works (the line before
        # the refusal), and `WaiterRight.Attach` is refused through it.
        #
        # The teardown line pins the order of root's teardown (spec 2.10).
        # Root dies holding two Events, the first also attached to its Waiter.
        # The Waiter is invalidated before the close-all, so its attachment's
        # reference goes first and the close-all drops each Event's last
        # handle itself: `events={two}`. Were the Waiter invalidated after the
        # close-all, the close-all would drop the first Event's handle while
        # the attachment still held it, then free the Waiter through its last
        # handle (the sibling, minted last), and that free's cascade would
        # drop the Event's last reference uncounted: `events={one}`.
        "name": "mint_narrow_waiter",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": MINT_WAITER_PKG,
        "expect_out": ["{banner}",
                       "SOS mintwaiter: the sibling waits",
                       "SOS mintwaiter: asking to attach through it",
                       "SOS: process fault: access denied",
                       "SOS: process teardown handles={eight} threads={one} "
                       "events={two} waiters={one} interrupts={zero} "
                       "timers={zero} process={zero}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # `Timer.mint(rights:)` narrows. The sibling works (the line before
        # the refusal), and `TimerRight.Arm` is refused through it.
        "name": "mint_narrow_timer",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": MINT_TIMER_PKG,
        "expect_out": ["{banner}",
                       "SOS minttimer: the sibling attaches",
                       "SOS minttimer: asking to arm through it",
                       "SOS: process fault: access denied",
                       "SOS: process teardown"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # `Thread.mint(rights:)` narrows. The sibling works (the line before
        # the refusal), and `ThreadRight.Join` is refused through it.
        "name": "mint_narrow_thread",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": MINT_THREAD_PKG,
        "expect_out": ["{banner}",
                       "SOS mintthread: the sibling yields",
                       "SOS mintthread: asking to join through it",
                       "SOS: process fault: access denied",
                       "SOS: process teardown"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # `Clock.mint(rights:)` narrows. The sibling works (the line before
        # the refusal), and `ClockRight.Read` is refused through it.
        "name": "mint_narrow_clock",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": MINT_CLOCK_PKG,
        "expect_out": ["{banner}",
                       "SOS mintclock: the sibling makes a timer",
                       "SOS mintclock: asking for the time through it",
                       "SOS: process fault: access denied",
                       "SOS: process teardown"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # `IoMemory.mint(rights:)` narrows. The sibling works (the line before
        # the refusal), and `IoMemoryRight.Map` is refused through it.
        "name": "mint_narrow_iomemory",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": MINT_IOMEMORY_PKG,
        "device": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={one}",
                       "SOS mintiomem: the sibling carves",
                       "SOS mintiomem: asking to map through it",
                       "SOS: process fault: access denied",
                       "SOS: process teardown"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # `Mapping.mint(rights:)` narrows. The sibling works (the line before
        # the refusal), and `MappingRight.Unmap` is refused through it.
        "name": "mint_narrow_mapping",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": MINT_MAPPING_PKG,
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={one}",
                       "SOS mintmapping: the sibling reports its base",
                       "SOS mintmapping: asking to unmap through it",
                       "SOS: process fault: access denied",
                       "SOS: process teardown"],
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
        # giving it up (this image asks for no tick, and `start` only makes a
        # thread runnable), so:
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
        # another process, which is a two-process case: `pipe_reply_narrowed`
        # for the `Reply` op and `pipe_reply_wait_narrowed` for `ReplyWait`.
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
        # The `Reply` gate, reached through a give's keep mask. Root gives an
        # obligation to a child with `Transfer | Wait`, so the child holds an
        # obligation it may pass on and may not answer. The child reads the
        # missing bit back and replies anyway: the kernel kills it, which is a
        # fault because the caller held the handle and could have checked.
        #
        # The last row is why the death matters to the exchange. The child's
        # teardown releases the obligation undischarged, so the claim root
        # still holds answers `PeerClosed`. A gate that let the reply through
        # would let the child exit with its own code: the fault row would be
        # missing and root, refusing that status, would print neither of the
        # last two rows.
        "name": "pipe_reply_narrowed",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_REPLY_NARROWED_PKG,
        "children": [CHILD_NARROWED_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS narrowreply: gave the obligation without Reply",
                       "SOS childnarrowed: the obligation reads back without "
                       "Reply",
                       "SOS childnarrowed: now replying",
                       "SOS: process fault: access denied process={one}",
                       "SOS narrowreply: the child died of the denial",
                       "SOS narrowreply: the claim answers the other end of "
                       "this connection is gone"],
        "expect_clean_exit": True,
    },
    {
        # The second `Reply` gate. `PipeRequestRight.Reply` is checked once in
        # the `Reply` op and once in `ReplyWait`, and `pipe_reply_narrowed`
        # reaches only the first. Same shape, with the child spending the
        # obligation through `reply_wait`: root gives it under the tag the
        # child reads as that choice. The fault comes before the reply is
        # written or the child parks, so the last row is again the claim's
        # `PeerClosed`; a gate that let the op through would write the reply
        # and park the child on its empty Waiter, which stops the machine at
        # the deadlock instead.
        "name": "pipe_reply_wait_narrowed",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_REPLY_WAIT_NARROWED_PKG,
        "children": [CHILD_NARROWED_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS narrowreplywait: gave the obligation without "
                       "Reply",
                       "SOS childnarrowed: the obligation reads back without "
                       "Reply",
                       "SOS childnarrowed: now replying and waiting",
                       "SOS: process fault: access denied process={one}",
                       "SOS narrowreplywait: the child died of the denial",
                       "SOS narrowreplywait: the claim answers the other end "
                       "of this connection is gone"],
        "expect_clean_exit": True,
    },
    {
        # The `Resolve` gate, the client's half of the case above. Root keeps
        # the obligation and gives the claim to a child with `Transfer | Wait`.
        # The child reads the missing bit back and resolves anyway, and is
        # killed before the resolve can park.
        #
        # The last row is the teardown's decision seen from the other end: the
        # claim died with the child, so root's reply through the obligation it
        # kept answers `PeerClosed`. A gate that let the resolve through would
        # park the child on a question nobody answers, and the machine would
        # stop at the deadlock instead.
        "name": "pipe_resolve_narrowed",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_RESOLVE_NARROWED_PKG,
        "children": [CHILD_NARROWED_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS narrowresolve: gave the claim without Resolve",
                       "SOS childnarrowed: the claim reads back without "
                       "Resolve",
                       "SOS childnarrowed: now resolving",
                       "SOS: process fault: access denied process={one}",
                       "SOS narrowresolve: the child died of the denial",
                       "SOS narrowresolve: the obligation answers the other "
                       "end of this connection is gone"],
        "expect_clean_exit": True,
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
        # Nor can a fused reply carry the Waiter its wait leg parks on: were
        # it that Waiter's last handle, the reply could free the Waiter under
        # the thread about to park on it. The fault comes before the exchange
        # and the wait leg, so nothing parks.
        "name": "reply_wait_carries_waiter",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_REPLY_WAIT_WAITER_PKG,
        "expect_out": ["{banner}",
                       "SOS replywaitwaiter: reply_wait carrying its own "
                       "Waiter",
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
        # case would end at the deadlock stop, `wait_deadlock`'s own report.
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
                       "SOS childnarrow: the given rights read back as the "
                       "masks left them",
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
    {
        # An Event is givable (spec 3) and a message may carry one (spec 2.1).
        # Root gives a child one Event and sends it another; the child signals
        # both and sends them back, and root reads the words.
        "name": "event_give",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": EVENT_GIVE_PKG,
        "children": [CHILD_EVENT_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS eventgive: started",
                       "SOS eventgive: given Event reads 5",
                       "SOS eventgive: sent Event waits 50",
                       "SOS eventgive: done"],
        "expect_clean_exit": True,
    },
    {
        # A give refused for capacity hands an Event back usable, as
        # `give_full_returns` does a System. `gave 12` is the child's handle
        # budget, `DEFAULT_QUOTA_HANDLES`; the Event give meets the same
        # refusal, and the Event comes back in the error payload and reads the
        # bits root signalled.
        "name": "give_event_returns",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": GIVE_EVENT_RETURNS_PKG,
        "children": [CHILD_BYE_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS eventreturns: gave 12 siblings then refused: this "
                       "process is at its quota",
                       "SOS eventreturns: the Event give was refused: this "
                       "process is at its quota",
                       "SOS eventreturns: the refused Event reads 9",
                       "SOS: process teardown handles={eight} threads={one} "
                       "events={one} waiters={zero} interrupts={zero} "
                       "timers={zero} process={zero}"],
        "expect_clean_exit": True,
    },
    {
        # An Event outlives its owner (spec 2.10). The owner makes three Events,
        # sends one in a message and exits; its teardown frees the two only it
        # named (`events={two}`) and disowns the sent one, which root then
        # takes, signals and waits on. `held=7` is the Event slab less the one
        # live Event; if the owner's other two had not come back it would read
        # `5`, and `released=8` is the whole slab once root lets go.
        #
        # The probe is the refund half. It is created into the dead owner's
        # reclaimed record (`process={one}` again), makes one Event, and root
        # then releases the disowned Event. A free that credited the owner's
        # record would credit the probe, which would then make one Event more
        # than its allowance: `spent 4` is the allowance, `spent 5` the leak.
        "name": "event_outlive",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": EVENT_OUTLIVE_PKG,
        "children": [CHILD_EVENT_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS eventoutlive: started",
                       "SOS: process exit: code={owner_mark} process={one}",
                       "SOS: process teardown handles={five} threads={one} "
                       "events={two} waiters={zero} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS eventoutlive: the owner is gone",
                       "SOS eventoutlive: the owner's Event delivered 6",
                       "SOS: process exit: code={four} process={one}",
                       "SOS eventoutlive: probe spent 4",
                       "SOS eventoutlive: capacity held=7 released=8",
                       "SOS eventoutlive: done"],
        "expect_clean_exit": True,
    },
    {
        # A give's keep mask narrows an Event. The child gets `Signal` alone:
        # it signals the Event, then attaching it (which spends `Wait`) ends it
        # with `AccessDenied`. Root sees the fault in the child's status word.
        "name": "event_narrow",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": EVENT_NARROW_PKG,
        "children": [CHILD_EVENT_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS eventnarrow: started",
                       "SOS childevent: narrow signalled",
                       "SOS: process fault: access denied process={one}",
                       "SOS eventnarrow: the child faulted"],
        "expect_clean_exit": True,
    },
    {
        # A dying process counts only its own Events. Root makes an Event and
        # gives it to a keeper that exits holding it; the keeper's close-all
        # drops the last name and frees the Event, but it is charged to root,
        # so the keeper's `events=` reads zero (a count that ignored the owner
        # would read one). Root's slab is whole afterwards.
        "name": "event_keeper",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": EVENT_KEEPER_PKG,
        "children": [CHILD_EVENT_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS eventkeeper: started",
                       "SOS: process exit: code={keeper_mark} process={one}",
                       "SOS: process teardown handles={three} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS eventkeeper: the keeper is gone",
                       "SOS eventkeeper: capacity 8"],
        "expect_clean_exit": True,
    },
    {
        # A Thread is givable (spec 3) and outlives its process (spec 2.10).
        # Root gives a child one of its threads, which the child joins: `33` is
        # that thread's exit code, so a Thread given at boot works. The child
        # sends back two of its own in a message, one exited and one parked, and
        # dies. Root's join of the parked one was itself parked, and the death
        # wakes it with `Revoked`; afterwards the exited one still reads `82`,
        # its code (`Exited` wins), and the invalid one answers `Revoked` to a
        # join and to a start.
        #
        # `before=7` and `after=7` are the free thread slots (the slab less
        # root's own thread), counted by a round that leaves nothing behind.
        # They agree only if every thread of the case was freed: the given
        # thread by the child's teardown, which drops its last name; the two
        # the child sent by root's releases, one `Exited` and one invalid; and
        # the child's main thread by its teardown.
        #
        # The child's `threads=` is its main thread alone: the two it sent
        # survive its death, named by root's handles. Its `handles=` are its
        # System and Process handles, the given thread's handle and its outbox,
        # its main thread's own word, and the exited worker's Process handle;
        # the worker's Thread handle went with its exit.
        "name": "thread_give",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": THREAD_GIVE_PKG,
        "children": [CHILD_THREAD_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS threadgive: capacity before=7",
                       "SOS threadgive: started",
                       "SOS threadgive: the child joined the given thread: 33",
                       "SOS: process exit: code={sent_mark} process={one}",
                       "SOS: process teardown handles={six} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS threadgive: the parked join answered: what this "
                       "call was waiting for is gone",
                       "SOS threadgive: the child is gone",
                       "SOS threadgive: the late join reads 82",
                       "SOS threadgive: the held join answered: what this "
                       "call was waiting for is gone",
                       "SOS threadgive: starting the invalid thread answered: "
                       "what this call was waiting for is gone",
                       "SOS threadgive: minting from the exited thread worked",
                       "SOS threadgive: minting from the invalid thread "
                       "answered: what this call was waiting for is gone",
                       "SOS threadgive: capacity after=7",
                       "SOS threadgive: done"],
        "expect_clean_exit": True,
    },
    {
        # A child's exited threads count against its Thread quota until their
        # slots are freed (spec 2.9, 2.10). The leaver dies leaving root one
        # exited thread, which still joins as `82`. The quota child lands in
        # the leaver's reused record (`process={one}` both times) and makes
        # workers that exit and stay named: `made 3` is its quota of four less
        # its main thread. Root then releases the leaver's thread, whose free
        # must credit nobody: credited to the record, it would reach the quota
        # child and let its next create in (`still refused`). The child then
        # releases its workers and makes `again=3`: each free credited it once.
        "name": "thread_quota",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": THREAD_QUOTA_PKG,
        "children": [CHILD_THREAD_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS: process exit: code={left_mark} process={one}",
                       "SOS: process teardown handles={four} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS threadquota: the leaver's thread outlived it: "
                       "joins 82",
                       "SOS childthread: quota made 3 then refused: this "
                       "process is at its quota",
                       "SOS childthread: quota still refused: this process is "
                       "at its quota",
                       "SOS childthread: quota again=3 then refused: this "
                       "process is at its quota",
                       "SOS: process exit: code={quota_mark} process={one}",
                       "SOS: process teardown handles={three} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS threadquota: the quota child is gone",
                       "SOS threadquota: done"],
        "expect_clean_exit": True,
    },
    {
        # A process dies with a thread parked joining another process's thread
        # and a started thread still on the ready queue (spec 2.10). The child
        # parks joining root's thread, and its ending worker runs before its
        # other worker ever does. The teardown frees all three (`threads=3`).
        # Root's thread then exits: its joiner list must hold only root, whose
        # join reads `61`, and the never-run worker must stay unrun (it prints
        # if it runs).
        "name": "thread_hang",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": THREAD_HANG_PKG,
        "children": [CHILD_THREAD_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS: process exit: code={hung_mark} process={one}",
                       "SOS: process teardown handles={six} threads={three} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS threadhang: the child is gone",
                       "SOS threadhang: my thread joins 61",
                       "SOS threadhang: done"],
        "expect_clean_exit": True,
    },
    {
        # A dying process's close-all can stage a message that is ready for one
        # of its own parked threads, and the delivery must not reach it (spec
        # 2.10). The server child dies holding a full ring's obligations in a
        # message staged on its own connection, with its main thread parked on
        # its completion queue and root's call parked for room. Its close-all
        # drops that connection's outlet, the obligations go, the ring has room
        # and root's call is staged. A delivery to the parked thread would mint
        # into the dying table after the close-all had passed it, and the body
        # would go back holding that entry, which stops the machine; instead
        # the call stays staged until the server end goes, and root's call
        # answers `PeerClosed`.
        "name": "teardown_mint",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": TEARDOWN_MINT_PKG,
        "children": [CHILD_TEARDOWN_MINT_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS cqhold: holding 16 obligations",
                       "SOS: process exit: code={ended_mark} process={one}",
                       "SOS: process teardown handles={nine} threads={two} "
                       "events={zero} waiters={one} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS teardownmint: the parked call answered: the other "
                       "end of this connection is gone",
                       "SOS teardownmint: the server is gone",
                       "SOS teardownmint: done"],
        "expect_clean_exit": True,
    },
    {
        # A teardown invalidates before it releases (spec 2.10). Root posts the
        # only handle onto a thread it never starts in a message on its own
        # connection, keeps both ends and exits. The thread is invalidated
        # while the message still names it, and the close-all's release of the
        # outlet drops the message and frees the thread inside that cascade,
        # uncounted: `threads={one}` is root's own thread alone. Released
        # before it was invalidated, the thread would be found unfreed at a
        # count of zero and counted, and the line would read `threads={two}`.
        "name": "teardown_staged_thread",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": TEARDOWN_STAGED_THREAD_PKG,
        "expect_out": ["{banner}",
                       "SOS stagedthread: staged",
                       "SOS: process teardown handles={six} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={zero}"],
        "expect_clean_exit": True,
    },
    {
        # A give refused for capacity hands a Thread back usable, as
        # `give_event_returns` does an Event: `gave 12` is the child's handle
        # budget, and the returned thread starts and joins for its code, `7`.
        # Root's teardown frees both its threads, its own and the returned one,
        # since nothing else names either.
        "name": "thread_give_returns",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": THREAD_GIVE_RETURNS_PKG,
        "children": [CHILD_BYE_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS threadreturns: gave 12 siblings then refused: this "
                       "process is at its quota",
                       "SOS threadreturns: the Thread give was refused: this "
                       "process is at its quota",
                       "SOS threadreturns: the refused Thread joins 7",
                       "SOS: process teardown handles={nine} threads={two} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={zero}"],
        "expect_clean_exit": True,
    },
    {
        # Thread slots are counted (spec 2). Each round makes threads until the
        # slab refuses one and lets go of every handle before the thread runs;
        # the threads still run, publish a handle onto themselves and exit
        # through another, and root then releases the published one. `first=7`
        # is the slab less root's own thread, and `again=7` is every exited
        # slot coming back once nothing named it.
        "name": "thread_slots",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": THREAD_SLOTS_PKG,
        "expect_out": ["{banner}",
                       "SOS threadslots: first=7 again=7",
                       "SOS threadslots: done"],
        "expect_clean_exit": True,
    },
    {
        # `ThreadOp.Exit` releases the handle it is invoked through (spec 2,
        # 2.10). Root keeps a worker's creator handle and joins it twice after
        # the worker exited through its own: the code reads both times. Root
        # then makes more workers than the slab holds, each exiting through the
        # only name its exited thread has, and none is refused because each
        # slot came back at its exit. `child-churn` does the same against a
        # Thread quota of four (`churned 8`), then ends its process by exiting
        # its last thread; the exit line carries its code and the teardown line
        # follows. A second `child-churn`, in its deadlock role, parks its main
        # thread and lets its only other thread exit through its only handle,
        # while root waits on it with no timeout: nothing can run and nothing
        # can wake a thread, so the kernel stops the machine with its deadlock
        # report and exit status. That is the last phase, as nothing runs after
        # the stop.
        "name": "thread_exit",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": THREAD_EXIT_PKG,
        "children": [CHILD_CHURN_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS threadexit: the kept handle joins 71, and again 71",
                       "SOS threadexit: churned 20 workers, 21 ran",
                       "SOS childchurn: churned 8 workers, 8 ran",
                       "SOS: process exit: code={churn_mark} process={one}",
                       "SOS: process teardown handles={two} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS threadexit: the churn child ended through its last "
                       "thread",
                       "SOS childchurn: parking the main thread",
                       "SOS: deadlock: every thread is blocked and nothing "
                       "can wake one"],
        "expect_clean_exit": False,
        "expect_status": EXIT_DEADLOCK,
    },
    {
        # A Waiter and a Timer are givable (spec 3) and a message may carry
        # them (spec 2.1). Root gives a child its pair at boot, keeping no
        # handle: the child attaches the Timer to the Waiter and reports the
        # fire count (`fired 1`). The child sends back a pair of its own, which
        # root uses the same way while the child lives.
        #
        # `before` and `after` are the free slab slots, counted by making
        # objects until refused. They agree only if every object of the case
        # was freed: root's pair by the child's teardown, whose close-all drops
        # their last names and so frees another process's objects, and the
        # child's pair by root's releases while the child lives. The child's
        # teardown line counts none of the four (`waiters=0 timers=0`): two are
        # root's, and two were already gone. Nothing here is invalidated;
        # `timer_outlive` and `waiter_outlive` cover that.
        "name": "waitable_give",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": WAITABLE_GIVE_PKG,
        "children": [CHILD_WAITABLE_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS waitablegive: capacity before waiters=4 timers=8",
                       "SOS waitablegive: started",
                       "SOS waitablegive: the child's given Timer fired 1",
                       "SOS waitablegive: the sent pair fired 1",
                       "SOS: process exit: code={served_mark} process={one}",
                       "SOS: process teardown handles={six} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS waitablegive: the child is gone",
                       "SOS waitablegive: capacity after waiters=4 timers=8",
                       "SOS waitablegive: done"],
        "expect_clean_exit": True,
    },
    {
        # A give refused for capacity hands a Waiter and a Timer back usable,
        # as `give_event_returns` does an Event: `gave 12` is the child's
        # handle budget, and the returned Timer attached to the returned Waiter
        # fires once. Root's teardown frees both, since nothing else names
        # either.
        "name": "give_waitable_returns",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": GIVE_WAITABLE_RETURNS_PKG,
        "children": [CHILD_BYE_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS waitablereturns: gave 12 siblings then refused: "
                       "this process is at its quota",
                       "SOS waitablereturns: the Waiter give was refused: "
                       "this process is at its quota",
                       "SOS waitablereturns: the Timer give was refused: "
                       "this process is at its quota",
                       "SOS waitablereturns: the returned pair fired: key=60 "
                       "fires=1",
                       "SOS: process teardown handles={ten} threads={one} "
                       "events={zero} waiters={one} interrupts={zero} "
                       "timers={one} process={zero}"],
        "expect_clean_exit": True,
    },
    {
        # A Timer outlives its owner invalid (spec 2.10). The owner sends root
        # a Timer, keeps a sibling, arms it every 5 ms and dies; root has
        # attached the Timer to its own Waiter and parked. The death fires the
        # attachment with the Timer's ordinary record, zero fires because the
        # invalidation disarmed it, and the level is terminal: waiting again
        # reads the same. `Arm` and `Disarm` answer `Revoked`, and after root
        # sleeps 25 ms, past several of the periods the owner armed, the record
        # still reads zero. The owner's teardown line counts no Timer.
        #
        # `held=7` is the Timer slab less the invalid Timer root holds. The
        # probe lands in the owner's reclaimed record (`process={one}` again)
        # and holds one Timer when root releases the invalid one: a free that
        # credited the record would let the probe make one more than its
        # allowance, `spent 4`. `after=8` is the whole slab.
        "name": "timer_outlive",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": TIMER_OUTLIVE_PKG,
        "children": [CHILD_WAITABLE_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS timeroutlive: capacity before=8",
                       "SOS timeroutlive: started",
                       "SOS: process exit: code={owned_mark} process={one}",
                       "SOS: process teardown handles={five} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS timeroutlive: the parked wait was answered: key=84 "
                       "fires=0",
                       "SOS timeroutlive: waiting again reads key=84 fires=0",
                       "SOS timeroutlive: arming it answered: what this call "
                       "was waiting for is gone",
                       "SOS timeroutlive: disarming it answered: what this "
                       "call was waiting for is gone",
                       "SOS timeroutlive: minting it answered: what this "
                       "call was waiting for is gone",
                       "SOS timeroutlive: its rights still read",
                       "SOS timeroutlive: past its deadlines it reads key=84 "
                       "fires=0",
                       "SOS timeroutlive: the attachment was removed by key",
                       "SOS timeroutlive: capacity held=7",
                       "SOS: process exit: code={four} process={one}",
                       "SOS timeroutlive: probe spent 4",
                       "SOS timeroutlive: capacity after=8",
                       "SOS timeroutlive: done"],
        "expect_clean_exit": True,
    },
    {
        # A Waiter outlives its owner invalid (spec 2.10, 2.2). The owner sends
        # root a Waiter and dies; root has attached an Event to it and parked
        # on it. The death wakes the parked wait with `Revoked`, and a later
        # `Wait`, `Add` and `Remove` answer `Revoked` too. The Event is no
        # longer attached, so it attaches to a Waiter of root's own and
        # delivers there (`3`). The owner's teardown line counts no Waiter;
        # its `timers={one}` is the owner's Timer, whose last name was its own
        # sibling.
        #
        # `held=3` is the Waiter slab less the invalid Waiter root holds. The
        # probe holds its first Waiter when root releases the invalid one:
        # `spent 2` is its allowance, and a credit to the record would make it
        # 3. `after=4` is the whole slab.
        "name": "waiter_outlive",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": WAITER_OUTLIVE_PKG,
        "children": [CHILD_WAITABLE_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS waiteroutlive: capacity before=4",
                       "SOS waiteroutlive: started",
                       "SOS: process exit: code={owned_mark} process={one}",
                       "SOS: process teardown handles={five} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={one} process={one}",
                       "SOS waiteroutlive: the parked wait answered: what this "
                       "call was waiting for is gone",
                       "SOS waiteroutlive: a later wait answered: what this "
                       "call was waiting for is gone",
                       "SOS waiteroutlive: a later add answered: what this "
                       "call was waiting for is gone",
                       "SOS waiteroutlive: a later remove answered: what this "
                       "call was waiting for is gone",
                       "SOS waiteroutlive: a later mint answered: what this "
                       "call was waiting for is gone",
                       "SOS waiteroutlive: the Event moved to another Waiter: 3",
                       "SOS waiteroutlive: capacity held=3",
                       "SOS: process exit: code={two} process={one}",
                       "SOS waiteroutlive: probe spent 2",
                       "SOS waiteroutlive: capacity after=4",
                       "SOS waiteroutlive: done"],
        "expect_clean_exit": True,
    },
    {
        # A dying process's Waiter is taken apart with nobody parked on it
        # (spec 2.10). Root parks on the owner's Waiter, which holds root's
        # claim (consuming, the lower attachment slot) and root's obligation
        # (one-shot, root keeps its handle). Dropping the claim's row raises
        # the obligation's level on the same Waiter. Root must already be
        # answered `Revoked`; a delivery to it in the middle of the sweep would
        # drop the obligation's reference twice, the exchange would settle
        # under root's handle, and the reply would stop the machine. Instead
        # the reply answers `PeerClosed`.
        "name": "teardown_detach",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": TEARDOWN_DETACH_PKG,
        "children": [CHILD_WAITABLE_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS teardowndetach: started",
                       "SOS: process exit: code={owned_mark} process={one}",
                       "SOS: process teardown handles={five} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS teardowndetach: the parked wait answered: what "
                       "this call was waiting for is gone",
                       "SOS teardowndetach: the reply answered: the other end "
                       "of this connection is gone",
                       "SOS teardowndetach: done"],
        "expect_clean_exit": True,
    },
    {
        # A Waiter freed under a parked thread answers the thread before its
        # cascade releases what it watched (spec 2.2): `free_cascade` is
        # `teardown_detach`'s route through a release, in one process. The
        # claim's row is walked first and raises the obligation's level on the
        # next row; the worker parked on the Waiter must not be handed it.
        "name": "free_cascade",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": FREE_CASCADE_PKG,
        "expect_out": ["{banner}",
                       "SOS freecascade: parking",
                       "SOS freecascade: the reply answered: the other end of "
                       "this connection is gone",
                       "SOS freecascade: the parked wait answered: what this "
                       "call was waiting for is gone"],
        "expect_clean_exit": True,
    },
    {
        # A process dies with a thread parked on another process's Waiter
        # (spec 2.10): the parker's main thread parks on root's Waiter and its
        # second thread ends the process. The teardown takes the parked thread
        # off root's Waiter before it frees the thread's slot, so root's Event
        # signal afterwards is delivered to root's own wait (`5`), not to a
        # thread that no longer exists.
        "name": "waiter_parked",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": WAITER_PARKED_PKG,
        "children": [CHILD_WAITABLE_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS waiterparked: started",
                       "SOS: process exit: code={parked_mark} process={one}",
                       "SOS: process teardown handles={six} threads={two} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS waiterparked: the child is gone",
                       "SOS waiterparked: the Event delivered 5",
                       "SOS waiterparked: done"],
        "expect_clean_exit": True,
    },
    {
        # A dying process's Timer is invalidated before its close-all frees the
        # Waiter the Timer is attached to (spec 2.10). Root parks a thread on a
        # Waiter of its own and then gives the Waiter's one handle to a child
        # (`child-waitable`, the attacher), which attaches a Timer of its own
        # to it unarmed and exits holding the Waiter's last name. The
        # teardown's first step invalidates the Timer, and an attachment on an
        # invalidated waitable fires: the Waiter is still live, so the parked
        # thread is handed the Timer's ordinary record (the attacher's key,
        # `74`, and a fire count of `0`). The close-all then frees the Waiter
        # with nobody parked on it. Released first, the Waiter would be freed
        # under the parked thread, which answers it `Revoked` (spec 2.2), and
        # the Timer would be freed in that same release without ever firing.
        # The teardown line is asserted too: a teardown in which the Timer
        # goes uncounted, inside the Waiter's free, reads `timers=0`.
        "name": "teardown_timer_before_release",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": TEARDOWN_TIMER_FIRST_PKG,
        "children": [CHILD_WAITABLE_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS timerfirst: parking",
                       "SOS timerfirst: started",
                       "SOS: process exit: code={attached_mark} process={one}",
                       "SOS: process teardown handles={five} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={one} process={one}",
                       "SOS timerfirst: the parked wait was handed key=74 "
                       "payload=0",
                       "SOS timerfirst: done"],
        "expect_clean_exit": True,
    },
    {
        # An invalidated Timer is disarmed (spec 2.10). The firing owner arms
        # its Timer every 1 ms and sleeps 10 ms before it dies, so the Timer
        # has counted fires nobody consumed; root holds it attached but waits
        # elsewhere. After the death the record reads zero fires (the disarm
        # cleared the count), and again after root sleeps past more periods.
        "name": "timer_disarm",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": TIMER_DISARM_PKG,
        "children": [CHILD_WAITABLE_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS timerdisarm: started",
                       "SOS: process exit: code={owned_mark} process={one}",
                       "SOS: process teardown handles={five} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS timerdisarm: the child is gone",
                       "SOS timerdisarm: the invalid Timer reads fires=0",
                       "SOS timerdisarm: later it still reads fires=0",
                       "SOS timerdisarm: done"],
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
                       "root image ok segments={three}",
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
                       "root image ok segments={three}",
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
        # not serve that allocation, and one too big for the pool answers
        # `NoMemory`, which the case reports as the pool not coming back
        # instead of printing a smaller number.
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
                       "no room for what was asked",
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
                       "no room for what was asked",
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
                       "no room for what was asked",
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
                       # so the second ask of the pair cannot be served:
                       # `PoolOp.Allocate`'s `NoMemory`.
                       "SOS slabnodes: the leaked range is gone: this pool has "
                       "no free range that long"],
        "expect_clean_exit": True,
    },

    # Process-body donation.
    #
    # It lists three children and asks for no pool, which is why the runner's
    # two-children-plus-a-pool refusal never comes up: each child's
    # destination row is a pool (`pool_destinations`), each child's span is
    # allocated off its front, and the allocation root donates is the third
    # one's next.
    {
        "name": "process_donate",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PROCESS_DONATE_PKG,
        "children": [ECHO_CHILD_PKG, ECHO_CHILD2_PKG, ECHO_CHILD3_PKG],
        "pool_destinations": [0, 1, 2],
        "expect_out": ["{banner}",
                       "SOS: boot regions={six}",
                       # Claim 1: root plus two children is the body
                       # table.
                       "SOS procdonate: floor=3 third child refused: "
                       "no room for what was asked",
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
        # allocates two regions from a sibling of root's pool and carves a
        # window off a device view, posts all four to root and exits, disowning
        # them. Root drops the charger's Process handle and death attachment,
        # and record 1 is reclaimed although the four objects are live.
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
        # (one with a message staged on it), allocates a region, carves a
        # window and exits holding all of it (`handles={nine}`).
        #
        #   connections back=1   the same counts as before: the teardown's
        #   windows back=1       close-all freed the idle connection with its
        #                        last end, the busy one by the cascade out of
        #                        settling its staged exchange, and the window
        #                        with its handle
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
    # =========================================================================
    # Pids, the sender's pid in a message, and the sender handle
    # =========================================================================
    {
        # Root serves clients of one image, and checks what the kernel wrote.
        # Two clients each tell once and call twice: every message carries the
        # pid root reads through `Stats` on that client's handle (so it is the
        # same across the client's messages), the sender handle on each
        # obligation names the client and carries exactly `Wait | Stats`, and
        # every empty reply names root as its sender, which the client checks
        # against root's pid of 1. The two clients' pids differ. A third client
        # fills the ring and then calls: the call parks for room and is staged
        # when root frees a slot, and it must carry the client's pid and not
        # root's, the process whose op staged it. Last, root asks itself, which
        # reads a reply through the two doors no child uses (`resolve` and a
        # wait that delivers it). No pid's number is asserted beyond root's 1.
        "name": "pid_basics",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PID_BASICS_PKG,
        "children": [CHILD_PID_CLIENT_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS pids: root is pid 1",
                       "SOS pids: first client ok",
                       "SOS pids: second client ok",
                       "SOS pids: the clients differ",
                       "SOS pids: a parked call names its client",
                       "SOS pids: resolve names the replier",
                       "SOS pids: a delivered reply names the replier",
                       "SOS pids: done"],
        "expect_clean_exit": True,
    },
    {
        # The sender handle on a request whose sender is dead. A client tells
        # once and exits before anyone takes the message; the message still
        # names it, and the sender handle answers `Revoked` in the three states
        # the client's record can then be in: `Gone` and pinned by root's
        # handle, reclaimed, and taken over by a new process. The first needs
        # only the sender's state, the last needs the pid the message kept
        # beside the record index, since a live record sits at that index.
        "name": "pid_sender_dead",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PID_SENDER_DEAD_PKG,
        "children": [CHILD_PID_CLIENT_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS pidsdead: the sender has exited",
                       "SOS pidsdead: a dead sender root still holds answers "
                       "Revoked",
                       "SOS pidsdead: a reclaimed record answers Revoked",
                       "SOS pidsdead: a reused record answers Revoked",
                       "SOS pidsdead: done"],
        "expect_clean_exit": True,
    },
    {
        # What bounds the sender handle. A server takes a staged tell and asks
        # for its sender, under three ceilings root sets through the server's
        # handle first. Default ceilings: the handle carries the observe set,
        # `Wait | Stats` (1024 + 1048576 = 1049600). A Process ceiling without
        # `Stats`: it carries `Wait` alone (1024). A PipeRequest ceiling
        # without `Sender`: the obligation the server takes lacks the right,
        # so the ask ends the server with access denied, which root reads in
        # the status word (`Faulted` in the high half, 3 in the low) and the
        # server's own line never prints. Last, a server that empties its own
        # Process ceiling before it asks: the op still succeeds and the handle
        # reads rights 0, since an empty intersection is a handle with no
        # rights, not a refusal.
        "name": "pid_sender_rights",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PID_SENDER_RIGHTS_PKG,
        "children": [CHILD_PID_SERVER_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS pidrights: default ceilings",
                       "SOS pidserver: sender handle rights=1049600",
                       "SOS pidrights: a Process ceiling without Stats",
                       "SOS pidserver: sender handle rights=1024",
                       "SOS pidrights: a PipeRequest ceiling without Sender",
                       "SOS: process fault: access denied",
                       "SOS pidrights: an empty Process ceiling",
                       "SOS pidserver: sender handle rights=0",
                       "SOS pidrights: done"],
        "expect_clean_exit": True,
    },
    {
        # The sender handle across a forwarded obligation, on the topology of
        # `pipe_delegate_3p`. The far process finds that the message it was
        # sent names the middle (the immediate sender), that the sender handle
        # on the obligation that arrived with it names that process (the
        # middle calls, so it is alive and the handle is obtainable), and that
        # the sender handle on the forwarded obligation names root, the client
        # that asked. Its checks are in its exit code (81 when they hold),
        # which root reads. Root's own half: the reply it gets names the far
        # process, which answered, and not the middle that routed, and its
        # body is the pid the forwarding message carried, which must be the
        # middle's pid as `Stats` reads it.
        "name": "pid_forward",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PID_FORWARD_PKG,
        "children": [CHILD_PID_FORWARD_PKG, CHILD_PID_FAR_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={four}",
                       "SOS pidforward: created",
                       "SOS pidforward: started far and middle",
                       "SOS pidforward: the forwarding message named the "
                       "middle",
                       "SOS pidforward: the reply names the far process",
                       "SOS pidforward: middle=65607 far=65617",
                       "SOS pidforward: done"],
        "expect_clean_exit": True,
    },
    {
        # The pid column of the stats region's rows. Root maps the region and
        # reads every row against the pids `Process.stats()` answers: its own
        # row names it, and the pid is 1; exactly one row names a created
        # child, a row no process has occupied reads zero, the dead child's
        # row keeps its pid, and the create that takes the freed body renames
        # the row, so no row names the dead child any more.
        "name": "pid_stats_row",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PID_STATS_ROW_PKG,
        "children": [CHILD_BYE_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS pidrow: root's row names root",
                       "SOS pidrow: a created process's row names it",
                       "SOS pidrow: a body no process occupied reads zero",
                       "SOS bye: exiting",
                       "SOS pidrow: the row keeps its pid after the death",
                       "SOS pidrow: a create reuses the body and renames "
                       "the row",
                       "SOS pidrow: done"],
        "expect_clean_exit": True,
    },
    # =========================================================================
    # The process walk and the lookup by pid
    # =========================================================================
    {
        # Root keeps one running process that nothing names and one dead
        # process it still holds, and walks the machine's processes from
        # cursor 0: root, the running one and the dead one, once each in pid
        # order, every handle reading the pid the cursor names with exactly
        # the observe set and the status word root reads itself. The walk ends
        # in `Drained` after the last pid. A process that has not been started
        # is passed over: two walkers interleave while one is created between
        # their steps and both end at `Drained` without it (its launcher's own
        # handle still reads running); once root starts it, the cursor that
        # ended at `Drained` answers it. The lookup answers a running process
        # and a dead one still held, answers `Revoked` for a process never
        # started, for a pid never assigned and for zero, and for a started
        # process once its record is reclaimed; a handle the lookup minted
        # keeps a dead record answered once the creator's handle is gone. No
        # pid's number is asserted beyond root's 1.
        "name": "process_walk",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PROCESS_WALK_PKG,
        "children": [CHILD_BYE_PKG, CHILD_WALK_IDLE_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={four}",
                       "SOS bye: exiting",
                       "SOS walk: a walk from zero answers every process "
                       "once, in order",
                       "SOS walk: every handle carries exactly the observe "
                       "set",
                       "SOS walk: the walk ends in Drained after the last "
                       "pid",
                       "SOS walk: a never-started child reads running to "
                       "its launcher",
                       "SOS walk: a process created mid-walk is passed over "
                       "until started",
                       "SOS walk: a second walker shares no state with the "
                       "first",
                       "SOS walk: the same cursor answers the process once "
                       "it has started",
                       "SOS walk: lookup answers a running process",
                       "SOS walk: lookup answers a dead process still held",
                       "SOS walk: lookup of a process never started is "
                       "Revoked",
                       "SOS walk: lookup of a pid never assigned is Revoked",
                       "SOS walk: lookup of zero is Revoked",
                       "SOS walk: a reclaimed process is not answered",
                       "SOS walk: the loop the sysapi documents ends on "
                       "Drained",
                       "SOS walk: a held observe handle keeps a dead record "
                       "answered",
                       "SOS walk: done"],
        "expect_clean_exit": True,
    },
    {
        # The Process ceiling caps what the walk and the lookup mint, on the
        # sender handle's terms: root asks for a handle onto itself by each
        # under three ceilings it sets on itself. The default ceiling leaves
        # the observe set (1049600); a ceiling of `Wait | Exit` leaves `Wait`
        # (1024); an empty ceiling still succeeds and the handles carry
        # nothing, while the walk's cursor still reads root's pid.
        "name": "process_walk_ceiling",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PROCESS_WALK_CEILING_PKG,
        "expect_out": ["{banner}",
                       "SOS walkceil: default ceiling",
                       "SOS walkceil: a ceiling with Wait and Exit",
                       "SOS walkceil: an empty ceiling",
                       "SOS walkceil: done"],
        "expect_clean_exit": True,
    },
    {
        # The faults and refusals of the two ops, one child launched per role
        # and its ending read in the status word. A System handle without
        # `ProcessObserve` faults the caller (`AccessDenied`) on either op. An
        # address that is not word-aligned is `BadAlign` and one outside the
        # process `BadBuffer`, for the cursor and for the pid. A cursor in
        # read-only memory is `BadBuffer` too, since the op writes it back,
        # while a pid there is read and answered. The end of a
        # walk answers `Drained` and leaves the cursor as it was. A caller
        # with a full handle table is refused for the table's reason, its
        # cursor still holds what it held, and once a slot is free the same
        # cursor answers root and is written with root's pid. A lookup at a
        # full Handle row is `QuotaExceeded` and mints nothing, and at a full
        # table `NoResource`. A step refused onto a dead record leaves it
        # unreferenced: once its launcher lets go it is reclaimed, so neither
        # the lookup nor the walk answers it.
        "name": "process_walk_gate",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PROCESS_WALK_GATE_PKG,
        "children": [CHILD_WALK_FAULT_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS walkgate: ProcessNext without ProcessObserve",
                       "SOS walkgate: ProcessByPid without ProcessObserve",
                       "SOS walkgate: ProcessNext with a misaligned cursor",
                       "SOS walkgate: ProcessByPid with a misaligned pid",
                       "SOS walkgate: ProcessNext with a cursor outside the "
                       "process",
                       "SOS walkgate: ProcessByPid with a pid outside the "
                       "process",
                       "SOS walkgate: ProcessNext with a cursor in read-only "
                       "memory",
                       "SOS walkgate: ProcessByPid reads a pid from read-only "
                       "memory",
                       "SOS walkgate: the end of a walk leaves the cursor "
                       "as it was",
                       "SOS walkgate: a refused mint writes nothing to the "
                       "cursor",
                       "SOS walkgate: a refused lookup answers QuotaExceeded",
                       "SOS walkgate: a refused step takes no reference on a "
                       "dead record",
                       "SOS walkgate: a full table refuses both ops with "
                       "NoResource",
                       "SOS walkgate: done"],
        "expect_clean_exit": True,
    },
    {
        # A process started after a walk has passed its pid is missed by that
        # walk, and a walk from zero answers it. Root creates a process and
        # does not start it, starts another created after it (which parks for
        # good), and walks: root, then the later process, the unstarted one
        # passed over, then `Drained`. It starts the unstarted one and lets it
        # end; the cursor that ended at `Drained` still answers nothing, and a
        # walk from zero answers root, that process and the later one, in pid
        # order. It is its own case because `process_walk` keeps a process
        # running throughout, and the kernel has three process bodies.
        "name": "process_walk_start",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PROCESS_WALK_START_PKG,
        "children": [CHILD_BYE_PKG, CHILD_WALK_IDLE_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={four}",
                       "SOS bye: exiting",
                       "SOS walkstart: a walk that passed its pid misses a "
                       "started process",
                       "SOS walkstart: a walk from zero answers it",
                       "SOS walkstart: done"],
        "expect_clean_exit": True,
    },
    {
        # `ProcessOp.Kill` through handles with and without the right (spec
        # §8). Root launches `child-walk-idle`, which parks for good, and a
        # watcher (`child-kill`) parked on a Waiter with it attached, then
        # kills it through the handle `ProcessCreate` gave it: the status word
        # reads `Killed` (196650) with the code, and the watcher is woken with
        # that word. A second kill of the dead child answers `Revoked` and
        # leaves the word. A process that kills through the handle
        # `ProcessByPid` answers (the observe set, no kill right), one that
        # kills through a handle minted without the right that names itself,
        # and a process that kills through its own full handle are each ended
        # with a fault (`AccessDenied` 131075, `AccessDenied`, `NotSelf`
        # 131076) and the target of the first still runs. A child whose Process
        # ceiling lacks the right creates a process and its handle on it lacks
        # the right too: the kill is `AccessDenied`.
        "name": "process_kill",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PROCESS_KILL_PKG,
        "children": [CHILD_KILL_PKG, CHILD_WALK_IDLE_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={four}",
                       "SOS kill: a child parked in a wait is killed; its "
                       "status reads Killed",
                       "SOS kill: a watcher parked before the kill is woken "
                       "with Killed",
                       "SOS kill: a kill of a Gone child answers Revoked and "
                       "keeps its death",
                       "SOS kill: a lookup handle carries no kill right",
                       "SOS kill: a kill through a lookup handle is "
                       "AccessDenied and the target lives",
                       "SOS kill: a handle without the right is AccessDenied, "
                       "naming the caller or not",
                       "SOS kill: a kill through the caller's own handle is "
                       "a NotSelf fault",
                       "SOS kill: a child whose Process ceiling lacks the "
                       "right cannot kill its child",
                       "SOS kill: done"],
        "expect_clean_exit": True,
    },
    {
        # A kill ends the calls into and out of its target (spec §8). A
        # server (`child-kill`) holds the outlet of a connection and tells
        # root when a message is staged; a client (`child-kill-caller`) is
        # parked in its call on the other end. Root kills the server: the
        # staged message is abandoned and the client's call comes back
        # `PeerClosed`, its exit code (65587 in the status word). Then root is
        # the server: it takes the message a new client's call staged, reads
        # the sender handle (the observe set, no kill right), kills the client,
        # and its reply is told `PeerClosed`.
        "name": "process_kill_calls",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PROCESS_KILL_CALLS_PKG,
        "children": [CHILD_KILL_PKG, CHILD_KILL_CALLER_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={four}",
                       "SOS killcalls: a client parked in a call on a killed "
                       "server is told PeerClosed",
                       "SOS killcalls: the sender handle carries no kill "
                       "right",
                       "SOS killcalls: a reply to a client killed while it "
                       "waited is PeerClosed",
                       "SOS killcalls: done"],
        "expect_clean_exit": True,
    },
    {
        # A kill naming a process that was never started is a `BadState`
        # fault on the caller and changes nothing in it (spec §8). Root creates
        # `child-walk-idle` and does not start it, gives `child-kill` a handle
        # onto it that carries the kill right, and starts `child-kill` first.
        # The killer is ended with the fault (131077); the target reads
        # running, has made no system call and was not torn down, then starts
        # and runs, and root kills it through the same handle (196650).
        "name": "process_kill_created",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PROCESS_KILL_CREATED_PKG,
        "children": [CHILD_KILL_PKG, CHILD_WALK_IDLE_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={four}",
                       "SOS killer: killing",
                       "SOS: process fault: object in the wrong state",
                       "SOS killcreated: the kill of a Created process is a "
                       "fault",
                       "SOS killcreated: nothing in the target changed",
                       "SOS killcreated: the target starts and runs",
                       "SOS killcreated: once started it is killed and reads "
                       "Killed",
                       "SOS killcreated: done"],
        "expect_clean_exit": True,
    },
    {
        # Killing root stops the machine with a failing status of the
        # kernel's own (`ExitCode.KilledRoot`), whatever code the killer
        # passed, and root's teardown is the last thing on the console. Root
        # holds the only name of a process it never started and gives
        # `child-kill` a handle onto itself with the kill right; the kill's
        # code is 0, which would read as a pass if it were the machine's
        # status. The unstarted process's own teardown, owed once root's
        # handle table is released, is not run: the machine has stopped.
        "name": "process_kill_root",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PROCESS_KILL_ROOT_PKG,
        "children": [CHILD_KILL_PKG, CHILD_WALK_IDLE_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={four}",
                       "SOS killer: killing",
                       "SOS: process teardown handles="],
        "expect_tail": " timers={zero} process={zero}",
        "expect_clean_exit": False,
        "expect_status": EXIT_KILLED_ROOT,
    },
    {
        # Create, start, kill and release a child, over and over, with the
        # scheduler tick on. Each round's child (`child-kill-victim`) is
        # spinning, parked on an Event, asleep on a Timer or parked in a call
        # on a pipe made for the round, and root kills it once its
        # system-call count says it holds still. Every create, pipe and give
        # of every round must succeed, so a body, record, thread, Waiter,
        # Timer, Event, pipe or handle a teardown failed to return runs a
        # slab dry and the case goes red. It ends with a child that runs to
        # its exit.
        "name": "kill_churn",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "tick": True,
        "root_pkg": KILL_CHURN_PKG,
        "children": [CHILD_KILL_VICTIM_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS killchurn: 200 rounds, every create succeeded",
                       "SOS killchurn: a new child runs to its exit",
                       "SOS killchurn: done"],
        "expect_clean_exit": True,
    },
    {
        # A kill of a child that is runnable and not running. With the tick
        # on, root launches a spinner (`child-kill-victim`) that makes no
        # system call once it is set up, and yields until its system-call
        # count says it is in its loop: the processor comes back to root only
        # by the tick taking it from the spinner, so the spinner is on the
        # ready queue when the kill lands. The status word reads `Killed`
        # (196689) with the code, a second kill answers `Revoked` and leaves
        # the word, and a watcher attached after the death is woken at once
        # with it. A new child then runs in the same region to a clean exit,
        # so the dead child's slots are reused. Three rounds.
        "name": "kill_runnable",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "tick": True,
        "root_pkg": KILL_RUNNABLE_PKG,
        "children": [CHILD_KILL_VICTIM_PKG],
        "expect_out": (["{banner}", "SOS: boot regions={two}"]
                       + [line for n in range(3) for line in (
                           "SOS: process teardown handles=",
                           f"SOS killrun: round {n}: a spinning child is "
                           "killed and reads Killed\n",
                           f"SOS killrun: round {n}: a second kill is "
                           "Revoked, word kept\n",
                           f"SOS killrun: round {n}: a late watcher reads "
                           "Killed\n",
                           "SOS: process exit: code=",
                           "SOS: process teardown handles=",
                           f"SOS killrun: round {n}: a new child runs to its "
                           "exit\n")]
                       + ["SOS killrun: done\n"]),
        "expect_clean_exit": True,
    },
    {
        # A kill whose teardown finds a parked watcher's buffer gone ends that
        # watcher at the switch point the kill returns through (spec §8), with
        # the setup of `late_door_buffer_gone`: a process (`child-late-door`)
        # is parked on a Waiter that has another (`child-late-door2`)
        # attached, with its wait record in an allocation root mapped into it
        # and has since unmapped. Root kills the other process; the teardown's
        # notification finds the watcher's buffer gone and owes it a
        # `BadBuffer` end (131083), and root's first system call after the
        # kill, a read of the watcher's status word, already finds it ended.
        # The target reads `Killed` (196650). The watcher is another process
        # than the killer; the killer's own process owed the end is not built
        # here (the shared-Waiter trial of `late_door_buffer_gone` shows the
        # switch point paying the end of the process that owns the running
        # thread). Each child's destination and the allocation come from a
        # pool that is the child's destination row.
        "name": "kill_late_door",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": KILL_LATE_DOOR_PKG,
        "children": [CHILD_LATE_DOOR_PKG, CHILD_LATE_DOOR2_PKG],
        "pool_destinations": [0, 1],
        "expect_out": ["{banner}",
                       "SOS: boot regions={four}",
                       "SOS killdoor: the watcher found without a buffer "
                       "ends with BadBuffer",
                       "SOS killdoor: the target reads Killed",
                       "SOS killdoor: done"],
        "expect_clean_exit": True,
    },
    {
        # A buffer a process hands the kernel may not lie in memory it can
        # execute. A child is launched once per role and hands a pipe send a
        # body at an address in its own code (the call ends it with
        # `BadBuffer`, status word 131083), then in its read-only data and in
        # its writable data (each sent, and the child exits with its own OK
        # code). The second row is what the rule leaves alone: the image keeps
        # read-only data, string literals among it, in a segment of its own,
        # so the code segment is the only executable one.
        "name": "copy_from_code_refused",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": COPY_FROM_CODE_REFUSED_PKG,
        "children": [CHILD_EXEC_FAULT_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS copyexec: a body in the child's code is refused",
                       "SOS copyexec: a body in its read-only data is sent",
                       "SOS copyexec: a body in its writable data is sent",
                       "SOS copyexec: done"],
        "expect_clean_exit": True,
    },
    {
        # A child used in place holds its image's bytes until its teardown
        # (spec §5.6, §12). The blob's row is a pool (`pool_blobs`). Root
        # launches the child from the image allocated off the blob's front,
        # releases that allocation's handle, and allocates again: the pool
        # serves its free list first, so an allocation holding filler, not
        # the image, says the bytes are still held while the child runs, and
        # one after the child's death holding the image again says the
        # teardown gave them back. The child is hand-assembled and prints
        # `SOS-H` each time it runs; it is used in place only where the board
        # does that.
        "name": "image_held",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": IMAGE_HELD_PKG,
        "child_asm": "child_inplace.S",
        "pool_blobs": [0],
        "arches": ["riscv32", "riscv32-flat"],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS imageheld: launched from the image, its handle "
                       "released",
                       "SOS imageheld: the next allocation is filler: the "
                       "bytes are held",
                       "SOS-H",
                       "SOS imageheld: the child exited from its image",
                       "SOS imageheld: after the teardown the next "
                       "allocation is the image",
                       "SOS-H",
                       "SOS imageheld: done"],
        "expect_clean_exit": True,
    },
    {
        # An image allocation a child runs from in place is held by its rows
        # (spec §2.5, §5.6). The blob's row is a pool. Root launches the child
        # from the image allocated off its front and releases the image's
        # handle before the child runs; the child runs, and after its death
        # the image comes back to the pool. Last, a second child is created
        # from the image, which is donated, and an allocation a row grants is
        # no donation: root ends with `BadState`.
        "name": "image_rows_held",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": IMAGE_ROWS_HELD_PKG,
        "child_asm": "child_inplace.S",
        "pool_blobs": [0],
        "arches": ["riscv32", "riscv32-flat"],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS imagerows: launched from the image, its handle "
                       "released",
                       "SOS-H",
                       "SOS imagerows: the child exited from its image",
                       "SOS imagerows: after its death the image's bytes "
                       "came back",
                       "SOS: process fault: object in the wrong state "
                       "process={zero}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # A child whose one segment, used in place, ends two bytes past a
        # word at the blob's end: its grant would run past the image, and the
        # create is `BadImage`.
        "name": "child_grant_past_image",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": INPLACE_LAUNCH_PKG,
        "child_asm": "child_grant_past.S",
        "arches": ["riscv32", "riscv32-flat"],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS inplacelaunch: create refused: not a loadable "
                       "process image"],
        "expect_clean_exit": True,
    },
    {
        # A child running in place in its blob, and the blob mapped into it:
        # the mapping would meet the child's code row, its first row, and is
        # `NoResource` (spec §2.5). Then its destination, where the one row
        # the child holds is its stack grant, every segment being in place:
        # `NoResource` too. The child then runs and exits.
        "name": "map_over_in_place_image",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": INPLACE_LAUNCH_PKG,
        "child_asm": "child_inplace.S",
        "arches": ["riscv32", "riscv32-flat"],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS inplacelaunch: a mapping over the child's image: "
                       "no room for what was asked",
                       "SOS inplacelaunch: a mapping over the child's stack: "
                       "no room for what was asked",
                       "SOS-H",
                       "SOS inplacelaunch: the child exited from its image"],
        "expect_clean_exit": True,
    },
    {
        # A destination held by nothing but its child's stack row (spec §2.5,
        # §5.6, §2.10). The child is used in place in its blob, so its only
        # row in its destination is the stack grant. Root allocates the
        # destination off the destination row, typed as a pool, creates the
        # child in it and releases its only handle before the start: the pool
        # reports what it did while the allocation was out (`held=1`), the
        # row being a reference on it. After the child's death the pool is
        # whole again. Used in place only where the board does that.
        "name": "stack_row_held",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": STACK_ROW_HELD_PKG,
        "child_asm": "child_inplace.S",
        "pool_destinations": [0],
        "arches": ["riscv32", "riscv32-flat"],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS stackheld: a child in its destination, the handle "
                       "released",
                       "SOS stackheld: while the child lives, its destination "
                       "held=1",
                       "SOS-H",
                       "SOS stackheld: the child exited from its image",
                       "SOS stackheld: after its death the destination came "
                       "home=1",
                       "SOS stackheld: done"],
        "expect_clean_exit": True,
    },
    {
        # Initialized writable data arrives with its values: the loader copies
        # the writable segment's bytes, not only its zero-filled tail.
        "name": "data_init",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": DATA_INIT_PKG,
        "expect_out": ["{banner}",
                       "SOS datainit: 1234567 7654321 31337 42",
                       "SOS datainit: written and read back 43"],
        "expect_clean_exit": True,
    },
    {
        # An empty region donated is a status with the region handed back, as
        # for any region too small for a slot; the region rule asks only of a
        # region with bytes (spec §2.8). The pool row is three bytes long
        # (`pool_bytes`) and begins one byte past the grain (`pool_skew`), so
        # it reaches no grain boundary on any profile and the kernel mints a
        # pool of no bytes.
        "name": "donate_empty",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": DONATE_EMPTY_PKG,
        "pool": True,
        "pool_skew": 1,
        "pool_bytes": 3,
        "expect_out": ["{banner}",
                       "SOS: boot regions={one}",
                       "SOS donateempty: the pool holds total=0 largest=0",
                       "SOS donateempty: an empty pool is refused: "
                       "no room for what was asked",
                       "SOS donateempty: done"],
        "expect_clean_exit": True,
    },
    {
        # A thread's entry must lie in memory the process can execute. A
        # child is launched once per role and makes a thread of its own with
        # an entry in its writable data and then in its read-only data (each
        # ends it with `BadEntry`, status word 131078), then at the first
        # address past its code (the code row's upper bound excludes it), then
        # in its code (the thread is made and the child exits with its own OK
        # code). Every refused address is inside the child's region.
        "name": "thread_entry_not_exec",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": THREAD_ENTRY_NOT_EXEC_PKG,
        "children": [CHILD_EXEC_FAULT_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS entryexec: an entry in the child's writable data "
                       "is refused",
                       "SOS entryexec: an entry in its read-only data is "
                       "refused",
                       "SOS entryexec: an entry just past its code is refused",
                       "SOS entryexec: an entry in its code is made",
                       "SOS entryexec: done"],
        "expect_clean_exit": True,
    },
    {
        # A mapping does not cover a row of its target's image or stack. A
        # launcher keeps the memory it gave `process_create`, so it can map the
        # child's own region into it; where a mapping's address is its memory's
        # own that row would lie over the child's code and data. Root maps the
        # region read-write into one child and read-execute into another: each
        # map is refused with `NoResource` where addresses are the memory's
        # own and taken at an address of its own where the target translates.
        # Either way a send from the child's code ends it with `BadBuffer`
        # (131083). Where the read-execute map is refused a thread entry in
        # the child's data ends it with `BadEntry` (131078); where it is taken
        # the child's whole region is memory it may execute, so none of it is
        # a buffer (spec §2.2) and the drain of its boot set ends it with
        # `BadBuffer`.
        "name": "map_over_image_refused",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": MAP_OVER_IMAGE_PKG,
        "children": [CHILD_EXEC_FAULT_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS mapimg: read-write map: {own_map}",
                       "SOS mapimg: a send from the child's code is refused",
                       "SOS mapimg: read-execute map: {own_map}",
                       "SOS mapimg: {own_rx_map_end}",
                       "SOS mapimg: done"],
        "expect_clean_exit": True,
    },
    {
        # A mapping does not overlap a row its target already holds (spec
        # §2.5). Root launches `child-buffer-rule` once per trial and maps into
        # it: its own destination (over its copied segments and its stack), an
        # allocation twice, an allocation read-only and then read-write, the
        # device window twice; and the stats region into root twice. Where a
        # mapping takes its memory's own address each second row would lie
        # over the first and is refused with `NoResource`; where the target
        # places a mapping it is taken at an address of its own. A device
        # window is mapped at its own address on every profile, so its second
        # map is refused on every profile.
        #
        # After the refused read-write map no row over the read-only view
        # permits write, so a `PipeCreate` record there ends the child with
        # `BadBuffer`: the kernel does not store where the process cannot.
        # Two allocations side by side, mapped in either order, only touch and
        # are both taken.
        #
        # The last trial spends the child's mapping allowance, which is two,
        # around refused maps: the window taken, nine refused maps of it, an
        # allocation taken, and the next refused by the quota. A refused map
        # that charged would have spent the allowance early; one that
        # installed a row would have filled the grant record, which has nine
        # rows at most, and stopped the machine. The window mapped once more
        # then is refused by the quota as well, which is asked before the
        # rows are.
        "name": "map_overlap_refused",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": MAP_OVERLAP_PKG,
        "children": [CHILD_BUFFER_RULE_PKG],
        "pool": True,
        "device": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={four}",
                       "SOS mapoverlap: the child's destination, over its "
                       "segments and its stack: {own_map}",
                       "SOS mapoverlap: an allocation mapped twice: {own_map}",
                       "SOS mapoverlap: an allocation read-only, then "
                       "read-write: {own_map}",
                       "SOS mapoverlap: a record at the read-only view: "
                       "BadBuffer",
                       "SOS mapoverlap: a device window mapped twice: refused "
                       "with NoResource",
                       "SOS mapoverlap: the stats region mapped twice: "
                       "{own_map}",
                       "SOS mapoverlap: an allocation mapped where the last "
                       "one ends: taken",
                       "SOS mapoverlap: an allocation mapped where the next "
                       "one begins: taken",
                       "SOS mapoverlap: refused maps installed nothing and "
                       "charged nothing",
                       "SOS mapoverlap: done"],
        "expect_clean_exit": True,
    },
    {
        # Where a mapping meets a row, to the grain (spec §2.5). Carves of the
        # console UART's page, each a naturally aligned power of two, mapped
        # into root around the eight bytes at offset 8: a carve over its first
        # or its last four bytes, or holding it, is refused with `NoResource`,
        # and one ending where it begins or beginning where it ends is taken.
        #
        # riscv32 only: arm64 grants a device window by the page, and the
        # window is one page.
        "name": "map_overlap_carves",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": MAP_OVERLAP_CARVES_PKG,
        "device": True,
        "arches": ["riscv32", "riscv32-flat"],
        "expect_out": ["{banner}",
                       "SOS: boot regions={one}",
                       "SOS mapcarves: eight bytes at offset 8: taken",
                       "SOS mapcarves: its first four bytes again: refused "
                       "with NoResource",
                       "SOS mapcarves: its last four bytes again: refused "
                       "with NoResource",
                       "SOS mapcarves: thirty-two bytes holding it: refused "
                       "with NoResource",
                       "SOS mapcarves: eight bytes ending where it begins: "
                       "taken",
                       "SOS mapcarves: sixteen bytes beginning where it ends: "
                       "taken",
                       "SOS mapcarves: done"],
        "expect_clean_exit": True,
    },
    {
        # No two rows of one image meet (spec §5.6). Root writes each image
        # itself and creates a process from it: two segments that meet, whole
        # or by one grain at either end, or by a segment ending a byte into a
        # grain that the next is linked at, are `BadImage`, and so are three
        # whose first and last, or whose last two, meet. Segments that only
        # touch, in either order, or past a grain the first ends a byte into,
        # load. A segment's row is rounded up to the grain, so a thread's stack
        # top at the end of a segment's part-filled last grain is a stack. A
        # segment of no bytes records a row of no bytes, which holds no
        # address: inside another segment, listed after it or before it, or at
        # its base or its top, it meets nothing and the image loads.
        "name": "segments_meet",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": SEGMENTS_MEET_PKG,
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={one}",
                       "SOS segmeet: two at one address: BadImage",
                       "SOS segmeet: the second over the first's last grain: "
                       "BadImage",
                       "SOS segmeet: the second under the first's first "
                       "grain: BadImage",
                       "SOS segmeet: a byte into a grain, the next at that "
                       "grain: BadImage",
                       "SOS segmeet: the first and the last of three: "
                       "BadImage",
                       "SOS segmeet: the last two of three: BadImage",
                       "SOS segmeet: side by side: loaded",
                       "SOS segmeet: side by side, the second below: loaded",
                       "SOS segmeet: a byte into a grain, the next past it: "
                       "loaded",
                       "SOS segmeet: three side by side: loaded",
                       "SOS segmeet: no bytes inside another, listed after "
                       "it: loaded",
                       "SOS segmeet: no bytes inside another, listed before "
                       "it: loaded",
                       "SOS segmeet: no bytes at another's base: loaded",
                       "SOS segmeet: no bytes at another's top: loaded",
                       "SOS segmeet: a stack top at a part-filled grain's "
                       "end: a thread made",
                       "SOS segmeet: done"],
        "expect_clean_exit": True,
    },
    {
        # A destination is on the grain (spec §5.6). The build gives root two
        # allocations off the grain (`off_grain_destinations`): one whose
        # length is not whole grains and one whose base is a byte past the
        # grain. Root writes an image of one code segment, and a create into
        # either is `BadImage`. Into an allocation on the grain it loads, and a
        # thread is made on its stack's top word; with a one-byte executable
        # segment just below the stack it loads too, and a thread is made on
        # the stack's lowest grain, which no row the process may execute
        # grants.
        "name": "destination_off_grain",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": DEST_GRAIN_PKG,
        "pool": True,
        "off_grain_destinations": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={three}",
                       "SOS destgrain: a destination whose length is off the "
                       "grain: BadImage",
                       "SOS destgrain: a destination whose base is off the "
                       "grain: BadImage",
                       "SOS destgrain: a destination on the grain: loaded",
                       "SOS destgrain: a thread on its stack's top word: made",
                       "SOS destgrain: a one-byte executable segment under "
                       "the stack: loaded",
                       "SOS destgrain: a thread on the stack's bottom grain: "
                       "made",
                       "SOS destgrain: done"],
        "expect_clean_exit": True,
    },
    {
        # A mapping meets a row by user address (spec §2.5). arm64 only, where
        # a mapping's address is not its frames': root maps one allocation,
        # nearly the pool's length, into itself three times, and the pool's
        # frames lie just above root's region, so the second view's addresses
        # hold the allocation's own frame numbers. The third view meets no row
        # by address and is taken.
        "name": "map_meets_by_user_address",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": MAP_USER_ADDRESS_PKG,
        "pool": True,
        "arches": ["arm64"],
        "expect_out": ["{banner}",
                       "SOS: boot regions={one}",
                       "SOS mapuser: the second view's addresses hold its own "
                       "frames",
                       "SOS mapuser: a third view: taken",
                       "SOS mapuser: done"],
        "expect_clean_exit": True,
    },
    {
        # A buffer in a segment that is writable and executable is refused at
        # both doors. The child is linked as one such segment (the loader
        # accepts it), and the destination of a `PipeCreate` record (the
        # copy-out door) and the bodies of sends from its code, read-only data
        # and writable data each end it with `BadBuffer` (131083): the
        # segment's row permits write, so only its execute permission refuses
        # the record.
        "name": "wx_segment_not_buffer",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": WX_SEGMENT_REFUSED_PKG,
        "children": [CHILD_WX_FAULT_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS wxseg: a record written into the writable data "
                       "is refused",
                       "SOS wxseg: a body in the code is refused",
                       "SOS wxseg: a body in the read-only data is refused",
                       "SOS wxseg: a body in the writable data is refused",
                       "SOS wxseg: done"],
        "expect_clean_exit": True,
    },
    {
        # A buffer that was good when a call parked and is gone when the answer
        # arrives ends the process whose buffer it was, and the machine carries
        # on (spec §2.2). A call that parks keeps its buffer's address, and the
        # kernel resolves it again at the wake: a launcher maps an allocation
        # (the piece) into a child, the child parks with a buffer in it, and
        # the launcher unmaps it and makes the answer arrive.
        #
        # One trial per late door: a wait record, a fused call's reply, a
        # fused call's args record and its body when it parked for room, that
        # record when the room is the server end closing, and a wait record
        # whose piece a sibling thread of the child unmaps (so the process that
        # ends is the running one), and a wait record whose Timer fires while
        # the kernel idles (so nothing is running when the door refuses, and
        # the death it owes is what makes root runnable). Each ends the child
        # with `BadBuffer` (131083), which root reads before it starts the next
        # trial. The `Event` still holding its bits and the rings taking as
        # many messages as a fresh one are what "disposed of as a dead
        # receiver's would be" looks like from outside: nothing spent, nothing
        # pinned.
        #
        # The piece mapped into the child twice, the same bytes, with a call
        # through its read-write view (a send body, a pipe-creation record):
        # mapped read-write only it is a legal buffer. On arm64 the two views
        # are at two addresses, and mapped read-write and read-execute, in
        # either order, the read-write view is no buffer, since a row that
        # permits execute grants its bytes whatever the other row says
        # (`translate_user`, comparing the bytes rows grant). Where a mapping
        # takes its memory's own address the second map would lie over the
        # first and is refused (spec §2.5), so the call is through the first
        # row alone: the read-write view is taken after a refused read-execute
        # map, which shows the refusal installed nothing, and the read-execute
        # view is no buffer after a refused read-write one.
        #
        # The trials after those use a second child, linked at the second child
        # base: a Timer's interrupt taken from the doomed process's own
        # spinning thread; two processes parked on one Waiter with their
        # records gone, so one wake owes both a fault and the one that owns the
        # running thread must not leave the other unpaid (created in both
        # orders, which fixes the order of their records); a death owed by
        # another process's teardown, paid before the supervisor that teardown
        # wakes runs; two callers parked for room, the first ended and the
        # second served; and a process with two threads parked, whose second is
        # passed over so that the delivery reaches a process that lives.
        #
        # Each child's destination and its piece are allocated from a pool
        # that is the child's destination row (`pool_destinations`).
        "name": "late_door_buffer_gone",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": LATE_DOOR_GONE_PKG,
        "children": [CHILD_LATE_DOOR_PKG, CHILD_LATE_DOOR2_PKG],
        "pool_destinations": [0, 1],
        "expect_out": ["{banner}",
                       "SOS: boot regions={four}",
                       "SOS latedoor: wait record gone: the process ended "
                       "with BadBuffer",
                       "SOS latedoor: wait record gone: the signal was not "
                       "spent",
                       "SOS latedoor: call reply gone: the process ended "
                       "with BadBuffer",
                       "SOS latedoor: call reply gone: the ring is whole",
                       "SOS latedoor: call record gone: the process ended "
                       "with BadBuffer",
                       "SOS latedoor: call record gone: the ring is whole",
                       "SOS latedoor: call body gone: the process ended "
                       "with BadBuffer",
                       "SOS latedoor: call body gone: the ring is whole",
                       "SOS latedoor: call record gone, server end closed: "
                       "the process ended with BadBuffer",
                       # The running process's own two threads are both
                       # freed by its teardown, the running one included.
                       "SOS: process teardown handles={ten} threads={two} "
                       "events={one} waiters={one}",
                       "SOS latedoor: wait record unmapped by a sibling: "
                       "the process ended with BadBuffer",
                       "SOS latedoor: wait record gone while idle: the "
                       "process ended with BadBuffer",
                       "SOS latedoor: exec row, send body, rw only: taken",
                       "SOS latedoor: exec row, send body, rw then rx: "
                       "{late_rw_rx}",
                       "SOS latedoor: exec row, send body, rx then rw: "
                       "{late_rx_rw}",
                       "SOS latedoor: exec row, record, rw only: taken",
                       "SOS latedoor: exec row, record, rw then rx: "
                       "{late_rw_rx}",
                       "SOS latedoor: exec row, record, rx then rw: "
                       "{late_rx_rw}",
                       "SOS latedoor: wait record gone, door reached from an "
                       "interrupt: BadBuffer",
                       "SOS latedoor: shared waiter, A's record first: both "
                       "ended with BadBuffer",
                       "SOS latedoor: shared waiter, B's record first: both "
                       "ended with BadBuffer",
                       "SOS latedoor: death owed by a death: ended before its "
                       "supervisor ran",
                       "SOS latedoor: second caller waiting for room: served "
                       "after the first ended",
                       "SOS latedoor: two threads of one process: the "
                       "delivery went to the other process",
                       "SOS latedoor: done"],
        "expect_clean_exit": True,
    },
    {
        # Any memory a row of a process grants is a buffer, judged by the row,
        # on every profile (spec §2.2), and a thread can run on any of it that
        # it may write (§2.7). Root maps allocations from the pool (and the
        # device window) into `child-buffer-rule`, launched once per trial,
        # and reads the §8 word it ends with: `Exited` with 7 (65543) where
        # the kernel took its call, `BadBuffer` (131083) or `BadEntry`
        # (131078). A body and a record in a mapped allocation are taken, and
        # so is a wait record written there at the wake. A buffer is judged
        # whole: a body ending one byte past a mapping's top, and a record
        # starting one word below it, are `BadBuffer`; a wait record one word
        # below it, and the body of a call that would park on a full ring,
        # are refused at the call, before anything parks. A read-only mapping
        # is a source and no destination. Two allocations mapped side by side
        # are two rows: a body ending at the first one's top is taken, and
        # one running into the second is `BadBuffer`; with the second
        # read-execute, a body ending where its bytes begin is taken. A device
        # row is never a buffer. A thread whose stack top is a mapped
        # allocation's top runs with its stack in it; a stack top in a
        # read-only mapping, and one of zero, are `BadEntry`. The stats
        # region's row, which the child maps itself, permits read only: a body
        # from it is taken and a record into it is `BadBuffer`. Last,
        # `child-data-first`, whose first segment is its writable data, has a
        # record written there: the first row the loader recorded is a row
        # like any other. The pool is that child's destination row
        # (`pool_destinations`), which its destination is allocated from too.
        "name": "buffer_rule",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": BUFFER_RULE_PKG,
        "children": [CHILD_BUFFER_RULE_PKG, CHILD_DATA_FIRST_PKG],
        "pool_destinations": [1],
        "device": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={five}",
                       "SOS bufrule: a body in a mapped allocation: taken",
                       "SOS bufrule: a record in a mapped allocation: taken",
                       "SOS bufrule: a wait record in a mapped allocation, "
                       "written at the wake: taken",
                       "SOS bufrule: a body that ends one byte past a "
                       "mapping's top: BadBuffer",
                       "SOS bufrule: a record one word below a mapping's top: "
                       "BadBuffer",
                       "SOS bufrule: a wait record one word below a mapping's "
                       "top: BadBuffer at the call",
                       "SOS bufrule: a call's body running out of its "
                       "mapping, parked for room: BadBuffer at the call",
                       "SOS bufrule: a body in a read-only mapping: taken",
                       "SOS bufrule: a record in a read-only mapping: "
                       "BadBuffer",
                       "SOS bufrule: a body at the end of the first of two "
                       "adjacent mappings: taken",
                       "SOS bufrule: a body across two adjacent mappings: "
                       "BadBuffer",
                       "SOS bufrule: a body ending at a read-write mapping's "
                       "top, a read-execute one after it: taken",
                       "SOS bufrule: a body in a device mapping: BadBuffer",
                       "SOS bufrule: a stack in a mapped allocation: the "
                       "thread ran on it",
                       "SOS bufrule: a stack top in a read-only mapping: "
                       "BadEntry",
                       "SOS bufrule: a stack top at zero: BadEntry",
                       "SOS bufrule: a body in the stats region: taken",
                       "SOS bufrule: a record in the stats region: BadBuffer",
                       "SOS bufrule: a record in a child's first segment, its "
                       "writable data: taken",
                       "SOS bufrule: done"],
        "expect_clean_exit": True,
    },
    {
        # A writable view of bytes a process may execute through another view
        # is no buffer (spec §2.2): rows are compared by the bytes they grant,
        # not by the addresses the process sees them at. Only arm64 places a
        # mapping at an address of its own, so only there can one process hold
        # two views of the same bytes. An allocation mapped into
        # `child-buffer-rule` read-write and read-execute, in either order, is
        # no buffer through the read-write view (`BadBuffer`, 131083), and
        # read-write only it is (`Exited` with 7, 65543). The child's own
        # destination mapped into it read-write, by the launcher that keeps
        # it, is a view of the child's code (at its base) and its stack (at its
        # top): a body or a record in the code through it is `BadBuffer`, a
        # body in the stack is taken. The image links its read-only data at
        # its code's top, so a body at the read-only data's first byte, which
        # starts where the code's bytes end, is taken. `child-data-first`,
        # with its writable data linked below its code, has its destination
        # mapped into it read-write: a body through that view that starts in
        # the last doubleword below the code and runs into it is `BadBuffer`.
        # The pool is that child's destination row (`pool_destinations`).
        "name": "buffer_alias",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": BUFFER_ALIAS_PKG,
        "children": [CHILD_BUFFER_RULE_PKG, CHILD_DATA_FIRST_PKG],
        "pool_destinations": [1],
        "arches": ["arm64"],
        "expect_out": ["{banner}",
                       "SOS: boot regions={four}",
                       "SOS bufalias: an allocation, read-write only: a body "
                       "taken",
                       "SOS bufalias: an allocation, read-write then "
                       "read-execute: a body BadBuffer",
                       "SOS bufalias: an allocation, read-execute then "
                       "read-write: a body BadBuffer",
                       "SOS bufalias: an allocation, read-write then "
                       "read-execute: a record BadBuffer",
                       "SOS bufalias: its destination, read-write: taken at "
                       "an address of its own",
                       "SOS bufalias: a body in its code through the view: "
                       "BadBuffer",
                       "SOS bufalias: a record in its code through the view: "
                       "BadBuffer",
                       "SOS bufalias: a body in its stack through the view: "
                       "taken",
                       "SOS bufalias: a body at the first byte of its "
                       "read-only data, at its code's top: taken",
                       "SOS bufalias: a body from its data into its code "
                       "through the view: BadBuffer",
                       "SOS bufalias: done"],
        "expect_clean_exit": True,
    },
    {
        # A call parked for room keeps its args record in the caller's memory,
        # and the kernel reads it again at the resume, so a sibling thread can
        # rewrite it in between (spec §2.1). The record the resume reads is
        # held to the rules the call was: a body length past `PIPE_BODY_BYTES`
        # ends the process with `BadArg` (131081) and stages nothing, and a
        # record rewritten into another legal message is that message, staged
        # with the length the resume read. The child's second thread runs only
        # once the first has parked, since nothing ticks in this case. Each
        # child's destination is allocated from a pool that is its destination
        # row.
        "name": "room_record_rewritten",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": ROOM_REWRITE_PKG,
        "children": [CHILD_ROOM_REWRITE_PKG],
        "pool_destinations": [0],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS roomrewrite: length past a slot: the process "
                       "ended with BadArg",
                       "SOS roomrewrite: length past a slot: the ring is "
                       "whole",
                       "SOS roomrewrite: shorter length: the message carries "
                       "the new length",
                       "SOS roomrewrite: shorter length: the call was "
                       "answered",
                       "SOS roomrewrite: done"],
        "expect_clean_exit": True,
    },
    {
        # The kernel writes a result only into a row that permits write (spec
        # §2.2). A launcher maps a piece into its child read-only, an
        # allocation from the child's destination row, a pool, beside the
        # child's destination: a pipe-creation record aimed there ends the
        # child with `BadBuffer` and leaves the piece as the launcher stamped
        # it. A
        # wait parked with its record in the piece mapped read-write, and the
        # piece unmapped and mapped again at the same address before the
        # wake, is judged by the row there at the wake: read-only ends the
        # child and writes nothing, read-write is written. Where the target
        # places a mapping, the address the unmap freed is the one the new
        # mapping takes.
        "name": "read_only_row_not_destination",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": RO_DESTINATION_PKG,
        "children": [CHILD_RO_DESTINATION_PKG],
        "pool_destinations": [0],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS rodest: read-write piece, record: taken",
                       "SOS rodest: read-only piece, record: the process "
                       "ended with BadBuffer",
                       "SOS rodest: read-only piece, record: the piece is as "
                       "root left it",
                       "SOS rodest: remapped read-only while parked: the "
                       "process ended with BadBuffer",
                       "SOS rodest: remapped read-only while parked: the "
                       "piece is as root left it",
                       "SOS rodest: remapped read-write while parked: "
                       "delivered into the new row",
                       "SOS rodest: done"],
        "expect_clean_exit": True,
    },
    {
        # The same rule over an image's own rows, on every profile (spec
        # §2.2): a child linked with its read-only data above its writable
        # data has that segment between two destinations, its data and its
        # stack. A pipe-creation record aimed at a word of it
        # ends the child with `BadBuffer`; the same record aimed at its
        # writable data is taken. A wait on an Event already signalled (which
        # the kernel would deliver at once) is held to the same: its record
        # in the read-only data ends the child at the call, and its record in
        # the writable data is delivered.
        "name": "read_only_segment_not_destination",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": RO_SEGMENT_PKG,
        "children": [CHILD_RO_SEGMENT_PKG],
        "pool_destinations": [0],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS roseg: a record into the read-only data: "
                       "BadBuffer",
                       "SOS roseg: a record into the writable data: taken",
                       "SOS roseg: a wait record into the read-only data: "
                       "BadBuffer",
                       "SOS roseg: a wait record into the writable data: "
                       "taken",
                       "SOS roseg: done"],
        "expect_clean_exit": True,
    },
    {
        # The kernel places the image it validated, whatever is written into
        # the image's memory while it places it (spec §2.8). The image is a
        # piece mapped into a child, an allocation from the child's
        # destination row, a pool, beside the child's destination, and the
        # child parks a wait with its record over the
        # image's second segment record, fed by a Timer of root's. A fire
        # during the first segment's long zero-filled tail is delivered at a
        # preemption point, between the two segments. Root tries until it
        # finds the record on the image of a create that succeeded, moving the
        # Timer's delay toward the placement after each miss; how many tries
        # that takes is the host's and is not printed. The image root builds
        # carries `ARCH_RISCV32` in its header, so arm64's loader refuses it.
        "name": "image_written_while_placed",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": IMAGE_REREAD_PKG,
        "children": [CHILD_RO_DESTINATION_PKG],
        "pool_destinations": [0],
        "pool": True,
        "arches": ["riscv32", "riscv32-flat"],
        "expect_out": ["{banner}",
                       "SOS: boot regions={three}",
                       "SOS imgreread: a record landed during placement: "
                       "created",
                       "SOS imgreread: done"],
        "expect_clean_exit": True,
    },
    {
        # A device window of four bytes grants those four bytes and no more
        # (spec §2.5). Root carves the console UART's page down to the four
        # registers at offset 4, maps them, reads the scratch register inside
        # the carve, and then reads the receive buffer at offset 0, just below
        # it, which must fault. A window that size is one NA4 PMP entry; as a
        # NAPOT entry its size would come from the base's trailing ones, here
        # sixteen bytes from the page's start.
        #
        # riscv32 only: the assertion is the hardware's denial, which the flat
        # profile disclaims, and arm64 grants device memory by the page, so a
        # four-byte carve is a `BadArg` there before anything is mapped.
        "name": "device_window_four_bytes",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": DEVICE_FOUR_BYTES_PKG,
        "device": True,
        "arches": ["riscv32"],
        "expect_out": ["{banner}",
                       "SOS: boot regions={one}",
                       "SOS devfour: mapped four bytes at offset 4",
                       "SOS devfour: read inside the window",
                       "SOS devfour: reading below the window",
                       "SOS: fault "],
        "expect_clean_exit": False,
    },
    {
        # A late door asks its questions in the order the call did (spec
        # §2.2). A launcher maps a piece into a child, an allocation from the
        # child's destination row, a pool, beside the child's destination; the
        # child parks a fused call with a buffer in it, and the launcher
        # unmaps the piece before the answer arrives. A reply carrying a
        # handle ends the child with `BadBuffer` whether its table has room or
        # is full, since the reply
        # buffer is asked before the table, and the Event the reply carried
        # goes back to its slab (root counts the Events it can create before
        # and after). With the buffer kept, a full table is still answered
        # `QuotaExceeded`. A call parked for room asks its body before the
        # connection's state: a body that is gone ends the child with
        # `BadBuffer` whether the server end is open, closed while parked or
        # closed at the call, and a call whose buffers are all there is
        # answered `PeerClosed` when the server end closes. A wake that
        # carries only a status writes no buffer and asks about none: with the
        # reply buffer gone, a request dropped unanswered and a server end
        # closed while the call is parked for room are answered `PeerClosed`
        # and the child runs on, while a reply after the room appears ends it
        # with `BadBuffer`.
        "name": "late_door_order",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": REPLY_DOOR_ORDER_PKG,
        "children": [CHILD_REPLY_DOOR_ORDER_PKG],
        "pool_destinations": [0],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS replyorder: reply gone, a handle, table room: "
                       "BadBuffer",
                       "SOS replyorder: reply gone, a handle, table room: "
                       "the carried Event was released",
                       "SOS replyorder: reply gone, a handle, table full: "
                       "BadBuffer",
                       "SOS replyorder: reply gone, a handle, table full: "
                       "the carried Event was released",
                       "SOS replyorder: reply kept, a handle, table full: "
                       "answered QuotaExceeded",
                       "SOS replyorder: body gone, server end open, parked: "
                       "BadBuffer",
                       "SOS replyorder: body gone, server end closed while "
                       "parked: BadBuffer",
                       "SOS replyorder: body gone, server end closed at the "
                       "call: BadBuffer",
                       "SOS replyorder: body kept, server end closed while "
                       "parked: answered PeerClosed",
                       "SOS replyorder: reply gone, the request dropped "
                       "unanswered: answered PeerClosed",
                       "SOS replyorder: parked for room, reply gone, server "
                       "end closed: answered PeerClosed",
                       "SOS replyorder: parked for room, reply gone, room "
                       "then a reply: BadBuffer",
                       "SOS replyorder: done"],
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

    Plain `clang` is tried before the Homebrew `llvm` ones. macOS's Apple
    clang mis-drives the riscv integrated assembler, so it fails the probe and
    a real LLVM clang is found there instead. `SOS_CLANG`, when set, is tried
    ahead of all of them. The probe compiles each architecture's own `boot.S`,
    because "can target riscv32" and "can target aarch64" are separate
    questions and one harness needs both answered yes.
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
              *arch.get("kernel_c_args", []),
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


def _blade_env(clang, lld=None):
    env = dict(os.environ)
    # The trailing -Oz rides into every blade-driven sawc invocation: the SAWC
    # value is a full command line split on spaces (blade's documented
    # `CC="ccache gcc"` convention), which carries -Oz on all freestanding
    # builds to the packages blade builds (root and every test sosimg).
    env["SAWC"] = tc().sawc_env_value() + " -Oz"
    # macOS's Apple clang mis-drives the riscv integrated assembler; hand Blade
    # the same clang this harness probed for.
    env["SOS_CLANG"] = clang
    # A board whose images only its vendor's linker can link (the ESP32-S3's
    # Xtensa objects) names that linker; Blade's default is `ld.lld` on PATH.
    if lld is not None:
        env["SOS_LD"] = lld
    return env


def _build_root_image(blade_bin, pkg_dir, arch, clang, lld=None):
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
         cwd=pkg_dir, env=_blade_env(clang, lld))

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
    """Emit one region row: {base u64, len u64, kind u8, 7 reserved}.

    24 bytes, which keeps the next row 8-aligned without a `.balign` the
    kernel's overlay would have to know about.
    """
    _emit_word64(lines, arch, base_expr)
    _emit_word64(lines, arch, len_expr)
    lines.append(f"    .byte  {kind}")
    lines.append("    .byte  0, 0, 0, 0, 0, 0, 0")


def _region_table_section(arch, rows, flags):
    """The `.regions` section, header and rows, as assembler lines.

    The one place the header is written, for every board's stitcher: magic,
    version, row count and the boot flags byte (`boot_flags`). `rows` are
    (comment, base-expression, length-expression, kind), as `_region_rows`
    makes them.
    """
    lines = [
        "    .section .regions, \"a\", @progbits",
        "    .balign 8",
        f"    .4byte {REGION_TABLE_MAGIC:#010x}",
        f"    .2byte {REGION_TABLE_VERSION}",
        f"    .byte  {len(rows)}",
        f"    .byte  {flags}",
    ]
    for i, (what, base_expr, len_expr, kind) in enumerate(rows):
        lines.append(f"    /* row {i}: {what} */")
        _emit_row(lines, arch, base_expr, len_expr, kind)
    return lines


def _region_row_specs(case, arch):
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

    Each row is a dict in the image description's row shape
    (`tools/sosimage/src/image.saw`): a `what` comment, a `kind` (`ram`,
    `device` or `pool`), and either the `blob` index of the child whose image
    it covers or a numeric `base` and `len`. A blob row's base and length are
    the linker's: that is what makes it work at all, since the address is
    whatever `ld.lld` decides.

    The kind column types each row by its role (spec §12). A blob and a
    destination are allocations (`Ram`), because `process_create` takes
    allocations and a launcher cannot allocate a blob out of a pool without
    its length, which is a linker expression here. The pool row is a pool. A
    case that allocates out of a child's blob names that child in
    `pool_blobs`, and its blob row is typed as a pool instead. A case that
    allocates a child's destination, and memory beside it, out of the
    child's own region names that child in `pool_destinations`, and its
    destination row is typed as a pool: that is how a case with two children
    has pools, since the RAM pool row would name the second child's region.
    """
    children = range(_child_count(case))
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
    pool_blobs = case.get("pool_blobs", ())
    for i in pool_blobs:
        if i not in children:
            raise ToolError(f"case {case['name']!r}: `pool_blobs` names child "
                            f"{i}, which the case does not have")
    rows = []
    for i, _pkg in enumerate(children):
        if i in pool_blobs:
            rows.append({"what": f"child {i}'s image blob, as a POOL "
                                 "(linker-resolved)",
                         "kind": "pool", "blob": i})
        else:
            rows.append({"what": f"child {i}'s image blob (linker-resolved)",
                         "kind": "ram", "blob": i})
    pool_destinations = case.get("pool_destinations", ())
    for i in pool_destinations:
        if i not in children:
            raise ToolError(f"case {case['name']!r}: `pool_destinations` names "
                            f"child {i}, which the case does not have")
    region_len = _child_region_len(arch)
    for i, _pkg in enumerate(children):
        base = arch["child_region_base"] + i * region_len
        if i in pool_destinations:
            rows.append({"what": f"child {i}'s destination RAM, as a POOL",
                         "kind": "pool", "base": base, "len": region_len})
        else:
            rows.append({"what": f"child {i}'s destination RAM",
                         "kind": "ram", "base": base, "len": region_len})
    if case.get("pool"):
        # `pool_skew` moves the pool row's base that many bytes up, off the
        # grain, and shortens it to keep its top. `pool_short_top` shortens
        # it by that many bytes at its top, so the top is off the grain too.
        # `pool_bytes` makes the row exactly that many bytes long, from where
        # `pool_skew` puts its base. They write the only rows the build puts
        # off the grain, for the cases that show the kernel trims a pool to
        # whole grains.
        skew = case.get("pool_skew", 0)
        short_top = case.get("pool_short_top", 0)
        if "pool_bytes" in case:
            short_top = arch["pool_len"] - skew - case["pool_bytes"]
        # `off_grain_destinations` gives the pool window's top half to two
        # allocations off the grain, after the pool row, so the pool is the
        # bottom half.
        if case.get("off_grain_destinations"):
            short_top += OFF_GRAIN_SPAN
        rows.append({"what": "the RAM POOL — free memory above every child "
                             "region",
                     "kind": "pool", "base": arch["pool_base"] + skew,
                     "len": arch["pool_len"] - skew - short_top})
        if case.get("off_grain_destinations"):
            # A row whose base is on the grain and whose length is three
            # bytes short of whole grains (a byte past the last whole
            # four-byte grain, and off a page's), then one whose base is a
            # byte past the grain and whose length is whole grains
            # (`tests/dest-grain`).
            base = arch["pool_base"] + arch["pool_len"] - OFF_GRAIN_SPAN
            rows.append({"what": "an allocation whose length is off the grain",
                         "kind": "ram", "base": base,
                         "len": OFF_GRAIN_SPAN // 2 - 3})
            rows.append({"what": "an allocation whose base is off the grain",
                         "kind": "ram",
                         "base": base + OFF_GRAIN_SPAN // 2 + 1,
                         "len": OFF_GRAIN_SPAN // 2 - 0x1000})
    if case.get("device"):
        rows.append({"what": "the DEVICE window — the console UART's register "
                             "page",
                     "kind": "device", "base": arch["device_base"],
                     "len": arch["device_len"]})
    return rows


# A row spec's kind as the Python path's table writer spells it.
_REGION_KIND_VALUES = {"ram": REGION_KIND_RAM, "device": REGION_KIND_DEVICE,
                       "pool": REGION_KIND_POOL}


def _region_rows(case, arch):
    """`_region_row_specs` as (comment, base-expression, length-expression,
    kind), for `_region_table_section`. A blob row's base and length are
    linker expressions over the child section's own symbols. A hand-assembled
    child is another object, and an assembler that cannot subtract two symbols
    it does not define (Xtensa's) needs its length as a symbol of its own,
    which the child's file defines."""
    rows = []
    for spec in _region_row_specs(case, arch):
        kind = _REGION_KIND_VALUES[spec["kind"]]
        if "blob" in spec:
            i = spec["blob"]
            length = (f"_sos_child{i}_len" if case.get("child_asm") else
                      f"_sos_child{i}_end - _sos_child{i}_start")
            rows.append((spec["what"], f"_sos_child{i}_start", length, kind))
        else:
            rows.append((spec["what"], f"{spec['base']:#x}",
                         f"{spec['len']:#x}", kind))
    return rows


def _stitch_regions(case, arch, clang):
    """Append this case's child images and emit the boot region table.

    The stitcher appends each child sosimg as it is (no flattening, no
    absolute placement, the blob lands wherever the linker puts it) and
    records offsets and lengths in a table. The kernel mints one object per row
    and interprets nothing; root's config says which ordinal is which.

    It emits rows for three roles: a case asks for `children`, a `pool`, a
    `device` window, or any combination, and `_region_rows` above is the order
    they land in. The kind column is what the kernel reads (a Ram row mints a
    Memory, a Pool row a Pool, a Device row an IoMemory) and it is the only
    thing in a row the kernel interprets at all.

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
            # A pool row's base is on the protection grain (spec §12), and its
            # length is padded to it, so the pool's longest run is the whole
            # blob: a page is a multiple of every profile's grain.
            pool_blob = i in case.get("pool_blobs", ())
            if pool_blob:
                lines.append("    .balign 4096")
            lines += [
                f"_sos_child{i}_start:",
                f"    .incbin \"{os.path.basename(staged)}\"",
            ]
            if pool_blob:
                lines.append("    .balign 4096")
            lines += [
                f"_sos_child{i}_end:",
                "    .balign 16",
            ]

    lines.append("")
    lines += _region_table_section(arch, _region_rows(case, arch),
                                   boot_flags(case))
    lines.append("")

    stub_s = os.path.join(dirs["build"], f"{name}.regions.S")
    with open(stub_s, "w") as f:
        f.write("\n".join(lines))
    stub_o = os.path.join(dirs["build"], f"{name}.regions.o")
    _run([clang, f"--target={arch['triple']}", *arch["cc_args"],
          "-nostdlib", "-I", dirs["build"], "-c", stub_s, "-o", stub_o])
    return stub_o


def _child_asm_obj(case, arch, clang):
    """Assemble a case's hand-written child image, from this architecture's
    test directory. It is its own `.childimg` and defines the symbols the
    region table's blob row names (`_child_count`)."""
    dirs = arch_dirs(arch)
    src = os.path.join(dirs["tests"], case["child_asm"])
    obj = os.path.join(dirs["build"], f"{case['name']}.child0.o")
    _run([clang, f"--target={arch['triple']}", *arch["cc_args"],
          "-nostdlib", "-c", src, "-o", obj])
    return obj


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
    if case.get("child_asm"):
        objs.append(_child_asm_obj(case, arch, clang))
    if case.get("root_pkg"):
        objs.append(_stitch_root_image(case["_root_image"][arch["name"]], arch,
                                       clang, name))
    # The child images and the boot region table that names them, the RAM pool
    # and the device window, and the boot flags in its header. A case that
    # needs none of them (it asks for no rows and wants the tick) links no
    # `.regions` section, so `_region_table_start` and `_region_table_end` come
    # out equal and the kernel sees zero regions and the default flags.
    if needs_region_table(case):
        objs.append(_stitch_regions(case, arch, clang))

    # The board's linker script, for `boot.S`'s reason: the memory map, the
    # payload section and the region table are the board's, and a protection
    # profile moves none of them.
    _run([lld, "-T", os.path.join(dirs["hal_board"], "virt.ld"), "--gc-sections",
          "-o", elf, *objs])
    if arch.get("kernel_fp_free"):
        _check_kernel_fp_free(elf, clang)
    return elf


# The kernel's own FP/SIMD instructions are exactly these two routines: the
# user-state save and load that run at a thread switch (`boot.S`). Anything else
# that touches a vector or floating-point register would clobber a user thread's
# state at a trap, where nothing saves it.
FP_ALLOWED_SYMBOLS = ("sos_fp_save", "sos_fp_load")

# A scalar, vector or SIMD register operand (`v0.16b`, `q3`, `d31`, `s0`, `h1`,
# `b2`), or the floating-point control registers. Registers are matched as whole
# tokens after the symbol annotation and comments are stripped, so a hex address
# or a symbol name cannot match.
_FP_REGISTER = re.compile(r"(?<![\w.])(?:[vqdshb](?:[12]?[0-9]|3[01])(?![\w])"
                          r"|fpcr|fpsr)", re.IGNORECASE)
_DISASM_SECTION = re.compile(r"^Disassembly of section (\S+):")
_DISASM_SYMBOL = re.compile(r"^[0-9a-f]+ <([^>]+)>:")
_DISASM_INSN = re.compile(r"^\s*[0-9a-f]+:\s*(\S+)\s*(.*)$")
# What objdump prints for a literal pool or other data in a text section: the
# raw bytes come first, then a directive. Not instructions, and its bytes
# (`b0`, `d1`) would read as register names.
_DISASM_DATA = re.compile(r"^\s*[0-9a-f]+:\s*(?:[0-9a-f]{2}\s+)*\.(?:word|long|quad|xword|hword|short|byte)\b")


def _fp_instructions(disassembly):
    """Yield (symbol, line) for each FP/SIMD instruction in kernel text.

    Only `.text*` sections are read: the root image and the child images ride
    in `.payload` and `.regions`, which are user code the kernel never runs.
    """
    section, symbol = None, None
    for line in disassembly.splitlines():
        m = _DISASM_SECTION.match(line)
        if m:
            section, symbol = m.group(1), None
            continue
        if section is None or not section.startswith(".text"):
            continue
        m = _DISASM_SYMBOL.match(line)
        if m:
            symbol = m.group(1)
            continue
        if _DISASM_DATA.match(line):
            continue
        m = _DISASM_INSN.match(line)
        if not m:
            continue
        operands = re.sub(r"<[^>]*>|//.*|#\S+", "", m.group(2))
        if _FP_REGISTER.search(operands):
            yield symbol, line.strip()


def _check_kernel_fp_free(elf, clang):
    """Fail the build if the kernel's text executes FP/SIMD outside the two
    user-state routines. The kernel is free of them so that a trap never
    disturbs a thread's vector registers; see `hal/arm64/kernel/boot.S`.
    """
    # Where `_find_clang` looks for an LLVM, in the same order: beside the
    # clang in use, on PATH, then the Homebrew prefixes (Apple's clang ships
    # without one).
    objdump = None
    for cand in (os.path.join(os.path.dirname(clang), "llvm-objdump"),
                 "llvm-objdump",
                 "/opt/homebrew/opt/llvm/bin/llvm-objdump",
                 "/usr/local/opt/llvm/bin/llvm-objdump"):
        objdump = shutil.which(cand) or (cand if os.path.exists(cand) else None)
        if objdump:
            break
    if not objdump:
        raise ToolError("the arm64 kernel's FP-free check needs llvm-objdump "
                        "(next to clang, on PATH, or in the Homebrew llvm)")
    result = subprocess.run([objdump, "-d", "--no-show-raw-insn", elf],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise ToolError(f"llvm-objdump failed on {elf}: {result.stderr}")
    bad = [(sym, line) for sym, line in _fp_instructions(result.stdout)
           if sym not in FP_ALLOWED_SYMBOLS]
    if bad:
        shown = "\n".join(f"  in {sym}: {line}" for sym, line in bad[:8])
        raise ToolError(f"{os.path.basename(elf)}: the kernel executes "
                        f"FP/SIMD instructions outside "
                        f"{', '.join(FP_ALLOWED_SYMBOLS)}:\n{shown}")


# =============================================================================
# The image builder
# =============================================================================
#
# `tools/sosimage/` is a hosted Saw program, built by Blade, that compiles a
# kernel, stitches root's image, the child images and the region table into
# it, and links it (its `src/main.saw` lists the commands). The runner keeps
# the package phase, the case table, QEMU and the judging; per profile it
# writes a profile description and per case an image description, both JSON,
# whose schemas `tools/sosimage/src/profile.saw` and `image.saw` state.
#
# `SOS_IMAGE_BUILDER=python` builds every image through the runner's own code
# instead (`_build_shared`, `_build_elf` and the S3 smoke's
# `case_objs`/`flash_image`), so the two can be compared image for image.

SOSIMAGE_DIR = os.path.join(REPO_ROOT, "tools", "sosimage")

# The link the builder's manifest reaches the sawlang checkout's packages
# through: `imgformat = { path = "../../.sawlang/libs/imgformat" }`.
SAWLANG_LINK = os.path.join(REPO_ROOT, ".sawlang")

# The schema version both descriptions carry; the builder reads only this.
SOSIMAGE_SCHEMA_VERSION = 1


def _python_builder():
    """Whether `SOS_IMAGE_BUILDER` asks for the runner's own image build.

    Unset or `sosimage` is the builder; `python` is the runner's path. Any
    other value is refused with exit 2 rather than read as one of them.
    """
    named = os.environ.get("SOS_IMAGE_BUILDER", "sosimage")
    if named not in ("sosimage", "python"):
        print(f"{RED}SOS_IMAGE_BUILDER must be `sosimage` or `python` "
              f"(got {named!r}){RESET}", file=sys.stderr)
        sys.exit(2)
    return named == "python"


def _link_sawlang():
    """Point `<sawos>/.sawlang` at the sawlang checkout the toolchain resolved.

    A link that already names it is left alone; one naming anything else is
    replaced, since the builder must compile against the checkout this run
    resolved and no other.
    """
    root = tc().root
    if not root:
        raise ToolError("the image builder depends on the sawlang checkout's "
                        "`imgformat` package, and the toolchain resolved no "
                        "checkout (name one with SAWLANG_ROOT)")
    if os.path.islink(SAWLANG_LINK):
        if os.readlink(SAWLANG_LINK) == root:
            return
        os.remove(SAWLANG_LINK)
    elif os.path.exists(SAWLANG_LINK):
        raise ToolError(f"{SAWLANG_LINK} exists and is not a link; it is the "
                        "runner's, to point at the sawlang checkout")
    os.symlink(root, SAWLANG_LINK)


def _build_sosimage(blade_bin):
    """`blade build --force` the image builder; return its binary's path.

    The previous binary is deleted first, so a build that fails cannot leave
    it to be run as if it were current.
    """
    binary = os.path.join(SOSIMAGE_DIR, ".build", "host", "sosimage")
    if os.path.exists(binary):
        os.remove(binary)
    env = dict(os.environ)
    env["SAWC"] = tc().sawc_env_value()
    _run([blade_bin, "build", "--force"], cwd=SOSIMAGE_DIR, env=env)
    if not os.path.exists(binary):
        raise ToolError(f"blade built the image builder and left no {binary}")
    return binary


def _test_sosimage(blade_bin):
    """`blade test` the image builder's own tests: its description readers'
    refusals, the region table's bytes and the in-place check, which need no
    emulator."""
    env = dict(os.environ)
    env["SAWC"] = tc().sawc_env_value()
    _run([blade_bin, "test"], cwd=SOSIMAGE_DIR, env=env)


def _run_sosimage(builder, args):
    """Run the image builder; return what it printed.

    A failure is the builder's own message, which says what was refused or
    which tool failed and how; the command line that ran the builder adds
    nothing to it.
    """
    proc = subprocess.run([builder, *args], capture_output=True, text=True)
    if proc.returncode != 0:
        out = ((proc.stdout or "") + (proc.stderr or "")).strip()
        raise ToolError(out or f"sosimage {args[0]} exited {proc.returncode} "
                               "and said nothing")
    return proc.stdout


def _write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(value, f, indent=1)
        f.write("\n")


def _profile_description(arch, clang, lld, native, linker_script,
                         module_paths, kernel_c_args=(), flash=None):
    """A profile description: every fact about the board an image needs."""
    profile = {
        "version": SOSIMAGE_SCHEMA_VERSION,
        "triple": arch["triple"],
        "word_bytes": 4 if arch["hex_width"] == 8 else 8,
        "sawc": tc().sawc(),
        "clang": clang,
        "lld": lld,
        "cc_args": list(arch["cc_args"]),
        "kernel_c_args": list(kernel_c_args),
        "features": arch.get("features") or "",
        "module_paths": list(module_paths),
        "native": [{"source": src, "object": obj} for src, obj in native],
        "linker_script": linker_script,
    }
    if flash is not None:
        profile["flash"] = flash
    return profile


def _gate_profile(arch, clang, lld):
    """The profile description of one of the gate's `ARCHES`, written into
    its build directory; returns its path.

    The native units are the HAL's: the board's `boot.S`, any assembly the
    architecture splits out of it (`hal_asm`), and the C every kernel links,
    `sink.c` from `hal_native` and the shared `support.c`.
    """
    dirs = arch_dirs(arch)
    build = dirs["build"]
    native = [(os.path.join(dirs["hal_board"], "boot.S"),
               os.path.join(build, "boot.o"))]
    for rel in arch["hal_asm"]:
        native.append((os.path.join(HAL_DIR, rel),
                       os.path.join(build,
                                    os.path.basename(rel).replace(".S", ".o"))))
    native.append((os.path.join(dirs["hal_native"], "sink.c"),
                   os.path.join(build, "sink.o")))
    native.append((os.path.join(RT_COMMON_C_DIR, "support.c"),
                   os.path.join(build, "support.o")))
    module_paths = [CORE_MODULE, f"hal={dirs['hal_kernel']}",
                    tc().module_path_arg("imgformat"), SOSRT_MODULE,
                    SOSABI_MODULE, *arch["hal_modules"]]
    path = os.path.join(build, "sosimage.profile.json")
    _write_json(path, _profile_description(
        arch, clang, lld, native,
        os.path.join(dirs["hal_board"], "virt.ld"), module_paths,
        kernel_c_args=arch.get("kernel_c_args", [])))
    return path


def _kernel_object(arch, src):
    """Where one kernel entry's object goes: a path of its own per profile and
    entry, since two compiles must never write one output."""
    rel = os.path.relpath(src, REPO_ROOT)
    stem = os.path.splitext(rel)[0].replace(os.sep, "-")
    return os.path.join(arch_dirs(arch)["build"], "kernels", f"{stem}.o")


def _build_kernels(builder, profile, arch, cases, jobs):
    """`sosimage kernel` once for each entry `cases` use; return each entry's
    failure, or None for one that compiled.

    The compiles run `jobs` at a time and print nothing: an entry that fails
    is reported by each case that boots it, with the builder's message.
    """
    entries = []
    for case in cases:
        if case["src"] not in entries:
            entries.append(case["src"])

    def compile_entry(src):
        out = _kernel_object(arch, src)
        os.makedirs(os.path.dirname(out), exist_ok=True)
        try:
            _run_sosimage(builder, ["kernel", profile, src, out])
            return None
        except ToolError as e:
            return e

    return dict(zip(entries, _map_ordered(jobs, entries, compile_entry)))


def _image_description(case, arch, kernel_obj, output, flash=None,
                       link_args=()):
    """The image description of one case on one profile."""
    dirs = arch_dirs(arch)
    name = case["name"]
    image = dict(version=SOSIMAGE_SCHEMA_VERSION, name=name, kernel=kernel_obj,
                 staging=os.path.join(dirs["build"], f"{name}.image"),
                 output=output)
    if case.get("asm"):
        image["asm"] = os.path.join(dirs["tests"], case["asm"])
    if case.get("child_asm"):
        image["child_asm"] = os.path.join(dirs["tests"], case["child_asm"])
    if case.get("root_pkg"):
        image["root_image"] = case["_root_image"][arch["name"]]
    if case.get("children"):
        image["children"] = list(case["_child_images"][arch["name"]])
    if needs_region_table(case):
        image["regions"] = {"flags": boot_flags(case),
                            "rows": _region_row_specs(case, arch)}
    if link_args:
        image["link_args"] = list(link_args)
    if flash is not None:
        image["flash"] = flash
    return image


def _link_image(builder, profile, case, arch, output, flash=None,
                link_args=()):
    """Write a case's image description into its staging directory and have
    the builder link it; return what the builder printed."""
    description = _image_description(
        case, arch, _kernel_object(arch, case["src"]), output, flash,
        link_args)
    path = os.path.join(description["staging"], "image.json")
    _write_json(path, description)
    return _run_sosimage(builder, ["link", profile, path])


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
# A case asks for it with `strip_kernel_lines`, and any case that does must
# not expect a line beginning `SOS: `.
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
    # A case may say how the console ends: the transcript, trailing whitespace
    # aside, must end with this text. It asserts that nothing was written after
    # the last line a case expects, which matching in order cannot.
    tail = case.get("expect_tail")
    if tail is not None:
        want = tail.format(**expectations(arch))
        if not hay.rstrip().endswith(want):
            return False, (f"the console does not end with {want!r} "
                           f"(got {out!r})")
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


def _run_arch(arch, qemu, lld, clang, blade_bin, builder, selected_cases,
              jobs):
    """Build and run every case for one architecture. Returns (passed, failed).

    `builder` is the image builder's binary, or None for the runner's own
    image build (`SOS_IMAGE_BUILDER=python`).

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
        if builder is None:
            shared_objs = _build_shared(arch, clang)
        else:
            profile = _gate_profile(arch, clang, lld)
            _run_sosimage(builder, ["native", profile])
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

    # Each kernel entry the cases boot is compiled once, here, before any case
    # links one.
    kernel_failures = {}
    if builder is not None:
        kernel_failures = _build_kernels(builder, profile, arch, cases, jobs)

    # One case = one unit of work: link it, boot it, judge it. A worker
    # returns its verdict and the lines it owns and prints nothing, so the
    # console is written by one thread walking `cases` in order and a parallel
    # run's report is the serial run's report.
    def run_case(case):
        name = case["name"]
        try:
            if builder is None:
                elf = _build_elf(case, arch, shared_objs, lld, clang)
            else:
                failure = kernel_failures[case["src"]]
                if failure is not None:
                    raise failure
                elf = os.path.join(dirs["build"], f"{name}.elf")
                _link_image(builder, profile, case, arch, elf)
                if arch.get("kernel_fp_free"):
                    _check_kernel_fp_free(elf, clang)
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


def _esp32s3_smoke(wanted, emit=None, emit_out=None):
    """The ESP32-S3 board smoke: Xtensa, call0, the `Flat` tier. Returns the
    process exit status. `wanted` is the `--case` names (underscored), or
    empty for every case. `emit`, a case name, builds that case's board image
    into `emit_out` instead of running anything.

    It is not part of `make sos-test`: `make sos-smoke-esp32s3` runs it
    against the Espressif QEMU, beside the C3's smoke and on the same terms.
    What it shares with the gate: `TEST_CASES` (the process cases are the
    gate's own, by name), the image builder and the descriptions it reads,
    the `boot_flags`/`needs_region_table` predicates, `_build_blade` and
    `_check`. What it does not: its build directory, `.build/esp32s3/sos`,
    and its run path, a flash image booted through the ROM under Espressif's
    QEMU, one case at a time. The builder writes the flash image and holds
    every resident image in it to its slot; `--emit-flash`'s last step,
    trimming the board image to its length and printing its checksum and the
    `esptool` line, stays here.

    Two kinds of case, one list:
      * the kernel on its own: boot to the banner, a fault taken in kernel
        mode, and harness kernels that enter user mode on a hand-assembled
        payload from `tests/xtensa/` (a syscall round trip; the tick taken from
        user mode, pending in the kernel, and polled at a preemption point; a
        device line through the interrupt matrix), and one kernel driving the
        HAL's CPU interrupt table directly;
      * real processes: the gate's own cases, named in `process_names` below,
        whose root and child packages Blade builds for this board's triple and
        the image builder stitches in as it does on the gate. Their kernel
        source, packages and assertions are the gate's, formatted for this
        board by `expectations(ESP32S3_ARCH)` and judged by `_check`. Two
        more are this board's own, on the shipping kernel the same way.

    The verdict is read twice. The kernel ends the emulator through
    semihosting with its status (`hal.exit_pass`/`exit_fail`, `kernel_fault`),
    so the exit status is asserted; and it prints a verdict line first, so the
    transcript is asserted in order as well. A run that never reaches the
    door is killed at the deadline and fails.

    Every board fact below was derived from the running emulator and is
    recorded with its probe in hal/xtensa-esp32s3/ABI.md.
    """
    import glob
    import hashlib
    import select
    import struct

    arch = ESP32S3_ARCH
    dirs = arch_dirs(arch)
    s3_hal = os.path.join(HAL_DIR, "xtensa-esp32s3")
    s3_build = dirs["build"]
    s3_tests = dirs["tests"]
    s3_triple = arch["triple"]
    flash_bytes = 4 * 1024 * 1024
    flash_ibus = 0x42000000
    flash_dbus = 0x3C000000
    # How much of the address space each window maps.
    flash_window = 32 * 1024 * 1024
    magic_offset = 0x400
    magic = 0xAEDB041D
    timeout_s = 20

    def s3_tool(pattern, what, hint):
        """Find a machine-local Espressif tool, newest version first."""
        hits = sorted(glob.glob(os.path.expanduser(pattern)), reverse=True)
        if not hits:
            print(f"{RED}no {what} found at {pattern}{RESET}", file=sys.stderr)
            print(f"  {hint}", file=sys.stderr)
            sys.exit(2)
        return hits[0]

    qemu = s3_tool(
        "~/.espressif/tools/qemu-xtensa/*/qemu/bin/qemu-system-xtensa",
        "Espressif QEMU for Xtensa",
        "install it with the ESP-IDF tools installer; the `qemu-system-xtensa`"
        " a package manager installs has no esp32s3 machine")
    clang = s3_tool(
        "~/.espressif/tools/esp-clang/*/esp-clang/bin/clang",
        "esp-clang",
        "the S3 build needs Espressif's clang and the ld.lld beside it, which "
        "can link Xtensa; see hal/xtensa-esp32s3/ABI.md §1")
    lld = os.path.join(os.path.dirname(clang), "ld.lld")
    if not os.access(lld, os.X_OK):
        print(f"{RED}no ld.lld beside {clang}{RESET}", file=sys.stderr)
        print("  the S3 build links with the ld.lld esp-clang ships, which "
              "can link Xtensa; see hal/xtensa-esp32s3/ABI.md §1",
              file=sys.stderr)
        sys.exit(2)

    os.makedirs(s3_build, exist_ok=True)

    def cc(src, obj, extra=()):
        _run([clang, f"--target={s3_triple}", *arch["cc_args"], "-nostdlib",
              *extra, "-c", src, "-o", obj])
        return obj

    def kernel_obj(name, src):
        obj = os.path.join(s3_build, f"{name}.kernel.o")
        _run(tc().sawc() + [src, "-o", obj, "-Oz",
                            "--freestanding", "--no-hidden-alloc",
                            "--runtime-provider",
                            "--target", s3_triple,
                            "--module-path", CORE_MODULE,
                            "--module-path",
                            f"hal={os.path.join(s3_hal, 'kernel')}",
                            "--module-path", tc().module_path_arg("imgformat"),
                            "--module-path", SOSRT_MODULE,
                            "--module-path", SOSABI_MODULE])
        return obj

    def elf_symbols(data):
        """Every named symbol in a linked ELF32's symbol table, by value: the
        kernel's linker symbols that bound the images it carries."""
        shoff, = struct.unpack_from("<I", data, 32)
        shentsize, shnum = struct.unpack_from("<HH", data, 46)
        symbols = {}
        for i in range(shnum):
            sh = struct.unpack_from("<10I", data, shoff + shentsize * i)
            if sh[1] != 2:                      # SHT_SYMTAB
                continue
            strtab = struct.unpack_from("<10I", data,
                                        shoff + shentsize * sh[6])[4]
            for at in range(sh[4], sh[4] + sh[5], sh[9]):
                st_name, st_value = struct.unpack_from("<II", data, at)
                start = strtab + st_name
                symbols[data[start:data.index(b"\0", start)].decode()] = \
                    st_value
        return symbols

    def check_in_place(name, data, image):
        """The build's own check that every resident image is linked where
        its bytes sit (spec §5.6): read each image back out of the flash image
        by the kernel's symbols bounding it, and require each executable
        segment linked at the instruction-window address of its own flash
        bytes, each other segment linked in flash at their data-window address
        and not writable, and nothing else linked in flash, a segment with no
        bytes in the image included. A segment linked outside both windows is
        copied into RAM, and the loader judges it. An image the kernel would
        not read through the data window is refused too.
        `user/root.ld` says how the scripts make it so; a mismatch names both
        numbers, since the loader would refuse the image at boot."""
        symbols = elf_symbols(data)
        images = []
        if symbols["_payload_data_end"] > symbols["_payload_data_start"]:
            images.append(("root's image", symbols["_payload_data_start"],
                           symbols["_payload_data_end"]))
        i = 0
        while f"_sos_child{i}_start" in symbols:
            images.append((f"child {i}'s image",
                           symbols[f"_sos_child{i}_start"],
                           symbols[f"_sos_child{i}_end"]))
            i += 1
        window = flash_window
        for what, start, end in images:
            if not flash_dbus <= start <= end <= flash_dbus + flash_bytes:
                raise ToolError(
                    f"{name}: {what} is held at {start:#x}..{end:#x}, outside "
                    "the flash data window, where the kernel reads every "
                    "image")
            at = start - flash_dbus
            blob = bytes(image[at:at + (end - start)])
            if len(blob) < 24:
                raise ToolError(f"{name}: {what} at flash {at:#x} is "
                                f"{len(blob)} bytes, shorter than a sosimg "
                                "header")
            magic, _version, count = struct.unpack_from("<IHB", blob, 0)
            if magic != 0x49534F53:             # 'S','O','S','I'
                raise ToolError(f"{name}: {what} at flash {at:#x} is not a "
                                "sosimg")
            hint = ("" if count == 3 else
                    f"; it has {count} segments, and the board's user "
                    "linker scripts link an image for three")
            if len(blob) < 24 + 24 * count:
                raise ToolError(f"{name}: {what}'s segment table runs past "
                                "its end")
            for k in range(count):
                load, off, flen, mlen, flags = struct.unpack_from(
                    "<QIIIB", blob, 24 + 24 * k)
                in_flash = (flash_ibus <= load < flash_ibus + window
                            or flash_dbus <= load < flash_dbus + window)
                if flags & 8:                   # SegFlag.Device: no bytes
                    continue
                if flen == 0 and in_flash:
                    raise ToolError(
                        f"{name}: {what}'s segment {k} has no bytes in the "
                        f"image and is linked in flash, at {load:#x}: its "
                        f"{mlen} bytes are zero-filled at load, which flash "
                        "cannot take, so it must be linked in DRAM")
                if not (flags & 4 or in_flash):
                    continue
                bytes_at = at + off
                if flags & 4:                   # SegFlag.Execute
                    want, where = flash_ibus + bytes_at, "instruction"
                else:
                    want, where = flash_dbus + bytes_at, "data"
                if load != want:
                    raise ToolError(
                        f"{name}: {what}'s segment {k} is linked at "
                        f"{load:#x}, but its bytes sit at flash "
                        f"{bytes_at:#x}, whose {where}-window address is "
                        f"{want:#x}{hint}")
                if flags & 2:                   # SegFlag.Write
                    raise ToolError(f"{name}: {what}'s segment {k} is "
                                    f"writable and linked in flash, at "
                                    f"{load:#x}")

    def flash_image(name, objs, board=False, check=True):
        """Link, then write each loadable segment into a 4 MiB flash image at
        its load address's flash offset (ABI.md §1e), and check the
        direct-boot magic is really there and byte 0 is not an esptool
        header's. Returns the image, the bytes placed, and the offset one past
        the last of them, which is the length a board needs written.

        Every resident image is then held to its slot (`check_in_place`),
        unless `check` is off: a case whose image is a fixture the loader must
        refuse turns it off, and nothing else does.

        A board image is linked with no `simcall` in its stops (`board`), and
        is never booted here: QEMU's runs are the emulator's image."""
        tag = "board" if board else "flash"
        elf = os.path.join(s3_build, f"{name}.{tag}.elf")
        flash = os.path.join(s3_build, f"{name}.{tag}.bin")
        board_args = ["--defsym=_sos_semihosting=0"] if board else []
        _run([lld, "-T", os.path.join(s3_hal, "kernel", "esp32s3.ld"),
              "--gc-sections", *board_args, "-o", elf, *objs])
        with open(elf, "rb") as f:
            data = f.read()
        phoff, = struct.unpack_from("<I", data, 28)
        phnum, = struct.unpack_from("<H", data, 44)
        image = bytearray(b"\xff" * flash_bytes)
        used = 0
        end = 0
        for i in range(phnum):
            p_type, offset, _vaddr, paddr, filesz = struct.unpack_from(
                "<5I", data, phoff + 32 * i)
            if p_type != 1 or filesz == 0:      # PT_LOAD with bytes to place
                continue
            if not flash_ibus <= paddr <= flash_ibus + flash_bytes - filesz:
                raise ToolError(f"{name}: a segment loads at {paddr:#x}, "
                                "outside the flash window")
            at = paddr - flash_ibus
            image[at:at + filesz] = data[offset:offset + filesz]
            used += filesz
            end = max(end, at + filesz)
        # Without these two words the mask ROM never enters the image and the
        # run is a silent timeout; checking here turns that into a sentence.
        m0, m1 = struct.unpack_from("<II", image, magic_offset)
        if m0 != magic or m1 != magic:
            raise ToolError(
                f"{name}: flash image lacks the direct-boot magic (read "
                f"{m0:#010x} {m1:#010x}, want {magic:#010x} twice) — check "
                ".magic in hal/xtensa-esp32s3/kernel/esp32s3.ld")
        # 0xE9 at byte 0 is an esptool image header: the ROM would take the
        # image for one and never reach the direct-boot check (ABI.md §2a).
        if image[0] == 0xE9:
            raise ToolError(f"{name}: flash byte 0 is 0xE9, an esptool "
                            "image header; the ROM would not direct-boot it")
        if check:
            check_in_place(name, data, image)
        with open(flash, "wb") as f:
            f.write(image)
        return flash, used, end

    def boot(flash):
        """Boot the image; collect the console until the emulator exits.

        Returns the transcript and the exit status, or None for a run killed
        at the deadline. Stderr is discarded: QEMU writes `Adding SPI flash
        device` there on every run, which is noise a diff should never see.
        """
        proc = subprocess.Popen(
            [qemu, "-machine", "esp32s3", "-nographic", "-semihosting",
             "-drive", f"file={flash},if=mtd,format=raw"],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL)
        out = b""
        deadline = time.time() + timeout_s
        status = None
        try:
            while time.time() < deadline:
                ready, _, _ = select.select([proc.stdout], [], [], 0.2)
                if ready:
                    chunk = os.read(proc.stdout.fileno(), 4096)
                    if chunk:
                        out += chunk
                        continue
                    # End of output: the emulator is exiting, so wait for its
                    # status, but never past the deadline.
                    try:
                        status = proc.wait(
                            timeout=max(0.0, deadline - time.time()))
                    except subprocess.TimeoutExpired:
                        pass
                    break
                if proc.poll() is not None:
                    # Exited: whatever it wrote is already in the pipe.
                    while True:
                        chunk = os.read(proc.stdout.fileno(), 4096)
                        if not chunk:
                            break
                        out += chunk
                    status = proc.returncode
                    break
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait()
            proc.stdout.close()
        return out.decode("utf-8", "replace"), status

    rom = "ESP-ROM:esp32s3-20210327"
    banner = "SOS M1: kernel up on xtensa (ESP32-S3)"
    halt_pass = "SOS-S3: halt pass"
    # The kernel-only cases, in the gate's case shape so `_check` judges them
    # too. The exit status is the kernel's own semihosting exit, so a failing
    # stop is non-zero and carries its code.
    kernel_cases = [
        {
            # No image appended: the kernel must say so and fail. Direct boot,
            # the split flash layout, the vectors and the console in one.
            "name": "boot",
            "src": os.path.join(KERNEL_DIR, "main.saw"),
            "expect_out": [rom, banner,
                           "SOS: bad root image: no root image appended",
                           "SOS-S3: halt fail code=0x00000004"],
            "expect_clean_exit": False,
            "expect_status": 4,
        },
        {
            # A load the kernel cannot make arrives at the kernel vector
            # (0x300) as LoadProhibited (0x1C) on the probe address, and stops
            # the machine with 0x80 plus that vector's slot.
            "name": "trap_fault",
            "src": os.path.join(TESTS_DIR, "trap.saw"),
            "expect_out": [rom,
                           "SOS-S3: kernel fault vector=0x00000300 "
                           "exccause=0x0000001c",
                           "excvaddr=0x10000000"],
            "expect_clean_exit": False,
            "expect_status": 0x80 | (0x300 >> 6),
        },
        {
            # "SOS-U" is written one character at a time through
            # `debug_print`, so seeing it at all proves the syscall round trip
            # resumed past each `syscall`; `shutdown(0)` is the last one.
            "name": "umode_syscall",
            "src": os.path.join(TESTS_DIR, "umode.saw"),
            "asm": "payload_ok.S",
            "expect_out": [rom, "SOS M1: entering U-mode", "SOS-U",
                           halt_pass],
            "expect_clean_exit": True,
        },
        {
            # Ticks land in the arch-free hook after the kernel entered user
            # mode for good, so each was taken from there, through the matrix
            # and the user vector.
            "name": "timer_tick",
            "src": os.path.join(TESTS_DIR, "timer.saw"),
            "asm": "payload_spin.S",
            "expect_out": [rom, "SOS M2: timer armed",
                           "SOS M2: entering U-mode",
                           "SOS: timer tick 0x00000001 at ",
                           "SOS: timer tick 0x00000002 at ",
                           halt_pass],
            "expect_clean_exit": True,
        },
        {
            # The tick came due while the kernel spun and was not taken there.
            "name": "timer_masked_in_kernel",
            "src": os.path.join(TESTS_DIR, "timer_mask.saw"),
            "asm": "payload_spin.S",
            "expect_out": [rom, "SOS M2: kernel section begin",
                           "SOS M2: kernel section end, timer expired, "
                           "ticks taken=0x00000000",
                           "SOS M2: entering U-mode",
                           "SOS: timer tick 0x00000001 at ",
                           halt_pass],
            "expect_clean_exit": True,
        },
        {
            # The same tick, delivered at a preemption point through
            # `irq_poll`: `at 0x00000001` is the sentinel such a tick reports.
            # How many ticks the section took accrues with host time (a
            # loaded host lets a second period pass between two points), so
            # the count is printed and not asserted; the section ends only
            # once the first has been taken.
            "name": "preempt_tick",
            "src": os.path.join(TESTS_DIR, "preempt_tick.saw"),
            "asm": "payload_spin.S",
            "expect_out": [rom, "SOS M3: kernel section begin",
                           "SOS: timer tick 0x00000001 at 0x00000001",
                           "SOS M3: kernel section end, ticks taken=",
                           "SOS M3: entering U-mode",
                           halt_pass],
            "expect_clean_exit": True,
        },
        {
            # A device line (UART0, source 27) raised in the kernel and taken
            # from user mode; the number crossing from the raise to the hook
            # says the claim found the source that was raised. The clean stop
            # after it says the masked line did not fire again.
            "name": "external_irq",
            "src": os.path.join(TESTS_DIR, "extirq.saw"),
            "asm": "payload_spin.S",
            "expect_out": [rom, "SOS M2: raised external irq 0x0000001b",
                           "SOS M2: entering U-mode",
                           "SOS: external irq 0x0000001b",
                           halt_pass],
            "expect_clean_exit": True,
        },
        {
            # CPU interrupts handed between lines by the HAL itself: a masked
            # line keeps its own, an unmask with none unassigned takes one
            # back from a masked line and not a pending one, and a line moved
            # off a CPU interrupt no longer reaches it. The file's header has
            # each step.
            "name": "irq_takeback",
            "src": os.path.join(s3_tests, "irq_takeback.saw"),
            "expect_out": [rom,
                           "SOS irqtb: 27 raised, poll=0x0000001b",
                           "SOS irqtb: 27 masked while asserting, "
                           "poll=0x00000000",
                           "SOS irqtb: lines 1-8 unmasked and masked, "
                           "poll=0x00000000",
                           "SOS irqtb: line 9 unmasked, poll=0x00000000",
                           "SOS irqtb: 27 unmasked, poll=0x0000001b",
                           "SOS irqtb: line 10 unmasked, poll=0x00000000",
                           "SOS irqtb: 27 raised, line 10 unmasked again, "
                           "poll=0x00000000",
                           "SOS irqtb: 27 unmasked, poll=0x0000001b",
                           halt_pass],
            "expect_clean_exit": True,
        },
        {
            # The accesses a board may refuse, one at a time, each named
            # before it is made, the probe carrying on past a refusal
            # (hal/xtensa-esp32s3/README.md's bring-up has it as a stage,
            # with the text a part printed: it refused six of these accesses
            # and answered `shared`, and ABI.md §3c sets its answers beside
            # these). The emulator allows every one, so the case asserts the
            # whole transcript: each value read back, `not shared` from both
            # alias steps (the emulator keeps IRAM and DRAM as two arrays),
            # the copied routine's answer in IRAM and at its DRAM name, the
            # region table's magic through the data window, and the
            # user-mode program's line and clean stop. The flash addresses
            # move with the code, so the four flash step lines are asserted
            # up to the window's top byte.
            "name": "bus_probe",
            "src": os.path.join(TESTS_DIR, "bus_probe.saw"),
            "asm": "bus_probe.S",
            "expect_out": [
                rom,
                "SOS probe: bus probe on xtensa (ESP32-S3)",
                "SOS probe: word store and load, process IRAM at 0x403a0000",
                "SOS probe: ok 0x5a5aa5a5",
                "SOS probe: word store and load, process IRAM at 0x403dfffc",
                "SOS probe: ok 0x5a5aa5a5",
                "SOS probe: alias, stored at 0x403a0000 then 0x3fcb0000, "
                "read at 0x403a0000",
                "SOS probe: ok 0x0f1e2d3c",
                "SOS probe: not shared",
                "SOS probe: alias, stored at 0x3fcefffc then 0x403dfffc, "
                "read at 0x3fcefffc",
                "SOS probe: ok 0x0f1e2d3c",
                "SOS probe: not shared",
                "SOS probe: word store and load, DRAM at 0x3fcb0000",
                "SOS probe: ok 0x5a5aa5a5",
                "SOS probe: halfword store and load, DRAM at 0x3fcb0002",
                "SOS probe: ok 0x0000c33c",
                "SOS probe: byte store and load, DRAM at 0x3fcb0003",
                "SOS probe: ok 0x00000096",
                "SOS probe: word store and load, DRAM at 0x3fcefffc",
                "SOS probe: ok 0x5a5aa5a5",
                "SOS probe: halfword store and load, DRAM at 0x3fcefffe",
                "SOS probe: ok 0x0000c33c",
                "SOS probe: byte store and load, DRAM at 0x3fceffff",
                "SOS probe: ok 0x00000096",
                "SOS probe: word store and load, DRAM at 0x3fcb0004",
                "SOS probe: ok 0x5a5aa5a5",
                # The emulator makes the load, unaligned, across the two
                # words: the step 3 word's bytes 1..3, then byte 0 of this
                # one.
                "SOS probe: unaligned word load, DRAM at 0x3fcb0001",
                "SOS probe: ok 0xa5963ca5",
                "SOS probe: ATOMCTL 0x00000015",
                "SOS probe: word store, then s32c1i swapping it, DRAM at "
                "0x3fcb0010",
                "SOS probe: ok 0x00000005",
                "SOS probe: word load after s32c1i, DRAM at 0x3fcb0010",
                "SOS probe: ok 0x00000007",
                "SOS probe: word store and load, process IRAM at 0x403a0020",
                "SOS probe: ok 0x5a5aa5a5",
                "SOS probe: byte load, process IRAM at 0x403a0021",
                "SOS probe: ok 0x000000a5",
                "SOS probe: halfword load, process IRAM at 0x403a0022",
                "SOS probe: ok 0x00005a5a",
                "SOS probe: byte store, word loaded back, process IRAM at "
                "0x403a0021",
                "SOS probe: ok 0x5a5a96a5",
                "SOS probe: halfword store, word loaded back, process IRAM at "
                "0x403a0022",
                "SOS probe: ok 0xc33c96a5",
                "SOS probe: payload copied by words, process IRAM at "
                "0x403a0100",
                "SOS probe: ok 0x802ac222",
                "SOS probe: call, process IRAM at 0x403a0100",
                "SOS probe: ok 0x0000102a",
                "SOS probe: payload copied by words, DRAM at 0x3fcb0100",
                "SOS probe: ok 0x802ac222",
                "SOS probe: call, DRAM at 0x3fcb0100",
                "SOS probe: ok 0x0000102a",
                "SOS probe: word load, instruction window at 0x42",
                "SOS probe: ok 0x802ac222",
                "SOS probe: byte load, instruction window at 0x42",
                "SOS probe: ok 0x000000c2",
                "SOS probe: word load, data window at 0x3c",
                f"SOS probe: ok {REGION_TABLE_MAGIC:#010x}",
                "SOS probe: byte load, data window at 0x3c",
                f"SOS probe: ok {(REGION_TABLE_MAGIC >> 8) & 0xFF:#010x}",
                "SOS probe: user literal, word store and load, process IRAM "
                "at 0x403a0108",
                "SOS probe: ok 0x3fcb0400",
                "SOS probe: entering user mode at 0x403a010c, byte access at "
                "0x3fcb0400",
                "SOS probe: every line after the console handover is from "
                "user mode",
                "SOS: console handover",
                "SOS-U K",
                "SOS probe: done",
                halt_pass],
            "expect_clean_exit": True,
        },
    ]
    # The loader's half of this board's bus rule and of the in-place rule,
    # on hand-assembled root images in root's slot (`tests/xtensa/`), each
    # refused at boot before anything runs. The build's own check would
    # refuse every one but `grant_past_image` first, so they are built past it
    # (`loader_fixture`): each is the image a wrong build makes, and the
    # loader is what stands behind the build. Each file's header says what
    # would happen were it accepted.
    def refused(name, asm, reason):
        return {
            "name": name,
            "src": os.path.join(KERNEL_DIR, "main.saw"),
            "asm": asm,
            "loader_fixture": True,
            "expect_out": [rom, banner, f"SOS: bad root image: {reason}",
                           "SOS-S3: halt fail code=0x00000004"],
            "expect_clean_exit": False,
            "expect_status": 4,
        }
    kernel_cases += [
        # Code linked in DRAM, to be copied: the part fetches nothing there.
        refused("code_in_dram", "payload_code_in_dram.S",
                "segment is linked where this board cannot serve it"),
        # Code linked in the instruction window, but not at its own bytes.
        refused("code_not_in_place", "payload_code_not_in_place.S",
                "segment is linked where this board cannot serve it"),
        # A writable segment linked in flash, to be copied there.
        refused("writable_in_flash", "payload_writable_in_flash.S",
                "segment is linked where this board cannot serve it"),
        # Code in place, whose grant rounded to the grain would run two bytes
        # past the image's end.
        refused("grant_past_image", "payload_grant_past_image.S",
                "segment used in place is granted past the image"),
        # Read-only data in place at the instruction-window name of its bytes:
        # a process's byte loads there, which the emulator serves and a part
        # refuses.
        refused("rodata_in_ibus", "payload_rodata_in_ibus.S",
                "segment is linked where this board cannot serve it"),
        # Code in place at the data-window name of its bytes, where the part
        # fetches nothing.
        refused("code_in_dbus", "payload_code_in_dbus.S",
                "segment is linked where this board cannot serve it"),
    ]

    # The process cases: the gate's, by name, each chosen for one thing the
    # board has to get right that no other case here shows.
    #
    #   root_server_boot   Blade's image for this triple, loaded at
    #                      `ROOT_LOAD_BASE`, entered, and its boot set drained
    #   thread_basics      threads of one process, switched as frames
    #   thread_preempt     the same under a tick harness kernel: preemption
    #   timer_oneshot      a Timer fire through a Waiter, with the kernel idle
    #                      on `irq_poll` in between
    #   tier_word          the platform says `Flat`
    #   process_lifecycle  a child: its image blob and destination rows, its
    #                      own link address, created, started, reaped
    #   death_fault        a child the kernel ends, observed by root as a wake
    #   pipe_pingpong      a pipe call and its reply across two processes
    #   map_into_child     the pool row, and `map` placing at the address
    #   iomemory_carve     the device row and this board's window rule
    #   interrupt_give     matrix sources bound as Interrupts, given to a child
    #                      and returned to the free set by its teardown
    #   tick_spinner       the shipping kernel's own tick preempting a child
    #                      that never traps, so root's Timer wake can run
    #   copy_from_code_refused  a buffer in a child's code, in the flash
    #                      instruction window, refused without the kernel
    #                      touching it; a string literal in place in flash
    #                      taken as a pipe body through the data window
    #   thread_entry_not_exec   a thread's entry judged by the executable
    #                      rows, with the code in flash outside the region
    #   image_held         a child running out of its image in flash, whose
    #                      launcher released the image's handle
    #   data_init          initialized writable data copied out of flash into
    #                      DRAM with its bytes
    #   kill_churn         kills over many rounds, on a child in each of four
    #                      states, with the board's tick on: nothing a
    #                      teardown owes goes unreturned on this part
    #   kill_runnable      a kill of a child the tick took the processor from
    #
    # Every process in them runs its code from the flash instruction window
    # and reads its read-only data through the data window, in place in its
    # image's slot; only its writable data, arena and stack are in DRAM.
    #
    # Each image's boot flags are the gate's (`boot_flags`): "no tick" unless
    # the case says `"tick": True`, which only `tick_spinner` does.
    #
    # A case asserting a hardware denial does not belong on a Flat board; the
    # check below refuses one named here by mistake, with the gate's reason.
    process_names = ["root_server_boot", "thread_basics", "thread_preempt",
                     "timer_oneshot", "tier_word", "process_lifecycle",
                     "death_fault", "pipe_pingpong", "map_into_child",
                     "iomemory_carve", "interrupt_give", "tick_spinner",
                     "copy_from_code_refused", "thread_entry_not_exec",
                     "image_held", "data_init", "pool_ops",
                     "kill_churn", "kill_runnable"]
    by_name = {c["name"]: c for c in TEST_CASES}
    process_cases = [by_name[n] for n in process_names]
    # This board's own process cases, for what only it has: a table of CPU
    # interrupts that lines are given and give back, and the trap frame of
    # its own entry path.
    process_cases += [
        {
            # More distinct lines bound and released over the process's life
            # than the board has CPU interrupts for, one held at a time; then
            # the slab's limit, `MAX_INTERRUPTS`, is the one the process
            # meets, as `NoResource`.
            "name": "irq_line_cycle",
            "src": os.path.join(KERNEL_DIR, "main.saw"),
            "root_pkg": os.path.join(TESTS_DIR, "irq-cycle"),
            "expect_out": ["{banner}",
                           "SOS irqcycle: bound and released lines 1 to 12, "
                           "one at a time",
                           "SOS irqcycle: the next bind answered NoResource",
                           "SOS irqcycle: held 4 at once",
                           "SOS irqcycle: done"],
            "expect_clean_exit": True,
        },
        {
            # Two threads holding distinct values in every register the trap
            # frame carries, each round's handoff forced by the other's
            # progress, so only a tick moves the processor between them
            # (`thread_preempt` is the shape). A lost register is named, and
            # the joined codes are not 0.
            "name": "frame_hold",
            "src": os.path.join(KERNEL_DIR, "main.saw"),
            "tick": True,
            "root_pkg": os.path.join(TESTS_DIR, "frame-hold"),
            "expect_out": ["{banner}",
                           "SOS framehold: two threads holding registers, "
                           "no yields",
                           "SOS framehold: joined a=0 b=0"],
            "expect_clean_exit": True,
        },
        {
            # A region over flash is neither a destination nor a donation
            # (spec §5.6, §2.8). The blob's row is a pool. The image allocated
            # off its front is valid and copies nothing, so only the
            # destination rule refuses the rest of the blob, allocated as a
            # second allocation, as its destination; then donating the image
            # ends root with `BadArg`.
            "name": "flash_region",
            "src": os.path.join(KERNEL_DIR, "main.saw"),
            "root_pkg": FLASH_REGION_PKG,
            "child_asm": "child_inplace.S",
            "pool_blobs": [0],
            "expect_out": ["{banner}",
                           "SOS: boot regions={two}",
                           "SOS flashregion: a destination in flash is "
                           "refused: not a loadable process image",
                           "SOS: process fault: argument outside its domain "
                           "process={zero}"],
            "expect_clean_exit": False,
            "expect_status": EXIT_PROCESS_FAULT,
        },
        {
            # The gate's `image_rows_held` over a flash image: the image's
            # handle released before the child runs, the child running, the
            # bytes back after its death. The donation at its end meets the
            # region rule first here, since the image is flash.
            "name": "image_rows_held_flash",
            "src": os.path.join(KERNEL_DIR, "main.saw"),
            "root_pkg": IMAGE_ROWS_HELD_PKG,
            "child_asm": "child_inplace.S",
            "pool_blobs": [0],
            "expect_out": ["{banner}",
                           "SOS: boot regions={two}",
                           "SOS imagerows: launched from the image, its "
                           "handle released",
                           "SOS-H",
                           "SOS imagerows: the child exited from its image",
                           "SOS imagerows: after its death the image's bytes "
                           "came back",
                           "SOS: process fault: argument outside its domain "
                           "process={zero}"],
            "expect_clean_exit": False,
            "expect_status": EXIT_PROCESS_FAULT,
        },
        {
            # A root image linked for the child's slot, stitched into root's:
            # the build reads it back and refuses it, naming where the code
            # is linked and where its bytes sit. Every number in the line is
            # fixed by the two slots and the image's header length.
            "name": "slot_mismatch",
            "src": os.path.join(KERNEL_DIR, "main.saw"),
            "root_pkg": CHILD_FAULT_PKG,
            "expect_build_error": "root's image's segment 0 is linked at "
                                  "0x42180060, but its bytes sit at flash "
                                  "0x100060, whose instruction-window "
                                  "address is 0x42100060",
            "expect_out": [],
            "expect_clean_exit": False,
        },
    ]
    for case in process_cases:
        if case.get("tier", arch["tier"]) != arch["tier"]:
            print(f"{RED}{case['name']} is isolated-only: "
                  f"{case['tier_reason']}{RESET}", file=sys.stderr)
            return 1
    cases = kernel_cases + process_cases

    # `--case` narrows the run, as on the gate, and only the named cases'
    # packages are built. `--emit-flash` names exactly one.
    if emit is not None:
        wanted = [emit]
    if wanted:
        names = [c["name"] for c in cases]
        unknown = [w for w in wanted if w not in names]
        if unknown:
            print(f"{RED}unknown --case for --board esp32s3: "
                  f"{', '.join(unknown)}; known: {', '.join(names)}{RESET}",
                  file=sys.stderr)
            return 2
        cases = [c for c in cases if c["name"] in wanted]
        process_cases = [c for c in process_cases if c["name"] in wanted]

    print(f"{BOLD}SOS ESP32-S3 board smoke{RESET}")
    print(f"  qemu   {qemu}")
    print(f"  clang  {clang}")
    print(f"  target {s3_triple} (call0, no FPU; the Flat tier)")
    print(f"  sawlang: {tc().source}  {tc().root or tc().sawc_env_value()}")

    # Blade builds the packages and the image builder.
    python_path = _python_builder()
    builder = None
    try:
        blade_bin = _build_blade(s3_build)
        if not python_path:
            _link_sawlang()
            builder = _build_sosimage(blade_bin)
    except ToolError as e:
        print(f"{CROSS} failed to build blade or the image builder\n{e}",
              file=sys.stderr)
        return 1

    # The native half is one compile for every case. `boot.S` holds the
    # vectors and the trap entry; `support.c` is the shared C floor, whose
    # word atomics nothing references here (the compiler inlines `s32c1i`).
    native = [(os.path.join(s3_hal, "kernel", "boot.S"),
               os.path.join(s3_build, "boot.o")),
              (os.path.join(s3_hal, "kernel", "sink.c"),
               os.path.join(s3_build, "sink.o")),
              (os.path.join(RT_COMMON_C_DIR, "support.c"),
               os.path.join(s3_build, "support.o"))]
    profile = os.path.join(s3_build, "sosimage.profile.json")
    try:
        if python_path:
            shared = [cc(native[0][0], native[0][1]),
                      cc(native[1][0], native[1][1], extra=("-O2",)),
                      cc(native[2][0], native[2][1], extra=("-O2",))]
        else:
            _write_json(profile, _profile_description(
                arch, clang, lld, native,
                os.path.join(s3_hal, "kernel", "esp32s3.ld"),
                [CORE_MODULE, f"hal={os.path.join(s3_hal, 'kernel')}",
                 tc().module_path_arg("imgformat"), SOSRT_MODULE,
                 SOSABI_MODULE],
                flash={"bytes": flash_bytes, "instruction_window": flash_ibus,
                       "data_window": flash_dbus,
                       "window_bytes": flash_window,
                       "magic_offset": magic_offset, "magic": magic}))
            _run_sosimage(builder, ["native", profile])
    except ToolError as e:
        print(f"{CROSS} failed to build the native half\n{e}", file=sys.stderr)
        return 1

    # Every root and child package, once, through Blade, with this board's
    # compiler and linker. The images land under each package's
    # `.build/<triple>/`, so nothing collides with the gate's builds.
    pkgs = []
    for case in process_cases:
        for pkg in [case["root_pkg"], *case.get("children", ())]:
            if pkg not in pkgs:
                pkgs.append(pkg)
    images = {}
    try:
        for pkg in pkgs:
            image = _build_root_image(blade_bin, pkg, arch, clang, lld)
            images[pkg] = image
            print(f"  {os.path.relpath(image, REPO_ROOT)}  "
                  f"({os.path.getsize(image)} bytes)")
    except ToolError as e:
        print(f"{CROSS} failed to build an image\n{e}", file=sys.stderr)
        return 1
    # The image descriptions read a case's images by arch name, as on the
    # gate.
    for case in process_cases:
        case.setdefault("_root_image", {})[arch["name"]] = \
            images[case["root_pkg"]]
        if case.get("children"):
            case.setdefault("_child_images", {})[arch["name"]] = \
                [images[c] for c in case["children"]]

    # Each kernel entry the cases boot is compiled once, before any case
    # links one; an entry that fails is reported by each case that boots it.
    kernel_failures = {}
    if not python_path:
        kernel_failures = _build_kernels(builder, profile, arch, cases, 1)

    def build_flash(case, board=False, check=True):
        """`flash_image` over one case's image, through the builder or the
        runner's own path. A board image is linked with no `simcall` in its
        stops."""
        name = case["name"]
        if python_path:
            return flash_image(name, case_objs(case), board=board,
                               check=check)
        failure = kernel_failures[case["src"]]
        if failure is not None:
            raise failure
        tag = "board" if board else "flash"
        flash = os.path.join(s3_build, f"{name}.{tag}.bin")
        printed = _link_image(
            builder, profile, case, arch,
            os.path.join(s3_build, f"{name}.{tag}.elf"),
            flash={"output": flash, "check": check},
            link_args=["--defsym=_sos_semihosting=0"] if board else [])
        found = re.search(r"^flash used=(\d+) end=(\d+)$", printed, re.M)
        if not found:
            raise ToolError(f"{name}: the image builder did not say how much "
                            f"of the flash image it placed:\n{printed}")
        return flash, int(found.group(1)), int(found.group(2))

    def case_objs(case):
        """Every object one case's image links: the native half, its kernel,
        and what is appended to it."""
        name = case["name"]
        objs = list(shared) + [kernel_obj(name, case["src"])]
        if case.get("asm"):
            objs.append(cc(os.path.join(s3_tests, case["asm"]),
                           os.path.join(s3_build, f"{name}.payload.o")))
        if case.get("child_asm"):
            objs.append(_child_asm_obj(case, arch, clang))
        if case.get("root_pkg"):
            objs.append(_stitch_root_image(
                case["_root_image"][arch["name"]], arch, clang, name))
        if needs_region_table(case):
            objs.append(_stitch_regions(case, arch, clang))
        return objs

    if emit is not None:
        # A board image: the case's own image, linked without `simcall`,
        # trimmed to its last byte, never booted here. The first run on a
        # board is hal/xtensa-esp32s3/README.md's "First run on a board".
        case = cases[0]
        try:
            _flash, used, end = build_flash(
                case, board=True, check=not case.get("loader_fixture"))
        except ToolError as e:
            print(f"{CROSS} {case['name']}: {e}", file=sys.stderr)
            return 1
        with open(_flash, "rb") as f:
            image = f.read(end)
        with open(emit_out, "wb") as f:
            f.write(image)
        print(f"{CHECK} {case['name']}: {emit_out}  ({end} bytes, written "
              f"at flash offset 0x0; magic checked at 0x400)")
        print(f"  sha256 {hashlib.sha256(image).hexdigest()}")
        print("  write it with:")
        print(f"    python3 -m esptool --chip esp32s3 --port PORT "
              f"write-flash 0x0 {emit_out}")
        return 0

    failed = 0
    for i, case in enumerate(cases, 1):
        name = case["name"]
        try:
            flash, size, _end = build_flash(
                case, check=not case.get("loader_fixture"))
            if case.get("expect_build_error"):
                print(f"[{i}/{len(cases)}] {CROSS} {name}")
                print(f"    the build succeeded; it must refuse: "
                      f"{case['expect_build_error']}")
                failed += 1
                continue
            out, status = boot(flash)
        except ToolError as e:
            if case.get("expect_build_error") and \
                    case["expect_build_error"] in str(e):
                print(f"[{i}/{len(cases)}] {CHECK} {name}  (the build "
                      "refused it)")
                continue
            print(f"[{i}/{len(cases)}] {CROSS} {name}")
            print(f"    {e}")
            failed += 1
            continue

        if status is None:
            ok, reason = False, f"the emulator was still running after " \
                                f"{timeout_s}s"
        else:
            ok, reason = _check(case, arch, status, out, False)
        if ok:
            print(f"[{i}/{len(cases)}] {CHECK} {name}  "
                  f"({size} bytes of flash)")
            continue
        failed += 1
        # `_check`'s reason quotes the whole console; the console is printed
        # below line by line instead, so only its first line is kept.
        print(f"[{i}/{len(cases)}] {CROSS} {name}")
        print(f"    {reason.split(' (got ')[0]}")
        print("    --- console ---")
        for line in out.splitlines():
            print(f"    {line}")
        print("    --- end ---")

    print()
    print("=" * 60)
    if failed:
        print(f"{RED}SOS ESP32-S3 SMOKE FAILED{RESET} "
              f"({failed} of {len(cases)})")
    else:
        print(f"{GREEN}ESP32-S3 SMOKE PASSED{RESET} ({len(cases)} cases)")
    print("=" * 60)
    return 1 if failed else 0


def own_table_problems(source, path):
    """Mistakes in this file's own tables, found from its syntax tree.

    Python keeps the last value of a key a dict literal names twice and says
    nothing, so two case entries joined into one literal (a `},` and a `{`
    lost in an edit) leave one case where two were written, with no error.
    Two findings, each as `PATH:LINE: message`:

      - a dict literal naming a key twice, wherever it is;
      - two cases of one list sharing a `name`. A case is a dict literal with
        a string `name`; a list is one list or tuple literal, so the board
        sections' own lists may repeat a name another list uses.
    """
    problems = []
    for node in ast.walk(ast.parse(source, filename=path)):
        if isinstance(node, ast.Dict):
            seen = {}
            for key in node.keys:
                if not isinstance(key, ast.Constant):
                    continue
                if key.value in seen:
                    problems.append(
                        f"{path}:{key.lineno}: a dict literal names the key "
                        f"{key.value!r} twice (first at line "
                        f"{seen[key.value]}); two entries joined into one?")
                else:
                    seen[key.value] = key.lineno
        elif isinstance(node, (ast.List, ast.Tuple)):
            named = {}
            for entry in node.elts:
                if not isinstance(entry, ast.Dict):
                    continue
                for key, value in zip(entry.keys, entry.values):
                    if (isinstance(key, ast.Constant) and key.value == "name"
                            and isinstance(value, ast.Constant)
                            and isinstance(value.value, str)):
                        if value.value in named:
                            problems.append(
                                f"{path}:{value.lineno}: the case name "
                                f"{value.value!r} is used twice in one list "
                                f"(first at line {named[value.value]})")
                        else:
                            named[value.value] = value.lineno
    return problems


def check_own_tables():
    """Stop, before anything is built, if this file's tables are malformed."""
    path = os.path.abspath(__file__)
    with open(path, encoding="utf-8") as f:
        problems = own_table_problems(f.read(), path)
    if problems:
        for line in problems:
            print(f"{RED}{line}{RESET}", file=sys.stderr)
        sys.exit(2)


def main():
    check_own_tables()
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
                             "toolchain on the gate's machine. `esp32s3` "
                             "is the Xtensa board's smoke, on the same "
                             "terms")
    parser.add_argument("--emit-flash", metavar="CASE",
                        help="with --board esp32s3: build CASE's image for a "
                             "real board (no semihosting exit), write it to "
                             "--out trimmed to its length, and run nothing")
    parser.add_argument("--out", metavar="PATH",
                        help="where --emit-flash writes the image")
    args = parser.parse_args()

    if args.emit_flash is not None and (args.board != "esp32s3"
                                        or args.out is None):
        print(f"{RED}--emit-flash needs --board esp32s3 and --out PATH"
              f"{RESET}", file=sys.stderr)
        sys.exit(2)

    if args.board == "esp32s3":
        # Board section: esp32s3, all of it in `_esp32s3_smoke`. `--case`
        # takes the same spellings as on the gate.
        wanted = [w.strip().replace("-", "_")
                  for value in (args.cases or ()) for w in value.split(",")
                  if w.strip()]
        emit = (args.emit_flash.strip().replace("-", "_")
                if args.emit_flash is not None else None)
        sys.exit(_esp32s3_smoke(wanted, emit, args.out))

    if args.board != "virt":
        # ------------------------------------------------------------------
        # Board section: esp32c3. Everything board-specific lands here and only
        # here.
        # ------------------------------------------------------------------
        if args.board != "esp32c3":
            print(f"{RED}unknown --board {args.board!r}; known: virt, "
                  f"esp32c3, esp32s3{RESET}", file=sys.stderr)
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

        def c3_regions_stub(name, child_images, flags):
            """The child blobs and the boot region table that names them.

            This board assembles its own stub, with its own compiler row and
            child layout; the table itself comes from the shared
            `_region_table_section`. Row order is the same contract (every
            blob, then every destination) because root's config reads it as
            tags.
            """
            lines = ["/* GENERATED by tools/sos_runner.py --board esp32c3 */",
                     "    .section .childimg, \"a\", @progbits",
                     "    .balign 16"]
            for i, img in enumerate(child_images):
                lines += [f"_sos_child{i}_start:",
                          f"    .incbin \"{img}\"",
                          f"_sos_child{i}_end:",
                          "    .balign 16"]
            rows = [(f"child {i}'s image blob (linker-resolved)",
                     f"_sos_child{i}_start",
                     f"_sos_child{i}_end - _sos_child{i}_start",
                     REGION_KIND_RAM) for i in range(len(child_images))]
            rows += [(f"child {i}'s destination RAM",
                      f"{C3_CHILD_BASE + i * C3_CHILD_LEN:#x}",
                      f"{C3_CHILD_LEN:#x}", REGION_KIND_RAM)
                     for i in range(len(child_images))]
            # The shared writer, with this board's word width: 64-bit fields
            # as a low half and a zero high half.
            lines += [""] + _region_table_section(
                dict(c3_arch, hex_width=8), rows, flags)
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
            {
                # The tick armed on the board. This case boots the
                # `isolation` case's images with the tick on (`"tick": True`)
                # and holds that the kernel gets through the load and the
                # first entry with it armed, with the same transcript. The
                # kernel masks interrupts at entry (`boot.S`; ABI.md §7d), so
                # nothing armed can fault it. This case does not pin that
                # clear: with it removed the case still passes, because the
                # fault it prevents is intermittent and needs a short period.
                # The pin is the stress run ABI.md §7d describes, not this
                # smoke. The emulator never delivers the interrupt to user
                # mode, so nothing here shows preemption; `tick_spinner` on
                # the virt profiles does.
                "name": "tick_armed",
                "tick": True,
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
                # Every image but the tick's carries a table, whatever its
                # rows, because the boot flags live in its header.
                if needs_region_table(case):
                    objs.append(c3_regions_stub(
                        name, [_build_root_image(blade_bin, pkg, c3_arch, clang)
                               for pkg in case["children"]],
                        boot_flags(case)))
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

    python_path = _python_builder()
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
    # driven per target. It builds the image builder too, which is why it is
    # built whenever the builder is used, whatever the cases need; only a run
    # that builds packages says so.
    blade_bin = None
    builder = None
    packages = any(case.get("root_pkg") or case.get("children")
                   for case in selected_cases)
    if packages or not python_path:
        shared_build = os.path.join(REPO_ROOT, ".build", "sos-host")
        os.makedirs(shared_build, exist_ok=True)
        try:
            if packages:
                print(f"{BOLD}building blade{RESET}")
            blade_bin = _build_blade(shared_build)
        except ToolError as e:
            print(f"{CROSS} failed to build blade\n{e}", file=sys.stderr)
            sys.exit(1)
        if not python_path:
            # The builder's own tests run in the gate, which is every case on
            # every profile; a run narrowed to some is a development run.
            try:
                _link_sawlang()
                builder = _build_sosimage(blade_bin)
                if not args.cases and not args.arch:
                    _test_sosimage(blade_bin)
            except ToolError as e:
                print(f"{CROSS} failed to build or test the image builder"
                      f"\n{e}", file=sys.stderr)
                sys.exit(1)
        if packages:
            print()

    total_passed = 0
    total_failed = 0
    for arch in arches:
        passed, failed = _run_arch(arch, qemus[arch["name"]], lld, clang,
                                   blade_bin, builder, selected_cases,
                                   args.jobs)
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
