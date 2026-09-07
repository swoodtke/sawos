---
{"assignee":"","author":"agent:codex-todo-import","body_bytes":2357,"created":"1788791282","id":"SO-9","labels":["todo-import","backlog","task","plan"],"priority":"normal","project":"SO","revision":1,"sequence":9,"status":"open","title":"Design length-taking buffered debug_print","updated":"1788791282"}
---


## Description

## Scope and status

Design length-taking buffered debug_print

Imported from repository TODO records on 2026-09-07. The reporter/version, evidence, workarounds and prior rulings are preserved in the source context below. This import is not a fresh reproduction or approval of a proposed design.

Scheduling: backlog; no new implementation order is assigned by this import.

## Proposed plan

1. Confirm the remaining scope and prerequisites against the source record and current tree; completed steps in the historical excerpt are context only.
2. Carry out the named migration, documentation, coverage or implementation work in a bounded change, following the recorded plan and preserving excluded cases.
3. Validate the source’s acceptance conditions, record the changed files and check results, and close only the completed remainder.
4. Name any permitted transcript/image-size changes before implementation and validate the affected riscv32/arm64 QEMU cases; preserve capability attenuation and failure behavior.

## Acceptance criteria

- [ ] Every in-scope item from the source has a disposition and supporting evidence.
- [ ] Required tests/documentation agree with the final behavior; any remaining work is linked explicitly.

## Existing design references

- [sawos/designs/016-process-stats.md](https://github.com/swoodtke/sawos/blob/754ec9570aef54609820cc7e28457c7623547670/designs/016-process-stats.md)

## Source context

Historical closed subcases are context, not new work. Legacy DF/SL/SO numbers use a nonbreaking hyphen here to avoid accidental tracker links; the linked source retains the original spelling.

### sawos TODO lines 166–175

[Original record](https://github.com/swoodtke/sawos/blob/754ec9570aef54609820cc7e28457c7623547670/designs/todo.md#L166-L175)

- buffered `debug_print` — a length-taking form [#16 As-built finding,
  Sep 1]: today the seam traps once per BYTE, so a test's prose
  outweighs its object ops ~10:1 in the syscall column
  (`pipe_oneshot`: ~5 traps of exchange, ~300 of description). A
  buffered form taking (addr, len) is the obvious answer and an ABI
  change with no consumer yet; filed for the unit that first wants
  the column quiet. The interrupt column measures preemption pressure
  on ONE process, not machine load — restate wherever the
  shared-region `top` seed (#16) gets built.


## Comments

