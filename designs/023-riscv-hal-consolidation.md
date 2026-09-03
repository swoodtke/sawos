# SawOS design 23 — riscv32 HAL consolidation (the named later pass)

User-ruled Sep 2 ("now that the board is clean we should consolidate
the riscv hals where possible"), review WAIVED by the user — this
brief is the record, not a review gate. It is the SIBLING-COPY rule's
own named later pass (hal/riscv32-esp32c3/README.md wrote it at the
placeholder): design 20 built the C3 board as its own directory with
copies, both boards have landed and gated, and now the copies
consolidate. arm64 is untouched — the ruling names the riscv HALs.

## The measured surface (lead, Sep 2, at design 20's integration)

- `kernel/sink.c` (125 lines) and `user/syscall.c` (61): BYTE-
  IDENTICAL between the boards. Mandatory dedup — one copy each.
- `kernel/lib.saw`: virt 1,240 / c3 1,593 lines, ~800 board-specific
  (UART sink, timer, interrupt wiring, board map) — the REST is the
  shared riscv32 core: PMP arithmetic (TOR pairs, cfg packing,
  grant-row programming), trap-frame shape, CSR discipline. The
  substantial win.
- `kernel/boot.S`: virt 235 / c3 297, ~108 lines differ (board
  entry/copy loop vs common CSR + stack + trap-vector setup).
  Optional dedup — take it only if the split is clean.
- Linker scripts stay PER BOARD (virt.ld vs esp32c3.ld and the user/
  scripts genuinely differ; c3 deliberately has no child2.ld).
- Drift is already real: virt grew `user/child2.ld` (unit 5) after
  the sibling copy — the consolidation exists to stop exactly this.

## The mechanics (probe first, then pick)

`kernel/core` imports the module named `hal`, and the runner maps
`hal=<board>/kernel` per build — that seam DOES NOT MOVE. The shared
core becomes a NEW module (working name `rv32core`) in a shared
directory (proposed: `hal/riscv32-common/`), added as one more
`--module-path` on riscv32 builds; each board's `hal` module imports
it and re-exports what `kcore` consumes (`public import` — design
249/SL-7's closure made re-export binding real; PROBE the re-export
of statics/functions/extern seams against pinned sawc BEFORE
committing to the shape, and if a wall appears — SL-6's one-owner
extern rule is the likely one — fall back to the C-style split:
shared .saw files that only the board modules name, or keep the
affected declarations per-board and dedup the rest). The shared C
files move to the common directory and both boards' build entries
point at them. Record whichever shape survives the probe in the
As-built AND at the top of `hal/riscv32-common/` — with the
origin-note convention retired at the files that stop being copies.

## Constraints

- BEHAVIOR-PRESERVING, bit for bit: `make sos-test` transcripts
  BYTE-IDENTICAL both arches (this pass compiles the same code from
  new places), and `make sos-smoke-esp32c3` 3/3 with its transcript
  matching design 20's recorded oracle. Both run under the suite
  lock; the smoke is non-gating for CI but it is THIS unit's proof
  that the C3 board survived its own consolidation.
- arm64 untouched. kernel/core, rt/, sysapi untouched (`hal`'s
  import surface must not change from kcore's side).
- The runner: build-wiring edits only (module paths, shared-file
  locations) — no case, assertion, or report changes.
- If the probe shows the .saw split cannot be made without changing
  kcore's view of `hal`, STOP and park — the C-file dedup and boot.S
  fragments still land, and the .saw wall gets filed (likely a new
  SL entry).

## The proof

The two gates above, transcript-diffed; the As-built records the
final file map (what lives where, what imports what), the probe
results, and the line counts before/after — the number the pass
exists to shrink.
