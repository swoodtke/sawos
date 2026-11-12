# Implementer rules

Every implementing agent follows these. A unit's issue carries its own
"Implementation notes"; where the two disagree, the issue wins.

## Conduct

- **If the notes are wrong, contradict the tree, or leave a decision you
  can't make from them, stop.** End your turn with a message that begins
  `CLARIFICATION REQUEST:` and states the exact question. Don't guess and
  don't work around it.
- **Never disable the sandbox.** If a command fails on a sandbox permission,
  stop with a `CLARIFICATION REQUEST:` and the exact error.
- **Do the work yourself:** no subagents and no forks.
- **Don't write to sawtracker** (issues, patches, chat). The lead uploads.
- Read `CLAUDE.md` first. Load the saw-lang skill before writing or reviewing
  any `.saw` file. `spec.md` is authoritative for kernel behaviour.

## The tree

- Work only in the worktree you were started in. Before the first edit and
  again before the final commit, fetch main over HTTPS and rebase onto it:
  `GIT_TERMINAL_PROMPT=0 git fetch -q https://github.com/swoodtke/sawos.git main`,
  then `git rebase FETCH_HEAD`. `git merge-base HEAD FETCH_HEAD` must equal
  `FETCH_HEAD`.
- Never edit `.sawtracker/` by hand.
- macOS: `sed -i ''`, and there is no `timeout` command.

## Comments, docs and spec text

- They say how the code works and what must stay true. No design, unit or
  issue references, no history ("used to", "now", dates), and no volatile
  facts (counts of tests, sites or callers, sizes, timings).
- Explain why only where it prevents a real mistake.
- Prose wraps at 80 columns, including every line you touch.
- When behaviour changes, change every place that describes it: the doc
  comment, the spec section, §11 if it lists the limit, and the README.

## Saw idioms the reviewer checks

- Bind-or-bail on a Result is `let x = try f() catch { …; return }`, never a
  `match` whose Err arm only prints and exits.
- A fallback value is `try f() catch { v }`.
- No Optional in an API signature unless absence is a real domain value. A
  transient condition rides the error channel.

## Tests

- Run targeted cases while you work, on every profile the change touches:
  `SAWLANG_ROOT=<path given at dispatch> python3 tools/sos_runner.py --case a,b`
  and `--arch NAME` for one profile. `--board esp32c3` is the C3 smoke.
- A new case asserts console lines, as its neighbours in `tools/sos_runner.py`
  do. Write its header comment to say what the case proves.
- **Every new check needs a mutation check:** remove or invert the code under
  test, show the case fails, restore it, and report the mutant and the failing
  line.
- Never weaken an existing assertion to make a case pass. If a transcript row
  must move, name the row and the reason in your report.
- The flat profile excludes cases by name with a printed reason. Exclude one
  only if what it asserts is a hardware denial.

## The suite lock

QEMU suite runs are serialized machine-wide through a directory lock at
`/private/tmp/claude-<uid>/sawos-suite-lock` (`id -u` gives the uid).

- Take it, run and release it as one command:
  `until mkdir L 2>/dev/null; do sleep 30; done; <run>; rmdir L`.
- The wait can take tens of minutes. That is normal.
- Run that command in the foreground if your harness allows it. If it
  refuses a foreground `sleep`, run the whole command as one tracked
  background task and wait for its completion notice before you do anything
  else. Never detach it with `&`, never start a second one while the first
  runs, and never end your turn while one is running.
- Never remove a lock your own `mkdir` did not create. Hold it only for the
  run.
- Before you finish, make sure no QEMU or runner of yours is still running,
  and say in your report how you ran the suite.

## Deliverable

- One commit on top of current main, with the message the notes give. No
  attribution trailer. No push.
- A final report: the worktree path and commit; what changed and where; each
  run with its totals; each mutation check; every transcript row that moved;
  anything in the notes you found wrong; and what you did not verify.
