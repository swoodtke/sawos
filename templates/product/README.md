# A sawos product

A product is a repository of its own whose images run on sawos: its
processes' packages, its image descriptions in `images/`, and the inputs
those name. It builds against one sawos commit, the one `sawos.pin` names.

Make one from a sawos checkout:

```bash
python3 tools/product.py new ../my-product
```

That copies this directory and pins the checkout's commit.

## Building and booting

```bash
python3 tools/sawos.py build [IMAGE...]   # every image in images/ when none
python3 tools/sawos.py run IMAGE          # boot it under its board's emulator
python3 tools/sawos.py test [IMAGE...]    # boot each, pass when it exits 0
```

`IMAGE` is a file in `images/` without its `.toml`. Outputs go in `build/`.
`run` hands the terminal to the emulator and exits with its status; `test`
prints each console and `test IMAGE: passed` or `failed`.

`tools/sawos.py` needs Python 3.11 or later. It links `.sawos` to the sawos
tree and runs that tree's `tools/product.py`, which says what each command
does. The tree is the one `SAWOS_ROOT` names, or else the pinned commit,
fetched once into `~/.cache/sawos/<sha>/` (`SAWOS_CACHE` moves it). The
sawlang toolchain is the one the sawos tree pins; `SAWLANG_ROOT` overrides
it, as it does in sawos itself.

## Naming sawos

Every sawos path goes through `.sawos`:

- a package's dependencies, linker script and native files, in its
  `Saw.toml`: `sos = { path = "../.sawos/kernel/sysapi" }`;
- a sawos package in an image: `package = "sawos:drivers/svc-uart-ns16550"`.

Any other path in an image is from the product's root. To move to another
sawos commit, change `sha` in `sawos.pin`.
