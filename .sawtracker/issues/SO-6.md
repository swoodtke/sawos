---
{"acceptance":[],"assignee":"","author":"agent:codex-todo-import","body_bytes":2942,"closed":"","created":"1788791281","id":"SO-6","labels":["todo-import","task","plan","tests"],"order":0,"parent":"","priority":"normal","project":"SO","queue_order":150,"revision":6,"sequence":745,"stage":"queued","status":"open","title":"Test attenuated Reply and Resolve rights through child transfers","updated":"1794502746"}
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

<!-- sawtracker:comment {"author":"agent:claude-sawos","body_bytes":2709,"created":"1794500579","id":"c1"} -->
## Implementation notes (lead, Nov 12)

**Model:** Sonnet. **Rules:** `.sawtracker/docs/implementer-rules.md`.
**Depends on:** nothing. Tests only: no kernel or sysapi change.

### What is untested

Two rights gates in `kernel/core/dispatch.saw` have no case:
- `obj.allows(PipeRequestRight.Reply)`, on the reply ops;
- `obj.allows(PipeReplyRight.Resolve)`, on resolve.

A handle that lacks the bit can only be made by a `give` with a keep mask
into another process, so each needs a two-process case. The comment above the
`pipe_no_reply` case in `tools/sos_runner.py` says so.

### Case 1: `pipe_reply_narrowed`

1. Model it on the `pipe_delegate` case: there root gives an obligation
   (`PipeRequest`) to a child, which replies. Read that case's root and child
   packages first.
2. Here root gives with `keep: PipeRequestRight.Transfer |
   PipeRequestRight.Wait`, through `Process.give(request:tag:keep:)`
   (`kernel/sysapi/src/system.saw`).
3. The child reads the handle's rights back with `rights()` and prints that
   `Reply` is absent, then calls `reply`. The kernel faults it:
   `SOS: process fault: access denied process=<child>`.
4. Root learns of the death (`get_status`, as `give_keep_mask`'s root does),
   prints what its own claim on that exchange then answers, and exits
   cleanly. Assert the answer you observe and say in the case header why it
   is that one.

### Case 2: `pipe_resolve_narrowed`

The same shape for the client's side: root gives a claim (`PipeReply`) with
`keep: PipeReplyRight.Transfer | PipeReplyRight.Wait`, through
`Process.give(reply:tag:keep:)`. Find the existing case whose child is
`tests/child-resolver` for the unnarrowed flow. The child's `resolve` faults
with access denied.

### Packages

- One new child package per case, or one child that picks its role from the
  boot tags it was given. `tests/child-narrow/` is the manifest to copy: it
  names the riscv32 `child.ld` and the arm64 `user.ld`.
- New root packages beside the existing ones; register each as a
  `*_PKG` constant and a case entry with `"children": [...]`, as
  `give_keep_mask` does.
- These are syscall-path checks, so they run on the flat profile too.

### Mutation checks

For each gate, delete the `allows` check, show the matching case fails (the
child's reply or resolve succeeds), and restore it.

### Also

Rewrite the sentence above `pipe_no_reply` that says no case covers the
`Reply` gate, so it names the case that now does.

### Runs

Both new cases, `pipe_delegate`, `pipe_no_reply`, `give_keep_mask` and every
case that uses `child-resolver`, on three profiles; then `--board esp32c3`.

### Commit

`tests: the Reply and Resolve gates, reached through a give's keep mask`


<!-- sawtracker:comment {"author":"agent:claude-sawos","body_bytes":108,"created":"1794502746","id":"c2"} -->
Submitted patch SO-6.p1 (revision 1): tests: the Reply and Resolve gates, reached through a give's keep mask

