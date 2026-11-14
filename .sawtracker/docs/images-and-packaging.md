# SO-95: images and packaging (design)

This is revision 6, the design to build from. The owner ruled on Nov 14:
"agree with all recommendations for so-95" (§15). Every choice that
earlier revisions put as a question is now written into the rule it
belongs to.

This revision also takes in the reviewer's threads t18 to t21 on
revision 5, and a read-through as the implementer of the formats (U3)
would make it. It was checked against main at `096dd17`.

What it takes from SO-102's design of the ESP32-S3's PSRAM
(`.claude/lead/so102-design.md`) is marked "(depends on SO-102)".

Section 17 answers every review item, old and new. Every claim about the
code names the file and function it comes from. Section 16 separates what
I ran, what I read, what the reviewer ran, and what nobody checked.

## The owner's fixed points

1. **Boards stay in sawos.** An image picks a board and describes what is
   attached to it.
2. **Root is replaceable,** so its interfaces are defined.
3. **Do it right first.** Nothing is hand-wired to save days.
4. **Tools are written in Saw wherever possible.** Saw gaps are filed with
   the sawlang lead.
5. **The kernel is compiled once per board, and an image's slab storage
   is linked in,** sized to its limits, rather than donated at boot.
6. **Where a design is constrained by a limit, use the simpler design and
   raise the limit.**
7. **Limits are set per board,** derived from the board's hardware where a
   hardware fact stands behind them.
8. **On the ESP32-S3, process memory may live in PSRAM.** The bring-up is
   SO-102's.
9. **The image says where each process and pool lives.** A process with
   tight deadlines may be pinned to internal SRAM.
10. **No fallback.** A part matches its board description exactly, or the
    boot stops on the console, naming what differed.
11. **One name for one kind of thing,** in every format (§3.0).
12. **Devices by type, one process per instance.**
    - A driver names the type of device it drives.
    - A board's devices each have a type.
    - An image launches one process per device instance, with that
      instance's settings.
    - One set of devices and inputs per process for now.
13. **Per-process limits are deferred to SO-105:** a handle table per
    process, a pool as a RAM limit, ceilings narrowed to a declaration, and
    a per-process stack.

---

## 0. What is to be built

1. **Three formats, with one set of rules** (§3.0):
   - `board.toml` in sawos, beside each HAL;
   - `process.toml` beside each package the stock root launches;
   - an image description in the product's repository.

   The grammar of a device reference, the uniqueness of every name and the
   reserved words are stated once, for the parser (t19, t20).
2. **Devices bound by type.**
   - A board lists its devices, each with a `type`, and may fix a type's
     board inputs per instance (t21).
   - A driver names the types it needs, and no board.
   - An image launches one process per instance, and names a device only
     where there is a choice, a part, or the console.
   - sawos's gate checks every in-tree declaration against every board
     that has its types.
3. **Slab storage linked in, beside the board's compiled floor,
   raise-only.**
   - One zero-filled `.bss.sos_slab` area per raised kind, sized from the
     kernel object's symbol table.
   - Installed at boot as one more extent, after a check against the board
     script's `.bss` bounds (prototyped on riscv32, arm64 and the S3).
4. **Per-board limits, consistent by construction** (§6.4).
   - Each number is set or derived.
   - The handle table is 32 on every board.
   - The grant rows are what each board's hardware allows: 8 on the PMP
     boards, 16 on the S3 and arm64.
5. **No fallback** (rule 3.6). A part matches its board, or the boot
   stops. `esp32s3-r8` is a board of its own (depends on SO-102).
6. **The box, first run, on the plain `esp32s3`:**
   - all in SRAM;
   - root peaks at 17 of 32 handles;
   - 12.0 KiB of linked areas in 21.9 KiB of kernel slack;
   - the PWM driver holds LEDC and two routing ranges, and root writes
     the clock gate, reset and pad enable;
   - it needs nothing from SO-102.
7. **Kernel changes:**
   - the linked-area block (table version 5) with its bounds check;
   - per-board constants in each HAL, exported in `sos_board_info`;
   - the row bound and root's mapping allowance read from capacities;
   - riscv32's end-of-kernel assert;
   - `ProcessOp.QuotaSet` (rule 7.2).
8. **The builder in Saw;** the resolvers and, for now, the emulator
   harness and the runner's case table in Python (§10).
9. **Deferred to SO-105:** per-process limits (rule 7.5).

### 0.1 Where the rulings, the spec and the code disagree

- **The spec calls the hand-kept child linker scripts deliberate** (§5.5,
  §5b.1). There is still one script per resident image, but the builder
  writes it.
- **§2.9 says "there is no per-create quota argument in v1".** Rule 7.2
  proposes the door.
- **§2.8 says capacity is "decided by the deployment rather than by the
  build".** With linked areas the build decides the boot capacity, and
  donation still grows it.
- **`limits.saw` relates image-raised numbers to the compiled floors at
  compile time.** That covers `DEFAULT_QUOTA_MAPPINGS < MAX_MAPPINGS`,
  `MAX_ATTACHMENTS` as a sum of floors, and `PIPE_INFLIGHT = 2 *
  MAX_THREADS`.
  - Once a board's defaults sit above its floors, each such relation is
    either restated against the boot capacities (a builder check, with the
    kernel's refusal behind it) or kept against the floor with its
    consequence stated (§6.4).
  - Revision 4 left three of them broken (t13, t15).
- **`install_quota_limits` caps root's mapping row at the `MAX_MAPPINGS`
  floor, and `mint_boot_regions` bounds rows by the sum of two floors.**
  Both are read from the capacities after this design (t13, t14).
- **riscv32's `virt.ld` has no end-of-kernel check.** arm64's has one. An
  over-large area ran into root's region silently in my run, and U2 adds
  the check.
- **The lead's reading of Nov 14 said a free slot is not all zeros.** The
  code says it is (`slab_donate`), and the prototype relied on that.
- **Stale comment.** The header of `limits.saw` says a derived static
  cannot take operands from another module; with the pinned compiler that
  compiles.

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

1. **A board description in sawos** (section 3), with devices by type,
   named RAM ranges and per-board limits (section 6).
2. **An image description in the product** (section 5).
3. **A process's declaration**, `process.toml` (section 4).
4. **One builder**, in Saw (section 9).
5. **A table-driven root** (section 8).
6. **A pin on sawos** (rule 9.8).
7. **The product's own test** (section 10).

---

## 3. The formats, and the board description

### 3.0 Rules every format follows

These hold in `board.toml`, `process.toml`, the image description, the
runner's JSON test image, and the generated `imageplan`. The loader
refuses, by name, a key a format does not define.

**Naming (owner's ruling, Nov 14).**

1. **A range is `base` and `len`.** This holds for a RAM range, the
   flash, a flash slot range, a device window, a region, a pool and a
   linked area. `top`, `bytes` and `size` are not used.
2. **`offset` is a position measured from a base the entry names:** a
   register within its device, an array within its device. It is never
   another word for `base`.
3. **Every size and address is a TOML integer, in bytes.** Hexadecimal is
   the usual spelling (`0x2_4000` for 144 KiB), and `_` may group digits.
   Suffixed strings (`"144K"`) are refused. A comment may give the size
   in words.
4. **One word for one idea in every file:**
   - `type`: a device's type;
   - `device`: which device a process gets;
   - `inputs`: build-time modules;
   - `ram`: a named RAM range in an image or board, and a speed (`fast`)
     in a declaration, the one exception, since both say where a process
     may live;
   - `quota`: per-process rows;
   - `limits`: slab totals;
   - `count`: how many;
   - `name`: an entry's name.

**Names.** A name matches `[A-Za-z][A-Za-z0-9_-]*`. It may not contain
`.`, which separates the parts of a reference. Names are case-sensitive.
By convention, devices, registers and arrays are upper-case, and
everything else lower-case.

**Reserved words.** These are refused as names, by name:

- `default`, `fast` and `slow`, as RAM range names, because `[ram.default]`
  is the defaults table and `fast` and `slow` are speeds;
- `root`, as a process name, because the stock root reports itself under
  it.

**Device references.** Used by an image's `device` values, `[[setup]]`'s
`register`, and `[rows] devices`:

```
ref    = DEVICE
       | DEVICE "." REGISTER
       | DEVICE "." ARRAY "[" INDEX "]"
       | DEVICE "." ARRAY "[" INDEX "..=" INDEX "]"
INDEX  = decimal integer
```

- `DEVICE` names a `[devices.<name>]` of the board: its whole window and
  its line.
- `DEVICE.REGISTER` names one 4-byte register. It is allowed only in
  `[[setup]]`.
- `DEVICE.ARRAY[i]` is one 4-byte element. `DEVICE.ARRAY[i..=j]` is
  elements `i` to `j` inclusive, so `[41..=42]` is two registers.
  - Both forms are allowed for a window record marked `part = true`.
  - The single element is also allowed in `[[setup]]`.
  - `..` without `=` is refused, with the message "write `..=`, an
    inclusive range".
  - `i <= j < count` is required.
- A range's bytes run from the array's `offset + i x stride`, for
  `(j - i) x stride + 4` bytes. A window must be a naturally aligned power
  of two of at least the board's grain, or it is refused.
- The builder prints, beside each part it grants, its first and last
  register and its bytes.

**Other references.**

- `PROCESS.RECORD` names a `serve` record of another process, in
  `clients` values.
- A pool's name, in `pools` values and `[rows] pools`.
- A RAM range's name, in `ram` values.

**Paths.** A path in `board.toml` is relative to its HAL directory, one
in `process.toml` to its package directory, and one in an image to the
product repository's root. `sawos:<path>` in an image is relative to the
pinned sawos tree.

**Uniqueness.** Each list below names what must be unique, and in what.

- **`board.toml`:**
  - RAM range names;
  - exactly one range with `use = "kernel"`;
  - device names;
  - within one device, its register and array names together;
  - a type's board input names (rule 4.6), across all of the board's
    types.
- **`process.toml`:**
  - tags;
  - exactly one `system` record;
  - names within each of three families: device needs (`window`,
    `line`), endpoints (`serve`, `client`), and `memory`. One name may
    appear in two families (the GPIO driver's `gpio` is a device need and
    a serve).
  - **one device need per type:** every `window` and `line` record of
    one type carries the same name, and is one instance with one binding.
    Two names for one type are refused, because two devices of one type
    is the list form deferred by rule 4.8;
  - input names, and `defaults` keys only among them.
- **Image:**
  - process names;
  - pool names;
  - `device` keys name device needs of that process;
  - `clients` keys name its client records, and `pools` keys its memory
    records;
  - each pool is bound once, to a process or to `[rows] pools`;
  - a device may be bound to several processes. The stock root mints a
    sibling for each, and the builder prints every shared binding.

### 3.1 The board description

**Rule 3.1.** Each board has one complete `board.toml` in its HAL
directory. There is no inheritance between board files. A board that is
a variant of another (the flat profile; `esp32s3-r8`, depending on
SO-102) has a facade HAL directory and a complete `board.toml` of its
own.

- Each fact the kernel needs lives in the HAL's `lib.saw` and is exported
  in `sos_board_info` (rule 3.4).
- `board.toml` restates none of them. The builder checks it against the
  export, so a restatement that drifted is refused.

**Rule 3.2.** `board.toml`'s tables:

- **`[board]`:** `name`, `triple`, `tier` (`isolated` or `flat`), `hal`,
  `placement` (`copied`, `in-place` or `translated`), `modules` (the extra
  kernel module paths), and `cflags`;
- **`[emulator]`:** `program` and `args`;
- **`[ram.<name>]`** and **`[ram.default]`** (rule 3.6);
- **`[flash]`** (in-place boards only):
  - `base` and `len`: the part's flash;
  - `ibus` and `dbus`: the bus windows, each `base` and `len`;
  - `root-slot`, as `base` and `len`;
  - `slots`, as `base`, `len` and `count`;
- **`[types.<type>]`:** `inputs`, the board inputs each instance of the
  type may fix (rule 4.6);
- **`[devices.<name>]`:**
  - `type`, `base`, `len`;
  - optional `line`;
  - optional `kernel = true`;
  - optional `registers` (name to `{ offset }`) and `arrays` (name to
    `{ offset, stride, count }`);
  - optional `inputs` (rule 4.6);
- **`[limits.default]`:** the defaults a hand-written image gets, with the
  same keys as an image's `[limits]` (§6.4);
- **`[lines]`:** `reserved`.

**Rule 3.3. Devices by type** (owner's ruling, Nov 14).

- A device's `type` names its register interface. Two UARTs are two
  devices of one type.
- A boot row covers exactly one device the board lists (t1).
- A device with `kernel = true` (the console) is bound only when an image
  names it. That is the console handover of spec §9a.
- Registers and array elements are 4 bytes.

**Rule 3.4.** `sos_board_info` holds, behind a version word:

- the RAM ranges the HAL owns, root's region, the grain and the stack
  length;
- `GRANT_ROW_BUDGET`, `MAX_GRANT_ROWS`, `MAX_HANDLES` and
  `PROT_DOMAIN_SLOTS`;
- `MAX_SLAB_EXTENTS`, `THREAD_FRAME_STRIDE`, `PIPE_INFLIGHT`,
  `PROCESS_RECORDS_PER_BODY`, and each slab's floor.

Slot sizes are read from the slab statics' symbols (rule 6.3).

**Rule 3.5.** On-chip peripherals are board facts. What is wired to them
is an image fact (rule 4.6), unless the board fixes it.

**Rule 3.6. Named RAM ranges.**

- Each `[ram.<name>]` has `use` (`kernel` or `process`), `speed` (`fast`
  or `slow`), `base` and `len`.
- `[ram.default]` has `process` and `pool`, each naming a `use = "process"`
  range.
- Kernel memory is internal SRAM, never behind a cache.
- **A range the description names is present** (owner's ruling, Nov 14).
  The board's kernel proves its ranges at boot, or stops and names what
  differed (SO-102 §5.2 for PSRAM). A part with less is a different board.

**The plain ESP32-S3 board,** as the box's first run uses it:

```toml
# hal/xtensa-esp32s3/board.toml
format = 1

[board]
name = "esp32s3"
triple = "xtensa-esp-unknown-elf"
tier = "flat"
hal = "hal/xtensa-esp32s3"
placement = "in-place"
modules = []
cflags = ["-mcpu=esp32s3", "-fintegrated-as"]     # abridged

[emulator]
program = "qemu-system-xtensa"
args = ["-machine", "esp32s3", "-nographic", "-semihosting"]

[ram.kernel]
use = "kernel"
speed = "fast"
base = 0x3FC8_C000
len = 0x2_4000              # 144 KiB

[ram.sram]
use = "process"
speed = "fast"
base = 0x3FCB_0000
len = 0x4_0000              # 256 KiB

[ram.default]
process = "sram"
pool = "sram"

[flash]
base = 0x0
len = 0x80_0000             # the part's flash: 8 MiB on the Freenove N8R8
ibus = { base = 0x4200_0000, len = 0x100_0000 }   # 16 MiB (SO-102)
dbus = { base = 0x3C00_0000, len = 0x100_0000 }
root-slot = { base = 0x10_0000, len = 0x8_0000 }
slots = { base = 0x18_0000, len = 0x8_0000, count = 4 }   # 128 KiB each

[types.esp32s3-uart]
inputs = ["uartpins"]       # each instance's pins, which a board may fix

[devices.UART0]
type = "esp32s3-uart"
base = 0x6000_0000
len = 0x1000
line = 27
kernel = true               # the console
inputs = { uartpins = "config/uart0-pins.saw" }

[devices.UART1]
type = "esp32s3-uart"
base = 0x6001_0000          # placeholder: not checked against the TRM
len = 0x1000
line = 28                   # placeholder

[devices.UART2]
type = "esp32s3-uart"
base = 0x6002_E000          # placeholder
len = 0x1000
line = 29                   # placeholder

[devices.GPIO]
type = "esp32s3-gpio"
base = 0x6000_4000
len = 0x1000
line = 16
registers.ENABLE1_W1TS = { offset = 0x030 }
arrays.out_sel = { offset = 0x554, stride = 4, count = 54 }

[devices.IO_MUX]
type = "esp32s3-io-mux"
base = 0x6000_9000
len = 0x1000
arrays.pad = { offset = 0x004, stride = 4, count = 49 }

[devices.LEDC]
type = "esp32s3-ledc"
base = 0x6001_9000
len = 0x1000
line = 35

[devices.SYSTEM]
type = "esp32s3-system"
base = 0x600C_0000
len = 0x1000
registers.PERIP_CLK_EN0 = { offset = 0x018 }
registers.PERIP_RST_EN0 = { offset = 0x020 }

[limits.default]            # set numbers only; derived ones are computed
processes = 8
waiters = 8
quota = { handles = 28, mappings = 12 }

[lines]
reserved = [57]
```

Notes on the file:

- **`[flash]` is the part's flash.** The image's extent is computed and
  printed by `flash`.
- **The slot range ends at the kernel's `.drom`** (0x20_0000) until U4
  moves `.drom` behind the last slot.
- **UART1, UART2, `uartpins` and its file are placeholders,** shown for
  the shape.
- **Registers and arrays use TOML's dotted keys,** the same as inline
  tables, so each line fits 80 columns.

**The board with PSRAM** (depends on SO-102 §4.1 and §5.4) is
`hal/xtensa-esp32s3-r8/board.toml`. It is complete, as every board file
is. It differs from the plain board's only in:

```toml
[board]
name = "esp32s3-r8"
hal = "hal/xtensa-esp32s3-r8"

[ram.psram]
use = "process"
speed = "slow"
base = 0x3D00_0000
len = 0x80_0000             # 8 MiB

[ram.default]
process = "psram"
pool = "psram"
```

- Its kernel brings the PSRAM up before the loader runs, or stops naming
  the step (SO-102 §5.2).
- Root's region stays in `sram`.

---

## 4. A process's declaration

**Rule 4.1.** Every process the stock root launches has a `process.toml`
beside its `Saw.toml`. It is sawos's file, so a new field is never a
toolchain change.

**Rule 4.2. Its keys.**

- **Top level:**
  - `format`;
  - `arena`: a size;
  - optional `ram = "fast"` (rule 4.3).
- **`[[boot]]`,** in the order the launcher gives them. Each has `name`,
  `kind` and `tag` (an integer, unique in the file), and by kind:
  - `system`: nothing more. Exactly one, and its tag is the start tag;
  - `window`: `type`, and optional `part = true` (a register range of the
    device will do, and the image must then name it);
  - `line`: `type`;
  - `serve` and `client`: `protocol`, the protocol package's name;
  - `memory`: nothing more. The image binds it to a pool.
- **`[quota]`:** `threads`, `events`, `waiters`, `timers`, `memories`,
  `iomemories`, `mappings`, `pipes` and `handles`, the same keys as an
  image's `quota` (rule 4.3).
- **`[build]`:** `inputs` (a list of names) and optional `defaults` (name
  to path).

There is no `boards` list and no `compatible` (owner's ruling, Nov 14).
The boards a driver runs on are those with a device of each type it
names, and limits that hold what it declares.

**Rule 4.3. What it needs.**

- `[quota]` states the rows the kernel charges for what the program does.
  `iomemories` is needed only by a program that carves (t4).
- The builder computes the `handles` floor itself: one per record, one for
  the Process handle, and one Mapping per window. A declared `handles`
  below that is refused.
- `ram = "fast"` says the program needs fast memory. A driver that
  services an interrupt, or anything with a deadline, says so.
- The builder refuses, by name, an image on a board that cannot give a
  process what it declares: a missing type, a quota above the board's
  maximum, or a `slow` placement for a `fast` process.

**Rule 4.4.** A process's region is computed (rule 5.6).

**Rule 4.5.** A driver finds its registers through `Mapping.base()`.

**Rule 4.6. Inputs** (owner's ruling, Nov 14, and t21).

- **Two kinds of input.**
  - A **board input** is one that `[types.<type>] inputs` names for a
    type the process needs: wiring a board may fix for each instance.
  - Every other input is the driver's own: settings.
- **Every driver with a device need of type T** lists each of T's board
  inputs in its `[build] inputs`, under the board's name for it.
  `check-tree` and the image build refuse a driver that does not (rule
  4.9). So the board's value follows the device and not a driver's choice
  of name.
- **The value comes from the first of:**
  1. the image's `inputs` on the process entry;
  2. for a board input, the `inputs` of the device instance the process is
     bound to;
  3. the driver's `[build] defaults`, for its own inputs only. A driver
     default for a board input is refused: it would be a guess at wiring.
- **Neither of the three:** the builder refuses by name.
- **What the builder prints:** the source of every input, and "overrides
  the board's" when an image replaces a board value. The image comes first
  by the owner's ruling. An override is for a product that rewired the
  board's fixed hardware, and the printed line makes it visible.

**Rule 4.7.** `sos` gains `BootSet`, which drains every record and
answers `take(tag:)`.

**Rule 4.8. One set of devices and inputs per process, for now** (owner's
ruling, Nov 14).

- Each device need is met by exactly one device, and each input by
  exactly one value. Two UARTs served means two processes.
- **The list forms are refused, by name, as reserved:**
  - a `count` on a record;
  - two device needs of one type;
  - a list as an image's `device` or `inputs` value.
- **What several devices per process would need later:**
  - a need declared as taking many;
  - the image giving a list for that need, and for each input that goes
    with it;
  - each boot record carrying an index beside its tag;
  - a driver written to serve them all.

  Refusing the list forms today means nothing written now changes meaning
  then.

**Rule 4.9. sawos's gate checks every in-tree declaration**
(`sosimage check-tree`, in the gate from U6). For every `process.toml`
in sawos, against every board, it checks:

- that the boards having a device of each type it names exist, and prints
  them;
- that each such board's maximum limits hold what the process declares;
- that every board input of its types is among its `inputs`, and that no
  default names one;
- that its file obeys §3.0.

The GPIO driver:

```toml
# drivers/svc-gpio-esp32s3/process.toml
format = 1
arena = 0x1000
ram = "fast"                # it services the switch's interrupt

[[boot]]
name = "system"
kind = "system"
tag = 1

[[boot]]
name = "gpio"
kind = "window"
type = "esp32s3-gpio"
tag = 2

[[boot]]
name = "iomux"
kind = "window"
type = "esp32s3-io-mux"
tag = 3

[[boot]]
name = "gpio"
kind = "line"
type = "esp32s3-gpio"
tag = 4

[[boot]]
name = "gpio"
kind = "serve"
protocol = "gpioproto"
tag = 5

[quota]
mappings = 2
handles = 11                # floor 8: 5 records, Process, 2 Mappings
waiters = 1

[build]
inputs = ["gpioconfig"]
```

The PWM driver, with the reach the owner ruled (rule 8.5): LEDC whole, and
two register ranges for its pads' routing.

```toml
# drivers/svc-pwm-esp32s3/process.toml
format = 1
arena = 0x1000

[[boot]]
name = "system"
kind = "system"
tag = 1

[[boot]]
name = "ledc"
kind = "window"
type = "esp32s3-ledc"
tag = 2

[[boot]]
name = "outsel"
kind = "window"
type = "esp32s3-gpio"
part = true
tag = 3

[[boot]]
name = "pads"
kind = "window"
type = "esp32s3-io-mux"
part = true
tag = 4

[[boot]]
name = "pwm"
kind = "serve"
protocol = "pwmproto"
tag = 5

[quota]
mappings = 3
handles = 10                # floor 9: 5 records, Process, 3 Mappings
waiters = 1

[build]
inputs = ["pwmconfig"]
```

---

## 5. The image description, and link addresses

### 5.1 The file

**Rule 5.1.** A hand-written image is TOML in the product's `images/`. A
test image the runner writes is JSON, with the same keys for the same
ideas and more keys of its own (rule 9.3).

**Rule 5.2. Its keys.**

- **`[image]`:** `name`, `board`, `tick` (a boolean).
- **`[limits]`:** the set numbers of §6.4 (`processes`, `threads`,
  `events`, `waiters`, `interrupts`, `timers`, `memories`, `iomemories`,
  `pipes`) and `quota`. Each defaults to the board's
  `[limits.default]`, then to the floor. A derived total (`records`,
  `attachments`, `mappings`) may be stated to raise it above its
  derivation.
- **`[root]`:** `package`: `"stock"`, or a package for a hand-written root.
- **`[[setup]]`** (stock root only):
  - `register`: a device reference to a register or one array element;
  - exactly one of `set` (OR the bits in), `clear` (AND them out) or
    `write` (store the word, for write-one-to-set registers), a 32-bit
    integer;
  - entries run in file order, before any child starts.
- **`[[process]]`:** for a hand-written root, `name` and `package` only.
  For the stock root:
  - `name`, `package`;
  - optional `device`: need name to device reference. It is required for a
    need whose type has several instances on the board, for a `part`
    window, and for a kernel device;
  - optional `inputs`: input name to path;
  - optional `ram`: a range name;
  - optional `quota`: the keys of rule 4.2;
  - optional `clients`: client record name to `PROCESS.RECORD`;
  - optional `pools`: memory record name to pool name;
  - optional `on-exit`: `report` (the default) or `shutdown`.
- **`[[pool]]`:** `name`, `len`, optional `ram`.
- **`[rows]`** (hand-written root only): `devices`, a list of device
  references naming whole devices; and `pools`, a list of pool names, in
  row order.

Per-process handle tables, pools as RAM limits, narrowed rights ceilings
and per-process stacks are deferred to SO-105 (§7).

**Rule 5.3.** A package is `sawos:<path>`, or a path in the product.

**Rule 5.4.** The builder refuses, by name and before compiling anything,
an image in which:

- a key, name, reference or path breaks §3.0;
- a type the process needs has no instance on the board, or several and
  the image names none, or a `part` window or a kernel device is unbound;
- a record is unbound, a tag repeated, or a `clients` value names no
  `serve` of a matching `protocol`;
- an input has no value, or a list form is used (rules 4.6 and 4.8);
- any limit or quota exceeds the board's maximum, a derived total is below
  its derivation, or a process's declared needs exceed what it is given
  (§6.4);
- any named range is over-asked, each budgeted separately;
- the linked areas exceed the kernel range's slack;
- the boot rows exceed the kernel's row bound, or any kind's rows exceed
  that kind's boot capacity: blobs, destinations and pools against
  Memories, devices against IoMemories (t14);
- root's table overflows at any step of the plan, or a child's `Handle`
  row is short.

### 5.2 Link addresses, regions and instances

**Rule 5.5.** The builder assigns every address in a hand-written image:

- root's region is the board's;
- each process's region and each pool come from its `ram` range;
- on an `in-place` board, each process gets a flash slot.

A test image's addresses are given by the runner.

**Rule 5.6.** A region is `round_up(.data + .bss + arena, grain) + stack`,
measured by a first link.

**Rule 5.7.** Per-process scripts are generated from one template per
board, and the hand-kept child scripts go.

**Rule 5.8.** On an `in-place` board the kernel's script includes a
generated `image_slots.ld`. In U4, `.drom` moves behind the last slot.

**Rule 5.9.** Row order: blobs, then destinations, then the image's rows.
For the stock root those are its device rows in first-use order, then its
pools. For a hand-written root they are `[rows]`.

**Rule 5.10. Where each process lives** (owner's rulings, Nov 14).

- `ram` on a process or pool names a `use = "process"` range. It defaults
  to `[ram.default]`.
- A `fast` process may not be in a `slow` range.
- Root's region and all kernel memory stay in internal SRAM.
- An image is built for one board, and there is no fallback (rule 3.6).
- A process is not told where it runs. `imageplan` records it for root's
  reports.

**Rule 5.11. One process per instance, and what it costs.**

- Each instance costs:
  - a body (1436 bytes of kernel SRAM on the S3);
  - a region (a 16 KiB stack plus its data and arena);
  - a flash slot on an `in-place` board;
  - two boot rows and a Process handle in root.
- Each instance is its own link of its package. The builder names its
  output by process: `build/<image>/<process name>.sosimg`.

### 5.3 Worked example: the useless box

`images/box.toml`, for the first run on the plain board:

```toml
format = 1

[image]
name = "useless-box"
board = "esp32s3"
tick = true

[root]
package = "stock"

[[setup]]                           # LEDC's clock on
register = "SYSTEM.PERIP_CLK_EN0"
set = 0x800

[[setup]]                           # LEDC out of reset
register = "SYSTEM.PERIP_RST_EN0"
clear = 0x800

[[setup]]                           # output enable, pads 41 and 42
register = "GPIO.ENABLE1_W1TS"
write = 0x600

[[process]]
name = "gpio"
package = "sawos:drivers/svc-gpio-esp32s3"
inputs = { gpioconfig = "config/gpio.saw" }

[[process]]
name = "pwm"
package = "sawos:drivers/svc-pwm-esp32s3"
device = { outsel = "GPIO.out_sel[41..=42]", pads = "IO_MUX.pad[41..=42]" }
inputs = { pwmconfig = "config/pwm.saw" }

[[process]]
name = "controller"
package = "controller"
clients = { switch = "gpio.gpio", servos = "pwm.pwm" }
on-exit = "shutdown"

[[process]]
name = "heartbeat"
package = "heartbeat"
```

What the builder makes of it:

- **Bindings.**
  - `gpio` gets GPIO and IO_MUX whole, by type, each having one instance.
  - `pwm` gets LEDC whole, plus two parts:
    - `GPIO.out_sel[41..=42]`: 0x6000_45F8, 8 bytes;
    - `IO_MUX.pad[41..=42]`: 0x6000_90A8, 8 bytes.
  - GPIO and IO_MUX are each bound twice, which is printed.
- **Rows: twelve.**
  - Four blobs and four destinations: 8 Memory rows, of the S3's 16.
  - GPIO, IO_MUX, LEDC and SYSTEM: 4 IoMemory rows, of 8. SYSTEM is a row
    because a setup write names it.
  - Within the row bound of 24.
- **IoMemory objects:** 4 rows, 2 parts, and 1 transient setup carve = 7,
  within 8.
- **Mappings:** GPIO's 2, PWM's 3 and root's 1 transient = 6, within the
  S3's derived 32.
- **Placement.**
  - GPIO is `fast` by its declaration, since it services the switch.
  - The rest take the board's default range, which on the plain board is
    `sram` too.
  - On `esp32s3-r8` (depends on SO-102) the image would add `ram = "sram"`
    on the controller, for its timing. The PWM driver and the heartbeat
    would go to the default, `psram`: LEDC times the pulses in hardware,
    and the heartbeat is bulk.
- **Bytes per range** (estimates; no driver exists):
  - kernel SRAM: 12.0 KiB of linked areas and growth, within 21.9 KiB of
    slack (§6.3);
  - process SRAM, plain board: root 96 + GPIO 24 + controller 32 + PWM 24
    + heartbeat 24 = 200 KiB, of 256;
  - on `esp32s3-r8`: SRAM 152 KiB, and PSRAM 48 KiB.
- **The cap: seven children.** The builder reports the smallest of four
  bounds:
  - processes: 8 less root = 7;
  - Memory rows: 16 / 2 = 8;
  - the row bound: (24 - 4) / 2 = 10;
  - root's table: 11.

**Handle counts** (by reading the plan against §1.3; root's table holds
32):

```
step                                                        root holds
entry: System, Process, Thread, 12 rows                             15
process_self                                                        16
create child 1 (+1), release its blob and destination (-2)      17, 15
children 2, 3 and 4                                 16/14, 15/13, 14/12
setup writes: carve (+1), map (+1); unmap, release both (-2)  14, 12 (x3)
gpio: System, GPIO and IO_MUX siblings, the line; each
      made then given                                      13, 12 (x4)
gpio: pipe_create (+2), give the outlet (-1)                     14, 13
pwm: System sibling; LEDC row given; two parts carved, given 14/13, 12,
                                                          13/12, 13/12
pwm: pipe_create (+2), give the outlet (-1)                      14, 13
controller: System sibling; two inlets minted, given, released  14..11
heartbeat: System sibling; release GPIO, IO_MUX, SYSTEM rows;
      supervise with one Waiter                                12..9
```

Root peaks at 17 of 32.

Children, against the S3's default `Handle` row of 28:

- GPIO peaks at 11;
- PWM at 10;
- the controller at 8;
- the heartbeat at 5.

### 5.4 Worked example: two UARTs

A sketch, on the plain board: neither the driver nor the UART numbers
exist or are checked.

```toml
# drivers/svc-uart-esp32s3/process.toml
format = 1
arena = 0x1000
ram = "fast"

[[boot]]
name = "system"
kind = "system"
tag = 1

[[boot]]
name = "uart"
kind = "window"
type = "esp32s3-uart"
tag = 2

[[boot]]
name = "uart"
kind = "line"
type = "esp32s3-uart"
tag = 3

[[boot]]
name = "uart"
kind = "serve"
protocol = "uartproto"
tag = 4

[quota]
handles = 8
waiters = 1

[build]
inputs = ["uartpins", "uartconfig"]
defaults = { uartconfig = "config/default.saw" }   # 115200 8N1
```

```toml
# images/logger.toml
format = 1

[image]
name = "logger"
board = "esp32s3"
tick = true

[root]
package = "stock"

[[process]]
name = "gps"
package = "sawos:drivers/svc-uart-esp32s3"
device = { uart = "UART1" }
inputs = { uartpins = "config/gps-pins.saw", uartconfig = "config/gps.saw" }

[[process]]
name = "modem"
package = "sawos:drivers/svc-uart-esp32s3"
device = { uart = "UART2" }
inputs = { uartpins = "config/modem-pins.saw" }

[[process]]
name = "logger"
package = "logger"
clients = { gps = "gps.uart", modem = "modem.uart" }
on-exit = "shutdown"
```

What the builder makes of it:

- **The driver.**
  - It lists `uartpins` because the board's `esp32s3-uart` type names it
    (rule 4.6).
  - Its window, line and serve all named `uart` are one need and one
    serve.
- **Bindings.** Both UART processes name their instance. The board has
  three of the type, and UART0 is the kernel's.
- **Inputs.**
  - `uartpins` comes from the image for both, since UART1 and UART2 have
    no board value.
  - `gps` takes the image's settings, and `modem` the driver's default.
  - The build prints each source.
- **Outputs.** `build/logger/gps.sosimg` and `build/logger/modem.sosimg`:
  two links of one package, in two slots.
- **Rows:** 6 Memory rows and 2 IoMemory rows. Root peaks at
  3 + 8 + 1 + 1 = 13 of 32.

### 5.5 Worked example: `uart_service_ns16550`

A hand-written root, as the case is today:

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

- **A hand-written root's processes** give only `name` and `package`. No
  declaration is read, and the root numbers its rows itself (rule 5.9).
- **The flat profile** is a second description naming
  `riscv32-virt-flat`.

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

**Ruled (Nov 14): (a).**

- **(b) would buy little.** Under fixed point 7 the board's defaults sit
  above the floors, so no image wants to lower them. The floors stay the
  numbers the test suite was written against, which is the strongest reason
  to keep them (§6.6).
- **(b) needs** every item above, a per-board `PIPE_INFLIGHT`, and a
  measurement of the access cost on the S3 before it is chosen.

### 6.3 Where the bytes come from, and what bounds them

The linked areas take the kernel's own RAM. This is the measured slack
between each linked kernel's end and its region's end (revision 4's
measurements, on main `2a13db8`):

| board | kernel region | kernel ends at (case) | slack | bound checked by |
|---|---|---|---|---|
| riscv32 | 0x8000_0000 to root at 0x8020_0000 | 0x8004_8EE0 (`process_donate`) | 1.72 MiB | nothing today; U2 adds an ASSERT |
| arm64 | 0x4000_0000 to root at 0x4020_0000 | 0x4007_3450 (`process_donate`) | 1.55 MiB | `virt.ld`'s ASSERT |
| ESP32-S3 | `kernel`: 0x3FC8_C000, 144 KiB, internal SRAM | 0x3FCA_A840 (`root_server_boot`) | 21.9 KiB | the `DRAM` region |
| ESP32-C3 | 0x4037_C000, 168 KiB | not measured | not measured | the `SRAM` region |

The S3's slack is internal SRAM: its kernel region is internal, and
nothing of the kernel is ever placed behind the cache. To give more, the
board's split moves: `esp32s3.ld`'s `DRAM` and `KERNEL_STACK_SIZE` (64
KiB, the cheapest lever), and `lib.saw`'s `RAM_BASE`. These are
per-board kernel facts.

**The box on the S3** needs 12.0 KiB of the 21.9, counted from the slab
symbols with the S3's 32-entry handle table and 16-row grant record. A
body is 784 + 512 (16 more handles) + 140 (7 more rows) = 1436 bytes
(t17).

| what | count | each | bytes |
|---|---|---|---|
| the floors' 3 bodies, grown | 3 | +652 | 1956 |
| bodies above the floor (8 processes) | 5 | 1436 | 7180 |
| records above the floor (derived: 4 x 8 = 32) | 20 | 104 | 2080 |
| Waiters above the floor (8) | 4 | 20 | 80 |
| Mappings above the floor (derived: 32) | 24 | 20 | 480 |
| attachments above the floor (derived: 52) | 20 | 24 | 480 |
| total | | | 12256 (12.0 KiB) |

This has not been linked as a whole.

### 6.4 The per-board limits, consistent by construction

**Rule 6.4. Every number is either set or derived.** Raising a set
number raises everything derived from it. The builder computes derived
totals, and an image may raise a derived total above its derivation, but
not below it.

The derivations are:

- **records** = `PROCESS_RECORDS_PER_BODY` (4) x processes. Every live
  process holds a record, and a supervisor keeps dead children's (t15).
- **threads** >= processes: every live process has a main thread.
- **attachments** >= events + interrupts + timers + records. This is
  `MAX_ATTACHMENTS`' own sum, taken over totals instead of floors (t15).
- **exchanges** = pipes x `PIPE_INFLIGHT`, computed inside `slab_install`.
- **mappings (slab)** = 4 x processes, and > the mapping quota default
  (t13). The `DEFAULT_QUOTA_MAPPINGS < MAX_MAPPINGS` assert becomes "the
  default is below the slab's boot capacity", the same relation over
  totals.
- **each quota default** is below its slab's boot capacity.
- **the mapping quota default** <= `GRANT_ROW_BUDGET` - `IMAGE_GRANT_ROWS`
  (4).
- **`DEFAULT_QUOTA_HANDLES`** < `MAX_HANDLES`. `MAX_HANDLES >= 5 +
  MAX_MEMORIES + MAX_IOMEMORIES` is a compile-time assert over one kernel's
  floors (below), and not a derivation over an image's totals. What the
  builder checks for an image is the rows that image has against root's
  table, step by step (rule 5.4) (t18).
- **the row bound** = Memories + IoMemories at boot. The kernel reads
  capacities, not floors (t14).
- **`PIPE_INFLIGHT`** = 2 x the threads *floor*. It is a per-board
  compile-time constant, because it is a dimension of the exchange chain.
  Its stated property (every thread can have a request in flight without
  filling a ring on its own account) holds up to the floor. Above the
  floor a full ring answers `WouldBlock`, a status. A board wanting the
  property at its default raises its threads floor, which deepens every
  ring and moves the suite's ring cases (7 packages name
  `PIPE_INFLIGHT`). No board does so now (t15).

The table below gives each number per board: riscv32-virt (and its flat
profile), arm64-virt, ESP32-S3 (and `-r8`), ESP32-C3. "Set" numbers are
stated in the HAL or `board.toml`; "derived" ones follow from the rule
above. Sizes are 32-bit unless marked.

| limit | kind | owner | HW or policy | today | proposed rv32 / a64 / S3 / C3 | cost |
|---|---|---|---|---|---|---|
| `GRANT_ROW_BUDGET` | set | HAL | HW on rv32, C3 (16 PMP entries as 8 TOR regions); policy on a64, S3 | 8 / 9 / 8 / 8 | 8 / 16 / 16 / 8 | 20 B a row a body (40 on a64): S3 +140 B, a64 +280 B (t17) |
| `MAX_GRANT_ROWS` | derived: max(budget, `MAX_ROOT_SEGMENTS` + 1) | HAL | as budget | 9 | 9 / 16 / 16 / 9 | as above |
| `MIN_GRANT_ROW_BUDGET` | removed | | | 8 | removed | none |
| `MAX_HANDLES` | set, >= 5 + row bound at the floors (29) | HAL | policy | 16 | 32 / 32 / 32 / 32 (C3 pending its slack) | +512 B a body; +896 B a64; walks 32 long |
| `PROT_DOMAIN_SLOTS` | set; ≤ 256 (8-bit ASID) on a64 | HAL | HW-bounded on a64 | 256 / 5 / 256 / 256 | 256 / 16 / 256 / 256 | a64 24 KiB of tables a slot: +264 KiB |
| slab floors (extent 0) | set | `limits.saw` | policy | processes 3, threads 8, events 8, waiters 4, interrupts 4, timers 8, memories 16, iomemories 8, mappings 8, pipes 4, free ranges 32 | unchanged on every board (the suite's numbers) | none |
| processes | set | `board.toml` default | RAM | 3 | 16 / 16 / 8 / 3 | S3: 5 x 1436 B |
| records | derived: 4 x processes | | | 12 | 64 / 64 / 32 / 12 | S3: 20 x 104 B |
| threads | derived: >= processes | | | 8 | 16 / 16 / 8 / 8 | 556 B each (frame included) |
| waiters, events, timers | set | `board.toml` | RAM | 4, 8, 8 | 16 each / 16 each / 8, 8, 8 / floors | 20, 24, 48 B |
| attachments | derived: sum | | | 32 | 100 / 100 / 52 / 32 | 24 B |
| memories, iomemories | set; their sum is the row bound | `board.toml` | RAM | 16, 8 | 32, 16 / 32, 16 / 16, 8 / floors | 56, 20 B |
| mappings (slab) | derived: 4 x processes, > quota default | | | 8 | 64 / 64 / 32 / 12 | 20 B (t13) |
| pipes | set | `board.toml` | RAM | 4 | 8 / 8 / 4 / 4 | 3.5 KiB each (16 exchanges) |
| `PIPE_INFLIGHT` | derived: 2 x threads floor | HAL | policy | 16 | 16 everywhere | unchanged (t15) |
| quota: mappings | set, <= budget - 4 and < mapping slab | `board.toml` | policy | 2 | 4 / 12 / 12 / 4 | a number |
| quota: handles | set, < `MAX_HANDLES` | `board.toml` | policy | 12 | 28 everywhere | a number |
| other quotas | set, each < its slab | `board.toml` | policy | 4, 4, 2, 4, 4, 2, 2 | unchanged | none |
| `ROOT_STACK_LEN` | set | HAL | policy | 16 KiB | unchanged | none |
| `KERNEL_STACK_SIZE` | set | board script | policy | 64 KiB | unchanged; the S3's lever | none |
| region length, slots | computed | builder | policy | 256 KiB / 96 KiB; one S3 slot | computed; S3 4 slots before U4 | none |

**The most an image may ask for** is computed, not listed:

- each slab's total within `MAX_SLAB_CAPACITY`;
- processes within `PROT_DOMAIN_SLOTS`;
- all linked bytes within the kernel range's slack.

**Which asserts stay compile-time, per board** (they relate set constants
of one kernel):

- `MAX_ROOT_SEGMENTS + 1 <= MAX_GRANT_ROWS`;
- `GRANT_ROW_BUDGET <= MAX_GRANT_ROWS`;
- the threads, process and handle floors' relations;
- `MAX_HANDLES >= 5 + MAX_MEMORIES + MAX_IOMEMORIES` (new);
- `MAX_HANDLES <= 255`;
- the capacity, stride and ring asserts;
- `MAX_PROCESSES <= PROT_DOMAIN_SLOTS`.

**Which become builder checks, with the kernel's `fatal_image` behind
them** (they relate an image's totals):

- every derivation above;
- each quota default below its boot capacity (the mapping one is t13's);
- the row bound and each kind's rows within its capacity (t14);
- root's handle peak;
- processes within the domain slots;
- the slack.

**Portability.** A program that needs what a small board cannot give
does not run there. Its declaration says what it needs (rule 4.3), and
the builder and the gate (rule 4.9) refuse by name. The PMP boards' 4
mappings is the hardware case. The ESP32-P4's 32 PMP entries would set
16.

### 6.4a Where the design had bent to fit a number

| bend | number | HW or policy | revision 5 |
|---|---|---|---|
| root carving from one peripheral row | 16 handles | policy | withdrawn; 32 handles |
| the child cap | handles, rows, memories, processes | policy (processes: RAM) | the builder reports the smallest; the box's is 7, by processes |
| PWM setup moved into root | mapping quota 2 | policy | raised to the board's rows; the reach ruled narrow anyway (rule 8.5) |
| `QuotaSet` for the box | quota defaults | policy | not needed |
| S3 grant budget 8 | copied from PMP | policy on S3 | 16 |
| 96 KiB regions | runner's slot | policy | computed |
| one S3 child slot | `esp32s3.ld` | policy | four now, more after U4 |
| raise-only | floors | (kept for the suite) | unchanged |

What stays, because hardware sets it: the PMP boards' 8 regions,
arm64's 256 ASIDs, and each board's RAM.

### 6.5 The ESP32-S3 boards (depends on SO-102 for `-r8`)

- **`esp32s3` (no PSRAM described).**
  - Processes have 256 KiB of SRAM: root 96 KiB, and 160 KiB for children
    and pools, which is four to six children of about 24 to 32 KiB.
  - The process default is 8: root and up to seven.
  - The builder refuses an image whose regions do not fit.
- **`esp32s3-r8` (8 MiB of PSRAM described, the default process range).**
  - Unpinned regions and pools go to PSRAM. Internal SRAM keeps root, the
    kernel and every `fast` process.
  - The bound on processes is then the kernel's 21.9 KiB of slack. At
    1436 bytes per body, plus a record, a thread and a Waiter each (about
    2.1 KiB a process), that is roughly 4 more beyond the default's 8.
  - A board that wants 16 moves its kernel stack or its DRAM split (§6.3).
  - The board's process default stays 8 until it does.
- **No fallback** on either: the r8 kernel proves the PSRAM or stops
  (SO-102 §5.2).

### 6.6 What it does to the test suite

1. **A test image gets the kernel's floors and today's quota defaults**
   unless its case says otherwise. Board defaults never apply to test
   images, so every case that reaches a slab floor or a quota on purpose
   reads the same on every board.
2. **A case that reaches a per-body array or a HAL number reads it per
   profile.**
   - The text side: a placeholder in `expectations()` (`{handles}`,
     `{grant_rows}`).
   - The program side: a per-profile constant, as `tests/irq-lines` gives
     lines.
   - Known so far:
     - `pipe_table_full` and `pipe-consume`, which both spell
       `FILL_SLOTS = 16`;
     - `quota_vs_wall` and `child-mapwall`;
     - arm64's domain-wall comments in `process_donate`.
   - U2's first task is the full list, made by running the suite with each
     moved number.
3. **A hand-written image gets its board's defaults.**

### 6.7 Revision 3's reserve, compared, and the one check kept (t16)

- **Placement.** The linker places each area inside the kernel's `.bss`.
  The kernel reads the block, so placement is only as true as the block.
  Two things make it true:
  1. **The block's length is the section's.** The builder writes
     `.4byte _sos_slab<k>_end - _sos_slab<k>_start` beside the base's
     relocation. The assembler computes it, so no number is written twice.
  2. **The kernel checks every area against bounds the board script
     supplies, and that no two meet.** The bounds are `[_bss_start,
     _arena_start)`: the `.bss` input sections, and not the arena or the
     stack behind them. Each board script defines both symbols, and each
     HAL gains one accessor for them in its `sink.c`, as for the region
     table.
- **Root's region and the process ranges** lie outside `.bss` by the
  board script's own layout, given riscv32's new ASSERT.
- **Gained against the reserve:** no trust in the builder for placement,
  overflow as a link error that names the region, and no process RAM
  spent.
- **Lost:** the bound is the kernel range's slack (21.9 KiB on the S3).
- I have not built the bounds check. It is U2's.

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

**Rule 7.5. Deferred to SO-105** (owner, Nov 14: "all of this can be
later since it's not strictly needed for the useless box but will be as
we scale the images e.g. support networking"). These are not designed
here:

- a handle table sized per process;
- a pool per process as its RAM limit;
- rights ceilings narrowed to a declaration;
- a per-process stack.

For the box, this design stands as ruled. An image's `[[process]]` has no
key for any of the four, and the loader refuses one as an unknown key.

---

## 8. Root's interfaces

### 8.1 Kernel contract (spec; cited)

1. **Entry:** System in the first argument register (§5.7, §2.8).
2. **Region and image:** the board's root region; unlimited quotas except
   the mapping clamp, which reads the Mapping slab's capacity (t13);
   unrestricted ceilings (§5.6, §2.9, §2.7).
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

9. **`imageplan`.** For each child: ordinals, quota rows, the
   records to give (tag, source, keep mask, and for a range the carve
   offset and length), the start tag and the exit policy. Also the
   connections and the setup writes. Its types live in `bootplan`.

### 8.3 Convention among userspace programs (new text for §12)

> **The launch convention** (userspace). A launcher that follows it gives
> a child exactly its declared records, under their tags, in declared
> order, before start. It starts the child with its System record's tag,
> masked with `Debug | ProcessSelf | ClockGet | Transfer`. It sets declared
> quota rows before any give. It gives
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
writes it, unmaps it, and releases both, before starting anyone.

This is how the box's LEDC clock gate, its reset and the pads' output
enable are written: by root, once, as data in the image (ruled Nov 14).
The PWM driver holds LEDC and its two pads' routing ranges, and nothing
of SYSTEM. The pads keep their reset state until the driver's `Setup`
selects GPIO and routes the channel, then show its idle level, low (read
from SO-77's notes, not seen on a part).

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
the SHA-256 (through `shasum`), the board the image is for, and the
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

**As reported by the sawlang lead:** the owner ruled, in the sawlang
session, that every gap in this section is valid and will be fixed, the
Blade flags (11.7) first, then `libs/toml` (11.1). The workarounds named
below stand until each fix reaches sawos's pin.

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

**12.1 Authority.** A description gives a process only devices its board
lists, a boot row covers one listed device, and the kernel's devices are
bound only by name. A replaced root is held to the board's list, because
it holds only rows.

**12.2 Raised numbers are bounds too.**
- A 32-entry table and a 16-row grant record lengthen walks the kernel
  makes with interrupts masked: teardown, mint and map.
- Raising a set number raises its derived ones, and their bytes come out
  of the kernel's slack. That is 21.9 KiB on the S3, where the box takes
  12.0 KiB.
- Nothing about interrupt latency was measured.

**12.3 Portability.** A driver that needs what a small board lacks does
not run there. The gate's check (rule 4.9) names that before any image
does.

**12.4 One process per instance.** Two UARTs served cost two bodies, two
stacks, two regions and two flash slots. Under fixed point 6 that is the
price of the simpler driver.

**12.5 The linked areas.** The block is checked against the script's
`.bss` bounds (t16). On riscv32 an overflow is silent until U2's ASSERT,
which is a bug in today's tree.

**12.6 PSRAM.** The bring-up, the identity checks and the stops are
SO-102's, and not reviewed. An image for `esp32s3-r8` runs only on a part
that matches that board.

**12.7 sawos moving under a pin.** Nothing moves until a product bumps
its pin. The formats are versioned and refused when unknown, and the
gate's scratch product (U7) catches a break first.

**12.8 Two languages, and Saw as the tool language.**
- The runner (Python) and the builder (Saw) meet only in the JSON test
  image.
- A sawlang release that breaks hosted builds is caught at sawos's pin
  bump.

---

## 13. Alternatives rejected, and deferred

- **Deferred, not rejected: one driver process serving several devices.**
  The owner's reason: the simpler thing first. The list forms are refused
  by name today (rule 4.8), so nothing changes meaning when it comes.
- **A per-board dictionary of devices in each driver**
  (`device = { esp32s3 = "GPIO" }`). It is replaced by types. A new board
  with the same peripheral needs no driver change.
- **`compatible` strings and a `boards` list.** They are replaced by
  `type`, and the boards are computed.
- **A fallback layout, or a boot-time presence probe.** Ruled out by
  fixed point 10.
- **Depth (b).** Not needed while defaults sit above floors (§6.2).
- **A reserve in process RAM.** It spends process RAM and needs the
  builder trusted for placement (§6.7).
- **Root carving windows from one peripheral row.** It hands root the
  megabyte at 0x6000_0000, eFuse and the interrupt matrix included (t1).
- **Minting boot handles at drain, or the handle table as a linked area.**
  Both are kernel and §12 changes that a 32-entry table makes unneeded.
- **A kernel per image.** Every image would boot a kernel the gate never
  ran.
- **A kernel cache keyed by revision.** It is stale under any uncommitted
  edit (t11).
- **A per-case builder that builds packages.** It races in a shared
  package directory (t9).
- **A hand-kept slot-size literal.** The symbol table gives the size (t7).
- **A builder in Python.** Ruled out: tools are Saw where possible.
- **JSON for hand-written descriptions.** JSON has no comments and no hex.
- **Quotas in the sosimg.** That would be an image claiming for itself.
- **The kernel as a Blade package.** It would teach Blade a recipe sawos
  already owns.
- **Git dependencies, or a submodule, for products.** Blade cannot reach a
  subdirectory (gap 11.8), and the owner asked for a pin.

---

## 14. Cost and order of work

Units marked sawlang are the sawlang lead's. SO-102's units are named by
its letters (A to D).

- **U1. Kernel, stitching and linking in Saw, transcript-identical
  (sawos; Sonnet, medium).**
  - Rules 9.1 to 9.4 and 9.7.
  - The runner keeps its package phase, builds each board's kernel once
    per run per entry, and calls `sosimage link` per case with a JSON test
    image.
  - Test: every transcript, total and image size identical to the base.
- **S1, S2 (sawlang).** Blade's flags and avoidance; `libs/toml`.
- **U2. Per-board limits and linked slab areas (sawos kernel and spec;
  Opus for the spec, Sonnet to build; medium-large).**
  - `install_linked_slabs` with the bounds check (t16), and the table at
    version 5;
  - the per-board constants in each HAL (§6.4);
  - `sos_board_info`;
  - the row bound and root's mapping allowance from capacities (t13, t14);
  - riscv32's ASSERT;
  - the builder's derivations and checks (§6.4);
  - the C3's slack measured;
  - the list of cases reaching moved numbers, and their per-profile reads.

  Tests: the linked cases on every board; the derivation refusals;
  t14's row refusals; an area outside the bounds; root's table at and
  past its limit.
- **U3. The formats (sawos; Opus; medium; after S2).**
  - `board.toml` per §3, for each board, with devices by type;
  - `process.toml` per §4;
  - the image per §5;
  - §3.0 enforced by the loader: unknown keys, suffixed sizes, bad names,
    reserved words, the reference grammar (`..` refused, `..=` inclusive)
    and every uniqueness rule, each refused by name with a test;
  - `[types.<type>] inputs` and the board-input rules of 4.6;
  - `uart_service_ns16550` from a file, identical to its case.

  Beside it, SO-102's A and B.
- **SO-102's C.** It comes before U4, and brings the `esp32s3-r8` facade
  board with its `psram` range (depends on SO-102).
- **U4. Generated addresses (sawos; Sonnet, medium; after S1's pin bump,
  U3 and SO-102 C).**
  - computed regions and named ranges, with per-range budgets;
  - `ram = "fast"` enforced;
  - artifacts named by instance (rule 5.11);
  - `.drom` behind the last slot;
  - the hand-kept scripts gone;
  - SO-102 D's hand-written PSRAM script generated.
- **U5. The quota door (sawos kernel and spec; Opus for the spec).** Rule
  7.2, with a test per fault and status. Its below-use test gives first.
- **U6. Declarations and the stock root (sawos; Opus for `bootplan`).**
  - types, instances, shared bindings and the list-form refusals;
  - parts carved and setup writes made by the stock root (rule 8.5);
  - input precedence (rule 4.6);
  - `BootSet` and `imageplan`;
  - the stock root;
  - `sosimage check-tree` in the gate (rule 4.9).
- **U7. Products. U8. The harness in Saw (after S3). U9. The C3 smoke.**

**Where SO-77, SO-78 and SO-102 sit.**

- SO-77's drivers come after U4 and U5, declared by type.
- SO-78 comes after U6, U7 and the drivers.
- **The box's first run is on the plain `esp32s3` board and needs nothing
  from SO-102.**

**The order, at most two agents at once across both repositories:**

1. U1, beside S1.
2. U2, beside S2.
3. U3, beside SO-102 A and B.
4. SO-102 C, beside U5.
5. U4, beside SO-77's GPIO driver.
6. U6, beside the PWM driver.
7. U7, beside SO-102 D.
8. U8 and U9.
9. SO-78.

---

## 15. The owner's rulings

All from Nov 14. The design's text already applies each.

1. **Per-board limits,** derived from the hardware where there is a
   hardware fact (§6.4).
2. **No fallback:** a part matches its board description, or the boot
   stops (rule 3.6).
3. **Each process and pool placed by the image** (rule 5.10).
4. **One name for one kind of thing** (§3.0).
5. **Devices by type, one process per instance, one set of devices and
   inputs per process for now** (rules 3.3, 4.8).
6. **Per-process limits deferred to SO-105** (rule 7.5).
7. **"Agree with all recommendations for so-95."** That settles each of
   these, as follows:
   - linked areas beside the board's floor, raise-only (§6.2);
   - a 32-entry handle table on every board, and rows per listed device
     (§6.4, rule 3.3);
   - the PWM driver's reach is the narrow one: root's setup writes for
     LEDC's clock gate, its reset and the pads' output enable, with the
     driver holding LEDC and two routing ranges (rule 8.5, §5.3);
   - the per-board values and derivations of §6.4;
   - inputs from the image, then the board instance, then the driver
     (rule 4.6);
   - the console bound only when an image names it (rule 3.3);
   - `PIPE_INFLIGHT` = 2 x the threads floor, its property holding up to
     the floor (§6.4);
   - test images keep the floors and today's quotas (§6.6);
   - driver pins as a build-time module (rule 4.6);
   - TOML for hand-written files, JSON for test images (rule 5.1);
   - sawlang's Blade flags and `libs/toml` ahead of the box (§11, §14);
   - the runner moved stepwise (§10);
   - the C3 smoke in Python until U9;
   - one board per image (rule 5.10);
   - the resolvers in Python (§10);
   - a pin and `.sawos` for products (rule 9.8);
   - the stock root's exit policy (rule 8.1);
   - the box in its own repository.

---

## 16. Verified, read, the reviewer's, and not checked

### 16.1 Verified by me, by running (scratch only)

- **Revision 4's prototype**, on main `2a13db8`:
  - linked slab areas on riscv32, arm64 and the ESP32-S3;
  - refusals by boot line and by link error;
  - the silent riscv32 overflow;
  - slot sizes from symbols matching `slab_geometry`;
  - a 32-entry table's body sizes (+512 B and +896 B);
  - the slack on three boards;
  - the S3's 26.5 KiB of floors.
- **Earlier revisions:** the Saw probes behind section 11, Blade's
  behaviour, the `.tool` collision, and a package built through a link.
- **This revision:** no new runs. I re-read the cited lines on main
  `096dd17`:
  - the boot-row refusals ("more boot regions than the kernel can hold",
    "out of Memory slots for the boot regions", "out of IoMemory slots for
    the boot regions");
  - `install_quota_limits`' `MAX_MAPPINGS`;
  - `PIPE_INFLIGHT`, `MAX_ATTACHMENTS` and `PROCESS_RECORDS_PER_BODY`;
  - the `DEFAULT_QUOTA_MAPPINGS < MAX_MAPPINGS` assert;
  - every board script's `_bss_start` and `_arena_start`;
  - `pipe-consume`'s `FILL_SLOTS`.

### 16.2 Read, not run

- The GrantRow size (20 B on 32-bit) behind the S3 body of 1436 bytes:
  784 + 512 + 7 x 20. The 784 and +512 were measured.
- The attachment (24 B) and record (104 B) units: from revision 4's
  symbol reads.
- The box's handle counts.
- SO-102's ranges, facade board and units.

### 16.3 The reviewer's runs

- Revision 4's list.
- t13: the ninth Mapping refused at the slab, on arm64.
- t15: `process_records_donate` on riscv32 and arm64.

### 16.4 Not checked

- **The bounds check of t16,** and the derivations in a linked whole.
- **The C3's slack.**
- **The S3 UART1 and UART2 addresses and lines, and every UART driver in
  §5.4:** placeholders.
- **The box's regions;** no driver exists.
- **Everything about PSRAM** (SO-102, unreviewed).
- **Interrupt latency** from longer walks.
- **Which cases move with each per-board number** (U2's first task).

---

## 17. Response to the review

| item | finding | response | where |
|---|---|---|---|
| t1 | 12 rows fill a 16-entry table | Rows per listed device, table 32, root at 17 of 32 (revision 4) | §5.3, 6.4 |
| t2 | the reserve's checks | Reserve gone; areas in `.bss`, checked against the script's bounds (t16) | §6.7 |
| t3 | cap, not budget; `Mapping`; sentinel | Accepted (revision 3) | §7 |
| t4 | a given window spends no `IoMemory` | Accepted | rule 4.3 |
| t5 | slots over `.drom` | Accepted; four slots until U4 | §3.1, rule 5.8 |
| t6 | 24 KiB regions | Accepted; computed | rule 5.6 |
| t7 | slot sizes from symbols | Accepted; used by the prototype | §6.1 |
| t8 | the PWM driver's reach | Ruled narrow: setup writes plus LEDC and two ranges | rule 8.5, §5.3 |
| t9 | per-case builder races | Accepted; package phase kept | rule 9.2 |
| t10 | U1's wider input; C3; profiles | Accepted | rule 9.3 |
| t11 | revision-keyed cache | Accepted; no cache | rule 9.4 |
| t12 | Blade behaviours | Accepted | rule 9.6 |
| t13 | mapping default 12 above the slab's 8; root's allowance is the floor | Accepted. The mapping slab is derived (4 x processes, above the default); the assert is restated over boot capacities; root's allowance reads the capacity | §6.4, U2 |
| t14 | the cap is 10 by rows and 8 by S3 Memories; the builder lacks both checks | Accepted. The row bound reads capacities; the builder checks the row bound and each kind's rows; the box's cap is 7, by processes; "eleven" corrected | rule 5.4, §5.3, 6.4 |
| t15 | records, attachments and `PIPE_INFLIGHT` left behind | Accepted. Records and attachments derived; `PIPE_INFLIGHT` kept as 2 x the threads floor, its property restated, and threads >= processes | §6.4, §15 |
| t16 | the block, not the section, is what the kernel trusts | Accepted. Length from label difference; kernel checks each area inside `[_bss_start, _arena_start)` and pairwise disjoint | §6.7, U2 |
| t17 | the S3 body omits its grant-record growth | Accepted. Body 1436 B; total 12.0 KiB with records and attachments; "+160 B" corrected to +140 B; `pipe-consume` added | §6.3, 6.4, 6.6 |
| owner: no fallback | the part matches or the boot stops | `probed`, the presence check and the SRAM layout removed; `esp32s3-r8` a board of its own | §3.1, rule 5.10 |
| owner: names | one name for one kind of thing | §3.0; every example rewritten; flash is the part's 8 MiB | §3.0, 3.1 |
| owner: types | devices by type, one process per instance | `type` on devices and declarations; no `boards` or `compatible`; instances named only when ambiguous; priced | §3.1, 4, 5.4, rule 5.11 |
| t18 | `MAX_HANDLES >= 5 + row bound`, read from capacities, refuses riscv32 and arm64 | Accepted. It is a compile-time assert over the floors only; an image's rows are checked against root's table; the box's cap lists the row bound's 10 | §6.4, §5.3 |
| t19 | `[41..42]`: one register or two | Accepted. `..=` is inclusive, as in Saw; `..` is refused by name; the builder prints first and last register and bytes | §3.0, §5.3 |
| t20 | a record name's uniqueness unstated; three reserved range names | Accepted. Three name families per declaration; one device need per type; reserved `default`, `fast`, `slow` and `root`; uniqueness listed for every file | §3.0, rule 4.2 |
| t21 | a board's default input keyed by a driver's chosen name | Accepted. `[types.<type>] inputs` names a type's board inputs; every driver of the type must take them; a driver default for one is refused; an image override is printed | rules 3.2, 4.6, 4.9 |
| owner: all recommendations | Nov 14 | Every open question written into its rule; §15 lists the rulings | §15 |
| owner: SO-105 | per-process limits later | Deferred, one paragraph | rule 7.5 |
| owner: gate check | check every in-tree `process.toml` against every board | `sosimage check-tree` | rule 4.9, U6 |
| owner: one set per driver | devices and inputs singular for now | List forms refused by name; the later shape sketched; deferred | rule 4.8, §13 |
| SO-102 | two boards, ranges, units | Taken as depending on SO-102: `esp32s3-r8`, `psram` at 0x3D00_0000, 16 MiB windows; SO-102 C before U4 | §3.1, 6.5, 14 |

Disputed: nothing in t13 to t21. From revision 4, the lead's premise
that a free slot is not all zeros stays disputed (§0.1).
