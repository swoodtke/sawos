---
{"assignee":"","author":"agent:codex-todo-import","body_bytes":2100,"created":"1788791279","id":"SO-2","labels":["todo-import","queued","design","plan","design-proposal"],"priority":"normal","project":"SO","revision":1,"sequence":2,"status":"open","title":"Scope M7 POSIX compatibility with toybox and posix_spawn","updated":"1788791279"}
---


## Description

## Scope and status

Scope M7 POSIX compatibility with toybox and posix_spawn

Imported from repository TODO records on 2026-09-07. The reporter/version, evidence, workarounds and prior rulings are preserved in the source context below. This import is not a fresh reproduction or approval of a proposed design.

Scheduling: retain the source’s queue order and prerequisites; seed/scoping tasks do not authorize implementation.

## Proposed plan

1. After M6, scope a C-facing sysapi and libc shim over the existing allocator, pipes, namespace and process capabilities.
2. Specify argv/envp and fd-disposition transfer as a userspace spawn convention, plus waitpid/status and errno mappings; retain bare fork as out of scope.
3. Validate a small utility set first (echo, cat, ls, sha1sum), then the multicall binary and spawning from a native launcher. Price tty/job-control work separately.
4. Set a feasibility checkpoint for the chosen toybox version/configuration and cross-toolchain; do not assume the seed’s spawn premise has been port-verified.

## Acceptance criteria

- [ ] The M6 dependency, minimal libc surface and spawn/fd authority model are explicit.
- [ ] The plan includes a measured utility build/run prototype on both target architectures and a decision on native shell versus toysh.
- [ ] Unruled tty and job-control scope is not silently included in implementation.

## Existing design references

- [sawos/designs/031-m7-posix-seed.md](https://github.com/swoodtke/sawos/blob/754ec9570aef54609820cc7e28457c7623547670/designs/031-m7-posix-seed.md)

## Source context

Historical closed subcases are context, not new work. Legacy DF/SL/SO numbers use a nonbreaking hyphen here to avoid accidental tracker links; the linked source retains the original spelling.

### sawos TODO lines 30–33

[Original record](https://github.com/swoodtke/sawos/blob/754ec9570aef54609820cc7e28457c7623547670/designs/todo.md#L30-L33)

- M7 (after M6): the POSIX compatibility layer, toybox first — seed
  `designs/031` (user-ruled Sep 3; the posix_spawn hinge). Scoping
  session at M6's close.


## Comments

