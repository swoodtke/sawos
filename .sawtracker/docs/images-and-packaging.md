# SO-95: images and packaging (design)

For the owner and the reviewer, before anything is built. Written against
main at `e7b9a20c`, with `tests/svc-uart-ns16550`, `tests/svc-uart-pl011`
and `tests/uartproto` read as if SO-96 had moved them to `drivers/`.

This revision carries two rulings of the owner's from Nov 13: tools are
written in Saw wherever possible, with Saw gaps filed with the sawlang lead;
and an image's limits are a table linked into a kernel compiled once per
board. Both were tried in scratch before being written down.

Every claim about the code names the file and function it comes from.
Section 16 says which claims I checked by running something, which I only
read, and which I could not check.

---

## 0. Verdict

1. The seven-part shape holds. A board lives in sawos (`board.toml` beside
   its HAL). A product repository holds image descriptions, its own
   packages and a `sawos.pin`. A process states its boot interface in
   `process.toml`, and the builder refuses an image that does not supply
   it.
2. **The builder is a Saw program**, `tools/sosimage/`, a hosted Blade
   package in sawos. The runner builds it once per run, right after it
   builds Blade, and calls it per case. Its first unit takes over the
   runner's stitching with every transcript unchanged.
3. **The kernel is compiled once per board, and an image's limits are a
   table linked in beside the boot region table.** At boot the kernel grows
   each slab by one extent from a reserve the builder assigns, through the
   code `SlabDonate` already runs. I built this into a scratch copy of the
   kernel. On riscv32 and arm64, `process_donate`'s root found a fourth
   process before any donation. The kernel exports each slab's slot size
   and alignment, and the board's facts, so the builder sizes the reserve
   and refuses an image that does not fit.
4. Per-board limits stay per board. These are the handle table, the boot
   set, the grant record and the HAL's domain pool. The box needs none of
   them raised.
5. The kernel changes in three places: the boot table (version 4, with a
   limits block), the export of board and slab facts, and
   `ProcessOp.QuotaSet`. With `QuotaSet` a launcher sets a `Created`
   child's quota rows, never above its own.
6. The stock root is table-driven from a Saw module the builder generates.
   Nothing is parsed on the target, and a product may bring its own root.
7. **What stays Python, and why** (section 10):
   - The toolchain resolver and a product's sawos resolver run before any
     Saw compiler exists, and building that compiler needs Python anyway.
   - The emulator harness waits on `std.process`.
   - The runner's case table stays as it is until the harness has moved
     and the TOML library is complete.
8. **Saw gaps found by running, eleven in all, listed in section 11.**
   - Blocking: `libs/toml` cannot read the description formats; a child
     process can be given no working directory, no input, no deadline and
     no kill; and Blade has no flags to place or name what it builds.
   - Workarounds exist and are named for: a slot's size cannot be exported
     at compile time; a toolchain library has no portable dependency
     spelling; hex formatting.
9. SO-77's U5 is replaced by this design's units, and SO-78 becomes the
   first product repository.

### 0.1 Where the rulings, the spec and the code disagree

None of the owner's rulings contradicts another. These are the places where
what the owner asked for meets text or code that says otherwise.

- **The spec calls the hand-kept linker scripts deliberate.** §5.5 says
  riscv32-virt "keeps one linker script per resident child image ...
  deliberately", and §5b.1's table calls them "the living identity-tier
  demonstration". There is still one script per resident image, but the
  builder writes it. Both sentences change.
- **§2.9 says "there is no per-create quota argument in v1",** and §11
  repeats it as a known limit. Rule 7.2 proposes the door and its text.
- **§2.8 says donation makes "the compiled limits ... a floor and capacity
  ... policy, decided by the deployment rather than by the build".** With a
  linked table the build decides the boot capacity and the deployment can
  still grow it. The sentence changes.
- **SO-77's design has root write the LEDC clock gate and route the servo
  pads** (`.claude/lead/so77-design.md` §3.3). A generic stock root cannot.
  Rule 7.4 and question 3 give the PWM driver its four windows instead.
- **A product depending on sawos packages through Blade's git dependencies
  does not work.** Blade ignores a `subdir` key and clones the repository's
  root, and it resolves only tagged versions (gap 11.8, reproduced).
  Products use path dependencies through a link (rule 9.7).
- **A driver states its needs, while "the launcher grants; an image never
  claims".** These are reconciled, not contradictory. The declaration is a
  requirement the builder checks; only the launcher's `QuotaSet` grants.
- **Stale comment.** The header of `kernel/core/limits.saw` says a derived
  static cannot take its operands from another module. With the pinned
  compiler that now compiles (§16.1). It matters less now that the limits
  are linked rather than compiled.

---

## 1. Today

### 1.1 How an image comes to be

The test runner, `tools/sos_runner.py`, is the only thing that builds a
system image.

- **A case is the image's description.** Each entry of `TEST_CASES` names a
  kernel source (`src`, usually `kernel/main.saw`), a root package
  (`root_pkg`), child packages (`children`) or one hand-assembled child
  (`child_asm`), and the flags `pool`, `device` and `tick`. `arches` and
  `tier` say where it runs.
- **Packages are built by Blade.** `_build_root_image` runs
  `blade build --force --target <triple>` in the package directory, with
  `SAWC`, `SOS_CLANG` and `SOS_LD` from `_blade_env`. The image lands at
  `<package>/.build/<triple>/<name>.sosimg`. `_build_blade` builds Blade
  from the sawlang checkout when no binary is supplied.
- **The kernel is compiled per case.** `_build_elf` compiles `case["src"]`
  with sawc directly: `--freestanding --no-hidden-alloc --runtime-provider`
  and `--module-path` for `kcore`, `hal=hal/<board>/kernel`, `imgformat`
  (from the sawlang checkout), `sosrt` and `sosabi`, plus the board's extra
  modules. It links with the board's `virt.ld`. Every process case uses the
  same `kernel/main.saw`, so the object differs only by board.
- **Root is stitched in.** `_stitch_root_image` assembles
  `kernel/rootimg.S`, whose `.incbin "root.sosimg"` points at a per-case
  staging directory, into `.payload`.
- **Children and the region table are generated.** `_stitch_regions` writes
  `<case>.regions.S`: each child sosimg `.incbin`'d into `.childimg`, then
  the `.regions` section from `_region_table_section` (magic, version 3,
  row count, boot flags, rows).
- **Row order is the runner's contract.** `_region_rows` says so: each
  child's blob, each child's destination, the pool, the device window. It
  refuses two children and a pool together, because the second child's
  destination and the pool share a base.
- **The ESP32-S3 has its own path.** `_esp32s3_smoke` compiles its kernel
  itself, links with `hal/xtensa-esp32s3/kernel/esp32s3.ld`, and
  `flash_image` writes a 4 MiB flash image, checks the direct-boot magic
  and runs `check_in_place`. `--emit-flash CASE --out PATH` cuts the image
  after its last byte and prints its SHA-256 and an `esptool` line.

### 1.2 Where each fact lives

Each fact, where it is stated, and where it is stated again:

- **A child's RAM base, the pool, the device window, the selftest line,
  root's entry:** `ARCHES` and `ESP32S3_ARCH` in the runner; again in the
  HAL's `lib.saw` and the user scripts.
- **A child's region length:** `CHILD_REGION_LEN`, or the board's
  `child_region_len`; again as each script's `LENGTH`.
- **A child's link address:** `hal/riscv32/user/child.ld`, `child2.ld` and
  `child3.ld` (`0x8024_0000`, `0x8028_0000`, `0x802C_0000`) and
  `hal/xtensa-esp32s3/user/child.ld`; again in `ARCHES`.
- **Which script a package links with:** its `Saw.toml`'s `[sos.<triple>]
  linker-script`; again as the package's position in a case's `children`.
- **The S3's flash slots:** `esp32s3.ld` (`ROOT_IMAGE_SLOT`,
  `CHILD_IMAGE_SLOT`, 512 KiB each); again as `IMAGE_SLOT` in `user/root.ld`
  and `user/child.ld`.
- **Root's region:** `hal.ROOT_LOAD_BASE` and `ROOT_REGION_TOP`, which
  `kcore.loader` reads; again in `user/root.ld`.
- **Every process's stack:** `hal.ROOT_STACK_LEN`, which `kcore.dispatch`
  also grants each child; again in the scripts.
- **Interrupt line numbers:** `tests/irq-lines/<arch>.c`
  (`driver_irq_line`); again in the HAL.
- **The pool's address:** `tests/poolbase_<arch>.c`; again in `ARCHES`.
- **Slab floors and quota defaults:** `kernel/core/limits.saw`, one set for
  every image.
- **Which boot row is which:** each root's source, by position; again in
  `_region_rows`.
- **What a child is given, under which tag:** each root's source
  (`TAG_DRV_WINDOW` in `tests/uart-service`); again in each child's source,
  as a drain position.

Two examples of the coupling. `tests/svc-client/Saw.toml` says the package
is linked at the second child base, "a contract with the case rather than a
property of the program", so it must be listed second. And
`drivers/svc-uart-ns16550/src/main.saw` maps its window, discards the
`Mapping`, and uses `static UART_BASE: UInt = 0x1000_0000`, although
`Mapping.base()` exists (`kernel/sysapi/src/mapping.saw`).

### 1.3 What the kernel takes from an image

- **The region table** (`kcore.process`, `mint_boot_regions`): version 3
  only, unknown flags and kinds refused with `fatal_image`, at most
  `MAX_MEMORIES + MAX_IOMEMORIES` rows, one boot record per row within the
  boot set (`boot_handle_room`: `MAX_HANDLES`, 16). Kinds are `Ram` and
  `Device` (`RegionKind`). Flag bit 0 turns off the tick. Bytes after the
  last row are not read.
- **Limits** are constants (`kernel/core/limits.saw`): `MAX_PROCESSES` 3,
  `MAX_WAITERS` 4, `MAX_PIPES` 4, `MAX_HANDLES` 16 and the rest. Each slab
  is a `Slab<T, N>` (`kernel/core/slab.saw`): an inline extent 0 of `N`
  slots, up to `MAX_SLAB_EXTENTS` (4) donated extents, and a run-time
  `total` every allocator and sweep reads. `SlabDonate`
  (`kcore.dispatch.slab_donate`) validates a region, sizes it with
  `slab_geometry`, zeroes it and appends an extent with `slab_install`.
- **Quotas.** `install_quota_limits` (`kcore.objects`) gives a child the
  `DEFAULT_QUOTA_*` numbers and root `QUOTA_UNLIMITED`, and clamps the
  mapping row to the grant rows the process has left after its image.

### 1.4 What cannot be done from outside the repository

- **Build a kernel.** The recipe exists only in the runner, with module
  paths into this tree.
- **Build a system image.** The stitchers are runner functions keyed by
  case dictionaries.
- **Link a sawos package at an address of the image's choosing.** Blade
  takes the script from the manifest alone, links with `-T <script>` and no
  `-L` (`blade/src/builder.saw`, `build_sos_image`), and silently ignores a
  flag it does not know (gap 11.7).
- **Build one package for two link addresses at once.** The output is fixed
  at `.build/<triple>/<name>.sosimg` (`BuildLayout`), and the runner builds
  cases in parallel.
- **Run more than one child in place on the ESP32-S3.** `esp32s3.ld` has one
  child slot.
- **Change a limit or a quota** without editing `limits.saw` for every
  image.

A package in another directory can depend on sawos packages by path and
build an sosimg. I built one (§16.1).

---

## 2. The parts

1. **A board description in sawos.** Kept. It holds the build-side facts.
   The kernel's own facts are no longer restated in it: the builder reads
   them from what the per-board kernel exports (rule 3.4).
2. **An image description in the product.** Kept. It also carries each
   process's build inputs, such as a driver's pin table, and its limits are
   the linked table's contents.
3. **A driver's statement of its needs.** Kept and widened. It is the
   process's whole boot interface, in `process.toml`.
4. **One builder, which the runner uses too.** Kept, and written in Saw
   (section 9).
5. **A table-driven root.** Kept. The table is a generated Saw module.
6. **A pin on sawos.** Kept. A Python resolver links the pinned tree into
   the product as `.sawos` (rule 9.7, section 10).
7. **The product's own test.** Kept. A test is a small description the
   harness reads. The harness is Saw once `std.process` allows it, and
   Python until then (section 10).

---

## 3. The board description

**Rule 3.1.** Each board in sawos has one `board.toml` in its HAL directory
(`hal/xtensa-esp32s3/board.toml`, `hal/riscv32/board.toml`,
`hal/arm64/board.toml`, and the flat profile's, which names the riscv32
board as its base). The runner's `ARCHES` tables and the hand-kept user
linker scripts are generated from it or deleted.

**Rule 3.2.** It states the facts only the build needs:

- the triple, the tier, the HAL directory and its extra kernel modules, the
  C flags, and how to run the board (the emulator and its arguments);
- the **placement kind**:
  - `copied` (riscv32): a child's blob may sit anywhere, and it is copied
    to its destination, which is where it is linked;
  - `in-place` (the ESP32-S3): code and read-only data run from a flash
    slot, and writable data is copied to a RAM region;
  - `translated` (arm64): every image links at one base, and a destination
    may be any frames;
- the default region and arena sizes, and, on an `in-place` board, the
  flash windows and the flash range for image slots;
- each **device** by name: base, length, a `compatible` string, its
  interrupt line, and whether the kernel uses it (the console);
- **reserved lines**, such as the kernel's tick.

**Rule 3.3.** An image names devices and lines by these names, never by
address or number.

**Rule 3.4.** Facts the kernel holds are read from the kernel, not
restated. This is the owner's "board info symbol". The per-board kernel
exports one table, `sos_board_info`, with:

- the process RAM range, root's region, the protection grain, the stack
  length and the grant-row budget;
- `PROT_DOMAIN_SLOTS`, `MAX_HANDLES` and `MAX_SLAB_EXTENTS`;
- for each slab kind, its floor, its unit size and its alignment.

The builder reads the table out of the kernel object by its symbol. A
version word leads it.

**Rule 3.5.** On-chip peripherals are board facts. What is wired to the
board (a switch on pin 21) is an image fact, carried in a driver's config
module (rule 4.6). Adding a peripheral to a board is a sawos patch.

The S3's file, abridged:

```toml
format = 1

[board]
name = "esp32s3"
triple = "xtensa-esp-unknown-elf"
tier = "flat"
hal = "hal/xtensa-esp32s3"
placement = "in-place"
default-region = "24K"
default-arena = "8K"

[flash]
bytes = "4M"
ibus = 0x4200_0000
dbus = 0x3C00_0000
root-slot = { offset = 0x10_0000, len = "512K" }
slots = { offset = 0x18_0000, top = 0x40_0000, default-len = "128K" }

[devices.UART0]
base = 0x6000_0000
len = 0x1000
compatible = "espressif,esp32s3-uart"
line = 27
console = true

[devices.GPIO]
base = 0x6000_4000
len = 0x1000
compatible = "espressif,esp32s3-gpio"
line = 16

[devices.IO_MUX]
base = 0x6000_9000
len = 0x1000
compatible = "espressif,esp32s3-io-mux"

[devices.LEDC]
base = 0x6001_9000
len = 0x1000
compatible = "espressif,esp32s3-ledc"
line = 35

[devices.SYSTEM_CLK]
base = 0x600C_0000
len = 0x40
compatible = "espressif,esp32s3-system-clock"

[lines]
reserved = [57]
```

The device numbers are SO-77's (`so77-design.md` §2.1, §3.1), from
ESP-IDF's headers. `SYSTEM_CLK` is the smallest naturally aligned window
covering `PERIP_CLK_EN0` (+0x18) and `PERIP_RST_EN0` (+0x20). The pinned
`libs/toml` cannot read this file (gap 11.1).

---

## 4. A process's declaration

**Rule 4.1.** Every process the stock root launches has a `process.toml`
beside its `Saw.toml`. It is sawos's file, read by sawos's builder, so a
new field is never a toolchain change. Blade refuses an unknown
`[sos.<name>]` table (`check_sos_subtables`), and its emitter is sawlang's.
A process launched only by a hand-written root may omit the file.

**Rule 4.2.** It declares the process's **boot set**, in the order the
launcher gives it: each record's name, tag and kind.

- `system`: a masked System sibling. Its tag is the start tag. It carries
  the §12 convention mask unless the declaration asks for more and the
  image agrees.
- `window`: an `IoMemory`, with the `compatible` strings it accepts.
- `line`: a bound `Interrupt`, with its `compatible` strings.
- `serve`: the outlet of a connection it serves, with the protocol, named
  by the package that defines it (`gpioproto`).
- `client`: an inlet onto a connection another process serves.
- `memory`: a RAM allocation or a pool row.

**Rule 4.3.** It declares the **quota rows it needs** above the image's
defaults. The builder refuses an image that grants less (rule 7.3).

**Rule 4.4.** It declares its default **region and arena** sizes, which an
image may raise, and the boards it builds for.

**Rule 4.5.** A driver finds its registers through `Mapping.base()`, never
through an address constant. `svc-uart-ns16550` is brought to this rule in
unit U6.

**Rule 4.6.** It declares its **build inputs**: modules the image must
supply, by name (`gpioconfig`). The builder passes each to the package's
compile as `--module-path NAME=DIR` (rule 9.5). A driver's pins and servo
ranges are such a module: typed Saw statics, checked by `static_assert`.
Nothing is sent at run time and nothing is parsed.

**Rule 4.7.** The tags in `process.toml` and in the source must agree. The
`sos` module gains a `BootSet` helper that drains every record into a small
table and answers `take(tag:)`. A missing or extra record is an error the
process prints with the tag, at its first instruction.

The GPIO driver's declaration:

```toml
format = 1
boards = ["esp32s3"]
region = "24K"
arena = "4K"

[[boot]]
name = "system"
kind = "system"
tag = 1

[[boot]]
name = "gpio"
kind = "window"
tag = 2
compatible = ["espressif,esp32s3-gpio"]

[[boot]]
name = "iomux"
kind = "window"
tag = 3
compatible = ["espressif,esp32s3-io-mux"]

[[boot]]
name = "gpio"
kind = "line"
tag = 4
compatible = ["espressif,esp32s3-gpio"]

[[boot]]
name = "gpio"
kind = "serve"
tag = 5
protocol = "gpioproto"

[quota]
mappings = 2
iomemories = 2
waiters = 1
handles = 10

[build]
inputs = ["gpioconfig"]
```

---

## 5. The image description, and link addresses

### 5.1 The file

**Rule 5.1.** An image is one TOML file in the product repository's
`images/` directory, with `format = 1`. The builder refuses a format it
does not know.

**Rule 5.2.** It states:

- `[image]`: the name, the board, the tick;
- `[limits]`: how many of each kernel object the image wants, and the
  children's quota defaults. Each is the board kernel's floor or default
  when absent (rule 6.1);
- `[root]`: `stock`, or a package path for a hand-written root;
- one `[[process]]` per child, in launch order: its name and package, its
  region and arena if not the declared defaults, each `window` and `line`
  bound to a board device by name, each `client` bound to another process's
  `serve` endpoint, each build input bound to a file, its quota rows, any
  narrowed ceilings, and `on-exit`;
- extra rows a hand-written root needs: `devices` and `pools`, in order.

**Rule 5.3.** A package is `sawos:<path>`, resolved in the pinned sawos
tree, or a path relative to the product repository's root.

**Rule 5.4.** The builder refuses, before it compiles anything, an image in
which:

- a device or line name is not on the board, or a line is reserved;
- a window's or line's device does not match its `compatible` strings;
- a declared boot record is not bound, or a binding names no record;
- a `client` and its `serve` name different protocol packages, or either
  package does not depend on that protocol package (read from `Saw.toml`);
- two records of one process share a tag;
- a quota row is below what the process declares it needs, or above what
  the kernel allows (rule 7.2);
- a limit breaks one of rule 6.4's relations;
- the regions, slots, pools and the kernel's reserve do not fit the board's
  memory, or overlap;
- the stock root's peak handle count exceeds `MAX_HANDLES`.

### 5.2 Link addresses: who assigns them

**Rule 5.5.** The builder assigns every address. Root's region is the
board's. Each child, in description order, gets a RAM region from the
board's process RAM after root's, of its stated size, aligned to the grain.
Pools and the kernel's reserve (rule 6.2) follow. On an `in-place` board
each child also gets a flash slot. On a `translated` board every image
links at the board's one base.

**Rule 5.6.** Each process is linked with a script the builder writes from
one template per board (`hal/<board>/user/process.ld.in`) and the assigned
numbers: the slot, the region, the arena (`PROVIDE(ARENA_SIZE = ...)`), and
`LENGTH` up to the stack. The scripts' `ASSERT`s and `check_in_place` stay.
`child.ld`, `child2.ld`, `child3.ld` and the S3's `child.ld` go. One
default script per board stays, for a standalone `blade build`.

**Rule 5.7.** On an `in-place` board the kernel's script `INCLUDE`s a
generated `image_slots.ld`, which places each child's `.childimg.<i>` at
its slot. The builder links the kernel, so it can pass `-L`.

**Rule 5.8.** A package is linked per image, and its output goes to the
image's build directory (`--out-dir`), so two images or two parallel cases
never share an artifact.

**Rule 5.9.** The region table keeps §12's row order, extended: each
child's blob, each child's destination, each pool, each device, each group
in description order. The runner's order today is the case of one pool and
one device, so every existing root still reads its rows.

### 5.3 Worked example: the useless box

The product repository:

```
box/
  sawos.pin
  tools/sawos.py           the resolver, a verbatim copy from sawos
  .sawos -> (the pinned sawos tree; written by the resolver, ignored by git)
  images/box.toml          the board image
  images/box-qemu.toml     the emulator image: stand-in drivers
  config/gpio.saw          the switch's pin
  config/pwm.saw           the servos' pins, channels and ranges
  controller/              Saw.toml, process.toml, src/
  heartbeat/               Saw.toml, process.toml, src/
  tests/box-qemu.toml      a test: an image, the expected console
```

`images/box.toml`:

```toml
format = 1

[image]
name = "useless-box"
board = "esp32s3"
tick = true

[limits]
processes = 5
waiters = 6

[root]
package = "stock"

[[process]]
name = "gpio"
package = "sawos:drivers/svc-gpio-esp32s3"
windows = { gpio = "GPIO", iomux = "IO_MUX" }
lines = { gpio = "GPIO" }
inputs = { gpioconfig = "config/gpio.saw" }

[[process]]
name = "pwm"
package = "sawos:drivers/svc-pwm-esp32s3"
windows = { ledc = "LEDC", gpio = "GPIO", iomux = "IO_MUX", clock = "SYSTEM_CLK" }
inputs = { pwmconfig = "config/pwm.saw" }
quota = { mappings = 4, iomemories = 4 }

[[process]]
name = "controller"
package = "controller"
clients = { switch = "gpio.gpio", servos = "pwm.pwm" }
region = "32K"
on-exit = "shutdown"

[[process]]
name = "heartbeat"
package = "heartbeat"
```

(The PWM `windows` line runs past 80 columns because TOML allows no line
break inside an inline table.)

What the builder makes of it:

- **Rows.** Four blobs (each in its flash slot), four destinations, and
  the devices in first-use order: GPIO, IO_MUX, LEDC, SYSTEM_CLK. That is
  twelve rows, within the boot set's 16.
- **RAM.** The board gives processes 256 KiB (`hal.RAM_BASE` to `RAM_TOP`,
  read from `sos_board_info`).
  - Root keeps its 96 KiB region.
  - The children take 24 + 24 + 32 + 24 = 104 KiB, each with the kernel's
    16 KiB stack at its top.
  - The kernel's reserve takes what `[limits]` costs: two process bodies
    and two Waiters above the floors (rule 6.2). On riscv32 a body is 804
    bytes (measured, §16.1); the S3's numbers come from its own export.
  - What is left is unused here.
- **Flash.** Four slots of 128 KiB from 0x18_0000 end at 0x20_0000, where
  the kernel's read-only data goes today.
- **Kernel.** The S3's one kernel object, as the gate tested it. The image
  adds a boot table whose limits block reads: processes 3 + 2, Waiters
  4 + 2. The pipe floor of 4 already covers the box's two connections.
  `processes = 5` is within `PROT_DOMAIN_SLOTS` (256 here).
- **Root's plan.**
  1. Create the four children.
  2. Mint a sibling of the GPIO and IO_MUX rows, since PWM is given them
     too. Then give the GPIO driver its System sibling (tag 1), the GPIO
     row (2), the IO_MUX row (3), line 16 bound (4), and the outlet of a
     new connection (5).
  3. Set PWM's `Mapping` and `IoMemory` rows to 4, then give it its four
     windows.
  4. Give the controller a System sibling and two inlets minted with the
     convention's client mask.
  5. Start the children in order and supervise them. When the controller
     ends, stop the machine with its exit code.

`config/pwm.saw`, the input the PWM driver declares:

```saw
//! The box's servos: pad, LEDC channel and the bench-measured safe range.
import pwmconfig_types.{Servo}

public static SERVOS: [Servo; 2] = [
    Servo(pad: 41, channel: 0, min_us: 500, max_us: 2500),
    Servo(pad: 42, channel: 1, min_us: 500, max_us: 2500),
]
```

`images/box-qemu.toml` is the same with the two drivers replaced by
stand-ins speaking the same protocols, because the emulator models no GPIO
input and no LEDC (`hal/xtensa-esp32s3/ABI.md` §9).

### 5.4 Worked example: `uart_service_ns16550`

The case today: `root_pkg` is `tests/uart-service`, `children` are the
NS16550 driver and `tests/svc-client`, `device` is true, `tick` is absent,
and the transcript asserts `SOS: boot regions={five}`.

As a description in sawos (`tests/images/uart_service_ns16550.toml`):

```toml
format = 1

[image]
name = "uart_service_ns16550"
board = "riscv32-virt"
tick = false

[root]
package = "sawos:tests/uart-service"

[[process]]
name = "driver"
package = "sawos:drivers/svc-uart-ns16550"

[[process]]
name = "client"
package = "sawos:tests/svc-client"

[rows]
devices = ["UART0"]
```

The root is hand-written, so no `process.toml` is needed and no boot
interface is checked. The table is the same five rows in the same order:
two blobs, destinations at 0x8024_0000 and 0x8028_0000 (the board's default
256 KiB regions packed from root's top), and the UART window. No
`[limits]`, so no limits block: the board kernel's floors, as today. The
client's script is generated, and `tests/svc-client/Saw.toml` stops warning
about its position.

The runner keeps its case dictionary. In unit U1 it writes the same
description as JSON for the builder, because `std.json` reads what the
runner writes today (§16.1) and that file is machine-written. The TOML form
waits for gap 11.1.

With the stock root, `[root]` becomes `stock`. The driver gets a
`process.toml` declaring system 21, window 22, serve 23 and line 24 in that
order, and the client binds `clients = { uart = "driver.uart" }`. The
driver's code does not change: it already drains window, outlet and line in
that order, and the stock root gives in declaration order (rule 8.4).

---

## 6. How limits reach the machine

The owner asked whether the kernel can stay the same per board, with each
image's limits in a linked table. I built it in a scratch copy of the
kernel. It works.

### 6.1 What was built and run

In a scratch copy of the tree:

- **Kernel** (`kcore.dispatch`): `apply_boot_limits()`, about 70 lines,
  called from `kernel/main.saw` after `hal.intc_init()` and before
  `load_root_and_enter`. It reads a block after the region table's rows:
  - a 24-byte header with magic, entry count, reserve base and reserve
    length;
  - one 8-byte entry per kind, giving the `SlabKind` and the slots to add.

  For each entry it calls the donation path's own `slab_geometry` for the
  unit size and alignment, aligns a cursor in the reserve, zeroes the
  bytes, and calls the same `slab_install` that `slab_donate` ends in.
- **Runner**: `_stitch_regions` writes the block after the rows when a case
  has a `limits` key.
- **Case**: `process_donate_limits`, `process_donate` with
  `limits = [(11, 1)]` (one more process body) and a reserve of 256 KiB
  above every other row.

`process_donate`'s root creates two children, then asserts that a third
is refused at the floor of three, then donates. On both translated and
identity tiers the boot table changed that:

```
SOS M1: kernel up on riscv32 (QEMU virt)
SOS: limits extended kind=0x0000000b unit=0x00000324 align=0x00000004
     by=0x00000001
SOS: root image ok segments=0x00000003 entry=0x8020030c prio=0x01010100
SOS: boot regions=0x00000006
SOS: console handover
SOS procdonate: a fourth process existed BEFORE the donation
```

(The second line is one console line, broken here.) arm64 printed the
same lines with `unit=0x...0590 align=0x...0008`. The
unchanged `process_donate`, with no block, passed on both. I ran this
under the suite lock with `--arch riscv32` and `--arch arm64`.

### 6.2 The three objections, taken again

**1. "The kernel carving its own storage before root exists."** It needs
no new mechanism. A boot-time extent is a donated extent: `slab_geometry`
answers the unit and alignment, `slab_install` appends it, and the two
two-chain kinds (threads with their frames, pipes with their exchanges)
are already handled inside `slab_install`. What changes:

- **Where the bytes come from.** The block names a reserve, a RAM range the
  builder assigns from the board's memory like a pool. It is never minted
  to root. The kernel checks that the reserve lies in RAM the board can
  give (`hal.map_source_ok`) and that no boot row overlaps it; the
  prototype did not check overlap. Nothing is taken from root's pool, and
  nothing in the reserve is ever user-reachable.
- **Extent 0.** It is each board's compiled floor, today's numbers, so an
  image can only raise a limit. A board that wants smaller floors sets
  them for that board; that is a per-board choice, not an image's.
- **Cost.**
  - Each kind an image raises spends one of its four extents, leaving
    three for run-time donation.
  - A slot above the floor is reached through `Slab.locate`, a loop of at
    most four steps. That path already exists for donated slots, so
    nothing new is checked per access.
  - The reserve costs exactly (wanted - floor) units of each kind, plus
    alignment pads.
  - Zeroing uses `zero_bytes` and not `long_zero`, because no thread
    exists yet at boot.
  - `mint_boot_regions` compares its row count with the
    `MAX_MEMORIES + MAX_IOMEMORIES` constants; it reads the capacities
    instead.

**2. "The build cannot size the reserve."** It can, if the kernel says
what a slot costs. The direct spelling does not compile with the pinned
compiler:

- `sizeof<ProcessBody>()` in a static's initializer is refused
  ("`kcore.pslot.ProcessBody` is laid out only by code generation, so
  `sizeof` and `alignof` measure it only in a `static_assert`").
- `static_assert(sizeof<ProcessBody>() == 804, ...)` is refused too ("a
  layout this `static_assert` measures is not in the bootstrap slice yet: a
  raw-backed case whose value was not evaluated"). Both are gap 11.4.

What does work, and I ran it:

- The HAL states each unit and alignment as literal numbers per board, in
  an exported static. I wrote `sos_slot_geometry`, linked it with
  `--gc-sections`, and read 0x324 and 4 back out of the kernel ELF's
  `.data` by its symbol.
- At boot the kernel compares each literal with `slab_geometry`'s answer
  and stops with the kind's name if one is stale. The gate boots every
  board, so a stale number is red on the first run.

The builder reads `sos_board_info` (rule 3.4) out of the per-board kernel
object and sums the reserve. It refuses an image whose reserve does not fit
the board's memory. A linker script cannot do this sizing: it can compute
with symbols but cannot read a value out of a section.

**3. "It reaches the donatable slabs only."**

The table reaches every `SlabKind`:

- Events, Waiters, Interrupts, Attachments and Timers;
- Memories, IoMemories, Mappings and FreeRanges;
- Threads, with their frames;
- Pipes, with their exchanges;
- Processes (bodies) and ProcessRecords.

It also reaches the children's quota defaults, which become values the
kernel reads from the table at boot (`install_quota_limits`), and the boot
flags.

It cannot reach these, which stay per-board constants:

- **Arrays inside every process body:**
  - `MAX_HANDLES`, which sizes both the handle table and the boot set;
  - `MAX_GRANT_ROWS`, the grant record, and the HAL's `GRANT_ROW_BUDGET`.
- **Ring and table dimensions:** `PIPE_INFLIGHT` (each connection's ring,
  derived from the board's thread floor), `MAX_SLAB_EXTENTS`,
  `MAX_SLAB_CAPACITY`, the frame stride and `CLOCK_KINDS`.
- **What the HAL sizes:** arm64's `PROT_DOMAIN_SLOTS` (5), a static pool of
  page-table sets, and the stats region it sizes. A create past it already
  answers `NoResource` (`process_slots_usable`).
- **Root's region, the stack length, and the kernel's own stack and
  arena.**

The box needs none of them raised:

- Its root's peak handle count fits 16 if root releases each blob and
  destination after its create, as `tests/uart-service` does.
- Its twelve rows fit the 16-record boot set.
- The PWM driver's four windows fit the grant budget, 8 - 4.

"Those stay per-board constants" is an acceptable line. Each costs every
body on the board. Raising one is a decision about the board, made in a
sawos patch.

### 6.3 Where the numbers are checked

**Rule 6.4.** The `static_assert`s in `limits.saw` split in two.

- **Those relating per-board constants stay compile-time**, in the
  per-board kernel:
  - records per body above one;
  - the grant record against the HAL;
  - the process floor against `PROT_DOMAIN_SLOTS`;
  - the capacity ceiling against the handle table and `Int`;
  - the frame stride;
  - `PIPE_INFLIGHT`'s bounds;
  - each floor's quota default below its floor.
- **Those relating an image's numbers become the builder's checks**, before
  anything is linked and by name, **and the kernel's at boot** as a
  backstop (`fatal_image` naming the kind and the number):
  - each quota default below its slab's boot capacity;
  - the mapping default plus the image's grant rows within
    `GRANT_ROW_BUDGET`;
  - attachments at least the sum of the waitables, which the builder
    computes when the image raises a waitable and does not state
    attachments;
  - processes within `PROT_DOMAIN_SLOTS`;
  - each total within `MAX_SLAB_CAPACITY`;
  - the reserve holding every extent.

### 6.4 Per board, per image

**Rule 6.1.** The table is per image, over board defaults.

- **The board owns:** the floors (extent 0), the per-body arrays, the HAL's
  pools, the slot geometry, and the quota defaults' floor values.
- **The image owns:** how many of each kind it wants above the floors, the
  children's quota defaults, and the tick.
- An image with no `[limits]` links no limits block and boots exactly as
  today.

**Rule 6.2.** The builder assigns the reserve from the board's RAM after
the pools. It writes the limits block into the boot table, whose version
becomes 4. The kernel accepts exactly its own version, so a table and a
kernel from different revisions refuse each other at boot.

**Rule 6.3.** Donation stays. A running system grows past its boot capacity
with `SlabDonate`, within the extents the boot table left.

### 6.5 What it buys and what it loses

What it buys:

- **One kernel object per board.** The gate tests it bit for bit, under
  every image.
- **A product never compiles the kernel.** It links the board's object with
  generated stubs. It still builds its own processes with sawc.
- **A precedent already exists:** the boot region table is the same shape,
  generated per image and linked in.
- **The board's kernel can be built once per sawos revision** and reused by
  every case and every image.

What it loses against compiling the kernel per image:

- **The per-body limits** (`MAX_HANDLES`, the grant record) cannot move per
  image.
- **Compile-time refusal by name** becomes the builder's refusal by name
  before the link, with the kernel's boot check behind it. The builder is
  sawos code, gate-tested like the kernel, so that is a fair trade.
- **`.bss` is no longer exactly what was asked.** Extent 0's floors are paid
  whether the image wants them or not, so an image cannot go below a
  floor.
- **A hand-kept number.** The slot geometry is a literal per board until
  gap 11.4 is closed.

**Recommended: the per-board kernel with a linked limits table**, for the
reasons above. The owner and the lead lean the same way, and objections 1
and 2 held when tried. Compiling the kernel per image is kept in section
13 as an alternative.

---

## 7. Per-process quotas: the door

**Rule 7.1.** A launcher sets a child's quota rows between its create and
its start, through the child's Process handle. The image's description is
the stock root's policy, so the description grants and the root applies.
An sosimg carries no quota, and a process cannot set its own: it can never
name itself while `Created`.

**Rule 7.2.** The op, as spec text for §2.7 (after `RestrictRights`):

> - **Quotas.** `ProcessOp.QuotaSet(kind, limit)` sets the named process's
>   limit for one `QuotaKind`. It is gated by `ProcessRight.QuotaSet`,
>   which the default set carries. It follows `Give`'s rule: the named
>   process must be `Created`, and naming one that is not is a `BadState`
>   fault. A kind the op does not know is a `BadArg` fault.
> - The limit may not exceed the caller's own limit for that kind, and a
>   limit above it is a `BadArg` fault: the caller can read its own
>   (`QuotaGet`, below). Root's rows are unlimited, so root is bounded by
>   the machine alone. A quota, like a ceiling, never grows down a chain of
>   launchers.
> - A `Handle` limit may not exceed `MAX_HANDLES`, and is a `BadArg` fault
>   above it. A `Mapping` limit past the grant rows the child's image left
>   free answers `NoResource` and sets nothing, because a launcher cannot
>   read the child's image.
> - A limit below what the child already uses is a `BadArg` fault. A
>   launcher sets quotas before it gives.
> - `ProcessOp.QuotaGet(kind)` answers the named process's limit and use as
>   two words through the copy-out funnel, gated by `ProcessRight.Stats`,
>   as `Ceiling` is.

§2.9's bullet "A child gets `kcore.limits`' documented defaults; there is
no per-create quota argument in v1" becomes:

> - A child starts with the image's quota defaults, which the boot table
>   sets over the board's (§12), and its launcher may set any row before it
>   starts (`ProcessOp.QuotaSet`, §2.7), up to the launcher's own limit.
>   There is no quota argument to `ProcessCreate`.

§11's known limit "there is no per-create quota argument" goes.

**Rule 7.3.** The builder refuses an image whose grant for a row is below
what the process declares it needs (rule 4.3). It applies `QuotaSet` only
for rows raised above the image's default.

**Rule 7.4.** "How many memory ranges a process may map" is the `Mapping`
row. Its hard bound is `hal.GRANT_ROW_BUDGET` less the image's rows, so
8 - 4 = 4 on riscv32 and the S3. The PWM driver's four windows fit exactly.
On the S3 the budget has no hardware behind it
(`hal/xtensa-esp32s3/kernel/lib.saw`), so five would be a per-board change.

---

## 8. Root's interfaces

Root is replaceable. This section is what any root may rely on and must
do, split three ways.

### 8.1 Kernel contract (spec; cited)

1. **Entry.** One handle wide: System in the first argument register, with
   `root_system_rights()` (§5.7 "The boot set is one handle wide", §2.8).
2. **Its region and image.** Root's image is the one the kernel loads, into
   the board's root region with the stack at its top (§5.6, §12
   "Loader-above-boot"). Its quotas are unlimited except the mapping row's
   clamp (§2.9), and its ceilings are unrestricted (§2.7).
3. **The boot set.** One record per region-table row, tagged by its
   ordinal, of the row's kind, answered in row order, then `Drained` for
   good (§12, "The boot set is collected").
4. **Row kinds and rights** (§12, "The kind column types every row").
   `Pool` is specified and not built: `RegionKind` has `Ram` and `Device`,
   so a pool row is a `Ram` row today.
5. **Boot flags and limits.** Bit 0 turns off the tick (§12). The limits
   block sets the slabs' boot capacity and the children's quota defaults
   before root runs (new, rule 6.2).
6. **Root's end is the machine's** (§8).
7. **Every other process** is root's to create, with
   `process_create(image:memory:)` (§12).

### 8.2 Generated per image (new text for §12)

8. **The boot table**, in rule 5.9's row order, and its limits block.
   Proposed §12 text, replacing "The tree's stitcher
   (`tools/sos_runner.py`) writes, in this order...":

   > The image builder (`tools/sosimage`) writes, in this order, which
   > root's config reads as tags: one image blob per child, one destination
   > per child, each pool, then each device window, each group in the order
   > the image description lists them. After the rows comes the image's
   > limits block, which the kernel applies before root runs and which
   > root never sees.

9. **The plan module, `imageplan`**, for a root that imports it. For each
   child it gives:
   - its name, and its blob and destination ordinals;
   - its ceilings and its quota rows;
   - the records to give, in order, with each one's tag, source and keep
     mask;
   - its start tag and its exit policy.

   It also lists the connections. Its types live in a sawos library,
   `bootplan`. A replacement root in Saw may import the module, or ignore
   it and number rows itself, as every root in `tests/` does.

### 8.3 Convention among userspace programs (new text for §12)

> **The launch convention** (userspace, not kernel surface). A launcher
> that follows it:
>
> - gives a child exactly the records its declaration names, under their
>   tags, in declared order, and nothing else, before it starts the child;
> - starts the child with its System record's tag, masked with
>   `Debug | ProcessSelf | ClockGet | Transfer` unless the image grants more;
> - sets the child's quota rows to at least what it declares, before any
>   give;
> - gives a window, a bound line and an endpoint as capabilities; the
>   child maps its own windows and finds them through `Mapping.base()`;
> - gives a client an inlet minted with `Post | Transfer`, and a server
>   the outlet;
> - keeps the child's Process handle, may kill it, and is the one who
>   learns of its death;
> - gives the child a region of at least its declared size, with the
>   kernel's stack at the top.

The kernel already does what the convention leans on
(`queue_boot_handle` appends at `boot_count`, and `boot_handle_next` reads
at `boot_next`), but the spec does not say so. For §2.7's `Give` list:

> - `BootHandleNext` answers records in the order they were queued: root's
>   in row order, a child's in the order its launcher gave them. The record
>   `Start` consumed is never answered.

### 8.4 The stock root

**Rule 8.1.** sawos's `root/` becomes the stock root. It imports
`imageplan` and does exactly this:

1. Derive its Process handle, and drain rows as each step needs them.
2. For each child in order: create it, release its blob and destination,
   apply its ceilings and quota rows.
3. Create each connection, and give each child its records in declared
   order. Lines are bound, and System and shared-window siblings minted,
   at the moment each is given.
4. Start each child in order.
5. Attach every child to one Waiter, and print one line per death:
   `SOS root: <name> ended status=<word>`.
6. Stop the machine when a child marked `on-exit = "shutdown"` ends, with
   that child's code, or when every child has ended (0 if all exited 0).

**Rule 8.2.** A step that fails prints the step, the child and the status,
and stops the machine. The stock root never runs a half-wired system.

**Rule 8.3.** It has no device-specific code, no address and no line
number of its own.

**Rule 8.4.** It gives each child's records in the child's declared order,
so a driver that drains by position works unchanged.

---

## 9. The builder, in Saw, and how a product finds sawos

### 9.1 What it is and where it lives

**Rule 9.1.** The builder is `tools/sosimage/`, a hosted Saw package built
by Blade: `Saw.toml`, `src/main.saw` (the command line) and modules for:

- the image model, and the JSON and TOML readers;
- validation (rule 5.4), address assignment (rule 5.5) and the generators
  (rule 9.3);
- building packages (it runs Blade), compiling and linking a board's kernel
  (it runs sawc, clang and `ld.lld`);
- the S3 flash image and `check_in_place`, which read ELF and sosimg bytes;
- `dump`, which replaces `tools/sosimg_dump.py`.

The parts that need more than `std.process` offers today are named in
section 10.

**Rule 9.2.** It is built before first use the way Blade is.
`_build_blade` already builds Blade from the sawlang checkout when no
binary is supplied. The runner then runs `blade build` in `tools/sosimage`
with the same `SAWC`. My probes of this size built in under a second, and
Blade itself (about 4000 lines) compiled cold in 0.8 s (§16.1). Until the
runner itself moves, it calls the builder once per case: it writes the case
as a JSON description into the case's build directory and runs
`sosimage build FILE --out DIR`, then boots what comes back as it does
today.

**Rule 9.3.** The generators are inside the builder, in Saw, writing text
with `StringBuilder` and files with `std.file` (both tried, §16.1). They
write:

- the boot-table stub (rows and limits block);
- the root and child `.incbin` stubs;
- the per-process linker scripts, and on an `in-place` board
  `image_slots.ld`;
- the `imageplan` module.

Nothing generates a kernel module any more.

**Rule 9.4.** The kernel is compiled once per board by the builder, with
sawc, as `_build_elf` does today, and cached under the build directory by
sawos revision and board. Every image on that board links that object.

**Rule 9.5.** Blade builds every process image with three new flags, which
are a sawlang change (gap 11.7):

- `--linker-script PATH`;
- `--out-dir DIR`, under which Blade writes everything, the lock included;
- `--module-path NAME=DIR`, passed through to sawc.

Until they land, the builder links each package with its manifest's
script, which is enough for unit U1.

### 9.2 How the builder names the libraries it uses

`libs/toml` and `libs/imgformat` live in the sawlang checkout, not in a
package registry. A manifest names a dependency by a path, and the
checkout's path differs by machine. Blade has no spelling for "the
toolchain's own library" (gap 11.9).

**Rule 9.6.** The toolchain resolver links the resolved checkout at
`<sawos>/.sawlang`, git-ignored, as the product resolver links `.sawos`.
`tools/sosimage/Saw.toml` names `toml = { path = "../../.sawlang/libs/toml" }`.
I built packages this way through a link (§16.1). This works for
`SAWLANG_ROOT` and for the fetched pin. It cannot work for a `$PATH`
toolchain, which supplies executables only; that is already true of the
kernel's build (CLAUDE.md).

### 9.3 How a product finds sawos

**Rule 9.7.** A product repository has a `sawos.pin` (`version`, `sha`) and
a verbatim copy of sawos's resolver, `tools/sawos.py`, in the shape of
`tools/toolchain.py`:

- it uses `SAWOS_ROOT` if set, else fetches the pinned sha into
  `~/.cache/sawos/<sha>/`;
- it links the result as `.sawos` at the product's root;
- it resolves sawos's own sawlang pin, builds Blade and `sosimage`, and runs
  `sosimage`.

The product inherits sawos's sawlang pin and does not pin sawlang itself.
Product packages depend on sawos packages by path through the link, and
Blade normalizes paths lexically, so the link and `../../` paths agree on
one source (§16.1).

### 9.4 Testing and flashing a product

**Rule 9.8.** `python3 tools/sawos.py build images/box.toml` builds into
`build/useless-box/`. `run` boots it under the board's emulator and prints
the console. `test tests/box-qemu.toml` boots the image a test names, feeds
its `stdin`, and judges the console against `expect`, an ordered list as
the runner's `expect_out` is, and its `status`. The no-timing rule of
sawos's CLAUDE.md applies to product tests too.

Judging a console is string work, and the Saw builder does it
(`sosimage check`). Booting under the emulator needs a child process with
input, streamed output, a deadline and a kill, which `std.process` does not
have (gap 11.2). So `run` and `test` boot through the runner's Python
harness, which the resolver calls, until the harness can move (section 10).

**Rule 9.9.** `sosimage flash images/box.toml --out box.bin` writes the
board image, prints its SHA-256 and the
`esptool --chip esp32s3 --port PORT write-flash 0x0 box.bin` line, and
flashes nothing. The image bytes are Saw (tried, §16.1). There is no
SHA-256 in the standard library (gap 11.10), so the builder runs
`shasum -a 256` until there is.

---

## 10. Every tool: Saw or not

**The builder (`tools/sosimage`).** Saw. Everything it needs either works
(binary files, paths, processes with arguments and an environment, maps,
sorting, text, JSON) or has a workaround I tried:

- a working directory for a child, through `sh -c 'cd ... && exec ...'`;
- a directory test, by whether `Directory.list` fails;
- hex output, with an eight-line function.

Its TOML input waits on gap 11.1. Unit U1 needs no TOML, because the
runner's description is machine-written JSON.

**The generators (boot table, stubs, link scripts, `imageplan`).** Saw,
inside the builder. Text building and file writing were tried. The per-image
`kconfig` generator of the previous revision is gone with the per-image
kernel.

**Reading the kernel's exported facts** (`sos_board_info`,
`sos_slot_geometry`). Saw, in the builder: an ELF symbol lookup and a
little-endian read, the same byte work as reading an sosimg, which I did.

**The product's test harness: boot, feed, deadline, diff.** Split. The
diff is Saw today. Booting is not possible in Saw with the pinned standard
library: `Command` takes no working directory and `Child` has no input,
no streamed output, no kill and no wait with a deadline (gap 11.2,
reproduced). Two ways out, neither tried end to end:

- the harness reaches the emulator through sockets rather than pipes (QEMU
  can put the serial port and its monitor on sockets, and `std.net` has
  TCP), stopping it with the monitor's `quit`;
- a libc module in sawos (`posix_spawn`, `poll`, `kill`). Calling libc from
  Saw works (`getpid`, `kill`, `usleep` ran, §16.1), but this would be a
  process library rebuilt outside std.

Recommended: file gap 11.2, keep the Python harness until it closes, and
probe the socket route in unit U8.

**The toolchain resolver (`tools/toolchain.py`) and the product's sawos
resolver (`tools/sawos.py`).** Python, for a concrete reason: they run
before any Saw compiler exists on the machine, and the compiler they fetch
is built by `bin/sawc` through `compiler/tools/build.py`, a Python program.
So Python 3 is already a requirement of every machine that can build Saw.
A POSIX shell script could fetch, but `toolchain.py` is a verbatim copy
shared with sawlang, and a second implementation would drift.

**`tools/sos_runner.py`.** It stays Python for now. A path to Saw exists,
in four steps:

1. **U1:** the build moves into the Saw builder. The runner writes JSON
   and calls `sosimage`.
2. **U8:** the judging (`_check`, the `{entry}`-style expectation
   formatting) moves to `sosimage check`.
3. **U8, after gap 11.2:** booting moves to a Saw harness. The runner is
   then a scheduler, a case table and a report.
4. **Not scheduled:** the case table. About 400 case dictionaries, most of
   them commented prose explaining what each transcript line proves, make
   up the bulk of the file. Moving them means a data format that keeps the
   comments, which is TOML once gap 11.1 closes (JSON has none). Then a Saw
   runner reads the cases, schedules them on threads, and reports.

What stays as it is for now: the case table, the scheduler, the report and
the emulator boot. Step 4 is worth doing only once 1 to 3 have landed, and
the transcript diff is the test for each step.

**`tools/sosimg_dump.py`.** Becomes `sosimage dump`, in Saw. Reading an
sosimg in Saw was tried.

**`esptool`.** Espressif's Python tool. It is not ours, and the builder
only prints the command.

---

## 11. Saw gaps

Each entry is written to be filed with the sawlang lead as it stands. All
were run against sawlang 0.23.0 (the lead's checkout at
`.claude/lead/sawlang-0.23`, `sawc-stage2` and its `blade`), hosted on
macOS arm64, unless the entry says otherwise. "Reproduced" means I ran it;
"read" means I only read the source.

### 11.1 `libs/toml` reads a subset of TOML and misreads the rest silently

- **Tried.** A hosted package depending on
  `toml = { path = "<checkout>/libs/toml" }` parsed files with
  `TomlDoc.parse`, then walked `section_at`, `names`, `get`, `get_int`,
  `table_value`. Reproduced.
- **What happened**, by construct:
  - `format = 1` before the first `[section]`: dropped. The document has no
    section for it, and no error.
  - `tier = "flat"   # trailing comment`: the value is
    `"flat"   # trailing comment`, quotes and comment included.
  - `base = 0x6000_0000`: stored as the string `0x6000_0000`, and
    `get_int` answers `None`. Every integer is a string, and `get_int` is
    base 10 only.
  - `reserved = [57]`: the string `[57]`. There are no arrays.
  - `[[process]]` three times: three sections each named `[process]`.
    `section("[process]")` finds the first only, though iteration with
    `section_at` sees all three.
  - An inline table across two lines: `windows = { ledc = "LEDC",` became a
    string key `windows`, and the next line, `clock = "SYSTEM_CLK" }`, a
    key `clock`. No error.
  - `x = { gpio = "p, q", clock = "r" }`: `gpio` reads `"p`. The table is
    split on commas inside strings.
  - `expect = ["a",` followed by `"b"]`: `TOML parse error: Invalid line:
    "b"] (line 19)`.
- **Expected.** TOML 1.0 for these constructs: top-level keys, comments,
  integers with hex and underscores, arrays (multi-line too), arrays of
  tables, and strings containing commas. An error, never a silent misread,
  for anything outside what the library supports.
- **Why sawos needs it.** The board, image, process and test descriptions
  are TOML. Blade's manifests would benefit too: Blade parses hex itself
  (`parse_manifest_word`).
- **How badly.** It blocks reading the description files in Saw. Workaround:
  the runner-to-builder path uses JSON (`std.json` works), so unit U1 is not
  blocked. Unit U3, the first that reads hand-written descriptions, is.
  Writing a strict TOML reader inside sawos is the fallback if sawlang
  declines.

### 11.2 `std.process` cannot run an interactive child

- **Tried.** Reproduced with this program:

  ```saw
  import std.process.{Command}
  func main() {
      var cmd = Command(program: "qemu-system-riscv32")
      try! cmd.current_dir("/tmp")
      var child = try! cmd.spawn()
      try! child.write_stdin("Zq7#")
      let line = try! child.read_stdout_line()
      child.kill()
      let _ = try! child.wait()
  }
  ```

- **What happened.**
  ```
  ERROR member.unknown `std.process.Command` has no method `current_dir`
    this module can see
  ERROR member.unknown `std.process.Child` has no method `write_stdin`
    this module can see
  ERROR member.unknown `std.process.Child` has no method
    `read_stdout_line` this module can see
  ERROR member.unknown `std.process.Child` has no method `kill` this
    module can see
  ```
  The method names are mine. Reading `std/process.saw` shows the surface
  is `Command.arg`, `env`, `merge_stderr`, `wait`, `spawn`, `output` and
  `output_bytes`, and `Child.wait`. Nothing else is there under any name.
- **Expected.** A child's working directory; a pipe to its standard input;
  its standard output read as it arrives; a kill; and a wait that can end
  at a deadline. Each is ordinary in a hosted standard library.
- **Why sawos needs it.**
  - Blade must run in the package directory, so the builder needs a
    working directory.
  - The emulator harness must type at the guest's serial port byte by byte
    with a delay (the echo cases), read its console as it arrives, and kill
    it at the harness deadline.
- **How badly.** The working directory has a workaround I ran:
  `/bin/sh -c 'cd "$1" && exec "$2" ...'`. The rest blocks moving the
  emulator harness to Saw. Workarounds: libc through `extern "C"` (works
  for `getpid`, `kill`, `usleep`), or QEMU's serial and monitor on sockets
  (untried). Both are worse than std having it.

### 11.3 No way to tell a directory from a file

- **Tried.** A recursive walk that descended when
  `Directory.exists(entry)` was true counted 0 `.saw` files under
  `kernel/`. Reproduced.
- **What happened.** `Directory.exists` is documented as "whether a file or
  directory exists at `path`" (`std/directory.saw`), and there is no
  `is_dir`, `is_file` or metadata call.
- **Expected.** A way to ask whether a path is a directory (or a stat).
- **Why sawos needs it.** Walking package trees, finding sosimg outputs,
  and the arch-name scan the runner does over `kernel/core`.
- **How badly.** Has a workaround, which I ran: treat a path as a directory
  when `Directory.list` succeeds. It counted all 41 files. That costs an
  `opendir` per file. Nice to have.

### 11.4 A struct's size cannot be exported or asserted

- **Tried.** In the kernel (freestanding riscv32, `-Oz`), reproduced:

  ```saw
  @export("sos_slot_geometry")
  static SLOT_GEOMETRY: [UInt32; 2] = [sizeof<ProcessBody>() as UInt32,
                                       alignof<ProcessBody>() as UInt32]
  ```

  and separately

  ```saw
  static_assert(sizeof<ProcessBody>() == 804, "stale")
  ```

  `ProcessBody` is `kcore.pslot`'s; it holds raw-backed enums.
- **What happened.**
  ```
  ERROR static.not-constant `sizeof<ProcessBody>()` is not a constant, and
    a static's initializer is a constant expression:
    `kcore.pslot.ProcessBody` is laid out only by code generation, so
    `sizeof` and `alignof` measure it only in a `static_assert`; elsewhere
    they measure an integer, `Bool`, `Float`, `String` or a raw pointer
  ERROR slice.not-yet a layout this `static_assert` measures is not in the
    bootstrap slice yet: a raw-backed case whose value was not evaluated
  ```
- **Expected.** `sizeof<T>()` and `alignof<T>()` of any sized struct as a
  constant: in a static's initializer, and in a `static_assert`.
- **Why sawos needs it.** The per-board kernel exports each slab's slot
  size, so the builder can size the kernel's reserve and refuse an image
  that does not fit (section 6). The kernel already computes these at run
  time (`Slab.slot_bytes`).
- **How badly.** Has a workaround, which I ran: literal numbers per board
  in the exported table, compared with `slot_bytes()` at boot. That is a
  hand-kept table per board, made red by the gate's first boot when stale.

### 11.5 No hexadecimal formatting of integers

- **Tried.** `print("{:x}", 255)`. Reproduced.
- **What happened.** `ERROR parse-error 2:13 expected an expression`. The
  one hex renderer, `StringBuilder.append(address:)`, is module-private
  (`std/stringbuilder.saw`).
- **Expected.** A public way to render an integer in hex, zero-padded to a
  width: a format spec, or a `StringBuilder` method.
- **Why sawos needs it.** Generated linker scripts, assembler stubs and
  every diagnostic name addresses, which are read in hex.
- **How badly.** Has a workaround, which I ran: an eight-line `hex(value,
  width)` in the builder. Nice to have.

### 11.6 Reading a value out of a JSON object needs the whole document mutable

- **Tried.** `top.get("name")` on the `Map<String, JsonValue>` from
  `JsonValue.as_object()`. Reproduced.
- **What happened.** `ERROR type.bound ... has no member get: it is
  declared where V: Copy, and std.json.JsonValue does not satisfy Copy`.
  The working spelling is `var doc`, then `borrow let top =
  doc.as_object()!` and `top.find(&key)!`: an exclusive borrow for a read.
- **Expected.** A shared, non-copying lookup on a `Map` whose values are
  not `Copy`.
- **Why sawos needs it.** The builder reads the runner's JSON descriptions.
- **How badly.** Has a workaround (the `var` and `find` spelling, which I
  ran). Nice to have.

### 11.7 `blade build` cannot place its output, and ignores unknown flags

- **Tried.** In a package that builds an sosimg, reproduced:
  `blade build --target riscv32-unknown-none-elf --linker-script /tmp/x.ld`,
  then the same with `--out-dir /tmp/o`, then with
  `--module-path imageplan=/tmp/p`.
- **What happened.** Each printed `Finished .build/riscv32-unknown-none-elf/
  hello-product.sosimg (up to date)` and exited 0. Each flag was ignored
  without a word. `blade/src/cli.saw` knows `--target` and `--force` for
  `build`.
- **Expected.**
  - `--linker-script PATH` overriding `[sos.<triple>] linker-script`;
  - `--out-dir DIR` replacing `.build/<triple>/`, with nothing written
    outside it;
  - `--module-path NAME=DIR`, repeatable, passed through to sawc;
  - and an error for any flag Blade does not know.
- **Why sawos needs it.**
  - On a board that does not translate, every image links each process at
    an address that image assigns, so one driver package is linked
    differently per image.
  - Two images, or two parallel test cases, must not overwrite one
    another's output.
  - The stock root and drivers take generated or product-supplied modules
    (`imageplan`, a pin table).
- **How badly.** Blocks generated link addresses, several children in
  place on the S3, the stock root and config modules. The ignored-flag
  behaviour is worth fixing on its own. The workaround I rejected: an
  `SOS_LD` wrapper that rewrites `-T`, which races on the shared output
  directory.

### 11.8 A git dependency cannot name a package in a subdirectory

- **Tried.** A scratch git repository with a package at `libs/greet`,
  tagged `v0.1.0`, and a manifest naming
  `greet = { git = "<repo>", version = "0.1.0", subdir = "libs/greet" }`.
  Reproduced. Separately,
  `sos = { git = "/Users/shawn/Projects/sawos", version = "0.1.0" }`.
- **What happened.**
  - `error: dependency resolution failed: cannot read manifest for greet at
    .blade/deps/greet-0.1.0`. The clone is the repository's root, and
    `subdir` is not read: `blade/src/manifest.saw` reads `path`, `git` and
    `version` only.
  - For sawos: `no tagged version of sos satisfies 0.1.0`. Resolution is by
    tag only (`blade/src/resolver.saw`, `visit`).
- **Expected.** A way to depend on a package inside a repository, pinned
  by tag or by commit, and an error for an unknown dependency key.
- **Why sawos needs it.** A product depends on `sos`, on protocol packages
  and on drivers, all in subdirectories of one sawos repository at one
  pinned commit.
- **How badly.** Has a workaround, which I ran: path dependencies through a
  `.sawos` link the product's resolver writes. Nice to have, unless sawos
  products should resolve without a resolver.

### 11.9 No portable dependency on a library shipped with the toolchain

- **Tried.** Reproduced:
  - `toml = { path = "<absolute path of the checkout>/libs/toml" }` builds,
    and its lock records the absolute path;
  - `toml = { path = "../.sawlang/libs/toml" }` through a link to the
    checkout also builds.
- **What happened.** Both work, but the first is one machine's path, and
  the second needs a link someone else makes. The toolchain, `$PATH` or
  `SAWLANG_ROOT` or a fetched pin, has a checkout whose `libs/` Blade
  cannot name.
- **Expected.** A dependency spelling for the toolchain's own libraries,
  resolved from wherever the running sawc lives, for example
  `toml = { toolchain = "libs/toml" }`.
- **Why sawos needs it.** The builder depends on `toml` and `imgformat`,
  and the kernel's build already reaches `imgformat` through the checkout.
- **How badly.** Has a workaround: the resolver writes `<sawos>/.sawlang`
  (rule 9.6). Nice to have.

### 11.10 No SHA-256

- **Tried.** `import std.sha256.{Sha256}`. Reproduced, and a search of
  `std/`, `libs/` and `blade/src` found no SHA-256 under any name.
- **What happened.** `ERROR import.unknown-module no module std.sha256`.
- **Expected.** A SHA-256 over `Data`, in std or a `libs/` package.
- **Why sawos needs it.** A board image is sent with its SHA-256 so the
  person flashing it can check it.
- **How badly.** Has a workaround: run `shasum -a 256` through `Command`.
  Nice to have.

### 11.11 What works, for the record

The following were tried and work, and none is a gap:

- A hosted package built by `blade build` in about half a second.
- Absolute-path and linked path dependencies, and an sosimg package outside
  the tree depending on sawos packages through a link.
- Reading and writing a 4 MiB binary file and patching a word at an offset.
- `Path.join`, `Directory.list`, `Env.args`.
- `Map` insert and get on `Copy` values, `Vector.sort`, `StringBuilder`,
  `String.replace`, and `to_uint(radix: 16)`.
- `Command` with arguments and an environment, collecting status and
  output. This includes running Blade to build an sosimg from a Saw program.
- `std.json` parsing a nested object.
- `extern "C"` calls into libc.
- `libs/imgformat`'s constants used by a hosted program to read an sosimg's
  header and segment table, matching Python's `struct` reading byte for
  byte.
- A cold build of Blade with sawc in under a second.

---

## 12. An attack on this design

**12.1 Authority.** The stock root holds every boot row and
`root_system_rights()`, so a description can give a child anything the
board offers: any listed device, any line, even `Shutdown` in a System
sibling. That is by design, because the description is the integrator's
policy. It cannot give a device the board does not list, a quota above the
launcher's, or anything a process claims for itself. The builder prints,
per process, every right above the convention it was given.

**12.2 A wrong description at boot.** Most errors are refused at build.
What is left each stops the machine with a printed reason:

- a device that is not wired, or a pin that is wrong;
- a `Mapping` grant the child's image leaves no room for;
- a tag the child's source disagrees with;
- a limits block the kernel rejects.

**12.3 The flat tier.** On the S3 every capability, quota and window here
is discipline, not a wall. Any process can write the kernel, the reserve,
root's plan and every register. The builder prints the tier at the top of
every build.

**12.4 sawos moving under a product's pin.** Nothing moves until the
product bumps the pin. What can then break: the formats (versioned and
refused when unknown), device names, the `sos` API and the protocols
(caught at compile), board floors and quota defaults (silent unless
printed: the builder prints every effective limit), and the stock root's
lines. A scratch product built by sawos's gate (unit U7) keeps sawos honest.

**12.5 The suite.** It loses the hand-kept scripts, which were readable
in the tree. It gains several children on the S3, the end of the
two-children-and-a-pool refusal, and generated `tests/irq-lines` and
`tests/poolbase_*` facts. One kernel object per board replaces one per
case, so every process case on a board boots the same kernel bytes.

**12.6 The per-board kernel.**

- A limits block makes every slot above a floor reachable only through the
  extent chain. That code is already in every donation case, so it is
  tested.
- The hand-kept geometry numbers (gap 11.4) are a new place for drift. The
  boot check makes drift red at once, but it is still churn whenever a slot
  struct changes.
- An image that wants fewer objects than a floor cannot have fewer.

**12.7 Writing the tools in Saw: bootstrap order.** A fresh machine needs:

1. Python 3 and git;
2. the toolchain resolver, which fetches sawlang;
3. sawlang's `build.py`, which builds sawc;
4. sawc, which builds Blade;
5. Blade, which builds `sosimage`;
6. `sosimage`, which builds images.

Nothing in that chain is new except step 5, and the runner already does
step 4.

**12.8 A compiler bug stopping the build tool.** The builder is now on the
critical path of every gate run, and it is hosted code, a different part of
the compiler and std from the kernel's freestanding path. A sawlang release
that breaks hosted builds breaks sawos's gate even if the kernel compiles.
Defences:

- sawos is pinned, so the break shows up at a pin bump, in that patch, and
  the old pin still works;
- the runner stays Python, so a sawos developer can still read and run the
  harness while the builder is broken.

**12.9 Build time.** Building Blade and a builder of this size takes about
a second each (measured). The builder's own run is dominated by what it
calls (sawc, clang, `ld.lld`, Blade), exactly as the Python runner's is
today.

**12.10 Two languages for a while.** Until steps 3 and 4 of section 10
land, the runner is Python and the builder Saw, and a case crosses between
them as JSON. The JSON is generated by one function and read by one
function, and the transcript diff guards it.

**12.11 Tags written twice.** A process's tags are in `process.toml` and
in its source. The builder cannot read Saw, so `BootSet` finds a
disagreement at the child's first instruction, under the emulator.

**12.12 Toolchain changes.** These are the gaps of section 11. The ones
the design waits on are 11.1 (unit U3), 11.2 (unit U8) and 11.7 (units
U4 and U6). The rest have workarounds that are already in place.

---

## 13. Alternatives rejected

- **Compiling the kernel per image** (the previous revision's
  recommendation; a generated `kconfig` module, which compiles, §16.1).
  - It does reach every limit, the per-body arrays included, and it refuses
    a bad limit at compile time by name.
  - Rejected for the linked table because every image would boot a kernel
    the gate never ran, and every product would compile the kernel. The
    per-body limits it alone reaches are not ones the box needs.
  - If a product ever needs `MAX_HANDLES` raised per image, this is the way,
    and it can coexist: a board could ship two kernels.
- **Root donates the image's limits at start.** Root cannot size a
  donation without the slot geometry. The kernel can apply the table before
  root runs, through the same code, with nothing taken from root's pool.
- **The builder in Python.** It would work today, without gaps 11.1, 11.2
  or 11.4. The owner ruled for Saw where possible, and it is possible for
  everything but the emulator boot.
- **The descriptions in JSON.** `std.json` reads them today, but JSON has
  no comments, so annotated cases and products lose their prose, and no hex
  integers, so addresses would be decimal. It stays the machine-written
  interchange between runner and builder.
- **A quota argument to `ProcessCreate`, or a quota in the sosimg or in
  `[sos]`.** Settled in the previous revision: the `Created`-window shape
  of `Give` and `RestrictRights` fits, and an image must not claim for
  itself.
- **The kernel as a Blade package.** Blade would need kernel linking, board
  scripts, HAL module swaps and `--runtime-provider`, to replace a recipe
  sawos owns.
- **The runner's cases as files now.** Hundreds of files whose comments
  JSON would drop. Revisit after gap 11.1.
- **Root's plan as a binary table in a region row.** A second format
  validated on the target, and a mapping row to read it.
- **Position-independent images; one fixed slot per driver; Blade git
  dependencies on sawos; an `SOS_LD` wrapper; pins sent as a `Setup`
  message from root.** As in the previous revision: respectively, the
  loader never relocates; slots run out; gap 11.8; it races; and it makes
  root protocol-specific.
- **A git submodule instead of a pin.** It works, but it diverges from
  `sawlang.pin`'s idiom and has no environment override.

---

## 14. Cost and order of work

Each unit says what it proves and how it is tested. "Sonnet" and "Opus"
follow CLAUDE.md. Units marked sawlang are the sawlang lead's to schedule.

**U1. The builder in Saw, transcript-identical (sawos; Sonnet, medium;
after SO-96).** `tools/sosimage` builds packages through Blade, compiles
the kernel, writes the region table and stubs, links, and builds the S3
flash image with `check_in_place` and `dump`. The runner builds it after
Blade, writes each case as JSON and calls it. Board facts move from
`ARCHES` into the JSON the runner writes, not yet into `board.toml`. Proves
one build path in Saw. Test: the full gate and both smokes, with every
transcript, total and image size identical to the base.

**S1. Blade's three flags, and refusing unknown ones (sawlang; small; a
release and a pin bump).** Gap 11.7. Can start now.

**S2. `libs/toml` complete enough for the descriptions (sawlang;
medium).** Gap 11.1. Blade's manifest reading should not change behaviour.

**U2. The per-board kernel and the linked limits table (sawos kernel and
spec; Opus for the spec, Sonnet to build; medium).** This is the scratch
prototype made real:

- the boot table at version 4 with a limits block, the reserve validated
  against `map_source_ok` and against every row;
- the board info and slot geometry exports, with literal geometry and the
  boot check (gap 11.4);
- quota defaults read from the table, and the split of the asserts (rule
  6.4);
- the builder's reserve sizing and refusals;
- one kernel object per board in the runner;
- spec §2.8, §2.9 and §12.

Test:

- `process_donate` and a `process_donate_limits` asserting the fourth
  process;
- a box-sized case with five processes and no donation;
- a refused image for each rule 6.4 relation;
- the gate unchanged.

Independent of S1 and S2.

**U3. The description format (sawos; Opus for the format, Sonnet to build;
medium; after S2).** `board.toml`, the image TOML loader, the `[rows]`
devices and pools (absorbing SO-77's U1), rule 5.4's checks, the CLI's
`build`, `run` and `test`. Test: `uart_service_ns16550` from a file,
byte-identical to its case, and one negative per check.

**U4. Generated addresses (sawos; Sonnet, medium; after S1's pin bump and
U3).** Rules 5.5 to 5.8; the hand-kept child scripts go; §5.5, §5.6 and
§5b.1's sentences. Test: the S3 smoke gains a two-child and a four-child
case.

**U5. The quota door (sawos kernel and spec; Opus for the spec, Sonnet to
build; medium).** Rule 7.2. Tests on every profile:

- a raised row met exactly;
- after start, `BadState`;
- above the launcher's own limit, `BadArg`;
- past the free rows, `NoResource`;
- a grandchild capped by its launcher.

Independent.

**U6. Declarations and the stock root (sawos; Opus for `bootplan`, Sonnet
to build; medium-large; after U4 and U5).** `process.toml`, `BootSet`,
`bootplan`, the `imageplan` generator, the stock root, and the UART drivers
brought to rule 4.5. Test: `uart_service_stock`, and negative builds for
each declaration mismatch.

**U7. Products (sawos; Sonnet, small; after U3, finished after U6).**
`sawos.pin`, `tools/sawos.py` (Python), the `.sawos` link, the
`.sawlang` link in `tools/toolchain.py`, and a gate case that copies a tiny
product out of the tree, builds it and boots it.

**S3. `std.process`: a working directory, input, streamed output, kill and
a deadline (sawlang; medium).** Gap 11.2.

**U8. The harness in Saw (sawos; Sonnet, medium).**

- **Judging:** `sosimage check` can land at any time after U1.
- **Booting:** after S3, or earlier if a probe of the socket route holds.

Test: the full gate's transcripts identical with the runner booting through
`sosimage`.

**Later, unscheduled:** the case table as files (section 10, step 4), and
gaps 11.3, 11.4, 11.5, 11.6, 11.8, 11.9 and 11.10, which each delete a
workaround when they close.

**Where SO-77 and SO-78 sit.**

- SO-77 U0, the board probe, can run now.
- SO-77's GPIO and PWM drivers come after U4 and U5. They are written as
  packages in `drivers/` with `process.toml` and config inputs (S1), each
  smoke case an image description with a test client as its root.
- SO-77 U1 is absorbed by U3, and SO-77 U5 is replaced by U2, U4 and U5.
- SO-78 is the box repository, after U6, U7 and the drivers.

**The order, at most two agents at once across both repositories:**

1. SO-96, beside S1.
2. U1, beside S2.
3. U2, beside U5.
4. U3, beside S3.
5. U4, beside SO-77's GPIO driver.
6. U6, beside the PWM driver.
7. U7, beside U8.
8. SO-78.

---

## 15. Questions for the owner

1. **The linked limits table, recommended in section 6.** Do you accept
   these terms?
   - The handle table, the boot set, the grant record and arm64's domain
     pool stay per board.
   - An image can raise a slab and not lower it.
   - The slot geometry is a hand-kept number per board, checked at every
     boot, until sawlang closes gap 11.4.

   *Recommended: yes.*
2. **Where does the kernel's reserve come from?** A RAM range the builder
   assigns from the board's memory, like a pool, or a linker-sized section
   of the kernel's own RAM. *Recommended: the builder's range.* It needs no
   HAL accessor and the prototype used it. On the S3 it competes with
   process RAM, and the builder accounts for it there.
3. **Who routes the servo pads and turns on LEDC's clock?** *Recommended:
   the PWM driver*, given its four windows through the quota door, so the
   stock root stays generic.
4. **How does a driver get its pins?** *Recommended: a module the image
   supplies at build time.*
5. **The description format.** TOML through a completed `libs/toml`
   (S2), or JSON today. *Recommended: TOML*, with JSON kept as the
   machine-written runner-to-builder format. This puts S2, a sawlang unit,
   before the first hand-written description.
6. **May sawlang take S1, S2 and S3 ahead of the box?** *Recommended: S1
   and S2 yes.* The box needs both. S3 can follow, because the Python
   harness serves until then.
7. **The runner's future.** *Recommended:* the builder now, judging and
   booting when S3 lands, and the case table only after both, as section
   10 describes. Not a rewrite in one go.
8. **The resolvers stay Python.** *Recommended: yes*, for the reason in
   section 10: Python is needed to build the Saw compiler anyway.
9. **How does a product find sawos?** *Recommended: a pin with a resolver
   and a `.sawos` link.*
10. **What does the stock root do when a child ends?** *Recommended:*
    report it; stop the machine when a child marked `shutdown` ends or all
    have; no restart in v1.
11. **Where does the box live?** *Recommended: its own repository*, with
    sawos's gate building a scratch product.

---

## 16. What I verified, what I read, what I could not check

### 16.1 Verified by running (scratch only; no repository file changed)

**The linked limits table.** A scratch copy of the tree under
`scratchpad/SO-95/proto/` with `apply_boot_limits`, the runner's limits
block and `process_donate_limits`. I ran it under the suite lock, which I
took at `/private/tmp/claude-501/sawos-suite-lock` and gave back.

- On riscv32 and arm64 the root saw "a fourth process existed BEFORE the
  donation", and the unchanged `process_donate` passed.
- Process body units measured 0x324 (riscv32) and 0x590 (arm64).
- An exported literal `sos_slot_geometry` survived `--gc-sections`. I read
  it back (`24030000 04000000`) from the ELF's `.data` by its symbol, and
  the boot check passed.
- `sizeof` in a static, and in a `static_assert` of `ProcessBody`, were
  refused with the errors in gap 11.4.

**Hosted Saw**, in `scratchpad/SO-95/sawprobe/`:

- the probes behind every entry of section 11, each a Blade package built
  with the lead's 0.23 `blade` and `sawc-stage2`;
- the TOML findings of 11.1;
- binary I/O, paths, the directory walk and its workaround;
- maps and sorting;
- `Command` with arguments and an environment, and through `sh -c` for a
  working directory;
- driving `blade build` of an sosimg from a Saw program;
- JSON parsing;
- reading an sosimg's header and segments with `libs/imgformat`;
- libc through `extern "C"`;
- the missing `std.process` methods, `{:x}` and `std.sha256`;
- Blade ignoring unknown flags and the `subdir` key;
- Blade's tag-only resolution against sawos;
- a cold Blade build in 0.8 s.

Blade wrote `Saw.lock` and `.build/` only into the package it built, never
into a dependency (`git status` of `libs/` clean).

**From the previous revision:**

- a package outside the tree building an sosimg against sawos through a
  link;
- a generated module sizing arrays and feeding `static_assert`s across a
  module boundary;
- `ld.lld`'s `INCLUDE` through `-L`;
- Python's `tomllib` reading the same shapes `libs/toml` cannot.

### 16.2 Read, not run

Everything in section 1, from the files named there; the donation path
(`slab_donate`, `slab_geometry`, `slab_install`) and `Slab<T, N>`; that the
boot set is FIFO; Blade's manifest, CLI, link and resolution code; that
`bin/sawc` runs `compiler/tools/build.py`; the S3 RAM and flash arithmetic.

### 16.3 Not checked

- The prototype was not run on riscv32-flat or the S3. The flat profile is
  the riscv32 kernel with a different `hal`, and the S3 has its own HAL
  and its own smoke.
- The prototype does not check that the reserve and the boot rows are
  disjoint, and does not put quota defaults in the table. Both are U2's.
- The S3's slot geometry is not measured, so the box's reserve size in
  §5.3 is not computed.
- Exporting the board facts as an `@export` static of imported HAL
  statics is not compiled. I compiled only literals.
- The full gate was not run, on the scratch copy or the tree. U1's
  "transcript-identical" is a requirement, not a result.
- The socket route for the emulator harness.
- Any hosted Saw program on Linux. All of this was macOS arm64.
- Espressif's `ld.lld` with `INCLUDE`.
- Whether the box's processes fit their regions; none is written yet.
- The device addresses, which are SO-77's from ESP-IDF headers.
- Running the scratch runner used the lead's sawlang checkout through its
  `bin/sawc` shim, which runs `compiler/tools/build.py --quiet` first. I do
  not know whether that wrote build stamps under that checkout's ignored
  `.build/`. The hosted probes used `.build/sawc-stage2` directly.
