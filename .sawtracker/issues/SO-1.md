---
{"assignee":"","author":"agent:codex-todo-import","body_bytes":2413,"created":"1788791279","id":"SO-1","labels":["todo-import","queued","design","plan","design-proposal"],"priority":"normal","project":"SO","revision":1,"sequence":1,"status":"open","title":"Scope M6 storage, namespace, loader and UART shell","updated":"1788791279"}
---


## Description

## Scope and status

Scope M6 storage, namespace, loader and UART shell

Imported from repository TODO records on 2026-09-07. The reporter/version, evidence, workarounds and prior rulings are preserved in the source context below. This import is not a fresh reproduction or approval of a proposed design.

Scheduling: retain the source’s queue order and prerequisites; seed/scoping tasks do not authorize implementation.

## Proposed plan

1. At M5 closure, use design 030 to scope CFI NOR pflash first; keep bus-mastering virtio-blk and its TCB/IOMMU implications in a later unit.
2. Specify one capability-based namespace protocol serving both a read-only flat archive and writable tmpfs. Price the service split and the proposed MemoryRight.MapWrite attenuation rule.
3. Plan the userspace loader using image Memory → ProcessCreate → give → start, then a UART client with ls/read/write builtins and a scripted test mode.
4. Define QEMU cases for archive reads, tmpfs create/write/read-back/delete and memory reclamation, loader success/refusal, and shell transcripts on both architectures.

## Acceptance criteria

- [ ] A scoped milestone brief records unit boundaries, protocol, authority/failure rules and the choices still needing a ruling.
- [ ] The plan preserves flash-first and both filesystem backends; it demonstrates the namespace protocol without new kernel-specific filesystem operations.
- [ ] The seed is not treated as an implementation dispatch before the recorded M5-close scoping session.

## Existing design references

- [sawos/designs/030-m6-storage-seed.md](https://github.com/swoodtke/sawos/blob/754ec9570aef54609820cc7e28457c7623547670/designs/030-m6-storage-seed.md)

## Source context

Historical closed subcases are context, not new work. Legacy DF/SL/SO numbers use a nonbreaking hyphen here to avoid accidental tracker links; the linked source retains the original spelling.

### sawos TODO lines 24–29

[Original record](https://github.com/swoodtke/sawos/blob/754ec9570aef54609820cc7e28457c7623547670/designs/todo.md#L24-L29)

- M6 (after M5): the storage milestone — seed `designs/030` (user-
  ruled Sep 3): flash-first block driver, RO archive fs + a simple
  tmpfs (the write path, same protocol), userspace loader, the
  namespace as the protocol, and a simple UART shell (ls/read/write
  builtins — poke at a running system). Scoping session at M5's
  close.


## Comments

