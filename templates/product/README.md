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
launches sawos's console driver, which writes to the kernel's console, and
`greeter`. Each image names its driver's serve as its console, so both
processes `print` through it.

An image chooses its board's build keys in `[keys]`: on the ESP32-S3,

```toml
[keys]
clock = "xtal40"
```

builds its kernel to run the CPU at 40 MHz from the crystal, and

```toml
[keys]
clock = "pll240"
flash = "dio80"
```

at 240 MHz with the cache reading the flash on two lines at 80 MHz; a
flash key needs a clock key on the PLL, and the build refuses one without.
A board's keys and their values are listed in its `board.toml` in the sawos
tree (`.sawos/hal/xtensa-esp32s3/board.toml`); a key an image leaves out
keeps its default, on the ESP32-S3 the clock the ROM left (`rom`), the
flash read as the ROM left it (`flash = "rom"`) and the console uncounted
(`stall = "off"`).

`tools/sawos.py` needs Python 3.11 or later. It links `.sawos` to the sawos
tree and runs that tree's `tools/product.py`, which says what each command
does. The tree is the one `SAWOS_ROOT` names, or else the pinned commit,
fetched once into `~/.cache/sawos/<sha>/` (`SAWOS_CACHE` moves it). The
sawlang toolchain is the one the sawos tree pins; `SAWLANG_ROOT` overrides
it, as it does in sawos itself.

## Writing a process

A process is written with an ordinary `main`, as `hello/src/main.saw` and
`greeter/src/main.saw` are:

```saw
import sos.{SosStatus}
import sosmain

/// A record's tag, as `process.toml` declares it.
static TAG_UART: UInt = 2

func main() -> Result<Void, SosStatus> {
    var set = sosmain.take_boot_set()
    var record = set.take(TAG_UART)!
    borrow let proc = sosmain.context().process() {
        let waiter = try proc.waiter_create()
        ...
    }
    print("served {} requests", n)
    return
}
```

Its `Saw.toml` depends on `sosmain`
(`sosmain = { path = "../.sawos/rt/main" }`) beside `sos`, and its source
imports it: that package's start-up runs `main`. `main` returns nothing, an
`Int` from 0 to 255, or a `Result` of either, and the process ends with
that status; an `Err` is written as `error: ...` and ends it with 1, which
needs a heap, since writing the error allocates (a process with no heap that
returns an `Err` panics instead). A panic ends it as `Faulted`, with its text
on the kernel's line.

`sosmain.take_boot_set()` hands `main` the records its `process.toml`
declares, once, to take by tag. `sosmain.context()` lends the process's
System and Process handles, which the runtime keeps for the process's life:
`main` uses them, and mints siblings of them, but cannot drop them or give
them away. `sosmain.exit(code)` ends the process early.

`print` writes a line to the image's console. An image names one in
`[image]`, as a serve record of a `uartproto` driver, in the grammar
`clients` uses:

```toml
[image]
console = "uart.uart"           # PROCESS.RECORD
```

The builder then gives every other process a client record of that connection,
which the runtime takes before `main` runs, so `main` cannot take it. A process
that also wants to speak to that driver itself, to read from it, say, declares
a client of its own and takes it from its boot set, as `hello` does. In an
image with no console, `print` writes through the kernel's debug print when the
process's System handle holds `debug`, and writes nothing when it does not.

A process's System handle keeps the rights its `process.toml` names in its
`system` record's `rights`. One that names none keeps its image's default,
which an image whose root is the stock root may state in `[system]`:

```toml
[system]
rights = ["process-self", "clock-get", "transfer"]
```

With no `[system]`, the default is all four rights a process may hold
(`debug`, `process-self`, `clock-get`, `transfer`), or in an image that
names a console the three without `debug`, since its processes print through
the console. `hello` and `greeter` name no rights, and have no `debug` in
their images. A process that writes through the kernel's debug print itself,
as a console's driver does, names `debug` in its own `rights`.

## Naming sawos

Every sawos path goes through `.sawos`:

- a package's dependencies, linker script and native files, in its
  `Saw.toml`: `sos = { path = "../.sawos/kernel/sysapi" }`;
- a sawos package in an image: `package = "sawos:drivers/svc-uart-ns16550"`.

Any other path in an image is from the product's root. To move to another
sawos commit, change `sha` in `sawos.pin`.
