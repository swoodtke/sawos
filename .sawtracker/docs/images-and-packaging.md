# SO-95: images and packaging (design)

For the owner and the reviewer, before anything is built. Written against
main at `e7b9a20c`, with `tests/svc-uart-ns16550`, `tests/svc-uart-pl011`
and `tests/uartproto` read as if SO-96 had moved them to `drivers/`.

Every claim about the code names the file and function it comes from.
Section 14 says which claims I checked by running something, which I only
read, and which I could not check.

---

## 0. Verdict

1. The seven-part shape holds, with three changes: limits reach the kernel
   by compiling it per image (it already is), a per-process quota door is
   added to the kernel, and a driver's pins and other instance settings
   arrive as a build-time module, not as a message from root.
2. Boards stay in sawos as `hal/<board>/board.toml`. A product repository
   holds image descriptions (`images/*.toml`), its own packages, a
   `sawos.pin`, and nothing else from sawos.
3. A process states its boot interface in `process.toml` beside its
   `Saw.toml`: tags, windows, lines, endpoints, quotas, build inputs. The
   builder refuses an image that does not supply them.
4. One builder, `tools/sosimage/`, is a library the test runner also uses.
   Its first unit moves the runner's stitching into it with no change to
   any transcript, so it pays for itself before any product exists.
5. The stock root is table-driven from a Saw module the builder generates.
   Nothing is parsed on the target. A product may bring its own root.
6. Doing it right needs one sawlang change: three `blade build` flags
   (`--linker-script`, `--out-dir`, `--module-path`). The pinned compiler
   needs none; I compiled the per-image limits module with it.
7. The kernel changes twice: `kcore.limits` reads a generated `kconfig`
   module, and `ProcessOp.QuotaSet` lets a launcher set a `Created` child's
   quota rows, never above its own.
8. SO-77's unit U5 is replaced by this design's units 4 to 6, and SO-78
   becomes the first product repository.

### 0.1 Where the rulings, the spec and the code disagree

None of the owner's rulings contradicts another. These are the places where
what the owner asked for meets text or code that says otherwise.

- **The spec calls the hand-kept linker scripts deliberate.** §5.5 says
  riscv32-virt "keeps one linker script per resident child image ...
  deliberately", and §5b.1's table calls them "the living identity-tier
  demonstration". The owner wants the per-slot scripts to stop being
  hand-kept. The demonstration survives: there is still one script per
  resident image, but the builder writes it. Both sentences change.
- **§2.9 says "there is no per-create quota argument in v1",** and §11
  repeats it as a known limit. Per-process limits need a door. Rule 7.2
  proposes one and its spec text.
- **SO-77's design has root write the LEDC clock gate and route the servo
  pads** (`.claude/lead/so77-design.md` §3.3). A generic stock root cannot
  carry device-specific code. Rule 6.4 and question 3 resolve it: the PWM
  driver is given the four windows it needs through the quota door.
- **The issue's sketch has a product depend on sawos drivers.** Blade's git
  dependencies cannot do that: they resolve by tag and clone a whole
  repository whose root must be the package (`blade/src/resolver.saw`,
  `visit`; `blade/src/git.saw`, `clone_tag`). sawos's packages sit in
  subdirectories. Rule 8.2 uses path dependencies through a link instead.
- **The driver's statement of its needs, and "the launcher grants; an image
  never claims".** These are reconciled, not contradictory. A driver's
  declared quota is a requirement the builder checks against the image. It
  is never something the kernel honours. Only the launcher's `QuotaSet`
  grants (rule 7.3).
- **A stale comment.** The header of `kernel/core/limits.saw` says "a
  derived static built from an imported const is not a compile-time length,
  so a derived size and its operands cannot be split across modules". With
  the pinned compiler that is no longer true; I compiled the split (§14.1).

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
  `<package>/.build/<triple>/<name>.sosimg`.
- **The kernel is compiled per case.** `_build_elf` compiles `case["src"]`
  with sawc directly: `--freestanding --no-hidden-alloc --runtime-provider`
  and `--module-path` for `kcore`, `hal=hal/<board>/kernel`, `imgformat`
  (from the sawlang checkout), `sosrt` and `sosabi`, plus the board's extra
  modules (`rv32core`, `rv32virt`). It links with the board's `virt.ld`.
- **Root is stitched in.** `_stitch_root_image` assembles
  `kernel/rootimg.S`, whose `.incbin "root.sosimg"` is pointed at a per-case
  staging directory, into `.payload`.
- **Children and the region table are generated.** `_stitch_regions` writes
  `<case>.regions.S`: each child sosimg `.incbin`'d into `.childimg` between
  `_sos_child<i>_start` and `_end`, then the `.regions` section from
  `_region_table_section` (header: magic, version 3, row count, boot flags).
- **Row order is the runner's contract.** `_region_rows` says so in its
  docstring: each child's blob, each child's destination, then the pool,
  then the device window. It refuses two children and a pool together,
  because the second child's destination and the pool share a base.
- **The ESP32-S3 has its own path.** `_esp32s3_smoke` reuses the stitchers,
  compiles its kernel itself (`kernel_obj`), and links with
  `hal/xtensa-esp32s3/kernel/esp32s3.ld`. `flash_image` writes a 4 MiB
  flash image from the ELF's load segments, checks the direct-boot magic,
  and calls `check_in_place`, which reads each resident image back and
  refuses a segment not linked at its own flash bytes. `--emit-flash CASE
  --out PATH` cuts the image after its last byte, prints its SHA-256 and the
  `esptool ... write-flash 0x0` line.

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
  `MAX_MEMORIES + MAX_IOMEMORIES` rows, and one boot record per row, which
  must fit the boot set (`boot_handle_room`: `MAX_HANDLES` records, 16).
  Kinds are `Ram` and `Device` (`RegionKind`). Bit 0 of the flags turns off
  the tick.
- **Limits** are constants (`kernel/core/limits.saw`): `MAX_PROCESSES` 3,
  `MAX_WAITERS` 4, `MAX_PIPES` 4, `MAX_HANDLES` 16 and the rest, with
  `static_assert`s tying them to the HAL. Every slab except the handle
  table and the grant record is donatable (`SlabKind`).
- **Quotas.** `install_quota_limits` (`kcore.objects`) gives a child the
  `DEFAULT_QUOTA_*` numbers and root `QUOTA_UNLIMITED`. The mapping row is
  clamped to the grant rows the process has left after its image.

### 1.4 What cannot be done from outside the repository

- **Build a kernel.** The module paths in `_build_elf` point into this tree,
  and the recipe exists only in the runner.
- **Build a system image.** The stitchers are runner functions keyed by
  case dictionaries.
- **Link a sawos package at an address of the image's choosing.** Blade
  takes the script from the manifest alone. `blade build` accepts only
  `--target` and `--force` (`blade/src/cli.saw`), and the link is
  `ld.lld -T <manifest script>` with no `-L` (`blade/src/builder.saw`,
  `build_sos_image`). So a script cannot `INCLUDE` a generated fragment
  either (§14.1).
- **Build one package for two link addresses at once.** The output path is
  fixed per triple (`BuildLayout`, `.build/<triple>/<name>.sosimg`), so two
  images building the same driver for different slots would overwrite each
  other. The runner runs cases in parallel.
- **Run more than one child in place on the ESP32-S3.** `esp32s3.ld` has one
  child slot. Every child's blob goes into the one `.childimg` section, and
  only the first sits at the slot's start (`hal/xtensa-esp32s3/user/
  child.ld`'s header says the same).
- **Change a limit or a quota** without editing `limits.saw` for every image.

What can be done from outside today: a package in another directory can
depend on sawos packages by path and build an sosimg. I built one (§14.1).

---

## 2. The parts

The seven parts of the lead's sketch, with what this design keeps and
changes:

1. **A board description in sawos.** Kept. It becomes the build-side
   source of the board's facts, and the kernel checks the ones it shares
   with the HAL (section 3).
2. **An image description in the product.** Kept. It also carries each
   process's build inputs, such as a driver's pin table (section 5).
3. **A driver's statement of its needs.** Kept and widened: it is the
   process's whole boot interface, every process launched by the stock
   root has one, and it lives in `process.toml`, not `Saw.toml` (section
   4).
4. **One builder, which the runner uses too.** Kept (section 9).
5. **A table-driven root.** Kept. The table is a generated Saw module, not
   data the target reads (section 8).
6. **A pin on sawos.** Kept. The resolver links the pinned tree into the
   product as `.sawos` (section 9).
7. **The product's own test.** Kept. A test is a small TOML file the
   harness reads (section 9).

Limits, the quota door and link addresses were open in the sketch. Rules 6,
7 and 5 settle them.

---

## 3. The board description

**Rule 3.1.** Each board in sawos has one `board.toml` in its HAL directory
(`hal/xtensa-esp32s3/board.toml`, `hal/riscv32/board.toml`,
`hal/arm64/board.toml`, and the flat profile's, which names the riscv32
board as its base). It is the single build-side source of the board's
facts. The runner's `ARCHES` tables and the hand-kept user linker scripts
are generated from it or deleted.

**Rule 3.2.** It states:

- the triple, the tier, the HAL directory and its extra kernel modules, the
  C flags, and how to run it (the emulator and its arguments);
- the **placement kind**: `copied` (riscv32: a child's blob is anywhere and
  is copied to its destination, which it is linked at), `in-place` (the
  ESP32-S3: code and read-only data run from a flash slot, writable data is
  copied to a RAM region), or `translated` (arm64: every image links at one
  base and a destination is any frames);
- the **process RAM** the build may divide (on the S3, `hal.RAM_BASE` to
  `hal.RAM_TOP`), root's region, the protection grain, and the default
  region and arena sizes;
- on an `in-place` board, the **flash windows** and the flash range for
  image slots, with a default slot length;
- each **device** by name: base, length, a `compatible` string, its
  interrupt line, and whether the kernel uses it (the console);
- **reserved lines**: the kernel's tick, which the kernel refuses anyway.

**Rule 3.3.** An image names devices and lines by these names, never by
address or number.

**Rule 3.4.** Facts the kernel also holds stay in the HAL's `lib.saw`
(`RAM_BASE`, `ROOT_LOAD_BASE`, `DEVICE_GRANT_BASE`). The builder writes the
values it used into the generated `kconfig` module (rule 6.2), and `kcore`
asserts each equals the HAL's. A `board.toml` that drifts from its HAL
fails the kernel's compile with the fact's name.

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

[memory]
process-ram = { base = 0x3FCB_0000, top = 0x3FCF_0000 }
root-region = { base = 0x3FCB_0000, top = 0x3FCC_8000 }
grain = 4
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

The device numbers are SO-77's (`so77-design.md` §2.1, §3.1), which cite
ESP-IDF's headers. `SYSTEM_CLK` is the smallest naturally aligned window
covering `PERIP_CLK_EN0` (+0x18) and `PERIP_RST_EN0` (+0x20).

---

## 4. A process's declaration

**Rule 4.1.** Every process the stock root launches has a `process.toml`
beside its `Saw.toml`. It is sawos's file, read by sawos's builder, so a
new field is never a toolchain change. (Blade refuses an unknown
`[sos.<name>]` table, `check_sos_subtables`, and its emitter is sawlang's.)
A process launched only by a hand-written root may omit it.

**Rule 4.2.** It declares the process's **boot set**, in the order the
launcher gives it: each record's name, tag and kind.

- `system`: a masked System sibling. Its tag is the start tag. Its rights
  are the §12 convention mask unless the declaration asks for more, and the
  image must agree to any more.
- `window`: an `IoMemory`, with the `compatible` strings it accepts.
- `line`: a bound `Interrupt`, with its `compatible` strings.
- `serve`: the outlet of a connection it serves, with the protocol, named
  by the package that defines it (`gpioproto`).
- `client`: an inlet onto a connection another process serves, with the
  protocol.
- `memory`: a RAM allocation or a pool row.

**Rule 4.3.** It declares the **quota rows it needs** above the image's
defaults. The builder refuses an image that grants less (rule 7.3).

**Rule 4.4.** It declares its default **region and arena** sizes, which an
image may raise, and the boards it builds for.

**Rule 4.5.** A driver finds its registers through `Mapping.base()`, never
through an address constant. That keeps it free of the board's map and
correct on a translating tier. `svc-uart-ns16550` is fixed to this rule in
unit 7.

**Rule 4.6.** It declares its **build inputs**: modules the image must
supply, by name (`gpioconfig`). The builder passes each to the package's
compile as `--module-path NAME=DIR` (rule 9.4). A driver's pins, servo
ranges and other per-instance settings are such a module. They are typed
Saw statics, checked by `static_assert` at compile time. Nothing is sent at
run time and nothing is parsed.

**Rule 4.7.** The tags in `process.toml` and the program's source must
agree. The `sos` module gains a `BootSet` helper that drains every record
into a small table and answers `take(tag:)`. A missing or extra record is an
error the process prints with the tag, at its first instruction, under the
emulator. The builder cannot read Saw constants, so this check is at run
time (attack 10.9).

The GPIO driver's declaration, as SO-77's design would have it:

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
`images/` directory, `format = 1`. The builder refuses a format it does not
know. Python's standard `tomllib` reads it.

**Rule 5.2.** It states:

- `[image]`: the name, the board (by `board.toml` name), the tick;
- `[limits]`: image-wide slab floors and quota defaults, each a default
  when absent (rule 6.1);
- `[root]`: `stock`, or a package path for a hand-written root;
- one `[[process]]` per child, in launch order: a name, its package, its
  region and arena if not the declared defaults, each `window` and `line`
  bound to a board device by name, each `client` bound to another process's
  `serve` endpoint, each build input bound to a file, its quota rows, any
  narrowed rights ceilings, and `on-exit`;
- extra rows a hand-written root needs: `devices` and `pools`, in order.

**Rule 5.3.** A package is `sawos:<path>` (resolved in the pinned sawos
tree) or a path relative to the product repository's root.

**Rule 5.4.** The builder refuses, before compiling anything, an image in
which:

- a device or line name is not on the board, or a line is reserved;
- a window's or line's device does not match its `compatible` strings;
- a declared boot record is not bound, or a binding names no declared
  record;
- a `client` and its `serve` name different protocol packages, or either
  package does not depend on that protocol package (read from `Saw.toml`);
- two records of one process share a tag;
- a quota row is below what the process declares it needs, or above what
  the kernel allows (rule 7.2);
- the regions, slots and pools do not fit the board's process RAM and
  flash, or overlap;
- the stock root's peak handle count exceeds `MAX_HANDLES` (it is a
  function of the plan, computed by the builder).

What it cannot refuse is in attack 10.1.

### 5.2 Link addresses: who assigns them

**Rule 5.5.** The builder assigns every address. Root's region is the
board's. Each child, in description order, gets a RAM region from the
board's process RAM after root's, of its stated size, aligned to the grain.
Pools follow. On an `in-place` board each child also gets a flash slot,
from the slot range, of its declared or default length. On a `translated`
board every image links at the board's one base, and destinations are
regions of any frames.

**Rule 5.6.** Each process is linked with a script the builder writes from
one template per board and placement kind (`hal/<board>/user/process.ld.in`)
and the assigned numbers: the slot, the region, the arena
(`PROVIDE(ARENA_SIZE = ...)`), and `LENGTH` up to the stack. The scripts'
`ASSERT`s and `check_in_place` stay. `child.ld`, `child2.ld`, `child3.ld`
and the S3's `child.ld` go. One default script per board stays, for a
standalone `blade build` of a package.

**Rule 5.7.** On an `in-place` board the kernel's script `INCLUDE`s a
generated `image_slots.ld`. It places root's image, each child's
`.childimg.<i>` section at its slot, and the read-only data after the last
slot. The builder links the kernel itself, so it can pass `-L`.

**Rule 5.8.** A package is linked per image. Its output goes to the image's
build directory (`--out-dir`), never to `<package>/.build`, so two images,
or two parallel cases, never share an artifact.

**Rule 5.9.** The region table's rows keep §12's order, extended to many
pools and devices: each child's blob, each child's destination, each pool,
each device, each group in description order. A row ordinal is the tag root
drains it under. The current runner's order is the special case of one
pool and one device, so every existing root still reads its rows.

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
pipes = 4

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
windows = { ledc = "LEDC", gpio = "GPIO", iomux = "IO_MUX",
            clock = "SYSTEM_CLK" }
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

What the builder makes of it:

- **Rows.** Four blobs (flash, in each slot), four destinations, and the
  devices the processes name, once each in first-use order: GPIO, IO_MUX,
  LEDC, SYSTEM_CLK. Twelve rows, within the boot set's 16 and within
  `MAX_MEMORIES + MAX_IOMEMORIES`.
- **RAM.** The board gives processes 256 KiB. Root keeps its 96 KiB region
  (`hal.ROOT_LOAD_BASE` to `ROOT_REGION_TOP`). The children take 24 + 24 +
  32 + 24 = 104 KiB, each with the kernel's 16 KiB stack at its top
  (`ROOT_STACK_LEN`). 56 KiB is left, unused here. Today's 96 KiB child
  region with a 48 KiB arena would fit one child, which is why region and
  arena are per process.
- **Flash.** Four slots of 128 KiB from 0x18_0000 end at 0x20_0000, where
  the kernel's read-only data goes today.
- **Kernel.** `kconfig` says `MAX_PROCESSES = 5`, `MAX_WAITERS = 6`,
  `MAX_PIPES = 4`. `MAX_PROCESSES <= hal.PROT_DOMAIN_SLOTS` (256 on this
  board) holds at compile time. Every other limit is sawos's default.
- **Root's plan.** Create the four; give GPIO a System sibling (tag 1), the
  GPIO row (2), the IO_MUX row (3), line 16 bound (4), and the outlet of a
  new connection (5), minting a sibling of the GPIO and IO_MUX rows first,
  since PWM is given them too. Set PWM's `Mapping` and `IoMemory` rows to 4
  before giving it its four windows. Give the controller a System sibling
  and two inlets, minted with the convention's client mask. Start them in
  order. Supervise; when the controller ends, stop the machine with its
  exit code.

`config/pwm.saw`, the input the PWM driver declares:

```saw
//! The box's servos: pad, LEDC channel and the bench-measured safe range.
import pwmconfig_types.{Servo}

public static SERVOS: [Servo; 2] = [
    Servo(pad: 41, channel: 0, min_us: 500, max_us: 2500),
    Servo(pad: 42, channel: 1, min_us: 500, max_us: 2500),
]
```

(`pwmconfig_types` is a library in the driver's protocol package. The shape
is SO-77's to settle; the mechanism is this design's.)

`images/box-qemu.toml` is the same file with the two drivers replaced by the
stand-ins SO-78's notes call for (`gpio-fake`, `pwm-fake`, speaking the
same protocols), because the emulator models no GPIO input and no LEDC
(`hal/xtensa-esp32s3/ABI.md` §9).

### 5.4 Worked example: `uart_service_ns16550`

The case today (`tools/sos_runner.py`): `root_pkg` is `tests/uart-service`,
`children` are the NS16550 driver and `tests/svc-client`, `device` is true,
`tick` is absent, and the transcript asserts `SOS: boot regions={five}`.

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

The root is hand-written, so no process needs a `process.toml` and the
builder checks no boot interface. The table is the same five rows in the
same order: two blobs, the destinations at 0x8024_0000 and 0x8028_0000 (the
builder packs regions of the board's default, 256 KiB, from root's top),
and the UART window. The client is linked at 0x8028_0000 because it is
listed second, as today, but the script is generated, and
`tests/svc-client/Saw.toml` stops warning about its position.

The runner keeps its case dictionary. Unit 1's adapter turns the dictionary
into the same in-memory `Image` this file loads into, and unit 2 holds the
two to byte-identical output. Rewriting the runner's cases as files would
be churn with no reader (rejected, §11).

The same image with the stock root replaces `[root]` with `stock`, gives the
driver a `process.toml` declaring system 21, window 22, serve 23 and line 24
in that order, and binds `clients = { uart = "driver.uart" }` on the client.
The driver's code does not change: it already drains window, outlet and line
in that order, and the stock root gives in declaration order (rule 8.1).

---

## 6. How limits reach the machine

**Rule 6.1.** An image's `[limits]` sets the kernel's slab floors and the
children's quota defaults. Each is sawos's default when absent. The keys
map one to one onto `kcore.limits`: `processes`, `records-per-process`,
`threads`, `events`, `waiters`, `interrupts`, `timers`, `memories`,
`free-ranges`, `iomemories`, `mappings`, `pipes`, `handles`, and
`quota.<kind>` for each `DEFAULT_QUOTA_*`. `MAX_GRANT_ROWS`,
`PIPE_INFLIGHT`, the extent constants and the frame stride stay fixed: they
are the HAL's, derived, or properties of the table rather than of a
deployment.

**Rule 6.2.** The builder writes them into a generated module, `kconfig`,
and compiles the kernel with `--module-path kconfig=<dir>`. `limits.saw`
imports its floors from `kconfig` and keeps every derivation and
`static_assert` it has. sawos ships a default `kconfig` (today's numbers),
which the runner uses for every case that states none, so the suite's
kernels are unchanged.

**Rule 6.3.** A limit the machine cannot hold fails the build, not the
boot: a `static_assert` names it (a floor above `hal.PROT_DOMAIN_SLOTS`, a
default at or above its slab, a mapping default past the free grant rows),
or the kernel's `.bss` overruns its RAM and the link fails ("will not fit
in region"; `esp32s3.ld` reserves 144 KiB for it).

**Rule 6.4.** Donation stays. `SlabDonate` is how a running system grows
past its floors. The floor is now the image's rather than the tree's, so
§2.8's "the compiled limits are a floor" still holds.

### 6.1 The three ways, weighed

**Root donates at start.**

- Kernel change: none to donate, but a new info op so root can size a
  donation (below).
- Who sizes the memory: root, which cannot, because slot sizes are
  kernel-internal.
- What it reaches: the donatable slabs only. The handle table and the grant
  record are arrays in every body and cannot be donated.
- A wrong number shows up at run time, as `NoResource`.
- Cost: RAM split off root's pool, for good.

**A limits record in the boot table, applied before root runs.**

- Kernel change: a new record, a new boot phase, and the kernel carving its
  own storage before root exists.
- Who sizes the memory: the kernel, from a reserve row the build must size,
  which it cannot do either.
- What it reaches: the donatable slabs only.
- A wrong number shows up at boot, as `fatal_image`.
- Cost: as donation, plus a format to version.

**A kernel compiled per image.**

- Kernel change: `limits.saw` imports one generated module.
- Who sizes the memory: the compiler.
- What it reaches: every limit, `MAX_HANDLES` included.
- A wrong number shows up at build time, by name.
- Cost: the kernel's `.bss` grows by exactly what is asked.

Root cannot size a donation: `tests/process-donate` says so ("a donated
region buys `len / sizeof(ProcessBody)` bodies, that size differs per
profile"), and nothing reports a slot's size to userspace. The kernel is
already compiled per case (`_build_elf`), so compiling it per image adds no
build step. **Recommended: the kernel compiled per image**, with donation
kept for run-time growth.

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
>   which the default set carries. It is a launcher's configuration and
>   follows `Give`'s rule: the named process must be `Created`, and naming
>   one that is not is a `BadState` fault, since the launcher that started
>   it knows. A kind the op does not know is a `BadArg` fault.
> - The limit may not exceed the caller's own limit for that kind, and a
>   limit above it is a `BadArg` fault: the caller can read its own
>   (`QuotaGet`, below). Root's rows are unlimited, so root is bounded by
>   the machine alone. So a quota, like a ceiling, never grows down a chain
>   of launchers.
> - A `Handle` limit may not exceed `MAX_HANDLES`, which the caller can
>   know, and is a `BadArg` fault above it. A `Mapping` limit past the grant
>   rows the child's image left free answers `NoResource` and sets nothing,
>   because a launcher cannot read the child's image.
> - A limit below what the child already uses (handles given before the
>   call) is a `BadArg` fault. A launcher sets quotas before it gives.
> - `ProcessOp.QuotaGet(kind)` answers the named process's limit and use as
>   two words through the copy-out funnel, gated by `ProcessRight.Stats`,
>   as `Ceiling` is.

And §2.9's bullet "A child gets `kcore.limits`' documented defaults; there
is no per-create quota argument in v1" becomes:

> - A child starts with the image's quota defaults (`kcore.limits`, which
>   the build sets per image), and its launcher may set any row before
>   it starts (`ProcessOp.QuotaSet`, §2.7), up to the launcher's own limit.
>   There is no quota argument to `ProcessCreate`.

§11's known limit "there is no per-create quota argument" goes.

**Rule 7.3.** The builder refuses an image whose grant for a row is below
what the process declares it needs (rule 4.3). It applies `QuotaSet` only
for rows the image or the declaration raises above the default.

**Rule 7.4.** "How many memory ranges a process may map" is the `Mapping`
row, and its hard bound is `hal.GRANT_ROW_BUDGET` less the image's rows:
8 - 4 = 4 on riscv32 and the S3. So the PWM driver's four windows fit
exactly. A driver wanting five needs a board change. On the S3 the budget
has no hardware behind it (`hal/xtensa-esp32s3/kernel/lib.saw`: "No
hardware wall stands behind either here"), so that is a number to raise,
at a cost in every body's grant record.

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
4. **Row kinds and rights.** `Ram` mints an allocation, `Device` an
   `IoMemory`, `Pool` a pool, each charged to root with its kind's rights
   (§12, "The kind column types every row"). `Pool` is specified and not
   built: `RegionKind` in `kcore.process` has `Ram` and `Device`, so a
   pool row is a `Ram` row today, as the runner writes it.
5. **Boot flags.** Bit 0 turns off the tick (§12).
6. **Root's end is the machine's.** Root's exit stops the machine with its
   code, and a killed root with the kernel's own failing status (§8).
7. **Every other process** is root's to create, from images it holds, with
   `process_create(image:memory:)` (§12).

### 8.2 Generated per image (the builder's output; new text for §12)

8. **The region table**, in rule 5.9's order. Proposed §12 text, replacing
   "The tree's stitcher (`tools/sos_runner.py`) writes, in this order...":

   > The image builder (`tools/sosimage`) writes, in this order, which
   > root's config reads as tags: one image blob per child, one destination
   > per child, each pool, then each device window, each group in the order
   > the image description lists them.

9. **The plan module, `imageplan`**, for a root that imports it: for each
   child its name, blob and destination ordinals, ceilings, quota rows, the
   records to give in order (each one's tag, source and keep mask), its
   start tag and its exit policy; and the connections. Its types live in a
   small sawos library, `bootplan`, which the stock root and the generated
   module both import. A replacement root in Saw may import it too, or
   ignore it and number rows itself, as every root in `tests/` does.

### 8.3 Convention among userspace programs (new text for §12)

What a child may assume of whoever launched it, if its launcher follows
the convention. The stock root does. Proposed §12 text, beside the v1
protocol conventions:

> **The launch convention** (userspace, not kernel surface). A launcher
> that follows it:
>
> 10. gives a child exactly the records its declaration names
>     (`process.toml`), under their declared tags, in declared order, and
>     nothing else, before it starts the child (§2.7 freezes the set at
>     `start`);
> 11. starts the child with its System record's tag, so the System sibling
>     arrives in the first argument register, masked with
>     `Debug | ProcessSelf | ClockGet | Transfer` unless the image grants
>     more (§12, "A child bootstraps exactly as root does");
> 12. sets the child's quota rows to at least what it declares, before any
>     give;
> 13. gives a window, a bound line and an endpoint as capabilities: the
>     child maps its own windows and finds them through `Mapping.base()`;
> 14. gives a client an inlet minted with `Post | Transfer`, and a server
>     the outlet (the uart-service shape);
> 15. keeps the child's Process handle, may kill it, and is the one who
>     learns of its death;
> 16. gives the child a region of at least its declared size, with the
>     kernel's stack at the top.

And one kernel sentence the convention leans on, which the spec does not
yet state although the kernel does it (`queue_boot_handle` appends at
`boot_count`; `boot_handle_next` reads at `boot_next`), for §2.7's `Give`
list:

> - `BootHandleNext` answers records in the order they were queued: root's
>   in row order, a child's in the order its launcher gave them. The record
>   `Start` consumed is never answered.

### 8.4 The stock root

**Rule 8.1.** sawos's `root/` becomes the stock root. It imports
`imageplan` and does exactly this:

1. derive its Process handle; drain rows as each step needs them;
2. for each child in order: create it from its blob and destination,
   release both, apply its ceilings and its quota rows;
3. create each connection; give each child its records in declared order,
   binding each line and minting each System sibling and each shared
   window's sibling as it gives;
4. start each child in order;
5. attach every child to one Waiter and print one line per death:
   `SOS root: <name> ended status=<word>`;
6. stop the machine with a child's status when a child marked
   `on-exit = "shutdown"` ends, or when every child has ended (status 0 if
   every child exited 0, else the kernel's failure code). A child with the
   default, `report`, is reported and left dead. No restart in v1.

**Rule 8.2.** Any step that fails prints the step, the child and the status,
and stops the machine with a failure. The stock root never runs a
half-wired system.

**Rule 8.3.** It has no device-specific code, no address and no line number
of its own: everything comes from `imageplan`.

---

## 9. The builder, and how a product finds sawos

### 9.1 The builder

**Rule 9.1.** `tools/sosimage/` is a Python package with a command line and
a library:

- `load(path)` reads an image description, and `from_case(case, board)` is
  the runner's adapter; both make an `Image`;
- `build(image, out_dir)` validates (rule 5.4), assigns addresses (rule
  5.5), generates `kconfig`, `imageplan` and the scripts, builds every
  package through Blade, compiles and links the kernel, and writes the
  region table;
- `flash(image)` produces a board's flash image (the S3's `flash_image`,
  `check_in_place` and the trimmed `--emit-flash` output move here);
- `boot(image, stdin)` runs it under the board's emulator, and
  `check(test, transcript)` judges it, as the runner's `_run_qemu` and
  `_check` do.

**Rule 9.2.** `tools/sos_runner.py` builds every case through it. Its case
keys, its parallel scheduler and its report stay. Test-only kernel objects
(the `asm` payloads, a hand-assembled `child_asm`, a loader fixture linked
with a deliberately wrong script for `slot_mismatch`) are passed to the
builder as extra objects or a script override, which are not part of the
description format.

**Rule 9.3.** The kernel stays out of Blade. The builder compiles it with
sawc, as `_build_elf` does, from the resolved sawos tree and the sawlang
checkout that sawos's own `sawlang.pin` resolves.

**Rule 9.4.** Blade builds every process image with three new flags (a
sawlang change):

- `--linker-script PATH`, which takes the manifest key's place;
- `--out-dir DIR`, which takes `.build/<triple>/`'s place, and under which
  Blade writes everything it writes, the lock included;
- `--module-path NAME=DIR`, repeatable, passed through to sawc for a module
  the manifest does not list.

### 9.2 How a product finds sawos

**Rule 9.5.** A product repository has a `sawos.pin` (`version`, `sha`) and
a verbatim copy of sawos's resolver, `tools/sawos.py`, in the shape of
`tools/toolchain.py`: `SAWOS_ROOT` if set, else a fetch of the pinned sha
into `~/.cache/sawos/<sha>/`. It writes the link `.sawos` at the product's
root to the resolved tree and runs `<sawos>/tools/sosimage`.

**Rule 9.6.** The product inherits sawos's sawlang pin: one revision of the
kernel, the `sos` module, every driver and the toolchain, which is what the
vDSO discipline asks for (§5.7). A product does not pin sawlang.

**Rule 9.7.** A product package depends on sawos packages by path through
the link (`sos = { path = "../.sawos/kernel/sysapi" }`) and names the
board's syscall stub the same way in `[sos.<triple>] native`. Blade
normalizes paths lexically (`normalize_path` in `blade/src/resolver.saw`),
so a sawos package reached through the link and its own `../../` paths
agree on one source. I built such a package (§14.1). An optional second
sawlang change would let the `sos` package carry its own native units, so
product manifests stop naming HAL paths. It is not needed for the box.

### 9.3 Testing and flashing a product

**Rule 9.8.** `python3 tools/sawos.py build images/box.toml` builds into
`build/useless-box/`. `run` boots it under the emulator and prints the
console. `test tests/box-qemu.toml` boots the image a test names, feeds its
`stdin`, and judges the console against `expect`, an ordered list of lines
as the runner's `expect_out` is, and its `status`. The no-timing rule of
sawos's CLAUDE.md applies to product tests too.

```toml
format = 1
image = "../images/box-qemu.toml"
expect = ["SOS controller: switch on",
          "SOS pwm-fake: set 0 2000",
          "SOS pwm-fake: set 0 1000",
          "SOS root: controller ended status=65536"]
status = 0
```

**Rule 9.9.** `flash images/box.toml --out box.bin` writes the board image,
prints its SHA-256 and the `esptool --chip esp32s3 --port PORT write-flash
0x0 box.bin` line, and flashes nothing itself.

---

## 10. An attack on this design

**10.1 Can a description give a process something its launcher does not
hold?** The stock root holds every boot row and `root_system_rights()`, so
a description can give a child anything the board offers: any device the
board lists, any line, even `Shutdown` or `ProcessCreate` in a System
sibling. That is by design: the description is the integrator's policy, as
root's source is today. What it cannot do: give a device the board does not
list (rule 5.4), give a quota above the launcher's (rule 7.2), or let a
process claim anything (an sosimg carries no authority; the dormant
`device-window` manifest key, which §11 wants retired, is not used). The
builder should print, per process, every right above the convention it was
given, so a reviewer sees them.

**10.2 What does a wrong description do at boot?** Most errors are refused
at build (rule 5.4). What is left: a device that is not wired, a pin that
is wrong, a `Mapping` grant the child's image leaves no room for
(`NoResource` at `QuotaSet`), a tag the child's source disagrees with
(10.9). Each stops the machine with a printed reason (rule 8.2). Nothing
runs half-wired.

**10.3 The flat tier.** On the S3 every capability, quota and window here
is discipline, not a wall (§5b.1). Any process can write root's memory, the
kernel's, the plan and every register. A product on a flat board trusts
every process it loads, sawos's drivers and its own code alike. This design
changes nothing about that and must not read as if it did; the builder
prints the board's tier at the top of every build.

**10.4 sawos moving under a product's pin.** Nothing moves until the
product bumps the pin. When it does, these can break it: the description
and declaration formats (versioned, refused when unknown), a board's device
names, the `sos` API and the protocol packages (source changes, caught at
compile), sawos's limit defaults (an image that relies on a default
changes behaviour silently), and the stock root's console lines (product
transcripts move). Two defences: the builder prints the effective limits
of every image, and sawos's gate builds a small product outside the tree
(unit 8), so a change that breaks products is red in sawos first.

**10.5 What the suite's cases lose.** The hand-kept scripts go, and with
them the readable "this child lives at 0x8028_0000" in the tree; the
generated script in the build directory says it instead, and the spec's
"living demonstration" sentences change (§0.1). A package built alone with
`blade build` uses the board's default script and may not be the image's.
And the image a case boots is one more step removed from its dictionary,
which the unit-1 transcript diff guards.

**10.6 What they gain.** The S3 runs cases with several children (the
uart-service shape, pingpong), the two-children-and-a-pool refusal in
`_region_rows` goes away, and `tests/irq-lines` and `tests/poolbase_*`
become generated facts rather than C files kept by hand.

**10.7 Every toolchain change.** Blade's three flags (rule 9.4), one
sawlang unit, a release and a pin bump, in SO-74's pattern. Optionally,
dependency-carried native units (rule 9.7). None in sawc: the per-image
limits module compiled with the pinned compiler (§14.1). None in the `[sos]`
manifest: every new field is in sawos's own files.

**10.8 The stock root's handle budget.** Root's table is `MAX_HANDLES`
(16) and so is its boot set (`boot_handle_room`). The box's twelve rows fit
only if root drains them as it goes and releases each blob and destination
after its create, as `tests/uart-service` does. The builder computes the
plan's peak and refuses an image past it (rule 5.4), and `[limits]
handles` raises it, at a cost in every body.

**10.9 Tags written twice.** A process's tags are in `process.toml` and in
its source. The builder cannot read Saw, so a disagreement is found at the
child's first instruction, by `BootSet`, under the emulator. Generating a
per-process tag module would close it at build time, at the price of a
module every process must import; I recommend not now.

**10.10 Config as a module.** A driver whose pins are a build input cannot
be built by `blade build` alone, and a sawos driver is compiled for each
product. Both were already true: every process is linked per image.

**10.11 The kernel differs per image.** A bug that only a particular limit
exposes would show in one product. sawos's gate runs the defaults and the
cases that set limits; that is all it can run.

**10.12 Writing into the pinned tree.** Blade writes `Saw.lock` and
`.build/` into the package it builds (§14.1 saw both appear). Without
`--out-dir`, a product build would write into the fetched sawos tree.
Whether Blade rewrites an unchanged lock I did not check; rule 9.4 makes
`--out-dir` cover it either way.

**10.13 Two levels of fetch.** A fresh product machine fetches sawos at its
pin, then sawlang at sawos's pin, then builds the compiler. That is slow
the first time and needs both repositories reachable over HTTPS.

---

## 11. Alternatives rejected

- **Root donates the image's limits at start.** No kernel change to donate,
  but userspace cannot size a donation (§6.1), donation is permanent, the
  handle table and grant record are not donatable, and a wrong number is a
  run-time `NoResource`.
- **A limits record in the boot table.** A new format and a boot phase, the
  same sizing problem moved into the kernel, and a wrong number is
  `fatal_image` at boot.
- **A quota argument to `ProcessCreate`.** Its three registers are taken (a
  record through the copy-in funnel would do), and setting rows one at a
  time in the `Created` window is the shape `Give` and `RestrictRights`
  already have.
- **A quota in the sosimg header or `[sos]`.** An image claiming for itself,
  and a toolchain change for each new field.
- **The kernel as a Blade package.** Blade would need kernel linking, board
  scripts, HAL module swaps and `--runtime-provider`, to replace a recipe
  sawos already owns.
- **The runner's cases as files.** Hundreds of files for no reader; an
  adapter gives one code path without them.
- **Root's plan as a binary table in a region row.** A second format to
  version and validate on the target, a mapping row spent to read it, and
  errors at boot rather than at compile.
- **Position-independent images and a relocating loader.** The loader stays
  microkernel-sized and never relocates (§5.6).
- **One fixed slot per sawos driver.** Slots run out, and fixed regions
  waste the S3's 256 KiB of process RAM.
- **Blade git dependencies on sawos.** No subdirectories, tags only, and a
  clone per package of one repository.
- **A wrapper `SOS_LD` that rewrites `-T`.** It avoids the sawlang change,
  but it is hand-wired and races on the shared output directory.
- **A driver's pins sent as a `Setup` message from root** (SO-77 §4.2).
  Root would need each protocol's encoding, so there would be no generic
  root. A message from the client gives configuration to whichever client
  speaks first.
- **A git submodule instead of a pin.** It works, and needs no resolver.
  But it diverges from `sawlang.pin`'s idiom, has no environment override
  for working against a local sawos, and a pin is what the owner asked for.

---

## 12. Cost and order of work

Each unit is small enough to brief, says what it proves, and how it is
tested. "Sonnet" and "Opus" follow CLAUDE.md's rule.

**U1. The builder library, transcript-identical (sawos; Sonnet, medium).**
Move the stitching, the kernel build, the region table, the S3 flash image
and the emulator boot into `tools/sosimage/`, and the `ARCHES` facts into
each board's `board.toml`. The runner builds every case through
`from_case`. Packages still link with their manifests' scripts. Proves one
path. Test: the full gate and both smokes, with every transcript, total and
image size identical to the base. Pays for itself: the S3 smoke stops
having its own build path. After SO-96.

**U2. The description format and the multi-row table (sawos; Opus for the
format, Sonnet to build, medium).** `load()`, the `[rows]` devices and
pools, rule 5.4's checks that need no `process.toml`, the CLI's `build`,
`run` and `test`, and `uart_service_ns16550` as a file, held byte-identical
to its case. Absorbs SO-77's U1 (multiple device rows). Spec §12's row-order
sentence. Test: that case both ways, and one negative per rule-5.4 check.

**U3. Blade's three flags (sawlang; Sonnet, small; a release and a pin
bump).** Rule 9.4. Test: Blade's own lane, plus a sawos case that builds one
package for two scripts in parallel. Starts now, in parallel with U1.

**U4. Generated addresses (sawos; Sonnet, medium; after U2 and U3).** Rules
5.5 to 5.8: the templates, the S3's `image_slots.ld`, per-process region
and arena, `--out-dir` per image; delete `child2.ld`, `child3.ld` and the
S3's `child.ld`; §5.5, §5.6 and §5b.1's sentences. Proves the S3 runs
several children in place. Test: the gate unchanged; the S3 smoke gains a
two-child and a four-child case.

**U5. Per-image limits (sawos kernel; Sonnet, small).** Rules 6.1 to 6.3,
rule 3.4's equality asserts, the default `kconfig`, the stale `limits.saw`
comment. Test: a case at `processes = 5` running five processes without a
donation; an `expect_build_error` case with a default above its slab.
Independent of U2 to U4.

**U6. The quota door (sawos kernel and spec; Opus for the spec, Sonnet to
build, medium).** Rule 7.2, the right, the `sos` wrappers, `QuotaGet`.
Tests on every profile: a raised row met exactly; after start `BadState`;
above the launcher's own limit `BadArg`; a `Mapping` grant past the free
rows `NoResource`; a grandchild capped by its launcher. Independent.

**U7. Declarations and the stock root (sawos; Opus for `bootplan`, Sonnet to
build, medium-large; after U4 and U6).** `process.toml`, `BootSet` in
`sos`, `bootplan`, the `imageplan` generator, the stock root, rule 4.5's fix
to the UART drivers, §12's convention and drain-order text. Test: a
`uart_service_stock` case wiring the same driver and client through the
stock root; negative builds for each declaration mismatch; a child given a
wrong tag stopping with the tag printed.

**U8. Products (sawos; Sonnet, small; after U2, finished after U7).**
`sawos.pin` and `tools/sawos.py`, the `.sawos` link, and a gate case that
copies a tiny product out of the tree into a scratch directory, builds it
and boots it. Proves a product outside sawos works, and keeps proving it.

**Where SO-77 and SO-78 sit.**

- SO-77 U0, the read-only board probe, needs none of this and can run now.
- SO-77 U1 is absorbed by U1 (board devices) and U2 (rows).
- SO-77's GPIO and PWM drivers are written after U4 and U6, as packages in
  `drivers/` with `process.toml` and config inputs (U3), each smoke case an
  image description with a test client as its root. Their designs change
  where §0.1 says: the pins arrive as a module, and the PWM driver holds its
  four windows.
- SO-77 U5 is replaced by U4, U5 and U6.
- SO-78 is the box repository, after U7, U8 and SO-77's drivers.

**The order, with at most two agents at once:** SO-96; then U1 beside U3
(sawlang); then U2 beside U5; then U6 beside U4; then U7 beside SO-77's
GPIO driver; then U8 beside the PWM driver; then SO-78.

---

## 13. Questions for the owner

1. **How does a product find sawos?** A pin with a resolver and a `.sawos`
   link (rule 9.5), or a git submodule. *Recommended: the pin*, as
   `sawlang.pin` is here, with `SAWOS_ROOT` for working against a local
   tree.
2. **How does a driver get its pins and limits?** A module the image
   supplies at build time (rule 4.6), a `Setup` message from root, or one
   from the client. *Recommended: the module.* It is typed, checked at
   compile time, needs nothing on the target, and keeps root generic.
3. **Who routes the servo pads and turns on LEDC's clock?** The PWM driver,
   given LEDC, GPIO, IO MUX and the 64-byte clock window through the quota
   door, or root with device-specific code. *Recommended: the driver.* It is
   what makes a generic stock root possible. The cost: it can touch every
   pad, the switch's included, which on the flat S3 any process can anyway.
4. **What does the stock root do when a child ends?** *Recommended:*
   report it; stop the machine when a child marked `shutdown` ends or when
   all have; no restart in v1.
5. **Are limits per image by compiling the kernel per image?**
   *Recommended: yes*, with donation kept for growth at run time.
6. **Is the quota door limited to before `start`, and capped by the
   launcher's own limit?** *Recommended: both.* Raising a running process's
   budget can be added later if something needs it.
7. **May sawlang's Blade gain three flags ahead of the box?** It puts a
   sawlang release on the box's path. *Recommended: yes.* The alternative
   is a linker wrapper that is hand-wired and racy.
8. **Where does the box live?** *Recommended:* its own repository, as the
   owner described, with sawos's gate building a tiny scratch product
   (U8) so sawos cannot break products unnoticed.
9. **Is root's region per image too?** Today it is the HAL's (96 KiB on the
   S3). *Recommended: not yet.* The box fits without it; it would be a
   `kconfig` fact checked against the board when an image needs it.

---

## 14. What I verified, what I read, what I could not check

### 14.1 Verified by running (scratch only; no repository file changed)

- **A package outside the tree builds against sawos.** In
  `scratchpad/SO-95/probe/product/`, a `.sawos` link to this tree and a
  package depending on `sos` and `uartproto` by path through it, with a
  riscv32 `linker-script` and `native` named through the link. Built with
  the lead's sawlang 0.23 Blade (`.build/blade`) and its stage 2 sawc:
  `hello-product.sosimg`, 5352 bytes. Its `Saw.lock` records `sos` at
  `../.sawos/kernel/sysapi` and `sosrt` at `../../rt/common`: one source
  each, through the link. Blade wrote `Saw.lock` and `.build/` into the
  package it built and nowhere else; `git status` was clean afterwards.
- **The per-image limits module compiles with the pinned compiler.** A
  `kconfig` module given by `--module-path` sized a static array of structs
  and a derived length, and fed two `static_assert`s across the module
  boundary (sawc 0.23, riscv32, freestanding, `-Oz`). With a mapping
  default of 5 the compile failed: "static assertion failed: mapping
  default fits".
- **`INCLUDE` finds a generated script through `-L` and not without it**
  (Homebrew `ld.lld`, riscv32): so the kernel link, which the builder owns,
  can include generated slots, and a Blade link, which passes no `-L`,
  cannot.
- **Python's `tomllib` reads `0x3FCB_0000`** and arrays of tables (Python
  3.15 here).

### 14.2 Read, not run

Everything in §1, from the files and functions named there; Blade's
manifest keys, CLI flags, link command, output path, git and path
resolution (`blade/src/sosimg.saw`, `cli.saw`, `builder.saw`,
`resolver.saw`, `git.saw`); that the boot set is FIFO (`queue_boot_handle`,
`boot_handle_next`); the S3 RAM and flash arithmetic in §5.3, from
`hal/xtensa-esp32s3/kernel/lib.saw` and `esp32s3.ld`.

### 14.3 Not checked

- **No emulator run and no gate run.** I took no suite lock because I ran
  no QEMU. Unit 1's "transcript-identical" is a requirement, not a result.
- Espressif's `ld.lld` was not tried with `INCLUDE`; I assume it behaves as
  Homebrew's.
- Only riscv32 was built through the `.sawos` link, not Xtensa or arm64.
- Whether Blade rewrites an unchanged `Saw.lock` in a dependency's tree.
- The `imageplan` module's exact Saw shape (a static table of structs with
  names in it, freestanding) is not compiled; the implementer compiles it
  first.
- That the box's processes fit 24 to 32 KiB regions with 4 to 8 KiB arenas:
  none of them is written yet.
- The device numbers in §3 are SO-77's, from ESP-IDF headers; none was read
  on a part.
- That `arm64`'s `PROT_DOMAIN_SLOTS` (5) could be sized from `kconfig`: it
  is a HAL static and is left as it is.
