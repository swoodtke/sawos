# SawOS design 39 — the C-leg probe: one freestanding C process, end to end

**Status: BRIEF, Sep 4 2026 (late night) — user-ruled experiment
("let's try the simple freestanding c process — see what works and
what's missing"). Dispatches as its own unit, scheduled after the
arena unit and the 0.8.0 pin bump, before M5's unit 8.**

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
