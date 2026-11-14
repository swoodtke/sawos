# SO-95: images and packaging (design)

For the owner and the reviewer, before anything is built. This is revision
4, checked against main at `2a13db8`. That main carries:

- SO-92 complete: pools, the widened copy doors, the overlap rule;
- `drivers/` (SO-96);
- SO-101 and SO-88.

It takes in the owner's questions and rulings of Nov 14, and the
reviewer's reply on thread t1. Section 17 answers every review item, old
and new.

Every claim about the code names the file and function it comes from.
Section 16 separates what I ran, what I read, what the reviewer ran, and
what nobody checked.

## The owner's fixed points

1. **Boards stay in sawos.** An image picks a board and describes what is
   attached to it.
2. **Root is replaceable,** so its interfaces are defined.
3. **Do it right first.** Nothing is hand-wired to save days.
4. **Tools are written in Saw wherever possible.** Saw gaps are filed with
   the sawlang lead.
5. **The kernel is compiled once per board, and an image's limits are
   linked in.** The owner's further ruling: the slab storage itself is
   linked in, sized to the limits, rather than donated at boot.
6. **Where a design is constrained by a limit, use the simpler design and
   raise the limit.**
7. **Limits are set per board,** derived from the board's hardware where a
   hardware fact stands behind them: more protection rows where the
   hardware holds more, more processes where there is more RAM.
8. **On the ESP32-S3, process memory may live in the board's 8 MB of
   PSRAM.** The bring-up is SO-102's; this design makes room for a second
   kind of RAM.
9. **The image says where each process and pool lives.** A process with
   tight deadlines is pinned to internal SRAM rather than the slower PSRAM.

---

## 0. Verdict

1. **The seven parts hold.** A board lives in sawos (`board.toml` beside
   its HAL), a product repository holds image descriptions, its packages
   and a `sawos.pin`, and each process states its boot interface in
   `process.toml`, which the builder checks the image against.
2. **Slab storage is linked in.** The builder emits one zero-filled section
   per raised slab kind, named `.bss.sos_slab<kind>`, sized at the limit
   times the slot size. The slot size is read from the kernel object's
   symbol table.
   - Every board's script already takes `.bss.*` into its `.bss`, so no
     script rule is needed. The areas land in the kernel's own SRAM.
   - At boot the kernel installs each area as one more extent, through the
     same `slab_install` a donation ends in.
   - I built this in a scratch kernel. On riscv32 and arm64,
     `process_donate`'s root found a fourth process before any donation.
     On the ESP32-S3 the areas linked into internal DRAM and the case
     passed.
   - Revision 3's reserve, and the kernel's checks on it, are gone.
   - Recommended depth: (a), the area beside the board's compiled floor.
3. **Limits are per board** (section 6, with a table per board). Hardware
   sets these:
   - the PMP boards' protection rows;
   - arm64's ASID width;
   - each board's RAM.

   Everything else is policy, and is raised where it constrained the
   design:
   - the handle table goes from 16 to 32 entries on every board;
   - the default mapping quota becomes what each board's rows allow (4 on
     the PMP boards, 12 on the S3 and arm64);
   - the S3's grant record goes from 8 rows to 16.
4. **The box (thread t1).** Rows name only the devices an image lists,
   which gives twelve. With a 32-entry table, root peaks at 17 of 32, and
   every child stays within its row. The peripheral-row carving of
   revision 3 is withdrawn: it handed root the megabyte at 0x6000_0000,
   eFuse and interrupt matrix included.
5. **The kernel changes in three places:**
   - the linked-area block after the boot table's rows (version 5),
     placed by the linker and checked by the kernel for shape only;
   - per-board constants moved into each HAL, with `sos_board_info`
     exported;
   - `ProcessOp.QuotaSet`, a per-process cap and not a budget.
6. **The builder is a Saw program,** `tools/sosimage`. Its first unit
   leaves the runner's package phase and transcript as they are, and
   compiles each board's kernel once per run. Nothing is cached across
   runs.
7. **A board names its RAM ranges, and an image places each process and
   pool in one.**
   - The ESP32-S3 names `sram` (fast, internal) and `psram` (slow,
     external, and possibly absent).
   - A process's `process.toml` may require fast RAM, and the builder
     holds the image to it.
   - Each range is budgeted separately, and the internal SRAM is the
     scarce one.
   - An image that places anything in PSRAM stops at boot on a part
     without it, by name. One that places nothing there runs on any part.
   - SO-102 comes before U4, and the box's first run needs nothing from
     it.
8. **What stays Python, and why:**
   - the resolvers, which run before any Saw compiler exists;
   - the emulator harness, until `std.process` can run an interactive
     child;
   - the runner's case table and the C3 smoke, for now.
9. **Saw gaps.** Twelve are listed in section 11; none is new in this
   revision.

### 0.1 Where the rulings, the spec and the code disagree

- **The spec calls the hand-kept child linker scripts deliberate** (§5.5,
  §5b.1). There is still one script per resident image, but the builder
  writes it.
- **§2.9 says "there is no per-create quota argument in v1".** Rule 7.2
  proposes the door.
- **§2.8 says donation makes capacity "decided by the deployment rather
  than by the build".** With linked areas, the build decides the boot
  capacity, and donation still grows it.
- **The lead's reading says "a free slot is not all zeros".** The code
  says it is.
  - `kcore.dispatch.slab_donate` zeroes a donated extent and calls it "an
    extent full of free slots by construction", because `Free` is zero in
    `SlotState` and in every per-kind state enum.
  - The inline extent 0 initializers write `NO_MEMORY` and the like into
    free slots, but no scan reads those fields of a free slot.
  - So the boot pass is a zeroing, not a vacating. Section 6.1 says where
    it happens.
- **SO-77's design has root route the servo pads in code.** Under fixed
  point 6 the PWM driver's mapping limit is raised instead (§5.3), and the
  authority question stays with the owner (question 3).
- **riscv32's `virt.ld` has no check that the kernel ends below root's
  region.** arm64's has one. An over-large linked area on riscv32 ran
  silently into root's region in my run (§6.1). Unit U2 adds the check.
- **Stale comment.** The header of `kernel/core/limits.saw` says a derived
  static cannot take operands from another module. With the pinned
  compiler that compiles (§16.1).

---

## 1. Today

### 1.1 How an image comes to be

The test runner, `tools/sos_runner.py`, is the only thing that builds a
system image. It holds 290 cases: 264 in the gate, 20 in the S3 smoke and
6 in the C3 smoke.

- **A case is the image's description.**
  - Its kernel entry (`src`): `kernel/main.saw` for most; some harness
    kernels, and `thread_preempt` and `arena_race`, have their own.
  - Its root: a package (`root_pkg`) or an assembly payload (`asm`).
  - Its children: packages (`children`) or one hand-assembled child
    (`child_asm`).
  - The flags `pool`, `device` and `tick`, plus narrower keys
    (`pool_blobs`, `pool_destinations`, `pool_skew`, the S3's
    `loader_fixture` and `expect_build_error`).
- **Packages are built once per profile, before any case.** Each distinct
  package gets one `blade build --force --target <triple>`
  (`_build_root_image`), and the runner prints its line. Doing each once is
  what keeps two builds out of one directory; nothing locks a build.
- **The kernel is compiled per case.** `_build_elf` runs sawc with the
  module paths for `kcore`, `hal`, `imgformat`, `sosrt` and `sosabi`. It
  links with the board's script, and on arm64 then runs
  `_check_kernel_fp_free`.
- **Root and children are stitched in.**
  - `_stitch_root_image` assembles `kernel/rootimg.S`.
  - `_stitch_regions` writes `<case>.regions.S`: the child images in
    `.childimg`, then the `.regions` section (magic, version 4, row count,
    flags, rows typed `Ram`, `Device` or `Pool`), in `_region_rows`'s
    order.
- **The boards have their own paths.**
  - The S3 smoke (`_esp32s3_smoke`) builds a 4 MiB flash image and runs
    `check_in_place`.
  - The C3 smoke has its own stitchers and an `objcopy` flatten.

### 1.2 Where each fact lives

- **Addresses** (child RAM, pool, device window, root's entry): `ARCHES`
  and `ESP32S3_ARCH`, again in the HAL and in the user scripts.
- **A child's link address:** the hand-kept `child.ld`, `child2.ld` and
  `child3.ld`, again in `ARCHES`.
- **The S3's flash layout:** `esp32s3.ld` (root's slot at 0x10_0000, one
  child slot at 0x18_0000, `.drom` at 0x20_0000).
- **Root's region and the stack length:** the HAL (`ROOT_LOAD_BASE`,
  `ROOT_REGION_TOP`, `ROOT_STACK_LEN`).
- **Limits:** `kernel/core/limits.saw`, one set for every board, and a few
  in the HAL (`GRANT_ROW_BUDGET`, `MAX_ROOT_SEGMENTS`,
  `PROT_DOMAIN_SLOTS`). Section 6.4 lists them.
- **Which row is which, and which tag means what:** each root's source,
  again in each child's drain order.

### 1.3 What the kernel takes from an image

- **The region table** (`kcore.process`, `mint_boot_regions`):
  - version 4 only, with rows typed `Ram`, `Device` or `Pool`;
  - at most `MAX_MEMORIES + MAX_IOMEMORIES` rows;
  - flag bit 0 turns off the tick;
  - bytes after the last row are not read.
- **Each row becomes a handle in root's table before root runs.**
  `start_process` mints System, Process and Thread, and
  `mint_boot_regions` mints one handle per row. So root holds 3 + rows at
  its first instruction, and 4 + rows after `process_self`.
- **A give** moves a handle into the child's table and charges its
  `Handle` row only (`move_entry`). An `IoMemory` row is charged where a
  window is made.
- **Each slab** is a `Slab<T, N>`: an inline extent 0 of `N` slots, up to
  four donated extents, and a run-time `total`. `slab_donate` validates a
  region, zeroes it with `long_zero` and calls `slab_install`.
- **Quotas.** `install_quota_limits` gives a child the `DEFAULT_QUOTA_*`
  numbers, and clamps its `Mapping` row to the grant rows its image left.

### 1.4 What cannot be done from outside the repository

- **Build a kernel or a system image;** both live only in the runner.
- **Link a sawos package where an image chooses** (gap 11.7).
- **Run more than one child in place on the S3.**
- **Change a limit** for one board or one image.

A package outside the tree builds an sosimg against sawos through a link:
I built one for riscv32, and the reviewer built it for arm64 and Xtensa,
byte-identical.

---

## 2. The parts

1. **A board description in sawos** (section 3), with per-board limits
   (section 6) and possibly two kinds of RAM.
2. **An image description in the product** (section 5).
3. **A process's declaration**, `process.toml` (section 4).
4. **One builder**, in Saw (section 9).
5. **A table-driven root** (section 8).
6. **A pin on sawos** (rule 9.8).
7. **The product's own test** (section 10).

---

## 3. The board description

**Rule 3.1.** Each board has one `board.toml` in its HAL directory, for
the build-side facts. Each fact the kernel needs lives in the HAL's
`lib.saw` and is not restated. The kernel exports the HAL's facts in
`sos_board_info`, an integer table the builder reads out of the kernel
object (rule 3.4).

**Rule 3.2.** `board.toml` states:

- the triple, the tier, the HAL and its extra modules, the C flags, and
  how to run the board under its emulator;
- the placement kind: `copied` (riscv32), `in-place` (the S3) or
  `translated` (arm64);
- the board's RAM kinds (rule 3.6), its flash windows and slot range;
- each device by name: base, length, `compatible`, line, its named
  registers and register arrays, and whether the kernel uses it;
- reserved lines;
- **the board's limits** (section 6): the defaults a hand-written image
  gets, and the most an image may ask for.

**Rule 3.3.** An image names devices, registers and register ranges, never
addresses. **A boot row covers exactly one listed device,** never a range
spanning devices the board does not list, and never a device the kernel
uses (thread t1). The builder refuses a register range that is not a
naturally aligned power of two of at least the grain.

**Rule 3.4.** `sos_board_info`, with a leading version word, holds:

- the RAM ranges the HAL owns, root's region, the grain and the stack
  length;
- `GRANT_ROW_BUDGET`, `MAX_GRANT_ROWS`, `MAX_HANDLES` and
  `PROT_DOMAIN_SLOTS`;
- `MAX_SLAB_EXTENTS`, `THREAD_FRAME_STRIDE` and each slab's floor.

Slot sizes are read from the slab statics' symbols (rule 6.3). An exported
integer array built from `kcore.limits`' and the HAL's statics compiles to
a data symbol of the right size (§16.1).

**Rule 3.5.** On-chip peripherals are board facts. What is wired to the
board is an image fact: a driver's config module, and the register ranges
the image names.

**Rule 3.6. Named RAM ranges.** `board.toml` names each RAM range and
states:

- **`use`.** One of:
  - `kernel`: the kernel's data, its slabs and linked areas, its stacks.
    Always internal SRAM, never behind a cache.
  - `process`: regions and pools.
- **`speed`.** `fast`, for internal SRAM, or `slow`, for external RAM
  behind the cache.
- **`present`.** `always`, or `probed`: brought up by the HAL, and
  possibly absent on a given part.

It also says which `process` range is the board's default for processes
and for pools. On the ESP32-S3:

- `kernel` is internal DRAM;
- `sram` is the internal process RAM (`fast`, `always`);
- `psram` is the external 8 MB (`slow`, `probed`, from SO-102).

The S3's default process range is `sram` until SO-102 lands, and `psram`
after. Bulk goes to the large range, and what is pinned goes to the fast
one (rule 5.10).

The S3's file, abridged:

```toml
format = 1

[board]
name = "esp32s3"
triple = "xtensa-esp-unknown-elf"
tier = "flat"
hal = "hal/xtensa-esp32s3"
placement = "in-place"

[ram.kernel]
use = "kernel"
base = 0x3FC8_C000
len = "144K"

[ram.sram]
use = "process"
speed = "fast"
base = 0x3FCB_0000
len = "256K"

[ram.psram]
use = "process"
speed = "slow"
present = "probed"
len = "8M"            # where it maps is SO-102's to settle

[ram.default]
process = "sram"      # "psram" once SO-102 lands
pool = "sram"

[flash]
bytes = "4M"
slots = { offset = 0x18_0000, top = 0x20_0000, default-len = "128K" }

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

[limits.default]      # what an image gets when it says nothing (§6.4)
processes = 8
threads = 16
waiters = 8

[lines]
reserved = [57]
```

UART0 is listed beside these and abridged out. The `kernel` and `process`
ranges restate `esp32s3.ld`'s `DRAM` and `lib.saw`'s `RAM_BASE` and
`RAM_TOP`. For those two, `board.toml` is generated from the HAL's export,
or checked against it, so a fact is stated once. The device numbers are
SO-77's, from ESP-IDF headers.

---

## 4. A process's declaration

**Rule 4.1.** Every process the stock root launches has a `process.toml`.
It is sawos's file, so a new field is never a toolchain change.

**Rule 4.2.** It declares the boot set in the order the launcher gives
it. Each record has a name, a tag and a kind: `system`, `window` (with
`compatible`, the device, and whether a register range will do), `line`,
`serve`, `client` or `memory`.

**Rule 4.3. What it needs from the board.** It declares the rows the
kernel charges for what the program does: `mappings`, `handles`,
`threads`, `waiters`, `timers`, `events`, `pipes`, and `iomemories` only
if it carves.

- The builder computes the `handles` floor itself: one per record, one
  for the Process handle, and one Mapping per window.
- **The builder refuses, by name, an image on a board that cannot give a
  process what it declares.** For example: "svc-pwm-esp32s3 needs 4
  mappings; riscv32-virt allows at most 4 per process, and this image's
  default is 2", or "needs 20 handles; esp32c3 has a 16-entry table".
- This is how per-board limits stay honest about portability. A program
  that needs more than a small board gives does not run there, and the
  builder says so before anything is compiled.

**Rule 4.4.** It declares its arena and the boards it builds for. Its
region is computed (rule 5.6).

It may declare `ram = "fast"`: its author's statement that the program
needs fast memory. A driver that services an interrupt, or anything with a
deadline, says so. The builder refuses an image that places such a process
in a `slow` range, naming the process and the range. The requirement is a
speed and not a range name, so the declaration is portable across boards.

**Rule 4.5.** A driver finds its registers through `Mapping.base()`.

**Rule 4.6.** It declares build inputs (modules the image supplies, such
as a driver's pin table).

**Rule 4.7.** `sos` gains `BootSet`, which drains every record and answers
`take(tag:)`. A missing or extra record is printed with its tag.

The GPIO driver's declaration (abridged):

```toml
format = 1
boards = ["esp32s3"]
arena = "4K"

[[boot]]
name = "system"
kind = "system"
tag = 1
# ... window gpio (2), window iomux (3), line gpio (4), serve gpio (5)

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

**Rule 5.1.** A hand-written image is TOML in the product's `images/`. A
test image the runner writes is JSON, with more keys (rule 9.3).

**Rule 5.2.** A hand-written image states:

- the board, the tick;
- `[limits]`, each within the board's maximum and defaulting to the
  board's default (section 6);
- the root;
- optional `[[setup]]` register writes for the stock root (rule 8.5);
- one `[[process]]` per child, with its windows (devices or register
  ranges), lines, clients, inputs, quota rows, ceilings and `on-exit`;
- for a hand-written root, its rows;
- for each process and each pool, optionally `ram = "<range>"`: a named
  `process` range of the board. The default is the board's default for
  that use (rule 5.10).

**Rule 5.3.** A package is `sawos:<path>` or a product path.

**Rule 5.4.** The builder refuses, before compiling anything, an image in
which:

- a name is unknown, a line reserved, or a range not carvable;
- a device mismatches `compatible`;
- a record is unbound or a tag repeated;
- protocols disagree;
- **any limit or quota exceeds what the board allows, or a process's
  declared needs exceed what the image gives** (by name);
- **any named range is over-asked.** Each range is budgeted on its own,
  and the refusal names the range and the processes in it. Internal SRAM
  is the scarce one: the kernel's region with its linked areas, root's
  region, and every pinned process and pool all come out of it;
- a process declaring `ram = "fast"` is placed in a `slow` range;
- **the linked areas do not fit the kernel's RAM.** The link fails anyway,
  but the builder names the kind first;
- root's table overflows at any step of the plan, or a child's `Handle`
  row is short. The count starts at the four handles root holds before
  any row.

### 5.2 Link addresses and regions

**Rule 5.5.** The builder assigns every address in a hand-written image:

- root's region is the board's;
- children's regions follow it, in the RAM kind rule 5.10 picks;
- pools follow the children;
- each child gets a flash slot on an `in-place` board.

A test image's addresses are given by the runner, so today's cases keep
their layout. `tests/poolbase_<arch>.c` stays hand-kept.

**Rule 5.6.** A region is `round_up(.data + .bss + arena, grain) + stack`.
The builder links each process once to measure it, then at its base. An
image may make a region larger.

**Rule 5.7.** Per-process linker scripts are generated from one template
per board. The hand-kept child scripts go.

**Rule 5.8.** On an `in-place` board the kernel's script includes a
generated `image_slots.ld`, which places each child's image, and `.drom`
behind the last slot from U4 on. Until U4, the slot range ends at
0x20_0000.

**Rule 5.9.** The row order stays §12's: blobs, destinations, then the
image's rows.

**Rule 5.10. Where each process and pool lives.**

- **The image places each process and pool in a named range** (`ram =`).
  The default is the board's default for that use.
- **The process's own declaration constrains the choice.** A process that
  declares `ram = "fast"` may not be placed in a `slow` range.
- **A process is linked for the address it runs at.** On the S3 that is
  its writable data, since its code runs from flash. So its placement is
  fixed in the image, and the builder links it there.
- **What stays in internal SRAM:**
  - root's region, because the kernel loads root before anything could
    probe PSRAM, and the boot path stays off the cache;
  - the kernel's memory, its slabs and the linked areas, always (rule
    3.6).
- **On a part without PSRAM:**
  - An image that places nothing in a `probed` range runs on every part.
    Every image is that until SO-102 lands.
  - An image that places anything there is marked by the builder as
    needing that range; `flash` prints it.
  - At boot, the kernel refuses a boot row that lies in a range the HAL
    reports absent: `fatal_image`, naming the row, before root runs. That
    is a HAL presence check, in SO-102's scope, and the kernel's only new
    check on placement.
- **Recommended: one image per description, refused at boot on a part
  that lacks a range it uses.** The alternative, a second all-SRAM layout
  built from the same description, is available on request
  (`sosimage build --layout sram`), and the builder refuses it if it does
  not fit. It is not built by default, because which processes may fall
  back is the image author's decision, and it is made by pinning them.
- **What a process may assume.** Nothing in the kernel's contract tells a
  process which RAM it is in, and nothing should. A program that cares
  states `ram = "fast"`, and the builder guarantees it. The placement is
  recorded in `imageplan`, so the stock root can print it when it reports
  a failure. No boot record carries it.

### 5.3 Worked example: the useless box

The repository (`images/box.toml`, `images/box-qemu.toml`, `config/`,
`controller/`, `heartbeat/`, `tests/`, `sawos.pin`, `tools/sawos.py`, the
`.sawos` link) is as in revision 3.

`images/box.toml`, as the simplest reading of question 3 has it:

```toml
format = 1

[image]
name = "useless-box"
board = "esp32s3"
tick = true

[root]
package = "stock"

[[process]]
name = "gpio"
package = "sawos:drivers/svc-gpio-esp32s3"
ram = "sram"
windows = { gpio = "GPIO", iomux = "IO_MUX" }
lines = { gpio = "GPIO" }
inputs = { gpioconfig = "config/gpio.saw" }

[[process]]
name = "pwm"
package = "sawos:drivers/svc-pwm-esp32s3"
windows = { ledc = "LEDC", gpio = "GPIO", iomux = "IO_MUX", system = "SYSTEM" }
inputs = { pwmconfig = "config/pwm.saw" }

[[process]]
name = "controller"
package = "controller"
ram = "sram"
clients = { switch = "gpio.gpio", servos = "pwm.pwm" }
on-exit = "shutdown"

[[process]]
name = "heartbeat"
package = "heartbeat"
```

The `windows` line runs past 80 columns because TOML allows no line break
inside an inline table.

No `[limits]` and no quota rows: the S3's board defaults cover the box
(table in §6.4). The PWM driver needs no `QuotaSet`, because the S3's
default mapping row becomes 12.

What the builder makes of it:

- **Rows: twelve.** Four blobs, four destinations, and GPIO, IO_MUX, LEDC
  and SYSTEM, one row per listed device.
- **Kernel.** The S3's one object, as the gate tested it. The board's
  default linked areas raise processes to 8, threads to 16 and Waiters to
  8, with the slot sizes read from that object (§6.4).
- **Placement.**
  - **GPIO driver: `sram`.** It services the switch's interrupt, and its
    declaration says `ram = "fast"`.
  - **Controller: `sram`.** It owns the debounce and the arm's timing, so
    its author pins it.
  - **PWM driver and heartbeat: the board default.** LEDC times the pulses
    in hardware, so a request served from PSRAM costs latency nobody
    sees. The heartbeat is the archetype of bulk.
  - Until SO-102 the default is `sram`, so all four are in SRAM. After
    it, the PWM driver and the heartbeat move to `psram`, and the image
    then needs PSRAM.
- **Bytes per range.** These are estimates; the regions are measured once
  the programs exist. Each region is a 16 KiB stack, plus a 4 to 8 KiB
  arena, plus a few KiB of data, so about 24 KiB, and 32 KiB for the
  controller.
  - **Kernel SRAM (144 KiB):** the kernel as linked today, plus about
    12.5 KiB of linked areas and handle-table growth, inside its measured
    21.9 KiB of slack (§6.3).
  - **Process SRAM (256 KiB), before SO-102:** root 96 + GPIO 24 +
    controller 32 + PWM 24 + heartbeat 24 = 200 KiB, leaving 56 KiB.
  - **Process SRAM after SO-102:** root 96 + GPIO 24 + controller 32 =
    152 KiB, leaving 104 KiB for SRAM pools.
  - **PSRAM after SO-102:** PWM 24 + heartbeat 24 = 48 KiB of 8 MiB.
- **Flash.** Four 128 KiB slots end at 0x20_0000, the kernel's `.drom`,
  which fits before U4.

**Handle counts** (by reading the plan against §1.3's rules; root's table
holds 32):

```
step                                                        root holds
entry: System, Process, Thread, 12 rows                             15
process_self                                                        16
create child 1 (+1), release its blob and destination (-2)      17, 15
children 2, 3 and 4                                 16/14, 15/13, 14/12
after the creates: 4 fixed, 4 device rows, 4 children               12
GPIO: System sibling, GPIO and IO_MUX siblings (the rows are
      kept for PWM), the bound line: each made then given     13, 12 (x4)
GPIO: pipe_create (+2), give the outlet (-1)                     14, 13
PWM: System sibling made and given                               14, 13
PWM: the GPIO, IO_MUX, LEDC and SYSTEM rows, each given          12..9
PWM: pipe_create (+2), give the outlet (-1)                      11, 10
controller: System sibling; each inlet minted, given, the
      original released                       11, 10; 11, 10, 9; 10, 9, 8
heartbeat: System sibling                                         9, 8
supervise: one Waiter                                                9
```

Root peaks at 17 of 32, at the first create. Root's table caps an image at
`4 + 2 x children + rows + 1 <= 32`: with four device rows, eleven
children.

Each child against its `Handle` row:

```
GPIO        5 records, Process, 2 Mappings, Waiter = 9; the outlet into
            the Waiter (-1); up to 3 held requests                   11
PWM         6 records (System, 4 windows, outlet), Process,
            4 Mappings, Waiter = 12; the outlet into the Waiter (-1);
            one request in hand                                      12
controller  System, 2 inlets, Process, Clock, Timer, Waiter, a claim  8
heartbeat   System, Process, Clock, Timer, Waiter                     5
```

The S3's default `Handle` row becomes 28 (§6.4). The PWM driver's 12 is
exactly today's default of 12, which is why the row is raised and not
kept.

**Objects.**

- IoMemory: 4 rows (floor 8).
- Mappings: GPIO's 2 and PWM's 4 make 6 (floor 8).
- Memories: 8 rows (floor 16).
- Processes: 5 (board default 8).
- Waiters: 5 (board default 8).

Under the narrower reading of question 3, root writes the clock gate,
reset and pad enable itself, and gives the PWM driver LEDC and two 8-byte
register ranges. Then PWM holds 10 handles and 3 mappings, and root peaks
at 17 again.

---

## 6. Limits per board, and how they reach the machine

### 6.1 What was built and run: linked slab areas

In one scratch copy of main (`2a13db8`):

**Kernel.** `kcore.dispatch.install_linked_slabs()`, about 80 lines, is
called from `kernel/main.saw` after `hal.intc_init()` and before
`load_root_and_enter`, so it runs before root's record, body or rows
exist. It reads a block after the region table's rows:

- an 8-byte header (magic `SLAB`, count);
- one 24-byte entry per area: the `SlabKind`, a linker-resolved base and
  a length.

For each area it:

1. normalizes the base with `hal.virt_to_phys`, as a blob row's base is;
2. takes the unit and alignment from `slab_geometry`;
3. refuses a misaligned base, a length that is not a whole number of
   units, or a kind with no extent left;
4. zeroes the area with `zero_bytes`;
5. calls `slab_install`.

**Builder stand-in** (the runner, in Python for the probe):

- reads each slot's size from the compiled kernel object's symbol table
  (`llvm-nm -S`, `(size - 14 words) / floor`);
- writes `.section .bss.sos_slab<kind>, "aw", @nobits` with a label and
  `.skip limit x unit`;
- writes the block entries naming each label.

**Results.**

- `process_donate_linked`, which is `process_donate` with Processes +2,
  Waiters +1 and Memories +4.
  - riscv32 (`--arch riscv32`):

    ```
    SOS: linked slab kind=0x0000000b unit=0x00000310 slots=0x00000002
    SOS: linked slab kind=0x00000001 unit=0x00000014 slots=0x00000001
    SOS: linked slab kind=0x00000005 unit=0x00000038 slots=0x00000004
    SOS: root image ok segments=0x00000003 entry=0x8020030c prio=0x01010100
    SOS: boot regions=0x00000006
    SOS: console handover
    SOS procdonate: a fourth process existed BEFORE the donation
    ```

  - arm64 printed the same with units 0x560, 0x28 and 0x70.
  - The units the builder stand-in read from symbols divided the lengths
    exactly, so builder and kernel agree on slot sizes on both word widths.
- **Unchanged cases passed:** `process_donate` (riscv32, arm64),
  `slab_donate` and `root_server_boot` (arm64).
- **ESP32-S3:** `root_linked`, which is `root_server_boot` with Processes
  +2 and Waiters +1, passed the S3 smoke. The areas linked at 0x3FC9_6880
  and 0x3FC9_6EC0, inside internal DRAM, and `_bss_end` rose by 0x6A0.
- **Refusals:**
  - `linked_skew` (a length 8 bytes over a whole number of bodies) stopped
    the boot with
    `SOS: bad root image: a linked area is not a whole number of slots`
    `(0x0000000b)`.
  - `linked_big` (3000 bodies) on arm64 failed the link with
    `kernel .bss overruns ROOT_LOAD_BASE (0x40200000) -- see PROT_DOMAIN_SLOTS`.
  - On the S3, 40 bodies failed with
    `section '.bss' will not fit in region 'DRAM': overflowed by 8992 bytes`.
  - On riscv32, 3000 bodies **linked, booted, and went silent after the
    console handover.** `virt.ld` has no end-of-kernel check, so root's
    image was loaded over the kernel's `.bss`. That is a latent bug in
    the tree (§0.1).

All runs were under the suite lock, which I took and gave back.

**What the kernel does with each area, exactly.**

- **The section's name is `.bss.sos_slab<kind>`.** Every board's script
  has `*(.bss .bss.*)` inside `.bss`: riscv32, arm64 and the S3 were run;
  the C3 I read. So the area lands in the kernel's own RAM, and `boot.S`
  zeroes it with the rest of `.bss`. No script rule is added. A section
  named otherwise would be an orphan; the sawlang lead reports that
  failing the S3 link.
- **The kernel zeroes it again** in `install_linked_slabs`, before
  `slab_install`, so it does not depend on where the link put it. Zero is
  vacant for every kind (`slab_donate`'s own reasoning). It runs once at
  boot, before root exists.
- **A section's length is the limit.** The count is length over unit. A
  remainder is refused at boot, which also catches a builder and a kernel
  that disagree about a slot's size.
- **The block shrinks** to a kind, a base relocation and a length per
  area. The children's quota defaults stay numbers in the block, because
  they are not storage.

### 6.2 Depth (a) or (b)

**(a) The area beside the compiled floor.** This is what was built.

- Extent 0 stays the board's floor, and an image raises above it.
- Cost: one of four extents per raised kind. Slots above the floor go
  through `Slab.locate`, as donated ones already do.
- No compiled number changes, so every `static_assert` stays.

**(b) The area as the slab's only storage.** Reading the code, these
depend on the compiled floor today and would change:

- `Slab<T, N>`'s inline `[T; N]` and its `i < N` fast path in `[]`
  (`kernel/core/slab.saw`). With no inline extent, every access goes
  through `locate` and `extent_addr`. That is one loop step and a multiply
  more on every handle lookup and every thread switch. I did not measure
  it, and I did not try whether `[T; 0]` compiles.
- The fourteen slab statics' initializers (`pslot`, `objects`,
  `waitables`, `threads`, `time`), each `[...; MAX_X]`.
- `threads.saw` `frame_slot`: `if index < MAX_THREADS` picks the inline
  frame arena (`THREAD_FRAMES`, `FRAME_ARENA_WORDS`).
- `objects.saw`: `PIPE_EXCHANGES = MAX_PIPES * PIPE_INFLIGHT`, and the
  root mapping clamp's `MAX_MAPPINGS`.
- `process.saw` `mint_boot_regions`: the
  `MAX_MEMORIES + MAX_IOMEMORIES` row bound.
- `PIPE_INFLIGHT = 2 * MAX_THREADS`. A ring's depth is a dimension of the
  exchange chain, so it needs its own constant.
- `ROOT_PROCESS` and `ROOT_BODY` are 0. They still work, since the area is
  installed before root exists.
- About ten of `limits.saw`'s 25 `static_assert`s relate floors to other
  numbers. Each becomes a builder check with a boot refusal behind it.

**What (b) buys on the S3.** The compiled floors cost 26.5 KiB of the S3
kernel's DRAM .bss (summed from the slab symbols; 13.6 KiB of it is the
four pipe connections' rings). An image could lower them and get that
back.

**Recommended: (a).**

- **(b) would buy little.** Under fixed point 7 the board's defaults sit
  above the floors, so no image wants to lower them. The floors stay the
  numbers the test suite was written against, which is the strongest reason
  to keep them (§6.6).
- **(b) needs** every item above, a per-board `PIPE_INFLIGHT`, and a
  measurement of the access cost on the S3 before it is chosen.

### 6.3 Where the bytes come from, and what bounds them

The linked areas take the kernel's own RAM. This is the measured slack
between each linked kernel's end and the end of the kernel's region:

| board | kernel region | kernel ends at (case) | slack | bound checked by |
|---|---|---|---|---|
| riscv32 | 0x8000_0000 to root at 0x8020_0000 | 0x8004_8EE0 (`process_donate`, 3 child blobs inside) | 1.72 MiB | nothing today (§0.1) |
| arm64 | 0x4000_0000 to root at 0x4020_0000 | 0x4007_3450 (`process_donate`) | 1.55 MiB | `virt.ld`'s ASSERT |
| ESP32-S3 | DRAM 0x3FC8_C000 + 144 KiB (internal SRAM) | 0x3FCA_A840 (`root_server_boot`) | 21.9 KiB | the `DRAM` region (link error) |
| ESP32-C3 | SRAM 0x4037_C000 + 168 KiB | not measured | not measured | the `SRAM` region |

- **The S3's slack is internal SRAM.** Its kernel `DRAM` region is
  internal, and the linked areas never go behind the cache.
- **On riscv32 and arm64 the slack depends on the image,** because the
  root and child blobs sit inside the kernel image there.

To give an image more than the slack, the board's split between kernel
and process memory moves:

- **On the S3:**
  - `esp32s3.ld`'s `DRAM` region and `KERNEL_STACK_SIZE` (64 KiB);
  - `lib.saw`'s `RAM_BASE`, `ROOT_LOAD_BASE` and `ROOT_REGION_TOP`;
  - and the user scripts, until U4 generates them.

  The kernel stack is the cheapest lever: 64 KiB is the other boards'
  number, not a measured need.
- **On riscv32 and arm64:** `ROOT_LOAD_BASE`, with each script's limit.

These are per-board kernel facts. The builder cannot own them, because
the kernel is compiled once per board, but it can check against them.

The box needs on the S3, from symbols read off the S3 kernel object:

- the board defaults of §6.4: 5 bodies at 1296 bytes (with 32 handles),
  8 threads at 556 bytes, and 4 Waiters at 20 bytes, about 11 KiB;
- plus the floors' growth from 32 handles: 3 bodies at 512 bytes, 1.5 KiB.

That is 12.5 KiB of 21.9 KiB. It has not been linked.

### 6.4 The per-board limits

Columns are riscv32-virt (also its flat profile), arm64-virt, ESP32-S3
and ESP32-C3. "HW" marks a value set by hardware and "policy" one set by a
choice. Costs are measured where marked, otherwise read off the structs.

| limit | owner | HW or policy | today (rv32 / a64 / S3 / C3) | proposed | why | cost |
|---|---|---|---|---|---|---|
| `GRANT_ROW_BUDGET` (rows a domain holds) | HAL | HW on rv32, C3 (16 PMP entries as 8 TOR regions); policy on a64, S3 | 8 / 9 / 8 / 8 | 8 / 16 / 16 / 8 | the PMP boards are at their hardware; arm64 maps by tables and the S3 enforces nothing, so their number is the grant record's storage | GrantRow 20 B (32-bit), 40 B (a64) per row per body: S3 +160 B, a64 +280 B per body. Longer row walks in `map`, overlap and buffer checks: 16 rows, not 8 |
| `MAX_GRANT_ROWS` (array) | HAL (moves from `limits.saw`) | follows the budget | 9 everywhere | 9 / 16 / 16 / 9 | storage for the budget plus... (`MAX_ROOT_SEGMENTS + 1`) | as above |
| `MIN_GRANT_ROW_BUDGET` | `limits.saw` | the smallest board's, by construction | 8 | removed | each board's own budget is used | none |
| `DEFAULT_QUOTA_MAPPINGS` | board default, image may set | policy, bounded by budget minus `IMAGE_GRANT_ROWS` (4) | 2 everywhere | 4 / 12 / 12 / 4 | the box's PWM driver needs 4 | a number; `quota_vs_wall`, `child-mapwall` and `child-quota` keep 2 through their test images |
| `MAX_HANDLES` (table and boot set in every body) | HAL | policy | 16 everywhere | 32 everywhere | root holds 4 + rows + 1 at its first create, and rows reach `MAX_MEMORIES + MAX_IOMEMORIES` (24) | measured: +512 B per body (32-bit), +896 B (arm64). Floors (3 bodies): +1.5 KiB, +2.7 KiB. Handle walks (teardown, mint scan) 32 not 16. `pipe_table_full` reads the size per profile |
| `DEFAULT_QUOTA_HANDLES` | board default | policy | 12 | 28 | below the table, so a child meets its own budget | a number; quota cases keep 12 via test images |
| `PROT_DOMAIN_SLOTS` | HAL | HW-bounded on a64 (8-bit ASID, ≤256); policy below that | 256 / 5 / 256 / 256 | 256 / 16 / 256 / 256 | arm64's pool is 24 KiB of page tables per slot, and the slack holds many | a64 +264 KiB `.bss` (+11 slots); a create past it answers `NoResource` as today |
| `MAX_PROCESSES` (floor, extent 0) | `limits.saw` | policy | 3 | 3 (floor kept) | the suite's number; boards raise by default (next row) | none |
| processes (board default, linked) | `board.toml` | RAM | none | 16 / 16 / 8 / 4 | RAM: a body is 1296 B (32-bit) or 2272 B (a64) with 32 handles | S3 5 x 1296 B = 6.3 KiB of 21.9 KiB |
| threads, Waiters, events, timers, pipes, memories (defaults) | `board.toml` | RAM | floors 8/4/8/8/4/16 | S3: 16/8/8/8/4/16; rv32, a64: 32/16/16/16/8/32; C3: floors | each a few units per process; pipes are 3.5 KiB each on 32-bit, so kept at 4 on the S3 | S3 threads +8 x 556 B = 4.4 KiB |
| other quota defaults | board default | policy | 4 threads, 4 events, 2 waiters, 4 timers, 4 memories, 2 iomemories, 2 pipes | unchanged | nothing constrains | none |
| `ROOT_STACK_LEN` (every process's stack) | HAL | policy | 16 KiB everywhere | unchanged | no case or the box needs more | none |
| `KERNEL_STACK_SIZE` | board script | policy | 64 KiB everywhere | 64 KiB, and the S3's lever (§6.3) | not measured | none |
| child region length | runner | policy | 256 KiB; S3 96 KiB | computed (rule 5.6) | the 96 KiB slot shaped nothing real | none |
| S3 flash slot length | `esp32s3.ld` | policy | 512 KiB, one slot | 128 KiB default, more after U4 moves `.drom` | four children fit before U4 | none |

The row "`MAX_GRANT_ROWS`... storage for the budget plus" means: the
grant record must hold the budget, and `MAX_ROOT_SEGMENTS + 1`, whichever
is larger. The asserts below keep that.

**Where the PMP boards stand.** Their budget is hardware: 16 PMP entries
used as 8 TOR regions, so a program needing more than 4 mappings beyond
its image cannot run there. A program declares its need, and the builder
refuses it by name on those boards (rule 4.3). That is the portability
cost of fixed point 7, stated rather than averaged away. The ESP32-P4
(32 PMP and 16 PMA) would set 16.

**Which asserts stay compile-time, per board.**

- `MAX_ROOT_SEGMENTS + 1 <= MAX_GRANT_ROWS`;
- `GRANT_ROW_BUDGET <= MAX_GRANT_ROWS`;
- `MAX_PROCESSES <= PROT_DOMAIN_SLOTS`;
- `MAX_HANDLES <= 255` (the handle word);
- new: `MAX_HANDLES >= 5 + MAX_MEMORIES + MAX_IOMEMORIES`;
- the capacity, stride and `PIPE_INFLIGHT` asserts;
- each board default quota below its floor or default;
- the board's `DEFAULT_QUOTA_MAPPINGS + IMAGE_GRANT_ROWS <= GRANT_ROW_BUDGET`.

**Which become the builder's checks, by name, with the kernel's
`fatal_image` behind them.** These are the same relations for an image's
own numbers:

- a quota default below the boot capacity;
- mappings within the budget;
- attachments at least the waitables' sum;
- processes within `PROT_DOMAIN_SLOTS`;
- totals within `MAX_SLAB_CAPACITY`;
- areas within the kernel's slack;
- root's handle peak within `MAX_HANDLES`.

**Could the handle table be a linked area instead of an array in each
body?** Yes, but not simply. The body would hold a base and a length, the
table length would become an image's choice, and every handle lookup
would check against a run-time length. Tables would come from a slab of
tables, all one size per image. It is depth (b) applied to the body.
Under fixed point 6 the per-board constant of 32 is the simpler design,
and the box fits it twice over. Kept as an alternative (section 13).

### 6.4a Where the design had bent to fit a number

Fixed point 6, applied. Each item says whether the number behind it is
hardware or policy, and what this revision does.

- **Root carving windows from one peripheral row,** to stay under the
  16-entry handle table. Policy. Withdrawn: the table is raised to 32, and
  rows name listed devices.
- **The five-child cap that followed from it.** Policy. Gone: with 32
  entries and four device rows, root holds eleven children.
- **The PWM driver's setup moved into root,** because the default mapping
  row was 2. Policy. Raised to the board's free rows (12 on the S3), so
  the driver can map its four windows. Whether it should is authority,
  not a limit (question 3).
- **`QuotaSet` needed for the box's PWM driver.** Policy. Not needed: the
  board defaults cover it. The door stays for other programs.
- **The S3's grant budget of 8, copied from the PMP boards.** Policy on
  the S3, where nothing enforces rows. The board sets its own, 16.
- **Regions sized to fit a 96 KiB slot,** and 24 KiB defaults. Policy.
  Regions are computed from measured programs.
- **One child slot on the S3, and 128 KiB slots before U4.** Policy
  (`esp32s3.ld`'s layout). U4 moves `.drom` behind the last slot.
- **Raise-only limits.** These are not a bend to fit a number. The floors
  are kept because they are the suite's numbers, and a board's defaults
  sit above them (§6.2).
- **What stays, because hardware sets it:**
  - the PMP boards' 8 regions;
  - arm64's 256 ASIDs;
  - each board's RAM, the S3's 21.9 KiB of kernel slack included.

### 6.5 The ESP32-S3 with and without PSRAM

These are inference only; nothing about PSRAM has been run.

- **Only SRAM, today's board.**
  - Processes have 256 KiB: root's 96 KiB, and 160 KiB for children and
    pools.
  - At a measured `.data`/`.bss` (unknown until the drivers exist), plus
    an arena and a 16 KiB stack each, that is about four to six children.
  - The process default stays 8 (root and up to seven), and the builder
    refuses what does not fit.
- **With 8 MB of PSRAM.**
  - Regions and pools move there. Internal SRAM keeps root and the kernel,
    and the freed 160 KiB can become an SRAM pool for anything that must
    not sit behind the cache.
  - The bound on processes is then the kernel's 21.9 KiB of slack, not
    process RAM: about 12 more bodies at 1296 bytes, fewer once threads
    and Waiters are counted.
  - So the board's process default with PSRAM is 16, by RAM, and moving
    the kernel stack or DRAM split (§6.3) is what raises it further.
- **Region sizes.** Computed either way (rule 5.6). In PSRAM an image may
  simply be generous; in SRAM it is held to measured sizes.
- **Pinning.** Every process pinned to `sram` comes out of the 256 KiB
  beside root. So with PSRAM, internal SRAM bounds the pinned processes
  and the kernel's slack bounds the bodies.
- **A pool.** Up to the PSRAM's size, less regions. Kernel slab areas
  never go there.

### 6.6 What it does to the test suite

32 test packages name a limit constant by name in their source (by grep;
many in comments that explain a wall they reach). The cases that build
them run on three gate profiles and the two smokes with one set of numbers
today. Per-board values change that only where a number moves per board.
The rule:

1. **A test image gets the kernel's floors and today's quota defaults
   unless the case states otherwise.** The runner's test images never
   take a board's defaults. So every case that reaches a slab floor or a
   quota on purpose (`process_donate`'s "floor=3", the thread-, event- and
   mapping-quota cases) reads the same on every board, unchanged.
2. **A case that reaches a per-body array or a HAL number reads it per
   profile.**
   - The text side: the runner's `expectations()` gains placeholders
     (`{handles}`, `{grant_rows}`), as it already has `{entry}`.
   - The program side: a per-profile constant, as `tests/irq-lines` gives
     lines.
   - Known cases: `pipe_table_full` (`FILL_SLOTS = 16`, spelled out),
     `quota_vs_wall`, `child-mapwall`, and arm64's domain-wall comments in
     `process_donate`.
   - The full list is U2's first task, made by running the suite with each
     number moved.
3. **A hand-written image gets its board's defaults.**

### 6.7 Revision 3's reserve, compared

Thread t2's points:

- **Placement.** The reserve was process RAM the builder placed. The
  kernel could only check it against root's region and `map_source_ok`,
  and the builder was trusted not to put it on the kernel. A linked area
  is placed by the linker inside the kernel's own `.bss`. It cannot meet a
  process region on a board whose script bounds the kernel, which is
  arm64, the S3 and the C3 today, and riscv32 after U2 adds its assert.
- **The three checks.** Extents fit the reserve; the reserve is in
  `map_source_ok` RAM; it misses root's region. All three go. What remains
  is the block's shape, whole units, alignment, and extents left.
- **Root's region.** A linked area is in `.bss`, and `.bss` is bounded
  below root's region by the board script, so the question no longer
  arises.

Gained: no trust in the builder for placement; a link error on overflow
that names the region; no process RAM spent; memory the boot shim already
zeroes.

Lost: the limit is the kernel region's slack (21.9 KiB on the S3), not
the 256 KiB of process RAM. To raise past it, the board's split moves,
which is a per-board kernel change. Revision 3's reserve is in section 13.

---

## 7. Per-process quotas: the door

**Rule 7.1.** A launcher sets a `Created` child's quota rows. An sosimg
carries none, and a process cannot set its own.

**Rule 7.2.** Spec text for §2.7, after `RestrictRights`:

> - **Quotas.** `ProcessOp.QuotaSet(kind, limit)` sets the named process's
>   limit for one `QuotaKind`, gated by `ProcessRight.QuotaSet`, which the
>   default set carries. The process must be `Created` (else a `BadState`
>   fault), and the kind known (else a `BadArg` fault).
> - **A limit is a per-process cap, not a budget.** Nothing is spent from
>   the caller's row. The machine's bound is the slab.
> - A limit is a number from 0 to 0xFFFE. The unlimited sentinel is a
>   `BadArg` fault as an argument.
> - For every row but `Mapping`, a limit above the caller's own is a
>   `BadArg` fault, and a `Handle` limit above `MAX_HANDLES` is a `BadArg`
>   fault.
> - A `Mapping` limit is bounded by the child's own free grant rows. Past
>   them it answers `NoResource` and sets nothing.
> - A limit below what the child already uses (only `Handle`, through
>   gives) is a `BadArg` fault.
> - `ProcessOp.QuotaGet(kind)` answers the limit and use, gated by
>   `ProcessRight.Stats`.

§2.9's bullet becomes:

> - A child starts with the image's quota defaults, which the boot table
>   sets over the board's (§12), and its launcher may set any row before
>   it starts (`ProcessOp.QuotaSet`, §2.7). A row is a cap on one process,
>   not a share of its launcher's.

**Rule 7.3.** Under fixed points 6 and 7 the board defaults are raised
(§6.4), so the box sets no row. The door stays for the process that needs
more than its board's default and less than its board allows.

**Rule 7.4.** The `Mapping` row is bounded by the board's budget less the
image's rows: 4 on riscv32 and the C3, 12 on the S3 and arm64.

---

## 8. Root's interfaces

### 8.1 Kernel contract (spec; cited)

1. **Entry:** System in the first argument register (§5.7, §2.8).
2. **Region and image:** the board's root region; unlimited quotas except
   the mapping clamp; unrestricted ceilings (§5.6, §2.9, §2.7).
3. **Boot handles:** words 1 to 3, then one handle per row, each already
   in root's table and announced in row order; `Drained` after the last
   (§12).
4. **Row kinds:** `Ram`, `Device` and `Pool`, all built (§12).
5. **Boot flags and linked areas:** the tick flag; the linked slab areas,
   installed before root runs, with the children's quota defaults (new).
6. **Root's end is the machine's** (§8).
7. **Every other process is root's to create** (§12).

### 8.2 Generated per image (new text for §12)

8. **The boot table.**

   > The image builder writes, in this order, which root's config reads as
   > tags: one image blob per child, one destination per child, then the
   > image's rows in the order its description lists them. After the rows
   > comes either nothing or the image's linked-area block, which the
   > kernel installs before root runs.

9. **`imageplan`.** For each child: ordinals, ceilings, quota rows, the
   records to give (tag, source, keep mask, and for a range the carve
   offset and length), the start tag and the exit policy. Also the
   connections and the setup writes. Its types live in `bootplan`.

### 8.3 Convention among userspace programs (new text for §12)

> **The launch convention** (userspace). A launcher that follows it gives
> a child exactly its declared records, under their tags, in declared
> order, before start. It starts the child with its System record's tag,
> masked with `Debug | ProcessSelf | ClockGet | Transfer` unless the image
> grants more. It sets declared quota rows before any give. It gives
> windows, lines and endpoints as capabilities: a client gets an inlet
> minted with `Post | Transfer`, a server the outlet. It keeps the child's
> Process handle.

And for §2.7's `Give` list:

> - `BootHandleNext` answers records in the order they were queued: root's
>   in row order, a child's in give order. The record `Start` consumed is
>   never answered.

### 8.4 The stock root

**Rule 8.1.** `root/` becomes the stock root. It:

1. derives its Process handle;
2. creates each child and releases its blob and destination;
3. applies the setup writes;
4. creates connections and gives each child its records in declared
   order. It mints a sibling of a row that a later child also needs, and
   gives the row itself to its last taker;
5. starts each child, and supervises;
6. stops the machine on a `shutdown` child, or when all have ended.

**Rule 8.2.** A failed step prints the step, the child and the status, and
stops the machine.

**Rule 8.3.** No device code, no address, no line number.

**Rule 8.4.** Records are given in declared order.

**Rule 8.5.** Each `[[setup]]` entry names a board register and a `set`,
`clear` or `write`. Root carves the word from the device's row, maps it,
writes it, unmaps it, and releases both, before starting anyone. Used only
under the narrower reading of question 3.

---

## 9. The builder, in Saw, and how a product finds sawos

**Rule 9.1.** `tools/sosimage/` is a hosted Saw package built by Blade.
Its subcommands are `kernel`, `link`, `build`, `flash`, `dump` and
`check`. The builder also writes the linked-area sections and their
block, from the kernel object's symbols.

**Rule 9.2. U1 keeps the runner's transcript.**

- The runner keeps its package phase.
- Once per run, for each profile and kernel entry, it calls
  `sosimage kernel`, writing to a unique path.
- Per case, it writes a JSON test image into the case's staging directory
  and calls `sosimage link`.
- Packages move into the builder in U4.

**Rule 9.3.** The test image is JSON, versioned, and wider than the
hand-written format. It carries kernel entries, `asm` and `child_asm`
payloads, explicit addresses, row kinds and the S3 fixtures. The Saw
`check_in_place` repeats today's message byte for byte, and
`_check_kernel_fp_free` stays in the runner. The C3 smoke stays Python
until U9.

**Rule 9.4.** No kernel cache across runs. A key by revision would serve a
stale kernel to every uncommitted edit. If a cache is ever wanted, its key
is the contents of every input.

**Rule 9.5.** The generators are in the builder: the boot-table stub and
its linked-area sections and block, the `.incbin` stubs, the scripts,
`image_slots.ld` and `imageplan`.

**Rule 9.6. Blade, as pinned.**

- `--force` on every build, because build avoidance does not see a script
  change.
- `--out-dir` covers `.build/<triple>/` only, and leaves the lock beside
  its manifest.
- A product copied out of the tree carries no lock.

**Rule 9.7.** The runner writes `<sawos>/.sawlang` before building the
builder. `tools/toolchain.py` stays a verbatim copy.

**Rule 9.8.** A product's `tools/sawos.py` (Python) takes `SAWOS_ROOT` or
fetches the pin, links `.sawos`, resolves every sawos path through that
one link, writes `.sawlang`, builds Blade and `sosimage`, and runs it.

**Rule 9.9.** `build|run|test|flash`. Judging is `sosimage check`.
Booting goes through the Python harness until gap 11.2. `flash` prints
the SHA-256 (through `shasum`), "needs PSRAM" when it does, and the
`esptool` line.

---

## 10. Every tool: Saw or not

- **The builder, the generators, reading the kernel's symbols and board
  info:** Saw. Workarounds are in place for gaps 11.2 (working directory),
  11.3, 11.5 and 11.9.
- **The harness:**
  - judging is Saw;
  - booting is Python until gap 11.2 closes.
- **The resolvers:** Python. They run before any Saw compiler exists, and
  the compiler's own build is Python.
- **`tools/sos_runner.py`:** Python for now, moving in four steps:
  1. U1: the kernel, stitching, linking and the flash image move to Saw;
  2. U8: judging moves;
  3. U8, after S3: booting moves;
  4. the 290 case definitions move only once TOML keeps comments.
- **The C3 smoke:** Python until U9.
- **`sosimg_dump.py`:** becomes `sosimage dump`.
- **`esptool`:** Espressif's tool; the builder only prints the command.

---

## 11. Saw gaps

As filed after revision 2 (SL-1092 to SL-1101) and revision 3 (11.12).
Each entry is self-contained. Unless an entry says otherwise, all were run
against sawlang 0.23.0 on macOS arm64. Nothing is new in this revision.

### 11.1 `libs/toml` misreads TOML outside its subset, silently (SL-1092)

- **Tried (reproduced).** `TomlDoc.parse`.
- **What happened.**
  - A top-level key is dropped.
  - A trailing comment and its quotes are kept.
  - `0x6000_0000` is a string, and `get_int` answers `None`.
  - `[57]` is raw text.
  - `[[x]]` becomes a section named `[x]`.
  - A two-line inline table becomes bogus keys.
  - A comma inside a string splits an inline table.
  - A two-line array fails: `Invalid line: "b"] (line 19)`.
- **Expected.** TOML 1.0 for these constructs, and an error outside the
  subset.
- **Why.** The descriptions are TOML.
- **How badly.** Blocks U3. U1 uses JSON.

### 11.2 `std.process` cannot run an interactive child (SL-1093)

- **Tried (reproduced).** `current_dir`, `write_stdin`, `read_stdout_line`,
  `kill`.
- **What happened.** `member.unknown` for each.
- **Expected.** A working directory, stdin, streamed stdout, kill, and a
  deadline.
- **Why.** Blade runs in a package's directory, and the harness drives
  QEMU.
- **How badly.** The working directory has a workaround (`sh -c`). The
  rest blocks a Saw harness.

### 11.3 No way to tell a directory from a file (SL-1094)

- **Tried (reproduced).** A walk on `Directory.exists` found nothing.
- **What happened.** No `is_dir` or stat.
- **Expected.** `is_dir` or a stat.
- **Why.** Walking trees.
- **How badly.** Workaround: `Directory.list` succeeding. Nice to have.

### 11.4 A struct's size cannot be a constant (SL-1095)

- **Tried (reproduced).** `sizeof<ProcessBody>()` in a static initializer,
  and in a `static_assert`.
- **What happened.** `static.not-constant ...`; and `slice.not-yet ... a
  raw-backed case whose value was not evaluated`.
- **Expected.** `sizeof` and `alignof` of any sized struct as a constant.
- **Why.** The kernel would export its slot sizes, and the builder would
  stop relying on the compiler's symbol spelling and `Slab`'s layout.
- **How badly.** Has a workaround, reading the slab symbols, which this
  revision's prototype used end to end on three boards. Nice to have.

### 11.5 No hexadecimal formatting (SL-1096)

- **Tried (reproduced).** `print("{:x}", 255)`.
- **What happened.** `parse-error 2:13 expected an expression`.
- **Expected.** A public hex rendering.
- **Why.** Addresses.
- **How badly.** Workaround: a short function. Nice to have.

### 11.6 Reading a JSON object needs the document mutable (SL-1097)

- **Tried (reproduced).** `get` on `as_object()`'s map.
- **What happened.** `type.bound ... V: Copy`.
- **Expected.** A shared lookup on non-`Copy` values.
- **Why.** The builder reads JSON test images.
- **How badly.** Workaround: `var`, `borrow let`, `find`. Nice to have.

### 11.7 `blade build` cannot place its output (SL-1098)

- **Tried (reproduced).** `--linker-script`, `--out-dir` and
  `--module-path`; and a script edit followed by a build without
  `--force`.
- **What happened.** Each flag was ignored with `(up to date)`. The edited
  script was not relinked: the first `LOAD` stayed at 0x802c0000 until
  `--force`.
- **Expected.** The three flags; an error on an unknown flag; build
  avoidance that hashes every input; and `--out-dir` that leaves the lock
  beside its manifest.
- **Why.** Each process is linked per image.
- **How badly.** Blocks U4 and U6. Workaround for avoidance: always
  `--force`.

### 11.8 A git dependency cannot name a package in a subdirectory (SL-1099)

- **Tried (reproduced).** `subdir = "libs/greet"` on a tagged repository.
- **What happened.** `cannot read manifest for greet at
  .blade/deps/greet-0.1.0`. Against sawos: `no tagged version of sos
  satisfies 0.1.0`.
- **Expected.** A subdirectory dependency, pinned by tag or commit, and an
  error for an unknown key.
- **Why.** Products depend on packages inside one sawos repository.
- **How badly.** Workaround: path dependencies through `.sawos`.

### 11.9 No portable dependency on a toolchain library (SL-1100)

- **Tried (reproduced).** Absolute and linked paths to `libs/toml`; both
  build.
- **What happened.** One is a machine-specific path, the other needs a
  link someone makes.
- **Expected.** A toolchain-relative spelling.
- **Why.** The builder needs `toml` and `imgformat`.
- **How badly.** Workaround: the `.sawlang` link.

### 11.10 No SHA-256 (SL-1101)

- **Tried (reproduced).** `import std.sha256.{Sha256}`.
- **What happened.** `import.unknown-module`.
- **Expected.** SHA-256 over `Data`.
- **Why.** Board images are sent with their checksum.
- **How badly.** Workaround: `shasum`. Nice to have.

### 11.11 What works

Hosted Blade builds; path dependencies through links; binary file I/O;
paths; `Map`, sorting and `StringBuilder`; `Command` with arguments and
environment; `std.json`; `extern "C"`; reading sosimg and ELF bytes; an
exported integer array built from imported statics; a cold Blade compile
in 0.8 s.

### 11.12 Two `sawc` runs to one output path collide on `<output>.tool`

- **Tried (reproduced).** Two `sawc-stage2` compiles to one `-o k.o`,
  started together, eight times.
- **What happened.** 7 of 16 runs failed, with
  `cannot remove .../k.o.tool ... (not found)` or
  `cannot open .../k.o.tool ... (not found)`.
- **Expected.** A message that names the collision, or a per-process
  scratch name.
- **Why.** sawos compiles in parallel.
- **How badly.** Nice to have. sawos never compiles twice to one path.

---

## 12. An attack on this design

**12.1 Authority.**
- A description can give any child anything its board lists, and only
  that: a boot row covers exactly one listed device (rule 3.3).
- A replaced root is held to the board's list, because it holds only
  rows.
- The stock root's register writes and ranges are data, read from the
  board's register names.

**12.2 Limits raised, and what each bounds.**
- Every raised number is also a bound. A 32-entry table makes each
  teardown's handle walk, and each mint's free-slot scan, twice as long.
  A 16-row grant record doubles the row walks of `map`, the overlap rule
  and the copy doors' buffer checks on the S3 and arm64.
- These loops run with interrupts masked, since the kernel has no
  preemption point inside an op. Each is bounded by a per-board constant,
  as before. Raising one is a decision about that board's interrupt
  latency, and nothing here was measured.

**12.3 Portability.** A program that needs what a small board cannot give
does not run there. The builder says so by name (rule 4.3). That is the
cost of per-board limits, made explicit.

**12.4 The flat tier.** On the S3, windows and quotas are discipline. The
choice in question 3 is about the next protected board.

**12.5 The linked areas.**
- They use the kernel's slack, which is 21.9 KiB on the S3.
- An image past it fails the link, by region name, after the builder has
  already named the kind.
- On riscv32 the failure is silent until U2 adds the missing end-of-kernel
  assert (§6.1). That is a bug today, independent of this design.

**12.6 PSRAM.** Its bring-up is SO-102's and unverified. An image built
for PSRAM does not run on a part without it, and says so when flashed
(rule 5.10). Code and kernel memory never go there.

**12.7 sawos moving under a pin; the suite; two languages; Saw as the
tool language.** As in revision 3: versioned formats; the scratch-product
gate case; build times of about a second; failures caught at the pin bump.

---

## 13. Alternatives rejected

- **Revision 3's reserve in process RAM.** It spends process RAM, it
  needs the builder trusted for placement, and it needs three kernel
  checks. Linked areas need none of that (§6.7).
- **Depth (b), the linked area as a slab's only storage.** It is not
  needed while board defaults sit above the floors (§6.2). It needs
  floor-free slabs, a per-board `PIPE_INFLIGHT` and an access-cost
  measurement first.
- **Root carving windows from one peripheral row** (revision 3's
  question 2 (a)). It handed root the megabyte at 0x6000_0000: UART0, both
  flash controllers, the eFuse controller, the RTC, timer group 0, the
  system timer, the permission registers and the interrupt matrix
  (reviewer, from ESP-IDF's `reg_base.h`). Under fixed point 6 the table
  grows instead.
- **Minting boot handles at drain; not minting the boot Process and
  Thread handles.** Kernel and §12 changes, not needed with 32 entries.
- **The handle table as a linked area per image.** It is depth (b)
  applied to the body; not needed at 32 per board.
- **Compiling the kernel per image.** Every image would boot a kernel the
  gate never ran.
- **One image linked for both SRAM and PSRAM.** Impossible: a process is
  linked where it runs.
- **Building two layouts by default.** Which processes may fall back to
  SRAM is the image author's choice, and pinning expresses it. A second
  layout is built only on request (rule 5.10).
- **A boot record telling a process which RAM it is in.** A program that
  cares states the requirement, and the builder guarantees it, so nothing
  needs to be known at run time.
- **A kernel cache keyed by revision; a per-case builder that builds
  packages; a hand-kept slot-size literal; a builder in Python;
  descriptions in JSON; quotas in the sosimg; the kernel as a Blade
  package; git dependencies; a submodule instead of a pin.** As before.

---

## 14. Cost and order of work

Units marked sawlang are the sawlang lead's.

**U1. Kernel, stitching and linking in Saw, transcript-identical (sawos;
Sonnet, medium).** Rules 9.1 to 9.4 and 9.7. The C3 path is untouched.
Test: every transcript, total and image size identical to the base.

**S1 (sawlang).** Blade's flags, unknown-flag errors and build avoidance
(gap 11.7).

**S2 (sawlang).** `libs/toml` (gap 11.1).

**U2. Per-board limits and linked slab areas (sawos kernel and spec; Opus
for the spec, Sonnet to build; medium-large).**
- `install_linked_slabs`, and the table at version 5.
- The per-board constants moved into each HAL (§6.4): `MAX_GRANT_ROWS`,
  `MAX_HANDLES` at 32, arm64's `PROT_DOMAIN_SLOTS` at 16, the S3's budget
  at 16.
- `sos_board_info`.
- riscv32's end-of-kernel assert.
- The asserts split into compile-time and builder checks.
- The builder's areas, read from symbols, and its refusals.
- The runner's per-profile placeholders for the cases of §6.6; making
  that list is the unit's first task.

Tests:
- `process_donate_linked` on every board;
- a five-process image with no donation;
- a remainder;
- an area past the slack on each board (a link error);
- garbage after the rows;
- root's table at its limit and one past it;
- every case reaching a moved number, green on every profile.

**U3. The description formats (sawos; Opus for the format; medium; after
S2).** `board.toml` with RAM kinds and per-board limits, and the image
TOML.

**SO-102. ESP32-S3 PSRAM bring-up (its own design and unit).** Before U4,
so U4 assigns regions into both kinds of RAM. If it slips, U4 is written
with the named ranges of rule 3.6 and assigns from `sram` alone, and
`psram` slots in when SO-102 lands. **The box's first run
needs nothing from SO-102.** It fits internal SRAM (§6.5), and PSRAM is
headroom.

**U4. Generated addresses (sawos; Sonnet, medium; after S1's pin bump,
U3, and SO-102 or its RAM-range stub).**
- computed regions;
- named ranges and per-range budgets;
- `ram = "fast"` enforcement;
- `--layout sram` on request;
- `.drom` behind the last slot;
- the hand-kept scripts gone.

**U5. The quota door (sawos kernel and spec).** As revision 3, plus the
board-allowed bound.

**U6. Declarations and the stock root (sawos; Opus for `bootplan`; after
U4 and U5).** `process.toml` with per-board refusals, `BootSet`,
`imageplan`, the stock root, setup writes and ranges.

**U7. Products.** **U8. The harness in Saw** (after S3). **U9. The C3
smoke through the builder.**

**SO-77 and SO-78.**
- SO-77's drivers come after U4 and U5, in the shape question 3 decides.
- SO-78 comes after U6, U7 and the drivers.

**The order, at most two agents at once across both repositories:**

1. U1, beside S1.
2. U2, beside S2.
3. U3, beside SO-102.
4. U4, beside U5.
5. U6, beside SO-77's GPIO driver.
6. U7, beside the PWM driver.
7. U8 and U9.
8. SO-78.

---

## 15. Questions for the owner

Fixed points 5 to 8 are no longer questions. New or changed questions are
marked.

1. **(changed) Depth of the linked areas.** (a) beside the board's floor,
   raise-only, or (b) the slab's only storage, so an image can lower too.
   *Recommended: (a).* Board defaults sit above the floors, so lowering
   buys about nothing, and (b) costs a slower access path and about ten
   asserts turned into checks.
2. **(changed) Root's handle table (t1).** Rows name only listed devices,
   so the box has twelve, and the table is raised to 32 on every board.
   - Root peaks at 17 of 32.
   - Each body grows 512 bytes (32-bit) or 896 bytes (arm64).
   - Handle walks double.

   The alternative, revision 3's single peripheral row, hands root the
   megabyte at 0x6000_0000: UART0, both flash controllers, the eFuse
   controller, the RTC, timer group 0, the system timer, the permission
   registers and the interrupt matrix.

   *Recommended: 32, rows per listed device.*
3. **(changed) What the PWM driver may reach.** This is authority, not a
   limit, and fixed point 6 does not decide it. Two readings:
   - **The limit reading:** the mapping row is raised (12 on the S3), so
     the simplest design gives the driver its four windows whole: LEDC,
     GPIO, IO MUX and SYSTEM. It can then reach every pad, the console's
     clock and reset, and the CPU clock select.
   - **The authority reading:** root writes the clock gate, reset and pad
     enable once (rule 8.5), and the driver gets LEDC and its two pads'
     register ranges.

   *Recommended: the authority reading.* It costs root about 40 lines of
   data handling, and SYSTEM is shared by every peripheral. The box works
   either way. On the S3 neither is enforced.
4. **(new) The per-board values of §6.4.** Do you accept them?
   - 32 handles everywhere;
   - rows 16 on the S3 and arm64, 8 on the PMP boards;
   - mapping defaults 12 and 4;
   - arm64 domains 16;
   - S3 defaults of 8 processes, 16 threads and 8 Waiters.

   *Recommended: yes.* Each is a starting point for its board, and a
   board or image changes it in data.
5. **(new) Placement.**
   - `ram =` on each process and pool, defaulting to the board's default
     range (`sram` until SO-102, `psram` after).
   - A process may require `ram = "fast"` in its declaration.
   - One image per description; on a part without PSRAM it stops at boot
     by name, and an all-SRAM layout is built only on request.
   - A process is not told where it runs.

   *Recommended: yes.*
6. **(new) Test images keep the floors and today's quotas.** Cases that
   reach a per-board array read it per profile. *Recommended: yes.*
7. The kernel's reserve: superseded by linked areas.
8. Driver pins as a build-time module. *Recommended: yes.*
9. TOML with a completed `libs/toml`, and JSON for test images.
   *Recommended: yes.*
10. Sawlang S1 and S2 ahead of the box. *Recommended: yes.*
11. The runner moves stepwise. *Recommended: yes.*
12. The C3 smoke stays Python until U9. *Recommended: yes.*
13. A hand-written image names one board. *Recommended: yes.*
14. The resolvers stay Python; a pin and `.sawos` for products; the stock
    root's exit policy; the box in its own repository. *Recommended: yes,
    each.*

---

## 16. Verified, read, the reviewer's, and not checked

### 16.1 Verified by me, by running (scratch only)

**Linked slab areas**, on a scratch copy of main `2a13db8`, under the
suite lock:
- riscv32 and arm64: `process_donate_linked` showed the fourth process
  before donation; `process_donate`, `slab_donate` and `root_server_boot`
  passed;
- ESP32-S3: `root_linked` passed, with its areas in internal DRAM;
- refusals: a remainder refused at boot; arm64 and S3 overflows failed the
  link; a riscv32 overflow linked and hung after the console handover.

**Slab units from symbols**, matching the kernel's `slab_geometry` at
boot:
- riscv32 and S3: body 784, Waiter 20, Memory 56, thread 44 plus a
  512-byte frame, pipe 24 plus 16 exchanges of 216;
- arm64: body 1376 (1376 from symbols; the boot line printed 0x560 for
  the linked case's unit, the same number).

**A 32-entry handle table** (a scratch compile): body 1296 bytes on
riscv32 (+512) and 2272 on arm64 (+896).

**Slack:** riscv32 1.72 MiB, arm64 1.55 MiB, S3 21.9 KiB, read from linked
ELFs' `_bss_end`.

**The S3 floors:** 26.5 KiB of slab statics in DRAM.

**From earlier revisions:** the Saw probes behind section 11; the `.tool`
collision; Blade's avoidance; the kconfig-style module; the exported
integer table; a package outside the tree through a link (riscv32).

### 16.2 Read, not run

- The code behind §1 and §6.2, on main.
- That zero is vacant for every kind (`slab_donate`).
- The C3 script's `.bss` rule.
- The grant-row struct sizes.
- arm64's page-table set size (24 KiB per slot).
- The PMP entry counts, from the HAL's comments.
- The box's handle counts.
- The count of 32 test packages naming a limit (grep).

### 16.3 The reviewer's runs

- Root's table at 10, 11 and 12 rows.
- Concurrent Blade builds and kernel compiles colliding.
- Blade's lock behaviour.
- `.sawos` builds on riscv32, arm64 and Xtensa, byte-identical.
- Espressif's `ld.lld` `INCLUDE`.
- Identity through one link.
- The case counts.
- What the megabyte at 0x6000_0000 holds (from ESP-IDF's `reg_base.h`).

### 16.4 Not checked

- The C3's slack.
- The access cost of depth (b); whether `[T; 0]` compiles.
- The interrupt latency added by longer handle and row walks.
- The box's actual region sizes, and its S3 defaults linked together.
- **Everything about PSRAM:** where it maps, whether the emulator wires
  it, the bring-up.
- Which cases break when each per-board number moves; that is U2's first
  task.
- The pad's state between setup and the driver's `Setup`.
- The QEMU socket route; hosted Saw on Linux.

---

## 17. Response to the review

| item | finding | response | where |
|---|---|---|---|
| t1 | 12 rows fill a 16-entry table | Revision 3: peripheral row and carving. Revision 4: withdrawn after the reviewer showed the megabyte's contents. Rows per listed device, table 32 on every board, root at 17 of 32 | §0, 3, 5.3, 6.4, 13, Q2 |
| t2 | the reserve's checks didn't keep it off root or the kernel | Revision 3: builder placement plus kernel checks. Revision 4: the reserve is gone; linked areas sit in the kernel's `.bss`, bounded by the board script | §6.7 |
| t3 | cap not budget; `Mapping`; sentinel | Accepted (revision 3) | §7 |
| t4 | a given window spends no `IoMemory` | Accepted; declarations state charged rows | rule 4.3 |
| t5 | slots ran over `.drom` | Accepted; `top` 0x20_0000 until U4 | §3, rule 5.8 |
| t6 | 24 KiB regions left no data | Accepted; regions computed | rule 5.6 |
| t7 | slot sizes from symbols | Accepted; now used end to end by the prototype | §6.1, 11.4 |
| t8 | the PWM driver's four windows | Kept as an owner question, now with the limit and authority readings separated | Q3 |
| t9 | per-case builder races | Accepted; package phase kept | rule 9.2 |
| t10 | U1's wider input; C3; profiles | Accepted | rule 9.3, U9 |
| t11 | revision-keyed cache | Accepted; no cache | rule 9.4 |
| t12 | Blade behaviours | Accepted | rule 9.6 |
| smaller | versions, pools, `.sawlang`, floors, trailing bytes, counts, `.tool` | Accepted (revision 3); the block is now linked areas | §6.1, 9 |
| owner, Nov 14 | linked RW slab areas instead of donation | Prototyped on riscv32, arm64 and the S3; recommended at depth (a) | §6 |
| owner rule | simpler design, raise the limit | Applied: table 32, mapping defaults, S3 rows 16, board defaults; carving withdrawn | §0, 6.4, 5.3 |
| owner ruling | limits per board, from hardware | Table per board; hardware vs policy marked; asserts split | §6.4 |
| owner, PSRAM | process memory in PSRAM | Named RAM ranges; SO-102 before U4; the box's first run needs none of it | rules 3.6, 5.10, §6.5, 14 |
| owner, placement | each process and pool placed by the image; critical ones pinned to SRAM | `ram =` per process and pool; `ram = "fast"` in `process.toml`; per-range budgets; one image, refused at boot without PSRAM | rules 3.6, 4.4, 5.2, 5.4, 5.10, §5.3, Q5 |
| lead's reading | "a free slot is not all zeros" | Disputed with the code: `slab_donate` treats zero as vacant for every kind; the prototype relied on it | §0.1, 6.1 |
| lead's reading | an orphan section fails the S3 link | Avoided: `.bss.sos_slab*` matches every board's `.bss` rule; S3 run passed | §6.1 |

One item is disputed: the lead's premise that a free slot is not all
zeros. Everything else is accepted.
