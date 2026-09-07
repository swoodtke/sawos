---
{"assignee":"","author":"agent:codex-todo-import","body_bytes":4108,"created":"1788791279","id":"SO-3","labels":["todo-import","backlog","task","plan"],"priority":"normal","project":"SO","revision":1,"sequence":3,"status":"open","title":"Replace test pool-base constants with actual Mapping addresses","updated":"1788791279"}
---


## Description

## Scope and status

Replace test pool-base constants with actual Mapping addresses

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

- [sawos/designs/033-placement.md](https://github.com/swoodtke/sawos/blob/754ec9570aef54609820cc7e28457c7623547670/designs/033-placement.md)
- [sawos/designs/040-m5-docs-sweep.md](https://github.com/swoodtke/sawos/blob/754ec9570aef54609820cc7e28457c7623547670/designs/040-m5-docs-sweep.md)

## Source context

Historical closed subcases are context, not new work. Legacy DF/SL/SO numbers use a nonbreaking hyphen here to avoid accidental tracker links; the linked source retains the original spelling.

### sawos TODO lines 36–68

[Original record](https://github.com/swoodtke/sawos/blob/754ec9570aef54609820cc7e28457c7623547670/designs/todo.md#L36-L68)

- **`sos_test_pool_base()` MIGRATES ONTO `Mapping.base()`, and
  `map_basics`' header is corrected with it** [#33 findings 5/6,
  RE-FILED BY M5 unit 8 after declining it — `designs/040` §7].
  Eleven test packages read an address out of a per-triple C constant
  whose arm64 body has, since design 33, returned a VIRTUAL address
  under a name that says "pool base" — documented at length in
  `tests/poolbase_arm64.c` and true only by the coincidence that every
  process's first mapping lands at `0x4024_0000`. `Mapping.base()`
  exists, asks the kernel, needs no build-time constant and is right
  on every tier; `tests/map-placed` is the worked example. **DECLINED
  BY UNIT 8 for four reasons, the second decisive**: (1) it is a code
  migration in eleven programs, not a docs or harness edit, and unit
  8's constraint was comments/docs/spec/harness; (2) **IT IS NOT A
  MECHANICAL SUBSTITUTION** — `map-wx-refused` needs the address to
  attempt a map that is REFUSED (so no Mapping exists to ask),
  `child-touch` pokes an address in a CHILD holding no Mapping at all,
  and `memory-recycle` does arithmetic with the pool LENGTH, which
  `Mapping.base()` has no twin for; each wants its own answer and two
  want a new way to learn an address; (3) folding ~24 moving
  image-size rows into unit 8's diff would have made that unit's row
  enumeration unreadable as two causes; (4) **the authorization
  argument is answerable** — the rows it needs are riscv32 and flat
  image-size rows, and ANY unit editing a shared test package moves
  those, so this needs its own brief naming them rather than a rare
  standing authorization. Scope when taken: the eleven packages, the
  two `tests/poolbase_*.c` files (delete `sos_test_pool_base`, keep
  `sos_test_pool_len` — a length is a length on any tier), and
  `map_basics`' header, whose prose still describes a double map as
  two rows over ONE address deciding by "the hardware's own matching
  rule" — under placement they are two rows at two addresses and
  nothing is aliased. The case passes and asserts the right value
  today; only its explanation is of a machine arm64 no longer is.


## Comments

