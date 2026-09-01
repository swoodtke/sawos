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
hal/riscv32/       # per-arch: kernel/ (boot.S, trap entry, board sinks, PMP,
hal/arm64/         # virt.ld) + user/ (the ecall stub + root.ld), ABI.md each
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
                     # them, boots both arches under QEMU. THE gate.
```
Every change gates on `make sos-test` before commit. The acceptance oracle
tradition: diff the console transcript, don't just read "green"
(sawlang#238 unit 0).

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
joins bind-or-bail. The 75 statement-position + 25 fold sites kept
during the ICE era are a queued conversion pass. On
this machine it is a user-level symlink
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
