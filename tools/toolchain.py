#!/usr/bin/env python3
"""The sawlang toolchain resolver — the ONE place a sawlang artifact is located.

WHY IT IS A FUNNEL (brief obligation 1). "Every place a consumer names a
sawlang artifact" is a position-quantified rule, and design 238 splits the SOS
tree out of the sawlang repository, so those positions become cross-repository
paths. A hand-kept list of constants in each consumer is how one of them
quietly keeps pointing at a tree that moved. This module is the chokepoint, and
its ENTRY POINTS are:

  * `tools/sos_runner.py` — the SOS QEMU harness (the five path constants it
    used to compute off a `REPO_ROOT` are calls into here).
  * the `Makefile` — `sos-test` drives sos_runner, so it inherits this.
  * CI — the workflow drives the same targets, and prefers to set
    `SAWLANG_ROOT` explicitly (below) so the resolution is visible in the log.

Nothing else may compute a sawlang path. What this module owns is exactly:
the compiler, the package manager, and the three Saw packages a consumer
compiles against — `imgformat`, `toml`, `semver`.

RESOLUTION ORDER (design 238 D-b, ruled Aug 19): explicit intent first, then
`$PATH`, then a pinned fetch, then a refusal naming all three.

  1. What the operator NAMED. `SAWC` and `BLADE` name one artifact each;
     `SAWLANG_ROOT` names a whole checkout. `SAWLANG_ROOT` sits in this group
     rather than below `$PATH` deliberately: below it, a developer standing in
     a sawlang checkout with any `sawc` installed globally would silently get
     the INSTALLED compiler instead of the one they are editing — which is
     also the only way to test an uncommitted compiler change against a
     consumer, so the split must not make it impossible.
  2. `sawc` and `blade` on `$PATH` — the developer fast path, which design 238
     unit 3 made reachable (`make install` in a sawlang checkout).
  3. Fetch `swoodtke/sawlang` at the SHA in the consumer's `sawlang.pin` and
     bootstrap it, cached by SHA.
  4. Otherwise refuse, naming all three.

THE IN-REPO DEFAULT. Step 1's root falls back to the repository this file
lives in, WHEN that repository looks like a sawlang checkout (it has
`sawc/sawc.py`). In sawlang that is always true, so sawlang resolves to itself
and nothing about `make sos-test` changes. Copied into a consumer repository
the same test simply fails, and resolution moves on to step 2 — so the file
travels verbatim and needs no per-repository edit.

TWO ASYMMETRIES, STATED RATHER THAN PAPERED OVER.

  * A `$PATH` toolchain supplies EXECUTABLES ONLY. `sawc` and `blade` are
    programs; `imgformat`, `toml` and `semver` are SOURCE DIRECTORIES inside a
    sawlang checkout, and no install puts those on a `$PATH`. So step 2 can
    answer `sawc()` and `blade()` and cannot answer `module_source()` — a
    consumer that compiles against `imgformat` needs a ROOT, from step 1 or
    step 3. The refusal for a source request says exactly that instead of
    failing later with a missing module.
  * A `$PATH` sawc is checked by VERSION, a fetched one by SHA. A semver
    string cannot separate two commits that share it, which for an unreleased
    compiler is the normal case, so the `$PATH` guarantee is the weaker of the
    two — accepted and documented, design 238 D-b2 option (b). See
    `sawc/version.py` for the policy that travels with it. A pin the found
    compiler disagrees with is a loud refusal naming both, never a silent
    build; a consumer with NO pin has stated no requirement, and gets a note
    on stderr rather than a failure.
"""

import os
import shutil
import subprocess
import sys

# The upstream this resolver fetches from. Public over HTTPS (user, Aug 19), so
# the fresh-machine promise needs no credentials.
UPSTREAM_URL = "https://github.com/swoodtke/sawlang.git"

# Where a fetched checkout is cached, keyed by SHA — a hit is the steady state,
# because the fetch is a clone plus a venv plus llvmlite, which is minutes.
# (Design 238 D-b3 wrote this as `~/.cache/sawos/toolchain/`; the repo-neutral
# spelling is used because this file is copied verbatim into the consumer at
# unit 5 and names no consumer anywhere else.)
CACHE_ENV = "SAW_TOOLCHAIN_CACHE"
DEFAULT_CACHE = os.path.join("~", ".cache", "saw-toolchain")

# The packages a consumer compiles against, and where they sit in a checkout.
# One table, so a package that moves — as `imgformat` just did, design 238
# unit 2 — is one line here rather than a search across two repositories.
MODULE_DIRS = {
    "imgformat": ("libs", "imgformat", "src"),
    "toml": ("libs", "toml", "src"),
    "semver": ("libs", "semver", "src"),
}

PIN_FILE = "sawlang.pin"


class ToolchainError(Exception):
    """No usable toolchain, with the message that says what to do about it.

    Every refusal in this module raises this, so a consumer prints one thing
    and the wording lives in one place — which is what design 238 unit 6's
    negative test reads.
    """


def _repo_root():
    """The repository this file lives in (`tools/` is always one level down)."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _looks_like_sawlang(path):
    """Is `path` a sawlang checkout? The compiler entry point is the marker."""
    return bool(path) and os.path.isfile(os.path.join(path, "sawc", "sawc.py"))


def _read_pin(consumer_root):
    """Parse `<consumer_root>/sawlang.pin` -> {"version": …, "sha": …} or None.

    Deliberately not TOML: the file has two keys and the resolver must work
    before any Saw package is built, so it may not depend on `libs/toml`.
    Unknown keys are ignored so the format can grow; a malformed LINE is a
    refusal, because a pin nobody can read is a pin nobody is checking.
    """
    path = os.path.join(consumer_root, PIN_FILE)
    if not os.path.isfile(path):
        return None
    pin = {}
    with open(path) as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            if "=" not in line:
                raise ToolchainError(
                    f"{path}:{lineno}: expected `key = value`, got `{line}`")
            key, _, value = line.partition("=")
            pin[key.strip()] = value.strip().strip('"')
    return pin


def _which(name, env):
    """`shutil.which` against the PASSED environment.

    Not the process's: `resolve()` takes `env` so the four steps are testable
    without mutating `os.environ`, and a `$PATH` lookup that ignored it would
    be the one step that could not be tested.
    """
    return shutil.which(name, path=env.get("PATH"))


def _probe_version(sawc_argv):
    """`sawc --version` -> the semver it printed, or None if it would not say.

    The output is `sawc <semver>`; the program name is part of it on purpose
    (design 238 unit 3), because this call's whole job is deciding whether the
    thing found on `$PATH` is a sawc at all.
    """
    try:
        r = subprocess.run(list(sawc_argv) + ["--version"],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    out = (r.stdout or r.stderr).strip()
    parts = out.split()
    if len(parts) != 2 or parts[0] != "sawc":
        return None
    return parts[1]


class Toolchain:
    """A resolved toolchain: how to run the compiler, and where the sources are.

    Build one with `resolve()`. Every field is decided at construction, so a
    consumer that got an object has a toolchain and a consumer that did not got
    a `ToolchainError` with the reason — there is no third state to check for.
    """

    def __init__(self, sawc_argv, source, root=None, blade_bin=None,
                 notes=()):
        self.sawc_argv = list(sawc_argv)
        # Which step answered, for a log line and for the tests.
        self.source = source
        # A sawlang checkout, when one was resolved. `None` under a
        # `$PATH`-only toolchain — see the source-directory asymmetry above.
        self.root = root
        self._blade_bin = blade_bin
        # Non-fatal things the operator should see once (an unpinned `$PATH`
        # compiler, say). The consumer prints them; the resolver does not, so
        # a library caller decides where they go.
        self.notes = list(notes)

    # -- the compiler ----------------------------------------------------

    def sawc(self):
        """The argv PREFIX that runs the compiler: `argv + [source, ...]`."""
        return list(self.sawc_argv)

    def sawc_env_value(self):
        """The `SAWC` value Blade reads — the same compiler, as one string."""
        return " ".join(self.sawc_argv)

    # -- the package manager ---------------------------------------------

    def blade_binary(self):
        """An already-built `blade`, or None meaning "build it from source".

        None is not a failure: a consumer holding a ROOT builds Blade itself
        (that is what `blade_package_dir()` and the module sources are for),
        which is also what keeps a sawlang checkout testing its OWN Blade
        rather than whatever is installed.
        """
        return self._blade_bin

    def blade_package_dir(self):
        """`<root>/blade` — Blade's own package, to build it from source."""
        return os.path.join(self._require_root("Blade's own sources"), "blade")

    # -- the Saw packages a consumer compiles against ---------------------

    def module_source(self, name):
        """The source directory for `imgformat` / `toml` / `semver`."""
        if name not in MODULE_DIRS:
            raise ToolchainError(
                f"`{name}` is not a sawlang package this resolver owns "
                f"(it owns: {', '.join(sorted(MODULE_DIRS))})")
        root = self._require_root(f"the `{name}` package's sources")
        return os.path.join(root, *MODULE_DIRS[name])

    def module_path_arg(self, name):
        """The `--module-path NAME=DIR` value for `name`."""
        return f"{name}={self.module_source(name)}"

    def _require_root(self, what):
        if self.root:
            return self.root
        raise ToolchainError(
            f"no sawlang checkout is available, and {what} live INSIDE one.\n"
            f"\n"
            f"The toolchain in use came from {self.source}, which supplies the\n"
            f"`sawc` and `blade` PROGRAMS and nothing else — no install puts a\n"
            f"package's source directory on a $PATH.\n"
            f"\n"
            f"Name a checkout with SAWLANG_ROOT=/path/to/sawlang, or add a\n"
            f"{PIN_FILE} so one can be fetched.")


def _resolve_root(env, consumer_root):
    """Step 1's checkout: `SAWLANG_ROOT`, else this repository if it is one."""
    named = env.get("SAWLANG_ROOT")
    if named:
        named = os.path.abspath(os.path.expanduser(named))
        if not _looks_like_sawlang(named):
            raise ToolchainError(
                f"SAWLANG_ROOT={named} is not a sawlang checkout "
                f"(no sawc/sawc.py under it)")
        return named
    # The in-repo default. False in a consumer repository, which is what lets
    # this file be copied there unedited.
    if _looks_like_sawlang(consumer_root):
        return consumer_root
    return None


def _sawc_from_root(root):
    """Run the checkout's compiler with the checkout's venv where there is one.

    The `bin/sawc` shim makes the same choice (design 238 unit 3); this does
    not go THROUGH the shim because a consumer wants the argv, not a process.
    """
    venv_py = os.path.join(root, ".venv", "bin", "python")
    py = venv_py if os.path.isfile(venv_py) else sys.executable
    return [py, os.path.join(root, "sawc", "sawc.py")]


def _check_version(sawc_argv, pin, where, notes):
    """Step 2's pin check: a mismatch refuses loudly, naming both."""
    found = _probe_version(sawc_argv)
    if pin is None or "version" not in pin:
        notes.append(
            f"note: using the unpinned `sawc` at {where}"
            + (f" (version {found})" if found else "")
            + f" — no {PIN_FILE} states which version is required.")
        return
    want = pin["version"]
    if found is None:
        raise ToolchainError(
            f"the `sawc` at {where} would not say what version it is, and\n"
            f"{PIN_FILE} requires {want}.\n"
            f"\n"
            f"`sawc --version` must print `sawc <semver>`. A compiler that\n"
            f"cannot name itself is not one this build will trust.")
    if found != want:
        raise ToolchainError(
            f"the `sawc` on your PATH is version {found}, and {PIN_FILE}\n"
            f"requires {want}.\n"
            f"\n"
            f"  found:    {where} ({found})\n"
            f"  required: {want}\n"
            f"\n"
            f"Install the required version, point SAWLANG_ROOT at a checkout\n"
            f"of it, or unset your PATH entry so the pinned commit is fetched\n"
            f"instead.")


def _cache_root(env):
    named = env.get(CACHE_ENV)
    if named:
        return os.path.abspath(os.path.expanduser(named))
    return os.path.expanduser(DEFAULT_CACHE)


def _fetch(sha, env, verbose=False):
    """Step 3: `swoodtke/sawlang` at `sha`, cached, with a venv beside it.

    Publishes by RENAME so a fetch that dies half way leaves nothing a later
    run can mistake for a complete checkout — the same reason `test_runner.py`
    publishes its result directory atomically (design 220).
    """
    dest = os.path.join(_cache_root(env), sha)
    if _looks_like_sawlang(dest):
        return dest

    for tool in ("git", "python3"):
        if not _which(tool, env):
            raise ToolchainError(
                f"fetching the pinned sawlang toolchain needs `{tool}`, and\n"
                f"there is none on your PATH.\n"
                f"\n"
                f"Install {tool}, or supply a toolchain yourself with\n"
                f"SAWLANG_ROOT / SAWC / a `sawc` on your PATH.")

    tmp = dest + ".partial"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(os.path.dirname(dest), exist_ok=True)

    def run(argv, cwd=None):
        r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
        if r.returncode != 0:
            shutil.rmtree(tmp, ignore_errors=True)
            raise ToolchainError(
                f"fetching the pinned sawlang toolchain failed:\n"
                f"  $ {' '.join(argv)}\n{r.stdout}{r.stderr}")
        return r

    if verbose:
        print(f"  fetching sawlang {sha[:12]} -> {dest}")
    # init + fetch rather than clone: a SHA is fetchable directly, and this
    # never downloads a branch history nobody asked for.
    os.makedirs(tmp)
    run(["git", "init", "--quiet", tmp])
    run(["git", "remote", "add", "origin", UPSTREAM_URL], cwd=tmp)
    run(["git", "fetch", "--quiet", "--depth", "1", "origin", sha], cwd=tmp)
    run(["git", "checkout", "--quiet", "FETCH_HEAD"], cwd=tmp)
    # D-b3: the fetch creates its OWN venv, which is what keeps the
    # fresh-machine promise. Nothing fancier — no wheel cache, no pin file for
    # llvmlite; this is a pre-1.0 bootstrap, not a package manager.
    run(["python3", "-m", "venv", os.path.join(tmp, ".venv")])
    run([os.path.join(tmp, ".venv", "bin", "pip"), "install", "--quiet",
         "llvmlite"])
    os.replace(tmp, dest)
    return dest


def _refusal(consumer_root, tried):
    """The step-4 message. It names all three steps AND why each did not fire.

    Design 238 unit 6 tests this text: a consumer with no toolchain must be
    told what to do, not handed a confusing failure from somewhere deeper.
    """
    return ToolchainError(
        "no sawlang toolchain found — `sawc` is needed to build this "
        "repository.\n"
        "\n"
        "Three ways to supply one, tried in this order:\n"
        "\n"
        "  1. NAME ONE.\n"
        "       SAWLANG_ROOT=/path/to/sawlang   a checkout to build against\n"
        "       SAWC=\"python /path/to/sawlang/sawc/sawc.py\"   the compiler\n"
        "       BLADE=/path/to/blade                          a built blade\n"
        f"     -> {tried['env']}\n"
        "\n"
        "  2. PUT `sawc` AND `blade` ON YOUR PATH.\n"
        "       `make install` in a sawlang checkout does this, into\n"
        "       ~/.local/bin.\n"
        f"     -> {tried['path']}\n"
        "\n"
        "  3. LET THIS REPOSITORY FETCH ONE.\n"
        f"       Needs a `{PIN_FILE}` naming the commit, plus `git` and\n"
        "       `python3`. The checkout is cached by SHA and reused.\n"
        f"     -> {tried['fetch']}\n"
        "\n"
        f"(looked for {PIN_FILE} in {consumer_root})")


def resolve(env=None, consumer_root=None, verbose=False):
    """Resolve a toolchain, or raise `ToolchainError` saying why not.

    `env` and `consumer_root` are parameters rather than globals so the four
    steps are testable without touching the process environment.
    """
    env = os.environ if env is None else env
    consumer_root = consumer_root or _repo_root()

    pin = _read_pin(consumer_root)
    notes = []
    tried = {}

    # --- step 1: what the operator named --------------------------------
    root = _resolve_root(env, consumer_root)
    named_sawc = env.get("SAWC")
    named_blade = env.get("BLADE")

    if named_sawc:
        # A string, because that is how Blade already takes it and how a CI
        # job already writes it (`SAWC="python .../sawc.py"`).
        sawc_argv = named_sawc.split()
    elif root:
        sawc_argv = _sawc_from_root(root)
    else:
        sawc_argv = None

    if sawc_argv:
        # `blade_bin` stays None unless BLADE named one: with a root in hand a
        # consumer builds Blade from ITS sources, so a checkout tests its own
        # package manager rather than whatever happens to be installed.
        named = []
        if named_sawc:
            named.append("SAWC")
        if named_blade:
            named.append("BLADE")
        if env.get("SAWLANG_ROOT"):
            named.append("SAWLANG_ROOT")
        elif root:
            named.append("this checkout")
        return Toolchain(sawc_argv, " + ".join(named), root=root,
                         blade_bin=named_blade, notes=notes)

    tried["env"] = "SAWLANG_ROOT, SAWC and BLADE are all unset"

    # --- step 2: $PATH ---------------------------------------------------
    path_sawc = _which("sawc", env)
    path_blade = _which("blade", env)
    if path_sawc:
        _check_version([path_sawc], pin, path_sawc, notes)
        return Toolchain([path_sawc], "PATH", root=None,
                         blade_bin=named_blade or path_blade, notes=notes)

    tried["path"] = "no `sawc` on your PATH"

    # --- step 3: the pinned fetch ---------------------------------------
    if pin and pin.get("sha"):
        fetched = _fetch(pin["sha"], env, verbose=verbose)
        return Toolchain(_sawc_from_root(fetched), "fetch", root=fetched,
                         blade_bin=named_blade, notes=notes)

    tried["fetch"] = (f"no {PIN_FILE} here" if pin is None
                      else f"{PIN_FILE} names no `sha`")

    # --- step 4: refuse, naming all three --------------------------------
    raise _refusal(consumer_root, tried)


def main(argv=None):
    """`python tools/toolchain.py` — report what would be resolved, and how."""
    argv = sys.argv[1:] if argv is None else argv
    try:
        tc = resolve(verbose=True)
    except ToolchainError as e:
        print(f"\033[1;31merror\033[0m: {e}", file=sys.stderr)
        return 1
    for note in tc.notes:
        print(note)
    print(f"resolved via : {tc.source}")
    print(f"sawc         : {tc.sawc_env_value()}")
    print(f"blade        : {tc.blade_binary() or '(build from source)'}")
    print(f"root         : {tc.root or '(none — executables only)'}")
    if tc.root:
        for name in sorted(MODULE_DIRS):
            print(f"  {name:<11}: {tc.module_source(name)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
