---
{"assignee":"","author":"agent:codex-todo-import","body_bytes":2998,"created":"1788791281","id":"SO-7","labels":["todo-import","backlog","design","plan","design-proposal"],"priority":"normal","project":"SO","revision":1,"sequence":7,"status":"open","title":"Design leaf handle modules for Process/System message attachments","updated":"1788791281"}
---


## Description

## Scope and status

Design leaf handle modules for Process/System message attachments

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

- [sawos/designs/018-rendezvous.md](https://github.com/swoodtke/sawos/blob/754ec9570aef54609820cc7e28457c7623547670/designs/018-rendezvous.md)

## Source context

Historical closed subcases are context, not new work. Legacy DF/SL/SO numbers use a nonbreaking hyphen here to avoid accidental tracker links; the linked source retains the original spelling.

### sawos TODO lines 116–132

[Original record](https://github.com/swoodtke/sawos/blob/754ec9570aef54609820cc7e28457c7623547670/designs/todo.md#L116-L132)

- `Process` and `System` in messages — a sysapi MODULE-SPLIT ruling
  [#18 As-built finding 1, Sep 2]: `PipeHandle` ships six cases
  (`Memory`, `IoMemory`, `PipeInlet`, `PipeOutlet`, `PipeReply`,
  `PipeRequest`) and the kernel refuses the other two AT THE SENDER in
  `msg_kind_of`. The obstacle is module ORDER and not doctrine —
  `sos.pipe` sits below `sos.system` and `sos.process` (both name pipe
  types in their `give` and factory surfaces), so a case holding a
  `Process` wrapper is a cycle, DF‑232e's shape. The scouted path: split
  the wrapper STRUCTS into a leaf module both layers import and leave
  the METHODS where they are, which Saw's extensions make mechanical
  since a type's declaration and its methods need not share a file. It
  moves every handle wrapper in the package, which is why it wants a
  lead ruling before it is written; what it buys is a launcher able to
  hand a child a Process or System handle AFTER `start`, which no
  in-tree program needs today. The kernel side is one line in
  `msg_kind_of` when it comes


## Comments

