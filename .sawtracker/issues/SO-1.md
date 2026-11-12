---
{"acceptance":[],"assignee":"","author":"agent:codex-todo-import","body_bytes":2413,"closed":"","created":"1788791279","id":"SO-1","labels":["todo-import","queued","design","plan","design-proposal"],"order":0,"parent":"","priority":"high","project":"SO","queue_order":140,"revision":5,"sequence":711,"stage":"queued","status":"open","title":"Scope M6 storage, namespace, loader and UART shell","updated":"1794500307"}
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

<!-- sawtracker:comment {"author":"agent:claude-sawos","body_bytes":936,"created":"1794498261","id":"c1"} -->
**Lead, Nov 12: queued for the week of Nov 12–19.** The deliverable this week
is the scoped milestone brief only, as a sawtracker doc for user review; it
dispatches no implementation.

The M5-close scoping session this issue waits on never happened: the split
from sawlang, the pin bumps and the lifecycle work (SO-52, SO-40) came first.
What changed since the seed, for the brief to account for:
- Pids and the sender handle (SO-49) and `ProcessObserve` (SO-50) are queued
  ahead of this. They give a name service a client identity and the shell a
  `ps`.
- `kill` is SO-70. The shell needs it to stop a program.
- A launcher still cannot send a Process or System handle in a message
  (SO-7), which the loader and launcher split may want.
- The shipping kernel arms no tick and priorities are not built (§7), so a
  program that never yields holds the machine. The brief should say whether
  the shell milestone needs preemption.


