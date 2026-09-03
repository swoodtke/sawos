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

---

# As-built (agent, Sep 2 2026)

**BOTH GATES GREEN AND BOTH TRANSCRIPTS BYTE-IDENTICAL, image sizes
included — there is no delta to explain.** The pass compiles the same
code from new places, and the proof is that nothing moved at all.

## 1. What landed

The brief's mandatory half, its substantial half AND its optional half.
Nothing was parked. The wall the brief named as likely — SL-6's
one-owner `extern` rule — did not fire, because the block was split by
CALLER rather than shared; see §4.

## 2. The file map

`hal/riscv32-common/` is the new shared directory, with its own README
holding the split rule, the two-import-line explanation, and the worked
cases for which side a declaration belongs on.

```
hal/riscv32-common/
  README.md          the split rule, and what adding a third board costs
  kernel/lib.saw     the `rv32core` MODULE — trap frame + FRAME_BYTES, the
                     PMP staging apparatus (TOR pairs, cfg packing, NAPOT,
                     the grant budget), frame_init / resume_frame, the
                     payload + region-table seams, the cause and syscall
                     decoding, arch_tag, device_window_ok, IRQ_NONE /
                     IRQ_TIMER / WORD_BITS / INTERRUPT_FLAG / InterruptCause
  kernel/trap.S      trap_entry (the 32-word save/restore and the mode
                     witness) and sos_resume_frame
  kernel/sink.c      the CSR and linker-symbol leaves — ONE copy
  user/syscall.c     the `ecall` stub — ONE copy

hal/riscv32/               (QEMU `virt`)     hal/riscv32-esp32c3/
  kernel/boot.S     _start, kernel_fault, the stack reservation
  kernel/lib.saw    the board `hal` module: console device, the two runtime
                    hooks, how the machine stops, the timer, the interrupt
                    controller, the memory map, the interrupt-CLASS shadow —
                    plus the two import lines that re-export `rv32core`
  kernel/*.ld       per board, unchanged
  user/*.ld         per board, unchanged
```

**THE `hal` SEAM DID NOT MOVE.** `kernel/core` still imports the module
named `hal`; the runner still maps `hal=<board>/kernel`. `rv32core` is a
SECOND module on an extra `--module-path`, and each board opens with:

```saw
public import rv32core.{ arch_tag, cause_tag, device_window_ok, FRAME_BYTES,
    frame_init, GRANT_ROW_BUDGET, IRQ_NONE, IRQ_TIMER, is_interrupt,
    is_syscall, MAX_ROOT_SEGMENTS, payload_end, payload_start, prot_commit,
    prot_device, PROT_GRAIN, prot_region, prot_reset, region_table_end,
    region_table_start, resume_frame, syscall_arg0, syscall_arg1,
    syscall_arg2, syscall_handle, syscall_op, syscall_return, trap_pc, }
import rv32core.{INTERRUPT_FLAG, InterruptCause, WORD_BITS}
```

Twenty-eight re-exported names — exactly the `hal.*` set `kcore` names,
minus the fourteen that are genuinely the board's — and three more the
board itself needs and `kcore` does not.

## 3. Line counts, before and after

| File | Before | After |
|---|---:|---:|
| `hal/riscv32/kernel/boot.S` | 235 | **92** |
| `hal/riscv32/kernel/lib.saw` | 1240 | **756** |
| `hal/riscv32/kernel/sink.c` | 125 | *(moved)* |
| `hal/riscv32/user/syscall.c` | 61 | *(moved)* |
| `hal/riscv32-esp32c3/kernel/boot.S` | 297 | **149** |
| `hal/riscv32-esp32c3/kernel/lib.saw` | 1593 | **1095** |
| `hal/riscv32-esp32c3/kernel/sink.c` | 125 | *(deleted)* |
| `hal/riscv32-esp32c3/user/syscall.c` | 61 | *(deleted)* |
| `hal/riscv32-common/kernel/lib.saw` | — | **605** |
| `hal/riscv32-common/kernel/trap.S` | — | **193** |
| `hal/riscv32-common/kernel/sink.c` | — | **142** |
| `hal/riscv32-common/user/syscall.c` | — | **68** |
| **Total** | **3737** | **3100** |

**−637 lines, −17.0%**, and the number understates the win twice over.
The two shared C files GREW (125 → 142, 61 → 68) because each gained a
header saying it is shared and why; `trap.S` likewise carries a header
the two `boot.S` copies did not need. What actually retired is one
complete second copy of every shared file — the deduplicated text is
about 950 lines, and the residue is prose that did not exist before.

The C3's `lib.saw` is still the larger of the two boards at 1095, and
that is honest: SYSTIMER's register block alone is 30 fields, the
interrupt matrix has no PLIC-shaped claim register, and several of its
comments are probe records the virt HAL has no equivalent for.

Whole-change diffstat: 17 files outside `tests/` (+1257 / −1704), plus
120 `tests/*/Saw.toml` and `root/Saw.toml` at one line each.

## 4. The probe results — every question the brief asked

Probed against pinned sawc 0.4.0 @ `46eebb36`, on the real tree rather
than a toy, by compiling `kernel/main.saw` for both boards.

1. **RE-EXPORTING A `static` THROUGH `public import` WORKS, AND IT STILL
   FOLDS AS A CONSTANT IN THE IMPORTER'S IMPORTER.** This was the
   load-bearing question — `kcore/limits.saw` reads `hal.FRAME_BYTES`,
   `hal.GRANT_ROW_BUDGET` and `hal.MAX_ROOT_SEGMENTS` inside
   `static_assert`s, so a re-export that resolved but did not fold would
   have silently disarmed three kernel invariants. **Proved by
   perturbation, not by absence of error:** `GRANT_ROW_BUDGET` was
   temporarily set to 10 in `rv32core`, and the build failed with
   `static assertion failed: a process's grant record must hold every row
   its profile may install` — which is `kcore`'s own
   `static_assert(hal.GRANT_ROW_BUDGET <= 9, ...)`, two module hops from
   the declaration. Restored to 8 afterwards.
2. **RE-EXPORTING FUNCTIONS WORKS** — all twenty, reached as `hal.<name>`
   from `kcore` exactly as before.
3. **RE-EXPORTING A `public enum` WORKS, AND ITS CASES STILL FOLD**: the
   C3's `static_assert(TIMER_CPU_INT == (InterruptCause.MachineTimer as
   UInt), ...)` names an imported enum's case in a const position and
   compiles.
4. **TWO IMPORT LINES NAMING ONE MODULE ARE COMPLEMENTARY** — a
   `public import rv32core.{…}` beside a plain `import rv32core.{…}` is
   not a duplicate-import error. That is what let the seam stay exactly
   the twenty-eight names `kcore` uses instead of widening `hal`'s
   surface with three the board happens to need.
5. **AN `unsafe static var` LIVES FINE IN THE SHARED MODULE** — the four
   `PMPCFG*` shadow words moved with the functions that stage and publish
   them, so no static is written across a module boundary. That was a
   design choice, not a discovery: `IRQ_CLASSES` deliberately stayed per
   board for the same reason (§5).
6. **SL-6 DID NOT FIRE, AND THE REASON IS WORTH RECORDING.** An `extern`
   declaration carries no visibility and cannot be re-exported, so
   `sink.c`'s surface genuinely cannot be declared once and shared. It did
   not need to be: the block is split **by CALLER**. `rv32core` declares
   the seven leaves it calls (`sos_resume_frame`, the two PMP writers, the
   two payload bounds, the two region-table bounds); each board declares
   the ones IT calls (`sos_mie_write` on both, `sos_wait_for_irq` on virt
   only — the C3's `wait_for_irq` is deliberately empty, so it names no
   extern and `--gc-sections` drops the leaf). The two halves are
   disjoint, which satisfies SL-6's one-owner rule with no shared
   declaration to own. **No new SL entry is owed by this pass.**
7. **THE ASSEMBLY SPLIT NEEDED NO NEW SYMBOL.** The only file-local
   coupling in `boot.S` was `sos_resume_frame`'s `j .Lresume_frame` into
   the middle of `trap_entry` — and both ends went to the shared half, so
   the local label never crosses a file. The cross-file references are
   three existing `.globl`s (`_stack_top`, `kernel_fault`, `ktrap`).
   Assembly has nowhere to DECLARE that contract, so `trap.S`'s header
   writes it out as an IN/OUT list; that is the one place this split is
   weaker than the Saw one.
8. **REPOINTING 121 MANIFESTS MOVED NO `Saw.lock`.** Blade's
   `manifest_deps_hash` (checked in `blade/src/lock.saw`) hashes
   DEPENDENCIES only, so a changed `[sos.<triple>] native` path does not
   invalidate a lock. Verified by reading the resolver and confirmed by
   the gate: not one `Saw.lock` is in this commit.

## 5. Which side each declaration landed on, and the three judgement calls

The rule is in `hal/riscv32-common/README.md`: **ask what would change if
the board changed.** Three calls are worth defending here because the
mechanical answer and the right answer differ.

- **`device_window_ok` is SHARED, `map_target_ok` is PER BOARD**, and
  they sit four lines apart in the original file. The first is PMP's
  NAPOT rule (power-of-two, naturally aligned, at least `PROT_GRAIN`);
  the second is *where RAM is*. Same shape, opposite answers.
- **The interrupt-CLASS shadow stayed PER BOARD even though its two tag
  constants are byte-identical.** On virt they ARE `mie` bit positions
  and `irq_class_enable` writes the shadow to the register; on the C3
  they are software tags and the register always takes MEIE. Sharing the
  constants would have meant sharing a name whose MEANING differs, which
  is the drift this pass exists to stop rather than an instance of it.
- **`ROOT_STACK_LEN`, `DEVICE_GRANT_LEN`, `UNMAPPED_PROBE` and the
  64 KiB stack reservation stayed PER BOARD despite having equal values
  on both.** Each is a budget or a map fact a board is entitled to choose
  differently, and each sits inside a doc comment about the board's own
  memory map. Four identical numbers is a cost worth paying to keep a
  board able to answer for itself; a fifth board changing one of them
  must not have to un-share it first.

`boot.S` split three ways and the residue is exactly the three things
design 20 listed as its board differences — `_start` (reset entry, plus
the XIP `.data` copy on the C3), `kernel_fault` (a DEVICE write: a
finisher on virt, a console line on the C3), and the stack reservation.
That the pre-existing "three things differ" list survived the split
unchanged is the best evidence the line was drawn in the right place.

## 6. Build wiring (the runner — build edits only)

Three per-arch keys, and the arm64 row spells out that it brings none:

- `hal_native` — where this architecture's `sink.c` comes from
  (`riscv32-common/kernel` vs `arm64/kernel`), surfaced through
  `arch_dirs`.
- `hal_modules` — extra `--module-path` args (`[RV32_CORE_MODULE]` vs
  `[]`), appended in `_build_elf`.
- `hal_asm` — shared assembly to assemble beside the board's `boot.S`
  (`riscv32-common/kernel/trap.S` vs nothing), handled in
  `_build_shared`.

The `--board esp32c3` section takes the same three by hand, since it is
its own build path. **No case, assertion or report changed** — the
report is byte-identical, which is the check.

## 7. The gates

**(a) `SAWLANG_ROOT=$HOME/Projects/sawlang make sos-test`** — 116 cases
per arch, `ALL SOS TESTS PASSED (232 passed across riscv32 + arm64)`.
The console transcript is **BYTE-IDENTICAL** to the baseline captured at
`a88a602` before any edit — `diff` returns empty. That covers every one
of the 232 case verdicts AND all 232 `.sosimg` byte sizes, so not even
the three documented timing-dependent case families had anything to
excuse. Nothing in the kernel or in any test package changed size, which
is the expected result of compiling identical code from new paths.

**(b) `SAWLANG_ROOT=$HOME/Projects/sawlang make sos-smoke-esp32c3`** —
3/3, and byte-identical both to a fresh baseline at `a88a602` and to the
oracle recorded in `designs/020-esp32c3-smoke.md` §9:

```
[1/3] ✓ boot  (456784 bytes of flash)
[2/3] ✓ timer  (485456 bytes of flash)
[3/3] ✓ isolation  (495808 bytes of flash)
```

**NO IMAGE-SIZE DELTA.** The brief allowed one — code compiles from new
places — and there is none to report: all three flash sizes match design
20's recorded numbers exactly.

The three CONSOLES were replayed from the built flash images and diffed
against §9 row by row. Every row matches, **including the addresses that
would have moved had the link layout shifted**: `entry=0x403a82cc`
(timer), `entry=0x403a8388` / `epc=0x403c5528` / `tval=0x4037c000`
(isolation), and both `process teardown` counter lines.

**ONE ORACLE CORRECTION, and it is a transcription artifact rather than a
behaviour delta** — see the addendum written into design 20 §9. The
isolation oracle shows `SOS c3iso: root survived child status=131072`
followed directly by `SOS c3iso: child syscalls=0 faults=1`, with no
blank line between them. There is one, and there always was: both lines
are `print("...\n", args)` in `tests/c3-isolation/src/main.saw`, which is
exactly the doubling §9's own note explains and which the timer oracle
shows consistently on every such row. Nothing this pass touched can
produce a blank line — the C3 root programs are untouched and their
images are byte-size identical — so the paste dropped it.

## 8. Findings

1. **The predicted wall was not the wall, and there was no wall.** The
   brief expected SL-6 to force a fallback and it did not, because the
   `extern` question turned out to be answerable by SPLITTING rather than
   by sharing. Worth carrying forward: SL-6 bites a package that needs
   ONE module to own a whole extern surface; it does not bite a split
   whose halves call disjoint symbols. SL-6's text stays accurate and
   needs no amendment.
2. **`public import` is stronger than the tracker's history suggested.**
   SL-7 spent three sites establishing that re-export did NOT widen
   extension-method lookup (fixed at the first pin bump) and design 249
   established that it binds names. What this pass adds is that a
   re-exported `static` is still a CONSTANT two hops away — which is what
   makes a facade module viable for a kernel HAL at all, since three of
   `kcore`'s invariants are compile-time assertions over HAL numbers.
3. **The C3 board HAL is now smaller than the virt one it was copied
   from** (1095 + 149 board lines vs the shared 605 + 193 + 142), which
   is the shape a board HAL should have: the emulator-probe prose is the
   bulk of what is left, and every line of it is about this part.
4. **Two repo-structure docs still describe the pre-23 layout and are
   DELIBERATELY LEFT for design 24** (whose §6 consistency grep names
   `README`, `CLAUDE.md` and kernel doc banners by name, and which
   dispatches next):
   - `CLAUDE.md`'s repo map: "`hal/riscv32/` `hal/arm64/` — per-arch:
     kernel/ (boot.S, trap entry, board sinks, PMP, virt.ld) + user/ (the
     ecall stub + root.ld), ABI.md each" — the trap entry, the sinks, PMP
     and the ecall stub have all moved to `hal/riscv32-common/`, and
     `hal/riscv32-esp32c3/` is missing from the map entirely.
   - `README.md` line 46: "`hal/riscv32/, hal/arm64/` per-architecture
     boot, trap entry, linking" — same correction.
   Both are one-line fixes; they are named here so design 24 does not
   have to rediscover them.
5. **`hal/riscv32/user/ABI.md` stayed where it is on purpose.** It
   documents a file that now lives in `hal/riscv32-common/user/`, and
   moving it would break `hal/arm64/user/ABI.md`'s cross-reference to it
   — and arm64 is untouched by ruling. It gained a header saying where
   the file went and that what remains in its own directory is the virt
   board's user-side linker scripts.

## 9. What this pass did NOT touch

`kernel/core`, `rt/`, `kernel/sysapi`, every `hal/arm64/` file, every
linker script, and every `.saw` file under `tests/` — the 120 test-side
edits are one `native =` path per `Saw.toml` and nothing else. No case,
assertion or report line moved in the runner. No new SL entry is owed.
