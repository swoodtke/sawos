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

BUILT Aug 29. `make sos-test` 124/124 both arches, the full console
transcript BYTE-IDENTICAL to the merge-base run (`diff` empty — every
case row, and every sosimg build-info size line, so there is no size
movement to account for), and the exported `sos_*` symbol list identical
across four packages × two arches × every `.o` and `.elf` (`root`,
`tests/process-lifecycle`, `tests/event-consume-wake`,
`tests/give-word-dead`; `llvm-nm`, 608 lines, `diff` empty).

### The file list and the order as landed

```
floor      the two syscall externs, checked/checked_value/call_value,
           the WHOLE @export'ed C-ABI surface, ALL_RIGHTS, the owning
           tier's contract, SYSTEM_HANDLE + park_system_handle
memory     Memory
thread     Thread
event      Event
interrupt  Interrupt
timer      Timer + arm_from_frame
clock      Clock + now_into_frame (constructs Timer)
waiter     Waiter + decode_wait + wait_into_frame
system     System + Process + BootHandle + decode_boot_handle +
           boot_handle_into_frame
rt         sos_rt_write / sos_rt_abort
lib        THE FACADE — public import lines, plus one plain import
```

Ten modules and the facade; 2,000 lines became 607 / 46 / 75 / 66 / 69 /
112 / 78 / 191 / 724 / 66 / 197. Bodies were moved by LINE RANGE out of
the original, so every moved docstring and statement is byte-identical;
what is written fresh is the eleven `//!` headers, the import lines, the
`public(package)` markers, and the six stale cross-references the moves
broke (listed under findings).

TWO DEPARTURES FROM THE BRIEF'S ORDER, both forced, both findings:

1. **`waiter` moved ABOVE `event`/`interrupt`/`timer`.** `Waiter.add`
   is one overload per kind taking `&Event` / `&Interrupt` / `&Timer`
   and reading the target's handle field, so the aggregator names all
   three and none of them names it. The brief's sketch put waiter below
   interrupt and timer, which is the cycle DF-232e diagnoses.
2. **`process` did not land as its own file.** See finding 1.

### Helper placements

- `checked` / `call_value` — floor, `public(package)`; every object file
  calls them. `checked_value` stays module-private: only `call_value`
  uses it, so nothing widened.
- **The whole per-op C surface went to floor**, not to each object's
  file. An `extern "C"` declaration carries no visibility modifier
  (sawc `sigvis.py`: "private by construction"), so `sos_syscall1` /
  `sos_syscall3` cannot be shared with a sibling module — and two
  modules declaring one symbol is a hard `ambiguous function
  \`sos_syscall1\`: defined in both ... ` at the importer, which was
  probed directly. One extern block, one file; the typed layer above
  calls the named `@export`ed seams, which ARE importable. This also
  keeps the original's own "The C-ABI surface (§5.7)" section intact as
  one seam rather than scattering thirty-one docstrings.
- `SYSTEM_HANDLE` + `park_system_handle` — floor, `public(package)`,
  under the brief's own rule: `sos.system` WRITES the parked handle out
  of `System(boot_handle:)` and `sos.rt` READS it out of the panic
  path's writer, so the helper lives below both. A cross-module
  `public(package) unsafe static var` read works.
- The owning tier's contract (the "ALL NINE WRAPPERS ARE `NoCopy`"
  block) — floor, beside `sos_handle_release`, which is the call every
  wrapper's `deinit` issues. Its banner is the one reworded by a move.
- Seven wrapper handle fields became `public(package)`: Memory, Thread,
  Event, Interrupt, Timer, Clock, Waiter — each is constructed or read
  by a sibling module. `System.handle` and `Process.handle` stay
  PRIVATE: only their own module touches them. Nothing widened to bare
  `public`, and `public(package)` is a real tier here (unlike kcore's
  DF-232f widening) because `kernel/sysapi/Saw.toml` is a manifest root
  and a manifest root IS a package.

### Findings

1. **`System`, `Process` and `BootHandle` CANNOT be three files, and
   this is structural rather than a placement choice.** Every pair names
   the other: `System.process_self` / `process_create` construct a
   `Process`; `Process.give(system:)` takes a `System` by value (design
   4 D-4's courier funnel); `BootHandle` carries a `Memory?`, a
   `Process?` AND a `System?` while `Process.boot_handle_next` answers
   with one. An import cycle is a compile error (DF-232e), and a type's
   extension methods must sit in the module that DECLARES the type —
   lookup consults your module, your direct imports, and the receiver
   type's defining module, and **a re-export widens no extension scope**
   (design 142/229) — so moving `Process.give(system:)` into
   `system.saw` would make it invisible to every consumer that writes
   `import sos.{Process}`. There is no arrangement of files that splits
   them in today's Saw. `system.saw` holds all three, with three section
   banners keeping the seams visibly apart, and its `//!` header carries
   the argument. What would change this: an `internal`/forward-declaration
   tier, or extension-method lookup that follows a facade's re-exports.
   The brief was written before design 4's `give(system:)` and the
   Aug-29 `BootHandleKind.System` case, which is what created the knot.
2. **A locally DECLARED type name and an IMPORTED one do not resolve
   alike, and `Thread` is where it bites.** `std.task` publishes a
   prelude `Thread<T>`. While `struct Thread` was declared in the same
   file that constructed it, the local declaration won. Split out, the
   selective `import sos.thread.{Thread}` in `system.saw` LOST to the
   prelude — `generic struct \`Thread\` requires type arguments`, plus a
   companion `ambiguous struct \`Thread\`: defined in both \`<unknown>\`
   and \`sos.thread\`` reported at line 1:1 of whatever file the reporter
   was on (the diagnostic is emitted with a hardcoded 1,1, so its
   location is noise). Landed fix: `system.saw` imports the QUALIFIER
   (`import sos.thread`) and writes `thread.Thread` at the five places
   `Process` names one. The facade's `public import sos.thread.{Thread}`
   is unaffected and consumers still write the bare `Thread` —
   `tests/event-consume-wake` proves it, byte-identical image.
3. **The facade cannot be `public import` lines ALONE.** `sos.rt`
   publishes nothing: `rt_write` and `rt_abort` are module-private and
   reach the world through `@export` alone, so there is no name to
   re-export — and an import is what compiles a module into the unit.
   `lib.saw` therefore ends with one plain `import sos.rt`, commented.
   Without it a process links with no `sos_rt_write` and its first
   `print` goes nowhere.
4. **Six cross-references went stale by the move and were repaired** (no
   other docstring text changed): `checked`'s "the typed methods below"
   -> "above"; `sos_waiter_wait`'s "`Waiter.wait` below" ->
   "`sos.waiter`'s `Waiter.wait`"; `Memory`'s "see `BootHandle` below"
   -> "in `sos.system`"; `decode_wait`'s "every other decode in this
   module" -> "in this package"; `Waiter.add`'s "the field access below
   is same-module" -> same-PACKAGE, naming the `public(package)` that
   makes it legal; and `SYSTEM_HANDLE`'s "parked for the seams below" ->
   "for the runtime hooks in `sos.rt`". The "typed Saw surface" banner
   became "The OWNING tier's contract", since in `floor.saw` it
   introduces a contract rather than a surface.
5. **The kernel-internal `sosabi` import got NARROWER, not wider.** The
   single 20-line `import sosabi.{...}` block became per-file lists of
   the handful of encodings each module actually uses. The vDSO wall is
   unchanged — every one of those is still an ordinary import, so
   nothing reaches a kernel-internal name through `sos`.
