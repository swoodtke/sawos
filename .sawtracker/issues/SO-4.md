---
{"acceptance":[],"assignee":"","author":"agent:codex-todo-import","body_bytes":3497,"closed":"","created":"1788791280","id":"SO-4","labels":["todo-import","backlog","design","plan","design-proposal","memory-safety"],"order":0,"parent":"","priority":"high","project":"SO","queue_order":0,"revision":3,"sequence":591,"stage":"backlog","status":"open","title":"Prevent unsaved arm64 EL0 FP/SIMD state from corrupting other processes","updated":"1794326082"}
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

