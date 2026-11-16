#!/usr/bin/env python3
"""Products: a repository of its own whose images build and boot against
this tree.

    python3 tools/product.py new DIR
        makes a product in DIR, a directory that must not exist yet: a copy
        of `templates/product/` without its Blade locks or build state
        (`copy_template`), and a `sawos.pin` naming this tree's commit,
        `git rev-parse HEAD`. A tree that is not a git checkout is refused,
        since there is no commit to pin.

    python3 tools/product.py COMMAND PRODUCT [IMAGE...]
        what a product's `tools/sawos.py` runs, through the product's
        `.sawos` link to this tree, for the product at PRODUCT. An IMAGE is
        the name of a description in PRODUCT/images/ without its `.toml`, and
        no IMAGE is every one there:

        build [IMAGE...]  builds each image with `sosimage build` into
                          PRODUCT/build/IMAGE/
        run IMAGE         builds the image and boots it under its board's
                          emulator with the terminal attached and no
                          deadline; exits with the emulator's status, which
                          is the kernel's
        test [IMAGE...]   builds each image, boots it with the harness's
                          deadline, prints its console, then
                          `test IMAGE: passed` when the kernel exited with 0
                          or `test IMAGE: failed (WHY)`; exits with 0 when
                          every image passed
        flash IMAGE       builds the image as a board image with
                          `sosimage flash` into PRODUCT/build/IMAGE.board/,
                          and prints its SHA-256, its board and the command
                          that writes it to a part, which it does not run;
                          a board with no flash is refused

Everything is reached through this file's own directory, so a product's
build names this tree only through its `.sawos` link. The sawlang toolchain
is resolved as the gate resolves it, by `tools/toolchain.py` from this
tree's `sawlang.pin`. Blade and the image builder are built as the gate
builds them (`sos_runner`), Blade into PRODUCT/build/host/ and the builder
in place in `tools/sosimage/`; `.sawlang` is written at this tree's top. A
board's profile and its native objects are written into
PRODUCT/build/<profile>/.

An image's board is read from its `[image] board`, and is built for when it
is one of the gate's profiles (`sos_runner.ARCHES`) or the ESP32-S3
(`sos_runner.ESP32S3_ARCH`, at its default build keys, with Espressif's
tools); any other is refused, naming the board.

A refusal prints `error: ` and its reason on standard error and exits 1; a
command line this file does not take exits 2.
"""

import os
import shutil
import subprocess
import sys
import tomllib

TOOLS = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TOOLS)
TEMPLATE = os.path.join(REPO_ROOT, "templates", "product")

COMMANDS = ("build", "run", "test", "flash")

# The commands that take exactly one image.
ONE_IMAGE = ("run", "flash")

USAGE = ("usage: python3 tools/product.py new DIR\n"
         "       python3 tools/product.py build|run|test|flash PRODUCT "
         "[IMAGE...]")

PIN_FILE = "sawos.pin"

# What a copy of the template leaves behind: Blade's locks, which hold the
# hash of a manifest as it was where it was built; build outputs; and the
# link a product's `tools/sawos.py` keeps.
LEFT_BEHIND = ("Saw.lock", ".build", "build", ".sawos", "__pycache__")


class Refusal(Exception):
    """A product command refused, with the reason."""


def head_commit():
    """This tree's commit. The search stops at the tree's own top, so a tree
    that is not a checkout is never answered by a repository around it."""
    env = {k: v for k, v in os.environ.items()
           if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    top = os.path.realpath(REPO_ROOT)
    env["GIT_CEILING_DIRECTORIES"] = os.path.dirname(top)
    try:
        r = subprocess.run(["git", "-C", REPO_ROOT, "rev-parse", "HEAD"],
                           capture_output=True, text=True, env=env)
    except OSError as e:
        raise Refusal(f"cannot read this tree's commit to pin: {e}")
    if r.returncode != 0:
        raise Refusal(f"cannot read this tree's commit to pin: {REPO_ROOT} "
                      f"is not a git checkout (git rev-parse HEAD: "
                      f"{(r.stdout + r.stderr).strip()})")
    return r.stdout.strip()


def copy_template(source, dest):
    """Copy the product template `source` to `dest`, leaving behind what
    `LEFT_BEHIND` names wherever it is."""
    shutil.copytree(source, dest, symlinks=True,
                    ignore=shutil.ignore_patterns(*LEFT_BEHIND))


def new(dest):
    dest = os.path.abspath(dest)
    if os.path.lexists(dest):
        raise Refusal(f"{dest} exists; a product is made in a new directory")
    sha = head_commit()
    copy_template(TEMPLATE, dest)
    with open(os.path.join(dest, PIN_FILE), "w") as f:
        f.write("# The sawos commit this product builds against: "
                "tools/sawos.py fetches it,\n"
                "# unless SAWOS_ROOT names a tree.\n"
                f"sha = \"{sha}\"\n")
    print(f"made the product {dest}, pinned to sawos {sha}")


def read_toml(path):
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise Refusal(f"cannot read {path}: {e}")


def image_files(product, names):
    """The description of each image `names` names, or of every image in
    PRODUCT/images/ when it names none, as (name, path)."""
    folder = os.path.join(product, "images")
    found = []
    if os.path.isdir(folder):
        found = sorted(f[:-len(".toml")] for f in os.listdir(folder)
                       if f.endswith(".toml"))
    if not names:
        if not found:
            raise Refusal(f"there is no image description in {folder}")
        names = found
    for name in names:
        if name not in found:
            raise Refusal(f"there is no image `{name}` in {folder}; its "
                          f"images are {', '.join(found) or 'none'}")
    return [(name, os.path.join(folder, f"{name}.toml")) for name in names]


def board_profiles(runner):
    """Each board this tree describes, by name: the profile it is built
    with, an `ARCHES` entry, or None for a board no product builds for."""
    hal = os.path.join(REPO_ROOT, "hal")
    profiles = {}
    for entry in sorted(os.listdir(hal)):
        path = os.path.join(hal, entry, "board.toml")
        if not os.path.isfile(path):
            continue
        name = read_toml(path).get("board", {}).get("name")
        profiles[name] = next((a for a in [*runner.ARCHES, runner.ESP32S3_ARCH]
                               if a["name"] == entry), None)
    return profiles


def image_profile(path, profiles):
    """The image at `path`'s name, and the profile its board is built with."""
    image = read_toml(path).get("image", {})
    name, board = image.get("name"), image.get("board")
    if not isinstance(name, str) or not isinstance(board, str):
        raise Refusal(f"{path} names no image and board in `[image]`")
    if board not in profiles:
        raise Refusal(f"{path}: no board.toml under {REPO_ROOT}/hal names "
                      f"the board {board}; the boards are "
                      f"{', '.join(sorted(profiles))}")
    arch = profiles[board]
    if arch is None:
        buildable = sorted(b for b, a in profiles.items() if a is not None)
        raise Refusal(f"{path}: a product cannot build for the board "
                      f"{board} yet; it builds for {', '.join(buildable)}")
    return name, arch


def verdict(name, status, timed_out, deadline):
    """Whether a booted image passed, and the line that says so."""
    if timed_out:
        return False, f"test {name}: failed (still running after {deadline} s)"
    if status != 0:
        return False, (f"test {name}: failed (the kernel's exit status is "
                       f"{status})")
    return True, f"test {name}: passed"


class Session:
    """The tools one command builds with: the toolchain, Blade, the image
    builder, and each profile's description, made once."""

    def __init__(self, runner, product, arches):
        self.runner = runner
        self.product = product
        self.build = os.path.join(product, "build")
        os.makedirs(self.build, exist_ok=True)
        # Each profile's (qemu, clang, lld).
        self.tools = {}
        virt = [a for a in arches if a is not runner.ESP32S3_ARCH]
        if virt:
            qemus, lld, clang = runner._probe_tools(virt, scratch=self.build)
            for arch in virt:
                self.tools[arch["name"]] = (qemus[arch["name"]], clang, lld)
        if len(virt) < len(arches):
            self.tools[runner.ESP32S3_ARCH["name"]] = runner._esp32s3_tools()
        tc = runner.tc()
        print(f"sawlang: {tc.source}  {tc.root or tc.sawc_env_value()}")
        print("building blade and the image builder")
        sys.stdout.flush()
        host = os.path.join(self.build, "host")
        os.makedirs(host, exist_ok=True)
        self.blade = runner._build_blade(host)
        runner._link_sawlang()
        self.builder = runner._build_sosimage(self.blade)
        self.profiles = {}

    def s3(self, arch):
        return arch is self.runner.ESP32S3_ARCH

    def profile(self, arch):
        if arch["name"] not in self.profiles:
            _qemu, clang, lld = self.tools[arch["name"]]
            build = os.path.join(self.build, arch["name"])
            if self.s3(arch):
                os.makedirs(build, exist_ok=True)
                path = self.runner._esp32s3_profile(clang, lld, self.blade,
                                                    ("rom", False, False), build)
            else:
                path = self.runner._gate_profile(arch, clang, lld, self.blade,
                                                 build=build)
            self.runner._run_sosimage(self.builder, ["native", path])
            self.profiles[arch["name"]] = path
        return self.profiles[arch["name"]]

    def image(self, command, file, path, name, arch):
        """Build the image for `command`; answer what boots it: its ELF, or
        on a board with a flash its flash image."""
        _qemu, clang, lld = self.tools[arch["name"]]
        verb, out = "build", os.path.join(self.build, file)
        if command == "flash":
            verb, out = "flash", os.path.join(self.build, f"{file}.board")
        for_part = " for a part" if command == "flash" else ""
        print(f"building {file}{for_part}")
        printed = self.runner._run_sosimage(
            self.builder, [verb, self.profile(arch), REPO_ROOT, self.product,
                           path, out],
            env=self.runner._blade_env(clang, lld if self.s3(arch) else None))
        sys.stdout.write(printed)
        if self.s3(arch):
            return os.path.join(out, f"{name}.flash.bin")
        return os.path.join(out, f"{name}.elf")

    def boot_command(self, arch, image):
        qemu = self.tools[arch["name"]][0]
        if self.s3(arch):
            return self.runner._esp32s3_qemu_command(qemu, image)
        return [qemu, *arch["qemu_args"], "-nographic", "-kernel", image]

    def boot(self, arch, image):
        """Boot `image` with the harness's deadline; answer (status, console,
        whether it was killed at the deadline, the deadline)."""
        qemu = self.tools[arch["name"]][0]
        if self.s3(arch):
            deadline = self.runner.ESP32S3_TIMEOUT_S
            out, status = self.runner._boot_esp32s3(qemu, image, deadline)
            return status, out, status is None, deadline
        status, out, timed_out = self.runner._run_qemu(qemu, arch, image)
        return status, out, timed_out, self.runner.QEMU_TIMEOUT_S


def product_command(command, product, names):
    sys.path.insert(0, TOOLS)
    import sos_runner as runner

    product = os.path.abspath(product)
    if command in ONE_IMAGE and len(names) != 1:
        raise Refusal(f"{command} takes one image")
    profiles = board_profiles(runner)
    images = []
    for file, path in image_files(product, names):
        name, arch = image_profile(path, profiles)
        images.append((file, path, name, arch))
    arches = []
    for _f, _p, _n, arch in images:
        if arch not in arches:
            arches.append(arch)

    try:
        session = Session(runner, product, arches)
        booted = [session.image(command, *image) for image in images]
    except runner.ToolError as e:
        raise Refusal(str(e))
    sys.stdout.flush()
    if command in ("build", "flash"):
        return 0
    if command == "run":
        _file, _path, _name, arch = images[0]
        return subprocess.call(session.boot_command(arch, booted[0]))
    passed = True
    for (file, _path, _name, arch), image in zip(images, booted):
        status, out, timed_out, deadline = session.boot(arch, image)
        if isinstance(out, bytes):
            out = out.decode(errors="replace")
        sys.stdout.write(out if out.endswith("\n") or not out else out + "\n")
        ok, line = verdict(file, status, timed_out, deadline)
        print(line)
        passed = passed and ok
    return 0 if passed else 1


def main(argv):
    if not argv:
        print(USAGE, file=sys.stderr)
        return 2
    command = argv[0]
    try:
        if command == "new":
            if len(argv) != 2:
                print(USAGE, file=sys.stderr)
                return 2
            new(argv[1])
            return 0
        if command not in COMMANDS:
            print(f"error: unknown command `{command}`; the commands are "
                  f"{', '.join(COMMANDS[:-1])} and {COMMANDS[-1]}",
                  file=sys.stderr)
            return 2
        if len(argv) < 2:
            print(USAGE, file=sys.stderr)
            return 2
        return product_command(command, argv[1], argv[2:])
    except Refusal as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
