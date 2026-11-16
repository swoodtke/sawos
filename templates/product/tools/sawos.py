#!/usr/bin/env python3
"""A product's way to sawos: builds, boots and tests the product's images.

    python3 tools/sawos.py COMMAND [IMAGE...]

It finds the sawos tree, links it as `.sawos` at the product's root, and
runs that tree's `tools/product.py` through the link, which states the
commands and does the work: resolving the sawlang toolchain from the tree's
own `sawlang.pin`, building Blade and the image builder, and building and
booting each image. Every sawos path a product names (its packages'
dependencies and scripts, an image's `sawos:` packages) is through
`.sawos`, so the link is the one place the tree is chosen.

The tree is, in order:

  1. `SAWOS_ROOT`, a sawos tree named in the environment;
  2. the commit `sawos.pin` at the product's root names, fetched from
     `UPSTREAM_URL` into `~/.cache/sawos/<sha>/` (`SAWOS_CACHE` moves the
     cache) and reused from there.

`sawos.pin` is lines of `key = "value"` with `#` comments, and its one key is
`sha`, the commit's 40 hexadecimal digits. Anything else in it is refused.

This file is a copy of sawos's `templates/product/tools/sawos.py`. It knows
no command, so a sawos with new ones needs no new copy.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

UPSTREAM_URL = "https://github.com/swoodtke/sawos.git"

PIN_FILE = "sawos.pin"
PIN_KEYS = ("sha",)
CACHE_ENV = "SAWOS_CACHE"
DEFAULT_CACHE = os.path.join("~", ".cache", "sawos")
LINK = ".sawos"

# What makes a directory a sawos tree: the program this file hands over to.
ENTRY = os.path.join("tools", "product.py")

USAGE = "usage: python3 tools/sawos.py COMMAND [IMAGE...]"


class Refusal(Exception):
    """Why this product cannot reach a sawos tree."""


def is_sawos(path):
    return os.path.isfile(os.path.join(path, ENTRY))


def read_pin(product):
    """The pinned sha in `<product>/sawos.pin`, or None with no pin."""
    path = os.path.join(product, PIN_FILE)
    if not os.path.isfile(path):
        return None
    pin = {}
    with open(path) as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            key, eq, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if not eq or len(value) < 2 or value[0] != '"' or value[-1] != '"':
                raise Refusal(f"{path}:{lineno}: expected `key = \"value\"`, "
                              f"got `{line}`")
            if key not in PIN_KEYS:
                raise Refusal(f"{path}:{lineno}: unknown key `{key}`; the "
                              f"one key is `sha`")
            pin[key] = value[1:-1]
    sha = pin.get("sha")
    if sha is None:
        raise Refusal(f"{path} names no `sha`")
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise Refusal(f"{path}: `sha` is \"{sha}\", not a commit's 40 "
                      "lowercase hexadecimal digits")
    return sha


def partial(cache, sha):
    """A directory of its own in `cache` for one fetch of `sha` to check out
    into, so two fetches of one pin at once never share one."""
    os.makedirs(cache, exist_ok=True)
    return tempfile.mkdtemp(prefix=f"{sha}.", suffix=".partial", dir=cache)


def publish(tmp, dest):
    """Rename the finished checkout `tmp` to `dest`; answer `dest`. When
    another fetch put a tree there first, that one is taken and `tmp` is
    removed."""
    try:
        os.rename(tmp, dest)
    except OSError:
        if not is_sawos(dest):
            shutil.rmtree(tmp, ignore_errors=True)
            raise Refusal(f"{dest} is in the sawos cache and is not a sawos "
                          f"tree (no {ENTRY}); remove it to fetch again")
        shutil.rmtree(tmp, ignore_errors=True)
    return dest


def fetch(sha, env):
    """The sawos tree at `sha`, from the cache or fetched into it.

    A fetch is published by renaming a finished checkout into place
    (`partial`, `publish`), so one that dies half way leaves nothing a later
    run takes for a tree.
    """
    named = env.get(CACHE_ENV)
    cache = os.path.abspath(os.path.expanduser(named or DEFAULT_CACHE))
    dest = os.path.join(cache, sha)
    if os.path.exists(dest):
        if not is_sawos(dest):
            raise Refusal(f"{dest} is in the sawos cache and is not a sawos "
                          f"tree (no {ENTRY}); remove it to fetch again")
        return dest
    if not shutil.which("git", path=env.get("PATH")):
        raise Refusal(f"fetching sawos {sha} needs `git`, and there is none "
                      "on your PATH; install it, or name a sawos tree with "
                      "SAWOS_ROOT")
    tmp = partial(cache, sha)

    def git(*args):
        r = subprocess.run(["git", *args], cwd=tmp, capture_output=True,
                           text=True)
        if r.returncode != 0:
            shutil.rmtree(tmp, ignore_errors=True)
            raise Refusal(f"fetching sawos {sha} failed:\n  $ git "
                          f"{' '.join(args)}\n{r.stdout}{r.stderr}")

    print(f"fetching sawos {sha} into {dest}", file=sys.stderr)
    git("init", "--quiet")
    git("remote", "add", "origin", UPSTREAM_URL)
    git("fetch", "--quiet", "--depth", "1", "origin", sha)
    git("checkout", "--quiet", "FETCH_HEAD")
    if not is_sawos(tmp):
        shutil.rmtree(tmp, ignore_errors=True)
        raise Refusal(f"the sawos commit {sha} has no {ENTRY}, so no product "
                      "can build against it; pin a later one")
    return publish(tmp, dest)


def sawos_tree(product, env):
    """The sawos tree this product builds against."""
    named = env.get("SAWOS_ROOT")
    if named:
        tree = os.path.abspath(os.path.expanduser(named))
        if not is_sawos(tree):
            raise Refusal(f"SAWOS_ROOT={tree} is not a sawos tree (no "
                          f"{ENTRY} under it)")
        return tree
    sha = read_pin(product)
    if sha is None:
        raise Refusal(f"no sawos tree: SAWOS_ROOT is unset, and there is no "
                      f"{PIN_FILE} in {product}. Name a tree with "
                      f"SAWOS_ROOT=/path/to/sawos, or pin a commit in "
                      f"{PIN_FILE} (`sha = \"<40 hex digits>\"`)")
    return fetch(sha, env)


def link(product, tree):
    """Point `<product>/.sawos` at `tree`; answer the link's path.

    A link naming another tree is replaced: the product builds against the
    tree this run resolved and no other.
    """
    path = os.path.join(product, LINK)
    if os.path.islink(path):
        if os.readlink(path) == tree:
            return path
        os.remove(path)
    elif os.path.exists(path):
        raise Refusal(f"{path} exists and is not a link; it is this tool's, "
                      "to point at the sawos tree")
    os.symlink(tree, path)
    return path


def main(argv):
    if not argv:
        print(USAGE, file=sys.stderr)
        return 2
    product = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        sawos = link(product, sawos_tree(product, os.environ))
    except Refusal as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    entry = os.path.join(sawos, ENTRY)
    os.execv(sys.executable, [sys.executable, entry, argv[0], product,
                              *argv[1:]])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
