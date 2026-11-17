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
python3 tools/sawos.py flash IMAGE        # build it for a part
```

`IMAGE` is a file in `images/` without its `.toml`. Outputs go in `build/`.
`run` hands the terminal to the emulator and exits with its status; `test`
prints each console and `test IMAGE: passed` or `failed`. `flash`, for a
board with a flash, writes `build/IMAGE.board/NAME.board.bin` and prints its
SHA-256, its board and the `esptool` command that writes it; it runs
nothing, so connecting and writing the part is yours.

`images/hello-riscv32.toml` launches sawos's UART driver and `hello`, its
client; `images/hello-esp32s3.toml`, on a board with no UART driver,
launches `greeter`, which prints through the kernel's console.

An image chooses its board's build keys in `[keys]`: on the ESP32-S3,

```toml
[keys]
clock = "xtal40"
```

builds its kernel to run the CPU at 40 MHz from the crystal. A board's keys
and their values are listed in its `board.toml` in the sawos tree
(`.sawos/hal/xtensa-esp32s3/board.toml`); a key an image leaves out keeps
its default, on the ESP32-S3 the clock the ROM left (`rom`) and the console
uncounted (`stall = "off"`).

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
