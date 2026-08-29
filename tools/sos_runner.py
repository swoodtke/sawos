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
"""

import argparse
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

# The BOOT REGION TABLE's wire format (sawos design 2 D-2), frozen: an 8-byte
# header — magic, version u16, count u8, reserved u8 — then `count` rows of
# {base: u64, len: u64}, all little-endian. The header's size is what puts every
# row on an 8-byte boundary, which is what lets the kernel overlay a struct on
# the section instead of assembling bytes. Kept in step with
# `kernel/core/process.saw`'s `RegionTableHeader` / `RegionRow` and the two
# `static_assert`s beside them.
REGION_TABLE_MAGIC = 0x4E475253          # 'S','R','G','N' read little-endian
REGION_TABLE_VERSION = 1


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
    {
        "name": "uart_echo_ns16550",
        "arches": ["riscv32"],
        "src": os.path.join(KERNEL_DIR, "main.saw"),
        "root_pkg": UART_ECHO_NS16550_PKG,
        "stdin": ECHO_INPUT,
        "expect_out": ["{banner}",
                       # THREE segments: code, data, and the DEVICE WINDOW the
                       # manifest declared — which is the grant being carried in
                       # the image rather than in kernel logic.
                       "root image ok segments={three}",
                       "SOS: console handover",
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
        "stdin": ECHO_INPUT,
        "expect_out": ["{banner}",
                       "root image ok segments={three}",
                       "SOS: console handover",
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
                       # legitimately reports 2 or 3 here. Pinning the number
                       # would make coalescing itself the flake. (Observed: both
                       # machines reported `fires=2` on the first wake.) What is
                       # asserted is that each wake HAPPENED, with no ack and no
                       # re-arm between them, which `ackfree` carries — and the
                       # two counts that ARE deterministic are pinned below.
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


def _stitch_root_image(image, arch, clang):
    """Assemble the `.incbin` stub that pulls `image` into `.payload`.

    The stub names `root.sosimg` and is assembled with `-I` pointing at this
    architecture's build directory, so one committed stub stitches whichever
    root image the case asked for.
    """
    dirs = arch_dirs(arch)
    os.makedirs(dirs["build"], exist_ok=True)
    staged = os.path.join(dirs["build"], "root.sosimg")
    shutil.copyfile(image, staged)
    stub_o = os.path.join(dirs["build"], "rootimg.o")
    _run([clang, f"--target={arch['triple']}", *arch["cc_args"],
          "-nostdlib", "-I", dirs["build"], "-c",
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


def _stitch_children(case, arch, clang):
    """Append this case's child images and emit the BOOT REGION TABLE.

    THE RULED HYBRID (sawlang#232 agenda item 2; sawos design 2 D-2): the
    stitcher appends each child sosimg AS IT IS — no flattening, no absolute
    placement, the blob lands wherever the linker puts it — and records offsets
    and lengths in a table. The kernel mints one Memory per row and interprets
    nothing; ROOT's config is what says which ordinal is which.

    THE STUB IS GENERATED RATHER THAN COMMITTED, and that is what buys the
    linker-resolved bases: a blob row names the child section's own symbols, so
    `ld.lld` fills in the address when it places the section and nothing here
    computes one. A committed stub would have had to be per-case anyway (the
    number of children is a property of the case), so generating it costs a file
    nobody has to keep in step and removes the one thing that could have gone
    wrong.

    ROW ORDER IS THE CONTRACT: every blob row first, in the order the case lists
    its children, then every destination row in the same order. That is what
    root's config reads — tag 0 is child 0's image, tag N is child 0's RAM — and
    it is stated here because it is stated nowhere else.
    """
    dirs = arch_dirs(arch)
    os.makedirs(dirs["build"], exist_ok=True)
    name = case["name"]
    children = case["children"]

    lines = [
        "/* GENERATED by tools/sos_runner.py — sawos design 2 D-2. */",
        "",
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

    lines += [
        "",
        "    .section .regions, \"a\", @progbits",
        "    .balign 8",
        f"    .4byte {REGION_TABLE_MAGIC:#010x}",
        f"    .2byte {REGION_TABLE_VERSION}",
        f"    .byte  {2 * len(children)}",
        "    .byte  0",
    ]
    for i, _pkg in enumerate(children):
        lines.append(f"    /* row {i}: child {i}'s image blob (linker-resolved) */")
        _emit_word64(lines, arch, f"_sos_child{i}_start")
        _emit_word64(lines, arch, f"_sos_child{i}_end - _sos_child{i}_start")
    for i, _pkg in enumerate(children):
        base = arch["child_region_base"] + i * CHILD_REGION_LEN
        lines.append(f"    /* row {len(children) + i}: child {i}'s destination RAM */")
        _emit_word64(lines, arch, f"{base:#x}")
        _emit_word64(lines, arch, f"{CHILD_REGION_LEN:#x}")
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
        objs.append(_stitch_root_image(case["_root_image"][arch["name"]], arch, clang))
    # sawos design 2: the child images and the region table that names them.
    # A case with no children links neither section, so `_region_table_start`
    # and `_region_table_end` come out equal and the kernel sees ZERO REGIONS —
    # which is every case that existed before this unit.
    if case.get("children"):
        objs.append(_stitch_children(case, arch, clang))

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

    A case with NO input keeps stdin inherited, exactly as before — the pipe is
    opened only where it is used, so nothing about the other cases changes.
    """
    cmd = [qemu, *arch["qemu_args"], "-nographic", "-kernel", elf]
    if feed is None:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=QEMU_TIMEOUT_S)
            return proc.returncode, proc.stdout, False
        except subprocess.TimeoutExpired as e:
            return None, (e.stdout or (e.stdout and e.stdout.decode()) or ""), True

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


def _run_arch(arch, qemu, lld, clang, blade_bin):
    """Build and run every case for one architecture. Returns (passed, failed)."""
    dirs = arch_dirs(arch)
    print(f"{BOLD}{arch['name']}{RESET}  ({arch['triple']}, {os.path.basename(qemu)} `virt`)")

    # design 158: a case may name the architectures it applies to. The default
    # is EVERY architecture and stays that way — an arch list is a claim that
    # the case is about something arch-specific, or that it is blocked on one,
    # and each one says which in a comment beside it. It is decided FIRST, so
    # every "this whole architecture failed" count below is the number of cases
    # that would have run here.
    cases = [c for c in TEST_CASES
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
        try:
            for pkg in root_pkgs:
                image = _build_root_image(blade_bin, pkg, arch, clang)
                size = os.path.getsize(image)
                print(f"  {os.path.relpath(image, REPO_ROOT)}  ({size} bytes)")
            for case in cases:
                if case.get("root_pkg"):
                    holder = case.setdefault("_root_image", {})
                    holder[arch["name"]] = _root_image_path(case["root_pkg"], arch)
                if case.get("children"):
                    holder = case.setdefault("_child_images", {})
                    holder[arch["name"]] = [_root_image_path(c, arch)
                                            for c in case["children"]]
        except ToolError as e:
            print(f"{CROSS} failed to build a {arch['name']} root image\n{e}",
                  file=sys.stderr)
            return 0, len(cases)
    passed = 0
    failed = 0
    for i, case in enumerate(cases, 1):
        name = case["name"]
        try:
            elf = _build_elf(case, arch, shared_objs, lld, clang)
        except ToolError as e:
            print(f"[{i}/{len(cases)}] {CROSS} {name}  (build error)")
            for line in str(e).splitlines():
                print(f"    {line}")
            failed += 1
            continue
        status, out, timed_out = _run_qemu(qemu, arch, elf, case.get("stdin"))
        ok, reason = _check(case, arch, status, out, timed_out)
        if ok:
            print(f"[{i}/{len(cases)}] {CHECK} {name}")
            passed += 1
        else:
            print(f"[{i}/{len(cases)}] {CROSS} {name}  ({reason})")
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
    args = parser.parse_args()

    arches = ARCHES
    if args.arch:
        arches = [a for a in ARCHES if a["name"] == args.arch]
        if not arches:
            names = ", ".join(a["name"] for a in ARCHES)
            print(f"{RED}unknown --arch {args.arch!r}; known: {names}{RESET}",
                  file=sys.stderr)
            sys.exit(2)

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
    if any(case.get("root_pkg") for case in TEST_CASES):
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
        passed, failed = _run_arch(arch, qemus[arch["name"]], lld, clang, blade_bin)
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
