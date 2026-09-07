---
{"assignee":"","author":"agent:codex-todo-import","body_bytes":3497,"created":"1788791280","id":"SO-4","labels":["todo-import","backlog","design","plan","design-proposal","memory-safety"],"priority":"high","project":"SO","revision":1,"sequence":4,"status":"open","title":"Prevent unsaved arm64 EL0 FP/SIMD state from corrupting other processes","updated":"1788791280"}
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

