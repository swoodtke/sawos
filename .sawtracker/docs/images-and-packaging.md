# SO-95: images and packaging (design)

For the owner and the reviewer, before anything is built. This is revision
3, checked against main at `efd4512`, which carries SO-92.p2 (typed boot
rows, version 4, a pool row of its own kind) and SO-96 (`drivers/`). It
takes in the reviewer's twelve threads and summary on revision 2. Section
17 answers each one.

It carries two rulings of the owner's from Nov 13:

- tools are written in Saw wherever possible, with Saw gaps filed with the
  sawlang lead;
- an image's limits are a table linked into a kernel compiled once per
  board.

Every claim about the code names the file and function it comes from.
Section 16 separates what I ran, what I read, what the reviewer ran, and
what nobody checked.

---

## 0. Verdict

1. **The seven parts hold.**
   - A board lives in sawos, as `board.toml` beside its HAL.
   - A product repository holds image descriptions, its own packages and a
     `sawos.pin`.
   - A process states its boot interface in `process.toml`, and the
     builder refuses an image that does not supply it.
2. **The builder is a Saw program,** `tools/sosimage/`, a hosted Blade
   package. Its first unit is smaller than revision 2 said.
   - The runner keeps its package phase and its transcript.
   - The builder compiles the kernel once per run for each profile and
     kernel entry, before any case. It then links each case from the
     runner's finished images.
   - Nothing is cached across runs.
   - The C3 smoke stays in Python.
3. **The kernel is compiled once per board and entry, and an image's
   limits are a block linked in after the boot table's rows.**
   - At boot the kernel grows each slab by one extent, from a reserve the
     builder assigns, through the code `SlabDonate` runs. I built this into
     a scratch kernel, and it ran on riscv32 and arm64.
   - The boot table becomes version 5.
   - The builder reads each slot's size from the kernel's own symbol
     table. No size is kept by hand.
4. **The box as revision 2 drew it does not launch.** Root's 16-entry
   handle table holds every boot row from root's first instruction, so 12
   rows leave no room for the first create (the reviewer's run).
   - Recommended: the stock root takes one peripheral row per board and
     carves each window as it gives it. The box then has 9 rows. Root peaks
     at 14 of 16 handles, and every child stays within its quota (§5.3).
   - The same carving gives the PWM driver narrower windows (question 3).
5. **The kernel changes in three places:**
   - the boot table's limits block, placed by the builder and checked by
     the kernel;
   - an exported integer table of board facts, `sos_board_info`;
   - `ProcessOp.QuotaSet`, which sets a per-process cap and is not a
     budget.
6. **The stock root is table-driven** from a generated Saw module, and
   carries no device code. Its one-time register writes (the LEDC clock
   gate) are data in the image.
7. **What stays Python, and why:**
   - the resolvers, which run before any Saw compiler exists;
   - the emulator harness, until `std.process` grows;
   - the runner's case table and the C3 smoke path, for now.
8. **Saw and Blade gaps:** twelve, one of them new (11.12: two `sawc` runs
   to one output collide).
   - Blocking: 11.1 (`libs/toml`), 11.2 (`std.process`), 11.7 (`blade
     build` flags and build avoidance).
   - Gap 11.4 (a struct's size as a constant) no longer blocks this design
     and is kept as nice to have.
9. **SO-77 and SO-78.** SO-77's U5 is replaced by this design's units, and
   SO-78 becomes the first product repository.

### 0.1 Where the rulings, the spec and the code disagree

None of the owner's rulings contradicts another. These are the places where
what the owner asked for meets text or code that says otherwise.

- **The spec calls the hand-kept linker scripts deliberate.** §5.5 says
  riscv32-virt "keeps one linker script per resident child image ...
  deliberately", and §5b.1 calls them "the living identity-tier
  demonstration". There is still one script per resident image, but the
  builder writes it. Both sentences change.
- **§2.9 says "there is no per-create quota argument in v1",** and §11
  repeats it. Rule 7.2 proposes the door and its text.
- **§2.8 says donation makes "the compiled limits ... a floor and capacity
  ... policy, decided by the deployment rather than by the build".** With a
  linked block the build decides the boot capacity, and the deployment can
  still grow it.
- **§12 says "the kernel interprets nothing" of a row and mints one handle
  per row.** That is what makes root's table the bound on rows (§5.3), and
  the design works within it rather than change it.
- **SO-77's design has root write the LEDC clock gate and route the servo
  pads in code** (`.claude/lead/so77-design.md` §3.3). A generic stock root
  cannot. Its one-time writes become data in the image instead (rule 8.5).
- **Blade's git dependencies cannot reach a package in a subdirectory**
  (gap 11.8). Products use path dependencies through a link.
- **A stale comment.** The header of `kernel/core/limits.saw` says a
  derived static cannot take its operands from another module. With the
  pinned compiler that compiles (§16.1).

---

## 1. Today

### 1.1 How an image comes to be

The test runner, `tools/sos_runner.py`, is the only thing that builds a
system image. It holds 290 cases: 264 in the gate, 20 in the S3 smoke and
6 in the C3 smoke (the reviewer's count).

**A case is the image's description.** Each entry of `TEST_CASES` names:

- a kernel entry (`src`): `kernel/main.saw` for most, and a harness kernel
  of its own for 19 riscv32 cases and 18 arm64 cases. `thread_preempt` and
  `arena_race` are process cases with entries of their own;
- a root package (`root_pkg`), or a hand-assembled payload as root
  (`asm`);
- child packages (`children`), or one hand-assembled child (`child_asm`);
- the flags `pool`, `device` and `tick`, and narrower keys (`pool_blobs`,
  `pool_skew`, `pool_short_top`, the S3's `loader_fixture` and
  `expect_build_error`).

**Packages are built once per profile, before any case.**
- The runner collects each distinct package and runs
  `blade build --force --target <triple>` in its directory
  (`_build_root_image`), with `SAWC`, `SOS_CLANG` and `SOS_LD` from
  `_blade_env`. It prints a line per image
  (`<pkg>/.build/<triple>/<name>.sosimg (N bytes)`).
- A case only copies finished images into its own staging. Nothing locks a
  build; doing it once is what keeps builds apart.
- `_build_blade` builds Blade from the sawlang checkout when no binary is
  supplied.

**The kernel is compiled per case.** `_build_elf` compiles `case["src"]`
with sawc: `--freestanding --no-hidden-alloc --runtime-provider`, and
`--module-path` for `kcore`, `hal`, `imgformat` (from the sawlang
checkout), `sosrt`, `sosabi` and the board's extra modules. It links with
the board's script. On arm64, `_check_kernel_fp_free` then disassembles the
kernel.

**Root and children are stitched in.**
- `_stitch_root_image` assembles `kernel/rootimg.S` into `.payload`.
- `_stitch_regions` writes `<case>.regions.S`: each child sosimg
  `.incbin`'d into `.childimg`, then the `.regions` section (magic, version
  4, row count, boot flags, rows).
- `_region_rows` states the row order: each child's blob, each child's
  destination, the pool, the device window. It types each row `Ram`,
  `Device` or `Pool`.

**The boards have their own paths.**
- The S3 smoke (`_esp32s3_smoke`) compiles its kernel itself, links with
  `esp32s3.ld`, writes a 4 MiB flash image and runs `check_in_place`.
  `--emit-flash` prints the image's SHA-256 and an `esptool` line.
- The C3 smoke has its own stitchers inside `main()`, its own user scripts,
  and an `objcopy` flatten padded to 4 MiB. It is a gating run
  (`.sawtracker/tests.json`).

### 1.2 Where each fact lives

Each fact, where it is stated, and where it is stated again:

- **A child's RAM base, the pool, the device window, the selftest line,
  root's entry:** `ARCHES` and `ESP32S3_ARCH`; again in the HAL's
  `lib.saw` and the user scripts.
- **A child's region length:** `CHILD_REGION_LEN` or the board's
  `child_region_len`; again as each script's `LENGTH`.
- **A child's link address:** `hal/riscv32/user/child.ld`, `child2.ld`,
  `child3.ld` and `hal/xtensa-esp32s3/user/child.ld`; again in `ARCHES`.
- **Which script a package links with:** its `Saw.toml`; again as the
  package's position in a case's `children`.
- **The S3's flash layout:** `esp32s3.ld`: root's slot at 0x10_0000, one
  child slot at 0x18_0000 (512 KiB each), and the kernel's `.drom`
  (read-only data and the region table) at 0x20_0000. Again as
  `IMAGE_SLOT` in the user scripts.
- **Root's region and every process's stack:** `hal.ROOT_LOAD_BASE`,
  `ROOT_REGION_TOP`, `ROOT_STACK_LEN`; again in the scripts.
- **Interrupt lines:** `tests/irq-lines/<arch>.c`; again in the HAL.
- **The pool's address as a program sees it:** `tests/poolbase_<arch>.c`.
  On arm64 this is where the first mapping lands, not the pool's frames.
- **Slab floors and quota defaults:** `kernel/core/limits.saw`, one set for
  every image.
- **Which boot row is which, and what a child is given under which tag:**
  each root's source, by position; again in `_region_rows` and in each
  child's drain order.

### 1.3 What the kernel takes from an image

- **The region table** (`kcore.process`, `mint_boot_regions`):
  - version 4 only, with rows typed `Ram`, `Device` or `Pool`
    (`RegionKind`), and anything else refused with `fatal_image`;
  - at most `MAX_MEMORIES + MAX_IOMEMORIES` rows;
  - flag bit 0 turns off the tick;
  - bytes after the last row are not read.
- **Each row becomes a handle in root's table, before root runs.**
  - `start_process` mints System, Process and Thread at words 1 to 3.
  - `mint_boot_regions` then mints one handle per row, with
    `mint_handle`, and queues a boot record naming it (`queue_boot_handle`).
  - So at its first instruction root's 16-entry table (`MAX_HANDLES`) holds
    3 + rows, and `process_self` makes it 4 + rows.
  - A give works the same way for a child: the handle is moved into the
    child's table and a record queued.
- **Limits are constants** in `limits.saw`. Each slab is a `Slab<T, N>`
  (`kernel/core/slab.saw`): an inline extent 0 of `N` slots, up to
  `MAX_SLAB_EXTENTS` (4) donated extents, and a run-time `total`.
  `kcore.dispatch.slab_donate` validates a region, sizes it with
  `slab_geometry`, zeroes it and appends with `slab_install`.
- **Quotas.** `install_quota_limits` gives a child the `DEFAULT_QUOTA_*`
  numbers and root `QUOTA_UNLIMITED` (0xFFFF, a `UInt16`). It clamps the
  `Mapping` row to the grant rows each process's own image left.
- **What a give and a carve charge.** A give charges the receiver's
  `Handle` row and nothing else (`move_entry`). An `IoMemory` row is
  charged where a window is made: at boot for root, and at `Carve` for
  whoever carves.

### 1.4 What cannot be done from outside the repository

- **Build a kernel.** The recipe exists only in the runner.
- **Build a system image.** The stitchers are runner functions.
- **Link a sawos package where an image chooses.** Blade takes the script
  from the manifest, links with `-T` and no `-L`, ignores flags it does not
  know, and does not see a script change in its build avoidance (gap
  11.7).
- **Run more than one child in place on the S3.** There is one child slot.
- **Change a limit or a quota** without editing `limits.saw` for every
  image.

A package in another directory can depend on sawos packages by path and
build an sosimg. I built one for riscv32. The reviewer built it for arm64
and Xtensa too, byte-identical to the in-tree package (§16.3).

---

## 2. The parts

1. **A board description in sawos.** Kept. It holds the build-side facts
   (section 3). Facts the kernel holds are read from the kernel object.
2. **An image description in the product.** Kept (section 5). It names
   devices, register ranges and register writes, never addresses.
3. **A driver's statement of its needs.** Kept and widened into the
   process's whole boot interface, `process.toml` (section 4).
4. **One builder.** Kept, written in Saw (section 9). It grows in steps
   that each leave the runner's transcript unchanged.
5. **A table-driven root.** Kept (section 8). Its table is a generated Saw
   module.
6. **A pin on sawos.** Kept. A Python resolver links the pinned tree into
   the product as `.sawos` (rule 9.8).
7. **The product's own test.** Kept. Judging is Saw. Booting goes through
   the Python harness until `std.process` allows it (section 10).

---

## 3. The board description

**Rule 3.1.** Each board in sawos has one `board.toml` in its HAL
directory: `hal/xtensa-esp32s3/`, `hal/riscv32/`, `hal/arm64/`, and the
flat profile's, which names the riscv32 board as its base. The C3's comes
when the C3 smoke moves (U9). The runner's tables and the hand-kept child
scripts are generated from it or deleted.

**Rule 3.2.** It states the facts only the build needs:

- the triple, the tier, the HAL and its extra modules, the C flags, and how
  to run the board under its emulator;
- the **placement kind**:
  - `copied` (riscv32): a blob may sit anywhere and is copied to the
    destination it is linked at;
  - `in-place` (the S3): code and read-only data run from a flash slot,
    and writable data is copied to RAM;
  - `translated` (arm64): one link base, and any frames;
- the default arena size, and on an `in-place` board the flash windows,
  the slot range and a default slot length;
- **peripheral rows**: one or a few ranges covering every device, each a
  naturally aligned power of two, which is what a boot row needs;
- each **device** by name: base, length, a `compatible` string, its line,
  and whether the kernel uses it;
- each device's named **registers** (offsets), and named **register
  arrays** (offset, stride, count), so an image can name
  `GPIO.out_sel[41..42]`;
- **reserved lines.**

**Rule 3.3.** An image names devices, register ranges and registers by
these names, never by address. The builder turns a name into an offset and
a length, and refuses a range that is not a naturally aligned power of two
of at least the grain, because `Carve` refuses one.

**Rule 3.4.** Facts the kernel holds are read from the kernel. This is the
owner's "board info symbol". The kernel exports one integer table,
`sos_board_info`, with a leading version word, holding:

- the process RAM range, root's region, the grain, the stack length and the
  grant-row budget;
- `PROT_DOMAIN_SLOTS`, `MAX_HANDLES`, `MAX_SLAB_EXTENTS` and
  `THREAD_FRAME_STRIDE`;
- each slab's floor.

Slot sizes are read from the slabs' own symbols (rule 6.3). An exported
array of integers built from the HAL's and `kcore.limits`' statics
compiles, and lands as a data symbol of the right size (§16.1).

**Rule 3.5.** On-chip peripherals are board facts. What is wired to the
board (a switch on pin 21) is an image fact: a driver's config module, and
the register ranges and writes the image names.

The S3's file, abridged:

```toml
format = 1

[board]
name = "esp32s3"
triple = "xtensa-esp-unknown-elf"
tier = "flat"
hal = "hal/xtensa-esp32s3"
placement = "in-place"
default-arena = "8K"

[flash]
bytes = "4M"
ibus = 0x4200_0000
dbus = 0x3C00_0000
root-slot = { offset = 0x10_0000, len = "512K" }
slots = { offset = 0x18_0000, top = 0x20_0000, default-len = "128K" }

[rows.PERIPH]
base = 0x6000_0000
len = 0x10_0000

[devices.GPIO]
base = 0x6000_4000
len = 0x1000
compatible = "espressif,esp32s3-gpio"
line = 16
registers = { ENABLE1_W1TS = 0x030 }
arrays = { out_sel = { offset = 0x554, stride = 4, count = 54 } }

[devices.IO_MUX]
base = 0x6000_9000
len = 0x1000
compatible = "espressif,esp32s3-io-mux"
arrays = { pad = { offset = 0x004, stride = 4, count = 49 } }

[devices.LEDC]
base = 0x6001_9000
len = 0x1000
compatible = "espressif,esp32s3-ledc"
line = 35

[devices.SYSTEM]
base = 0x600C_0000
len = 0x1000
compatible = "espressif,esp32s3-system"
registers = { PERIP_CLK_EN0 = 0x018, PERIP_RST_EN0 = 0x020 }

[lines]
reserved = [57]
```

UART0 (0x6000_0000) is listed beside these and abridged out here.

- **The slot range ends at 0x20_0000,** where `esp32s3.ld` puts the
  kernel's `.drom` today. Unit U4 moves `.drom` behind the last slot,
  through the generated `image_slots.ld`, and `top` becomes the flash's
  end then. Until U4, four 128 KiB slots is the most the board takes.
- **The device numbers are SO-77's** (`so77-design.md` §2), from ESP-IDF's
  headers.
- **`PERIPH` covers every device here,** from 0x6000_0000 to 0x6010_0000.
- **The pinned `libs/toml` cannot read this file** (gap 11.1).

---

## 4. A process's declaration

**Rule 4.1.** Every process the stock root launches has a `process.toml`
beside its `Saw.toml`. It is sawos's file, read by sawos's builder, so a
new field is never a toolchain change. A process launched only by a
hand-written root may omit the file.

**Rule 4.2.** It declares the process's **boot set**, in the order the
launcher gives it: each record's name, tag and kind.

- `system`: a masked System sibling; its tag is the start tag.
- `window`: an `IoMemory`, with the `compatible` strings it accepts, the
  device it must lie in, and whether a part of the device will do.
- `line`: a bound `Interrupt`, with its `compatible` strings.
- `serve` and `client`: an outlet or an inlet, with the protocol package's
  name.
- `memory`: a RAM allocation or a pool row.

**Rule 4.3.** It declares the quota rows the kernel charges for what the
program does: `mappings`, `handles`, `threads`, `waiters`, `timers`,
`events` and `pipes`. It declares `iomemories` only if it carves.

- A given window spends the child's `Handle` row and not its `IoMemory`
  row (§1.3).
- The builder works out the `handles` floor itself: one per boot record,
  one for the derived Process handle, and one Mapping per window. It
  refuses a declared `handles` below that floor.

**Rule 4.4.** It declares its arena and the boards it builds for. Its
region is computed (rule 5.6), not declared.

**Rule 4.5.** A driver finds its registers through `Mapping.base()`, never
through an address constant. The UART drivers are brought to this in U6.

**Rule 4.6.** It declares its build inputs: modules the image supplies by
name (`gpioconfig`), passed as `--module-path NAME=DIR`. A driver's pins
are such a module: typed statics, checked by `static_assert`.

**Rule 4.7.** The tags in `process.toml` and in the source must agree.
`sos` gains a `BootSet` helper that drains every record and answers
`take(tag:)`. A missing or extra record is printed with its tag at the
child's first instruction.

The GPIO driver's declaration:

```toml
format = 1
boards = ["esp32s3"]
arena = "4K"

[[boot]]
name = "system"
kind = "system"
tag = 1

[[boot]]
name = "gpio"
kind = "window"
tag = 2
device = "GPIO"
compatible = ["espressif,esp32s3-gpio"]

[[boot]]
name = "iomux"
kind = "window"
tag = 3
device = "IO_MUX"
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
handles = 11      # floor 8 (5 records, Process, 2 Mappings) + Waiter + 3 held
waiters = 1

[build]
inputs = ["gpioconfig"]
```

---

## 5. The image description, and link addresses

### 5.1 The file

**Rule 5.1.** A hand-written image is one TOML file in the product's
`images/` directory, with `format = 1`. A test image, which the runner
writes, is JSON with more keys (rule 9.3).

**Rule 5.2.** A hand-written image states:

- `[image]`: the name, the board, the tick;
- `[limits]`: how many of each kernel object the image wants, and the
  children's quota defaults. Each is the board's floor or default when
  absent;
- `[root]`: `stock`, or a package for a hand-written root;
- `[[setup]]`: register writes the stock root makes once, before it starts
  anyone (rule 8.5);
- one `[[process]]` per child, in launch order, giving:
  - its package and its arena, if not the declared one;
  - each `window`, bound to a device or a register range;
  - each `line` and each `client`;
  - its build inputs, its quota rows and its narrowed ceilings;
  - its `on-exit` policy;
- for a hand-written root only, the rows it reads: `devices` and `pools`,
  each whole and in order.

**Rule 5.3.** A package is `sawos:<path>` or a path in the product's
repository.

**Rule 5.4.** The builder refuses, before it compiles anything, an image in
which:

- a name is not on the board, a line is reserved, or a range is not a
  carvable power of two;
- a window's device does not match its `compatible` strings or its
  declared device;
- a declared record is unbound, a binding names no record, or two records
  share a tag;
- a `client` and its `serve` name different protocols;
- a quota row is below what the process declares, or breaks rule 7.2;
- a stated limit is below the board's floor (it is refused, not rounded
  up), or breaks one of rule 6.5's relations;
- the regions, slots, pools and reserve do not fit the board, or overlap
  root's region or the kernel;
- **root's handle table overflows** at any step of the plan, or any
  child's `Handle` row is short. The count starts from the four handles
  root holds before any row (System, Process, Thread, and the derived
  Process handle), adds every row, and follows the plan step by step. A U2
  case boots an image at the limit and one past it.

### 5.2 Link addresses and regions

**Rule 5.5.** The builder assigns every address in a hand-written image:

- Root's region is the board's.
- Children's regions follow root's in order. Pools and the reserve follow
  the children.
- On an `in-place` board each child also gets a flash slot.
- On a `translated` board every image links at one base.

A test image's addresses are given by the runner and not assigned, so
today's pool cases keep their pool where it is. `tests/poolbase_<arch>.c`
stays hand-kept. On arm64 its number is where the first mapping lands, not
the pool's frames, so it cannot be computed from the row.

**Rule 5.6.** A region's size is computed, not declared:
`round_up(.data + .bss + arena, grain) + stack`. The builder links each
process once to measure `.data` and `.bss`, which do not depend on the
address, then links it at its assigned base. An image may give a larger
region for one process. The previous revision's 24 KiB defaults are gone:
24 KiB less a 16 KiB stack left 8 KiB, which the smallest child does not
fit (t6).

**Rule 5.7.** Each process is linked with a script the builder writes
from one template per board. `child.ld`, `child2.ld`, `child3.ld` and the
S3's `child.ld` go. One default script per board stays, for a standalone
`blade build`.

**Rule 5.8.** On an `in-place` board the kernel's script `INCLUDE`s a
generated `image_slots.ld`, which places each child's image at its slot
and, from U4 on, `.drom` behind the last slot.

**Rule 5.9.** The region table's row order stays §12's: each child's blob,
each child's destination, then the image's rows. A hand-written root reads
the rows it named. The stock root reads the board's peripheral rows and
its pools.

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
waiters = 5

[root]
package = "stock"

[[setup]]
register = "SYSTEM.PERIP_CLK_EN0"
set = 0x800

[[setup]]
register = "SYSTEM.PERIP_RST_EN0"
clear = 0x800

[[setup]]
register = "GPIO.ENABLE1_W1TS"
write = 0x600

[[process]]
name = "gpio"
package = "sawos:drivers/svc-gpio-esp32s3"
windows = { gpio = "GPIO", iomux = "IO_MUX" }
lines = { gpio = "GPIO" }
inputs = { gpioconfig = "config/gpio.saw" }

[[process]]
name = "pwm"
package = "sawos:drivers/svc-pwm-esp32s3"
windows = { ledc = "LEDC", outsel = "GPIO.out_sel[41..42]", pads = "IO_MUX.pad[41..42]" }
inputs = { pwmconfig = "config/pwm.saw" }
quota = { mappings = 3 }

[[process]]
name = "controller"
package = "controller"
clients = { switch = "gpio.gpio", servos = "pwm.pwm" }
on-exit = "shutdown"

[[process]]
name = "heartbeat"
package = "heartbeat"
```

The PWM `windows` line runs past 80 columns because TOML allows no line
break inside an inline table.

The setup writes turn on LEDC's clock, take it out of reset, and enable
output on pads 41 and 42 (`ENABLE1` bits 9 and 10). Question 3 asks the
owner whether these are the right shape.

What the builder makes of it:

- **Rows: nine.** Four blobs (in flash slots), four destinations, and
  `PERIPH`. The stock root carves every window from `PERIPH` as it gives
  it.
- **Windows.**
  - The GPIO driver gets GPIO and IO_MUX whole.
  - The PWM driver gets LEDC, and two 8-byte ranges: GPIO's out-sel words
    for pads 41 and 42 at 0x6000_45F8, and their IO MUX words at
    0x6000_90A8. Each is naturally aligned.
- **IoMemory objects.** `PERIPH`, five windows given away, and one
  transient carve during setup make 7, within the floor of 8.
- **Mappings.** GPIO's 2, PWM's 3, and root's one transient make 6, within
  the machine-wide floor of 8. PWM's row is raised to 3 (the default is 2),
  within its free grant rows (8 - 4).
- **Kernel.** The S3's object for `kernel/main.saw`, as the gate tested
  it, with a limits block adding two process bodies and one Waiter
  (counted below).
- **RAM.**
  - The board gives processes 256 KiB, read from `sos_board_info`.
  - Root keeps 96 KiB.
  - Each child takes its measured data, its arena and a 16 KiB stack.
  - The reserve holds two bodies and one Waiter at the S3's slot sizes,
    which the builder reads from the S3 kernel's symbols. I have not
    measured them (§16.4).
  - The fit is the builder's check. It cannot be stated until the drivers
    exist.
- **Flash.** Four 128 KiB slots end at 0x20_0000, the kernel's `.drom`.
  That fits before U4.

The limits: root plus four children is five processes, two above the
floor of three. Waiters are one per child plus root's supervisor, five
against a floor of four. Pipes (two connections against a floor of four),
timers and IoMemories stay at their floors. The block therefore reads
Processes +2 and Waiters +1.

**Handle counts, recounted.** These come from the plan and §1.3's rules,
by reading, not by running. Root's table holds 16:

```
step                                                          root holds
entry: System, Process, Thread, 9 rows                                12
process_self                                                          13
create child 1 (+1), release its blob and destination (-2)      14, 12
children 2, 3 and 4, the same                     13/11, 12/10, 11/9
after the creates: 4 fixed, PERIPH, 4 children                         9
each setup write: carve, map (+2); unmap, release both (-2)      11, 9
GPIO: System sibling, two carves, the bound line, each
      made (+1) then given (-1)                          10, 9 (x4)
GPIO: pipe_create (+2), give the outlet (-1)                     11, 10
PWM: System sibling and three carves, made then given   11, 10 (x4)
PWM: pipe_create (+2), give the outlet (-1)                      12, 11
controller: System sibling made and given                        12, 11
controller: each inlet minted (+1), given (-1), the
      original released (-1)                     12, 11, 10; 11, 10, 9
heartbeat: System sibling made and given                         10, 9
supervise: one Waiter                                                 10
```

Root peaks at 14 of 16, at the first create. Each further child would add
two rows: a fifth child peaks at 16 of 16, and a sixth does not launch
(§12.2).

Each child against its `Handle` row (default 12):

```
GPIO        5 records, Process, 2 Mappings, Waiter = 9; the outlet
            given into the Waiter (-1); up to 3 held requests     peak 11
PWM         5 records (System, LEDC, two ranges, outlet), Process,
            3 Mappings, Waiter = 10; outlet into the Waiter (-1);
            one request in hand                                   peak 10
controller  System, 2 inlets, Process, Clock, Timer, Waiter, one
            posted claim                                          peak 8
heartbeat   System, Process, Clock, Timer, Waiter                 peak 5
```

**What the pad shows.** SO-77 §5.6's open point: the pads' IO MUX
function is JTAG until the PWM driver's `Setup` selects GPIO and routes
the channel. So root's output-enable write changes nothing visible, and
the pad keeps its reset state until `Setup`, then shows the channel's idle
level, low. This is read from SO-77's register notes and not seen on a
part.

`config/pwm.saw`:

```saw
//! The box's servos: pad, LEDC channel and the bench-measured safe range.
import pwmconfig_types.{Servo}

public static SERVOS: [Servo; 2] = [
    Servo(pad: 41, channel: 0, min_us: 500, max_us: 2500),
    Servo(pad: 42, channel: 1, min_us: 500, max_us: 2500),
]
```

`images/box-qemu.toml` replaces the two drivers with stand-ins speaking
the same protocols (`hal/xtensa-esp32s3/ABI.md` §9).

### 5.4 Worked example: `uart_service_ns16550`

The case today runs on two profiles, riscv32 and the flat one. Its root is
`tests/uart-service`, its children `drivers/svc-uart-ns16550` and
`tests/svc-client`, and it has one device row and no tick. It asserts
`SOS: boot regions={five}`.

As a hand-written description:

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

- **The root is hand-written,** so it gets whole device rows, in the order
  named, and no `process.toml` is checked.
- **The table is today's five rows.**
- **One board per hand-written image.** The flat profile is a second
  description naming `riscv32-virt-flat`. A test image the runner writes
  is per profile already.
- **With the stock root,** the driver's `process.toml` declares system 21,
  window 22, serve 23 and line 24 in that order, and the client binds
  `uart = "driver.uart"`. The driver's code does not change, because the
  stock root gives in declaration order (rule 8.4).

---

## 6. How limits reach the machine

### 6.1 What was built and run

In a scratch copy of the tree (before SO-92.p2):

- **Kernel.** `kcore.dispatch.apply_boot_limits()`, about 70 lines, called
  from `kernel/main.saw` before `load_root_and_enter`. It reads a block
  after the region table's rows: a 24-byte header (magic, count, reserve
  base, reserve length), then one 8-byte entry per kind (`SlabKind`,
  slots to add). For each entry it takes the unit and alignment from
  `slab_geometry`, aligns a cursor in the reserve, zeroes, and calls
  `slab_install`.
- **Runner.** `_stitch_regions` writes the block when a case has a
  `limits` key.
- **Case.** `process_donate_limits` is `process_donate` with one extra
  body and a 256 KiB reserve. Its root creates two children and asserts
  that the third is refused at the floor of three. With the block, on
  riscv32 and on arm64, it printed:

  ```
  SOS procdonate: a fourth process existed BEFORE the donation
  ```

  The kernel's extension line came first; its unit was 0x324 on riscv32
  and 0x590 on arm64. The unchanged `process_donate` passed on both. I ran
  this under the suite lock.

### 6.2 The kernel side

**Rule 6.1. Extent 0 is the board's floor,** today's numbers. An image can
raise a limit and cannot lower it. A stated limit below the floor is
refused by the builder.

**Rule 6.2. The block.**

- The region table becomes version 5: its header and rows as version 4,
  then a limits block or nothing.
- What follows the rows is either nothing or exactly one well-formed block
  (magic, count, entries, reserve). Anything else stops the boot with
  `fatal_image`.
- Each kind an image raises spends one of its four extents, leaving three
  for run-time donation.
- A slot above the floor is reached through `Slab.locate`, as a donated
  slot already is.
- Zeroing uses `zero_bytes`, because no thread exists yet at boot.
- `mint_boot_regions` bounds rows by the capacities, not the constants.
- The children's quota defaults are read from the block at boot by
  `install_quota_limits`.

**Rule 6.3. The reserve's placement (t2).** The builder is trusted for
placement, as it already is for every row: the kernel compares no rows
today. The kernel's checks on the reserve are:

- the block is well-formed;
- every extent fits inside the reserve the block names;
- the reserve lies in RAM the board can give (`hal.map_source_ok`);
- the reserve does not meet root's region
  (`[ROOT_LOAD_BASE, ROOT_REGION_TOP)`). Those are two HAL constants, so
  the check costs nothing.

Keeping the reserve off the kernel's own image is the builder's job: it
reads the kernel's extent from the ELF. Revision 2 called "no boot row
overlaps it" a backstop; it is not one, and is dropped. U2's negative
cases: a reserve over root's region (refused at boot), and one over the
kernel (refused by the builder).

### 6.3 The build side: sizing the reserve

**Slot sizes come from the kernel's symbol table** (t7, the reviewer's
finding, re-run). Each slab is a static, and its symbol's size in the
kernel object is `N` slots plus the extent table:
`3 * MAX_SLAB_EXTENTS + 2` words. So:

    slot = (symbol size - (3 * MAX_SLAB_EXTENTS + 2) * word) / floor

| measured | symbol | size | floor | slot |
|---|---|---|---|---|
| main, riscv32 `.o` | `PROCESSES` | 0x9b0 | 3 | 808 |
| main, riscv32 `.o` | `WAITERS` | 0x88 | 4 | 20 |
| pre-SO-92.p2, riscv32 linked ELF | `PROCESSES` | 0x9a4 | 3 | 804 |
| pre-SO-92.p2, arm64 linked ELF | `PROCESSES` | 0x1120 | 3 | 1424 |

- The last two match what the scratch kernel's `slab_geometry` printed at
  boot (0x324, 0x590), so the formula is right on both word sizes.
- The local symbols survive linking with `--gc-sections`.
- The thread unit is its slot plus `THREAD_FRAME_STRIDE`. The pipe unit is
  its slot plus `PIPE_INFLIGHT` exchanges, both read the same way.
- Alignments are taken as `THREAD_FRAME_STRIDE` for threads and 16 bytes
  for every other kind, so the builder pads each extent by its kind's
  alignment.

What this costs:

- The builder depends on the compiler's spelling of a static's symbol
  (`static kcore.pslot.PROCESSES`), and on `Slab`'s layout. A missing
  symbol, or a size that does not divide, stops the builder with the
  kind's name.
- U2 adds a gate case that boots the limits block on every board, so a
  wrong unit is red at once.

What it removes:

- the hand-kept geometry literal of revision 2 (which was already stale:
  808 on main, not 804);
- its boot comparison.

An exported unit static (`@export static SOS_UNIT_WAITER: WaiterSlot`) is
refused: "type `kcore.waitables.WaiterSlot` is not a C-ABI-safe data
layout". So the symbol table of the slabs themselves is the route.

**Gap 11.4 (SL-1095) is still worth having filed, as nice to have.** A
`sizeof` constant would let the kernel export its units in
`sos_board_info`. The builder would then rely on neither the compiler's
symbol spelling nor `Slab`'s layout.

### 6.4 What a table reaches

The block reaches every `SlabKind`:

- Events, Waiters, Interrupts, Attachments and Timers;
- Memories, IoMemories, Mappings and FreeRanges;
- Threads (with their frames) and Pipes (with their exchanges);
- process bodies and records.

It also reaches the children's quota defaults and the boot flags.

It does not reach these, which stay per board:

- **arrays in every body:** `MAX_HANDLES` (the handle table and the boot
  set) and the grant record;
- **ring and table dimensions:** `PIPE_INFLIGHT`, the extent constants,
  the frame stride and `CLOCK_KINDS`;
- **what the HAL sizes:** arm64's `PROT_DOMAIN_SLOTS` (5) and the stats
  region;
- **root's region, the stack length, and the kernel's own stack and
  arena.**

**`MAX_HANDLES` is the one the box comes close to** (§5.3, question 2).
Under rule 8.5's carving the box fits at 14 of 16. "Per-board constants" is
an acceptable line. Raising any of these costs every body on the board:
two 16-byte entries per handle on riscv32, one in the table and one in the
boot set. It is a sawos patch to that board.

### 6.5 Where the numbers are checked

**Rule 6.4.** Relations between per-board constants stay as `static_assert`s
in the per-board kernel:

- records per body;
- the grant record against the HAL;
- the process floor against `PROT_DOMAIN_SLOTS`;
- the slab capacity ceiling;
- the frame stride and `PIPE_INFLIGHT`;
- each floor's quota default below its floor.

**Rule 6.5.** Relations involving an image's numbers become the builder's
checks, by name and before anything links, with the kernel's
`fatal_image` at boot behind them:

- each quota default below its slab's boot capacity;
- the mapping default plus the image's grant rows within the budget;
- attachments at least the waitables' sum (the builder raises attachments
  when the image raises a waitable and states none);
- processes within `PROT_DOMAIN_SLOTS`;
- totals within `MAX_SLAB_CAPACITY`;
- the reserve holding every extent;
- no limit below its floor.

### 6.6 What it buys and loses

It buys:

- one kernel object per board and entry, tested bit for bit by the gate
  under every image;
- a product that never compiles the kernel;
- a table precedent the boot rows already set.

Against compiling the kernel per image, it loses:

- per-body limits per image;
- compile-time refusal by name, which becomes the builder's refusal by
  name;
- `.bss` sized exactly as asked, since a floor is paid whether used or not;
- independence from the compiler's symbol spelling (rule 6.3).

**Recommended: this.** Compiling the kernel per image stays in section 13
as an alternative.

---

## 7. Per-process quotas: the door

**Rule 7.1.** A launcher sets a child's quota rows between its create and
its start, through the child's Process handle. An sosimg carries no quota,
and a process cannot set its own.

**Rule 7.2.** The op, as spec text for §2.7 (after `RestrictRights`):

> - **Quotas.** `ProcessOp.QuotaSet(kind, limit)` sets the named process's
>   limit for one `QuotaKind`. It is gated by `ProcessRight.QuotaSet`,
>   which the default set carries. The named process must be `Created`, and
>   naming one that is not is a `BadState` fault. A kind the op does not
>   know is a `BadArg` fault.
> - **A limit is a per-process cap, not a budget.** It bounds how many of
>   that kind the one process may hold. Nothing is spent from the caller's
>   row, and a launcher may set several children to its own limit. The
>   machine's bound is the slab.
> - A limit is a number from 0 to 0xFFFE. The unlimited sentinel
>   (0xFFFF), which only root's rows carry, is a `BadArg` fault as an
>   argument, so no process is handed an unlimited row.
> - For every row but `Mapping`, a limit above the caller's own limit for
>   that kind is a `BadArg` fault, so a launcher cannot hand a child a cap
>   above its own. A `Handle` limit above `MAX_HANDLES` is a `BadArg`
>   fault.
> - A `Mapping` limit is bounded by the child's own free grant rows and not
>   by the caller's. The caller's row says how much of its own domain its
>   image left, which is nothing about the child. A limit past the child's
>   free rows answers `NoResource` and sets nothing, because a launcher
>   cannot read the child's image.
> - A limit below what the child already uses is a `BadArg` fault. On a
>   `Created` child only the `Handle` row can be in use, through gives.
> - `ProcessOp.QuotaGet(kind)` answers the named process's limit and use,
>   gated by `ProcessRight.Stats`.

§2.9's bullet "A child gets `kcore.limits`' documented defaults; there is
no per-create quota argument in v1" becomes:

> - A child starts with the image's quota defaults, which the boot table
>   sets over the board's (§12), and its launcher may set any row before it
>   starts (`ProcessOp.QuotaSet`, §2.7). A row is a cap on one process,
>   not a share of its launcher's.

**Rule 7.3.** The builder refuses an image whose grant is below what a
process declares, and the stock root applies `QuotaSet` only for raised
rows.

**Rule 7.4.** "How many memory ranges a process may map" is the `Mapping`
row, bounded by `GRANT_ROW_BUDGET` less the image's rows: 4 on riscv32
and the S3, 5 on arm64. The box's PWM driver uses 3.

---

## 8. Root's interfaces

### 8.1 Kernel contract (spec; cited)

1. **Entry.** System in the first argument register, with
   `root_system_rights()` (§5.7, §2.8).
2. **Its region and image.** Loaded into the board's root region, with the
   stack at the top (§5.6, §12). Its quotas are unlimited except the
   mapping clamp (§2.9). Its ceilings are unrestricted (§2.7).
3. **The boot handles.** System, Process and Thread at words 1 to 3, then
   one handle per row, each already in root's table and announced by a
   record in row order; `Drained` after the last (§12).
4. **Row kinds and rights.** `Ram` mints an allocation, `Device` an
   `IoMemory`, `Pool` a pool, each with its kind's rights and charged to
   root (§12). All three are built (SO-92.p2).
5. **Boot flags and limits.** Bit 0 turns off the tick. The limits block
   sets the slabs' boot capacity and the children's quota defaults before
   root runs (new, rule 6.2).
6. **Root's end is the machine's** (§8).
7. **Every other process** is root's to create (§12).

### 8.2 Generated per image (new text for §12)

8. **The boot table.** Proposed §12 text, replacing "The tree's stitcher
   (`tools/sos_runner.py`) writes, in this order...":

   > The image builder writes, in this order, which root's config reads as
   > tags: one image blob per child, one destination per child, then the
   > image's rows in the order its description lists them. After the rows
   > comes either nothing or the image's limits block, which the kernel
   > applies before root runs.

9. **The plan module, `imageplan`.** For each child: its blob and
   destination ordinals, its ceilings and quota rows, the records to give
   in order (tag, source, keep mask, and for a window the carve offset and
   length), its start tag and its exit policy. Also the connections and
   the setup writes. Its types live in `bootplan`.

### 8.3 Convention among userspace programs (new text for §12)

> **The launch convention** (userspace, not kernel surface). A launcher
> that follows it:
>
> - gives a child exactly the records its declaration names, under their
>   tags, in declared order, before it starts the child;
> - starts the child with its System record's tag, masked with
>   `Debug | ProcessSelf | ClockGet | Transfer` unless the image grants more;
> - sets the child's quota rows to at least what it declares, before any
>   give;
> - gives windows, lines and endpoints as capabilities; the child maps its
>   own windows and finds them through `Mapping.base()`;
> - gives a client an inlet minted with `Post | Transfer`, and a server the
>   outlet;
> - keeps the child's Process handle, and is the one who learns of its
>   death.

And for §2.7's `Give` list (the reviewer confirmed the kernel does this):

> - `BootHandleNext` answers records in the order they were queued: root's
>   in row order, a child's in the order its launcher gave them. The record
>   `Start` consumed is never answered.

### 8.4 The stock root

**Rule 8.1.** sawos's `root/` becomes the stock root. It imports
`imageplan` and does exactly this:

1. Derive its Process handle.
2. Create each child, releasing its blob and destination at once.
3. Apply the setup writes (rule 8.5).
4. Create each connection, and give each child its records in declared
   order. Each window is carved from a peripheral row, and each sibling
   minted and each line bound, at the moment it is given. Root releases
   whatever it does not keep.
5. Start each child, then supervise: one Waiter and a line per death.
6. Stop the machine when a `shutdown` child ends, or when every child has
   ended.

**Rule 8.2.** A failed step prints the step, the child and the status,
and stops the machine.

**Rule 8.3.** It has no device code, no address and no line number of its
own.

**Rule 8.4.** It gives records in each child's declared order.

**Rule 8.5.** **Setup writes.** Each `[[setup]]` entry names a board
register and a `set`, `clear` or `write` value. For each, the stock root:

1. carves the register's 4-byte word from a peripheral row;
2. maps it;
3. reads, modifies and writes it;
4. unmaps it, and releases the mapping and the carve.

This is data, not device code. It runs before any child starts, so its
order against the children is fixed.

---

## 9. The builder, in Saw, and how a product finds sawos

### 9.1 What it is and how U1 is shaped

**Rule 9.1.** The builder is `tools/sosimage/`, a hosted Saw package built
by Blade. Its subcommands are:

- `kernel`: compile a board's kernel for one entry;
- `link`: stitch and link one image from finished process images;
- `build`: everything for a hand-written image, packages included, from
  U4;
- `flash`, `dump` and `check`.

**Rule 9.2. U1 keeps the runner's transcript** (t9). The runner keeps its
package phase exactly as it is: each distinct package built once per
profile, before any case, by `blade build --force`, printing today's lines.
Then:

1. **Once per run, for each profile and kernel entry the selected cases
   use,** the runner calls `sosimage kernel`, writing to a path unique to
   that profile and entry. This happens before the cases, as
   `_build_shared` does for the native objects, so no two compiles share an
   output (gap 11.12).
2. **Per case,** the runner writes a test image (rule 9.3) naming the
   finished sosimg paths and the kernel object, into the case's own
   staging directory, and calls `sosimage link` on it.

Packages move into the builder only in U4, with `--out-dir` per image. By
then the builder runs its own package phase once per profile, before the
cases, and prints the same lines.

**Rule 9.3. The test image is JSON and wider than the hand-written format**
(t10). It carries what the runner's cases need:

- the kernel entry, an `asm` payload, a `child_asm`;
- explicit addresses (pool base and length, region bases, device windows);
- row kinds (`pool_blobs`), `pool_skew`, `pool_short_top`, boot flags;
- the S3's `loader_fixture` and `expect_build_error`.

It is machine-written and versioned, and hand-written descriptions are
never asked to grow these keys. The Saw `check_in_place` repeats today's
message byte for byte, because `slot_mismatch` asserts it.
`_check_kernel_fp_free` stays in the runner and runs on the linked ELF.
The C3 smoke stays in Python in U1 and moves in U9.

**Rule 9.4. No kernel cache across runs** (t11). A key by revision would
serve a stale kernel to every uncommitted edit, and every kernel mutant
would pass. The kernel is compiled once per run for each profile and entry
(0.7 s each). For a product, `sosimage build` compiles its board's kernel
once per invocation. If a cache is ever wanted, its key is the contents of
every input, not the revision: the kernel and HAL sources, the entry, the
profile, the compiler binary, and `imgformat`'s sources.

**Rule 9.5. The generators** are inside the builder, in Saw. They write:

- the boot-table stub, with its rows and its limits block;
- the `.incbin` stubs;
- the per-process linker scripts and `image_slots.ld`;
- `imageplan`.

**Rule 9.6. Blade, as pinned** (t12, the reviewer's runs; the first
reproduced by me):

1. **Build avoidance does not see a linker script change.** The builder
   passes `--force` on every build, as the runner does, until S1 makes
   Blade hash what `--linker-script` names.
2. **`--out-dir` covers `.build/<triple>/` only.** The lock stays beside
   its manifest, so every sawos package builds with the lock it was
   committed with.
3. **A product copied out of the tree carries no `Saw.lock`.** Blade writes
   a fresh one; a committed lock beside a repointed manifest is refused.

**Rule 9.7. The `.sawlang` link.** The builder depends on `libs/toml` and
`libs/imgformat` by path through `<sawos>/.sawlang`. The runner writes that
link before it builds the builder, from the checkout the resolver found.
`tools/toolchain.py` is a verbatim copy of sawlang's resolver and is not
changed. For a product, `tools/sawos.py` writes it. This is a workaround
for gap 11.9, and U1 needs it, so it lands in U1.

### 9.2 How a product finds sawos

**Rule 9.8.** A product has a `sawos.pin` and a verbatim copy of sawos's
`tools/sawos.py`, in Python:

- it takes `SAWOS_ROOT` if set, else fetches the pin into
  `~/.cache/sawos/<sha>/`;
- it links the result as `.sawos`, and resolves every sawos path through
  that link, `SAWOS_ROOT` included. A package's identity is its path as
  written, and two spellings of one package are refused ("required from two
  different sources", the reviewer's run);
- it resolves sawos's own sawlang pin, writes `<sawos>/.sawlang`, builds
  Blade and `sosimage`, and runs it.

### 9.3 Testing and flashing a product

**Rule 9.9.** `python3 tools/sawos.py build|run|test|flash`. Judging is
`sosimage check`, in Saw. Booting goes through the Python harness until
gap 11.2 closes. `flash` writes the image and prints its SHA-256 (through
`shasum` until gap 11.10 closes) and the `esptool` line.

---

## 10. Every tool: Saw or not

**The builder (`tools/sosimage`).** Saw. What it needs works or has a
workaround I ran:

- a child's working directory through `sh -c 'cd ...'`;
- a directory test by whether `Directory.list` fails;
- a hex function;
- `.sawlang` for the libraries.

**The generators.** Saw, inside the builder.

**Reading the kernel's facts** (`sos_board_info`, the slab symbols). Saw:
reading ELF symbols and integers, the same byte work as reading an sosimg,
which I did.

**The harness.**
- Judging is Saw.
- Booting is Python until gap 11.2. Two routes are named and untried: QEMU
  on sockets, or libc.

**The resolvers** (`tools/toolchain.py`, a product's `tools/sawos.py`).
Python. They run before any Saw compiler exists, and `bin/sawc` builds
that compiler with `compiler/tools/build.py`, which is Python.

**`tools/sos_runner.py`.** Python for now, moving in four steps:

1. **U1:** kernel compile, stitching, linking and the S3 flash image move
   to `sosimage`. The package phase, the scheduler and the report stay.
2. **U8:** judging moves.
3. **U8, after S3:** booting moves.
4. **Unscheduled:** the 290 case definitions, most of them commented
   prose. They move to files only once TOML keeps comments (gap 11.1).

The C3 smoke path stays Python until U9.

**`tools/sosimg_dump.py`.** Becomes `sosimage dump`.

**`esptool`.** Espressif's tool; the builder prints the command.

---

## 11. Saw gaps

Each entry is written to be filed with the sawlang lead as it stands.
Unless an entry says otherwise, all were run against sawlang 0.23.0 (the
lead's checkout at `.claude/lead/sawlang-0.23`, `sawc-stage2` and its
`blade`) on macOS arm64. "Reproduced" means I ran it; "read" means I only
read the source. Entries 11.1 to 11.10 were filed after revision 2, as
SL-1092 to SL-1101. Entry 11.7 gains a reproduced behaviour, and 11.12 is
new.

### 11.1 `libs/toml` misreads TOML outside its subset, silently (SL-1092)

- **Tried (reproduced).** `TomlDoc.parse` from a hosted package.
- **What happened.**
  - A top-level `format = 1` is dropped.
  - `tier = "flat"   # trailing comment` keeps its quotes and comment.
  - `0x6000_0000` is a string, and `get_int` answers `None`.
  - `[57]` is a raw string.
  - `[[process]]` becomes sections named `[process]`, and `section()` finds
    only the first.
  - A two-line inline table becomes bogus keys.
  - `{ gpio = "p, q" }` reads `gpio` as `"p`.
  - A two-line array fails: `TOML parse error: Invalid line: "b"]
    (line 19)`.
- **Expected.** TOML 1.0 for these constructs, and an error outside the
  supported subset.
- **Why.** The board, image, process and test descriptions are TOML.
- **How badly.** Blocks hand-written descriptions (U3). The runner's test
  images are JSON, so U1 is not blocked.

### 11.2 `std.process` cannot run an interactive child (SL-1093)

- **Tried (reproduced).** `cmd.current_dir`, `child.write_stdin`,
  `child.read_stdout_line`, `child.kill`.
- **What happened.** `member.unknown` for each. The surface is
  `Command.arg/env/merge_stderr/wait/spawn/output/output_bytes` and
  `Child.wait`.
- **Expected.** A working directory, stdin, streamed stdout, kill, and a
  wait with a deadline.
- **Why.** Blade runs in a package's directory, and the harness types at a
  serial port, streams the console, and kills at a deadline.
- **How badly.** The working directory has a workaround (`sh -c`). The
  rest blocks a Saw harness.

### 11.3 No way to tell a directory from a file (SL-1094)

- **Tried (reproduced).** A walk recursing on `Directory.exists` found 0
  files.
- **What happened.** `exists` is file-or-directory, and there is no
  `is_dir` or stat.
- **Expected.** `is_dir` or a stat.
- **Why.** Walking trees.
- **How badly.** Has a workaround: `Directory.list` succeeding. Nice to
  have.

### 11.4 A struct's size cannot be a constant (SL-1095)

- **Tried (reproduced).** `sizeof<ProcessBody>()` in a static initializer,
  and in a `static_assert`.
- **What happened.**
  - `static.not-constant`: "... laid out only by code generation, so
    `sizeof` and `alignof` measure it only in a `static_assert`".
  - `slice.not-yet`: "a layout this `static_assert` measures is not in the
    bootstrap slice yet: a raw-backed case whose value was not evaluated".
- **Expected.** `sizeof` and `alignof` of any sized struct as a constant.
- **Why.** The kernel would export its slot sizes directly.
- **How badly.** Now has a workaround that keeps nothing by hand: the
  builder reads the slab statics' symbol sizes (rule 6.3). That workaround
  depends on the compiler's symbol spelling and `Slab`'s layout. Nice to
  have.

### 11.5 No hexadecimal formatting (SL-1096)

- **Tried (reproduced).** `print("{:x}", 255)`.
- **What happened.** `parse-error 2:13 expected an expression`.
- **Expected.** A public hex rendering with a width.
- **Why.** Scripts, stubs and diagnostics name addresses.
- **How badly.** Has a workaround (an 8-line function). Nice to have.

### 11.6 Reading a JSON object needs the document mutable (SL-1097)

- **Tried (reproduced).** `top.get("name")` on `as_object()`'s map.
- **What happened.** `type.bound ... it is declared where V: Copy`.
- **Expected.** A shared lookup on a `Map` of non-`Copy` values.
- **Why.** The builder reads JSON test images.
- **How badly.** Has a workaround (`var doc`, `borrow let`, `find`). Nice
  to have.

### 11.7 `blade build` cannot place its output (SL-1098, extended)

- **Tried (reproduced).**
  - `--linker-script`, `--out-dir` and `--module-path NAME=DIR` on an
    sosimg build.
  - New: in a package whose manifest names a local `slot.ld`, build once,
    move `ORIGIN` from 0x802C0000 to 0x80300000, and build again without
    `--force`.
- **What happened.**
  - Each flag printed `(up to date)` and did nothing.
  - The rebuild printed `Finished ... hello2.sosimg (up to date)`, and the
    ELF's first `LOAD` stayed at 0x802c0000. With `--force` it moved to
    0x80300000. The reviewer adds, from reading, that the hash covers
    `Saw.toml`, `Saw.lock` and `src/*.saw`, and not the script, the native
    C files, `SAWC` or the tools.
- **Expected.**
  - `--linker-script` overriding the manifest's;
  - `--out-dir` replacing `.build/<triple>/`, with the lock left beside its
    manifest;
  - a repeatable `--module-path` passed through to sawc;
  - an error for an unknown flag;
  - build avoidance that hashes every input that changes the output: the
    linker script, the native sources, `SAWC` and the tools.
- **Why.** Each process is linked per image at an assigned address, and a
  stale link is silent. Parallel images must not share output.
- **How badly.** Blocks generated addresses (U4) and generated modules
  (U6). Workaround for the avoidance: always `--force`.

### 11.8 A git dependency cannot name a package in a subdirectory (SL-1099)

- **Tried (reproduced).** A tagged scratch repository with `libs/greet`,
  and `{ git = ..., version = "0.1.0", subdir = "libs/greet" }`.
- **What happened.** `cannot read manifest for greet at
  .blade/deps/greet-0.1.0`: `subdir` is ignored silently. Against sawos:
  `no tagged version of sos satisfies 0.1.0`.
- **Expected.** A subdirectory dependency pinned by tag or commit, and an
  error for an unknown key.
- **Why.** Products depend on packages inside one sawos repository.
- **How badly.** Has a workaround (path dependencies through `.sawos`).

### 11.9 No portable dependency on a toolchain library (SL-1100)

- **Tried (reproduced).** An absolute path to the checkout's `libs/toml`,
  and a path through a link; both build.
- **What happened.** One is a single machine's path, and the other needs a
  link someone makes.
- **Expected.** For example `{ toolchain = "libs/toml" }`, resolved from
  the running sawc.
- **Why.** The builder needs `toml` and `imgformat`.
- **How badly.** Has a workaround (the `.sawlang` link, rule 9.7).

### 11.10 No SHA-256 (SL-1101)

- **Tried (reproduced).** `import std.sha256.{Sha256}`.
- **What happened.** `import.unknown-module no module std.sha256`.
- **Expected.** SHA-256 over `Data`.
- **Why.** A board image is sent with its checksum.
- **How badly.** Has a workaround (`shasum`). Nice to have.

### 11.11 What works, for the record

- hosted Blade builds;
- path dependencies through links;
- binary file I/O;
- paths and `Env`;
- `Map`, sorting and `StringBuilder`;
- `Command` with arguments and an environment, including driving
  `blade build`;
- `std.json`;
- `extern "C"`;
- reading sosimg and ELF bytes;
- an exported integer array built from imported statics;
- a cold Blade compile in 0.8 s.

### 11.12 Two `sawc` runs to one output path collide on `<output>.tool` (new)

- **Tried (reproduced).** Two `sawc-stage2` compiles of the same kernel
  source with the same `-o k.o`, started together, eight times (sixteen
  runs). The reviewer saw the same with two `blade build --force` in one
  package, which run two `sawc` to one `.o`.
- **What happened.** 7 of 16 runs failed with one of:
  ```
  sawc build: cannot remove .../race/k.o.tool: unlink ".../race/k.o.tool"
    failed (not found)
  sawc: cannot open .../race/k.o.tool: open ".../race/k.o.tool" failed
    (not found)
  ```
- **Expected.** Two builds to one path are a mistake, but the failure
  should say so. Better still: the scratch file named per process, so the
  output is simply last-writer-wins, or a lock on the output naming the
  other build.
- **Why.** sawos's runner and builder run many compiles in parallel. A
  collision there is a sawos bug, and it should read as one, not as a
  missing file.
- **How badly.** Nice to have. sawos's fix is to never compile twice to one
  path (rule 9.2).

---

## 12. An attack on this design

**12.1 Authority.** A description can give any child anything the board
offers, by design. It cannot give what the board does not list, a quota
cap above its launcher's, or an unlimited row. The builder prints every
right above the convention per process.

**12.2 Root's handle table.** Every row is a handle in root's table from
its first instruction (§1.3). With one peripheral row, an image of n
children starts root at 4 + 2n + 1 and needs one more for its first create.
So five children is the most a 16-entry table launches. Past that there
are two ways out, each a cost the owner may choose later (question 2):

- raise `MAX_HANDLES` for the board, 32 bytes per handle per body on
  riscv32;
- change the kernel to mint a boot row's handle when its record is
  drained, which is a §12 change and moves handle words in transcripts.

**12.3 A wrong description at boot.** Most errors are refused at build.
The rest stop the machine with a printed reason.

**12.4 The flat tier.** On the S3, windows and quotas are discipline. The
narrower PWM windows of question 3 matter for the next protected board,
not for this one.

**12.5 sawos moving under a product's pin.** The formats are versioned.
API changes are caught at compile, and board floors are printed with every
build. The scratch product in sawos's gate (U7) catches breakage first.

**12.6 The suite.** It loses the readable hand-kept child scripts. It
gains several children on the S3, and one kernel object per profile and
entry instead of one per case. `tests/irq-lines` can be generated from
`board.toml`. `tests/poolbase_*` cannot (rule 5.5).

**12.7 The per-board kernel.** The builder reads slot sizes through the
compiler's symbol spelling and `Slab`'s layout. A change to either stops
the builder loudly, and a gate case boots a limits block on every board.

**12.8 Writing the tools in Saw.**
- The bootstrap is Python, then git, then sawlang's build, then Blade,
  then `sosimage`.
- A sawlang release that breaks hosted builds breaks sawos's gate. It is
  caught at the pin bump, and the old pin still works.
- Build time is a second or so per tool (measured).

**12.9 Two languages for a while.** Runner and builder meet in JSON test
images written by one function and read by one. The transcript diff
guards it.

**12.10 Tags written twice.** `BootSet` finds a disagreement at the
child's first instruction.

---

## 13. Alternatives rejected

- **Compiling the kernel per image.**
  - It compiles (§16.1), and it reaches the per-body arrays.
  - Rejected: every image would boot a kernel the gate never ran, and
    products would compile kernels.
  - It is the way if a product ever needs `MAX_HANDLES` per image.
- **Raising `MAX_HANDLES` now, instead of carving.** It is cheap in RAM
  and needs no root code. But it changes the cases that meet today's wall,
  and it leaves the windows whole, which question 3 asks about. It stays
  as the way past five children.
- **Minting boot handles at drain.** It fixes the class, since root's
  table would hold only what root has drained. But it changes the kernel,
  §12 and transcripts. Deferred until an image needs it.
- **Not minting the boot Process and Thread handles.** It saves two
  entries, at the price of a §12 change. Not needed for the box.
- **A kernel cache keyed by revision.** Stale under every edit (t11).
- **One builder process per case that builds packages.** It races and
  loses the transcript (t9).
- **A hand-kept slot-size literal.** Superseded by the symbol table (t7).
- **Root donates at start; a builder in Python; descriptions in JSON;
  quota in the sosimg; the kernel as a Blade package; the runner's cases
  as files now; a binary plan in a row; position-independent images; fixed
  driver slots; git dependencies; an `SOS_LD` wrapper; pins as a `Setup`
  message from root; a submodule instead of a pin.** Each as revision 2
  said.

---

## 14. Cost and order of work

"Sonnet" and "Opus" follow CLAUDE.md. Units marked sawlang are the sawlang
lead's.

**U1. Kernel, stitching and linking in Saw, transcript-identical (sawos;
Sonnet, medium).** Rules 9.1 to 9.4 and 9.7.

- `sosimage kernel` and `sosimage link`, including the S3 flash image,
  `check_in_place` (same message) and `dump`.
- The JSON test image.
- The runner's package phase unchanged.
- One kernel compile per run for each profile and entry.
- `.sawlang` written by the runner.

The C3 path is untouched. Test: the gate and both gating smokes, every
transcript, total and image size identical to the base.

**S1 (sawlang).** Blade's flags, an error on unknown flags, and build
avoidance over every input (gap 11.7).

**S2 (sawlang).** `libs/toml` (gap 11.1).

**U2. The linked limits block (sawos kernel and spec; Opus for the spec,
Sonnet to build; medium).** Rules 6.1 to 6.5:

- table version 5;
- `sos_board_info`;
- quota defaults read at boot;
- the reserve checks;
- the builder's sizing from symbols, and its refusals.

Tests:

- `process_donate_limits`;
- a five-process case with no donation;
- negatives: a reserve over root's region, over the kernel, a limit below
  its floor, garbage after the rows;
- an image at root's handle limit, and one past it;
- the limits block booted on every board.

**U3. The description formats (sawos; Opus for the formats; medium; after
S2).** `board.toml` with peripheral rows, registers and arrays; the image
TOML; rule 5.4's checks; `uart_service_ns16550` from a file, identical to
its case.

**U4. Generated addresses (sawos; Sonnet, medium; after S1's pin bump and
U3).** Rules 5.5 to 5.8:

- packages into the builder, with `--out-dir` and its own package phase;
- computed regions;
- `.drom` behind the last slot;
- the hand-kept child scripts go.

Test: the S3 smoke gains two- and four-child cases.

**U5. The quota door (sawos kernel and spec; Opus for the spec, Sonnet to
build; medium).** Rule 7.2. Tests on every profile:

- a raised row met exactly;
- after start, `BadState`;
- above the launcher's own, `BadArg`;
- the sentinel, `BadArg`;
- a `Mapping` limit past the child's free rows, `NoResource`;
- a limit below use, `BadArg`, after a give;
- a grandchild capped.

**U6. Declarations, the stock root, setup writes and carving (sawos; Opus
for `bootplan`; medium-large; after U4 and U5).** `process.toml` with the
builder's handle counting, `BootSet`, `imageplan`, the stock root (rule
8.5), and the UART drivers on `Mapping.base()`. Test: `uart_service_stock`,
and negatives for each declaration mismatch.

**U7. Products (sawos; Sonnet, small).** `tools/sawos.py`, `.sawos`, and a
gate case building a scratch product with no committed lock.

**S3 (sawlang).** `std.process` (gap 11.2).

**U8. The harness in Saw (sawos; Sonnet, medium).** `check` after U1;
booting after S3.

**U9. The C3 smoke through the builder (sawos; Sonnet, small).** Its
`board.toml`, its stitching, and its `objcopy` flatten.

**Where SO-77 and SO-78 sit.**

- SO-77 U0 can run now.
- SO-77's drivers come after U4 and U5, with `process.toml` and config
  inputs. The PWM driver's windows are as question 3 is decided.
- SO-77 U1 is absorbed by U3, and its U5 is replaced by U2, U4 and U5.
- SO-78, the box repository, comes after U6, U7 and the drivers.

**The order, at most two agents at once across both repositories:**

1. U1, beside S1.
2. U2, beside S2.
3. U5, beside U3.
4. U4, beside S3.
5. U6, beside SO-77's GPIO driver.
6. U7, beside the PWM driver.
7. U8, beside U9.
8. SO-78.

---

## 15. Questions for the owner

New or changed questions are marked.

1. **The linked limits block (changed).** Do you accept its terms? The
   per-body arrays stay per board. A limit can be raised and not lowered.
   Slot sizes come from the kernel's symbols, with no number kept by hand.
   *Recommended: yes.*
2. **Root's handle table (new; t1).** The box as drawn does not launch:
   12 rows fill root's table before its first create. The options:
   - (a) the stock root takes one peripheral row and carves each window as
     it gives it. The box has 9 rows, and root peaks at 14 of 16. No
     kernel change. Five children is the most.
   - (b) raise `MAX_HANDLES` on the S3 or everywhere. Cheap in RAM; it
     changes the cases at today's wall, and leaves windows whole.
   - (c) mint a boot row's handle at its drain. It fixes the class, but
     it is a kernel and §12 change.

   *Recommended: (a) now, with (b) as the way past five children.*
3. **What the PWM driver may touch (changed; t8).**
   - (1) four whole windows (LEDC, GPIO, IO MUX, the 64-byte clock
     window). That reaches every pad's routing, the console's clock gate
     and reset, and the CPU's clock select.
   - (2) named register ranges carved by root: LEDC whole, plus the two
     pads' out-select words and IO MUX words.
   - (3) a table of one-time register writes root makes before starting
     anyone.

   *Recommended: (3) for the clock gate, its reset and the pads' output
   enable, and (2) for the pads' routing* (as the box is drawn). The pad
   keeps its reset state until the driver's `Setup`, then shows idle low.
4. **The kernel's reserve.** A RAM range the builder assigns, which the
   builder places and the kernel checks against root's region.
   *Recommended: yes* (t2).
5. **How a driver gets its pins.** *Recommended: a build-time module.*
6. **The description format.** TOML through a completed `libs/toml`, with
   JSON for the runner's test images. *Recommended: yes.*
7. **Sawlang ahead of the box.** S1 and S2. *Recommended: yes.* S3 can
   follow.
8. **The runner's future.** *Recommended:* stepwise, and U1 smaller than
   revision 2 said (t9).
9. **The C3 smoke (new; t10).** It stays in Python through U1 and moves in
   U9. *Recommended: yes.*
10. **Test images per profile (new; t10).** A hand-written image names one
    board, and a case that runs on two profiles is two test images.
    *Recommended: yes.*
11. **The resolvers stay Python.** *Recommended: yes.*
12. **How a product finds sawos.** *Recommended: a pin and a `.sawos`
    link.*
13. **What the stock root does when a child ends.** *Recommended:* report
    it; stop on a `shutdown` child or when all have ended.
14. **Where the box lives.** *Recommended: its own repository.*

---

## 16. Verified, read, the reviewer's, and not checked

### 16.1 Verified by me, by running (scratch only)

**The linked limits block.** A scratch kernel built before SO-92.p2, run
under the suite lock on riscv32 and arm64 (§6.1).

**Slot sizes from symbols.**
- Main's riscv32 kernel object, compiled in scratch from a shallow clone
  of main: `PROCESSES` 0x9b0 gives 808 bytes, and `WAITERS` 0x88 gives 20.
- The linked scratch kernels: 804 bytes on riscv32 and 1424 on arm64,
  matching what `slab_geometry` printed at boot.
- An exported integer `sos_board_info` built from imported statics, HAL
  ones included, compiled to a 48-byte data symbol.
- An exported struct static was refused (`export.c-abi`).

**Gap 11.12.** Two `sawc` runs to one output collided in 7 of 16 runs.

**Blade build avoidance.** It kept a stale link address until `--force`.

**From revision 2:**
- the hosted Saw probes behind section 11;
- reading an sosimg with `imgformat`;
- driving Blade from Saw;
- a package outside the tree building through a link (riscv32);
- `INCLUDE` through `-L` with Homebrew's `ld.lld`;
- a cross-module `kconfig`-style module compiling.

### 16.2 Read, not run

- §1, from the files named there, re-read on main for the boot table
  (version 4, three kinds) and for the handle per row.
- The donation path.
- That a give charges only `Handle`.
- That a carve must be a naturally aligned power of two of at least the
  grain (`device_window_ok`).
- The box's handle counts, which come from the plan and these rules.
- The pad-state reasoning, from SO-77's notes.

### 16.3 The reviewer's runs, taken in

- **Root's handle table:** `uart_service_ns16550` with 10, 11 and 12 rows
  (t1).
- **Concurrent builds:** two `blade build --force` in one package failing
  intermittently (t9), and two kernel compiles to one path failing (t11).
- **Blade:** build avoidance blind to the script; lock relocation; and a
  committed lock beside a repointed manifest refused (t12).
- **The `.sawos` link:** a package outside the tree builds for riscv32,
  arm64 and Xtensa through it, byte-identical to the in-tree package's,
  with nothing written inside the linked tree.
- **Espressif's `ld.lld`:** resolves `INCLUDE` through `-L`.
- **Identity:** one package reached by two spellings is refused.
- **The counts:** the 290 cases, the kernel-entry counts and the pool
  cases (t10, summary).

### 16.4 Not checked

- The S3's slot sizes, so the box's reserve and RAM fit are not computed.
- Carving windows from a peripheral row, and the setup writes, under the
  S3 emulator or on a part. The prototype ran only on riscv32 and arm64,
  and not on main.
- The pad's state between root's setup and the driver's `Setup`.
- Whether the box's processes link in the regions they compute to. None is
  written.
- The QEMU socket route.
- Hosted Saw on Linux.
- The device addresses, which are SO-77's from ESP-IDF headers.

---

## 17. Response to the review

| item | finding | response | where |
|---|---|---|---|
| t1 | 12 rows fill root's table before the first create | Accepted. Root counts 4 + rows. Recommended: one peripheral row, carved per window (9 rows, peak 14/16). Raising `MAX_HANDLES` and minting at drain kept as later options. Builder counts root and children step by step; U2 boots at the limit and past it | §0, 1.3, 5.1, 5.3, 6.4, 12.2, 13, Q2 |
| t2 | reserve checks don't keep it off root's region or the kernel | Accepted. The builder is trusted for placement. The kernel checks that the extents fit, the RAM range, and root's region. The kernel image is the builder's to check. "No row overlaps" dropped. Negative cases in U2 | §6.2 rule 6.3, U2 |
| t3 | a cap, not a budget; `Mapping` bound; the sentinel | Accepted. Rule says per-process cap. `Mapping` is bounded by the child's free rows only. 0xFFFF refused, so 0 to 0xFFFE. Below-use only via `Handle`; U5 gives first | §7, U5 |
| t4 | a given window spends no `IoMemory`; PWM at `Handle` 12 | Accepted. Declarations state charged rows only, and the builder computes the `handles` floor. PWM recounted at 10 with three windows | §1.3, 4.3, 5.3 |
| t5 | slot range runs over `.drom` at 0x20_0000 | Accepted. `top` is 0x20_0000 now. U4 moves `.drom` behind the last slot | §3, 5.2, U4 |
| t6 | 24 KiB region leaves no data room | Accepted. Regions are computed from measured `.data`/`.bss` plus arena plus stack. The 24 KiB defaults are gone | §4.4, 5.6, 5.3 |
| t7 | 804 stale; sizes are in the symbol table | Accepted, re-run on main (808). Literal and boot check dropped. Gap 11.4 kept as nice to have | §6.3, 11.4 |
| t8 | four windows reach the console's clock and every pad | Accepted as an owner question with three shapes. Recommended: setup writes for the clock gate, reset and enable, and carved ranges for the pads. Pad state stated | §3, 5.3, 8.5, Q3 |
| t9 | per-case builder races, loses package lines | Accepted. Runner keeps its package phase. `sosimage kernel` once per profile and entry, then `link` per case. Packages move in U4 | §9.1 rule 9.2, U1 |
| t10 | U1's input needs more keys; the C3 path; two profiles | Accepted. A JSON test-image format with those keys. The C3 smoke stays Python until U9. Same check message. fp-free check stays. Per-profile test images | §1.1, 9.1 rule 9.3, U1, U9, Q9, Q10 |
| t11 | a revision cache is stale under edits | Accepted. No cache across runs. If ever, keyed by input contents | §9.1 rule 9.4 |
| t12 | three Blade behaviours; `.sawos` builds verified | Accepted. Always `--force`; `--out-dir` leaves the lock; no lock in the scratch product. Results credited. Identity through one link | §9.1 rule 9.6, 9.2, 11.7, 16.3 |
| version | table is version 4 on main | Accepted. The block makes it version 5. §1 says version 4 and three kinds | §0, 1.1, 1.3, 6.2 |
| §8.1 item 4 | a pool row is its own kind | Accepted | §8.1 |
| pools moved | pool cases would move; `poolbase` can't be generated | Accepted. Test images keep explicit addresses; `poolbase` stays hand-kept | §5.2 rule 5.5, 12.6 |
| `.sawlang` in U7 | U1 needs it; `toolchain.py` is a verbatim copy | Accepted. The runner writes the link in U1. `toolchain.py` unchanged | §9.1 rule 9.7, U1 |
| limit below floor | refuse, don't round | Accepted | rules 6.1, 6.5 |
| bytes after rows | say what may follow | Accepted: nothing or one whole block, else the boot stops | rule 6.2 |
| 400 cases | there are 290 | Accepted | §1.1, 10 |
| `.tool` collision | two `sawc` runs on one output | Accepted, reproduced, filed as new gap 11.12 | §11.12 |

Nothing in the review is disputed.
