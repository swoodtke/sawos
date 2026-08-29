# SawOS design 5 — the sysapi split (user-requested, Aug 29)

Status: AUTHORED Aug 29 2026 (lead). User ruling: refactor
`kernel/sysapi/src/lib.saw` (~2,000 lines after unit 3) into
per-object files with `lib.saw` publicly importing the surface — the
kcore precedent applied to the `sos` module ("the kernel used to be
one 4000-line file... the facade holds no code", kernel/core/lib.saw).

MECHANICAL ONLY. Zero behavior change; the acceptance is the
strictest we have: every transcript row byte-identical, the C-export
symbol list identical, no consumer edits (`import sos.{System}` and
every other existing import spelling keeps meaning what it means, via
`public import` re-exports in the facade).

## The files, and the declared order

Imports point only DOWNWARD (DF-232e diagnoses cycles now, but the
order is written out as kcore's is, with the where-the-line-falls
paragraph). Bottom to top:

```
floor      the two syscall externs, checked/checked_value/call_value,
           sos_handle_release + sos_handle_mint C seams, NO_HANDLE
           sentinel vocabulary the deinits share
memory     Memory + BootHandle (take_memory) + decode_boot_handle
thread     Thread
event      Event
waiter     Waiter
interrupt  Interrupt
timer      Timer
clock      Clock (constructs Timer)
process    Process (constructs Thread/Event/Waiter/Interrupt/Memory
           records via the drain; give funnels; start)
system     System (constructs Process and Clock; process_create;
           mint of siblings is per-wrapper and stays with each)
rt         sos_rt_write / sos_rt_abort (the runtime hooks)
lib        THE FACADE — public import lines only, no code
```

Exact placement of shared helpers (e.g. where the per-wrapper mint
method bodies get their plumbing) is the implementer's, recorded in
the As-built; the constraint is the order above and one rule: a
helper needed by two files lives below both, never duplicated.

Cross-file internals go `public(package)` — the documented DF-232f
cost kcore already pays; do not widen anything to bare `public` that
was not already surface.

## What this is NOT

No renames of types, methods, labels, exports, or rights; no
docstring rewording beyond what the file moves force (each file gains
the house `//!` header naming its one job); no test edits of any
kind; no runner edits. If the split exposes a latent problem (a
cycle the single file was hiding, a visibility gap), that is a
FINDING to raise, not a thing to quietly restructure around.

## Acceptance

`make sos-test` both arches: 124/124 with EVERY row byte-identical to
the pre-split run (baseline at the merge base, full diff, zero
content changes — build-info lines for sosimg sizes should also be
compared and any movement explained; pure code motion may shift
sizes, which is acceptable ONLY if named and accounted). Also:
`nm`-level comparison of one representative image's exported
sos_* symbols against baseline, identical list.

## As built

(Implementer: final file list + order as landed, helper placements,
any finding.)
