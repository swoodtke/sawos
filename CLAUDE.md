# sawos — Development Guide

The SOS microkernel (design sawlang#140 onward), written in Saw, split out
of the sawlang repository Aug 28 2026 (design sawlang#238). **spec.md is
authoritative** for kernel behavior. This file covers how to develop here.

## Repo map (flattened at the split — sawlang#238 D-e)
```
kernel/            # main.saw + core/lib.saw — the module every kernel image
                   # shares (drivers, trap frame, ktrap, object-op dispatch,
                   # the sosimg loader)
kernel/abi/        # KERNEL-INTERNAL: every op number/right/status, one place
kernel/sysapi/     # the PUBLIC `sos` module exported to userspace (vDSO
                   # discipline: numbers are not ABI)
hal/arm64/         # one directory, arch + board: kernel/ (boot.S with the
                   # vectors, sink.c, lib.saw, virt.ld) + user/ (the svc stub
                   # + root.ld/child*.ld), ABI.md each
hal/riscv32-common/  # SPLIT ARCH/BOARD since design 23 — the ARCH half both
                   # riscv32 boards import as the `rv32core` module: kernel/
                   # (trap.S, sink.c, lib.saw = trap frame, PMP, ktrap seam)
                   # + user/ (the ecall stub); see its README.md
hal/riscv32/       # the riscv32 `virt` BOARD: kernel/ (boot.S, lib.saw =
                   # the `hal` facade re-exporting rv32core, virt.ld, ABI.md)
                   # + user/ (root.ld, child.ld/child2.ld/child3.ld, ABI.md
                   # — one script per resident image, the tier-2 cost)
hal/riscv32-flat/  # the FLAT BUILD PROFILE of that board (design 35): 250
                   # lines of facade, NOT a fork — it re-exports rv32core and
                   # the virt board (mapped a second time as `rv32virt`) and
                   # overrides the protection seam. THE THIRD GATE RUN
hal/riscv32-esp32c3/ # the ESP32-C3 BOARD, same shape (design 20) — reached
                   # only by `make sos-smoke-esp32c3`, never by sos-test
rt/common/         # `sosrt`: the SOS runtime, arch-free + role-free Saw
rt/common_c/       # support.c — mem* + atomic libcalls, ONE copy
root/              # the root server: a real Blade package, sosimg emit
tests/             # kernel entries + hand-assembled payloads/images
tools/             # sos_runner.py (build+boot harness, QEMU both arches)
                   # + toolchain.py (the sawlang resolver)
```

## The toolchain (design sawlang#238 D-b)
sawos builds against a sawlang checkout resolved by `tools/toolchain.py`:
1. Env overrides: `SAWLANG_ROOT=/path/to/sawlang` (the ruled everyday
   spelling — a kernel build NEEDS a root, since sosrt/imgformat are source
   packages a `$PATH` sawc cannot supply), or `SAWC`/`BLADE` directly.
2. A `sawc` on `$PATH` whose `--version` matches `sawlang.pin` (enough only
   for work that needs no source packages).
3. A cached fetch of the pinned sha (`~/.cache/saw-toolchain/<sha>/`).
The refusal names all three. Bump `sawlang.pin`'s two lines together,
rigorously — the check is version-only (D-b2's documented asymmetry).

## Testing
```bash
make sos-test        # tools/sos_runner.py: builds kernel AND root, stitches
                     # them, boots THREE PROFILES under QEMU — riscv32,
                     # arm64, and riscv32-flat (design 35). THE gate.
```
At M5's close the gate is **382 = 129 + 129 + 124**. The flat profile runs
four fewer cases, each excluded BY NAME with its reason printed above the
rows, because each asserts a HARDWARE denial the tier disclaims — no silent
skips (the no-silent-caps doctrine).

Every change gates on `make sos-test` before commit. The acceptance oracle
tradition: diff the console transcript, don't just read "green"
(sawlang#238 unit 0).

`make sos-smoke-esp32c3` (design 20, stubbed) is NON-GATING board
smoke against the machine-local Espressif QEMU — never part of
sos-test, never CI.

The runner is PARALLEL BY DEFAULT — `-j 4`, a user ruling (Sep 1: four
P-cores, six E-cores; the parallelism tracks the P-cores). The report is
printed in case-definition order whatever the completion order, so a
transcript diff reads the same as it always did. `-j 1` is the serial
harness, kept reachable for exactly that comparison. THREE CASES CARRY
TIMING-DEPENDENT CONSOLE OUTPUT that moves run to run in EITHER mode and
that NO ASSERTION READS: `thread_preempt`'s `SOS: timer tick` lines and
its `interrupts=` count; `timer_interval`'s `fires=` counters; and
`process_stats`' `interrupts=` count (design 16 — the column is printed
and never asserted, because a tick lands where the host puts it; it
happens to read 0 there today, since root takes no tick in user mode
over so short a run, and a value that is not 0 is not a finding).
**`thread_preempt`'s A/B INTERLEAVE IS NOT ON THAT LIST — it IS asserted**
(design 35 §7a's correction of this paragraph, fixed by M5 unit 8): the
case matches `AB`, `BA`, `AB` as ordered ADJACENT PAIRS, which is "the
processor crossed between the two threads at least three times, whoever
went first". It is matched against the letters-only projection of the
console (the kernel's own `SOS: ` lines stripped, since they land inside
the workers' byte stream) and with overlapping matches (three crossings
can be consecutive) — `strip_kernel_lines` and `overlapping_matches` on
that case, both documented at the site. None of the above appears in the
transcript at all on a passing run: the report prints case rows, image
sizes and totals, so a console line only reaches the screen when a case
goes red. Anything moving in a transcript diff is a real finding.

## The suite lock (machine-wide, sawos's own)
QEMU-suite invocations serialize through a mkdir lock at
`/private/tmp/claude-<uid>/sawos-suite-lock` (uid = `id -u`) — a DIFFERENT
path from sawlang's `saw-suite-lock`, so the two repos' gates never queue
on each other. Same protocol as sawlang's: acquire + gate + release in
foreground steps, never background the wait or the gate, clear only
verified-dead.

## Writing Saw here
Load the **saw-lang skill** before writing or reviewing any .saw file.
**Idiom ruling (user, Aug 31): the bind-or-bail shape on a Result is
an inline `let x = try f() catch { …; return }`** — the implicit
`error` is in scope, a diverging catch satisfies any type — never a
`match` whose Err arm just prints and exits. `match` stays for arms
doing genuinely different work and for negative tests asserting a
specific error value. Freestanding printing stays `{}` format args.
The statement-position form (`try f() catch {…}` on Result<Void, E>)
is legal since sawc 0.2.1 (SL-12 closed) — use the guard shape at
every position. **The fold shape is ruled in too (user, Sep 1): a
`match` whose Err arm just supplies a fallback value
(`case Err(_) -> v`) is `try f() catch { v }`** — bind-or-default
joins bind-or-bail. The tree is FULLY CONVERTED (Sep 1, 95 sites);
the 33 `match` sites that remain are by design — real-work arms,
negative tests, and success predicates (`case Ok(_) -> true`, a
shape try/catch cannot express since the catch yields the payload's
replacement, not a second value for the Ok path).
**Optional discipline (user, Sep 2): an Optional in an API signature
is an ANTIPATTERN unless the absence is a real domain value. A
transient condition ("not yet", "try again") rides the ERROR channel
— error + retry — never `Ok(None)`/`Ok(false)`:** making every
common-path caller unwrap an Optional for a case that rarely happens
is the wrong trade (designs/010 ruling 12 re-ruled design 13 D-2's
poll split on exactly this; the tree had ZERO `case None` arms). On
this machine the saw-lang skill is a user-level symlink
(`~/.claude/skills/saw-lang -> <sawlang checkout>/.claude/skills/saw-lang`,
sawlang#238 D-f) — one canonical copy, no drift. On a machine without a
sawlang checkout, re-create the symlink against one or copy the skill in.

## Design records (sawlang#238 D-c, amended Aug 28)
sawlang stays the archival record for its briefs and the sos DF
entries — cite them as `sawlang#NNN` — but the sos briefs
(78/79/112/140/162/172/178/232, the 238 split brief, and the 238
oracle transcript) are COPIED into `designs/sawlang/` here for
reference. sawos-native numbering starts at 1 in `designs/`. The open
work tracker is `designs/todo.md` (sawlang's tracker flow: agents
close entries in place, the lead moves closed entries to done files at
integration). The M3 ladder (sawlang#232: interruptibility,
CreateProcess, handle lifecycle, give, Memory/IoMemory, quotas, death
notifications) is the plan of record and runs HERE.

## Conduct
Same doctrine as sawlang (its CLAUDE.md is the fuller statement): never
hide errors; APIs do the expected thing; no attribution trailers in
commits; kernel branches follow the park-for-review flow the user runs.
