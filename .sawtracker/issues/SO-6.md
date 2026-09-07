---
{"assignee":"","author":"agent:codex-todo-import","body_bytes":2942,"created":"1788791281","id":"SO-6","labels":["todo-import","backlog","task","plan"],"priority":"normal","project":"SO","revision":1,"sequence":6,"status":"open","title":"Test attenuated Reply and Resolve rights through child transfers","updated":"1788791281"}
---


## Description

## Scope and status

Test attenuated Reply and Resolve rights through child transfers

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

- [sawos/designs/022-one-shot-discipline.md](https://github.com/swoodtke/sawos/blob/754ec9570aef54609820cc7e28457c7623547670/designs/022-one-shot-discipline.md)

## Source context

Historical closed subcases are context, not new work. Legacy DF/SL/SO numbers use a nonbreaking hyphen here to avoid accidental tracker links; the linked source retains the original spelling.

### sawos TODO lines 98–115

[Original record](https://github.com/swoodtke/sawos/blob/754ec9570aef54609820cc7e28457c7623547670/designs/todo.md#L98-L115)

- **`PipeRequestRight.Reply` HAS NO TEST ANY MORE** [#22 As-built
  finding 1, Sep 2]. Ruling 12(c) took `Mint` out of
  `pipe_request_rights()`, and the only way this tree ever produced a
  `PipeRequest` handle WITHOUT `Reply` was `pipe-no-reply` minting an
  attenuated sibling — so that spelling died with the bit and the case
  retargeted to the one-name rule instead. The gate itself is still in
  `pipe_request_op_decoded` and is now reachable ONLY through a `give`
  keep mask into another process (`Take` mints the full set; a
  message-carried handle carries the sender's entry rights verbatim),
  which is a two-process case: root posts, takes the obligation, gives
  it to a child with `keep = Transfer | Wait`, and the child's `reply`
  faults with `AccessDenied`. It wants a new child package (~60 lines,
  `child-narrow`'s shape) and one runner entry. THE SAME HOLE EXISTS AT
  `PipeReplyRight.Resolve` for the same reason and would be covered by
  the same case shape at the other end. Filed rather than done because
  design 22's scope was the discipline, not the coverage the discipline
  displaced.


## Comments

