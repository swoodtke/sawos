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

CLOCK_BASICS_PKG = os.path.join(TESTS_DIR, "clock-basics")
TIMER_ONESHOT_PKG = os.path.join(TESTS_DIR, "timer-oneshot")
TIMER_INTERVAL_PKG = os.path.join(TESTS_DIR, "timer-interval")
TIMER_DEADLOCK_PKG = os.path.join(TESTS_DIR, "timer-deadlock")
TIMER_BADCLOCK_PKG = os.path.join(TESTS_DIR, "timer-badclock")
TIMER_BADRECORD_PKG = os.path.join(TESTS_DIR, "timer-badrecord")

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

ARCHES = [
    {
        "name": "riscv32",
        "triple": "riscv32-unknown-none-elf",
        "qemu": "qemu-system-riscv32",
        # `-bios none`: no OpenSBI, the kernel IS the reset target.
        "qemu_args": ["-M", "virt", "-bios", "none"],
        # A triple names the architecture but not which optional extensions the
        # part has: without `+m` the Saw half is built for base rv32i and
        # formatting an integer emits `__divsi3` calls this link cannot satisfy.
        # The C half gets the same set through `-march`.
        "cc_args": ["-march=rv32imac_zicsr", "-mabi=ilp32"],
        "features": "+m,+a,+c",
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
        "hex_width": 16,
        "root_entry": 0x40200000,
        # One region above root's top, as on Profile A — and here the choice is
        # CONSTRAINED as well as tidy: EL0 can only be granted pages inside the
        # HAL's grant window, the first 4 MiB of RAM, so a child's destination
        # has to sit between root's top (0x4024_0000) and 0x4040_0000. See
        # `hal/arm64/user/child.ld`.
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
    """The per-architecture directories a build reaches into."""
    return {
        "hal_kernel": os.path.join(HAL_DIR, arch["name"], "kernel"),
        "tests": os.path.join(TESTS_DIR, arch["name"]),
        "build": os.path.join(REPO_ROOT, ".build", arch["triple"], "sos"),
    }


def expectations(arch):
    """The per-architecture substitutions a case's expected output is written in.

    A case says `entry={entry}`, not `entry=0x80200000`, because the FACT under
    test is "the entry the image declared came through intact" and the digits
    are the target's word width.
    """
    width = arch["hex_width"]
    return {
        "banner": f"SOS M1: kernel up on {arch['name']}",
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
        "arches": ["riscv32"],
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
        # It reuses the child-fault package, and both creates load the same
        # image into the same RAM — the first child's remains are nothing the
        # kernel tracks.
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
        # DESIGN 6's RECORDED DEVIATION, SHOWN RESOLVED, and §2.5's leak shown
        # to be exactly what it always said it was.
        #
        # `rounds=12` is past `MAX_MAPPINGS` (8), so the slab is demonstrably
        # recycling — and past root's own ROW allowance too (its free rows,
        # five on the smaller profile), so `Unmap` is demonstrably crediting the
        # ledger as well as returning the row. One number carries both halves.
        #
        # `husk wrote 0x5e read 94` is the other side: a Mapping dropped WITHOUT
        # an unmap frees its slot and LEAVES its row, so the memory is still
        # reachable through a grant no object names any more. That is §2.5's
        # "permanent, safe-but-leaked", executed.
        "name": "mapping_slot_free",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": MAPPING_SLOT_FREE_PKG,
        "pool": True,
        "expect_out": ["{banner}",
                       "SOS: boot regions={one}",
                       "SOS mapfree: rounds=12 refused=0",
                       "SOS mapfree: husk wrote 94 read 94",
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
        # time. With the Process HANDLE released and only the attachment left, a
        # second `process_create` is still refused (`MAX_PROCESSES` is two and
        # slot 1 is still `Gone`); remove the attachment and the count reaches
        # zero, the slot frees inside that very syscall, and the same create
        # succeeds. A kernel that did not count the attachment would print
        # `held=0` — and the second wait above would have been reading a slot
        # the kernel had already given away.
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
        "arches": ["riscv32"],
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
        # `pending is none` is the typed tier's two channels at the reply end: a
        # resolve before an answer exists is `Ok(None)` and consumes NOTHING, so
        # the same claim polls again. A shape that reported it as an error would
        # make the poll loop unwritable, and a shape that consumed the claim would
        # make it impossible.
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
                       "SOS oneshot: pending is none",
                       "SOS oneshot: reply len=2 b=90,89",
                       "SOS oneshot: zero reply len=0",
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
        # **AN UNSPENT RIGHT ON AN OBLIGATION** (spec §3). `pipe_no_post`'s claim
        # made once more at the other end of the exchange: a one-shot's default
        # set is permissive because attenuation is monotonic, so the narrowing is
        # the holder's and it is written once at a `MINT_OP` keep mask.
        #
        # THE MASK IS THE DELEGATION POLICY. `Transfer | Mint` and nothing about
        # `Reply` describes a COURIER — a delegate that may pass the obligation
        # further along and may never speak for it — which is a thing §2.1's
        # forwarding example genuinely wants to be able to say.
        "name": "pipe_no_reply",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_NO_REPLY_PKG,
        "expect_out": ["{banner}",
                       "SOS noreply: minted without Reply",
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
        # **CONSUMED IS CONSUMED** (spec §2.1's single-use; sawos design 14). A
        # resolve that delivers destroys the caller's entry through the op —
        # unbind, generation bump, unref, `RELEASE_OP`'s own two calls — so the
        # word is stale the instant it returns and asking again is the ordinary
        # `BadHandle` fault rather than a second answer.
        #
        # **THE TYPED LAYER MAKES THIS HARD TO REACH, WHICH IS WHY THE CASE
        # EXISTS.** A `PipeReply` disarms itself when the kernel consumes it, so
        # a spent claim drops to nothing and an ordinary program never meets this;
        # the case asks a SECOND time through the same value to show the kernel's
        # check is real underneath the wrapper's. Single use is a property of the
        # LEDGER, and `NoCopy` is the ergonomics on top of it.
        "name": "pipe_dead_claim",
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": PIPE_DEAD_CLAIM_PKG,
        "expect_out": ["{banner}",
                       "SOS deadclaim: resolved len=1",
                       "SOS: process fault: bad handle process={zero}",
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
        "expect_out": ["{banner}",
                       "SOS: boot regions={two}",
                       "SOS sendmanual: gave the outlet",
                       "SOS childserver: replied",
                       "SOS: process exit: code={four} process={one}",
                       "SOS: process teardown handles={four} threads={one} "
                       "events={zero} waiters={one} interrupts={zero} "
                       "timers={zero} process={one}",
                       "SOS sendmanual: reply len=4 b=80,79,78,71",
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
            probe_src = os.path.join(dirs["hal_kernel"], "boot.S")
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
          "-nostdlib", "-c", os.path.join(dirs["hal_kernel"], "boot.S"),
          "-o", boot_o])
    for src, obj in ((os.path.join(dirs["hal_kernel"], "sink.c"), sink_o),
                     (os.path.join(RT_COMMON_C_DIR, "support.c"), support_o)):
        # -fno-builtin: support.c DEFINES memcpy, and without it LLVM may
        # rewrite its byte loop into a call to itself.
        _run([clang, f"--target={arch['triple']}", *arch["cc_args"],
              "-ffreestanding", "-fno-builtin", "-ffunction-sections",
              "-fdata-sections", "-nostdlib", "-O2", "-c", src, "-o", obj])
    return [boot_o, sink_o, support_o]


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

    _run([lld, "-T", os.path.join(dirs["hal_kernel"], "virt.ld"), "--gc-sections",
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
    args = parser.parse_args()

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
    for arch in arches:
        print(f"  qemu : {qemus[arch['name']]}")
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
