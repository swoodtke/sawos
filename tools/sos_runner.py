#!/usr/bin/env python3
"""SOS QEMU test harness (designs 112, 140, 162).

Builds the freestanding SOS kernel — and, for the cases that need one, a root
server image — runs them under QEMU `virt` on EVERY architecture SOS targets,
and asserts the console transcript and the emulator's exit status. `make
sos-test` green means "sawc-built code boots, crosses into user mode, and gets
the right answer or the right diagnostic", on BOTH profiles; either failing is
red.

Design 162 made this two-architecture. The shape that makes it cheap is the HAL
seam: one arch-free kernel (`sos/kernel/core/`) plus one directory per machine
(`sos/hal/<arch>/kernel/`), so adding a target here is a table entry and a
directory rather than a second harness. The scan in `_check_arch_free` is what
keeps that true — an architecture name in the arch-free kernel would still
COMPILE, and would only be wrong on the profile nobody happened to be building.

Every kernel source builds under `--no-hidden-alloc` (design 135): a kernel is
the audience for that flag, so the gate carries it permanently and any
compiler-inserted allocation the source does not name breaks the build here
rather than shipping.

Pipeline per test case, per architecture:
  1. sawc   : <src>.saw  -> <name>.o   (--freestanding --no-hidden-alloc
              --target <triple>, --module-path kcore=… hal=… — the arch-free
              kernel and the HAL it reaches the machine through)
  2. clang  : boot.S     -> boot.o     (kernel HAL; assembled once)
  3. clang  : sink.c     -> sink.o     (kernel HAL: board hooks + protection)
     clang  : support.c  -> support.o  (mem* + the atomic libcalls — the C that
                                        must stay C, compiled once)
  4. the `.payload` blob, if the case has one — EITHER a hand-written `.S` from
     `sos/tests/<arch>/` (unit A's user-mode code, unit B's hand-assembled
     sosimgs) OR a root package built by Blade and pulled in through
     sos/kernel/rootimg.S's `.incbin`
  5. ld.lld : link with the HAL's virt.ld --gc-sections -> <name>.elf
  6. qemu   : run with a hard timeout; capture console stdout + exit status

A root package (`root_pkg`) is a real Blade package with `[sos] emit =
"sosimg"` in its manifest and a `[sos.<triple>]` section per machine, so the
two-image cases exercise the same build path any later SOS process will use
rather than a rule written here — and prove that ONE unchanged `src/` builds
for both profiles.

QEMU / ld.lld / clang are HOST PREREQUISITES (like the Python venv), not
Blade-managed; the harness probes for them up front and fails with an install
hint. It is deliberately separate from test_runner.py (a different execution
model) but reports in the same pass/fail style.

The SAWLANG side — the compiler, Blade, and the `imgformat`/`toml`/`semver`
packages — is NOT a host prerequisite and is not a path computed here either:
it comes from `tools/toolchain.py` (design 238 unit 4), which is the one place
allowed to locate a sawlang artifact. In this repository it resolves to this
checkout and nothing about running the harness changes; after design 238 unit 5
the language lives in a different repository and the same call finds it there.

`--arch <name>` runs one architecture, for development. The GATE is both.

`-j N` runs N package builds, and then N cases, at a time. It DEFAULTS TO 4
(user ruling, Sep 1): this host has four performance cores and six efficiency
ones, and the parallelism tracks the P-cores rather than the core count, because
a QEMU boot parked on an E-core is the slowest thing in the run. `-j 1` is the
serial harness, unchanged and reachable, for when a transcript has to be read
against the one the suite has always printed.

WHAT PARALLELISM IS NOT ALLOWED TO MOVE (the acceptance the `-j` work was
gated on): a case's transcript, its assertions, and the ORDER the report prints
in. Workers return text and a verdict; the CALLER prints, walking the case list
in definition order, so completion order never reaches the console and a
parallel run's output is byte-identical to a serial one's. The isolation that
makes the transcripts identical is stated at each of the three places it is
bought — `_stitch_root_image`'s per-case staging directory, `_run_qemu`'s own
stdin, and `main`'s eager `tc()`.
"""

import argparse
import concurrent.futures
import os
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

# THE SAWLANG SIDE COMES FROM THE RESOLVER, NOT FROM `REPO_ROOT` (design 238
# unit 4). The compiler, Blade, and the `imgformat`/`toml`/`semver` packages
# live in the LANGUAGE repository, which unit 5 makes a different repository
# from this harness's — so every one of them is a `tools/toolchain.py` call and
# none is a path computed here. Everything above and below stays computed off
# `REPO_ROOT`: those are SOS's own sources, which travel with this file.
_TOOLCHAIN = None


def tc():
    """The resolved sawlang toolchain (design 238 D-b), resolved once.

    Lazy rather than module-level so importing this file cannot exit: the
    refusal is a message for an operator running the harness, and it is
    printed once, here, rather than at each of the four use sites.
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
# the HAL's boot code calls, so the module path is not optional for any case.
CORE_MODULE = f"kcore={CORE_DIR}"

# Arch-free, role-free Saw runtime helpers, shared with every process build.
SOSRT_DIR = os.path.join(REPO_ROOT, "rt", "common", "src")
SOSRT_MODULE = f"sosrt={SOSRT_DIR}"

# The call numbers. Kernel-INTERNAL (sos/spec.md §5.7's vDSO discipline): the
# dispatch tables here and the `sos` module the kernel exports to userspace
# share it, and nothing else does. A process links `sos` and never sees a
# number, so it never needs this path.
SOSABI_DIR = os.path.join(KERNEL_DIR, "abi", "src")
SOSABI_MODULE = f"sosabi={SOSABI_DIR}"

RT_COMMON_C_DIR = os.path.join(REPO_ROOT, "rt", "common_c")

# THE SHARED riscv32 HAL (design 23). `kernel/sink.c` and `user/syscall.c` were
# byte-identical between the two riscv32 boards, and `kernel/lib.saw`'s
# ARCHITECTURAL half — the trap frame, the cause decoding, the syscall
# accessors, the PMP staging, the payload/region seams — was very nearly so. All
# three live here in ONE copy now, and both boards' build entries point at them:
# the C files by path (`hal_native` below, and every root/child manifest's
# `native` line), the Saw half as one more `--module-path` beside the board's
# own `hal=`.
#
# THE `hal` SEAM DID NOT MOVE. `kernel/core` still imports the module named
# `hal` and the runner still maps `hal=<board>/kernel`; the board's `hal` module
# `public import`s from `rv32core` and re-exports what `kcore` consumes.
RV32_COMMON_DIR = os.path.join(HAL_DIR, "riscv32-common")
RV32_CORE_MODULE = f"rv32core={os.path.join(RV32_COMMON_DIR, 'kernel')}"

# **THE VIRT BOARD, UNDER A SECOND MODULE NAME** (sawos design 35, M5 unit 4).
#
# The same directory the isolated profile maps as `hal=`, mapped as `rv32virt=`
# for the FLAT profile, whose own `hal` module re-exports the board half from it
# and defines only the protection surface itself. A Saw module's name comes from
# its `--module-path` mapping, so one directory can be the board's `hal` in one
# build and a component of somebody else's `hal` in another — which is what lets
# a build profile exist without a second copy of the UART, the CLINT, the PLIC
# and the memory map, and what keeps design 23's consolidation untouched.
RV32_VIRT_MODULE = f"rv32virt={os.path.join(HAL_DIR, 'riscv32', 'kernel')}"

# `ExitCode.ProcessFault` in sos/kernel/core/lib.saw: what the machine exits
# with when the kernel TERMINATES a process for a caller error it could have
# checked (design 178's faults ruling). Kept in step with that enum.
EXIT_PROCESS_FAULT = 5

# Root-server packages. These are real Blade packages built by Blade — the
# whole point of unit C is that root goes through the same package pipeline any
# SOS process will, not a bespoke rule in this file. (Blade itself, and the
# packages it is built from, come through `tc()` above.)
ROOT_PKG = os.path.join(REPO_ROOT, "root")
FAULT_ROOT_PKG = os.path.join(TESTS_DIR, "faulting-root")
# design 178 M2 unit 2: three root servers that exercise Thread and Process
# objects. They are real Blade packages for the same reason sos/root is — a
# process that makes threads should go through the pipeline every SOS process
# goes through, and should reach the kernel through the `sos` module and never
# through an op number.
THREAD_BASICS_PKG = os.path.join(TESTS_DIR, "thread-basics")
THREAD_PREEMPT_PKG = os.path.join(TESTS_DIR, "thread-preempt")
# design 178 M2 unit 3: two more, for the Event and Waiter objects. Same
# reasoning — a process that makes Events reaches the kernel through the `sos`
# module and never through an op number, so the test is written the way a real
# one would be.
EVENT_BASICS_PKG = os.path.join(TESTS_DIR, "event-basics")
EVENT_WAKE_PKG = os.path.join(TESTS_DIR, "event-wake")
# The Aug-17 consume ruling: a wait delivery TAKES an Event's word. Two
# packages, split by DOOR — one where every wait finds something already ready
# and one where both takes are real wakes — because the delivery runs a
# different path in each (the waiting thread reads its own record; a signalling
# thread writes into a parked thread's frame), and one funnel is exactly the
# claim that needs a case per path.
EVENT_CONSUME_PKG = os.path.join(TESTS_DIR, "event-consume")
EVENT_CONSUME_WAKE_PKG = os.path.join(TESTS_DIR, "event-consume-wake")
# rider 4: the one fault in this unit that a SAW process can reach, so the test
# is written the way a real process would be rather than as a payload.
EVENT_DUPKEY_PKG = os.path.join(TESTS_DIR, "event-dupkey")
# design 178 M2 unit 4: the Interrupt object and the milestone's proof. The two
# echo packages are PER DEVICE rather than per architecture — a driver names its
# device, and the two profiles have different UARTs — so each case names the
# architecture it applies to and `_root_packages_for` builds only that one's.
# The two fault packages are arch-free: both line numbers below mean the same
# thing on either board.
UART_ECHO_NS16550_PKG = os.path.join(TESTS_DIR, "uart-echo-ns16550")
UART_ECHO_PL011_PKG = os.path.join(TESTS_DIR, "uart-echo-pl011")
IRQ_BADLINE_PKG = os.path.join(TESTS_DIR, "irq-badline")
IRQ_EARLYACK_PKG = os.path.join(TESTS_DIR, "irq-earlyack")
# The branch unit 4 did NOT change, pinned beside the one it did: with no line
# bound, "nothing runnable" is still the deadlock the kernel has always
# reported.
WAIT_DEADLOCK_PKG = os.path.join(TESTS_DIR, "wait-deadlock")
# design 232 M3 unit 1: the Clock and Timer objects. Six packages, and the split
# follows the M2 units' rule — what an object ANSWERS and whether a wait PARKS
# are separate questions, so testing them together would let a scheduling bug
# hide behind an arithmetic one.
# sawos design 2 (M3 unit 2): the second address space. FIVE root packages and
# TWO CHILD packages, and the split is the same one every unit above has used —
# one claim per image, so a bug in the reload cannot hide behind a load that
# happened to work.
#
# The CHILDREN are the first Blade packages in the tree that are not root
# servers. They are built exactly as a root is and linked at a DIFFERENT base
# (`hal/<arch>/user/child.ld`), because under the no-translation contract every
# process shares one physical address space. Neither holds a handle: `start()`
# hands a child `NO_HANDLE`, so its observable behaviour is faulting.
PROCESS_LIFECYCLE_PKG = os.path.join(TESTS_DIR, "process-lifecycle")
PROCESS_ISOLATION_PKG = os.path.join(TESTS_DIR, "process-isolation")
PROCESS_BADIMAGE_PKG = os.path.join(TESTS_DIR, "process-badimage")
PROCESS_DOUBLESTART_PKG = os.path.join(TESTS_DIR, "process-doublestart")
PROCESS_BOOTDRAIN_PKG = os.path.join(TESTS_DIR, "process-bootdrain")
CHILD_FAULT_PKG = os.path.join(TESTS_DIR, "child-fault")
CHILD_POKE_PKG = os.path.join(TESTS_DIR, "child-poke")

# sawos design 3 (M3 unit 2.75): the handle lifecycle. SEVEN packages, and the
# split is the house rule taken literally — one claim per image — plus one fact
# about this unit that forces it: five of the seven end in a FAULT, and a fault
# ends the process, so a second probe after one would never run. Three of them
# (`nothing`, `twice`, `malformed`) are the three shapes of "this word resolves
# to no object", which is one ANSWER from the kernel and three different
# mistakes from a caller.
#
# FOUR REACH THE C ALTITUDE, and that is a claim rather than a shortcut: the
# typed Saw surface has no word extractor and no word constructor, so a handle
# word cannot be compared, re-used after release, or forged there at all.
# `handle-drop-release` is the same mechanism seen from the typed side, where
# release is spelled by a value going out of scope and no word appears.
HANDLE_REMINT_PKG = os.path.join(TESTS_DIR, "handle-remint")
HANDLE_RELEASE_UNGATED_PKG = os.path.join(TESTS_DIR, "handle-release-ungated")
HANDLE_RELEASE_NOTHING_PKG = os.path.join(TESTS_DIR, "handle-release-nothing")
HANDLE_RELEASE_TWICE_PKG = os.path.join(TESTS_DIR, "handle-release-twice")
HANDLE_MALFORMED_WORD_PKG = os.path.join(TESTS_DIR, "handle-malformed-word")
HANDLE_DROP_RELEASE_PKG = os.path.join(TESTS_DIR, "handle-drop-release")
PROCESS_RECLAIM_PKG = os.path.join(TESTS_DIR, "process-reclaim")
# sawos design 4 (M3 unit 3): mint, give, and the boot drain. Eight root servers
# and two more CHILDREN — the first SOS processes that are neither root nor
# sandboxed, since each receives a MASKED SYSTEM HANDLE and can therefore print,
# name itself, drain and exit.
GIVE_BOOT_DRAIN_PKG = os.path.join(TESTS_DIR, "give-boot-drain")
GIVE_DUPLICATE_TAG_PKG = os.path.join(TESTS_DIR, "give-duplicate-tag")
GIVE_AFTER_START_PKG = os.path.join(TESTS_DIR, "give-after-start")
GIVE_NO_TRANSFER_PKG = os.path.join(TESTS_DIR, "give-no-transfer")
START_BAD_TAG_PKG = os.path.join(TESTS_DIR, "start-bad-tag")
GIVE_WORD_DEAD_PKG = os.path.join(TESTS_DIR, "give-word-dead")
MINT_REVOKED_PKG = os.path.join(TESTS_DIR, "mint-revoked")
CHILD_NO_SHUTDOWN_PKG = os.path.join(TESTS_DIR, "child-no-shutdown")
CHILD_DRAIN_PKG = os.path.join(TESTS_DIR, "child-drain")
CHILD_OVERSTEPS_PKG = os.path.join(TESTS_DIR, "child-oversteps")

# sawos design 6 (M3 unit 4): Memory, IoMemory and Mapping. FIVE root servers
# and one more CHILD, and the split is the house rule plus the same fact design
# 3's split turned on: THREE of the five end in a FAULT, and a fault ends the
# process, so a second probe after one would never run. Each of those three does
# its positive work FIRST and meets its refusal last, which is `irq_early_ack`'s
# shape — the transcript reads as everything that worked, then the one thing
# that must not.
#
# THREE OF THEM NAME AN ADDRESS, through a one-line C constant per architecture
# (`tests/poolbase_<arch>.c`). That is root's config, not kernel state leaking:
# a region has no bounds reader and unit 4 deliberately did not add one, so
# WHERE a pool is is a fact the build publishes and the program expects — the
# same arrangement the uart-echo driver has always had with `UART_BASE`.
MEMORY_SPLIT_PKG = os.path.join(TESTS_DIR, "memory-split")
MAP_BASICS_PKG = os.path.join(TESTS_DIR, "map-basics")
MAP_UNMAP_PKG = os.path.join(TESTS_DIR, "map-unmap")
MAP_INTO_CHILD_PKG = os.path.join(TESTS_DIR, "map-into-child")
IOMEMORY_CARVE_PKG = os.path.join(TESTS_DIR, "iomemory-carve")
CHILD_TOUCH_PKG = os.path.join(TESTS_DIR, "child-touch")

# sawos design 7 (M3 unit 5): quotas and the reference count. FIVE root servers
# and two more CHILDREN, and the split into two halves is forced by the unit
# itself rather than by the house rule.
#
# THE THREE REFCOUNT CASES ARE ROOT'S, because free-on-zero is about objects and
# root may hold any number of them. THE TWO QUOTA CASES NEED A CHILD, because
# root CANNOT MEET A QUOTA: root is init, its policy cap IS the machine (design
# 7 D-3), so its ledger rows are unlimited and the slab is the only thing that
# refuses it. A budget is a thing a launched process has, which is why proving
# one costs a launcher and a child rather than a single image.
QUOTA_EXCEEDED_PKG = os.path.join(TESTS_DIR, "quota-exceeded")
QUOTA_VS_WALL_PKG = os.path.join(TESTS_DIR, "quota-vs-wall")
REFCOUNT_FREE_PKG = os.path.join(TESTS_DIR, "refcount-free")
INTERRUPT_UNBIND_PKG = os.path.join(TESTS_DIR, "interrupt-unbind")
MAPPING_SLOT_FREE_PKG = os.path.join(TESTS_DIR, "mapping-slot-free")

# sawos design 28 (M5 unit 5): the real allocator. ONE root server, and it
# borrows M3 unit 4's child (`child-touch`) rather than growing one of its own —
# what leg 2 needs from a child is exactly what that one does, which is to mark
# a page it was mapped and die.
MEMORY_RECYCLE_PKG = os.path.join(TESTS_DIR, "memory-recycle")

# M5 unit 6 (sawos design 32): root donates a page to the Waiter slab and creates
# past the compiled floor. No child, no device — only a pool to cut from.
SLAB_DONATE_PKG = os.path.join(TESTS_DIR, "slab-donate")

# The negative half: a region with a SIBLING handle is refused, by ending the
# caller. See the package header for why that is a fault and not a status.
SLAB_DONATE_SHARED_PKG = os.path.join(TESTS_DIR, "slab-donate-shared")

# M5 unit 6a (sawos design 34): the two kinds design 32 excluded, one package
# each. `thread-donate` is the unit's RISK witness — a donated thread is started,
# yields and is joined, so the context-switch path runs on a frame in donated
# memory. `pipe-donate` carries a real message over a connection whose slot and
# whose staging ring are both in extent 1.
THREAD_DONATE_PKG = os.path.join(TESTS_DIR, "thread-donate")
PIPE_DONATE_PKG = os.path.join(TESTS_DIR, "pipe-donate")

# The two gaps design 32's As-built recorded against itself, closed here.
# `slab-donate-mapped` is the `maps == 0` half of the safety condition (finding
# 2); `slab-donate-free-nodes` is the unit-5 composition (finding 6), which needs
# a Memories donation before the pool can even be fragmented far enough to reach
# the node slab's wall.
SLAB_DONATE_MAPPED_PKG = os.path.join(TESTS_DIR, "slab-donate-mapped")
SLAB_DONATE_MAPPED_CHILD_PKG = os.path.join(TESTS_DIR, "child-donor")
SLAB_DONATE_NODES_PKG = os.path.join(TESTS_DIR, "slab-donate-free-nodes")

# M5 unit 6b (sawos design 36): the last of the three kinds the user named.
# `process-donate` fills the process table at `MAX_PROCESSES` (root plus two
# children), watches the next create answer `NoResource`, donates, and then
# creates + starts + reaps + RE-creates a third child that can only be living in
# the donated extent.
#
# THREE ECHO CHILDREN, AND THERE ARE THREE OF THEM FOR ONE PROFILE'S SAKE.
# riscv32 places by identity, so three CONCURRENT children need three load
# addresses and therefore three linker scripts — and a package's manifest picks
# a script, so three packages that differ only in that line and in an exit code.
# On aarch64 design 33's placement means all three name one `user.ld`.
PROCESS_DONATE_PKG = os.path.join(TESTS_DIR, "process-donate")
ECHO_CHILD_PKG = os.path.join(TESTS_DIR, "echo-child")
ECHO_CHILD2_PKG = os.path.join(TESTS_DIR, "echo-child2")
ECHO_CHILD3_PKG = os.path.join(TESTS_DIR, "echo-child3")
CHILD_QUOTA_PKG = os.path.join(TESTS_DIR, "child-quota")
CHILD_MAPWALL_PKG = os.path.join(TESTS_DIR, "child-mapwall")

# sawos design 8 (M3 unit 5.5): death notifications. THREE root servers and ONE
# more CHILD, and the split follows the house rule — one claim per image.
#
# THE TWO NOTIFY CASES DIFFER ONLY IN HOW THE CHILD DIES, which is the point:
# the same attachment and the same park deliver `Exited` for one and `Faulted`
# for the other, so a §8 status word arrives through one mechanism whichever
# ending happened. `death_late_attach` is the terminal-level case and carries
# the design-7 refcount interplay with it, because "which reference was holding
# the dead slot open" only has an answer once there are two.
#
# `child-bye` is the new child: the smallest program that can die OBSERVABLY —
# derive a Process object, say one line, `exit(5)`. `child-fault` is reused
# unchanged for the fault arm, which is what makes that arm cost one root
# package rather than two.
DEATH_NOTIFY_PKG = os.path.join(TESTS_DIR, "death-notify")
DEATH_FAULT_PKG = os.path.join(TESTS_DIR, "death-fault")
DEATH_LATE_ATTACH_PKG = os.path.join(TESTS_DIR, "death-late-attach")
CHILD_BYE_PKG = os.path.join(TESTS_DIR, "child-bye")

# sawos design 9 (M3 unit 6): the driver child, shared memory, and the exec
# gate. FOUR root servers and THREE more CHILDREN, and the split follows the
# house rule with one arithmetic fact forcing part of it: a rights refusal and a
# bad-argument refusal are both FAULTS, and a fault ends the process, so the two
# exec-gate claims cannot share an image.
#
# **THE DRIVER CHILD IS ONE LAUNCHER AND TWO DRIVERS, and that asymmetry is the
# claim.** `driver-child` names no device at all — what it moves is a capability,
# and a capability has no datasheet — so it is arch-free and built for both
# profiles, while the two echo children are per-CHIP exactly as the
# root-as-driver twins beside them are. The twins STAY, unmoved: the same driver
# body running as root and as a child, in two transcripts, is what says the
# difference is the launcher rather than the program.
DRIVER_CHILD_PKG = os.path.join(TESTS_DIR, "driver-child")
CHILD_ECHO_NS16550_PKG = os.path.join(TESTS_DIR, "child-echo-ns16550")
CHILD_ECHO_PL011_PKG = os.path.join(TESTS_DIR, "child-echo-pl011")
SHARE_DOUBLE_MAP_PKG = os.path.join(TESTS_DIR, "share-double-map")
# **PLACEMENT'S OWN PAIR** (sawos design 33, M5 unit 2): one region, two
# address spaces, two DIFFERENT addresses. aarch64 only — riscv32 keeps
# identity placement deliberately (design 25 ruling 11), so there the two
# addresses would be one and the case would assert nothing.
MAP_PLACED_PKG = os.path.join(TESTS_DIR, "map-placed")
CHILD_PLACED_PKG = os.path.join(TESTS_DIR, "child-placed")
CHILD_SHARE_PKG = os.path.join(TESTS_DIR, "child-share")
MAP_EXEC_GATED_PKG = os.path.join(TESTS_DIR, "map-exec-gated")
MAP_WX_REFUSED_PKG = os.path.join(TESTS_DIR, "map-wx-refused")

# sawos design 12 (M4 unit 0): waiter revocation. ONE root server, and one is
# the whole unit — the three claims it makes (a revocation is delivered, the
# walk is the whole blocked list, the COUNT gates it rather than the release)
# are one story about one release, and splitting them would need the same three
# threads on the same one Waiter in each image.
#
# IT REACHES THE C ALTITUDE, and that is forced rather than chosen:
# the count claim needs TWO NAMES for one Waiter, and the typed `sos` surface
# publishes `mint` on `System` and `Memory` alone while a `Waiter` keeps its
# handle word private. So the reference arithmetic is written where a handle IS
# a word, which is `handle-remint`'s own stated reason for living down there.
WAITER_REVOKED_PKG = os.path.join(TESTS_DIR, "waiter-revoked")

# sawos design 13 (M4 unit 1): the pair. FOUR root servers and ONE child, and
# the split is the house rule plus the arithmetic that keeps forcing it — a
# rights refusal and a bad-argument refusal are both FAULTS, and a fault ends
# the process, so the two negative arms cannot share an image with each other or
# with anything that has work left to do.
#
# **`pipe-basics` AND `pipe-peer-gone` ARE ONE PROCESS EACH, DELIBERATELY.** Unit
# 1 has no waitability and nothing parks, so the entire data path — round trip,
# FIFO, the empty message, the ring's depth, both zero-arms — is provable inside
# one address space with both ends in one hand. That is what makes `pipe-child`
# a proof about GIVE and the boot drain rather than a second copy of the data
# path: it moves ONE message, and everything else about it is the launch flow
# that shipped in M3 unit 3.
PIPE_BASICS_PKG = os.path.join(TESTS_DIR, "pipe-basics")
PIPE_PEER_GONE_PKG = os.path.join(TESTS_DIR, "pipe-peer-gone")
PIPE_CHILD_PKG = os.path.join(TESTS_DIR, "pipe-child")
CHILD_POST_PKG = os.path.join(TESTS_DIR, "child-post")
PIPE_NO_POST_PKG = os.path.join(TESTS_DIR, "pipe-no-post")
PIPE_OVERSIZED_PKG = os.path.join(TESTS_DIR, "pipe-oversized")

# sawos design 14 (M4 unit 2): the one-shot pair. THREE root servers, ONE child
# and THREE negative arms, and the split is the same house rule the unit-1 block
# above states — a rights refusal, a bad-argument refusal and a dead-handle
# refusal are all FAULTS, and a fault ends the process, so none of the three can
# share an image with each other or with anything that has work left to do.
#
# **`pipe-oneshot` IS ONE PROCESS PLAYING BOTH SIDES, DELIBERATELY.** The whole
# exchange — claim, obligation, would-block, reply, out-of-order replies, and the
# ring counted around ONE unsettled exchange — is provable inside one address
# space with both ends in one hand, exactly as unit 1's data path was. That is
# what leaves `pipe-delegate` free to be a proof about the OBLIGATION CROSSING A
# BOUNDARY rather than a second copy of the exchange.
PIPE_ONESHOT_PKG = os.path.join(TESTS_DIR, "pipe-oneshot")
PIPE_ABANDON_PKG = os.path.join(TESTS_DIR, "pipe-abandon")
PIPE_DELEGATE_PKG = os.path.join(TESTS_DIR, "pipe-delegate")
CHILD_REPLY_PKG = os.path.join(TESTS_DIR, "child-reply")
PIPE_NO_REPLY_PKG = os.path.join(TESTS_DIR, "pipe-no-reply")
PIPE_BIG_REPLY_PKG = os.path.join(TESTS_DIR, "pipe-big-reply")
PIPE_DEAD_CLAIM_PKG = os.path.join(TESTS_DIR, "pipe-dead-claim")

# sawos design 15 (M4 unit 3): waitability. FOUR root servers for the four arms,
# ONE launcher-plus-child for the blocking sends, and TWO negative arms — and the
# split is the same house rule the unit-1 and unit-2 blocks above state: a rights
# refusal and a bad-argument refusal are FAULTS, a fault ends the process, so
# neither can share an image with anything that has work left to do.
#
# **THE FOUR ARM CASES ARE ONE PROCESS EACH, AND NOTHING IN THEM PARKS.** Root
# plays both sides of every connection, so each level is raised BEFORE it is
# waited on — which is §2.2's level-triggering doing exactly what it is ratified
# for, and is what lets one thread assert what a delivery CONTAINS. That is a
# deliberate division of labour: the four cases prove the four arms' payloads and
# their ordering rules, and `pipe_send_manual` is the one that proves the WAKE,
# because a genuine park needs something else to be runnable — and it proves it
# with the composition a CALLER writes, since the ruled surface is the primitives
# and not a library `send` (user, lead review Sep 1).
PIPE_WAIT_REPLY_PKG = os.path.join(TESTS_DIR, "pipe-wait-reply")
PIPE_WAIT_GIVE_PKG = os.path.join(TESTS_DIR, "pipe-wait-give")
PIPE_WAIT_ROOM_PKG = os.path.join(TESTS_DIR, "pipe-wait-room")
PIPE_WAIT_SERVER_PKG = os.path.join(TESTS_DIR, "pipe-wait-server")
PIPE_SEND_MANUAL_PKG = os.path.join(TESTS_DIR, "pipe-send-manual")
CHILD_SERVER_PKG = os.path.join(TESTS_DIR, "child-server")
PIPE_NO_WAIT_PKG = os.path.join(TESTS_DIR, "pipe-no-wait")
PIPE_BAD_MODE_PKG = os.path.join(TESTS_DIR, "pipe-bad-mode")

# sawos design 16: EXACT TRAP INTROSPECTION. One root and one child, and the
# child is the unusual one — it prints NOTHING. Every printed byte is an `ecall`,
# so a talking child has a syscall count that is a hash of its own prose, and the
# whole point of the case is that its launcher asserts the exact number.
PROCESS_STATS_PKG = os.path.join(TESTS_DIR, "process-stats")
CHILD_STATS_PKG = os.path.join(TESTS_DIR, "child-stats")

# sawos design 17: THE FUSED PATHS. `pipe-pingpong` is the ladder's performance
# claim as a transcript row — root serves with `reply_wait`, a silent child
# clients with `send`, and BOTH trap counts are asserted exactly. The three
# abandonment cases are the peer-gone doctrine reaching the one park that is on
# no list, and the three negative arms are the two new ops' refusals.
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

# M4 unit 4 — handles in messages, the rendezvous ledger, the completion queue
# and the keep mask (sawos design 18).
PIPE_HANDLES_PKG = os.path.join(TESTS_DIR, "pipe-handles")
PIPE_DELEGATE_MSG_PKG = os.path.join(TESTS_DIR, "pipe-delegate-msg")
CHILD_HANDLES_PKG = os.path.join(TESTS_DIR, "child-handles")
CHILD_SENDER_PKG = os.path.join(TESTS_DIR, "child-sender")
PIPE_SEND_EXIT_PKG = os.path.join(TESTS_DIR, "pipe-send-exit")
PIPE_TABLE_FULL_PKG = os.path.join(TESTS_DIR, "pipe-table-full")
PIPE_CQ_PKG = os.path.join(TESTS_DIR, "pipe-cq")
PIPE_RESOLVE_PARK_PKG = os.path.join(TESTS_DIR, "pipe-resolve-park")
GIVE_KEEP_MASK_PKG = os.path.join(TESTS_DIR, "give-keep-mask")
CHILD_NARROW_PKG = os.path.join(TESTS_DIR, "child-narrow")

# M4 unit 5 — DRIVER-AS-SERVICE (sawos design 21), the money shot.
#
# `uartproto` is a LIBRARY package rather than an image: the protocol is ONE
# declaration the driver, the client and both launchers compile against, which
# is `PIPE_BODY_BYTES`' argument one altitude down. It has no constant here
# because nothing builds it directly — Blade pulls it in as a path dependency.
#
# The driver is PER-CHIP and the client and launchers are arch-free, which is
# the M3 driver-child split unchanged: a driver names its device, a launcher
# moves a capability, and a capability has no datasheet.
#
# **`svc-client` IS LINKED AT THE SECOND CHILD BASE.** Unit 5 is the first case
# in this tree with two children alive at once, so `hal/*/user/child2.ld` came
# with it — and because `_region_rows` assigns destinations BY INDEX, a package
# linked there is a package its case must list SECOND.
UART_SERVICE_PKG = os.path.join(TESTS_DIR, "uart-service")
UART_CANCEL_PKG = os.path.join(TESTS_DIR, "uart-cancel")
SVC_UART_NS16550_PKG = os.path.join(TESTS_DIR, "svc-uart-ns16550")
SVC_UART_PL011_PKG = os.path.join(TESTS_DIR, "svc-uart-pl011")
SVC_CLIENT_PKG = os.path.join(TESTS_DIR, "svc-client")
DELEGATE_3P_PKG = os.path.join(TESTS_DIR, "delegate-3p")
CHILD_FORWARD_PKG = os.path.join(TESTS_DIR, "child-forward")
CHILD_FAR_PKG = os.path.join(TESTS_DIR, "child-far")

CLOCK_BASICS_PKG = os.path.join(TESTS_DIR, "clock-basics")
TIMER_ONESHOT_PKG = os.path.join(TESTS_DIR, "timer-oneshot")
TIMER_INTERVAL_PKG = os.path.join(TESTS_DIR, "timer-interval")
TIMER_DEADLOCK_PKG = os.path.join(TESTS_DIR, "timer-deadlock")
TIMER_BADCLOCK_PKG = os.path.join(TESTS_DIR, "timer-badclock")
TIMER_BADRECORD_PKG = os.path.join(TESTS_DIR, "timer-badrecord")
TIER_WORD_PKG = os.path.join(TESTS_DIR, "tier-word")

# What the harness types at the guest's serial port for the echo cases, and what
# it then expects to read back out of it.
#
# FOUR BYTES, NONE OF THEM SPECIAL. They avoid the emulator's own escape
# character (a control byte) and they are a string that appears nowhere else in
# any transcript, so finding them in the output means the driver put them there
# — nothing echoes them on the way in, since the guest's serial input is a pipe
# rather than a terminal.
ECHO_INPUT = "Zq7#"

# How long the harness waits before each byte it types.
#
# THE DELAY IS THE TEST, not a workaround. Handed the whole string up front, the
# emulator buffers it before the guest's driver exists and every byte is already
# waiting by the time the driver looks — which proves the echo and NOT the
# interrupt path, since a driver that simply polled would pass. Withholding each
# byte forces the driver to PARK, which leaves the kernel with nothing runnable,
# which is what makes it wait for a wake from outside the set of runnable
# threads. So one wake per byte, and the whole ladder is under test.
#
# It costs `len(ECHO_INPUT) * this` per echo case, well inside the QEMU timeout,
# and a slower machine only makes the guest wait longer — which is what it is
# supposed to do.
SERIAL_TYPE_DELAY_S = 0.15

QEMU_TIMEOUT_S = 10

# =============================================================================
# The architectures
# =============================================================================
#
# One entry per machine SOS targets (spec §5b). Everything that differs between
# them is HERE, in data, which is the harness-level statement of the same claim
# the HAL makes in code: a second architecture is a table row and a directory.
#
# `hex_width` is how many digits the kernel's `write_hex` prints — one per
# nibble of a MACHINE WORD, so eight on a 32-bit profile and sixteen on a
# 64-bit one. Expectations below are written with placeholders and formatted
# per architecture rather than duplicated, so a case asserts the same FACT on
# both and the widths follow the target.

# =============================================================================
# The protection tiers (sawos design 19, ruled as design 25 D-4, built by 35)
# =============================================================================
#
# TWO WORDS, because the kernel advertises two: `Isolated` means the hardware
# refuses an access a process was not granted, `Flat` means it does not. They are
# spelled here as the runner's own constants rather than read out of the kernel,
# because the harness has to decide which cases to RUN before it has booted
# anything — and the `tier_word` case is what checks that the runner's claim and
# the kernel's answer agree.
#
# WHAT THE WORD DECIDES, and it is only ever this: whether a case whose assertion
# is that the HARDWARE denied something is meaningful on this profile. Nothing
# about the kernel boundary is tiered — handles, rights, quotas and lifetimes are
# enforced identically everywhere — so the object-model suite carries no tier at
# all and runs on all three profiles unchanged.
TIER_ISOLATED = "isolated"
TIER_FLAT = "flat"

ARCHES = [
    {
        "name": "riscv32",
        "triple": "riscv32-unknown-none-elf",
        "qemu": "qemu-system-riscv32",
        # PMP: an unmatched U-mode access faults whenever any entry is
        # implemented, which is what locks the kernel, the UART and the finisher
        # away from root by SAYING NOTHING about them (sawos design 35).
        "tier": TIER_ISOLATED,
        # `-bios none`: no OpenSBI, the kernel IS the reset target.
        "qemu_args": ["-M", "virt", "-bios", "none"],
        # A triple names the architecture but not which optional extensions the
        # part has: without `+m` the Saw half is built for base rv32i and
        # formatting an integer emits `__divsi3` calls this link cannot satisfy.
        # The C half gets the same set through `-march`.
        "cc_args": ["-march=rv32imac_zicsr", "-mabi=ilp32"],
        "features": "+m,+a,+c",
        # design 23: this board's `sink.c` and the architectural half of its
        # `hal` module are the SHARED riscv32 ones, so both are named here
        # rather than under `hal/riscv32/`.
        "hal_native": os.path.join("riscv32-common", "kernel"),
        "hal_modules": [RV32_CORE_MODULE],
        "hal_asm": [os.path.join("riscv32-common", "kernel", "trap.S")],
        "hex_width": 8,
        "root_entry": 0x80200000,
        # Where a CHILD process's memory is (sawos design 2). This is the
        # "config-assigned destination range" the ruled hybrid puts on the build
        # side: the kernel never knows it, the region table publishes it as an
        # ordinary free-RAM row, and root learns it as a capability. It is one
        # region above root's top, and the per-arch child linker script
        # (`hal/<arch>/user/child.ld`) is linked at exactly this base.
        "child_region_base": 0x80240000,
        # The line `hal.irq_raise_selftest_line()` raises (design 178 M2 unit
        # 1). It is per-machine because WHAT a board can interrupt itself with
        # is: this one has no software trigger, so the HAL makes the console
        # interrupt, on the line this board wires it to.
        "selftest_line": 10,
        # THE RAM POOL a `"pool": True` case publishes (sawos design 6 D-5) —
        # a free window ABOVE every child region, so a case may have children
        # and a pool at once and neither moves. It is a runner constant
        # mirroring a HAL fact: this is ordinary RAM, past root's region
        # (0x8024_0000) and past the one child region above it.
        #
        # THE TEST PACKAGES KNOW THIS NUMBER, through a one-line C constant per
        # architecture (`tests/poolbase_<arch>.c`). That is not a leak of kernel
        # state: root's config is what says which ordinal means what, and WHERE
        # a pool is is the same class of config — the uart-echo driver has
        # always known `UART_BASE` for exactly this reason. Saw cannot name an
        # address any other way (DF-172a), which is why it is C.
        "pool_base": 0x80280000,
        "pool_len": 0x40000,
        # THE DEVICE WINDOW a `"device": True` case publishes — the console
        # UART's page, mirroring `hal.DEVICE_GRANT_BASE` / `DEVICE_GRANT_LEN`.
        # This is what the uart-echo migration obtains instead of declaring.
        "device_base": 0x10000000,
        "device_len": 0x1000,
    },
    {
        "name": "arm64",
        "triple": "aarch64-unknown-none-elf",
        "qemu": "qemu-system-aarch64",
        # Page tables: since design 29 the kernel lives under TTBR1 and a
        # process reaches exactly what its own tables map — isolation by
        # absence (sawos design 35).
        "tier": TIER_ISOLATED,
        # `-cpu cortex-a53` (design 162 decision 3): ubiquitous, EL1
        # well-exercised. `-semihosting` is what makes SYS_EXIT carry a status
        # code — see sos/hal/arm64/kernel/sink.c for why not PSCI.
        # `gic-version=2` is PINNED rather than defaulted (design 178 M2 unit
        # 1): the HAL programs a v2 controller, which is what this machine has
        # given us so far, and a newer emulator changing its default would swap
        # the hardware under a kernel that cannot say so.
        "qemu_args": ["-M", "virt,gic-version=2", "-cpu", "cortex-a53",
                      "-semihosting"],
        # The base aarch64 triple already has everything this kernel uses, and
        # there is no ABI variant to select.
        "cc_args": [],
        "features": None,
        # There is one arm64 board in this tree, so its HAL is whole: its own
        # `sink.c`, and no shared module beside its `hal`. Design 23 consolidated
        # the riscv32 pair and deliberately left this profile alone.
        "hal_native": os.path.join("arm64", "kernel"),
        "hal_modules": [],
        "hal_asm": [],
        "hex_width": 16,
        # **THE ONE VIRTUAL BASE EVERY IMAGE ON THIS BOARD IS LINKED AT** (sawos
        # design 33), mirroring `hal.USER_IMAGE_BASE` and `hal/arm64/user/user.ld`.
        # It is also root's physical destination, which is why the console's
        # `entry=` row did not move when placement landed: root is the one
        # process whose two addresses still coincide.
        "root_entry": 0x40200000,
        # **A CHILD'S DESTINATION FRAMES — PHYSICAL, AND NO LONGER AN ADDRESS ANY
        # PROGRAM SEES** (sawos design 33). One region above root's, as on
        # Profile A. It used to be a link base too (`hal/arm64/user/child.ld`,
        # now collapsed into `user.ld`): a child linked HERE because a user
        # address was a physical address, and it links at `root_entry` now
        # because the kernel translates. The number stays because the FRAMES stay
        # — the build's region table still hands a launcher this range.
        #
        # Still CONSTRAINED as well as tidy, and the constraint moved with the
        # split: the frames must be RAM the linear map covers (`map_source_ok`),
        # while it is the VIRTUAL range that must land in the HAL's 4 MiB grant
        # window (`map_target_ok`).
        "child_region_base": 0x40240000,
        # This controller HAS a software trigger, so the selftest line is a
        # software-generated one and no device is involved.
        "selftest_line": 5,
        # The RAM pool, one region above the child region as on Profile A — and
        # here the choice is CONSTRAINED as well as tidy, exactly as
        # `child_region_base` is: EL0 can only be granted pages inside the HAL's
        # 4 MiB grant window, so the pool has to end below 0x4040_0000. It does
        # (0x4028_0000 + 256 KiB = 0x402C_0000).
        "pool_base": 0x40280000,
        "pool_len": 0x40000,
        # The console UART's page, mirroring `hal.DEVICE_GRANT_BASE` / `_LEN`.
        "device_base": 0x09000000,
        "device_len": 0x1000,
    },
    # =========================================================================
    # **THE FLAT BUILD PROFILE** (sawos design 35, M5 unit 4; design 19's
    # "testing targets for the flat tier"; design 25's tier table, last row)
    # =========================================================================
    #
    # THE SAME BOARD, WITH THE PROTECTION TURNED OFF AND THE TIER WORD SAYING SO.
    # Not a third architecture and not a third board — the riscv32 `virt`
    # machine, the same QEMU invocation, the same triple, the same toolchain,
    # the same `boot.S`, the same linker scripts, the same `tests/riscv32`
    # payloads, the same root and child images down to the byte. What differs is
    # ONE module: `hal/riscv32-flat/kernel/lib.saw`, whose `prot_*` bodies are
    # empty, whose PMP is opened once to permit everything, and whose
    # `prot_isolated()` answers `false`.
    #
    # **IT IS LAST IN THIS LIST DELIBERATELY.** The architectures run in list
    # order and each prints a contiguous block, so appending here leaves the
    # riscv32 and arm64 sections exactly where a reader of an older transcript
    # left them — which is design 35's fence: the tier word and the case sorting
    # are required to cost the isolated profiles nothing, and a diff is how that
    # is checked.
    #
    # **WHY A THIRD PROFILE RATHER THAN A BUILD FLAG.** The flat/isolated choice
    # has to change the VALUES of constants that `kernel/core`'s `static_assert`s
    # and `load_domain`'s folded `if` read, and this toolchain has no
    # conditional-compilation mechanism at all — no `--define`, no `cfg`, and the
    # only build-side switches in this file are `--target-features` and
    # `--module-path`. A module swap is therefore not one option among several;
    # it is the mechanism the language gives, and it happens to be the honest
    # one: the profile is a HAL, so it is a HAL directory.
    {
        "name": "riscv32-flat",
        "tier": TIER_FLAT,
        # The same machine, so the same everything the machine decides.
        "triple": "riscv32-unknown-none-elf",
        "qemu": "qemu-system-riscv32",
        "qemu_args": ["-M", "virt", "-bios", "none"],
        "cc_args": ["-march=rv32imac_zicsr", "-mabi=ilp32"],
        "features": "+m,+a,+c",
        # `hal=` is THIS profile's own module; `rv32core` and `rv32virt` are the
        # arch half and the board half it re-exports from. `hal_native` and
        # `hal_asm` are the shared riscv32 ones, unchanged — the trap entry and
        # the CSR sinks are facts about the ISA and this profile does not touch
        # them.
        "hal_native": os.path.join("riscv32-common", "kernel"),
        "hal_modules": [RV32_CORE_MODULE, RV32_VIRT_MODULE],
        "hal_asm": [os.path.join("riscv32-common", "kernel", "trap.S")],
        # `boot.S` and `virt.ld` come from the BOARD, because they are the
        # board's: where the stack is, where the payload section lands, where
        # RAM begins. A flat profile changes none of that. There is deliberately
        # no copy of either under `hal/riscv32-flat/`.
        "hal_board": os.path.join("riscv32", "kernel"),
        # The hand-assembled payloads under `tests/riscv32/` are riscv32
        # instructions, so they are this profile's too.
        "tests_arch": "riscv32",
        # **THE ONE THING THAT MUST NOT BE SHARED.** Two profiles on one triple
        # would otherwise write their kernel objects into the same
        # `.build/<triple>/sos/`, and the second would silently relink the
        # first's — the exact hazard the esp32c3 section below documents and
        # dodges the same way.
        "build_tag": "riscv32-unknown-none-elf-flat",
        # The BANNER names the architecture, not the profile: the kernel prints
        # "riscv32 (QEMU virt, flat profile)" and this is matched as a prefix of
        # it, so a case asserts the same fact on all three profiles while a human
        # reading a transcript can still see which kernel wrote it.
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

# How big a child's destination region is, on both profiles.
#
# THE SAME 256 KiB ROOT GETS, and for the same reasons: 240 KiB of image plus
# the kernel's 16 KiB stack grant at the top, which is `hal.ROOT_STACK_LEN` and
# is the one stack length v1 has (a per-create length is unit 4/5 vocabulary).
# Keeping the two the same size is what lets one number here and one `ORIGIN`
# per child linker script express the whole layout.
CHILD_REGION_LEN = 0x40000

# The BOOT REGION TABLE's wire format (sawos design 2 D-2, VERSION 2 by design
# 6 D-5), frozen: an 8-byte header — magic, version u16, count u8, reserved u8 —
# then `count` rows of {base: u64, len: u64, kind: u8, reserved x 7}, all
# little-endian. The header's size is what puts the first row on an 8-byte
# boundary and the row's own padding is what keeps the next one there, which is
# what lets the kernel overlay a struct on the section instead of assembling
# bytes. Kept in step with `kernel/core/process.saw`'s `RegionTableHeader` /
# `RegionRow` / `RegionKind` and the two `static_assert`s beside them.
#
# THE KERNEL ACCEPTS EXACTLY VERSION 2 — no v1 arm — so this constant and that
# file move in one commit. What survives the bump untouched is every image with
# NO table at all: the kernel's `len == 0` short-circuit runs ahead of the
# header read, which is why 122 of the suite's rows never see this format.
REGION_TABLE_MAGIC = 0x4E475253          # 'S','R','G','N' read little-endian
REGION_TABLE_VERSION = 2

# The kind column's values, mirroring `kernel/core/process.saw`'s `RegionKind`.
# A Ram row mints a Memory and a Device row mints an IoMemory, which is design
# 6 D-1's kind-not-a-flag ruling arriving at the one place a window enters the
# system.
REGION_KIND_RAM = 0
REGION_KIND_DEVICE = 1


def arch_dirs(arch):
    """The per-profile directories a build reaches into.

    `hal_kernel` is where the `hal` MODULE lives. `hal_board` is where the
    BOARD's native half lives — `boot.S` and the linker script — and it is the
    same directory for every profile that is a board in its own right. `hal_native`
    is where this architecture's `sink.c` lives, which is the same directory for
    arm64 and the SHARED `riscv32-common` one for riscv32 (design 23).

    **THE THREE `.get` DEFAULTS ARE WHAT MAKES A PROFILE POSSIBLE** (sawos design
    35). A profile that is a variant of an existing board — riscv32-flat, whose
    only difference from riscv32-virt is that it programs the hardware to deny
    nothing — shares that board's `boot.S`, its linker script and its `tests/`
    payloads, and differs only in the `hal` module it compiles and the `.build/`
    directory it writes into. Every default below is the value the two original
    entries already had, so neither of them changes by one byte.

    `build` is keyed on `build_tag` rather than on the triple for the hazard the
    esp32c3 section names one screen down: two profiles can share a target triple,
    and a shared `.build/<triple>/` would let one silently relink the other's
    objects.
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

    A case says `entry={entry}`, not `entry=0x80200000`, because the FACT under
    test is "the entry the image declared came through intact" and the digits
    are the target's word width.
    """
    width = arch["hex_width"]
    return {
        # `banner_arch` is the ARCHITECTURE's name, which is the profile's name
        # for a profile that is a board in its own right and the BOARD's for a
        # variant of one (sawos design 35). The kernel prints `hal.arch_name()`
        # and this is matched as a PREFIX of it, so riscv32-flat's banner —
        # "SOS M1: kernel up on riscv32 (QEMU virt, flat profile)" — is asserted
        # by the same string the isolated profile asserts, while a reader can
        # still see which kernel wrote the line.
        "banner": f"SOS M1: kernel up on {arch.get('banner_arch', arch['name'])}",
        # **THE TIER WORD, AS A CASE WRITES IT** (sawos design 35). A case says
        # `tier={tier}` and gets `Isolated` on the two protected profiles and
        # `Flat` on the flat one — the same mechanism `{entry}` and `{banner}`
        # use, and for the same reason: the FACT under test is "the platform
        # advertised what it can actually deny", and the word is the target's.
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
        # sawos design 3 D-6: the getters MINT now, so handle counts rose and
        # the placeholder list had to grow past seven. `ten` is thread-basics,
        # where three threads each derive their own Process and Thread handles.
        "eight": f"0x{8:0{width}x}",
        "nine": f"0x{9:0{width}x}",
        "ten": f"0x{10:0{width}x}",
        "prio": f"0x{0x01010100:0{width}x}",
        "irq_line": f"0x{arch['selftest_line']:0{width}x}",
        # M3 unit 4: the byte `child-touch` writes into the page its launcher
        # mapped and reads back, which is ALSO its exit code — so
        # `map_into_child` can assert the round trip through the kernel's exit
        # line as well as through the child's own console line and root's §8
        # status word. A number that appears nowhere else in any transcript.
        "touch_mark": f"0x{0x3C:0{width}x}",
        # sawos design 17: the three exit codes the fused-path cases assert.
        # Each is a number that appears nowhere else in any transcript, on
        # `touch_mark`'s own reasoning — a code a launcher asserts should not be
        # producible by any other ending in the tree.
        #
        #   pingpong_mark  every round trip's reply matched its own request
        #   caller_mark    BOTH parked fused calls came back `PeerClosed`
        #   orphan_mark    the thread whose call was answered ended the process
        "pingpong_mark": f"0x{0x21:0{width}x}",
        "caller_mark": f"0x{0x33:0{width}x}",
        "orphan_mark": f"0x{0x3D:0{width}x}",
        # sawos design 22: the same shape one spelling over — the thread whose
        # RESOLVE was answered ended the process, leaving its sibling parked in
        # one. A number that appears nowhere else, on `orphan_mark`'s reasoning.
        "resolve_orphan_mark": f"0x{0x47:0{width}x}",
        # M3 unit 6: the byte ROOT writes into the shared page, which is also
        # `child-share`'s exit code — so `share_double_map` asserts the first
        # direction of the round trip through the kernel's exit line as well as
        # through root's §8 status word. Its partner (0x5A, the byte the CHILD
        # writes) needs no placeholder: it comes back as a decimal on a console
        # line rather than as a machine word.
        "share_mark": f"0x{0xA5:0{width}x}",
    }


# =============================================================================
# The seam check (design 162 unit 1)
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
    # Design 178 M2 unit 1 brought two more classes of machine name within
    # reach of the arch-free kernel — the interrupt controllers and the timers
    # — so the scan learns them at the same time as the code that could leak
    # them. Each is spelled tightly enough not to fire on English: `gic` alone
    # would match "magic", which the loader's own diagnostics say.
    "plic", "clint", "gicd", "gicc", "gicv", "sgir",
    "mtime", "cntp", "cntfrq", "sstc",
]

# A token that is BURIED IN A LONGER ENGLISH WORD is not a leak — and the note
# above turned out to be optimistic about how tight these spellings are. `plic`
# is inside "duplicate", "explicit", "implicit", "complicated" and "replica";
# design 178 M2 unit 3 rider 4 hit the first of those, and the check would
# otherwise have dictated the kernel's vocabulary, which is exactly backwards
# for a check that exists to police the kernel's DEPENDENCIES.
#
# THE RULE: a hit is suppressed only when the token has a letter on BOTH sides.
# That is deliberately weaker than a word boundary, because the real usages are
# prefixes and suffixes of longer identifiers — `mtimecmp`, `csrw`, `PLIC_BASE`,
# `gicd_ctlr`, `cntfrq_el0` — and requiring a boundary on both sides would miss
# every one of them. An arch token in the MIDDLE of an otherwise-alphabetic word
# is English; at either END of one it is an identifier.
def _is_english_embedding(line, token, at):
    before = line[at - 1] if at > 0 else ""
    after_index = at + len(token)
    after = line[after_index] if after_index < len(line) else ""
    return before.isalpha() and after.isalpha()

# ANSI colors (matched to test_runner.py's style; disabled when not a TTY).
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
# architecture's own `sos/tests/<arch>/` directory, one or more expected console
# substrings (or None), and whether the emulator should exit cleanly (True →
# status 0) or fail (False). Expected strings are `str.format`ed with
# `expectations(arch)`.

TEST_CASES = [
    {
        # The kernel exists to hand control to root. Built with no image
        # appended it must say so and FAIL — never exit quietly as if the
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
        # Design 172 unit 4: the panic-recursion pin. The message comes from
        # the COMPILER's bounds check, so the reporter is entered from the trap
        # path — the one that would recurse if the writer under it could panic.
        # Asserting the prefix AND the reason is what catches a garbled report;
        # asserting a non-zero exit is what catches a hung one.
        # The location is the TRAPPING expression's own (design 122), which for
        # an indexed accessor is inside std — so this asserts `vector.saw`, not
        # the test's file. That is the point: three independent pieces (prefix,
        # location, reason) all arriving means nothing re-entered the writer
        # mid-report.
        "name": "panic_from_check",
        "src": os.path.join(TESTS_DIR, "panic_from_check.saw"),
        "expect_out": ["panic at ", "vector.saw:",
                       "Vector.[]: index out of range"],
        "expect_clean_exit": False,
    },
    {
        # design 158 unit 3: the in-process task dump, freestanding. The kernel
        # walks its OWN task slots through the in-binary backtrace table and
        # writes each parked task's logical stack to the serial port — the whole
        # reason the design exists, since a kernel has no debugger to attach and
        # no core to open. Both halves are asserted, in order: the explicit
        # `dump_tasks()` with its two-frame nest, then the panic line, then the
        # dump the PANIC PATH emits by itself. The nest is two frames deep since
        # design 187 closed DF-158e — a freestanding compile now embeds a nested
        # suspending callee exactly as a hosted one does, so the dump has a
        # logical stack to reconstruct rather than a single frame. It also
        # closed DF-158c, which had made this case arm64-only: an `@export`ed
        # `Int64`-returning seam came out `i32` on a 32-bit target, so the clock
        # stub and the executor's own `Int64` clock arithmetic disagreed and
        # LLVM rejected the module. BOTH arches run it now.
        "name": "task_dump",
        "src": os.path.join(TESTS_DIR, "taskdump.saw"),
        "csrc": "taskdump_stubs.c",
        "expect_out": ["saw tasks: 2 live (unsynchronized snapshot)",
                       "at taskdump.saw:93 in knap",
                       "at taskdump.saw:98 in ksleeper",
                       "panic at taskdump.saw:114: SOS task dump: deliberate panic",
                       "saw tasks: 1 live (as-of panic, unsynchronized)",
                       "at taskdump.saw:93 in knap",
                       "at taskdump.saw:98 in ksleeper"],
        "expect_clean_exit": False,
    },
    {
        # design 158 unit 3: the dump path on EVERY architecture — the table is
        # in the image, the walker links and runs freestanding, and a panic with
        # no live task still prints exactly its own message and nothing else.
        "name": "task_dump_empty",
        "src": os.path.join(TESTS_DIR, "taskdump_empty.saw"),
        "expect_out": ["saw tasks: none live",
                       "panic at taskdump_empty.saw:21: "
                       "SOS task dump: no tasks here"],
        "expect_clean_exit": False,
    },
    # --- design 140 unit A: the privilege split, without any image format ----
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
        # ISOLATED-ONLY (sawos design 35). The payload stores into the kernel's
        # own .text, which it was never granted, and the ASSERTION IS THE FAULT.
        # A flat platform permits the store, the payload runs on, and the line
        # is missing rather than wrong — which is the failure mode this case was
        # engineered for and exactly why it cannot be asked here.
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
        # design 178's faults ruling (Aug 15), which REVERSED this case: a
        # caller's mistake is a FAULT, not a status. The payload makes a good
        # call (the '!' proves the round trip resumed) and then an op the
        # System object does not have, which terminates it — so the transcript
        # is the ruling in three lines, in order, and the exit status is the
        # kernel's own `ExitCode.ProcessFault` rather than anything the process
        # chose. The teardown line is what says the KERNEL stayed up to report.
        "name": "umode_bad_calls",
        "src": os.path.join(TESTS_DIR, "umode.saw"),
        "asm": "payload_badcall.S",
        "expect_out": ["SOS M1: entering U-mode", "!",
                       "SOS: process fault: bad op",
                       "SOS: process teardown handles={three} threads={one}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    # --- design 178 M2 unit 1: interrupts -----------------------------------
    # Three claims, one per case, and each is read from the ORDER of the
    # transcript rather than from any single line: a tick lands in the
    # arch-free hook once user mode is running; a tick that comes due in the
    # kernel is not taken there; and a device line goes round the interrupt
    # controller's claim/complete cycle. All three run the same spinning
    # payload, which shuts the machine down cleanly when it is done — so a
    # kernel that wedged in its handler shows up as a timeout, not a pass.
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
        # design 178 D2. The middle line is the whole case: the kernel spun
        # until the timer was DUE, and the tick counter is still zero — so the
        # interrupt was pending and untaken while the kernel ran. The tick
        # arrives after the entry to user mode, and ordered matching is what
        # makes "after" an assertion.
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
    # --- sawos design 1 (M3 unit 1.5): kernel interruptibility ---------------
    # The two cases above are D2's witnesses: an interrupt that comes due while
    # the kernel runs is not TAKEN there. The two below are design 1's, and they
    # invert those assertions rather than replacing them — the kernel still takes
    # no trap in kernel mode, and now it ASKS at named points inside long
    # operations, so the same interrupt is SERVICED there.
    #
    # Both run the SHIPPED mover (`kcore.preempt.long_zero`, which is what the
    # loader places a root image with) over a kernel scratch buffer. A test-only
    # loop with a point in it would prove that a point can work rather than that
    # the one the kernel ships does.
    {
        # timer_mask's mirror, over the comparator. The exit condition IS the
        # proof: the section runs until the tick COUNTER moves, and in kernel
        # mode nothing but a preemption point can move it — a hardware delivery
        # here is each HAL's kernel-bug path. So a broken poll is a timeout, not
        # a wrong number, and the ticks-taken line asserts the count as well as
        # the order.
        #
        # `at {one}` is `PREEMPT_PC`, the sentinel a tick taken at a point
        # reports (the idle poll's is zero, and a real interrupted PC is even on
        # both profiles). Asserting it is what says WHICH of the three
        # deliveries ran.
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
        # extirq's mirror, over the interrupt controller — and the ORDER is the
        # inversion: that case asserts the report lands after the entry to user
        # mode, this one asserts it lands before, from inside the kernel section.
        # Ordered matching is what makes "before" an assertion.
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
    # --- design 140 unit B: the sosimg format and the kernel's loader -------
    # These images are assembled by hand (sos/tests/<arch>/payload_*.S), so they
    # pin the format independently of Blade's emitter — two producers, one
    # loader, and on two architectures the SAME 16-byte header.
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
        # Design 172 unit 8: a version bump is a REFUSAL boundary. The payload
        # is a real v2 image — correct magic, right arch, sane permissions —
        # and a v3 loader must stop at the version rather than guess at a
        # record whose address field is a different width in a different place.
        "name": "root_image_bad_version",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "asm": "payload_badversion.S",
        "expect_out": "bad root image: unsupported version",
        "expect_clean_exit": False,
    },
    {
        # Design 162 unit 3: one format, two profiles. An image whose header is
        # correct in every other way but says it was built for the other
        # machine is refused on the tag, before a byte is copied.
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
    # --- design 140 unit C: the real two-image boot ------------------------
    # Kernel and root are separate builds — separate linker scripts, separate
    # load addresses, root built by Blade from its own package manifest — and
    # meet only as an appended blob the kernel parses. Since design 162 the
    # SAME root sources build for both profiles: only the manifest's
    # `[sos.<triple>]` section differs.
    {
        "name": "root_server_boot",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": ROOT_PKG,
        "expect_out": ["{banner}",
                       "root image ok segments={two}",
                       "prio={prio}",
                       # The typed SAW altitude: a method on a handle.
                       "SOS root: hello from U-mode via a System op",
                       # The RUNTIME SEAM altitude, end to end: `print` ->
                       # `__saw_rt_write` (sosrt) -> `sos_rt_write` (sysapi) ->
                       # `sos_system_debug_print` -> `sos_syscall1` -> `ecall`.
                       # Every hop but the last is Saw since design 172 part 2;
                       # before it, the middle of that chain was C and this line
                       # was described as exercising the C altitude. It no
                       # longer does — see DF-172i.
                       # Also design 137 formatting with no allocator present.
                       "SOS root: boot handle 1"],
        "expect_clean_exit": True,
    },
    # --- design 178 M2 unit 2: Thread and Process objects + the scheduler ----
    # Three claims, one per case, and the three root servers are real Blade
    # packages that reach the kernel through the `sos` module — so what is under
    # test is the object surface a process actually has, not an op number
    # written into an assembler payload.
    {
        # (a) + (c): create two threads, start them, join them for THEIR OWN
        # exit values — and, in between, cooperative alternation. The kernel
        # here arms NO timer, so nothing can take the processor away and the
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
                       # The ratified Process teardown, reported. TEN handles
                       # since sawos design 3 D-6, and the arithmetic is the
                       # mint-per-call claim at its loudest: the three §12 boot
                       # handles, the two Threads this process created, and then
                       # FIVE derivations — each of the three threads asks the
                       # System object which Process it is, and the two workers
                       # additionally ask the Process which Thread they are.
                       # Under the old getter model those five reads answered
                       # cached words and minted nothing, which is what made
                       # this five. Nothing is released before the exit, so
                       # every one of them is still bound at teardown.
                       # The two trailing counts arrived with M2 unit 3 and are
                       # asserted here too — a process that made no Event and no
                       # Waiter must report none, which is the null row of the
                       # same claim the Event cases below make with real objects.
                       "SOS: process teardown handles={ten} threads={three} "
                       "events={zero} waiters={zero}"],
        "expect_clean_exit": True,
    },
    {
        # (b) PREEMPTION. The same shape with the yields REMOVED and the timer
        # armed: two workers that print and then spin, making no call that could
        # give the processor up. Every alternation in the transcript is the
        # scheduler taking it away at a tick, which is the whole of design 178
        # D3 that a cooperative test cannot show.
        #
        # The root's own banner is deliberately NOT asserted: it is written a
        # byte per syscall while the kernel is still narrating its first ticks,
        # so the two interleave mid-word on the console. That is real and
        # harmless — a shared serial port with no locking — but it is not
        # something a substring can match. The tick narration stops after four,
        # so everything below is written in the quiet that follows.
        "name": "thread_preempt",
        "src": os.path.join(TESTS_DIR, "threads_timer.saw"),
        "root_pkg": THREAD_PREEMPT_PKG,
        #
        # THE ALTERNATION IS ASSERTED AS DIRECTION CHANGES, not as a fixed
        # sequence, and that is not a weakening. WHICH worker runs first depends
        # on where the first tick lands relative to the two `start` calls, so a
        # sequence starting `A` fails half the time on a claim it was never
        # making. `AB` then `BA` then `AB` says the processor crossed between
        # the two threads at least three times, whoever went first — and a run
        # with no preemption at all reads `AAAAAAAABBBBBBBB`, which has one `AB`
        # in it and no `BA` after it.
        #
        # **AND IT PRINTS THE INTERRUPT COLUMN, UNASSERTED** (sawos design 16).
        # This is the one case in the tree whose whole point is that ticks land
        # in user mode, so it is the one place the column has something to show —
        # every alternation above is one of them. The row is NOT in the list
        # below, deliberately: a tick lands where the host puts it, so asserting
        # the number would be asserting the weather. It joins the two
        # timing-dependent rows this case already carries.
        "expect_out": ["SOS M2: preemptive kernel up on",
                       "AB", "BA", "AB",
                       "SOS preempt: joined a=33 b=44"],
        "expect_clean_exit": True,
    },
    {
        # (d) THE FAULTS RULING, on a handle the process never held. It ends the
        # process; the kernel reports the reason, reports the teardown, and
        # stops the machine with its OWN exit code.
        #
        # IT IS A PAYLOAD RATHER THAN A ROOT SERVER, and that is the second
        # review round showing up in the harness: the `sos` module's typed layer
        # has no way to build a `Thread` from a word, so a handle the process
        # was never given is not a thing a Saw program can spell. The test of
        # the kernel's validation therefore lives at the altitude where raw
        # handles legitimately live, beside `umode_bad_calls`.
        "name": "umode_bad_handle",
        "src": os.path.join(TESTS_DIR, "umode.saw"),
        "asm": "payload_badhandle.S",
        "expect_out": ["SOS M1: entering U-mode",
                       "SOS: process fault: bad handle",
                       "SOS: process teardown handles={three} threads={one}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    # --- design 178 M2 unit 3: the Event and Waiter objects -----------------
    # Three claims, and the split between the first two is deliberate: what an
    # Event ACCUMULATES and what a Waiter REPORTS is one question, and whether a
    # wait PARKS is another. Testing them together would let a scheduling bug
    # hide behind an accumulation bug and the other way round.
    {
        # Level-triggered attach (§2.2), OR and saturating-sum accumulation
        # (§2.4), and the key identifying WHICH of several attachments became
        # ready. One thread, no timer: every wait here answers immediately, and
        # a wait that did not would park the only thread in the system, which
        # the kernel reports as the deadlock it is — so a regression fails in
        # microseconds rather than at the timeout.
        "name": "event_basics",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": EVENT_BASICS_PKG,
        "expect_out": ["{banner}",
                       # The signal came BEFORE the wait and was not lost.
                       # `at-wait` is read out of the RECORD the kernel copied
                       # into the process's memory, and it is the accumulated
                       # word rather than a readiness flag — 4, then 8, then
                       # 1|2|1, then a count of five.
                       #
                       # `after` is the `receive` that FOLLOWS each wait, and it
                       # reads zero every time: the delivery took the word (the
                       # Aug-17 ruling). The `detached` line below is the same
                       # call answering a real number when no wait took it
                       # first, which is what says these zeroes are a take
                       # rather than a broken drain.
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
                       # word there is, which SATURATE instead of wrapping back
                       # through zero — the value that means "not ready".
                       "SOS events: counting key=33 at-wait=5 after=0 "
                       "saturated=1",
                       "SOS events: done",
                       # Teardown reports the two new object kinds: three events
                       # and one waiter, beside EIGHT handles and one thread.
                       # Eight rather than seven since sawos design 3 D-6:
                       # `process_self()` MINTS now, where it used to answer a
                       # word cached on the process slot. The +1 is that mint,
                       # and it is the same +1 on every single-threaded case
                       # below.
                       "SOS: process teardown handles={eight} threads={one} "
                       "events={three} waiters={one}"],
        "expect_clean_exit": True,
    },
    {
        # THE PARK AND THE WAKE. Read from the ORDER: the middle line is written
        # by a thread that could only have run because the first one blocked,
        # since this kernel arms no timer and `start` does not switch. The line
        # after it carries the record back into the woken thread's own stack
        # buffer, written by the SIGNALLING thread through the copy-out funnel —
        # unit 2's block-on-wait substrate answering a syscall it did not answer
        # when the call was made.
        "name": "event_wake",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": EVENT_WAKE_PKG,
        "expect_out": ["{banner}",
                       "SOS wake: worker started, parking",
                       "SOS wake: worker signalling",
                       # ONE PLUS FOUR IS FIVE, and that arithmetic is the third
                       # claim. The worker signals five times; the FIRST wakes
                       # the parked thread and the delivery TAKES that 1 (the
                       # Aug-17 ruling), so the other four accumulate from zero
                       # and the `receive` after the wake reads 4. Nothing lost,
                       # nothing counted twice — where the old snapshot
                       # semantics answered 1 and 5.
                       "SOS wake: woke key=77 at-wait=1 after=4",
                       "SOS wake: joined worker=99"],
        "expect_clean_exit": True,
    },
    {
        # THE FAULT CASE: attaching something that is not a waitable. A payload
        # rather than a root server, for the reason `umode_bad_handle` is one —
        # `Waiter.add` takes an `&Event` and the typed layer has no way to build
        # one from a word, so this is not a thing a Saw process can spell. The
        # teardown line is the second half: the Waiter the process DID make goes
        # back to the kernel's slab.
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
        # KEYS ARE UNIQUE PER WAITER (rider 4). Two Events, one key, and the
        # second `add` terminates the process — the invariant that `remove(key)`
        # and the wait record both rest on.
        #
        # A ROOT SERVER RATHER THAN A PAYLOAD, and it is the only fault case in
        # this unit that can be one: the typed layer makes the others
        # unspellable (no handle you were not given, no buffer you name), while
        # a duplicate key is two real Events and two of the caller's own words,
        # which no type can catch. So the check is the kernel's, and the test
        # lives where a real process would hit it.
        "name": "event_dupkey",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": EVENT_DUPKEY_PKG,
        "expect_out": ["{banner}",
                       "SOS dupkey: first attach ok",
                       "SOS: process fault: an attachment already uses that key",
                       # Seven handles — the three it was given, the Process
                       # handle `process_self()` MINTED (sawos design 3 D-6:
                       # +1 where a cached word used to be reused), a waiter and
                       # two events — and both events and the waiter still go
                       # back to their slabs.
                       "SOS: process teardown handles={seven} threads={one} "
                       "events={two} waiters={one}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    # THE COPY-OUT FUNNEL'S TWO REJECTION ROWS. `Waiter.Wait` answers by writing
    # a record into memory the caller supplies — SOS's first
    # kernel-writes-userspace path — and the funnel that validates the
    # destination is the only door. One case per row, because a funnel with a
    # matrix owes its tests row by row.
    #
    # Both are payloads for the reason `umode_bad_handle` is one: the typed
    # `Waiter.wait` supplies its own buffer out of its frame and never lets a
    # caller name one, so neither mistake is a thing a Saw process can spell.
    {
        # ROW 1 — outside the process's writable memory. This payload has NO
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
        # ROW 2 — not word-aligned. The kernel writes machine words, so this is
        # refused before the range is even looked at; the two cases are told
        # apart by that ORDER, since this payload's address would fail the range
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
    # --- design 178 M2 unit 4: the Interrupt object + the userspace UART echo -
    # THE MILESTONE'S PROOF, and it is worth naming what each line of the
    # transcript rules out. The harness types four bytes AT the guest's serial
    # port and asserts they come back out of it AFTER the kernel's handover
    # marker. For that to happen: the image's declared device window was granted
    # (or the driver's first register read faults), the line was bound and
    # unmasked (or nothing ever fires), the kernel's idle path noticed an
    # interrupt with no thread runnable (or the kernel reports a deadlock in
    # microseconds), the wait answer carried the right key and the line, the
    # driver reached the receiver through the window, and the ack unmasked so
    # the next byte could arrive. A missing byte leaves the driver parked and
    # the case fails on the emulator's timeout rather than quietly.
    #
    # TWO CASES, ONE PER DEVICE. A driver names its device, so the two profiles
    # get two packages, named for the chip; the line number in the last line is
    # the board's and is asserted because it came back through the WAIT RECORD's
    # payload rather than from the program's own constant.
    #
    # **MIGRATED IN M3 UNIT 4 (sawos design 6 D-6), AND THESE ARE THE ONLY TWO
    # EXISTING CASES THE UNIT AUTHORIZED TO MOVE.** The window is no longer
    # DECLARED in the manifest and installed before user mode; it is a DEVICE row
    # of the build's region table, minted as an `IoMemory`, drained from the boot
    # set and mapped into the driver by the driver. Three lines change per case
    # and each is accounted for here:
    #
    #   segments={three} -> {two}   the third segment WAS the declaration
    #   + boot regions={one}        the kernel now mints one object at boot
    #   + "window mapped"           the driver says it obtained what it used to
    #                               be handed
    #
    # **THE ECHO BEHAVIOUR IS UNCHANGED — same bytes, same line number** — which
    # is the migration proving "the same window, obtained rather than declared"
    # (spec §2.5's migration case, come true). Everything the M2 transcript
    # ruled out, this one still rules out.
    #
    # **AND THESE TWO STAY, UNMOVED, PAST M3 UNIT 6** (sawos design 9 D-2). The
    # driver body below now also runs as a CHILD (`child_echo_ns16550` /
    # `child_echo_pl011` at the end of this file, launched by `driver-child`),
    # and keeping both is the claim: the same program in two transcripts, one as
    # root and one as a child, says the difference is the LAUNCHER rather than
    # the driver. Neither of these two rows moved for that unit.
    {
        "name": "uart_echo_ns16550",
        # **AND `riscv32-flat`, WHICH IS THE SAME MACHINE** (sawos design 35).
        # An `arches` list names the profiles a case applies to, and a riscv32
        # profile added after this list was written is not named by it — so
        # widening these five is what stops a NEW profile from silently running
        # fewer cases than the one it is a variant of. Nothing about the reason
        # each case is riscv32-only (a per-DEVICE driver package; a `PROT_GRAIN`
        # arithmetic) distinguishes the two profiles: they share the device, the
        # grain and the triple.
        "arches": ["riscv32", "riscv32-flat"],
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": UART_ECHO_NS16550_PKG,
        "device": True,
        "stdin": ECHO_INPUT,
        "expect_out": ["{banner}",
                       # TWO segments: code and data. The third was the device
                       # window the manifest declared, and it is gone.
                       "root image ok segments={two}",
                       # ONE boot region: the DEVICE row. The kernel minted an
                       # IoMemory for it and queued the record the driver drains.
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
    # The Interrupt object's two caller-checkable refusals. Both are ROOT
    # SERVERS rather than payloads, for rider 4's reason: a line number and an
    # ack are ordinary things a Saw driver writes, so no type can catch either
    # and the check has to be the kernel's — which puts the test at the altitude
    # a real driver would hit it from. Both line numbers mean the same thing on
    # either board, so one source serves both profiles.
    {
        "name": "irq_bad_line",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": IRQ_BADLINE_PKG,
        "expect_out": ["{banner}",
                       "SOS badline: binding a line this board lacks",
                       "SOS: process fault: argument outside its domain",
                       # Four rather than three since sawos design 3 D-6: the
                       # `process_self()` this case makes before it binds now
                       # MINTS a handle instead of answering a cached word.
                       "SOS: process teardown handles={four} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        "name": "irq_early_ack",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": IRQ_EARLYACK_PKG,
        # The teardown counts the Interrupt the process DID bind, and masking
        # its line is part of the same reclaim — a device the dead process was
        # driving must not be able to interrupt a kernel with nothing to
        # deliver it to.
        "expect_out": ["{banner}",
                       "SOS earlyack: bound, acking without a fire",
                       "SOS: process fault: object in the wrong state",
                       # Five rather than four: the design-3 D-6 `process_self`
                       # mint, as everywhere in this file.
                       "SOS: process teardown handles={five} threads={one} "
                       "events={zero} waiters={zero} interrupts={one}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # NOTHING RUNNABLE, AND NOTHING THAT COULD EVER MAKE SOMETHING
        # RUNNABLE. Unit 4 made this state conditional — with a bound IRQ line
        # the kernel now idles instead of reporting — so the branch that did
        # NOT change is pinned here, beside the echo case that proves the one
        # that did. Without it, widening the idle rule could quietly swallow a
        # real deadlock and turn every such bug into a harness timeout.
        #
        # It also protects an assumption `event_basics` relies on out loud: a
        # wait there that stopped answering immediately would park the only
        # thread in the system, and this is the report that makes that fail in
        # microseconds rather than at the timeout.
        "name": "wait_deadlock",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": WAIT_DEADLOCK_PKG,
        "expect_out": ["{banner}",
                       "SOS deadlock: waiting for a signal nobody sends",
                       "SOS: process fault: every thread blocked",
                       # Six rather than five: the design-3 D-6 `process_self`
                       # mint.
                       "SOS: process teardown handles={six} threads={one} "
                       "events={one} waiters={one} interrupts={zero}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    # --- design 232 M3 unit 1: the Clock and Timer objects -------------------
    # THE MILESTONE'S FIRST CLAIM IS THAT A PROCESS CAN SLEEP. Before this unit
    # a wait either returned at once or blocked forever, because every wake in
    # the system came from a thread or from a device line. Six cases, split so
    # that what an object ANSWERS and whether a wait PARKS cannot cover for each
    # other.
    {
        # The Clock alone, with NO waiting anywhere: every call answers
        # immediately, so a regression that parked would park the only thread in
        # the system and be reported as the deadlock it is — in microseconds
        # rather than at the timeout. (`event_basics` uses the same trick.)
        "name": "clock_basics",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": CLOCK_BASICS_PKG,
        "expect_out": ["{banner}",
                       # ASKING TWICE MINTS TWICE, which is sawos design 3 D-4
                       # replacing this row's old `same=1` claim. `minted=1` is
                       # the second ask succeeding and `differs=1` is its WORD
                       # being a different one — two capability INSTANCES onto
                       # the machine's single kernel-eternal Clock, each
                       # independently owned and independently releasable. It
                       # amplifies nothing: `SystemRight.ClockGet` gates the
                       # minting and every mint carries the kind's default
                       # rights. The teardown line below still carries no
                       # `clocks=` column, and for the unchanged reason: a
                       # process owns no clock, so a dead one frees none.
                       "SOS clock: minted=1 differs=1",
                       "SOS clock: monotonic=1",
                       # `advanced` alone would pass on a counter of anything;
                       # `spanned` is what says the units are NANOSECONDS.
                       "SOS clock: advanced=1 spanned=1",
                       # The cancel idiom's precondition: disarming a Timer that
                       # was never armed must NOT fault, or a timeout that has
                       # already expired could not be cancelled safely.
                       "SOS clock: disarm_unarmed=1",
                       "SOS clock: done",
                       # EIGHT, AND THE ARITHMETIC IS THE MINT-PER-CALL CLAIM IN
                       # A NUMBER: the three §12 boot handles, the Process
                       # handle `process_self()` minted, THREE Clock handles for
                       # three asks, and the Timer. Under the old getter model
                       # the three asks were one handle and `process_self` was
                       # none, which is what made this five. Nothing here is
                       # released — `handle_drop_release` below is the case that
                       # proves the other direction.
                       "SOS: process teardown handles={eight} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={one}"],
        "expect_clean_exit": True,
    },
    {
        # A PROCESS SLEEPS — the headline. One thread, no scheduler tick, no
        # bound line: at the moment of the wait there is nothing runnable and
        # nothing that could ever make something runnable EXCEPT the clock. So
        # this transcript existing at all is the idle rule's new branch working,
        # and `timer_deadlock` below is the branch that did not change.
        "name": "timer_oneshot",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": TIMER_ONESHOT_PKG,
        "expect_out": ["{banner}",
                       "SOS oneshot: parking on a timer",
                       # `slept=1` is the clock agreeing with itself across the
                       # park, which is what a `wait` that wrongly returned
                       # immediately would fail even with the right count.
                       "SOS oneshot: woke key=55 fires=1 slept=1",
                       # A one-shot DISARMS ITSELF, so the second wake can only
                       # be reached by arming again — no ack, no re-attach.
                       "SOS oneshot: rearmed woke fires=1",
                       "SOS oneshot: done",
                       # Seven rather than six: the design-3 D-6 `process_self`
                       # mint.
                       "SOS: process teardown handles={seven} threads={one} "
                       "events={zero} waiters={one} interrupts={zero} "
                       "timers={one}"],
        "expect_clean_exit": True,
    },
    {
        # THE PERIODIC HALF. Nothing happens between the first two waits — no
        # ack, no re-arm — so a timer that did not re-arm ITSELF would leave the
        # second wait parked with nothing to wake it, which this kernel reports
        # as a deadlock rather than hanging.
        "name": "timer_interval",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": TIMER_INTERVAL_PKG,
        "expect_out": ["{banner}",
                       # NEITHER PERIODIC COUNT IS ASSERTED, and the reason is
                       # the feature under test. A fire delivered more than a
                       # period late COALESCES, by design — so under an emulator
                       # whose timing is the host's business, a correct kernel
                       # legitimately reports 1, 2 or 3 here. Pinning the number
                       # would make coalescing itself the flake — and it WOULD
                       # have flaked: both machines reported `fires=2` on the
                       # first wake through M3 unit 3, and riscv32 reported
                       # `fires=1` at unit 4, where a bigger kernel changed how
                       # much of the first period was spent before the wait. The
                       # count moved and nothing was wrong, which is exactly the
                       # case this comment exists for. What is asserted is that
                       # each wake HAPPENED, with no ack and no re-arm between
                       # them, which `ackfree` carries — and the two counts that
                       # ARE deterministic are pinned below.
                       "SOS interval: tick one fires=",
                       "SOS interval: tick two fires=",
                       "ackfree=1",
                       # Expiries that landed while nobody was waiting did not
                       # queue as separate wakes and were not dropped: they
                       # COALESCED into one count above one.
                       "SOS interval: coalesced=1",
                       # The drain, proved without a stopwatch: cancel, re-arm
                       # one-shot, wait. A kernel that never reset the count
                       # would report the coalesced total here instead.
                       "SOS interval: drained fires=1",
                       "SOS interval: done",
                       # Seven rather than six: the design-3 D-6 `process_self`
                       # mint.
                       "SOS: process teardown handles={seven} threads={one} "
                       "events={zero} waiters={one} interrupts={zero} "
                       "timers={one}"],
        "expect_clean_exit": True,
    },
    {
        # THE BRANCH THAT DID NOT CHANGE, in the new way to get it wrong. A
        # Timer EXISTS and is NOT ARMED, so it can never fire and the system is
        # as deadlocked as one with no Timer at all. A kernel that counted
        # Timers rather than ARMED ones would idle here forever and fail at the
        # harness timeout — the failure mode hardest to read — which is why the
        # report is pinned. (`wait_deadlock` pins the same report with no Timer
        # in the system at all.)
        "name": "timer_deadlock",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": TIMER_DEADLOCK_PKG,
        "expect_out": ["{banner}",
                       "SOS timerdead: waiting on a timer nobody armed",
                       "SOS: process fault: every thread blocked",
                       # Seven rather than six: the design-3 D-6 `process_self`
                       # mint.
                       "SOS: process teardown handles={seven} threads={one} "
                       "events={zero} waiters={one} interrupts={zero} "
                       "timers={one}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # A CLOCK DOMAIN THIS KERNEL CANNOT NAME. `ClockType` is raw-backed so
        # the domain space can grow without renumbering, and v1 declares one
        # value — so any other number is a mistake the caller could have
        # checked, and the faults ruling ends it. It goes through the C altitude
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
        # THE COPY-IN DOOR'S FIRST REFUSAL. `Timer.Arm` is the first op that
        # reads a record OUT of a process rather than writing one in, and the
        # record's size is published — so a wrong length is caller-checkable.
        # The length is checked for EQUALITY, not "at least": a copy-out
        # capacity is a buffer that may be over-provisioned, while a copy-in
        # length is the caller's statement of what it is handing over, and a
        # number that is not the record's size means the two sides disagree
        # about the shape of the thing being passed.
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
    # --- the Aug-17 consume ruling: a wait DELIVERY takes an Event's word ----
    # The two cases split by DOOR, and the split is not cosmetic: the delivery
    # runs a different path in each — a wait that finds something already ready
    # has the waiting thread read its own record, while a wake has the
    # SIGNALLING thread write into a parked thread's frame. `deliver_attachment`
    # is one funnel precisely so those cannot diverge, and a case per path is
    # what that claim costs.
    {
        # THE POLL DOOR, with nothing parking anywhere. A regression that parked
        # would park the only thread in the system and be reported as the
        # deadlock it is, in microseconds rather than at the timeout.
        "name": "event_consume",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": EVENT_CONSUME_PKG,
        "expect_out": ["{banner}",
                       # TWO SIGNALS, ONE RECORD: 1 and 2 land with nobody
                       # waiting, and the wait carries the MERGED word. Per
                       # signal it would read 1.
                       "SOS consume: or key=11 at-wait=3 after=0",
                       # A signal AFTER the take carries only the new bits — 8,
                       # not 1|2|8. This is the number that separates a take
                       # from a copy.
                       "SOS consume: newbits key=11 at-wait=8 after=0",
                       # The same in the counting mode, so the take cannot be a
                       # per-mode accident: three signals of one, one record of
                       # three, and the count restarts from zero.
                       "SOS consume: sum key=22 at-wait=3 after=0",
                       "SOS consume: done",
                       # Seven handles — the three it was given, the Process
                       # handle `process_self()` minted (design 3 D-6), a waiter
                       # and two events — one thread, and both events back on
                       # the slab.
                       "SOS: process teardown handles={seven} threads={one} "
                       "events={two} waiters={one}"],
        "expect_clean_exit": True,
    },
    {
        # THE WAKE DOOR. Two threads, two parks, and the order is forced rather
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
                       # NOTHING. Under the snapshot semantics it read 5.
                       "SOS consumewake: woke first key=41 at-wait=5 after=0",
                       # Reached only because the SECOND wait genuinely parked —
                       # which it can only do if the first wake emptied the
                       # event.
                       "SOS consumewake: worker second signal",
                       # ONLY THE NEW BITS: 2, not 5|2 = 7.
                       "SOS consumewake: woke second key=41 at-wait=2",
                       "SOS consumewake: joined worker=99",
                       # Both waiters and both events go back to their slabs.
                       "events={two} waiters={two}"],
        "expect_clean_exit": True,
    },
    {
        # The grant has to hold against a root that is merely WRONG, not just
        # against one that behaves.
        "name": "root_server_oversteps",
        # ISOLATED-ONLY (sawos design 35): "the grant has to hold" is a claim
        # about what the HARDWARE refuses, and a flat platform holds no grant
        # against anybody. Root reaches for the kernel and simply arrives.
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
    # M3 unit 2 — CreateProcess: the second address space (sawos design 2)
    # =========================================================================
    #
    # Five cases, one claim each, and each claim is INVERTED somewhere above:
    # every earlier case in this file runs in a system with exactly one process,
    # where a process's death was the machine's and a protection domain never
    # had to be put back.
    #
    # All five append a child image and publish a two-row region table, which is
    # what makes them the only cases in the suite whose `_region_table_start`
    # and `_region_table_end` differ. Every case ABOVE this line links no
    # `.regions` section at all, so the kernel reads zero regions and mints
    # nothing — which is why their transcripts are untouched.
    {
        # THE HEADLINE. Root creates a process, starts it, and is still there
        # after it dies. Read the transcript as an ORDER: the two-phase
        # lifecycle (created, then started), root printing AFTER the start
        # (`start()` makes a child runnable, it does not hand the processor
        # over), the child's death naming PROCESS 1, and root's own line after
        # it.
        #
        # `handles={zero} threads={one}` in the teardown is the child's whole
        # estate: a child in this unit is handed nothing, so it owns one thread
        # and not one handle. That number is also the assertion that the
        # teardown ran against the CHILD's table and not root's — root holds
        # nine handles by then.
        #
        # `child status=131073` is the §8 status word root reads out of the dead
        # child through the handle it still holds: kind `Faulted` (2) in the
        # high half, reason `BadHandle` (1) in the low. It is the supervision
        # story working, and it is what makes "the child died, and this is how"
        # a value rather than an inference from the console.
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
        # THE MONEY PROOF. The child stores into ROOT's region and the access
        # faults, which can only happen if the switch INTO the child reloaded
        # the protection domain — riscv32 spends all four PMP regions on one
        # image and arm64's grant window is a single shared EL0 permission map,
        # so neither profile can carry two processes' grants at once.
        #
        # THE ASSERTION IS THE PRESENCE OF THE FAULT. A kernel that left root's
        # rows installed would let the store LAND, the child would spin, and the
        # line would be missing rather than wrong — which is the failure mode
        # worth engineering for.
        #
        # `child status=131072` is `Faulted` with code ZERO, because a hardware
        # fault carries no `FaultReason`: the cause is in the console line. That
        # is the one thing that differs from the lifecycle case's `131073`, and
        # it is what says the two children died in two different ways.
        "name": "process_isolation",
        # ISOLATED-ONLY, AND THE CASE THE TIER WORD IS ABOUT (sawos design 35).
        # This is the money proof named four comments up, and the property it
        # proves — a peer cannot reach another process's memory — is precisely
        # the one design 19 says the flat tier DISCLAIMS. Running it there would
        # not be a stricter test, it would be a test of a promise nobody made:
        # the child's store lands in root's stack, the child spins in the
        # `while { }` its own source calls unreachable, and the case burns its
        # ten-second timeout to discover the tier word was telling the truth.
        "tier": TIER_ISOLATED,
        "tier_reason": "peer isolation is the property the Flat tier disclaims "
                       "BY NAME (design 19) — the child's poke would land",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PROCESS_ISOLATION_PKG,
        "children": [CHILD_POKE_PKG],
        "expect_out": ["{banner}",
                       "SOS isolation: started",
                       # The kernel's hardware-fault report. The tag and the
                       # cause differ per machine — a store access fault here, a
                       # data abort there — so what is asserted is that ONE was
                       # taken, and the teardown below says whose it was.
                       "SOS: fault ",
                       "SOS: process teardown handles={zero} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS isolation: root survived child status=131072",
                       # **THE SWEEP ROW** (sawos design 16), and it is
                       # `child-poke`'s whole biography in two numbers: it holds
                       # no handles, so it never reached the kernel through the
                       # syscall door at all, and the ONE trap it took was the
                       # store the hardware refused. This is the only shape in
                       # which the fault column is observable — a faulting
                       # process cannot ask about itself, so the reader is the
                       # supervisor whose handle held the slot open (design 3
                       # D-3). `death_fault` asserts the exact mirror.
                       "SOS isolation: child syscalls=0 faults=1",
                       "SOS isolation: done"],
        "expect_clean_exit": True,
    },
    {
        # BADIMAGE IS A STATUS. Root hands `process_create` a region of zeros
        # (its two arguments swapped), gets a VALUE back, reports it and exits
        # CLEAN — which is the assertion that separates this from every other
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
        # THE LINE'S OTHER SIDE: a second `start()` is the CALLER's fault, not a
        # status. `process=0` in both lines is what says root itself died, which
        # is what makes this transcript different from the lifecycle case's —
        # the same two report lines, a different process, and the machine stops.
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
        # THE BOOT-HANDLE ITERATOR, drained. Two rows in, two records out, in
        # ordinal order — and then `Drained`, twice, because the cursor only
        # advances. It creates no process: the claim is the DELIVERY, and a
        # launch beside it would let an iterator bug hide behind a load that
        # happened to work.
        "name": "process_bootdrain",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PROCESS_BOOTDRAIN_PKG,
        "children": [CHILD_FAULT_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       # `tags` is the ordinals accumulated as DIGITS, so it
                       # carries the delivery ORDER and not only the set: 0 then
                       # 1 accumulates to 1, while 1 then 0 would print 10.
                       "SOS bootdrain: n=2 tags=1 kinds=0",
                       "SOS bootdrain: exhausted, and still exhausted",
                       "SOS bootdrain: done"],
        "expect_clean_exit": True,
    },
    # =========================================================================
    # M3 unit 2.75 — the handle lifecycle (sawos design 3)
    # =========================================================================
    #
    # Seven cases, and every one of them is IMPOSSIBLE OR SILENTLY WRONG before
    # this unit: there was no release op, so a slot was never reused, so there
    # was no such thing as a stale word — and the wrappers were copyable values
    # whose drop did nothing.
    #
    # The three mechanisms land together because each alone is broken.
    # Mint-per-call without release is a leak by design (`handle-drop-release`
    # is what makes the pairing visible). Release without generations is
    # aliasing (`handle-remint` is the money proof that it is not). And a
    # release op beside COPYABLE wrappers is a stale-fault factory, which is
    # what the Aug-29 drop-is-release ruling closed.
    {
        # THE MONEY PROOF: a released handle's word does not name its slot's
        # next occupant, it names NOTHING, and the kernel says so.
        #
        # `reused=1` is asserted without naming the split — see the source
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
                       # one). The one that was released is not among them,
                       # which is the count's own small statement.
                       "SOS: process teardown handles={five} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={zero}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # THE ONLY CLEAN EXIT IN THE FAMILY, and that is the assertion: a
        # release is an ordinary successful op. No right is checked, and the
        # slot it frees is available to the very next ask.
        "name": "handle_release_ungated",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": HANDLE_RELEASE_UNGATED_PKG,
        "expect_out": ["{banner}",
                       "SOS release: ungated=1 refilled=1",
                       "SOS release: done",
                       # Five: §12's three, the minted Process handle, and the
                       # ONE Clock left after a mint, a release and a re-mint.
                       "SOS: process teardown handles={five} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={zero}"],
        "expect_clean_exit": True,
    },
    {
        # `NO_HANDLE` names nothing, so releasing it is asking to destroy a
        # capability the caller never had — the ordinary `BadHandle` fault.
        # The teardown's THREE is §12's boot set and nothing else: this process
        # derives nothing before it dies, which is what makes the number a
        # statement about the fault rather than about what it happened to hold.
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
        # THE SAME ANSWER FOR A DIFFERENT MISTAKE, and the first line is what
        # separates the two: the word released here was VALID one instruction
        # earlier. What makes the second call a fault is the entry's LIFE having
        # moved on, which is the generation doing the only job it has.
        #
        # This is also the shape a buggy transfer funnel would land in — a
        # diagnosed fault naming the process, never a silent alias.
        "name": "handle_release_twice",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": HANDLE_RELEASE_TWICE_PKG,
        "expect_out": ["{banner}",
                       "SOS releasetwice: first release ok=1",
                       "SOS releasetwice: releasing the same word again",
                       "SOS: process fault: bad handle process={zero}",
                       # Three: the Clock this program minted is the one it
                       # released, so §12's boot set is all that is left. FOUR
                       # would mean the first release did not unbind.
                       "SOS: process teardown handles={three} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={zero}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # THE REGRESSION THE SPLIT WORD INTRODUCED, pinned. `0x100` is not equal
        # to `NO_HANDLE`, so a lookup that kept the old whole-word test would
        # take it for a handle and index the table with a zero. The rule that
        # refuses it is that the INDEX FIELD is 1-based, so zero is not an index
        # at any generation — and the probe stays valid at every split, since a
        # wider index field makes this word an out-of-range index instead.
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
        # DROP IS RELEASE, from the TYPED side — the one case in this family
        # that never names a handle word. Four Clock handles are minted; three
        # die in an inner scope and one is kept, and the teardown counts FIVE
        # (§12's three, the minted Process handle, the kept Clock). EIGHT would
        # be mint-per-call without RAII, which is a leak by design and is why
        # the two halves could not land apart.
        #
        # `scoped=3` is an assertion about the TIMING as well: the handle is
        # USED inside the scope, so a release that ran too early would fault the
        # process there rather than quietly changing a count.
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
        # THE ONE SLAB THAT RECLAIMS ON RELEASE (D-3), closing unit 2's pend.
        # Both flags are asserted in one line because either alone is
        # misleading: `refused_before` is unit 2's behaviour still holding while
        # a reader exists, and `reclaimed_after` is the reader-count reaching
        # zero. A kernel that reclaimed eagerly would pass the second and fail
        # the first, and lose the supervision story with it.
        #
        # It reuses the child-fault package, and every create loads the same
        # image into the same RAM — the first child's remains are nothing the
        # kernel tracks.
        #
        # **THE CASE ARRANGES A FULL TABLE SINCE M4 UNIT 5** (sawos design 21):
        # `MAX_PROCESSES` is THREE now, so root plus a dead child leaves a slot
        # free and `refused_before` would simply not happen. One unstarted
        # FILLER process spends it. THE ASSERTED ROW IS UNCHANGED, which is what
        # says the proof survived the bump rather than being re-aimed by it.
        "name": "process_reclaim",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PROCESS_RECLAIM_PKG,
        "children": [CHILD_FAULT_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS reclaim: started",
                       # The child dies first, and the teardown names PROCESS 1
                       # — the same two lines `process_lifecycle` asserts, here
                       # only as the precondition for what follows.
                       "SOS: process fault: bad handle process={one}",
                       "SOS: process teardown handles={zero} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS reclaim: refused_before=1 reclaimed_after=1",
                       "SOS reclaim: done"],
        "expect_clean_exit": True,
    },
    # =========================================================================
    # M3 unit 3 — mint, give, and the boot drain (sawos design 4)
    # =========================================================================
    #
    # Eight cases, and the thing they have in common is that BEFORE this unit a
    # child held no handle at all. Every one is therefore about a sentence that
    # could not previously be written: NARROW an authority, hand it across a
    # process boundary, name it with a word the kernel never reads, and let the
    # receiver find it.
    #
    # THE THREE OPS DIVIDE CLEANLY and the cases follow them. `MINT_OP` narrows a
    # capability inside one table (`mint_revoked`, `give_no_transfer`); `Give`
    # moves one between two (`give_duplicate_tag`, `give_after_start`,
    # `give_word_dead`); `Start` delivers one to a register and freezes the set
    # (`start_bad_tag`). `give_boot_drain` is all three at once, and
    # `child_no_shutdown` is what the narrowing was FOR.
    {
        # THE MONEY SHOT: MINT -> GIVE -> START, three ordinary ops, and the
        # launcher keeps what it supervises with.
        #
        # Root NARROWS its own System authority into a sibling (`Debug |
        # ProcessSelf | ClockGet | Transfer` — `Shutdown`, `ProcessCreate` and
        # `Mint` absent BY SILENCE, which is the keep mask's fail-closed
        # polarity), GIVES the sibling under tag 7 with two Memory regions under
        # 3 and 5, and STARTS the child naming tag 7. It never gives away the
        # child's Process handle, so it still supervises. `Transfer` stays in the
        # mask because it is the bit `give` checks on the thing being given — a
        # launcher cannot narrow below the ticket that lets the handle arrive,
        # which is a finding recorded in the brief.
        #
        # `SOS childdrain:` IS THE FIRST TIME ANYTHING BUT ROOT HAS SPOKEN. The
        # child owns no device; `Debug` travelled in the mask.
        #
        # `n=2` IS A RULING, not an off-by-one: three capabilities were given and
        # the one named by the boot tag was CONSUMED by `start`'s resolution,
        # because the register IS its delivery. `tags=35` carries the ORDER as
        # digits, so a kernel delivering out of order prints `53`.
        #
        # `code=2` and `status=65538` are the SAME FACT from the two sides of the
        # boundary — the kernel's account of the child's exit, and root reading
        # `Exited`(1)<<16 | 2 through the Process handle it kept. Both at once is
        # what the mint bought: before it, a launcher had one Process handle per
        # child and had to choose between supervising and furnishing.
        #
        # `handles={two}` in the teardown is the child's estate: the System
        # handle it printed through and the Process handle it derived from it.
        # Three capabilities were given and it DECLINED both regions at the drain
        # — their records dropped and released them — which is drop-is-release
        # showing up in a case that is not about it.
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
        # THE TAG IS THE IDENTITY, so two handles under one tag is broken config
        # — caller-checkable, and it would make the boot record a multimap and
        # `start(boot_tag:)`'s lookup ambiguous.
        #
        # The fault tag is the WAITER's, word for word: `DuplicateKey` was
        # written for §2.2's attachment keys and says exactly the same thing
        # here. It is asserted verbatim because a shipped transcript
        # (`event_dupkey`) asserts that string and design 4 authorised no
        # expectation changes.
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
        # THE FREEZE. The give-before-start ordering IS the soundness argument
        # for the launch flow — a child's boot handles must answer completely at
        # its first instruction — and `start` is the barrier that makes a
        # half-populated table unrepresentable with no synchronization invented.
        # `BadState` is the same fault a second `start` raises, through the same
        # process-state word, which is what says the two refusals are one rule.
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
        # THE ATTENUATION PROOF, and the FIRST CONSUMER §3's universal low byte
        # has ever had: `Transfer` has been declared in every kind's rights enum
        # since M2 and read by nothing until give.
        #
        # ONE TRANSCRIPT, TWO CLAIMS. Root MINTS a sibling through a mask that
        # omits `Transfer` — the complement notation, which is what the polarity
        # ruling leaves available for "the same handle, minus one bit" — and the
        # give of it is `AccessDenied`. So the give's gate is real AND a masked
        # sibling is genuinely narrower than its source, with no way back: a
        # mint's result is a subset, so minting again only narrows further.
        #
        # It used to give root's own System handle, on the M2 fact that
        # `root_system_rights()` withheld `Transfer`. The Aug-29 rulings ended
        # that — root's handle carries it now, because giving a masked System
        # sibling to a child IS the launch flow — so the ungivable handle in this
        # system is one a launcher MADE ungivable.
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
        # A BOOT TAG THAT NAMES NO GIVEN RECORD IS A FAULT. There is no honest
        # word to put in the child's first argument register, and a zero would be
        # indistinguishable from the deliberate no-tag form — so a typo'd tag
        # would silently produce a sandboxed process instead of a diagnosis.
        #
        # The give above it is what makes the case sharp: tag 3 IS in the set, so
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
        # THE GIVER'S WORD IS DEAD — unit 2.75's generations composing with a
        # give. The unbind is `unbind_handle`, the same one release and the
        # teardown call, so the word the giver held names a live slot at the
        # wrong life and using it is the ordinary `BadHandle`. Without
        # generations it would be an ALIAS instead: the freed slot is the lowest
        # free index, so the very next mint takes it back.
        #
        # It works at the C altitude, and that is half the claim: `Process.give`
        # CONSUMES its wrapper and disarms it before the syscall, so "use the
        # word you gave away" is not a sentence the typed layer can express.
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
        # PROLIFERATION IS REVOCABLE. `MINT_OP` is gated on the universal `Mint`
        # bit and that bit is maskable, so a launcher hands out a handle that can
        # be USED and not MULTIPLIED — without which one `Mint` anywhere would be
        # `Mint` everywhere downstream forever.
        #
        # THE FIRST MINT SUCCEEDS, which is what makes the second refusal mean
        # something: the caller holds a perfectly good System handle and the one
        # thing it cannot do is the thing its mask took away.
        #
        # It is also the ONLY way to hold a source lacking `Mint` at all — every
        # default set grants it under the uniform lean — so this transcript is
        # simultaneously "the sibling cannot mint" and "a mint through a source
        # without `Mint` is `AccessDenied`". A second case would be this program.
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
        # THE MASK HOLDS AT RUN TIME — what the narrowing was FOR, and the only
        # case in the suite where a RIGHTS refusal falls on a process that is not
        # root.
        #
        # The child prints first, which is half the claim: a keep mask is a SET
        # rather than a wall, `Debug` was named, so the program has a voice and
        # can announce what it is about to try. Then it asks to stop the machine,
        # `Shutdown` is absent BY SILENCE, and the fault names PROCESS 1 — the
        # asker, not the launcher — while the machine keeps running.
        #
        # `status=131075` is that same fact as a VALUE through root's retained
        # handle: `Faulted`(2)<<16 | `AccessDenied`(3). A masked bit is enforced
        # by exactly the path a never-minted one is.
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
    # M3 unit 4 — Memory, IoMemory, Mapping (sawos design 6)
    # =========================================================================
    #
    # **A MAPPING IS AN INSTALLED GRANT ROW**, and five cases is what proving
    # that takes: derivation (two verbs, because the kinds differ), installation,
    # revocation, and installation ACROSS a process boundary.
    #
    # THE INVERSION WORTH NAMING. Every protection case above this line asserts
    # that an address a process was not granted FAULTS — `root_server_oversteps`,
    # `process_isolation`, `child_oversteps`. These assert the other direction:
    # an address that would have faulted is memory, because somebody installed a
    # row. The two together are what makes a protection domain DATA rather than a
    # property of an image.
    #
    # THREE OF THE FIVE END IN A FAULT and do their positive work first, which is
    # `irq_early_ack`'s shape and is forced by the same fact: a `BadArg` fault
    # ends the process, so a second probe would never run.
    {
        # DERIVATION, and the arithmetic made observable. A region has no bounds
        # reader, so "the parent advanced" is proven by USING both pieces: 0xaa
        # into the first, 0xbb into the second, and the first still 0xaa.
        #
        # `pool exhausted after N cuts` is the slab, and it is a STATUS rather
        # than a fault because a caller cannot know how many slots are left.
        # Free-on-last-reference is unit 5's, so the pieces this loop drops do
        # NOT come back — which is the honest state of the system and is what
        # the number says. It pins `MAX_MEMORIES` in the transcript, which is
        # the point: raising the slab should move a line somebody reads.
        #
        # The last line is a DEATH: an oversize cut is caller-checkable, so it
        # is a fault, so meeting it is the only way to assert it.
        "name": "memory_split",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": MEMORY_SPLIT_PKG,
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={one}",
                       "SOS split: a=170 b=187 a-again=170",
                       # M3 UNIT 5 MOVED THIS LINE, and moving it is the point
                       # (sawos design 7 D-1). It used to read `pool exhausted
                       # after 13 cuts` — the Memory SLAB running out with most
                       # of the pool unallocated — and unit 4's own finding 5
                       # said why: free-on-last-reference was unit 5's, so a
                       # dropped piece returned a slot to nobody. It does now,
                       # so twenty cut-and-drop rounds (past `MAX_MEMORIES`,
                       # which is 16) complete with nothing refused. The number
                       # that pinned the slab in a transcript is exactly what
                       # stopped being true.
                       "SOS split: cuts=20 refused=0",
                       "SOS split: asking for more than is left",
                       "SOS: process fault: argument outside its domain",
                       "SOS: process teardown"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # INSTALLATION. Root maps a page into ITSELF and writes it, which is the
        # inverse of every protection proof above — and it is also half the
        # live-domain rule: the scheduler skips equal domains, so a map into the
        # RUNNING process that did not reload would never be installed at all
        # and the write below would fault instead of printing.
        #
        # The double map is agenda item 7's lean shown: two rows over one region
        # with different access, allowed, no aliasing bookkeeping owed on a
        # machine that does not translate. The case asserts that both INSTALL
        # and that a read still answers — deliberately not a write afterwards,
        # since which row answers is the hardware's own matching rule.
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
        # REVOCATION, AND THE LIVE-DOMAIN RULE END TO END. Root maps, writes,
        # reads, UNMAPS, and touches again — and the touch has to die.
        #
        # **THE ASSERTION IS THE PRESENCE OF THE FAULT**, which is the failure
        # mode worth engineering for: a kernel that edited the grant record
        # without reloading would let the touch LAND, the program would print
        # `UNREACHABLE` and exit non-zero on its own account, and the missing
        # line would be the tell. A revocation that does not revoke until the
        # next reschedule is not a revocation.
        #
        # The fault's tag and cause differ per machine (a load access fault
        # here, a data abort there), so what is asserted is that ONE was taken.
        "name": "map_unmap",
        # ISOLATED-ONLY (sawos design 35), and the OTHER property the tier word
        # disclaims by name: `unmap`'s revocation half. On this profile the row
        # leaves the record, the hardware forgets it, and the touch dies. On a
        # flat profile the row still leaves the record and the Mapping still
        # dies — the kernel-side half is not tiered — but the hardware goes on
        # permitting the access, so the program prints its own UNREACHABLE line
        # and exits non-zero on its own account.
        "tier": TIER_ISOLATED,
        "tier_reason": "unmap's revocation half is the other property Flat "
                       "disclaims by name — the touch after unmap would succeed",
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
        # INSTALLATION ACROSS A PROCESS BOUNDARY — the launcher shape, and the
        # one unit 6 is built on. Root maps a page into a child it has CREATED
        # but not started, and the child touches an address its image never
        # declared. (Unit 6's `share_double_map` is this shape carried through
        # to a conversation: the same install, plus a row in ROOT's own domain
        # over the same region, plus bytes crossing in both directions.)
        #
        # **THE MAP IS BEFORE THE START AND IS NOT A BOOT RECORD**, which is why
        # the give-freeze does not reach it: §12's set freezes at `start`
        # because a child's HANDLES must be complete at its first instruction,
        # and a protection row is not a handle. The row reaches the child
        # through the ordinary switch-in replay (design 2 D-5) carrying a row
        # that did not come from an image.
        #
        # `status=65596` is `Exited`(1)<<16 | 60 — the byte the child read back,
        # arriving as a VALUE through the Process handle root kept. The same
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
        # DEVICE AUTHORITY. A window is carved NON-EXCLUSIVELY — the same range
        # twice, which a split could not do — mapped, and then a window the
        # machine's protection hardware cannot express is a FAULT.
        #
        # THE REFUSAL IS ONE LINE FOR TWO DIFFERENT RULES: one profile needs a
        # naturally-aligned power of two (one protection entry), the other a
        # whole number of pages, and `0xC00` is neither. That is what a per-HAL
        # predicate buys — the case is arch-free and the rule is not.
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
    # M3 unit 5 — quotas and the reference count (sawos design 7)
    # =========================================================================
    #
    # Five cases, and every one of them is IMPOSSIBLE OR SILENTLY WRONG before
    # this unit. There was no reference count, so an object's slot came back at
    # its process's teardown and at no other moment — which made `refcount_free`
    # unobservable, `interrupt_unbind` a `LineBound` FAULT, and
    # `mapping_slot_free` the deviation design 6 recorded rather than a
    # behaviour. And there was no ledger at all, so neither quota case had a
    # refusal to name: `QuotaExceeded` did not exist as a value.
    #
    # The two mechanisms land together because the zero-crossing and the credit
    # are ONE EVENT — a count without a ledger reclaims silently and a ledger
    # without a count can only ever be credited by a death.
    {
        # **THE MONEY PROOF OF THE COUNT**: two ways an object outlives a
        # release, and a teardown that counts NEITHER of the events this
        # program made.
        #
        # `events={zero}` is the line to read. Two Events were created here;
        # one was held by a SIBLING handle after its first was released, the
        # other by an ATTACHMENT after its only handle was released, and each
        # freed at its own zero — the first at the second release, the second
        # at the `Waiter.Remove` that dropped its attachment. Yesterday this
        # count was the number of events a program had ever made.
        #
        # `waiters={one}` beside it is the asymmetry that makes the cascade
        # terminate: an attachment references the WAITABLE and never the Waiter
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
        # A BOUND LINE COMES BACK MID-LIFE. Releasing the last Interrupt handle
        # masks the line and frees the slot, so the very next bind of the SAME
        # line succeeds — where before this unit it met a live object and was a
        # `LineBound` fault, which ends the process. The second line existing at
        # all is therefore the claim.
        #
        # `interrupts={one}` says the kernel is holding exactly one Interrupt
        # when the process ends, after this program bound the same line twice.
        # `handles={five}` is §12's three, the Process handle this program
        # derived, and the SECOND interrupt handle — the first is not among
        # them, which is the count's own small statement.
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
        # DESIGN 6's RECORDED DEVIATION, SHOWN RESOLVED — and, since M5 unit 5,
        # §2.5's leak shown to be SMALLER than it always said it was.
        #
        # `rounds=12` is past `MAX_MAPPINGS` (8), so the slab is demonstrably
        # recycling — and past root's own ROW allowance too (its free rows,
        # five on the smaller profile), so `Unmap` is demonstrably crediting the
        # ledger as well as returning the row. One number carries both halves.
        #
        # **THE OTHER TWO LINES WERE RETARGETED BY SAWOS DESIGN 28**, which
        # authorises this case's rows to move by name, and they are the ruled
        # second-zero condition from both sides. `husk wrote 94 read 94`: every
        # HANDLE on the region is destroyed and its bytes are still there, so
        # the slot survived the handle drop — a kernel counting handles alone
        # would have freed it with a protection row still reaching those bytes.
        # `recycled read 94`: the unmap takes the last row, the range folds back
        # into the pool's frontier, and the next split of the same size is the
        # SAME PAGE — proven by the marker written through the first mapping and
        # read through the second.
        #
        # What it used to assert with those numbers was narrower and is still
        # true of the Mapping object itself: a Mapping dropped without an unmap
        # frees its slot and leaves its row. What changed is that the RANGE no
        # longer leaks with it.
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
        # **A PROCESS TOLD "NO" BY POLICY RATHER THAN BY THE MACHINE**, for the
        # first time in SOS. The child creates Events to its own limit, meets
        # `QuotaExceeded`, RELEASES one and creates again successfully — which
        # is the half `NoResource` can never have, since a machine edge does not
        # move because one process let go of something.
        #
        # THE SLAB WAS NEVER THE LIMIT THAT TRIPPED, and the numbers say so:
        # `MAX_EVENTS` is 8, the child's allowance is 4, and root creates no
        # events in this case at all — so half the slab was free at the moment
        # of the refusal. If the two were confused, the fifth create would have
        # succeeded and `made=` would read 5.
        #
        # `events={three}` in the child's teardown is the ledger's other half
        # arriving as somebody else's count: five events made, one released, one
        # dropped, three still held at the end.
        #
        # `status=65540` is `Exited`(1)<<16 | 4 — the child's own count, read
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
                       # THREE events it was still holding. The one it released
                       # and the one it dropped after the credit are not among
                       # them — the same fact `events={three}` states from the
                       # slab's side.
                       "SOS: process teardown handles={five} threads={one} "
                       "events={three} waiters={zero} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS quotaex: root observed child status=65540",
                       "SOS quotaex: done"],
        "expect_clean_exit": True,
    },
    {
        # AGENDA ITEM 8's ORDERING, OBSERVABLE — and a clean run IS the proof.
        # The kernel's half of this claim is a `fatal_kernel` at the row-budget
        # check, which by construction never appears in a passing transcript; so
        # what a case can show is that the POLICY refusal arrives first, and
        # that the machine is still standing to say so.
        #
        # `maps=2` IS THE SAME ON BOTH PROFILES, which is why this is a child
        # and not root. A child's allowance is `DEFAULT_QUOTA_MAPPINGS`, clamped
        # to the rows its own domain has left after its image spent three — two
        # on both machines, against five free rows on riscv32 and six on arm64.
        # Root's allowance IS its free row count, so a root-side version would
        # print a different number per machine.
        #
        # EACH MAPPING OBJECT IS DROPPED AND EACH ROW STAYS, which is what makes
        # this a LEDGER test: the Mapping slab is untouched at the refusal, so
        # only a quota that counts installed ROWS could have refused the third.
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
        # **A SUPERVISOR WOKEN BY A DEATH** (sawos design 8, M3 unit 5.5) — the
        # first case in the suite whose launcher parks with NO TIMER ARMED.
        # Every earlier one waits on a generous deadline while its child runs,
        # because until this unit a death woke nobody; this one attaches the
        # CHILD's own Process handle to a Waiter and parks on it, so the only
        # thing in the machine that can make root runnable again is the child
        # ending. A kernel that failed to notify would leave nothing runnable
        # and the run would die with `every thread blocked` — the failure mode
        # is loud, which is what makes a plain `wait()` an assertion.
        #
        # **THE ORDER OF THE LAST THREE LINES IS THE UNIT'S OWN D-1 CLAIM.**
        # The kernel notifies as soon as the §8 status word is recorded —
        # before it closes a single handle — but a notification only QUEUES the
        # woken thread, so the whole teardown runs first and root is picked up
        # afterwards. The wake line therefore lands AFTER the teardown line,
        # and there is no arrangement of these lines in which a supervisor
        # observes a half-dead process.
        #
        # `status=65541` is `Exited`(1) << 16 | 5 — `child-bye`'s own exit code,
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
        # THE FAULT ARM — the same program with one thing taken away. This
        # launcher gives its child NOTHING, so the child holds `NO_HANDLE` and
        # the kernel terminates it at its first `ecall`. The attachment, the
        # park and the wake are identical; what differs is the word that comes
        # back, which is the whole claim: a supervisor learns THAT a child died
        # and HOW through one mechanism, whether the child chose the ending or
        # the kernel did.
        #
        # `status=131073` is `Faulted`(2) << 16 | `BadHandle`(1) — byte for byte
        # the word `process_lifecycle` reads through `get_status`, arriving here
        # as a wake instead of as a poll. That equality is the point: unit 5.5
        # adds no vocabulary to §8, it adds a second door onto §8's word.
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
                       # **THE SWEEP ROW** (sawos design 16), and it is the
                       # mirror of `process_isolation`'s. `child-fault` made ONE
                       # `ecall`, naming handle zero, and the kernel ended it —
                       # so the trap is charged to the SYSCALL column even though
                       # it killed the process. The columns are keyed by how the
                       # machine entered the kernel, not by how the kernel felt
                       # about it, which is what keeps `syscalls` predictable: a
                       # caller cannot know in advance which call gets refused.
                       "SOS deathfault: child syscalls=1 faults=0",
                       "SOS deathfault: done"],
        "expect_clean_exit": True,
    },
    {
        # **NOTHING UN-DIES** — terminal level, and design 7's count observed
        # through the supervision flow (sawos design 8, sharpening 1).
        #
        # `first=65541 second=65541` is two claims in one line. Root sleeps on a
        # timer until the child is ALREADY DEAD and only then attaches, so an
        # edge-triggered design would have nothing left to report and this wait
        # would never return; it returns immediately, because readiness is
        # `state == Gone` and that is still true. And the SECOND wait on the
        # same attachment answers the same word, because a death — unlike an
        # Event's word or a Timer's fire count — is not spent by the delivery
        # that reports it.
        #
        # `held=1 freed=1` is which reference was load-bearing, asked one at a
        # time. With the Process HANDLE released and only the attachment left,
        # a `process_create` against an otherwise-full table is still refused
        # (slot 1 is still `Gone`); remove the attachment and the count reaches
        # zero, the slot frees inside that very syscall, and the same create
        # succeeds. A kernel that did not count the attachment would print
        # `held=0` — and the second wait above would have been reading a slot
        # the kernel had already given away.
        #
        # **"OTHERWISE FULL" IS ARRANGED BY AN UNSTARTED FILLER PROCESS SINCE
        # M4 UNIT 5** (sawos design 21, `MAX_PROCESSES` = 3), exactly as in
        # `process_reclaim` and for the same reason. Both asserted rows are
        # unchanged.
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
                       "SOS deathlate: held=1 freed=1",
                       "SOS deathlate: done"],
        "expect_clean_exit": True,
    },
    # =========================================================================
    # M3 unit 6 — the driver child, shared memory, and the exec gate
    # (sawos design 9) — THE MILESTONE FINALE
    # =========================================================================
    #
    # FIVE CASES, and the first two are what the whole ladder was for. Unit 2
    # gave SOS a second process, unit 3 a way to furnish one, unit 4 a device
    # window that arrives as a capability, unit 5 a budget and a reference
    # count, unit 5.5 a death a supervisor is woken by. Put together they are a
    # LAUNCHER THAT HANDS A DEVICE TO A DRIVER — which is the sentence the
    # migrated `uart-echo` headers have been promising to this unit since M3
    # unit 4, and which no earlier unit could write.
    #
    # THE SPLIT INTO FIVE follows the house rule (one claim per image) plus the
    # arithmetic fact that has shaped every unit since design 6's: a fault ends
    # the process, so two refusals cannot share a transcript. That is what makes
    # the exec gate TWO cases rather than the one the brief sketched — see
    # `map_exec_gated` for the argument, which is recorded there rather than
    # here because it is a deviation.
    {
        # **THE MONEY SHOT: A DRIVER CHILD, FROM CONFIG, ON RISCV32.**
        #
        # It is `uart_echo_ns16550` above with one thing changed and it is not
        # the driver: the echoing process is no longer the one the kernel
        # loaded. Root drains the console's register page out of its own boot
        # set, GIVES it away, mints a masked System beside it, starts the child
        # and parks on its death. Everything after `gave the window` is a
        # process that root created, driving a device root no longer has.
        #
        # WHAT EACH ASSERTED LINE RULES OUT, beyond what the root-as-driver twin
        # already ruled out:
        #
        #   boot regions={three}   the child's blob, its RAM, and the DEVICE row
        #   gave the window        `Transfer` spent on an `IoMemory`; root's
        #                          wrapper is disarmed and root can map it nowhere
        #   window mapped          the CHILD called `Process.map(iomemory:)` on
        #                          its own Process handle — the give moved
        #                          authority, and where the row goes was then the
        #                          holder's business
        #   Zq7#                   give, drain, map, bind, enable, park, wake and
        #                          ack, all across a process boundary
        #   status=65536           `Exited`(1) << 16 | 0, read by root through the
        #                          Process handle it kept — so the driver's clean
        #                          ending is a fact root OBSERVED rather than one
        #                          the console merely showed
        #
        # **THERE IS NO TIMER ANYWHERE**, which matters twice here. A driver
        # waits on a human-scale serial port, so a deadline would have to be
        # guessed; and §9a's one bounded exception to the console handover is a
        # kernel whose armed tick narrates over a process that owns the device.
        # A case whose transcript matters after handover arms none.
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
                       # THE DRIVER'S WHOLE ESTATE, counted by the kernel as it
                       # takes it back: the masked System its launcher gave it,
                       # the Process handle it derived, the IoMemory it was
                       # given, the Mapping that window produced, the Interrupt
                       # it bound and the Waiter it parked on. SIX, and every
                       # one of them is a step of §9's cycle — which is another
                       # way of saying a driver is exactly its handles.
                       "SOS: process teardown handles={six} threads={one} "
                       "events={zero} waiters={one} interrupts={one} "
                       "timers={zero} process={one}",
                       "SOS driverchild: root observed child status=65536",
                       "SOS driverchild: done"],
        "expect_clean_exit": True,
    },
    {
        # The same money shot on the other machine, and the same package split
        # the root-as-driver twins have: a driver names its DEVICE, so there are
        # two child packages named for the chip — while the launcher above them
        # is arch-free and is built for both, because a capability has no
        # datasheet.
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
                       # The same six, on the other machine and the other chip —
                       # which is the count saying what the two packages say:
                       # what differs between them is the datasheet.
                       "SOS: process teardown handles={six} threads={one} "
                       "events={zero} waiters={one} interrupts={one} "
                       "timers={zero} process={one}",
                       "SOS driverchild: root observed child status=65536",
                       "SOS driverchild: done"],
        "expect_clean_exit": True,
    },
    {
        # **SHARED MEMORY: ONE REGION, TWO ROWS, TWO ADDRESS SPACES.** §2.5's
        # own words — "the SAME physical memory ... the shared-memory primitive"
        # — as a transcript, and the first CROSS-PROCESS double map.
        #
        # BOTH DIRECTIONS, AND EACH IS PROVED TWICE. Root writes 0xA5 through
        # its own row; the child says it saw 165 AND carries that byte out as
        # its exit code, which root reads back as the §8 status word
        # (`Exited`(1) << 16 | 165 = 65701). The child writes 0x5A; root loads
        # it back out of the same page. On a machine that does not translate
        # there is no copy step for either fact to hide in.
        #
        # **ROOT INSTALLS THE CHILD'S ROW, AND THE CHILD HOLDS NO Memory
        # HANDLE** — which is the deliberate contrast with the two echo cases
        # above, where root gave the capability away and the child installed its
        # own row. Access without possession, beside possession-then-install, in
        # one unit: the object model answers both without a mode flag anywhere.
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
                       # **`handles={two}` IS THE CLAIM OF THIS CASE, IN THE
                       # KERNEL'S OWN ACCOUNTING.** The child's entire estate is
                       # the masked System it was given and the Process handle
                       # it derived from it. There is NO Memory handle among
                       # them and no Mapping either — the row it read and wrote
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
        # **EXECUTABLE IS AN AUTHORITY** (design 9 D-1). Root maps a page
        # read-execute through its full pool handle and it installs; root then
        # mints a SIBLING with `MemoryRight.MapExecute` masked out, proves that
        # sibling is a working handle by mapping read-write through it, and asks
        # for read-execute through it — which ends the process.
        #
        # THE READ-WRITE LINE IS WHAT MAKES THE REFUSAL MEAN ANYTHING: without
        # it, a sibling broken in any of a dozen ways would produce the same
        # `access denied`. One handle, two asks, one refusal — the difference is
        # the bit.
        #
        # **DEVIATION FROM THE BRIEF, RECORDED HERE AND ARGUED IN THE
        # AS-BUILT.** Design 9's proof section sketches ONE case with three
        # arms: root maps X successfully, a mint-without-`MapExecute` is
        # refused, and `W|X` in one row is refused. Two of those three arms are
        # FAULTS, and a fault ends the process — the fact design 6's own case
        # split turned on and the runner has stated ever since ("a second probe
        # after one would never run"). So the three arms are two cases: this
        # one, whose positive arm and refusal are both about the RIGHT, and
        # `map_wx_refused` below, whose positive arm and refusal are both about
        # the WORD. Each case's success motivates its own failure, which reads
        # better than the sketch did and costs one extra row per profile.
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
                       # NINE, and the arithmetic is the program: §12's three
                       # boot handles, the pool region, the Process handle
                       # `process_self` minted, the page the split cut off, the
                       # executable Mapping, the narrowed SIBLING, and the
                       # read-write Mapping it installed. The refused map minted
                       # nothing, which is what a fault before any allocation
                       # means.
                       "SOS: process teardown handles={nine} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={zero}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    {
        # **W^X, PER ROW** (design 9 D-1's companion invariant), and the two
        # lines above the refusal are why it costs nothing expressible: one
        # region is mapped read-write HERE and read-execute THERE — the
        # sanctioned double map — so the JIT-shaped pattern survives in full
        # while every individual protection row is W^X.
        #
        # THE REFUSAL GOES THROUGH ROOT'S FULL POOL HANDLE, which carries
        # `MemoryRight.MapExecute` and has just proved it. So this cannot be the
        # exec gate wearing a different hat: every right is present and the WORD
        # is what is wrong, which is what `argument outside its domain` says and
        # `access denied` would not.
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
                       # EIGHT — `map_exec_gated`'s nine minus the sibling it
                       # minted, since this case narrows nothing. TWO Mappings
                       # are among them, over one region, which is the double
                       # map counted rather than merely printed.
                       "SOS: process teardown handles={eight} threads={one} "
                       "events={zero} waiters={zero} interrupts={zero} "
                       "timers={zero} process={zero}"],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },
    # =========================================================================
    # M4 unit 0 — waiter revocation (sawos design 12)
    # =========================================================================
    {
        # **NOTHING PARKED CAN BE SILENTLY DOOMED** — the peer-gone doctrine's
        # first instance (`designs/010` rulings 1 and 2). Through M3, releasing
        # the last handle to a Waiter a sibling thread was parked on left that
        # thread `Blocked` forever, recorded at the free arm as legal-but-doomed.
        # It wakes now, with a status that names what happened.
        #
        # THREE CLAIMS IN ONE ORDERED TRANSCRIPT, and the ORDER is what carries
        # two of them. Nothing in this image can move the processor except a
        # thread giving it up — the kernel booting a root server arms no timer,
        # and `start` only makes a thread runnable — so:
        #
        #   both `parking` lines            the two threads really are on ONE
        #                                   Waiter's blocked list
        #   `sibling released, nobody woke` printed AFTER a yield that offered
        #                                   both workers the processor, so its
        #                                   position IS the claim that the COUNT
        #                                   gates the revocation and not the
        #                                   release: 2 -> 1 frees nothing
        #   two `woke` lines, one release   the walk is the WHOLE list, not the
        #                                   pop a readiness does (§2.2's
        #                                   distribution, which `event-wake`
        #                                   proves on the other side)
        #
        # `b` before `a` is the blocked list being a stack — `block_on_wait`
        # pushes at the head — which this transcript happens to show and nothing
        # promises.
        #
        # THE STATUS IS NAMED, NOT NUMBERED. The program decodes the raw word
        # through `SosStatus.from(raw:)`, so the words on the console are the
        # ABI enum's own `describe()` and a case added without a describe arm
        # would not compile.
        #
        # `waiters={zero}` in the teardown is the other half of "zero frees,
        # synchronously": the slab sweep found nothing left, because the release
        # freed it.
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
    # M4 unit 1 — the pair (sawos design 13)
    # =========================================================================
    {
        # **THE DATA PATH, IN ONE PROCESS.** Root holds both ends, so every claim
        # below is about the CONNECTION rather than about scheduling — nothing
        # parks, nothing is given, and the transcript is a list of properties.
        #
        # `round trip len=3 b=65,66,67` is the BYTES, not a status: the copy-in
        # and copy-out funnels really carried the body, which a status alone
        # would not have shown.
        #
        # `fifo order=123` carries the ORDER as digits (`child-drain`'s `tags=35`
        # idiom), so a ring that handed back the newest first would print 321.
        #
        # `empty message len=0` and `take empty is none` are the two halves of
        # one distinction: a data-free message is a MESSAGE (§2.1's own option),
        # and the typed tier's `Optional` is what keeps it apart from "nothing
        # staged". A shape that could not tell them apart would make the poll
        # loop below unwritable.
        #
        # **`ring depth=16` IS A CLAIM ABOUT A BUILD DEFINE THIS PROGRAM CANNOT
        # NAME.** `PIPE_INFLIGHT` is `2 * MAX_THREADS` in `kcore.limits` and is
        # deliberately not published to userspace; root posts until the kernel
        # refuses and COUNTS. `drained=16` beside it says the refusal was a
        # refusal — a ring that dropped the oldest to make room would drain
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
        # **THE PEER-GONE DOCTRINE, AND THE TWO DIRECTIONS ARE NOT SYMMETRIC**
        # (sawos design 13 D-2; `designs/010` D-1). Two connections, killed from
        # opposite ends, and the difference between the two answers IS the unit's
        # sharpest claim.
        #
        # THE OUTLET GOES: `post says …` at once. Nobody can ever take again, so
        # there is no future for a staged message and the refusal is immediate.
        #
        # THE INLET GOES: `drained a=7 b=8` FIRST, and only then `take says …`.
        # Everything the departed sender staged is handed over before the server
        # is ever told — design 230's close-drains-first rule — so a client
        # exiting mid-conversation loses nothing. A kernel that unwound the ring
        # at the inlet's zero would print the terminal line with no drain before
        # it.
        #
        # `again says …` is TERMINAL LEVEL: nothing un-closes, so a caller may
        # stop rather than retry. That is what separates `PeerClosed` from
        # `WouldBlock`, which `pipe_basics` meets on the same two ops.
        #
        # THE STATUSES ARE NAMED, NOT NUMBERED — the program prints the ABI
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
        # **A CONNECTION THAT CROSSES A PROCESS BOUNDARY** — the seed of unit 5's
        # client (sawos design 13 D-3). Root creates the pair, keeps the outlet,
        # GIVES the inlet, and the child posts through it.
        #
        # **NOTHING ABOUT THE LAUNCH FLOW CHANGED**, which is what ruling 3 buys
        # by putting the factory on the Process object: mint a masked System
        # sibling, give it, give the pipe end, start naming the boot tag. The
        # only new thing is the KIND that travelled, and `Transfer` is minted in
        # `pipe_inlet_rights()` for exactly this.
        #
        # **THE ORDER IS THE CLAIM.** `SOS childpost: posted`, the child's exit
        # and its teardown all come BEFORE root reads the bytes — root's first
        # take necessarily answered `Ok(None)` (the child was runnable and had
        # not run), so root parked on a timer and the child ran to completion.
        # So the message outlives its sender, which is `pipe_peer_gone`'s
        # drain-first rule proved across a real process boundary.
        #
        # `code={four}` and `len=4` are the SAME FACT from the two sides of the
        # boundary: the child exits with its payload's length, and root reads
        # that many bytes. `b=80,73,80,69` is `PIPE` in ASCII — the bytes, so
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
        # **AN UNSPENT RIGHT ON A CONNECTION END** (spec §3). An endpoint's
        # default set is permissive and HAS TO BE — attenuation is monotonic, so
        # a bit not minted at creation could never appear later — which means the
        # narrowing is the holder's, written once at a `MINT_OP` keep mask.
        #
        # ONE TRANSCRIPT, TWO CLAIMS, exactly as `give_no_transfer` makes them:
        # a masked sibling of a pipe end is genuinely narrower than its source,
        # and the op's gate is real. The mask names `Transfer | Mint` and says
        # nothing about `Post`, which is what a KEEP mask's polarity means —
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
        # **A BODY BIGGER THAN THE SLOT** (spec §2.1's fixed body). The maximum
        # is published in `sosabi` — a caller cannot even declare the buffer
        # without it — so overshooting it is a mistake the process could have
        # checked, which design 178's faults ruling puts outside the status enum
        # entirely.
        #
        # THE CHECK IS BEFORE THE COPY, which is what makes the refusal safe
        # rather than merely correct: nothing is read out of the caller's memory
        # and no staging slot is touched, so a hostile length is a diagnostic and
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
    # M4 unit 2 — the one-shot pair (sawos design 14)
    # =========================================================================
    {
        # **THE WHOLE EXCHANGE, IN ONE PROCESS.** Root plays both sides, so every
        # row below is about the PAIR rather than about scheduling — nothing
        # parks, nothing is given.
        #
        # `request len=3 b=65,66,67` is the take answering TWO things: the bytes,
        # and (invisibly here, provably below) the obligation to reply to them.
        #
        # `pending is not ready` is `PipeReply.ready()` — M4 unit 4.5's
        # non-consuming level question (`designs/010` ruling 12(b)). Through unit
        # 4 this row read `pending is none` and came from a `resolve` answering
        # `Ok(None)`; a resolve here would PARK now, which is the whole of what
        # the ruling changed. `ready()` consumes NOTHING, so the same claim asks
        # again and resolves afterwards — and readiness is monotonic, so the
        # `false` this row prints can only ever become a `true`.
        #
        # `reply len=2 b=90,89` and `zero reply len=0` are the two halves of one
        # distinction on the way back — a reply that carries no data is an ANSWER,
        # not an absence — which is the request side's `empty message len=0`
        # mirrored.
        #
        # `fifo replies a=11 b=22 c=33` is THREE EXCHANGES IN FLIGHT AT ONCE,
        # answered OUT OF ORDER (2, 3, 1) and resolved in order. A kernel with one
        # reply slot per connection rather than per exchange would print the same
        # digit three times; one that keyed replies by queue position would print
        # them permuted.
        #
        # **`depth with one in flight=15` AND `depth settled=16` ARE THE UNIT'S
        # SHARPEST CLAIM** (design 14 D-1). A ring slot lives until its exchange
        # SETTLES, not until its bytes are taken — so ONE unsettled exchange costs
        # ONE slot, and discharging it gives the slot back. The program never
        # names `PIPE_INFLIGHT`: it counts twice, around a held exchange, and the
        # DIFFERENCE is what a kernel that freed at the take could not produce.
        "name": "pipe_oneshot",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_ONESHOT_PKG,
        "expect_out": ["{banner}",
                       "SOS oneshot: created",
                       "SOS oneshot: request len=3 b=65,66,67",
                       "SOS oneshot: pending is not ready",
                       "SOS oneshot: reply len=2 b=90,89",
                       "SOS oneshot: zero reply len=0",
                       # **THE SWEEP ROW** (sawos design 16): the smallest
                       # complete exchange in the file, counted. Post, take,
                       # reply, resolve — four ops — plus ONE for the closing
                       # `stats()` call, which `ktrap` charges at trap entry
                       # before the op answers. The bracket contains no printing
                       # (a printed byte is an `ecall`) and no wrapper falling
                       # out of scope (a release is an `ecall` too), which is
                       # what makes five arithmetic rather than observation.
                       "SOS oneshot: the exchange cost 5 traps",
                       "SOS oneshot: fifo replies a=11 b=22 c=33",
                       "SOS oneshot: depth with one in flight=15",
                       "SOS oneshot: depth settled=16",
                       "SOS oneshot: done"],
        "expect_clean_exit": True,
    },
    {
        # **ABANDONMENT IS INFORMATION, NOT AN IMPERATIVE** (spec §2.1, ratified;
        # sawos design 14 D-2). Three ways an exchange ends with nobody on the
        # other side, and every one of them is a DELIVERED ANSWER:
        #
        #   `reply says …`         the client dropped its claim, and the server's
        #                          obligation is discharged all the same — §2.1's
        #                          "never a silent success"
        #   `resolve says …`       the server dropped its obligation, and the
        #                          client is told rather than left waiting
        #   `staged claim says …`  the connection's SERVER END went while the
        #                          message was still staged, so the obligation
        #                          could never be minted
        #
        # THE THIRD IS THE DERIVED ONE and is why the columns are the state: the
        # kernel reads `Staged` plus an outlet column at zero, with no flag
        # anywhere saying "abandoned".
        #
        # THE STATUSES ARE NAMED, NOT NUMBERED — the program prints the ABI enum's
        # own `describe()`, so a renumbering of the wire table would not move this
        # transcript and a case added without a describe arm would not compile.
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
        # **THE DELEGATION PRIMITIVE, ACROSS A REAL BOUNDARY** — §2.1's ratified
        # zero-copy example, which had nothing to be true of until the one-shot
        # pair existed (sawos design 14). Root asks its own question, takes the
        # obligation off its own outlet, and GIVES that obligation to a child that
        # holds no end of the connection at all.
        #
        # **WHAT THE CHILD CANNOT DO IS THE PROOF.** It cannot post, cannot take
        # and cannot name root's pipe; the obligation is the whole of its
        # authority, and it knows where the answer goes. That is the filesystem
        # forwarding a read to the flash driver, at its smallest.
        #
        # **THE ORDER IS THE CLAIM.** `SOS childreply: replied`, the child's exit
        # and its teardown all come BEFORE root reads the bytes — root's first
        # resolve necessarily answered `Ok(None)` (the child was runnable and had
        # not run), so root parked on a timer and the child ran to completion. So
        # a reply outlives the process that wrote it, which is what putting it in
        # the exchange's own ring slot buys.
        #
        # `code={four}` and `len=4` are the SAME FACT from the two sides of the
        # boundary, and `b=68,79,78,69` is `DONE` in ASCII — the bytes, so that a
        # status alone could not have passed this case.
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
        # **AN OBLIGATION HAS ONE NAME** — RETARGETED at M4 unit 4.5
        # (`designs/010` ruling 12(c); sawos design 22), and the mirror of
        # `pipe_dead_claim`'s probe on the server half. `pipe_request_rights()`
        # withholds the universal `Mint` bit, so a sibling of an obligation cannot
        # be made and asking for one is `pipe_no_post`'s fault at a different
        # kind.
        #
        # A ONE-SHOT IS THE EXCEPTION TO THE UNIFORM LEAN, and only because it is
        # spent by ONE op: a second name is not a second way to use the object,
        # it is a handle whose only possible answer is `PeerClosed`, and it
        # undermines the compile-time single use `consumes` now delivers on all
        # three one-shot ops.
        #
        # DELEGATION IS UNTOUCHED — §2.1's forwarding primitive is `Transfer`
        # MOVING the one name (`pipe_delegate`, `pipe_delegate_msg`), and
        # narrowing what a delegate may do is `give`'s keep mask
        # (`give_keep_mask`).
        #
        # WHAT THIS CASE USED TO PROVE was the `Reply` bit through a sibling
        # minted without it. That spelling died with the bit; the `Reply` gate is
        # now reachable only through a `give` keep mask into another process,
        # which is a two-process case tracked as a follow-up rather than left
        # silent.
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
        # **A REPLY BIGGER THAN THE SLOT** (spec §2.1's fixed body).
        # `pipe_oversized`'s line running outward: the reply rides the SAME
        # staging slot the request came in on (design 10 ruling 4's rider), so one
        # published maximum governs both directions and overshooting it is a
        # mistake the process could have checked.
        #
        # THE CHECK IS BEFORE THE COPY AND BEFORE THE CONSUME, which is the part
        # worth a case of its own: a refused reply leaves the obligation
        # UNDISCHARGED, so a hostile length cannot quietly close somebody's
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
        # **A CLAIM THAT CAN NEVER BE ANSWERED** — RETARGETED at M4 unit 4.5
        # (`designs/010` ruling 12(b)/(c); sawos design 22). Through unit 4 this
        # case resolved a spent claim twice to meet the kernel's `BadHandle`; that
        # second call is a COMPILE error now, because `resolve` consumes on every
        # path and says so in its effect slot. The suite's last Saw-visible ledger
        # `BadHandle` retires with it, and the kernel's check stands for a
        # raw-altitude caller — `pipe_reply_wait_dead`'s precedent for a fault
        # that moved out of Saw's reach.
        #
        # What the case proves instead is the RULED FLOW, three rows of it: the
        # server drops its obligation, the client's `ready()` answers the terminal
        # ON THE ERROR CHANNEL, and the claim is DROPPED rather than resolved.
        # That last row is ruling 12(b)'s amendment executed — a reply that can
        # never come needs no second call, so the terminal does not sit where a
        # caller's instinct is to ask once more.
        #
        # **AND THE MINT PROBE ENDS IT** (ruling 12(c)): a one-shot has ONE NAME,
        # so `pipe_reply_rights()` withholds the universal `Mint` bit and asking
        # for a sibling is `pipe_no_post`'s fault at a different kind. It is the
        # LAST act deliberately — everything above it is the flow, and the flow
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
    # M4 unit 3 — waitability (sawos design 15)
    # =========================================================================
    {
        # **THE REPLY ARM: THE WAIT IS THE RESOLVE** (`designs/010` ruling
        # 11(a)). Unit 2's client polled `resolve` until the bytes were there;
        # this one attaches the claim and the BYTES ARRIVE IN THE WAIT RECORD.
        #
        # `reply len=2 b=90,89` is the record's body region carrying a reply out
        # of the ring slot that held it — the second of the two copies
        # `deliver_wait_record` makes, and the only one any kind but this makes
        # at all.
        #
        # `after delivery ...` is ruling 11(a)'s consequence and the sharper
        # claim: the exchange was DISCHARGED by the copy-out, so the claim the
        # caller kept answers `PeerClosed`. A kernel that left the slot
        # `Replied` would hand the same answer out twice, which is the duplicate
        # §2.2's consume rule exists to make impossible.
        #
        # `keyed a=11 b=22` is TWO CLAIMS ON ONE WAITER, asserted BY KEY and
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
        # **THE CONSUMING ATTACH** (`designs/010` ruling 11(c)). `give` takes the
        # wrapper by MOVE and the ATTACHMENT owns the exchange; the caller holds
        # nothing at all while the answer is in flight.
        #
        # **`depth after delivery=16` IS THE ROW THAT COULD NOT BE FAKED.**
        # `pipe_oneshot` showed a held exchange costing one ring slot (15) and
        # settling giving it back (16); this counts the same ring after a
        # delivery to a consuming attachment and gets the WHOLE depth — so the
        # delivery ended the attachment, the entry and the exchange in one act,
        # with no `resolve` anywhere in the program.
        #
        # `cancelled reply says ...` is §2.1's abandonment sentence in its
        # re-worded form (ruling 11): with the claim given away there is no
        # handle to drop, so cancelling IS destroying the subscription — and the
        # server learns exactly what the ratified text says it learns.
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
        # **THE INLET'S ROOM-TO-POST LEVEL** (`designs/010` ruling 8). `post` can
        # never block, so a refused poster parks on the client end instead of
        # polling — and the payload word is what says which readiness woke it.
        #
        # `filled=16 then room says there is space` counts the ring around
        # itself: the fill runs until the kernel refuses (so the level is
        # provably false), one exchange is settled, and the level is provably
        # true. The program never names `PIPE_INFLIGHT` — what it asserts is the
        # difference.
        #
        # `room says the peer is gone` is the other reading, and the ORDER is the
        # claim: the ring is empty there, so a kernel that answered "space" would
        # be telling a poster to write into a connection nobody can ever read.
        # `PipeInletOp.Post` asks the same two questions in the same order.
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
        # **THE SERVER'S TWO ARMS** (spec §2.2's readable level; §2.1's ratified
        # request-abandoned early notice).
        #
        # **THE TWO `readable says` ROWS ARE ONE ORDERING CLAIM.** The message is
        # staged and the WHOLE client end is then dropped, so both facts are true
        # before either wait runs — and the level chooses the message. Only after
        # the take empties the ring does the second wait say the peer is gone.
        # That is design 230's close-drains-first rule at the wait door, and a
        # kernel that reported the terminal level as soon as the sender's last
        # handle went would lose a message that had already been sent.
        #
        # ONE PERSISTENT ATTACHMENT ANSWERS BOTH WAITS, which is also the
        # one-shot footgun stated from the other side: a `OneShot` attachment
        # would have detached at the first delivery and the second wait would
        # have had an empty Waiter to park on forever.
        #
        # `abandoned` is the third arm and it carries NO payload — the fact is
        # the notification — and it is TERMINAL, so the attach happens AFTER the
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
        # **THE BLOCKING SEND WRITTEN OUT OF THE PRIMITIVES, AND THE ONLY CASE
        # IN THIS UNIT WHOSE THREADS REALLY PARK** (spec §2.1's ratified client
        # API; `designs/010` ruling 4). Two processes, two blocked threads, each
        # waking the other exactly once.
        #
        # **THERE IS NO LIBRARY `send`, AND THAT IS THE RULING THIS CASE
        # CARRIES** (user, lead review Sep 1). The composition is four calls this
        # program makes itself — post, attach, wait, and a Timer on the same
        # Waiter for the timeout leg — because a wrapper type would freeze a
        # shape unit 3.5's fused `Call` is meant to replace, and a timeout the
        # kernel ever genuinely needs arrives as an OP. What the case proves is
        # therefore that the PRIMITIVES suffice, which is a stronger claim than
        # that a wrapper works.
        #
        # **THE ORDER IS THE PROOF.** `SOS childserver: replied`, the child's
        # exit and its teardown all come BEFORE root reads the bytes — root
        # posted, attached its claim and parked, which left the kernel nothing
        # runnable but the child; the child parked on its OUTLET and was woken by
        # the post; its reply is what woke root. A polling implementation of
        # either half would print the same bytes with none of that interleaving.
        #
        # `reply len=4 b=80,79,78,71` is `PONG` in ASCII, arriving in the WAIT
        # RECORD's body region: there is no `resolve` anywhere on this path,
        # because ruling 11(a) deleted that leg.
        #
        # `filled=16 then room says there is space` is the ROOM LEG (ruling 8),
        # which a library `send` would have hidden: the ring is filled first, so
        # the poster is REFUSED and has to park on the inlet's level before it
        # has a claim at all. `pipe_wait_room` proves the level exists; this
        # proves a caller composing a blocking send has to use it.
        #
        # `timed out` is the same connection with nobody serving it — the Timer
        # on the same Waiter is what ends the wait, which is the whole of what
        # ruling 4 says a timeout is, and the kernel still knows nothing about
        # durations. THE KEY IS WHAT RESOLVES THE RACE, and the bookkeeping after
        # it (remove the loser, keep the claim) is the caller's three lines.
        # `after cancel ...` is what the still-live claim being DROPPED means:
        # §2.1's cancellation primitive, and the server told rather than left to
        # succeed silently.
        "name": "pipe_send_manual",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_SEND_MANUAL_PKG,
        "children": [CHILD_SERVER_PKG],
        # **THE CHILD SERVES TWO MESSAGES SINCE M4 UNIT 3.5** (sawos design
        # 17), which is what moved two of the rows below. `child-server` still
        # prints `replied` after its FIRST turn and still exits with `code=4`,
        # so its own lines are where they always were; what changed is that it
        # no longer DIES at the first reply, so root's report of the first round
        # now precedes the child's death instead of following it. Every string
        # in this list is byte-identical to what it was; two of them moved up
        # two places, and two are new.
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS sendmanual: gave the outlet",
                       "SOS childserver: replied",
                       "SOS sendmanual: reply len=4 b=80,79,78,71",
                       # **THE SWEEP'S HEADLINE ROW** (sawos design 16). The
                       # composition a caller writes by hand is post, attach,
                       # wait — THREE ops — and the fourth trap is the closing
                       # `stats()` call, charged at trap entry before it answers.
                       # What the number says is the thing the ruled surface has
                       # always claimed and could never demonstrate: the WAIT
                       # parked, a whole cross-process round trip happened
                       # underneath it (the child was scheduled, woke on its
                       # outlet, took and replied), and root paid ONE trap for
                       # all of it — a park is one `ecall` however long it
                       # parks, because the wake writes the answer into the
                       # parked frame rather than re-entering through the door.
                       # (The child no longer DIES there: since design 17 it
                       # serves the fused turn below as well, which is why this
                       # row now precedes its exit instead of following it.)
                       "SOS sendmanual: the round trip cost 4 traps",
                       "SOS: process exit: code={four} process={one}",
                       "SOS: process teardown handles={four} threads={one} "
                       "events={zero} waiters={one} interrupts={zero} "
                       "timers={zero} process={one}",
                       # **THE COMPANION ROWS** (sawos design 17's proof 4). The
                       # SAME exchange, the same server, the same four bytes —
                       # done the other way and measured in the same boot by the
                       # same process. `ops manual=3 fused=1` is the ladder's
                       # refactor-into-a-kernel-call story stated as a
                       # measurement rather than as a claim: post + attach +
                       # wait against `send`. The fused call also mints NOTHING,
                       # which the numbers do not show — the composition holds a
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
        # **AN UNSPENT `Wait` RIGHT** (spec §3). `pipe_no_post`'s claim made once
        # more at the door M4 unit 3 opened: all four pipe kinds gained a `Wait`
        # bit in their default sets, because attenuation is monotonic and a bit
        # not minted at creation could never appear later — so the narrowing is
        # the holder's, written once at a `MINT_OP` keep mask.
        #
        # THE MASK IS THE POLICY. `Transfer | Mint | Take` with `Wait` absent
        # describes a POLLING helper: a process handed work to drain and
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
        # **A FLAGS WORD OUTSIDE ITS DOMAIN** (spec §5.7's faults ruling).
        # `WaiterOp.Add` grew a third argument in M4 unit 3 and published exactly
        # two bits; a word carrying any other names a behaviour the kernel has no
        # reading of, and the set is published, so it is a mistake the caller
        # could have checked.
        #
        # **IT REACHES THE C ALTITUDE, AND THAT IS THE POINT RATHER THAN A
        # WORKAROUND.** The typed surface cannot spell an invalid mode —
        # `AttachMode` has two cases and consuming is a VERB — so the four legal
        # combinations are the only things a Saw caller can ask for. The kernel's
        # check is still real underneath, and this is the altitude at which a raw
        # word legitimately exists to test it, exactly as `pipe_dead_claim`
        # reaches for a spent value to show the generation check is real.
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
        # **EXACT TRAP INTROSPECTION** (sawos design 16, user-ruled Sep 1). The
        # design's dedicated proof, and the four rows below are its four claims.
        #
        # **`self delta=9 over 8 probes` IS ARITHMETIC, NOT OBSERVATION.** Root
        # brackets a window containing exactly eight `get_status` traps and
        # NOTHING else — no print, no mint, no allocation — and the delta is nine
        # because the closing `stats()` is itself a syscall and `ktrap` charges it
        # at trap entry, ahead of the dispatch. The `+ 1` is that call. The window
        # is print-free on purpose: every printed byte is one `ecall`, so a
        # measured window containing a `print` counts the prose and the assertion
        # would break on a wording change rather than on a kernel change.
        #
        # **`child syscalls=8 faults=0` IS READ AFTER THE CHILD IS DEAD.** The
        # handle root created the child with outlives it (design 3 D-3 holds the
        # slot open while anything names it), so the columns are still there —
        # which is the only way the fault column is ever observable at all, since
        # a faulting process is not around to ask about itself. Eight is
        # `child-stats`' whole life: one `process_self`, six probes, one `exit`,
        # and that child prints NOTHING precisely so the number is not a hash of
        # its own prose. Zero faults is the negative half of the same claim.
        #
        # **`interrupts=` IS THE ONE TIMING-DEPENDENT ROW IN THIS CASE and it is
        # deliberately NOT asserted** — a tick lands where the host puts it, so
        # asserting the value would be asserting the weather. It is printed
        # because the column is real and a reader wants to see what a userspace
        # `top` would read. The value is smaller than a reader expects, and the
        # reason is worth knowing: an interrupt taken while the kernel is IDLE
        # never reaches `ktrap`, because a parked root idles in `idle_until_
        # runnable`, which POLLS the controller rather than taking a vector. The
        # column counts interrupts that preempted this process while it was
        # RUNNING, which is what the ruling's "taken while this process was
        # current" means.
        #
        # **AND THE LAST TWO ROWS ARE THE RIGHT BEING REAL.** Root mints a
        # sibling of its own Process handle through a mask naming `Transfer |
        # Mint | Wait` and never `Stats` — "you may learn whether this process is
        # alive, and you may not watch it work", which is the whole reason the
        # authority is a bit of its own rather than a second use of `Wait`. The
        # read through it is `AccessDenied`, and it is a FAULT rather than a
        # status because a process knows which rights it holds (design 178's
        # faults ruling). So this case ends the way `pipe_no_post` and
        # `pipe_no_wait` end.
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
        # **THE LADDER'S PERFORMANCE CLAIM, AS A TRANSCRIPT ROW** (sawos design
        # 17; `designs/010` ruling 4's Aug-31 rider said the proof would be "a
        # ping-pong case that COUNTS TRAPS"). Design 16 built the instrument;
        # this is what it was built for.
        #
        # **ROOT SERVES AND THE CHILD CLIENTS, and the topology is a decision.**
        # The client's measured window is its WHOLE LIFE, so the client has to be
        # silent — every printed byte is one `ecall` — and a silent process
        # cannot report its own number, so its launcher reads the column through
        # the handle that outlives it. Reversed, the exactly-asserted count would
        # have belonged to the process that also has to print the report.
        #
        # **THE LOOP ENDS BY ITSELF AND THAT IS WHY THE ARITHMETIC IS CLEAN.**
        # The child's exit takes the last inlet handle with it, the outlet's
        # readable level turns terminal, and root is ALREADY PARKED in the wait
        # that delivers `PeerGone` — so termination costs zero extra traps and
        # needs no counter, no timer and no sentinel message. The peer-gone
        # doctrine is the loop's exit condition.
        #
        # `client traps=11 for 8 round trips` is `process_self` + one
        # `boot_handle_next` + EIGHT `send` calls + `exit`. **ONE TRAP PER RPC**,
        # where the composed spelling `pipe_send_manual` writes out is THREE
        # (post, attach, wait) — and the fused client also holds ZERO handles for
        # its claims, which is design 14 finding 5 answered.
        #
        # `server traps=18 for 8 messages` is one opening `wait`, then a `take`
        # and a `reply_wait` per message, plus the closing read's own trap.
        # **THE SERVER IS TWO PER MESSAGE AND NOT ONE, AND THE SECOND IS THE
        # `take`**: the readable level says "there is something", never "here it
        # is", so the take stays its own op until ruling 11(d)'s delivery-as-take
        # lands in unit 4. What the fusion removed is exactly one — the composed
        # loop is wait + take + reply, three.
        #
        # `last request b=7,80,73,78` is round 7's `PIN` with its sequence
        # number, and `code=0x...21` is the child's verdict that every one of the
        # eight replies carried ITS OWN number plus one. The two together are the
        # payload round trip proved from both ends.
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
        # **NOTHING PARKED CAN BE SILENTLY DOOMED, AND A FUSED CALL IS THE
        # HARDEST CASE THAT RULE HAS MET** (sawos design 17; `designs/010`
        # ruling 2). A thread inside `PipeInlet.send` is on NO LIST — no Waiter,
        # no joiner chain — so the only thing that can reach it is the exchange
        # it is tied to, which is what `PIPE_CALLER` and the one wake arm exist
        # for.
        #
        # TWO ARMS, TWO WAYS TO KILL AN EXCHANGE, ONE ANSWER:
        #
        #   `dropped the obligation`   the server TOOK the message and then let
        #                              the request fall out of scope. The
        #                              exchange is `Taken` and the REQUEST
        #                              column reaches zero.
        #   `closed the server end`    the server end went away with the message
        #                              still STAGED and never taken at all. The
        #                              exchange is `Staged` and the CONNECTION's
        #                              outlet column reaches zero.
        #
        # Different states, different columns, and both have to produce
        # `PeerClosed` at the client — the same word `resolve` answers with,
        # which is what says the fused and composed paths report one fact.
        #
        # **THE SECOND ARM TAKES TWO STEPS, AND THE FIRST IS NOT TIDINESS**: an
        # attachment is a counted reference on the end it watches, so root's own
        # subscription holds the server end open and the handle going is not the
        # column reaching zero. `remove` then drop. That is ruling 11's
        # abandonment re-wording seen from the server's side — hanging up means
        # destroying the subscription too.
        #
        # `child said both were closed` is read out of the §8 status word:
        # `code=0x...33` is reachable only if BOTH calls came back with that
        # exact status, so root asserts a conjunction it could not have observed
        # itself.
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
        # **THE ORPHANED-CLAIM ARM** (sawos design 17 D-2; `designs/010` ruling
        # 4's rider names it and says the unit brief must). A fused call's claim
        # is the ONE reference in the system no handle table holds — that is the
        # saving — so `end_process`'s close-all cannot reach it and the teardown
        # needed an arm of its own.
        #
        # **A PROCESS IS MADE TO DIE WITH A THREAD PARKED, which nothing outside
        # it can arrange.** SOS cannot kill a thread, so `child-orphan` is two
        # threads doing the SAME thing: one fused call each, and
        # `ProcessOp.Exit` if it is answered. Root answers exactly one, and the
        # symmetry is what makes that deterministic — root never has to know
        # which thread it is talking to.
        #
        # `two calls in flight` is both messages taken, which means both threads
        # are parked; the second take is separated from the first by a PARK
        # rather than a poll, because nothing is staged until the sibling runs.
        # `answered one` ends the process. `the orphaned claim answers ...` is
        # the arm: root still holds the second obligation, and its `reply` is
        # told `PeerClosed` — §2.1's ratified abandoned-by-client answer, reached
        # with NO NEW VOCABULARY, because the teardown dropped the claim and
        # `pipe_reply` read the columns exactly as it has since unit 2.
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
        # **THE FUSED CALL SPENDS `PipeInletRight.Post`** (sawos design 17's
        # reviewed API), which is `pipe_no_post`'s case at the second op on the
        # same object — and the pair is what says the two ops share one gate. A
        # right of its own was refused deliberately: `Call` submits a message
        # exactly as `post` does and what it adds is a park, which needs no
        # authority because no Waiter is involved.
        #
        # **IT FAULTS BEFORE IT COULD EVER PARK**, and that is worth a case of
        # its own for a blocking op: the check is in `pipe_inlet_op_decoded`,
        # above `pipe_call`, so nothing is staged and no thread is tied to an
        # exchange. A call that got past the gate here would park for ever —
        # nobody serves this connection — and the deadlock report would be the
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
        # **A `send` LONGER THAN THE PUBLISHED BODY IS A FAULT** (sawos design
        # 17), which is `pipe_oversized`'s claim at the fused op. The maximum is
        # a `sosabi` constant the caller imports, so a length above it is a
        # mistake it could have checked — design 178's line for what stays
        # outside the status enum entirely.
        #
        # BOTH ENDS ARE HELD, so `PeerClosed` is not an answer this could have
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
        # **THE REPLY LEG FIRES, AND IT DOES NOT PARK** (sawos design 17; §2.1's
        # abandonment paragraph). `reply_wait` does two things in one trap, so
        # the ORDER of its failures is a contract: a reply that cannot land
        # returns AT ONCE and the wait leg never runs, which is what keeps one
        # abandoned exchange from taking a server loop down with it.
        #
        # **THE EMPTY WAITER IS THE ASSERTION.** Nothing is attached to the
        # Waiter the op is handed, so a park would have nothing to wake it and
        # the case would end `every thread blocked` — `wait_deadlock`'s own
        # report. Reaching `done` is the proof that no park happened.
        #
        # **IT USED TO PROVE A SPENT OBLIGATION IS `BadHandle`, AND THAT PROOF
        # IS NOW A COMPILE ERROR** (SL-16, sawc 0.4.0). `reply` and `reply_wait`
        # are `consumes` methods, so the second use through a spent wrapper —
        # which is how this case reached the fault — no longer compiles. The
        # kernel check is untouched and still stands for a raw-altitude caller;
        # a Saw program simply has no spelling for the mistake any more, and
        # `pipe_dead_claim` still draws the same fault through `resolve`, which
        # stays pollable and therefore stays non-consuming. The rows below moved
        # for that reason and no other.
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
        # **A CAPABILITY OUTLIVES THE PROCESS THAT SENT IT** (sawos design 18,
        # M4 unit 4; `designs/010` ruling 5 as re-ruled Sep 2 -- take-at-post).
        # The child posts a live connection END and exits at once; root parks on
        # the DEATH, then takes, then posts through what arrived and reads its
        # own byte back off the far end. Every capability root spends there
        # belongs to a process that no longer exists.
        #
        # THE SENDER'S TEARDOWN COUNT IS ASSERTED and is the write-off half of
        # the proof: TWO handles closed -- its boot System and the Process it
        # derived. The gift is not among them, because it stopped being the
        # child's at the post; under sender-keeps this row would read three and
        # the entry it closed would have been the one root is about to be
        # handed, so the case could not have finished at all.
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
        # **THE RENDEZVOUS REFUSES ATOMICALLY** (sawos design 18; `designs/010`
        # ruling 5's second consequence). Three table states, and the middle one
        # -- one free slot, still refused -- is the row that separates an atomic
        # refusal from a partial delivery.
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
        # **HANDLES IN MESSAGES, BOTH DIRECTIONS, WITH THE LEDGER OBSERVED**
        # (sawos design 18; spec 2.1's send(data?, handles?)). The three
        # free-slot readings are printed and NOT asserted -- they are a property
        # of root's own table at three moments -- while the VERDICT the case
        # computes from them is.
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
        # **THE DELEGATION PRIMITIVE, ARRIVING AS A MESSAGE** (sawos design 18;
        # spec 2.1's ratified zero-copy example). Root's own question is
        # answered by a process that holds no end of the connection it was asked
        # on, and the four bytes prove the payload never transited the middle.
        #
        # THE ROUND TRIP GOES THROUGH TWO PIPES RATHER THAN THREE PROCESSES:
        # the mechanism under test is a one-shot crossing a process boundary
        # INSIDE A MESSAGE, and the third party in 2.1's example differs from
        # this child in nothing the kernel can see. It was written that way
        # because MAX_PROCESSES was 2; **the three-process spelling landed with
        # M4 unit 5 as `pipe_delegate_3p` below** (sawos design 21), and THIS
        # CASE STAYS UNMOVED beside it, because the two prove different things:
        # this one that the mechanism is the message, that one that the middle
        # is a different principal.
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
        # **THE COMPLETION-QUEUE SERVER: ONE TRAP PER MESSAGE** (sawos design
        # 18; `designs/010` ruling 11(d)). The client is `child-pingpong`,
        # UNCHANGED from unit 3.5, so the two server numbers are measured over
        # identical work: 18 traps for eight messages there, 10 here.
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
        # **ATTENUATE AT GIVE, AND AT THE DERIVE** (sawos design 18;
        # `designs/010` agenda 7b). The case ends on its own negative arm --
        # root reads stats through a handle it derived without `Stats` -- which
        # is pipe_no_post's shape and the only one available when the fault
        # kills the reader.
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
    # =========================================================================
    # M4 unit 4.5 — the one-shot discipline (sawos design 22)
    # =========================================================================
    {
        # **RESOLVE PARKS** (`designs/010` ruling 12(b)). Root posts and then
        # RESOLVES, with NO Waiter anywhere in the process and no attachment at
        # all: the resolve finds the exchange pending and blocks the calling
        # thread on the claim's own ready level, and a child server's reply is
        # what wakes it, inside its own syscall, with the bytes in its own
        # buffer.
        #
        # **REACHING THE END IS THE PROOF.** Through unit 4 a pending resolve
        # answered `WouldBlock` and consumed nothing, so this exact program would
        # have fallen through with no reply and failed its own length check.
        # There is nothing else in root that could have produced the answer.
        #
        # **THE ORDER IS THE SECOND HALF OF IT**, and it is `pipe_send_manual`'s:
        # `SOS childserver: replied` comes BEFORE root's row, because root parked
        # with nothing else runnable and the kernel had to go find the child; the
        # child parked on its OUTLET and was woken by root's post.
        #
        # `ops resolve=2 fused=1` is the sweep row (sawos design 16's bracket).
        # Post plus resolve against the fused `send`, measured by the same
        # process against the same server in the same boot, each delta less its
        # own closing `stats()` trap. Unit 3's composition was THREE for the same
        # answer — post, attach, wait — which is what ruling 12(b) took a trap
        # out of for a client that wants a HANDLE on its exchange.
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
        # **THE ORPHANED-CLAIM ARM AT A HANDLE-BACKED CLAIM** (`designs/010`
        # ruling 12(b)'s `end_process` mirror; sawos design 22).
        # `pipe_call_orphan` with the other spelling: the child's threads park
        # inside a `resolve` rather than inside a fused `send`, and the launcher
        # answers one, watches the process die, and asks the ORPHANED obligation
        # what the teardown decided.
        #
        # **WHAT THE ROW PROVES IS THAT NOTHING WAS ADDED.** `park_resolve`
        # unbinds the caller's entry and KEEPS its reference, so from the park
        # onward a parked resolver and a parked fused caller are the same thing:
        # one reference on the claim column, `PIPE_CALLER` naming the thread.
        # `release_pending_calls` walks threads with a claim tie and never asks
        # which op put it there, so this case reaches the arm through code
        # written for unit 3.5.
        #
        # **WHY A SIBLING CASE AND NOT AN ARM ON `pipe_call_orphan`.** The proof
        # needs the ORPHANED thread to be the shape under test, and one
        # two-thread child can orphan only one shape — whichever thread the
        # launcher answers is the one that exits. `child-orphan`'s two threads
        # are interchangeable ON PURPOSE, which is what makes that case
        # deterministic; making them differ would have traded a fact for a scan
        # order. Two cases, one spelling each, keeps both proofs.
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
    # M4 unit 5 — DRIVER-AS-SERVICE, THE MONEY SHOT (sawos design 21)
    # =========================================================================
    #
    # FIVE CASES ACROSS THE TWO PROFILES, and the first pair is what the whole
    # M4 ladder was for. M2 put a driver in userspace; M3 unit 4 made its window
    # a capability and unit 6 handed that capability to a CHILD; M4 built the
    # connection, the message, the one-shot pair, the waitable levels and the
    # fused paths. Here they meet: one process owns the UART and TWO OTHERS read
    # it through a pipe, over a protocol none of them got from the kernel.
    #
    # **THE M3 TWINS AND THE M2 ANCESTORS ALL STAY, UNMOVED.** `uart_echo_*`
    # (root as driver), `child_echo_*` (driver as a child) and these — the same
    # device, the same four bytes, three transcripts — say what changed each
    # time, which is never the driver's device half.
    #
    # **THESE ARE THE FIRST CASES IN THE TREE WITH TWO CHILDREN AT ONCE**, which
    # is what `MAX_PROCESSES = 3` bought and what `hal/*/user/child2.ld` came
    # with. `_region_rows` assigns destinations BY INDEX, so a package's linker
    # script is a contract with the order it is listed in here.
    {
        # **THE MONEY SHOT ON RISCV32.** Read the transcript as an order:
        #
        #   read posted      root's `Read` is staged BEFORE the client process
        #                    exists, so it is first in the connection's FIFO
        #                    ring by construction rather than by timing — which
        #                    is what pins the ORDER of the two outstanding reads
        #                    before a single input byte has arrived.
        #   client started   and now there are two, on one connection, from two
        #                    processes, through two sibling inlets.
        #   Zq7#             the harness's phrase, echoed — the FIRST byte by
        #                    root through its own claim and the rest by the
        #                    client through its own. Two claims, two tables, one
        #                    driver, and each reply landed at the claim that
        #                    asked for it.
        #   root wrote ...   root's own round, after the client is dead: two
        #                    TELLs and a `Sync`, so a launcher's prose comes out
        #                    of a transmitter it does not own.
        #   served=12 ...    the driver's own accounting, and every number in it
        #                    is deterministic: twelve messages (root's read and
        #                    echo, the client's three reads and three writes, and
        #                    root's four-message closing round), nothing
        #                    abandoned, nothing dropped.
        #   messages=4 ...   the MEASURED WINDOW — `Mark` opens it and the loop's
        #                    end closes it, and there is no input left by then so
        #                    no interrupt can fall inside it. Four messages, five
        #                    traps: ONE TRAP PER MESSAGE plus the closing
        #                    `stats()` read. That is `pipe-cq`'s ladder end state
        #                    measured in a real driver rather than in a bench.
        #
        # **WHAT `Zq7#` RULES OUT** is everything at once: the give, the map, the
        # bind, an IRQ waking a process that is not root, a request crossing a
        # process boundary, a one-shot reply pair carrying the answer back, and
        # the whole thing again through a SECOND client's inlet. There is no way
        # to fake it on a machine with no translation and no driver in the
        # kernel.
        "name": "uart_service_ns16550",
        "arches": ["riscv32", "riscv32-flat"],
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": UART_SERVICE_PKG,
        "children": [SVC_UART_NS16550_PKG, SVC_CLIENT_PKG],
        "device": True,
        "stdin": ECHO_INPUT,
        "expect_out": ["{banner}",
                       "root image ok segments={two}",
                       # FIVE rows: two image blobs, two destinations, the
                       # device window. The first case in the tree to publish
                       # more than three.
                       "SOS: boot regions={five}",
                       "SOS: console handover",
                       "SOS uartsvc: created",
                       "SOS uartsvc: driver started",
                       "SOS uartsvc: read posted",
                       "SOS uartsvc: client started",
                       ECHO_INPUT,
                       "SOS uartsvc: root wrote this line through the driver",
                       "SOS uartsvc: and root does not hold the device",
                       # 0x5A — 'Z', the first byte of the phrase, which is the
                       # one root's own claim was answered with.
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
        # **ONE DEAD READ CANNOT WEDGE A DRIVER**, and there is NO STDIN in this
        # case, which is what makes it exact. The device never raises its line,
        # so the whole run contains no interrupt and the driver's trap
        # accounting is arithmetic rather than a measurement.
        #
        # Root asks for a byte nobody will type, times out on its OWN Waiter
        # with its OWN Timer — the kernel still does not know what a timeout is
        # (`designs/010` ruling 4) — and DROPS ITS CLAIM. That release is the
        # entire cancellation: no op, no message, no correlation. The driver
        # learns through the request-abandoned level it was already watching
        # because it was already holding the obligation, and `abandoned=1` is
        # that arm firing.
        #
        # **THE ORDER IS FORCED.** When the timer fires the driver has been
        # parked in `wait` for twenty milliseconds, so root's release wakes it
        # THERE with that record, before root's next message is even posted.
        # Twenty milliseconds is three orders of magnitude more than the driver
        # needs to reach its park, which is `process-lifecycle`'s own argument.
        #
        # `served=5` is the read, the mark, two writes and the sync — and the
        # window numbers are the SAME 4/5 the money shot prints, from the same
        # driver program, which is what makes them about the loop rather than
        # about a case.
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
                       # THE TIMER, not the claim: on a console nobody is typing
                       # at, a claim that answered would mean the driver
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
        # **DELEGATION ACROSS THREE PROCESSES** — the spelling unit 4 deferred
        # on exactly the `MAX_PROCESSES` bump this unit makes.
        # `pipe_delegate_msg` proved the MECHANISM with two pipes and one child
        # and said so in as many words; what three processes add is that the
        # middle is a genuinely different principal from both ends, with its own
        # table, its own quota and its own death.
        #
        #   reply len=4 b=68,79,78,69   `DONE`, written by a process that holds
        #                               no end of the connection root is parked
        #                               on, into a ring slot root owns both ends
        #                               of — and the byte root posted never
        #                               reached it.
        #   middle=65607 far=65617      both §8 status words: `Exited` in the
        #                               high half, and codes that appear nowhere
        #                               else in any transcript.
        #
        # **THE MIDDLE IS USUALLY DEAD BY THE TIME THE ANSWER LANDS**, which
        # take-at-post makes irrelevant (ruling 5 as re-ruled Sep 2). Under
        # sender-keeps this ordering would have been a race; `pipe_send_exit` is
        # where that independence is the point rather than a footnote.
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
    # M5 unit 5 — the real allocator: spent bytes come back (sawos design 28)
    # =========================================================================
    #
    # ONE NEW CASE, and its sibling is a RETARGET: `mapping_slot_free` above now
    # proves the second zero from the region's side (its slot survives a handle
    # drop while a row counts against it, and the range returns at the last
    # unmap). This one proves the pool itself, and is APPENDED rather than filed
    # beside the M3 memory cases on purpose — the report is printed in
    # case-definition order, so a case added at the end leaves every existing
    # case's ordinal exactly where a reader of an older transcript left it.
    {
        # **THE PROOF THAT A POOL IS A POOL** (sawos design 28; design 25 D-5).
        #
        # `quarters=4` then `whole=1` is the allocator's whole arithmetic in two
        # numbers: four 64 KiB pieces are the ENTIRE 256 KiB pool, they are
        # released OUT OF ORDER — which forces the free list, two disjoint holes
        # and a three-into-one merge rather than a tidy LIFO fold — and what
        # comes out afterwards is ONE region the size of the pool. A cursor that
        # had not taken all four back could not serve that split, and a `split`
        # too big is a `BadArg` FAULT, so a kernel without pool returns dies on
        # that line instead of printing a smaller number.
        #
        # `reused read 60` is the second zero across a process boundary, and it
        # is the sharpest form of §2.5's sentence: root maps a page into a child
        # and then destroys BOTH of its own objects — the Memory handle and the
        # Mapping — leaving the child's protection ROW as the only reference to
        # that region. The bytes come home at the child's TEARDOWN, and the mark
        # read back is the child's own (`{touch_mark}`, the same byte
        # `map_into_child` asserts from the other side).
        #
        # The child is `child-touch` unchanged: it writes its mark at the pool
        # base and exits with it, which is exactly the witness this case needs.
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
    # M5 unit 6 — slab donation: capacity becomes policy (sawos design 32)
    # =========================================================================
    #
    # APPENDED, for `memory_recycle`'s reason one section up: the report prints
    # in case-definition order, so a case added at the END leaves every existing
    # case's ordinal where a reader of an older transcript left it.
    #
    # The case asserts that the ceiling MOVED and never what it moved TO. A
    # donated extent holds `page / sizeof(WaiterSlot)` slots and that divisor is
    # per-profile, so the new ceiling is arch-dependent while every line below is
    # not — see the package's own header.
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

    # THE NEGATIVE, and it is a FAULT case: the safety condition's `refs == 1`
    # half. Root mints a sibling onto the region it then tries to donate, and
    # the kernel ends it — "object in the wrong state" is `FaultReason.BadState`,
    # the same wording `process_doublestart` above asserts. The `maps == 0` half
    # is deliberately uncovered; design 32's As-built names the gap.
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

    # **SHARED AT PER-PROCESS ADDRESSES** (sawos design 33, M5 unit 2) — the
    # case §5.5's old identity law forbade. One page, two domains, two DIFFERENT
    # addresses, and both processes read and write the same bytes through their
    # own.
    #
    # **THE ADDRESSES ARE ASSERTED AS LITERALS AND THAT IS DELIBERATE.** They are
    # what the kernel's VA policy answers — first fit at or above a process's own
    # region top, every image linked at `hal.USER_IMAGE_BASE` = 0x4020_0000, a
    # region 256 KiB — so root's spacer and the child's only mapping both land at
    # 0x4024_0000 (1076101120) and root's second mapping one page higher at
    # 0x4024_1000 (1076105216). Pinning them is what makes the case an oracle rather than a
    # tautology: a policy change has to come here and say so.
    #
    # The two witnesses are independent. The console lines say the two processes
    # got different numbers; the status word (65731 = `Exited`(1) << 16 | 195)
    # says the child really read the byte root wrote, through a path no console
    # line goes near.
    #
    # **AARCH64 ONLY, and the `arches` key is this unit's gate as much as its
    # scope.** riscv32 is untouched by M5 unit 2 and its half of the transcript
    # is required to be byte-identical, so a case that ran there would move 119
    # rows to prove something that tier does not claim.
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

    # M5 UNIT 6a (sawos design 34): the two kinds unit 6 excluded.
    #
    # THE RISK ONE FIRST. A donated thread is not proven by a count — what design
    # 32 declined this kind over is the context-switch path, so the case starts
    # the thread whose slot and whose FRAME are both in donated memory, watches it
    # yield three times, and reads its exit code back through a join. `TTT` is the
    # three switches; `code=33` is the register file surviving them.
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

    # AND THE ONE UNIT 6 EXCLUDED ON MERIT. A connection in extent 1 carries a
    # real message end to end — post, take, reply, resolve — so the staging ring
    # that lives in donated memory beside its connection slot is shown to work
    # rather than merely to exist.
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

    # GAP CLOSER 1 (design 32 finding 2): the `maps == 0` half of the safety
    # condition, which unit 6 implemented and did not test.
    #
    # THE DONOR IS THE CHILD, AND THAT IS WHAT MAKES THE SECOND HALF OBSERVABLE.
    # The refusal is a FAULT, so the donor dies — and "the region was not
    # absorbed" cannot be read out of a dead process. So root maps the page into
    # its OWN space, stamps a witness byte through the mapping, hands the region
    # to a child carrying `SlabDonate`, and reads the witness back AFTER the child
    # has faulted. A donation that had been accepted would have zeroed those
    # bytes before installing them.
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
                       # Faulted(2) << 16 | BadState(5) — the child died of the
                       # `maps` leg specifically, which is the claim.
                       "SOS slabmapped: child faulted status=131077",
                       "SOS slabmapped: witness still 165",
                       "SOS slabmapped: done"],
        "expect_clean_exit": True,
    },

    # GAP CLOSER 2 (design 32 finding 6): the unit-5 composition, which that unit
    # filed with its shape and could not reach.
    #
    # IT IS A TWO-STAGE DONATION, and the first stage is the very op under test.
    # A free-range node is only ever spent on a HOLE, so exhausting the node slab
    # needs more non-adjacent free ranges than `MAX_MEMORIES` can hold live
    # regions to make — the pool physically cannot be fragmented that far on a
    # stock image. Donating to `Memories` first is what lifts that wall.
    {
        "name": "slab_donate_free_nodes",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": SLAB_DONATE_NODES_PKG,
        "pool": True,
        # riscv32 ONLY, and the package header carries the arithmetic: the
        # construction needs strictly growing cuts (first fit would otherwise
        # serve them out of the holes it just made), the step is one
        # `hal.PROT_GRAIN`, and at the other profile's 4096-byte grain the 32
        # holes alone would want 2.1 MiB against a pool that cannot exceed the
        # grant window. Design 34's As-built records the asymmetry.
        "arches": ["riscv32", "riscv32-flat"],
        "expect_out": ["{banner}",
                       "SOS: boot regions={one}",
                       "SOS slabnodes: pool trimmed to the construction",
                       "SOS slabnodes: fragmented holes=32",
                       "SOS slabnodes: released one more past the node slab",
                       "SOS slabnodes: donated a page of free ranges",
                       "SOS slabnodes: the range came back",
                       # THE ASSERTION. R2's range came home and R1's did not, so
                       # the second ask of the pair cannot be served and the
                       # kernel ends the caller — `MemoryOp.Split`'s `BadArg`.
                       "SOS: process fault: argument outside its domain "
                       "process={zero}",
                       "SOS: process teardown handles="],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
    },

    # M5 unit 6b (sawos design 36): THE LAST OF THE THREE KINDS. A process's
    # storage was `MAX_PROCESSES`-shaped in five places at once; unit 6b made
    # four of them fields of `ProcessSlot`, which is what let the kind convert.
    #
    # It lists THREE children and asks for NO POOL, which is why the runner's
    # two-children-plus-a-pool refusal never comes up: the region root donates is
    # cut out of the third child's own destination row, after that child's span
    # has been carved off its front.
    {
        "name": "process_donate",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PROCESS_DONATE_PKG,
        "children": [ECHO_CHILD_PKG, ECHO_CHILD2_PKG, ECHO_CHILD3_PKG],
        "expect_out": ["{banner}",
                       "SOS: boot regions={six}",
                       # CLAIM 1: root plus two children IS the table.
                       "SOS procdonate: floor=3 third child refused: "
                       "out of kernel objects",
                       # CLAIMS 2 and 3.
                       "SOS procdonate: donated a region of process slots",
                       "SOS procdonate: third child created past the floor",
                       "SOS procdonate: started three children",
                       # CLAIM 4: each child is a whole process. The third one's
                       # line is a syscall made through a handle table that lives
                       # in donated memory.
                       "SOS echo0: alive",
                       "SOS echo1: alive",
                       "SOS echo2: alive",
                       "SOS procdonate: exits a=65577 b=65578 c=65579",
                       # CLAIM 5: the donated slot is reclaimed and spent again,
                       # at a new generation.
                       "SOS procdonate: donated slot reused d=65579",
                       "SOS procdonate: done"],
        "expect_clean_exit": True,
    },

    {
        # =====================================================================
        # M5 UNIT 4 — THE TIER WORD ITSELF (sawos design 35)
        # =====================================================================
        #
        # The platform is asked what it can DENY and says so, twice with the same
        # answer; then the ask is made through a handle minted WITHOUT the right
        # and the kernel ends the process.
        #
        # **FLAT-ONLY, AND THE `arches` KEY IS A TRANSCRIPT FENCE RATHER THAN A
        # CLAIM ABOUT THE CASE.** This case is meaningful on all three profiles
        # and would pass on all three — `{tier}` substitutes `Isolated` on the
        # two protected ones — but design 35 requires the riscv32 and arm64
        # sections to stay BYTE-IDENTICAL to the pre-unit baseline, and a case
        # added to their lists moves every `[i/N]` row in both by changing N.
        # So the word's ISOLATED answer is witnessed by a probe recorded in
        # design 35's As-built rather than by the gate, and promoting this case
        # to all three profiles is queued for the unit that next holds an
        # authorization to move those rows (unit 8, the M5 docs sweep).
        #
        # It is APPENDED, per the convention this file states above: the report
        # prints in case-definition order, so a case added at the END leaves
        # every existing case's ordinal where a reader of an older transcript
        # left it.
        "name": "tier_word",
        "arches": ["riscv32-flat"],
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": TIER_WORD_PKG,
        "expect_out": ["{banner}",
                       # THE WORD. `{tier}` is the HARNESS's claim about this
                       # profile and the line is the KERNEL's answer, so this row
                       # is the two of them agreeing — the one place in the suite
                       # where the runner's tier table is checked against the HAL
                       # that implements it.
                       "SOS tier: {tier}",
                       "SOS tier: stable",
                       "SOS tier: kernel boundary intact",
                       # AND THE BOUNDARY, ON THE FLAT TIER. A right is not
                       # tiered: the sibling minted without `TierGet` is refused
                       # here exactly as it would be on hardware that can deny,
                       # because this refusal is the KERNEL's and not the
                       # platform's. It is the positive statement of what the
                       # tier word does NOT disclaim.
                       "SOS: process fault: access denied process={zero}",
                       "SOS: process teardown handles="],
        "expect_clean_exit": False,
        "expect_status": EXIT_PROCESS_FAULT,
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
    """`work(item)` over `items`, `jobs` at a time, YIELDING IN ITEM ORDER.

    THE ORDERING IS THE WHOLE POINT. A parallel suite that reported in
    completion order would print a different transcript every run, and the
    harness's oracle tradition is to DIFF the transcript rather than read
    "green" — so the report has to be a function of the case list and nothing
    else. Submitting every item up front and then awaiting the futures IN
    SUBMISSION ORDER gives exactly that: work overlaps, results arrive in the
    order the caller asked for them, and each one prints the moment the head of
    the queue is ready rather than at the end of the run.

    Threads, not processes: every unit of work below is a `subprocess.run`, so
    the GIL is released for the duration and a process pool would buy nothing
    but a pickling constraint on the case dictionaries.

    `jobs <= 1` runs INLINE, on this thread — not a one-worker pool. The serial
    harness is the reference the parallel one is diffed against, so `-j 1` takes
    no code path that `-j 4` introduced.

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


def _find_clang(arches):
    """Return the first clang that can assemble EVERY target's boot code.

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
            # The BOARD's boot.S (sawos design 35): a profile that is a variant
            # of an existing board has none of its own.
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

    Design 162 unit 1: the deliverable is as much the SEAM as the port. A
    kernel that names its architecture still compiles — the leak only shows up
    on the OTHER target, whenever someone next ports — so the check that keeps
    the seam honest has to be mechanical. Comments count: a note that says
    `mepc` is a note that will be wrong on the profile that has no `mepc`.
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
        print("  (design 162 unit 1: kcore reaches the machine through `hal` "
              "only)", file=sys.stderr)
        return False
    return True


def _build_shared(arch, clang):
    """Build one architecture's native objects once; return them as a list.

    A kernel image is that architecture's kernel HAL (boot + trap entry, the
    board sinks, the protection primitive) plus the shared C floor every SOS
    build links — the same `support.c` a root package names in its
    `[sos.<triple>] native`, which since design 172 part 2 is `mem*` and the
    atomic libcalls and nothing else. The runtime seams it used to carry are
    Saw, and reach the image through `--module-path sosrt=` below.
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
    # design 23: any SHARED assembly this architecture's HAL splits out of its
    # board `boot.S` — the riscv32 trap entry and M -> U transition, which are
    # the ISA's rather than the board's. arm64 brings none.
    asm_objs = []
    for rel in arch["hal_asm"]:
        obj = os.path.join(build, os.path.basename(rel).replace(".S", ".o"))
        _run([clang, f"--target={arch['triple']}", *arch["cc_args"],
              "-nostdlib", "-c", os.path.join(HAL_DIR, rel), "-o", obj])
        asm_objs.append(obj)
    # `sink.c` comes from `hal_native`, which is this board's own directory on
    # arm64 and the shared `riscv32-common` one on riscv32 (design 23).
    for src, obj in ((os.path.join(dirs["hal_native"], "sink.c"), sink_o),
                     (os.path.join(RT_COMMON_C_DIR, "support.c"), support_o)):
        # -fno-builtin: support.c DEFINES memcpy, and without it LLVM may
        # rewrite its byte loop into a call to itself.
        _run([clang, f"--target={arch['triple']}", *arch["cc_args"],
              "-ffreestanding", "-fno-builtin", "-ffunction-sections",
              "-fdata-sections", "-nostdlib", "-O2", "-c", src, "-o", obj])
    return [boot_o, *asm_objs, sink_o, support_o]


def _build_blade(build_dir):
    """Get a Blade to drive: the resolver's, or one built from its sources.

    The `blade_bootstrap.py` stage0 step, reused: the SOS root packages are
    built BY BLADE, so the harness needs a Blade to drive.

    A resolved binary (design 238 D-b: `BLADE=…`, or one found on `$PATH`)
    is used as it is. Otherwise the toolchain came with a CHECKOUT and Blade
    is built out of it — which is what keeps a sawlang checkout testing its
    OWN package manager rather than whatever happens to be installed.
    """
    resolved = tc().blade_binary()
    if resolved:
        return resolved
    blade_bin = os.path.join(build_dir, "blade")
    _run(tc().sawc() +
         [os.path.join(tc().blade_package_dir(), "src", "main.saw"),
          "-o", blade_bin,
          "--module-path", tc().module_path_arg("toml"),
          "--module-path", tc().module_path_arg("semver"),
          "--module-path", tc().module_path_arg("imgformat")])
    return blade_bin


def _blade_env(clang):
    env = dict(os.environ)
    env["SAWC"] = tc().sawc_env_value()
    # macOS's Apple clang mis-drives the riscv integrated assembler; hand Blade
    # the same clang this harness probed for.
    env["SOS_CLANG"] = clang
    return env


def _build_root_image(blade_bin, pkg_dir, arch, clang):
    """`blade build --target <triple>` a root package; return its sosimg path.

    Always `--force`: the harness's job is to prove the CURRENT tree boots, and
    Blade's build avoidance keys on content it cannot see change here (the
    kernel side, the linker script's meaning).
    """
    # Design 143: artifacts live under `<package>/.build/<target>/`, never
    # beside the source — which is also what lets one package hold two
    # architectures' images at once.
    out_dir = os.path.join(pkg_dir, ".build", arch["triple"])

    # Delete first: a build that fails must not leave the PREVIOUS image lying
    # around to be booted as if it were current. (Blade used to exit 0 on a
    # failed build, which is exactly how a stale image once passed this suite.)
    if os.path.isdir(out_dir):
        for stale in os.listdir(out_dir):
            if stale.endswith(".sosimg"):
                os.remove(os.path.join(out_dir, stale))

    _run([blade_bin, "build", "--force", "--target", arch["triple"]],
         cwd=pkg_dir, env=_blade_env(clang))

    # The image is named for the PACKAGE, which need not match its directory.
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

    **THE STAGING DIRECTORY IS PER CASE**, which is what makes this step safe to
    run concurrently. `kernel/rootimg.S` is committed and names a FIXED
    filename, so the case cannot be distinguished by the file's name the way
    every other artifact here is (`<case>.o`, `<case>.elf`, `<case>.regions.S`);
    it has to be distinguished by the directory the `-I` points at. Staged into
    one shared build directory, two cases running at once would each copy their
    own root image over `root.sosimg` and one of them would boot the other's
    image — a wrong-but-plausible transcript, which is the worst failure a test
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
    """Emit one 64-bit little-endian field, however wide this target's `.word` is.

    The region table's fields are 64-bit on BOTH profiles — the same decision
    sosimg v3 made about addresses, and for the same reason: one layout both
    kernels read. A 32-bit assembler has no 8-byte relocation to point at a
    symbol with, so a 32-bit target writes the low half and a zero high half,
    which is the same bytes little-endian. A 64-bit one writes the field.
    """
    if arch["hex_width"] == 8:
        lines.append(f"    .4byte {expr}")
        lines.append("    .4byte 0")
    else:
        lines.append(f"    .8byte {expr}")


def _emit_row(lines, arch, base_expr, len_expr, kind):
    """Emit one VERSION 2 region row: {base u64, len u64, kind u8, 7 reserved}.

    24 bytes, which is what keeps the NEXT row 8-aligned without a `.balign`
    the kernel's overlay would have to know about.
    """
    _emit_word64(lines, arch, base_expr)
    _emit_word64(lines, arch, len_expr)
    lines.append(f"    .byte  {kind}")
    lines.append("    .byte  0, 0, 0, 0, 0, 0, 0")


def _region_rows(case, arch):
    """Which rows this case's table carries, in ORDER — the whole contract.

    ROW ORDER IS THE CONTRACT, and it is stated here because it is stated
    nowhere else. Root's config reads it as tags:

        every blob row       one per child, in the order the case lists them
        every destination    one per child, same order
        the RAM POOL         if the case asks for one
        the DEVICE window    if the case asks for one

    Blobs and destinations come first so that every M3-unit-2 case's tags are
    exactly what they were (tag 0 is child 0's image, tag N is child 0's RAM),
    and the two unit-4 rows APPEND rather than insert. A case that wants only a
    pool gets tag 0 for it; that is not a special case, it is the same rule with
    no children in front.

    Each row is (comment, base-expression, length-expression, kind). A base may
    be a linker EXPRESSION rather than a number — that is what makes a blob row
    work at all, since the address is whatever `ld.lld` decides.
    """
    children = case.get("children", ())
    # **TWO CHILDREN AND A POOL CANNOT COEXIST**, and the refusal is here rather
    # than in a comment because the collision is silent otherwise. The pool base
    # was chosen as "one region above the one child region above root", so with
    # two children the second one's destination IS the pool's window — and the
    # table would publish two rows naming the same bytes under two kinds. M4
    # unit 5 is the first case with two children; the day one wants a pool too,
    # widening the layout is a decision for this file to make out loud.
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
    """Append this case's child images and emit the BOOT REGION TABLE.

    THE RULED HYBRID (sawlang#232 agenda item 2; sawos design 2 D-2): the
    stitcher appends each child sosimg AS IT IS — no flattening, no absolute
    placement, the blob lands wherever the linker puts it — and records offsets
    and lengths in a table. The kernel mints one object per row and interprets
    nothing; ROOT's config is what says which ordinal is which.

    **IT EMITS THREE KINDS OF ROW SINCE M3 UNIT 4** (sawos design 6 D-5), which
    is why it is no longer named for children: a case asks for `children`, a
    `pool`, a `device` window, or any combination, and `_region_rows` above is
    the order they land in. The KIND COLUMN is what the kernel reads — a Ram row
    mints a Memory, a Device row mints an IoMemory — and it is the only thing in
    a row the kernel interprets at all.

    THE STUB IS GENERATED RATHER THAN COMMITTED, and that is what buys the
    linker-resolved bases: a blob row names the child section's own symbols, so
    `ld.lld` fills in the address when it places the section and nothing here
    computes one. A committed stub would have had to be per-case anyway (the
    number of children is a property of the case), so generating it costs a file
    nobody has to keep in step and removes the one thing that could have gone
    wrong.
    """
    dirs = arch_dirs(arch)
    os.makedirs(dirs["build"], exist_ok=True)
    name = case["name"]
    children = case.get("children", ())

    lines = [
        "/* GENERATED by tools/sos_runner.py — sawos design 2 D-2, 6 D-5. */",
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

    A case may name an extra `.S` payload, taken from THIS architecture's test
    directory, which lands in the `.payload` section the HAL's linker script
    bounds — unit A's raw user-mode code, and from unit B on the `.incbin` stub
    that carries the root sosimg.
    """
    dirs = arch_dirs(arch)
    os.makedirs(dirs["build"], exist_ok=True)
    name = case["name"]
    obj = os.path.join(dirs["build"], f"{name}.o")
    elf = os.path.join(dirs["build"], f"{name}.elf")
    # design 135: `--no-hidden-alloc` rides along on every kernel build. The
    # kernel logs through the alloc-free formatting path already (design 137's
    # dogfood), so this costs nothing today and is what keeps it that way — an
    # interpolated log line or an escaping closure added later fails the gate
    # instead of quietly reaching for an allocator the kernel may not have.
    # design 172 part 2: `--runtime-provider` (design 149) says this compile
    # IMPLEMENTS the frozen `__saw_rt_*` seams rather than merely calling them.
    # It has to be here rather than in a manifest because a kernel image is not
    # a Blade package: `sosrt` carries the seam bodies and rides in on
    # --module-path, so THIS is the compile that defines them. Without it the
    # `@export`s are refused by name; with it every signature is checked against
    # sawc/rt/ABI.md.
    cmd = tc().sawc() + [case["src"], "-o", obj,
                         "--freestanding", "--no-hidden-alloc",
                         "--runtime-provider", "--target", arch["triple"]]
    if arch["features"]:
        cmd += ["--target-features", arch["features"]]
    cmd += ["--module-path", CORE_MODULE,
            "--module-path", f"hal={dirs['hal_kernel']}",
            "--module-path", tc().module_path_arg("imgformat"),
            "--module-path", SOSRT_MODULE,
            "--module-path", SOSABI_MODULE]
    # design 23: a riscv32 board's `hal` module re-exports the shared
    # `rv32core`, so that module has to be on the path beside it. arm64 brings
    # none and this adds nothing there.
    for extra in arch["hal_modules"]:
        cmd += ["--module-path", extra]
    _run(cmd)

    objs = list(shared_objs) + [obj]
    # design 158: a case may bring ONE C file of its own, for the bodies Saw
    # cannot write (a raw C function pointer, DF-113b). Per-case rather than in
    # the shared `support.c` every image links: a stand-in that satisfies one
    # test must not satisfy another kernel's accidental reference to a facility
    # that is not there.
    if case.get("csrc"):
        c_src = os.path.join(TESTS_DIR, case["csrc"])
        c_obj = os.path.join(dirs["build"], f"{name}.stubs.o")
        _run([clang, f"--target={arch['triple']}", *arch["cc_args"],
              "-nostdlib", "-ffreestanding", "-O2", "-c", c_src, "-o", c_obj])
        objs.append(c_obj)
    if case.get("asm"):
        payload_src = os.path.join(dirs["tests"], case["asm"])
        payload_o = os.path.join(dirs["build"], f"{name}.payload.o")
        _run([clang, f"--target={arch['triple']}", *arch["cc_args"],
              "-nostdlib", "-c", payload_src, "-o", payload_o])
        objs.append(payload_o)
    if case.get("root_pkg"):
        objs.append(_stitch_root_image(case["_root_image"][arch["name"]], arch,
                                       clang, name))
    # sawos design 2 + 6: the child images and the BOOT REGION TABLE that names
    # them, the RAM pool and the device window. A case that asks for none of the
    # three links no `.regions` section, so `_region_table_start` and
    # `_region_table_end` come out equal and the kernel sees ZERO REGIONS —
    # which is every case that predates unit 2 and is what keeps their
    # transcripts untouched by a table format that has now versioned twice.
    if case.get("children") or case.get("pool") or case.get("device"):
        objs.append(_stitch_regions(case, arch, clang))

    # The BOARD's linker script, for `boot.S`'s reason (sawos design 35): the
    # memory map, the payload section and the region table are the board's, and
    # a protection profile moves none of them.
    _run([lld, "-T", os.path.join(dirs["hal_board"], "virt.ld"), "--gc-sections",
          "-o", elf, *objs])
    return elf


def _run_qemu(qemu, arch, elf, feed=None):
    """Run the ELF under QEMU with a hard timeout.

    Returns (exit_status, stdout, timed_out). A timeout is a hang — the whole
    point of the kernel-bug path is that faults never reach it.

    SERIAL INPUT (design 178 M2 unit 4). `-nographic` wires the guest's console
    UART to this process's stdin and stdout both, so a case with a `stdin` key
    gets its bytes TYPED AT the guest — which is the only way to test a receive
    driver, since nothing inside the guest can make its own UART receive.

    The bytes go one at a time behind `SERIAL_TYPE_DELAY_S`, for the reason
    stated there: withholding them is what makes the driver park and the kernel
    idle, and a burst handed over at once would test neither.

    THE PIPE IS MADE HERE rather than by `Popen(stdin=PIPE)`, because
    `communicate()` closes the stdin it owns as soon as it is called — which
    would shut the port before the first delayed byte and leave the guest
    waiting forever for input that was never sent. That failure looks exactly
    like a kernel that cannot wake, which is worth one comment to never debug
    twice.

    A case with NO input GETS A PORT OF ITS OWN ANYWAY — an empty pipe this
    process holds the write end of, so the guest's console never sees a byte and
    never sees an EOF either. It used to inherit the harness's stdin, which was
    the same thing whenever that was a terminal nobody typed at, and is NOT the
    same thing once cases run concurrently: `-nographic` on a TTY puts the
    terminal in raw mode and restores what it found, so four QEMUs saving and
    restoring each other's termios leave the operator's shell in whichever state
    the last one to exit happened to have seen. An unwritten pipe is what an
    idle console always was, without the shared device underneath it.
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
            # The write end is held OPEN for the child's whole life above and
            # closed only here: closing it earlier would EOF the guest's console
            # rather than leave it quiet.
            os.close(read_fd)
            os.close(write_fd)

    read_fd, write_fd = os.pipe()
    proc = subprocess.Popen(cmd, stdin=read_fd, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    os.close(read_fd)

    def typist():
        # Owns the write end for its whole life, including the closing — a
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


def _check(case, arch, status, out, timed_out):
    """Return (ok, reason). Validates console output and exit expectations."""
    if timed_out:
        return False, f"QEMU hung (> {QEMU_TIMEOUT_S}s) — no clean exit"
    if isinstance(out, bytes):
        out = out.decode(errors="replace")
    expected = case["expect_out"]
    if expected is not None:
        # A single substring or a list of them — one boot can assert several
        # lines without rebuilding the kernel once per assertion.
        if isinstance(expected, str):
            expected = [expected]
        fmt = expectations(arch)
        # design 158: matched IN ORDER — each expectation starts where the
        # previous one ended. A console transcript is a sequence, and a case
        # like the task dump is asserting that the dump comes AFTER the panic
        # line, which an unordered `in` cannot see. Every list already reads in
        # output order, so this only adds what they were already claiming.
        cursor = 0
        for want in expected:
            want = want.format(**fmt)
            at = out.find(want, cursor)
            if at < 0:
                where = "out of order" if want in out else "missing"
                return False, (f"{where} expected output {want!r} "
                               f"(got {out!r})")
            cursor = at + len(want)
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

    THE ARCHITECTURES STAY SEQUENTIAL, and that is a decision rather than an
    oversight. Two of them at once would build the SAME Blade package for two
    triples concurrently, and a package's `.build/` tree is per-package with the
    target only one level down — the artifacts would not collide, but the stale
    `.sosimg` sweep at the top of `_build_root_image` and the lock/stamp files
    beside them are one directory for both. `jobs` buys its parallelism INSIDE
    an architecture, where every unit of work already has a name of its own, and
    an arch block's report stays the contiguous thing it has always been.
    """
    dirs = arch_dirs(arch)
    print(f"{BOLD}{arch['name']}{RESET}  ({arch['triple']}, {os.path.basename(qemu)} `virt`)")

    # design 158: a case may name the architectures it applies to. The default
    # is EVERY architecture and stays that way — an arch list is a claim that
    # the case is about something arch-specific, or that it is blocked on one,
    # and each one says which in a comment beside it. It is decided FIRST, so
    # every "this whole architecture failed" count below is the number of cases
    # that would have run here.
    cases = [c for c in selected_cases
             if arch["name"] in c.get("arches", (arch["name"],))]

    # **AND A CASE MAY NAME THE TIER IT NEEDS** (sawos design 35, M5 unit 4;
    # design 19's "one story, one test — sorted by tier"). The default is EVERY
    # tier and that is the important half: the object model — pipes, waiters,
    # events, timers, stats, quotas, handles, donation, the allocator — is not
    # tiered at all, so the overwhelming majority of this table carries no `tier`
    # key and runs identically on all three profiles. A `tier` key is a claim
    # that the case's ASSERTION IS DENIAL BY THE HARDWARE, which is the one
    # thing a flat platform cannot do.
    #
    # **NOTHING IS SKIPPED SILENTLY.** Every excluded case is named, with its
    # reason, in the report below — the no-silent-caps doctrine, and the reason
    # this is a two-list partition rather than one comprehension. A suite that
    # quietly ran 5 fewer cases on one profile would be a suite whose green line
    # means something different in each section.
    # Partitioned in ONE pass and by IDENTITY rather than by filtering twice: a
    # case is a plain dict, so `in` would compare CONTENTS, and two cases that
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

    # Root packages are built by Blade, per architecture, so build them first —
    # but only if some case that RUNS HERE needs one. The arch filter is applied
    # first because design 178 M2 unit 4 brought the first packages that are
    # per-DEVICE: a driver names its device, so each echo package has a
    # `[sos.<triple>]` section for one machine only and building it for the
    # other is a refusal rather than wasted work.
    #
    # CHILD packages (sawos design 2) ride the same list. They are not root
    # servers — nothing loads one at boot — but they are built by exactly the
    # same call, which is the claim: a child image goes through the pipeline
    # every SOS process goes through, and the only thing that differs is the
    # linker script its manifest names.
    root_pkgs = []
    for case in cases:
        if case.get("root_pkg") and case["root_pkg"] not in root_pkgs:
            root_pkgs.append(case["root_pkg"])
        for child in case.get("children", ()):
            if child not in root_pkgs:
                root_pkgs.append(child)
    if root_pkgs:
        # Each package is a separate directory and a separate Blade invocation,
        # so the only thing the pool has to preserve is the ORDER these lines
        # print in — which `_map_ordered` does. The failure arm keeps the serial
        # shape too: the first package to fail IN LIST ORDER is the one
        # reported, and the run gives up on this architecture there, exactly as
        # the serial loop's first raise did.
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
        # ON THIS THREAD, before any case starts: the case dictionaries are the
        # ONE table every worker reads, so the only writes into them happen
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
    # worker RETURNS its verdict and the lines it owns and prints nothing, so
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

    # **THE EXCLUDED CASES, BY NAME, WITH THE REASON** (sawos design 35).
    #
    # Printed here rather than folded into the numbered rows because these cases
    # did not run and did not pass, and a `[i/N]` line saying either would be a
    # lie. It sits immediately above the rows so the relationship between this
    # list and that `N` is on the screen at once.
    #
    # THE BLOCK IS SKIPPED ENTIRELY WHEN THE LIST IS EMPTY, which is what keeps
    # the riscv32 and arm64 sections byte-identical: on an isolated profile
    # every `tier` key matches and nothing is excluded, so not one character of
    # this appears. It is the flat profile's section that grows it.
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
    parser.add_argument("-j", "--jobs", metavar="N", type=int, default=4,
                        help="run N package builds, and then N cases, at a "
                             "time (default: 4 — this host's performance-core "
                             "count; `-j 1` is the serial harness)")
    parser.add_argument("--board", metavar="NAME", default="virt",
                        help="board profile (default: virt — the GATE's, and "
                             "the only implemented one). `esp32c3` is design "
                             "20's NON-GATING smoke target: Espressif QEMU, "
                             "machine-local, never CI")
    args = parser.parse_args()

    if args.board != "virt":
        # ------------------------------------------------------------------
        # BOARD SECTION: esp32c3 — design 20, NON-GATING.
        # Everything board-specific lands HERE and only here, so the virt
        # tables above stay another unit's to edit (the Sep-2 stub-pass
        # ruling: pre-carved regions instead of rebase conflicts).
        # ------------------------------------------------------------------
        if args.board != "esp32c3":
            print(f"{RED}unknown --board {args.board!r}; known: virt, "
                  f"esp32c3{RESET}", file=sys.stderr)
            sys.exit(2)

        # ------------------------------------------------------------------
        # THE ESP32-C3 SMOKE (design 20). NON-GATING BY RULING: never part of
        # `make sos-test`, never CI, and it runs against a MACHINE-LOCAL
        # Espressif QEMU that no other developer is assumed to have.
        #
        # It shares no table, no build directory and no run path with the gate
        # above. That is deliberate on three counts. The BOARD is different in
        # every way that matters (direct boot from a flash image rather than
        # `-kernel`, XIP text, no exit door). The BUILD DIRECTORY has to differ
        # even though the target triple does not — both profiles are
        # `riscv32-unknown-none-elf`, so a shared `.build/<triple>/` would let
        # `make sos-smoke-esp32c3` silently clobber the objects `make sos-test`
        # is about to link. And the VERDICT is read differently: this part has
        # no sifive_test finisher and Espressif QEMU gives a guest no shutdown
        # door, so the emulator never exits on its own and its exit status
        # carries nothing — the TRANSCRIPT is the whole of the result, and the
        # kernel's `exit_pass`/`exit_fail` say which it was in words.
        #
        # Serial, not `-j`: three cases, and a smoke whose job is to be
        # diffable has nothing to gain from overlapping them.
        #
        # Everything below was derived from the running emulator and is
        # recorded with its probe in hal/riscv32-esp32c3/ABI.md.
        #
        # The three imports are LOCAL on purpose: this section is the only
        # thing in the file that needs them, and the stub-pass ruling keeps
        # every board-specific edit inside these lines rather than spreading
        # one more diff hunk into a header another unit is also editing.
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
            "install it with the ESP-IDF tools installer; this target is "
            "machine-local and NON-GATING, so its absence breaks nothing else")
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
            """The child blobs and the BOOT REGION TABLE that names them.

            The virt path's `_stitch_regions` cannot be reused: it sizes a
            child's destination row from the module-level `CHILD_REGION_LEN`
            (256 KiB), and this board's whole SRAM is 400 KiB. Row ORDER is the
            same contract — every blob, then every destination — because root's
            config reads it as tags.
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
            # THE ONE INVARIANT THE HARNESS CHECKS ITSELF. Without these two
            # words the mask ROM does not enter the image at all and the run
            # fails as a silent timeout with no output — a failure mode that
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

            The machine never exits on its own — there is no finisher and no
            shutdown door — so the run ends at the kernel's own halt line or at
            the timeout, and QEMU is killed either way. Stderr is collected
            separately and kept OUT of the transcript: QEMU writes
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
                            # Drain what is still in flight, POLLING rather than
                            # reading: the halt line is usually the last thing
                            # the machine ever writes, so a blocking read here
                            # waits on a guest that has stopped talking — which
                            # is a hang, not a timeout, because the deadline
                            # above is no longer being consulted.
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

        # THE SMOKE LIST, in the order it is reported — design 20's three
        # reviewed cases and nothing else. `boot` needs no package at all: a
        # kernel with no root image appended must say so, which proves direct
        # boot, the XIP linker layout and the Espressif console sink in one.
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
        ]

        print(f"{BOLD}SOS ESP32-C3 board smoke{RESET} (design 20, NON-GATING)")
        print(f"  qemu   {qemu}")
        print(f"  clang  {clang}")
        print(f"  target {C3_TRIPLE} --target-features {C3_FEATURES} "
              f"({C3_MARCH[len('-march='):]} — no A extension)")

        blade_bin = _build_blade(C3_BUILD)

        # The kernel is ONE compile for all three cases: they differ in what is
        # appended to it, never in the kernel itself.
        # `boot.S` is this board's; `trap.S` and `sink.c` are the SHARED riscv32
        # ones, out of `hal/riscv32-common/kernel/` (design 23).
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
                            "--freestanding", "--no-hidden-alloc",
                            "--runtime-provider",
                            "--target", C3_TRIPLE,
                            "--target-features", C3_FEATURES,
                            "--module-path", CORE_MODULE,
                            "--module-path",
                            f"hal={os.path.join(C3_HAL, 'kernel')}",
                            # design 23: this board's `hal` re-exports the
                            # shared riscv32 core, so `rv32core` rides beside it
                            # exactly as it does on the virt path.
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

    if args.jobs < 1:
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

    # The case filter is applied to the ONE table everything downstream reads,
    # so a filtered run builds only what the named cases need (the blade build
    # below and the per-arch package list both see the narrowed list). Names
    # are matched with '-' and '_' interchangeable because the case names use
    # underscores while the tests/ directories use hyphens, and remembering
    # which is nobody's job.
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

    print(f"{BOLD}SOS QEMU tests{RESET}")
    print(f"  clang: {clang}")
    print(f"  lld  : {lld}")
    # One line per DISTINCT emulator, in first-use order. This header names the
    # TOOLS a run used, and two profiles of one architecture use one binary —
    # riscv32 and riscv32-flat are the same `qemu-system-riscv32` (sawos design
    # 35). Printing it twice would say there were two. Deduplicating also leaves
    # this header byte-identical to the two-architecture one, which is a fence
    # design 35 asked for rather than a coincidence worth relying on.
    seen_qemus = []
    for arch in arches:
        binary = qemus[arch["name"]]
        if binary not in seen_qemus:
            seen_qemus.append(binary)
            print(f"  qemu : {binary}")
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

    # Resolve the toolchain HERE, on this thread, whether or not the Blade build
    # above already did it. `tc()` is a lazy global with a one-time print, and a
    # lazy global first touched by four workers at once is two resolutions and a
    # note printed into the middle of the report. After this line every worker
    # reads a value that is already there.
    tc()

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
