# SawOS design 39 — the C-leg probe: one freestanding C process, end to end

**Status: BUILT, Sep 5 2026 (As-built at the end) — user-ruled
experiment ("let's try the simple freestanding c process — see what
works and what's missing"). Dispatched as its own unit after the arena
unit and the 0.8.0 pin bump, before M5's unit 8.** Gate **380/380 = 128 + 128 + 124** across
riscv32 / arm64 / riscv32-flat (was 377); with the `[i/N]` total and the
image-size column normalised out the transcript diff is SEVEN hunks,
every one an addition and every one the new case's own row — not one
pre-existing case row moved on any profile. **NO kernel, sysapi, ABI or toolchain change was needed and NO new
runner build leg** — none of the brief's three STOP conditions fired. A
freestanding C image reaches Blade's sosimg emit through the same
`[sos] native` line the HAL's own C stubs use, and the crt0 turned out to
be a plain C function on both profiles, because the kernel's entry
contract IS the C calling convention. **The one thing the lead must see
is a LATENT ARM64 HAZARD the probe did not trip and M7 will: EL0 FP/SIMD
is enabled (`CPACR_EL1.FPEN = 3`) and the trap frame saves no FP state**
— As-built §7 finding K1.

## What this is, and what it is not

An EXPERIMENT whose deliverable is FINDINGS. M7 (`designs/031`) rests
on two feasibility hinges. The spawn hinge is proven — ProcessCreate,
boot-set tags, Waiter-join and the §8 status word all exist and gate.
The OTHER hinge does not have a single demonstrated instance: **no C
`main` has ever been compiled with clang freestanding, linked under
the sosimg discipline with a crt0, loaded, run, and exited clean
through the kernel.** This unit builds exactly one, and writes down
everything it hits.

It is NOT the start of sos-libc, NOT the C export surface (design 31
part 1 — the probe may hand-roll what it needs and RECORD whether the
export surface would have been the better substrate), and NOT an M5
or M6 unit. sawos-native numbering; the As-built feeds M7's scoping.

## The probe

One C program, one new gate case:

1. **A C child**: `tests/c-hello/` (or the shape the tree suggests) —
   a `main.c` compiled by the SAME clang the tree already uses for
   `sink.c`/`support.c` (`/opt/homebrew/opt/llvm/bin/clang`,
   freestanding, both targets — the kernel-side C leg is already
   proven; this extends it to a whole userspace image).
2. **A crt0**: entry per the arch's user ABI.md (the svc/ecall stub
   convention), sets up the stack the loader hands it, calls `main`,
   exits through the syscall on return. Hand-written per arch is
   fine; shared if it falls out naturally.
3. **Two or three hand-rolled syscall wrappers in C**: enough to
   (a) print a line (whatever the uart/debug path the existing child
   payloads use), (b) exit with a code. Inline asm per
   `hal/*/user/ABI.md`. NO general binding layer — that is design 31
   part 1's job, and finding out how much this probe *wants* it is a
   finding, not a scope.
4. **A gate case**: root spawns the C child (image as Memory handle,
   the existing child-package machinery), asserts its printed line
   and its exit code through the §8 status word. APPENDED at the end
   of the case list (ordinal stability). Runs on riscv32 + arm64;
   flat too if it costs nothing. If making it gate-stable is
   disproportionate, STOP and report rather than shipping a flaky
   case — a non-gating make target with the reason recorded is an
   acceptable landing, but the gate is the preference and the
   tradition.

## Hard constraints

- **NO kernel changes, NO sysapi changes, NO ABI changes.** If the
  probe cannot land without one, STOP AND REPORT — that is the most
  valuable finding this unit could produce, not a failure.
- Blade/toolchain: use what exists (the runner already drives clang
  for C objects). A new Makefile leg inside the runner's build flow
  is expected; a sawc/blade feature request is a STOP-and-report.
- `-Oz` note: by the time this dispatches, freestanding Saw builds
  use -Oz (seventh pin bump, user-ruled). The C side may use -Oz
  too; record the flags either way.

## Findings the As-built must answer

1. The exact clang invocation per arch (flags, target, what broke).
2. The crt0 shape: what the loader/ABI actually hands a process at
   entry vs what the docs say (any doc skew is a finding for unit 8).
3. Stack and memory: what the C child needed, where it came from,
   and whether the arena/linker conventions fit a non-Saw image.
4. The syscall seam from C: how painful hand-rolled wrappers were,
   and whether design 31 part 1 (the `sos_*` C export surface over
   sysapi) is the right next step or over-engineering.
5. Image plumbing: anything the sosimg emit / child-package flow
   assumed about Saw images that a C image tripped.
6. A sized list of "what's missing" for M7's libc shim, each item
   marked kernel / userspace-convention / toolchain.

## Adjacent

`designs/031` (M7 seed — this probe de-risks its part 4 and informs
part 1). `designs/030` (M6 — untouched by this; the probe needs no
fs, no namespace, no loader). Thread surface note (user question,
Sep 4 night): pthread-core create/join/exit/yield exists 1:1 on
`Process.thread_create` + `Thread` ops; gaps are detach, TLS
(thread-pointer convention), and futex-shaped wait — record in the
M7 pile, nothing for this probe.

---

# As-built (agent, Sep 5 2026)

**The experiment ran and the answer is that almost nothing is missing.**
One `main.c`, one `crt0.c`, one `syscall.c`, an empty `src/main.saw`
Blade insists on, and two two-line linker scripts: that is a whole SOS
process written in C, loaded, scheduled, printing and exiting through
the §8 status word on riscv32, arm64 and riscv32-flat. It took no
kernel change, no sysapi change, no ABI change, no toolchain change and
no new build leg in the runner.

## 1. What landed

| Path | What it is |
|---|---|
| `tests/c-child/main.c` | the C `main` — prints a greeting and a runtime-computed byte count, returns 55 |
| `tests/c-child/crt0.c` | the entry: calls `main`, turns its return into `Process.exit` |
| `tests/c-child/syscall.c` | `sos_syscall1`/`sos_syscall3` in inline asm, both arches behind one `#if`, plus three named ops |
| `tests/c-child/sos.h` | the word typedef, the op numbers, the prototypes |
| `tests/c-child/src/main.saw` | EMPTY — Blade demands a Saw source file; see finding 5 |
| `tests/c-child/c-child.{riscv32,arm64}.ld` | design 38's override, used to set `ARENA_SIZE = 0` |
| `tests/c-child/Saw.toml` | no `[dependencies]`, no `runtime`, `native = "crt0.c syscall.c main.c"` |
| `tests/c-hello/` | the launcher — `tests/death-notify/`'s program with the strings changed |
| `tools/sos_runner.py` | two package constants and the `c_hello` case, APPENDED |

Nothing else in the tree was touched. In particular the ABI documents
were READ and deliberately not edited (finding 2 hands four items to
unit 8), and `hal/`, `kernel/` and `rt/` are untouched.

## 2. Finding 1 — the clang invocation, per arch, and what broke

**The probe did not choose the invocation; Blade did, and it is the
same one `sink.c` and `support.c` go through.** Blade's
`build_sos_image` (sawlang `blade/src/builder.saw`) compiles each unit
of a package's `[sos.<triple>] native` list through the program named
by `SOS_CLANG` — which `tools/sos_runner.py`'s `_blade_env` sets to the
clang the harness probed. So the answer to "which clang" is: the one
the banner prints.

    clang: /opt/homebrew/opt/llvm/bin/clang          (Homebrew clang 23.1.0)

riscv32 (`march`/`mabi` default FROM THE TRIPLE, so the manifest names
neither):

    clang --target=riscv32-unknown-none-elf -march=rv32imac_zicsr -mabi=ilp32 \
          -ffreestanding -fno-builtin -ffunction-sections -fdata-sections \
          -nostdlib -O2 -c <unit> -o <name>.native<n>.o

arm64 (an EMPTY flag is not passed — `-march=` with nothing after it is
a clang error rather than a no-op, which is why the list is shorter
rather than blank):

    clang --target=aarch64-unknown-none-elf \
          -ffreestanding -fno-builtin -ffunction-sections -fdata-sections \
          -nostdlib -O2 -c <unit> -o <name>.native<n>.o

and the link, both arches:

    ld.lld -T <the manifest's linker-script> --gc-sections -o <name>.elf \
           <name>.o <name>.native0.o <name>.native1.o <name>.native2.o

**`-O2`, NOT `-Oz`, AND THAT IS NOT A CHOICE ANYONE HERE MADE.** The
seventh pin bump's `-Oz` ruling rides the `SAWC` environment value
(`_blade_env`: `tc().sawc_env_value() + " -Oz"`), which reaches the SAW
half of every image and cannot reach the C half; Blade's native compile
writes `-O2` as a literal. There is no manifest key for per-target C
flags, so a package cannot ask for another level. This is DF-172c,
already filed sawlang-side for a different reason
(`-mgeneral-regs-only`) — see finding K1, which is why it matters more
than an optimization level does.

**WHAT BROKE: one thing, and it was C's fault rather than SOS's.** A
block comment containing the glob `hal/*/user/ABI.md` ENDS AT THE `*/`,
so three of the four C files failed to parse, with twenty errors
pointing at prose. Two minutes; the comments now write
`hal/<arch>/user/`. That is the complete list. Both targets compiled and
linked on the next attempt, and the resulting images booted and passed
on the FIRST run, on all three profiles.

**WHAT DID NOT BREAK, each of which was a candidate:** an em dash and a
`§` in a C comment (the tree's own C files already carry both); the
`register ... __asm__("a0")` local-register-variable idiom (both arches,
`-O2`); `--gc-sections` collecting `_start` (`ENTRY(_start)` is a GC
root); `sawc --freestanding` on a module with no declarations;
`ARENA_SIZE = 0` in a linker script; the emitter refusing an image with
one segment; and the loader refusing an image with no `.data`.

## 3. Finding 2 — the crt0, and the doc skew it found

**THERE IS NO ASSEMBLY IN THE CRT0. IT IS A PLAIN C FUNCTION, AND IT IS
THE SAME FILE ON BOTH PROFILES.** That is the probe's happiest result
and it deserves to be stated as a property rather than as luck: what
`process_start` hands a new process IS the C calling convention.

| What a crt0 usually does | What this one does | Why |
|---|---|---|
| set up a stack | nothing | `PROCESSES[slot].stack_top = dest.link_top()` at create, into `sp` at `frame_init`. Both profiles' region tops are 16-byte aligned, which is what AArch64's sp rule wants. |
| find its argument | takes a parameter | the boot handle is placed in the FIRST ARGUMENT REGISTER (a0 / x0), so `void _start(unsigned long h)` receives it |
| zero `.bss` | nothing | the loader zero-fills each segment's `mem_len` tail past its `file_len` |
| copy `.data` from a load address | nothing | segments are copied to their LINK addresses; there is no separate load address to relocate from |
| `exit` on return from `main` | `process_self` then `exit` | the only door out; a child derives its own Process object exactly as root does (§12's symmetry) |
| never return | RETURNS on the error path | deliberate: the link register is zero (§8), so falling off `_start` FAULTS and the launcher reads `Faulted` where it expected `Exited`. A `_Noreturn` crt0 that spun would turn a bootstrap failure into a case timeout. |

**THE DOC SKEW, and it is a gap rather than a lie.** Every row of that
table is TRUE and only the first is written down in
`hal/<arch>/user/ABI.md`, whose "Required of a process" section is one
bullet — the entry register. The other four came out of
`kernel/core/dispatch.saw`, `kernel/core/loader.saw` and `spec.md §8`. A
crt0 is exactly the artifact that section exists to make writable, so
the four rows belong in it. Filed for unit 8, not edited here (the
brief's constraint).

Three more, same treatment — `hal/riscv32/user/ABI.md` says "three
scripts" in THREE places and lists `root/child/child2` where design 36
added `child3.ld`, and `hal/arm64/user/ABI.md`'s tier-split paragraph
repeats the count. All four sites are in `designs/todo.md`'s unit-8
pile.

## 4. Finding 3 — stack and memory, and the arena knob at zero

**A C IMAGE LINKS NO `sosrt`, SO THE ARENA MACHINERY SIMPLY DOES NOT
APPLY TO IT — AND IT IS A KNOB, SO SAYING SO COSTS TWO LINES.** That is
the whole answer, and design 38 had already built the mechanism.

`tests/c-child/c-child.riscv32.ld`, in its entirety past the header:

    ARENA_SIZE = 0;

    INCLUDE ../../hal/riscv32/user/child.ld

Measured, riscv32, with and without:

| | segments | `.text` | `.data` | `.bss` | sosimg |
|---|---|---|---|---|---|
| `ARENA_SIZE = 0` (as landed) | 1, `[RX]` | 532 B | 0 | 0 | **580 B** |
| `ARENA_SIZE = 64K` (the default) | 2 | 532 B | 0 | 65,536 B | 604 B |
| `tests/echo-child` (Saw, same job) | 2 | 7,524 B | 1,140 B | 65,548 B | 8,736 B |

The 24-byte file difference is the second segment RECORD; the 64 KiB is
`mem_len` the loader would zero-fill into the child's region for a
program that can never name it. With the knob at zero the C image is ONE
`[RX]` segment, `file_len == mem_len`, and its whole footprint in a
256 KiB child region is 532 bytes of image plus the 16 KiB stack the
kernel grants — against roughly 74 KiB for the Saw child beside it.

**arm64 pays a page and it is protection granularity, not waste.**
`user.ld` page-aligns `.text` at both ends because two segments sharing
a page would be granted the union of their permissions (design 33), so
the smallest possible arm64 image is 4,096 bytes of segment — 4,144 in
the file. The C image's real content there is the same ~530 bytes; the
rest is padding the loader copies. Profile A grants at four-byte
granularity and pays none of it.

What the 532 bytes ARE, riscv32, from `llvm-nm --print-size`:

    _start                    42        main                     276
    sos_system_debug_print     8        GREETING (.rodata)        39
    sos_system_process_self   16        __saw_bt_table           138
    sos_process_exit           8

That last row is finding 5's, not this one's.

The STACK needed nothing: `put_uint`'s ten-byte digit buffer and the
frames under it live in the 16 KiB the kernel grants above the linked
region, and the second console line proves the frame is real memory
rather than an accident.

## 5. Finding 4 — the syscall seam, and whether design 31 part 1 is right

**THE HAND-ROLLED WRAPPERS WERE PAINLESS AND UNNECESSARY. THE OP NUMBERS
WERE NEITHER.** Those are two different questions and the brief was
right to ask them together, because the answer to the first is what
makes the second sharp.

*The instruction.* `syscall.c` is 30 lines of body across both arches,
transcribed from `hal/<arch>/user/ABI.md`'s register tables, and it
worked first time on both. It is also a DUPLICATE:
`hal/arm64/user/syscall.c` and `hal/riscv32-common/user/syscall.c` are
already exactly this, are already C, and are already nameable from any
manifest's `native` line — an M7 C program would simply name them and
write none of it. The probe wrote its own on purpose, to find out what a
program arriving from OUTSIDE the tree pays, and the answer is: an
afternoon's reading of two documents that are accurate. **Nothing about
the trap seam wants a binding layer.**

*The numbers.* This is where the C leg actually hurts, and the shape of
the hurt is not effort — it is spec §5.7's vDSO discipline failing to
hold. The discipline says an op number is not ABI: userspace names the
op, the `sos` module supplies the number, a renumbering is a rebuild. A
C image cannot hold up its end. `tests/c-child/sos.h` writes three
numbers out of `kernel/abi/`, a module whose own header calls itself
KERNEL-INTERNAL, on `tests/riscv32/payload_sosimg.S`'s precedent:

    #define SOS_OP_SYSTEM_DEBUG_PRINT  0u
    #define SOS_OP_SYSTEM_PROCESS_SELF 2u
    #define SOS_OP_PROCESS_EXIT        2u

**AND THE SURFACE THAT WOULD FIX IT ALREADY EXISTS AND IS OUT OF REACH.**
`kernel/sysapi/src/floor.saw` carries the whole `@export`ed per-op C
block — `sos_system_debug_print`, `sos_system_process_self`,
`sos_process_exit`, `sos_process_create`, `sos_process_give`, the lot —
under a header that says in as many words "PER-OP functions are the
supported C interface ... a C caller gets the same contract a Saw caller
does minus the type wrapper, and still never writes an op number". Both
`user/ABI.md`s advertise the row as "typed C, for non-Saw languages". A
C image cannot use it, because those symbols are COMPILED FROM A SAW
MODULE: reaching them means depending on `sos`, `sos` depends on
`sosrt`, and the image is then a Saw image with a C `main` in it — 8 KiB
of runtime and a 64 KiB arena for three calls. DF-172i records that the
typed C row has had no in-tree caller since design 172 part 2. This
probe is the first non-Saw process in the tree's history and it could
not become one.

**VERDICT ON DESIGN 31 PART 1: RIGHT, BUT ITS DELIVERABLE IS A HEADER
AND A LINKABLE OBJECT, NOT MORE `@export`s.** The exports are already
written. What does not exist is (a) an `sos.h` the tree owns and keeps
in step with `kernel/abi/src/ops.saw`, and (b) a way to link those
exports without the Saw runtime behind them — either a `sos` build
profile that carries the floor and nothing else, or the header plus this
probe's `syscall.c` with the numbers GENERATED from `ops.saw` rather
than typed. (b) is the real work; (a) is a day. It is not
over-engineering: without it every C program in M7 hardcodes the same
three-to-thirty numbers and the vDSO discipline becomes a claim about
one language.

## 6. Finding 5 — what the image plumbing assumed about Saw

**ONE THING, AND IT IS BLADE'S: A PACKAGE MUST HAVE A SAW SOURCE FILE.**
`build_sos_image` opens with `find_source_file()`, which looks for
`src/main.saw`, then `main.saw`, then `src/lib.saw`, and refuses with
"No main.saw or lib.saw found". A package of pure C is not expressible.
A package of pure C plus one empty file is, so
`tests/c-child/src/main.saw` is an empty file that explains itself.

**IT IS NOT FREE, AND MEASURING THAT IS THE USEFUL HALF.** The object
`sawc --freestanding` produces from an empty module has an empty `.text`
and defines nothing anything references — but it carries design 158's
static backtrace table in `.rodata.__saw_bt_table`, and that section is
marked RETAIN, so `--gc-sections` keeps it. 138 bytes, a quarter of this
image's 532, constant per image rather than per declaration. Nothing
else survives: the entry is the C `_start`, the one loadable segment is
clang's, and `_arena_start` and `_arena_end` sit on top of each other.

**EVERYTHING ELSE DOWNSTREAM WAS INDIFFERENT**, which is worth listing
because each was a plausible place to trip:

- the ELF→sosimg converter reads PT_LOAD program headers and nothing
  else — no section names, no symbol table, no Saw metadata — so a
  hand-linked C ELF converts exactly as a Saw one does;
- one loadable segment is fine (the emitter refuses ZERO, not one);
- `validate_image` checked magic, v3, arch tag, segment count, the
  segment table's bounds, the entry's width, the entry inside
  `[link_base, link_top - stack_len)` and the entry inside an EXECUTABLE
  segment — all of which a C image satisfies for the ordinary reason
  that its linker script is the same one;
- `check_segment`'s page-alignment rule on arm64 is satisfied by
  `user.ld`, not by anything the package wrote;
- the runner's child machinery — `.incbin` into `.childimg`, the blob
  row and the destination row in the boot region table, the index
  coupling to `children` — never looks inside an image;
- and the LAUNCHER cannot tell. `tests/c-hello/src/main.saw` is
  `death-notify`'s program with different strings: two Memory
  capabilities into `process_create`, a masked System handle given under
  a tag, a Waiter on the child's Process handle, a §8 status word out of
  the wake. Root never parses, so root never noticed.

**NO NEW BUILD LEG WAS ADDED TO THE RUNNER**, which the brief expected
would be needed. The two package constants and the case are the whole
diff to `tools/sos_runner.py`; `_build_root_image` already builds child
packages with the same call it builds roots with, and a C package is a
package.

## 7. Finding 6 — sized what's-missing for M7's libc shim

Marked **[K]** kernel, **[U]** userspace convention, **[T]** toolchain.
Sized in days of one agent's work, and the sizing assumes the M6 fs and
namespace exist where an item names them.

### The one that is a hazard rather than a gap

- **K1. FP/SIMD state is not saved across a trap, and EL0 may use it.**
  `hal/arm64/kernel/boot.S` sets `CPACR_EL1.FPEN = 3`, which is "no
  trapping at EL0 **or** EL1" — so a userspace program on Profile B may
  execute Advanced SIMD today. `TrapFrame` is 34 doublewords: elr, spsr,
  SP_EL0 and x0-x30, and no FP state whatsoever. Two processes using `q`
  registers would therefore corrupt each other across a context switch,
  silently. **The probe did not trip it** — its arm64 image contains
  ZERO SIMD register references, verified by disassembly — but
  `rt/common_c/support.c` compiles to 16 SIMD references at `-O2` in the
  KERNEL, and a libc's `memcpy`/`memset` is the same idiom on the same
  optimizer, so the first real C program in userspace is likely to be
  the one that finds this. Three ways out and they are not equivalent:
  save FP in the frame (correct, costs every trap), trap EL0 FP by
  setting FPEN to 0b01 and fault the process (cheap, honest, closes the
  door), or compile all userspace C with `-mgeneral-regs-only` (needs
  DF-172c's manifest key and is a convention nobody can enforce).
  **Recommend the FPEN narrowing now** — it costs one instruction and
  turns a silent corruption into a named fault — and price the save when
  a floating-point workload actually asks. riscv32 is unaffected:
  `rv32imac_zicsr` has no FP registers to lose. 1 day for the narrowing,
  3-4 for lazy FP save.

### Kernel

- **K2. `debug_print` is ONE BYTE PER SYSCALL**, so `write(2)` over it
  is a trap per character — the probe's 38-byte greeting is 38 syscalls.
  M6's console service over a pipe is the intended answer and this item
  is a note that it is on M7's critical path, not a request for a
  bulk-write op. 0 days here; a dependency edge.
- **K3. No `brk`/`sbrk` and no anonymous `mmap`.** `malloc` has to sit
  on `Memory.split` + `Process.map`, which exist, so this is a shim
  design question rather than a kernel gap — but the shim needs a region
  to grow into and the placement rules are M5's. 0 days kernel, 3-5 days
  shim (item U3).
- **K4. No thread-pointer convention** (`tp` / `TPIDR_EL0`), so no TLS
  and therefore no per-thread `errno`. `Process.thread_create` takes
  entry/stack/arg and the frame has nowhere to carry a TLS base. This is
  the one item on the list that is a genuine ABI addition. 2-3 days
  including both HALs and the spec §5.7 wording.
- **K5. No futex-shaped wait**, so pthread mutex/condvar contention has
  no primitive. Events plus Waiters cover the wake, not the
  address-keyed sleep. The Sep-4 thread-surface note already parks this;
  repeated here because a libc that ships `pthread_mutex_lock` needs it.
  4-5 days, and it should be designed rather than sized.
- **K6. No signals.** Design 31 already scopes them MINIMAL; nothing the
  probe saw changes that.

### Userspace convention

- **U1. `argv`/`envp` have no representation.** Design 31 part 3's
  proposal — a Memory region the parent fills, given under a well-known
  tag — is untouched by this probe and remains the right shape; the
  probe's child took a single boot handle and nothing else. 2 days for
  the convention plus a decoder in the shim.
- **U2. `fd` table.** Design 31 part 2's "fd = index into a userspace
  table of handles" is unbuilt. 2-3 days on top of M6's fs protocol.
- **U3. `malloc`.** The sosrt arena is Saw's and a C image links none of
  it; the shim owns its own allocator over `Memory.split`. 3-5 days, and
  it is the item most likely to want K3 revisited.
- **U4. `stdio`.** Over U2 and M6's console service. 3-4 days for the
  subset toybox reaches for.
- **U5. `exit` semantics.** `Process.exit` takes a full word and the §8
  status is `Exited`(1) << 16 | code; POSIX narrows to 8 bits at `wait`.
  Decide once, in the shim's `waitpid`, and write it down. 0.5 days.

### Toolchain

- **T1. Blade cannot build a package with no Saw source** (finding 5).
  An emit path that takes OBJECTS rather than a package is what an M7
  build of toybox wants; until then, one empty `.saw` file and 138
  retained bytes per binary. sawlang-side. 1-2 days.
- **T2. No per-target C flags in a manifest** — DF-172c, already filed.
  It blocks `-Oz` on the C half and, more importantly,
  `-mgeneral-regs-only` (K1). sawlang-side. 1 day.
- **T3. No `sos.h`, and no way to link the exported C surface without
  `sosrt`** (finding 4). This IS design 31 part 1 and it is the single
  highest-value toolchain item on the list: it retires the hardcoded op
  numbers and makes the vDSO discipline true for C. 1 day for the
  header, 3-5 for the linkable floor.
- **T4. No archive/multi-binary story.** Toybox is one binary
  dispatching on `argv[0]`, which needs U1 and nothing else — noted so
  the scoping session does not price a loader it does not need.

**Total, excluding M6 dependencies and excluding K5's design: about four
to six weeks of agent work, and none of it is research.** The hinge the
brief named — "no C `main` has ever been compiled, linked, loaded, run
and exited clean" — is now proven rather than assumed, and the probe
found no wall behind it.

## 8. Gate evidence

**380/380 = 128 + 128 + 124**, up from 377/377 = 127 + 127 + 123. The
new case is `[124/124] c_hello` on each profile — LAST, appended, so
every pre-existing case keeps its ordinal.

**THE TRANSCRIPT DIFF IS SEVEN HUNKS AND EVERY ONE OF THEM IS THIS
UNIT'S.** With the `[i/N]` totals and the image-size column normalised
out (they move on any addition and say nothing), a full `diff -u` of the
before and after runs is: two package build lines added on each of the
three profiles (three hunks), one `✓ c_hello` row added on each (three
hunks), and the total. NOT ONE PRE-EXISTING CASE ROW MOVED, on any
profile — including the three cases CLAUDE.md lists as carrying
timing-dependent rows, which is what one would expect from a unit that
adds a case and touches no kernel.

The `c_hello` transcript, riscv32 (arm64 is the same lines with 64-bit
hex; riscv32-flat is identical to riscv32 here):

    SOS M1: kernel up on riscv32 (QEMU virt)
    SOS: root image ok segments=0x00000002 entry=0x80200e46 prio=0x01010100
    SOS: boot regions=0x00000002
    SOS: console handover
    SOS chello: started
    SOS cchild: hello from freestanding C
    SOS cchild: wrote 38 bytes
    SOS: process exit: code=0x00000037 process=0x00000001
    SOS: process teardown handles=0x00000002 threads=0x00000001 ... process=0x00000001
    SOS chello: woke status=65591
    SOS chello: done
    SOS: process teardown handles=0x00000008 ... process=0x00000000

`code=0x37` is 55, `main`'s own return value carried through the crt0
into `Process.exit`; `status=65591` is `Exited`(1) << 16 | 55, read out
of the launcher's wake record.

**THE THREE ASSERTED LINES ARE THREE DIFFERENT CLAIMS**, which is why
the case does not simply check that the child said something:

- the greeting proves the image LOADED — `.rodata` reached its link
  address and the console op works;
- `wrote 38 bytes` proves it COMPUTED — 38 is counted by a loop, held in
  a stack frame the kernel's initial `sp` provides, and rendered by an
  integer division. A replay of a constant string cannot produce it;
- the status proves it ENDED PROPERLY — `Exited` in the high half is a
  kind no fault produces, and a child that could not reach its own
  Process object would fall off `_start` and read `Faulted` instead.

## 9. What the lead should see before merging

1. **K1, the arm64 FP/SIMD hazard**, is the one finding that is about
   the KERNEL rather than about M7's scope. It is latent today (no
   userspace image in the tree emits SIMD; the probe's arm64 image was
   disassembled to check) and it is not this unit's to fix under the
   brief's constraints. It wants a decision before the first real C
   program lands.
2. **No STOP condition fired.** The brief named three — a
   kernel/sysapi/ABI change, a sawc/blade feature request, and a case
   that could not be made gate-stable — and none was reached. The case
   passed on the first boot on all three profiles and is fully
   deterministic: no timer, no tick, no interleave, because the child's
   death is the only thing that can wake the launcher.
3. **Four doc items were LOCATED AND NOT EDITED**, per the brief, and
   are in `designs/todo.md`'s unit-8 pile: the stale "three scripts"
   count in four places, the four missing rows of "Required of a
   process", the typed-C row's unstated `sosrt` dependency, and
   spec §5.7's vDSO claim not holding for C.
4. **The probe deliberately duplicated `hal/<arch>/user/syscall.c`**
   rather than naming it, to measure what an outside program pays. If
   the lead prefers the tree not to carry a second copy, the manifest's
   `native` line can name the HAL files instead and
   `tests/c-child/syscall.c` shrinks to the three named ops — the
   measurement is recorded here either way and does not need the file to
   survive.
5. **`tests/c-child/sos.h` hardcodes three KERNEL-INTERNAL op numbers.**
   They are correct today and there is nothing keeping them correct
   tomorrow. If `kernel/abi/src/ops.saw` is renumbered before design 31
   part 1 lands, the `c_hello` case is what will notice — loudly, as a
   fault rather than a link error.
