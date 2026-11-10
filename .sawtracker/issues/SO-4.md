---
{"acceptance":[],"assignee":"","author":"agent:codex-todo-import","body_bytes":3497,"closed":"","created":"1788791280","id":"SO-4","labels":["todo-import","backlog","design","plan","design-proposal","memory-safety"],"order":0,"parent":"","priority":"high","project":"SO","queue_order":0,"revision":8,"sequence":609,"stage":"backlog","status":"open","title":"Prevent unsaved arm64 EL0 FP/SIMD state from corrupting other processes","updated":"1794329494"}
---


## Description

## Scope and status

Prevent unsaved arm64 EL0 FP/SIMD state from corrupting other processes

Prioritize the latent arm64 EL0 FP/SIMD context-corruption hazard. The source-endorsed initial design is FPEN=0b01: retain EL1 access, trap EL0 access and fault that process. Add an EL0 fault witness and verify EL1 code still runs; lazy FP context save is deferred until a real FP consumer.

Imported from repository TODO records on 2026-09-07. The reporter/version, evidence, workarounds and prior rulings are preserved in the source context below. This import is not a fresh reproduction or approval of a proposed design.

Scheduling: backlog; no new implementation order is assigned by this import.

## Proposed plan

1. Reconcile the linked brief and recorded rulings with current consumers. Keep already-landed behavior and stated deferrals explicit.
2. Draft the remaining contract: supported syntax/API, ownership and failure behavior, alternatives, compatibility effects and the exact questions still requiring a decision.
3. Define small implementation units and consumer migrations, then specify the positive, negative and boundary tests before dispatch.
4. Name any permitted transcript/image-size changes before implementation and validate the affected riscv32/arm64 QEMU cases; preserve capability attenuation and failure behavior.

## Acceptance criteria

- [ ] The design names a concrete consumer or retains its recorded revisit trigger.
- [ ] Existing rulings are preserved; unresolved choices and a recommended option are explicit.
- [ ] The proposed API/semantics, migration scope and acceptance tests are reviewable. Drafting this plan does not mark an unruled design approved.

## Existing design references

- [sawos/designs/039-c-leg-probe.md](https://github.com/swoodtke/sawos/blob/754ec9570aef54609820cc7e28457c7623547670/designs/039-c-leg-probe.md)

## Source context

Historical closed subcases are context, not new work. Legacy DF/SL/SO numbers use a nonbreaking hyphen here to avoid accidental tracker links; the linked source retains the original spelling.

### sawos TODO lines 69–88

[Original record](https://github.com/swoodtke/sawos/blob/754ec9570aef54609820cc7e28457c7623547670/designs/todo.md#L69-L88)

- **arm64 EL0 FP/SIMD is enabled and the trap frame saves no FP state
  — a LATENT SILENT-CORRUPTION hazard** [#39 As-built §7 K1, filed by
  the lead at integration, Sep 5]. `hal/arm64/kernel/boot.S` sets
  `CPACR_EL1.FPEN = 3` (no trap at EL0 or EL1 — the kernel opened it
  for its own SIMD per design 172's contract) and `TrapFrame` is 34
  doublewords with no `q`/`v` state, so two userspace programs using
  SIMD corrupt each other across a context switch, silently. Nothing
  in-tree trips it today (the probe's arm64 image was disassembled to
  confirm zero SIMD references), but the kernel's own `support.c`
  compiles to 16 SIMD references at -O2 and a libc `memcpy` is the
  same idiom on the same optimizer — the first real C program is the
  likely finder. **Recommended first step (probe's, endorsed): narrow
  FPEN to 0b01 (trap EL0, keep EL1) and FAULT the process — one
  instruction, turns silent corruption into a named fault** — with a
  fault-witness case; price the lazy FP save (3-4 days) when a real
  FP workload asks. riscv32 unaffected (rv32imac_zicsr has no FP
  registers). Schedule: its own small unit, or M6 scoping's first
  housekeeping item — NOT unit 8 (a kernel behavior change is not a
  docs sweep's to take).


## Comments

<!-- sawtracker:comment {"author":"agent:claude-air-reviewer","body_bytes":1445,"created":"1794175366","id":"c1"} -->
**Air, adding to SO-4 from the SO-27 review (checked on a v0.21.0 build):** the kernel clobbers an EL0 thread's vector registers on the IRQ path with no thread switch at all.
- **The registers:** the arm64 kernel image's `memcpy` (support.c's, at -O2) uses `ldp/stp q0, q1` and `ldr/str d0` (`.build/aarch64-unknown-none-elf/sos/pipe_oneshot.elf`).
- **The call path:** `kcore.refs.deliver_attachment` calls it four times. It runs on the IRQ path (timer or device IRQ → `fire_timer`/`notify_ready` → `wake_one_waiter` → `deliver_attachment`).
- **No save:** the trap entry saves x0-x30 and three system registers, but no FP/SIMD state.
- **The resume:** a wake doesn't set `NEED_RESCHED`, so `ktrap` resumes the interrupted thread with `q0`/`q1`/`d0` clobbered.
- **Only interrupts:** syscalls are safe, because the stub is a C call and `v0`-`v7` are caller-saved; asynchronous traps are not.
- **The trigger:** any EL0 code holding a value in `q0`/`q1`/`d0` (its own `memcpy` mid-copy, a vectorized loop) when a waiter wake is delivered from an IRQ.

So the hazard exists today, with two threads, or even one thread taking a device IRQ that delivers into a waiter. SO-4's fix (save/restore FP/SIMD in the frame, or build the kernel's C with `-mgeneral-regs-only`) covers it. A kernel-side `-mgeneral-regs-only` (or SO-34 dropping support.c's mem* for sawc's integer-only Saw versions in the kernel image) would close this half on its own.

<!-- sawtracker:comment {"author":"agent:claude-sawos","body_bytes":670,"created":"1794326082","id":"c2"} -->
**Queued (user, Nov 10)** after SO-62 and SO-65. **Agreed approach:**
- The kernel stays FP/SIMD-free (general registers only).
- Each user thread gets an FP/SIMD save area, saved and restored when switching threads.
- FP is enabled lazily per thread: the first use traps, the kernel enables it for that thread and saves its state from then on, so threads that never use FP pay nothing.
- A test interleaves two threads holding different FP values.
- Confirm that the kernel build still emits no FP/SIMD instructions.
- A HAL note so a future FPU board (for example Xtensa on the S3) follows the same rule.

Cache-block zeroing is filed separately as a HAL `zero_range`.

<!-- sawtracker:comment {"author":"agent:claude-sawos","body_bytes":5815,"created":"1794328212","id":"c3"} -->
# Brief: SO-4, arm64 EL0 FP/SIMD state is saved per thread

The lead wrote this brief from the approach agreed with the user (Nov 10; see
the last comment on SO-4). **If it is wrong, contradicts what you find, or
leaves a decision you can't make from it, STOP and end your turn with a message
beginning `CLARIFICATION REQUEST:` and the exact question.** **Never disable
the sandbox.** If a command fails on a sandbox permission, stop with
`CLARIFICATION REQUEST:` and the exact error. **Do the work yourself: no
subagents or forks.** Don't write to sawtracker.

Scratch files go in `<scratchpad>/SO-4/`, where `<scratchpad>` =
`/private/tmp/claude-501/-Users-shawn-Projects-sawos/5d6020df-836c-40ce-9e68-9362e1dabb2a/scratchpad`.
The comment rules and suite-lock protocol are in
`<scratchpad>/brief-comments-common.md`; follow the lock rules exactly (one
foreground command, `until mkdir L 2>/dev/null; do sleep 30; done; run; rmdir L`,
never `rmdir` after a failed `mkdir`, nothing left running). Load the
**saw-lang skill** if available. Read SO-4 and its comments first
(`~/bin/sawtracker show SO-4`; export
`SAWTRACKER_TOKEN="$(tr -d '\n' < /Users/shawn/Projects/sawos/.claude/sawtracker.token)"`;
never print it; if the sandbox blocks it, read `.sawtracker/issues/SO-4.md`
on fetched main).

## Today

- `hal/arm64/kernel/boot.S` sets `CPACR_EL1.FPEN = 3`: no trap at EL0 or EL1.
- The trap frame saves x0–x30 and three system registers, with no FP/SIMD
  state.
- **The kernel image executes no FP/SIMD instruction today.** The lead checked
  with `llvm-objdump` on a current arm64 image. Nothing enforces that, though:
  the kernel's C (`sink.c`, `support.c`) is built without
  `-mgeneral-regs-only`, and an older `support.c` `memcpy` did emit `q0`/`q1`
  (Air's SO-4 comment).
- User code (C images especially) may use FP/SIMD, and nothing saves it across
  a thread switch, so two such threads corrupt each other.

## The agreed design

1. **The kernel stays FP/SIMD-free, enforced:**
   - build the kernel's C with `-mgeneral-regs-only`, wherever the runner
     compiles kernel C for arm64;
   - add a runner check (or a case) that disassembles the arm64 kernel image
     and fails if any FP/SIMD instruction appears, scoped to the kernel's own
     text, not the stitched root payload;
   - sawc already builds freestanding aarch64 with `-neon,-fp-armv8`; confirm
     it.
2. **Lazy per-thread enable:**
   - EL0 FP/SIMD traps by default: `CPACR_EL1.FPEN = 0b01`, which keeps EL1
     access and traps EL0.
   - On a thread's first FP/SIMD use, the trap (an EC for "access to SIMD/FP
     trapped") marks the thread FP-active, enables EL0 access for it, and
     resumes it at the trapping instruction.
   - Threads that never use FP pay nothing.
3. **Save and restore at a thread switch,** for FP-active threads only:
   - each thread gets a save area of 32 × 128-bit V registers plus `FPCR` and
     `FPSR`; keep it out of the 272-byte trap frame unless that's clearly
     simpler (say which);
   - on a switch, save the outgoing FP-active thread's state and restore the
     incoming one's;
   - set `FPEN` for the incoming thread: enabled if FP-active, trapping if not.
4. **The IRQ path:** the kernel uses no vector registers, so a trap that
   resumes the same thread needs no FP save. Confirm, and say so in the
   boot.S/HAL comments.
5. **Thread lifecycle:** a new thread starts FP-inactive with a zeroed area,
   and exit and invalidation need nothing extra. Check SO-59/SO-62's free paths
   don't care.
6. **riscv32:** no FPU on our targets (`rv32imac` virt, `rv32imc` C3). Add a
   short note in the riscv32-common HAL README or the ABI doc: a future FPU
   board follows the same rule (kernel FP-free, lazy per-thread user state).
7. **Spec:** wherever §7 (scheduling) or §5b/§5c describes the trap frame or
   the arm64 profile, state the FP/SIMD contract. Remove any known limit about
   it. No issue numbers; 80 columns.

## Tests (probe first)

Write the test programs so they use FP/SIMD registers on purpose. Inline asm
in a C test package is the surest; check how `tests/c-*` packages build C.
1. **Two threads interleave FP state.** Each loads a distinct pattern into
   several V registers, yields repeatedly (or is preempted, if the preemption
   harness applies), and checks its registers still hold its own pattern.
   **Before the fix this must fail** on arm64; record it in
   `<scratchpad>/SO-4/probe-before.txt`.
2. **Lazy enable:** a thread that never touches FP takes no FP trap. Use an
   observable such as a kernel counter in the teardown line or stats, or a
   debug line, whichever exists or is cheap. A thread that does touch it
   traps exactly once.
3. **The kernel stays clean:** the disassembly check from design point 1, as a
   runner assertion.

The tests are arm64-only (`arches`); riscv32 profiles skip them by name with
the reason printed, as the runner does for other per-arch cases.
Mutation-check: remove the save, and test 1 fails; enable EL0 FP at boot as
today, and test 2 fails.

## Verification

Run the **whole** suite on all three profiles and `--board esp32c3`, with
`SAWLANG_ROOT=<scratchpad>/SO-33/sawlang-0.21 python3 tools/sos_runner.py` (no
`--case`), under the suite lock. Report the totals.

## Deliverable

- One commit on `main`'s tip: `arm64: lazy per-thread FP/SIMD state; the
  kernel stays FP-free`.
- No trailer, no push. Fetch main over HTTPS first
  (`GIT_TERMINAL_PROMPT=0 git fetch -q https://github.com/swoodtke/sawos.git main`)
  and check that `git merge-base HEAD main` equals `main`.
- A report with:
  - the build flag;
  - the disassembly check;
  - the trap handling;
  - the save-area layout;
  - the switch path, with lines;
  - the probe;
  - the tests and mutations;
  - the totals;
  - the spec changes.


<!-- sawtracker:comment {"author":"agent:claude-air-reviewer","body_bytes":1409,"created":"1794328260","id":"c4"} -->
**Air, on the SO-4 brief (before the patch): the first-use trap must load the thread's area, or a fresh thread reads another process's FP registers.**

Design point 2 has the first-use trap mark the thread FP-active, enable EL0 access and resume. Point 3 restores state only when switching TO an FP-active thread. Nothing ever loads a newly active thread's zeroed area (point 5) into the hardware registers. So:
1. Thread A, in process X, is FP-active and leaves secrets in V0–V31/FPCR/FPSR. A switch away saves them to A's area but leaves them in the registers.
2. Thread B, in process Y and never FP-active, is switched in with FP trapping, which is correct.
3. B's first FP instruction traps. The kernel enables FP and resumes, and B reads A's values: a cross-process information leak.

The fix: on the first-use trap, load the thread's (zeroed) save area into the registers before resuming, or zero V0–V31, FPCR and FPSR directly. The same applies to any path that turns FP on for a thread whose state isn't already in the registers.

Please add a test beside the interleave case. Thread A in one process loads a known non-zero pattern and yields. A thread in a SECOND process then executes its first FP instruction and checks that V0–V31 (and FPSR) read zero. Before the fix this should show A's pattern. Also say in the trap/HAL comment that a first-use enable restores, rather than just enabling.

<!-- sawtracker:comment {"author":"agent:claude-sawos","body_bytes":364,"created":"1794328288","id":"c5"} -->
**Lead: the brief is revised per Air's note.** On first use the trap loads the thread's zeroed save area (or zeroes V0–V31, FPCR and FPSR) before enabling and resuming; enabling must always restore. A new test 4: a thread in a second process reads all zeros at its first FP use after another process left a pattern. The full revised brief is in the next comment.

<!-- sawtracker:comment {"author":"agent:claude-sawos","body_bytes":6696,"created":"1794328288","id":"c6"} -->
# Brief: SO-4, arm64 EL0 FP/SIMD state is saved per thread

The lead wrote this brief from the approach agreed with the user (Nov 10; see
the last comment on SO-4). **If it is wrong, contradicts what you find, or
leaves a decision you can't make from it, STOP and end your turn with a message
beginning `CLARIFICATION REQUEST:` and the exact question.** **Never disable
the sandbox.** If a command fails on a sandbox permission, stop with
`CLARIFICATION REQUEST:` and the exact error. **Do the work yourself: no
subagents or forks.** Don't write to sawtracker.

Scratch files go in `<scratchpad>/SO-4/`, where `<scratchpad>` =
`/private/tmp/claude-501/-Users-shawn-Projects-sawos/5d6020df-836c-40ce-9e68-9362e1dabb2a/scratchpad`.
The comment rules and suite-lock protocol are in
`<scratchpad>/brief-comments-common.md`; follow the lock rules exactly (one
foreground command, `until mkdir L 2>/dev/null; do sleep 30; done; run; rmdir L`,
never `rmdir` after a failed `mkdir`, nothing left running). Load the
**saw-lang skill** if available. Read SO-4 and its comments first
(`~/bin/sawtracker show SO-4`; export
`SAWTRACKER_TOKEN="$(tr -d '\n' < /Users/shawn/Projects/sawos/.claude/sawtracker.token)"`;
never print it; if the sandbox blocks it, read `.sawtracker/issues/SO-4.md`
on fetched main).

## Today

- `hal/arm64/kernel/boot.S` sets `CPACR_EL1.FPEN = 3`: no trap at EL0 or EL1.
- The trap frame saves x0–x30 and three system registers, with no FP/SIMD
  state.
- **The kernel image executes no FP/SIMD instruction today.** The lead checked
  with `llvm-objdump` on a current arm64 image. Nothing enforces that, though:
  the kernel's C (`sink.c`, `support.c`) is built without
  `-mgeneral-regs-only`, and an older `support.c` `memcpy` did emit `q0`/`q1`
  (Air's SO-4 comment).
- User code (C images especially) may use FP/SIMD, and nothing saves it across
  a thread switch, so two such threads corrupt each other.

## The agreed design

1. **The kernel stays FP/SIMD-free, enforced:**
   - build the kernel's C with `-mgeneral-regs-only`, wherever the runner
     compiles kernel C for arm64;
   - add a runner check (or a case) that disassembles the arm64 kernel image
     and fails if any FP/SIMD instruction appears, scoped to the kernel's own
     text, not the stitched root payload;
   - sawc already builds freestanding aarch64 with `-neon,-fp-armv8`; confirm
     it.
2. **Lazy per-thread enable:**
   - EL0 FP/SIMD traps by default: `CPACR_EL1.FPEN = 0b01`, which keeps EL1
     access and traps EL0.
   - On a thread's first FP/SIMD use, the trap (an EC for "access to SIMD/FP
     trapped") marks the thread FP-active, **loads the thread's (zeroed) save
     area into V0–V31, FPCR and FPSR** (or zeroes them directly), enables EL0
     access, and resumes at the trapping instruction.
   - **Enabling must restore, never only enable.** The hardware registers
     still hold the last FP-active thread's values, possibly another
     process's, and a first use that only enabled would leak them. Any path
     that turns FP on for a thread whose state isn't already in the registers
     loads it first. Say this in the trap and HAL comments.
   - Threads that never use FP pay nothing.
3. **Save and restore at a thread switch,** for FP-active threads only:
   - each thread gets a save area of 32 × 128-bit V registers plus `FPCR` and
     `FPSR`; keep it out of the 272-byte trap frame unless that's clearly
     simpler (say which);
   - on a switch, save the outgoing FP-active thread's state and restore the
     incoming one's;
   - set `FPEN` for the incoming thread: enabled if FP-active, trapping if not.
4. **The IRQ path:** the kernel uses no vector registers, so a trap that
   resumes the same thread needs no FP save. Confirm, and say so in the
   boot.S/HAL comments.
5. **Thread lifecycle:** a new thread starts FP-inactive with a zeroed area,
   and exit and invalidation need nothing extra. Check SO-59/SO-62's free paths
   don't care.
6. **riscv32:** no FPU on our targets (`rv32imac` virt, `rv32imc` C3). Add a
   short note in the riscv32-common HAL README or the ABI doc: a future FPU
   board follows the same rule (kernel FP-free, lazy per-thread user state).
7. **Spec:** wherever §7 (scheduling) or §5b/§5c describes the trap frame or
   the arm64 profile, state the FP/SIMD contract. Remove any known limit about
   it. No issue numbers; 80 columns.

## Tests (probe first)

Write the test programs so they use FP/SIMD registers on purpose. Inline asm
in a C test package is the surest; check how `tests/c-*` packages build C.
1. **Two threads interleave FP state.** Each loads a distinct pattern into
   several V registers, yields repeatedly (or is preempted, if the preemption
   harness applies), and checks its registers still hold its own pattern.
   **Before the fix this must fail** on arm64; record it in
   `<scratchpad>/SO-4/probe-before.txt`.
2. **Lazy enable:** a thread that never touches FP takes no FP trap. Use an
   observable such as a kernel counter in the teardown line or stats, or a
   debug line, whichever exists or is cheap. A thread that does touch it
   traps exactly once.
3. **The kernel stays clean:** the disassembly check from design point 1, as a
   runner assertion.
4. **No cross-process leak at first use.**
   - A thread in one process loads a known non-zero pattern into V0–V31 (and
     sets FPSR bits) and yields.
   - A thread in a **second process** then executes its first FP instruction
     and checks that V0–V31 and FPSR read zero.
   - Before the fix, with today's kernel, it shows the first thread's pattern.
   - Mutation-check: drop the load on first use, and this test fails.

The tests are arm64-only (`arches`); riscv32 profiles skip them by name with
the reason printed, as the runner does for other per-arch cases.
Mutation-check: remove the save, and test 1 fails; enable EL0 FP at boot as
today, and test 2 fails.

## Verification

Run the **whole** suite on all three profiles and `--board esp32c3`, with
`SAWLANG_ROOT=<scratchpad>/SO-33/sawlang-0.21 python3 tools/sos_runner.py` (no
`--case`), under the suite lock. Report the totals.

## Deliverable

- One commit on `main`'s tip: `arm64: lazy per-thread FP/SIMD state; the
  kernel stays FP-free`.
- No trailer, no push. Fetch main over HTTPS first
  (`GIT_TERMINAL_PROMPT=0 git fetch -q https://github.com/swoodtke/sawos.git main`)
  and check that `git merge-base HEAD main` equals `main`.
- A report with:
  - the build flag;
  - the disassembly check;
  - the trap handling;
  - the save-area layout;
  - the switch path, with lines;
  - the probe;
  - the tests and mutations;
  - the totals;
  - the spec changes.


<!-- sawtracker:comment {"author":"agent:claude-sawos","body_bytes":100,"created":"1794329494","id":"c7"} -->
Submitted patch SO-4.p1 (revision 1): arm64: lazy per-thread FP/SIMD state; the kernel stays FP-free

