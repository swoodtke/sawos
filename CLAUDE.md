# sawos — Development Guide

The SOS microkernel, written in Saw, split out of the sawlang repository.
**spec.md is authoritative** for kernel behavior. This file covers how to
develop here.

## Repo map
```
kernel/            # main.saw (the kernel entry) + rootimg.S (stitches the root
                   # image in) + core/ (`kcore`, the arch-free kernel every
                   # image shares: console, ktrap, object-op dispatch, the
                   # sosimg loader; core/lib.saw is its facade)
kernel/abi/        # kernel-internal: every op number/right/status, one place
kernel/sysapi/     # the public `sos` module exported to userspace (vDSO
                   # discipline: numbers are not ABI)
hal/arm64/         # one directory, arch + board: kernel/ (boot.S with the
                   # vectors, sink.c, lib.saw, virt.ld) + user/ (the svc stub
                   # + user.ld, one script for every image), ABI.md each
hal/riscv32-common/  # split arch/board — the arch half both riscv32 boards
                   # import as the `rv32core` module: kernel/ (trap.S, sink.c,
                   # lib.saw = trap frame, PMP, ktrap seam) + user/ (the ecall
                   # stub); see its README.md
hal/riscv32/       # the riscv32 `virt` board: kernel/ (boot.S, lib.saw =
                   # the `hal` facade re-exporting rv32core, virt.ld, ABI.md)
                   # + user/ (root.ld, child.ld/child2.ld/child3.ld, ABI.md
                   # — one script per resident image, because an address is
                   # the same number in every process)
hal/riscv32-flat/  # the flat build profile of that board: a facade, not a
                   # fork — it re-exports rv32core and the virt board (mapped
                   # a second time as `rv32virt`) and overrides the protection
                   # seam. The gate runs this profile
hal/riscv32-esp32c3/ # the ESP32-C3 board, same shape — reached only by
                   # `make sos-smoke-esp32c3`, never by sos-test
rt/common/         # `sosrt`: the SOS runtime, arch-free + role-free Saw
rt/common_c/       # support.c — the arena accessors + word atomics, one copy
root/              # the root server: a real Blade package, sosimg emit
tests/             # kernel entries, test packages, hand-assembled payloads
tools/             # sos_runner.py (build+boot harness, QEMU, every profile)
                   # + toolchain.py (the sawlang resolver) + sosimg_dump.py
```

## The toolchain
sawos builds against a sawlang checkout. `tools/toolchain.py`, a verbatim copy
of sawlang's resolver, locates one in this order:
1. What the environment names: `SAWLANG_ROOT=/path/to/sawlang` (the everyday
   spelling), or `SAWC`/`BLADE` directly. A checkout is recognised by its
   `bin/sawc`.
2. A `sawc` (and `blade`) on `$PATH`. Its `--version` must match
   `sawlang.pin`'s `version`; a mismatch is a refusal naming both, not a
   fall-through to the fetch.
3. A fetch of the pin's `sha`, cached by SHA in `~/.cache/saw-toolchain/<sha>/`
   (`SAW_TOOLCHAIN_CACHE` moves the cache).

Otherwise the refusal names all three. A kernel build needs a checkout
(`SAWLANG_ROOT` or the fetch): the `imgformat` package the kernel compiles
against, and Blade's sources when no `blade` binary is supplied, live inside
one, and a `$PATH` toolchain supplies executables only.

Bump `sawlang.pin`'s two lines together, rigorously. A `$PATH` sawc is checked
by version only, while a fetch is checked by SHA, so a version line that
doesn't belong to its sha lets a `$PATH` compiler pass that the pin never
named.

## Testing
```bash
make sos-test        # tools/sos_runner.py: builds the kernel and root,
                     # stitches them, boots three profiles under QEMU —
                     # riscv32, arm64 and riscv32-flat. The full suite.
```
Implementers run targeted tests while implementing: the cases the change
affects, selected with `--case a,b` (comma-separated or repeated) and, for one
profile, `--arch NAME`:
```bash
SAWLANG_ROOT=/path/to/sawlang python3 tools/sos_runner.py \
    --case thread_basics,tier_word --arch riscv32
```
A `SAWLANG_ROOT` run skips the pin's version check, so it builds with whatever
compiler that checkout holds, which may not be the one the server's `fetch` run
uses.
The sawtracker server runs the full suite. `.sawtracker/tests.json` runs
`make sos-test` on every SO patch, and a merge needs a green run. The server
rebases a patch onto current main before its run. `sawtracker patch log
PATCH` prints the run's transcript; the header's `sawlang:` line names the
resolution step that answered, and a run at the pin reads `fetch`.

The acceptance oracle tradition: diff the console transcript, don't just read
"green".

The runner prints the totals. The flat profile excludes cases by name, with
the reason printed above the rows, because each one asserts a hardware denial
the tier disclaims; nothing is skipped silently. On that profile handles,
rights, quotas and lifetimes are checked identically on the syscall path, but
it disclaims protecting memory, the kernel's included, from a process. The
isolated profiles enforce both the syscall-path checks and memory protection.

`make sos-smoke-esp32c3` is non-gating board smoke against the machine-local
Espressif QEMU — never part of sos-test, never CI.

The runner is parallel by default: one job per performance core, detected.
`SOS_JOBS` overrides the detection and `-j N` overrides both; the count is
never printed. The report is printed in case order (definition order, or the
order `--case` names them) whatever the completion order, so a parallel
transcript reads the same as a serial one.
`-j 1` is the serial harness, kept reachable for exactly that comparison.

Three cases carry timing-dependent console output that moves run to run in
either mode and that no assertion reads: `thread_preempt`'s `SOS: timer tick`
lines and its `interrupts=` count; `timer_interval`'s `fires=` counters; and
`process_stats`' `interrupts=` count (the column is printed and never
asserted, because a tick lands where the host puts it).
**`thread_preempt`'s A/B interleave is not on that list; it is asserted**: the
case matches `AB`, `BA`, `AB` as ordered adjacent pairs, which is "the
processor crossed between the two threads at least three times, whoever went
first". It is matched against the letters-only projection of the console (the
kernel's own `SOS: ` lines stripped, since they land inside the workers' byte
stream) and with overlapping matches (three crossings can be consecutive) —
`strip_kernel_lines` and `overlapping_matches` on that case, both documented at
the site. None of the above appears in the transcript at all on a passing run:
the report prints case rows, image sizes and totals, so a console line only
reaches the screen when a case goes red. Anything moving in a transcript diff
is a real finding.

## The suite lock (machine-wide, sawos's own)
QEMU-suite invocations serialize through a mkdir lock at
`/private/tmp/claude-<uid>/sawos-suite-lock` (uid = `id -u`) — a different
path from sawlang's `saw-suite-lock`, so the two repos' gates never queue
on each other. Same protocol as sawlang's: acquire + gate + release in
foreground steps, never background the wait or the gate, clear only
verified-dead.

## Workflow
- The lead writes a brief for each unit as a sawtracker issue.
- Implementers are subagents: Sonnet by default, Opus for units whose judgment
  warrants it (spec and API contracts). At most 2 run at once, whatever the
  model (user ruling). An implementer never spawns subagents or forks, and
  never disables the sandbox.
- An implementer who finds a brief wrong or incomplete stops and returns a
  `CLARIFICATION REQUEST:` to the lead rather than guessing.
- The lead reviews the diff and uploads it (`sawtracker patch add`).
- The reviewer (`claude-air-reviewer`) reviews, approves and merges.

## Writing Saw here
Load the **saw-lang skill** before writing or reviewing any .saw file.
**Idiom (user ruling): the bind-or-bail shape on a Result is
an inline `let x = try f() catch { …; return }`** — the implicit
`error` is in scope, a diverging catch satisfies any type — never a
`match` whose Err arm just prints and exits. `match` stays for arms
doing genuinely different work and for negative tests asserting a
specific error value. Freestanding printing stays `{}` format args.
The statement-position form (`try f() catch {…}` on Result<Void, E>)
is legal — use the guard shape at every position. **The fold shape is
ruled in too (user ruling): a `match` whose Err arm just supplies a
fallback value (`case Err(_) -> v`) is `try f() catch { v }`** —
bind-or-default joins bind-or-bail. The `match` sites that stay are
real-work arms, negative tests, and success predicates
(`case Ok(_) -> true`, a shape try/catch cannot express since the catch
yields the payload's replacement, not a second value for the Ok path).
**Optional discipline (user ruling): an Optional in an API signature
is an antipattern unless the absence is a real domain value. A
transient condition ("not yet", "try again") rides the error channel
— error + retry — never `Ok(None)`/`Ok(false)`:** making every
common-path caller unwrap an Optional for a case that rarely happens
is the wrong trade. The saw-lang skill lives in a sawlang checkout at
`.claude/skills/saw-lang`; install it as a user-level symlink
(`~/.claude/skills/saw-lang -> <sawlang checkout>/.claude/skills/saw-lang`)
so there is one canonical copy and no drift, or copy the skill in.

## Comments and docs
Comments and docs say how the code works. No design, unit or issue
references, no history narration, and no volatile facts (test counts, sizes,
counts of sites or callers, timings, snapshots of current state): name the
constant, or say where the current value is printed. Explain why only when
it's interesting or prevents a real mistake. Prose wraps at 80 columns.

## Tracker and design records
Work is tracked in sawtracker, project `SO`, through `~/bin/sawtracker` with
`SAWTRACKER_TOKEN` set. The token lives in the git-ignored
`.claude/sawtracker.token`; never print it or commit it. A unit's brief is its
issue description, epics group units, and longer design documents are
sawtracker docs (`sawtracker doc`). sawlang's archive is still cited as
`sawlang#NNN`.

`.sawtracker/` is sawtracker's own store: issues, events, docs and the
project description (`project.md`). Read it freely, but never edit it by hand;
changes go through the `sawtracker` CLI. The exception is server configuration
such as `.sawtracker/tests.json`, which lands as an ordinary patch (user
ruling).

The legacy `designs/` records are in git history.
`git log --diff-filter=D --oneline -- designs` names the commit that deleted
them; read them at its parent (`git show <commit>^:designs/todo.md`, or
`git ls-tree -r --name-only <commit>^ -- designs` for the list).

## Conduct
Same doctrine as sawlang (its CLAUDE.md is the fuller statement): never
hide errors; APIs do the expected thing; no attribution trailers in
commits; changes land as sawtracker patches (see Workflow).
