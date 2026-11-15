# hal/xtensa-esp32s3 — the board facts

The record of what the ESP32-S3 emulator does, probe by probe, for the Xtensa
HAL that will be written against it. Nothing here is from a datasheet unless it
says so: each fact carries the probe that produced it (what was run and the
output that matters) or a cited file. "The emulator" means the QEMU below;
"silicon" means an ESP32-S3 part. A claim probed on a part says so and names
the board; every other claim is the emulator's or a source's. Where the two
can disagree, the section says so, and §11 collects every such place.

Oracle:

```
~/.espressif/tools/qemu-xtensa/esp_develop_9.2.2_20250817/qemu/bin/
    qemu-system-xtensa -machine esp32s3
QEMU emulator version 9.2.2 (esp_develop_9.2.2_20250817)
bundled mask ROM banner: ESP-ROM:esp32s3-20210327 / Build:Mar 27 2021
ROM ELF: ~/.espressif/tools/esp-rom-elfs/20241011/esp32s3_rev0_rom.elf
Espressif clang version 20.1.1 (... esp-20.1.1_20250829)
```

The `qemu-system-xtensa` a package manager puts on `PATH` is not this one and
has no `esp32s3` machine; always name the Espressif binary.

The probes were small direct-boot images built as §1 describes: C and
assembly, call0, printing on UART0 (§4), with a fault-catching vector table
(§5). A bare stub built the same way boots, prints a banner, takes a
`syscall` and three timer interrupts, and ends the emulator with a status;
its transcript closes this file (§12).

---

## 1. The build spelling

### 1a. Triple, CPU, ABI

```
clang --target=xtensa-esp-elf -mcpu=esp32s3 \
      -Xclang -target-feature -Xclang -windowed \
      -Xclang -target-feature -Xclang -fp \
      -fintegrated-as -ffreestanding
```

- The driver normalises the triple: `-###` shows
  `"-triple" "xtensa-esp-unknown-elf"` `"-target-cpu" "esp32s3"`.
- The default ABI is windowed. `-###` for `-mcpu=esp32s3` lists, among
  others, `+windowed`, `+fp`, `+loop`, `+mac16`, `+bool`, `+threadptr`,
  `+s32c1i`, `+div32`, `+mul32high`, `+esp32s3`; plain `-O2` code begins
  with `entry a1, 32` and returns with `retw.n`.
- There is no `-mabi`: `-mabi=call0` gives
  `clang: error: unsupported option '-mabi=' for target 'xtensa-esp-elf'`, and
  `-mno-windowed` is `unknown argument`. call0 is the cc1 feature
  `-windowed`, which turns the same function into `callx0`/`ret.n` with an
  explicit `addi a1, a1, -16` frame.
- `-msoft-float` is accepted and ignored (`wfr`/`mul.s` still emitted). The
  cc1 feature `-fp` is what removes FPU use: float arithmetic becomes calls
  (`__ltsf2`, `__muldf3`).
- `char` is unsigned: `-###` passes `-fno-signed-char`.

### 1b. Assembler and linker

- clang hands a `.S` file to an external GNU assembler
  (`-###` ends in `xtensa-esp32s3-elf-clang-as`), which writes
  zlib-compressed debug sections that the bundled `ld.lld` then refuses:
  `ld.lld: error: ...(.debug_line) is compressed with ELFCOMPRESS_ZLIB, but lld
  is not built with zlib support`. `-fintegrated-as` makes `.S` use the same
  integrated assembler C already uses (C's `-###` names no external `as`), and
  it accepts every instruction in the stub, `.literal` and `.org` included.
- The linker is the `ld.lld` beside Espressif's clang (the driver's link line
  invokes it). GNU `xtensa-esp32s3-elf-ld` (GNU ld 2.45) accepts the same
  objects (given a call0 object needing `__udivdi3`, both linkers report only
  that undefined symbol). A package manager's `ld.lld` is not Espressif's
  build; it was not used here.

### 1c. Literal pools: `.literal` must precede `.text`

`l32r` loads a constant from a PC-relative literal that must sit **below** the
instruction, within 256 KiB. The compiler emits the pool for `.text.foo` into
`.literal.foo` (and for `.text` into `.literal`). Probed by linking a reset
path with its literal section placed after its code:

```
ld.lld: error: boot.S.o:(.text.start+0x0): relocation R_XTENSA_SLOT0_OP out
  of range: 168 is not in [-262141, 18446744073709551612]; references section
  '.literal.start'
```

So every output section that holds code lists its literals and its code in one
input pattern, `*(.literal .literal.* .text .text.*)`, which the linker orders
by input file and, within a file, by section order, so a function follows its
own pool. Listing every pool ahead of every function instead would put a
function more than 256 KiB past the pools it reads out of reach, once the code
is that large. The reset path's pool and code lead, and the vectors' literals
are named in a section of their own. The kernel's script, which links cleanly:

```
.text : { *(.literal.start) *(.text.start)
          *(.literal .literal.* .text .text.*) } > FLASH
```

### 1d. Integer and float helpers: nothing call0 ships

`-print-multi-lib` (GNU) and the clang `multilib.yaml` list rows only for
`-mcpu=esp32`, `esp32s2` and `esp32s3` (plus psram and no-rtti variants); none
is call0. Disassembly of `__udivdi3` in both
`esp-clang/lib/clang-runtimes/xtensa-esp-unknown-elf/esp32s3/lib/
libclang_rt.builtins.a` and the same directory's `libgcc.a` begins with
`entry a1, 48` / `entry a1, 32`: windowed. The mask ROM's functions are
windowed too (every ROM function disassembled here opens with `entry`).

What call0 code needs from a helper library, seen in the compiler's output:
32-bit multiply and divide are inline (`mull`, `mulsh`, `muluh`, `quou`,
`quos`); 64-bit multiply is inline; 64-bit divide calls `__udivdi3`;
an 8-byte atomic calls `__atomic_fetch_add_8`; float and double arithmetic
call the soft-float helpers. A call0 image must supply its own copy of any of
these it reaches, or not reach them. The stub reaches none.

### 1e. The image

Flash images are 4 MiB of `0xFF` with each `PT_LOAD` segment written at
`LMA - 0x4200_0000`, attached as `-drive file=<image>,if=mtd,format=raw`.
QEMU prints `Adding SPI flash device` on stderr when a drive is attached and
`Not initializing SPI Flash` without one; stderr is noise for a transcript.

A board image is the same bytes cut after the last one placed, written at
flash offset 0x0 (`tools/sos_runner.py --board esp32s3 --emit-flash CASE
--out PATH`). `esp32s3.ld` places the kernel's read-only data last, at flash
offset 0x20_0000, after root's and a child's image slots (§13), so a board
image is a little over 2 MiB whatever the case, its gaps 0xFF, as erased
flash reads. The build reads each resident image back out of it and refuses
one not linked for its slot, as for the smoke's images. Padded back to 4 MiB
it boots under the emulator like the smoke's own image, apart from its stop
(§10).

---

## 2. Boot

### 2a. The ROM's direct boot, read from the ROM ELF

The S3 mask ROM has a direct-boot path. `ets_run_flash_bootloader`
(0x4004_578C) maps flash, then tests the first byte of the image header:

```
40045804: l8ui  a7, a3, 60      ; header byte 0
40045807: movi  a5, 233         ; 0xE9, the esptool image magic
4004580a: bne   a7, a5, 40045834
```

When that byte is not `0xE9`:

```
40045834: call8 ets_efuse_secure_boot_enabled ; secure boot on -> no
4004583a: l32r  a5, (6000703c)                ; eFuse word, bit 1
40045843: bbsi  a5, 1, 40045851               ; set -> no direct boot
40045849: call8 ets_loader_is_direct_boot_jump
4004584e: call8 ets_run_direct_boot
```

`ets_loader_is_direct_boot_jump` (0x4004_5684) compares the two words at
flash offset 0x400 with `0xAEDB041D` (the second through
`word + 0x5124FBE3 == 0`). `ets_run_direct_boot` (0x4004_56B4) then:

```
Cache_Ibus_MMU_Set(0, 0x42000000, 0, 64, 0x200, 0)  ; flash 0 -> 0x4200_0000
Cache_Dbus_MMU_Set(0, 0x3C000000, 0, 64, 0x200, 0)  ; flash 0 -> 0x3C00_0000
l32r a8, (42000408) ; callx8 a8                     ; CALLX8 flash offset 0x408
ets_printf("Direct boot returned") ; uart_tx_flush ; software_reset
```

The eFuse bit at 0x6000_703C bit 1 is the one the ROM's
`ets_efuse_legacy_spi_boot_mode_disabled` (0x4004_29C4) reads.

**The contract**, verified by booting it: flash offsets 0x400 and 0x404 both
`0xAEDB041D`, the first instruction at flash offset 0x408, offsets 0..0x3FF
anything but `0xE9` at byte 0 (the stub leaves them `0xFF`). The ROM maps
32 MiB of flash at both windows (0x200 pages of 64 KiB) and enters with
**`callx8 0x4200_0408`**. If the image returns, the ROM prints
`Direct boot returned` and resets. The esptool-format path (header `0xE9`)
exists and was not exercised.

### 2b. CPU state at handover

Probed: the first instructions at 0x4200_0408 store the registers before
anything else touches them.

```
a0=0x80045851  a1=0x3fceb550  ps=0x00060320
windowbase=5   windowstart=0x2a   vecbase=0x40000000
intenable=0    cpenable=0         prid=0xcdcd     memctl=0x1
atomctl=0x15   lbeg=0x4004f705    lend=0x4004f709 lcount=0
threadptr=0    configid0=0xc2f0fffe configid1=0x23090f1f
```

- `PS = 0x00060320`: WOE=1, CALLINC=2 (the CALLX8), OWB=3, UM=1, EXCM=0,
  INTLEVEL=0. The core arrives **windowed**: the caller's `a8..a15` are this
  code's `a0..a7` only after an `entry`, which the stub never executes;
  `a0` here is the ROM caller's own return address (0x4004_5851 in
  `ets_run_flash_bootloader`), not a way back.
- `a1` is the ROM's stack, inside its `.stack_pro` (0x3FCE_9704, 0x200C
  bytes, from the ROM ELF's section headers).
- `VECBASE` is the ROM's table at 0x4000_0000; interrupts are all disabled;
  the FPU is off (`CPENABLE=0`), which `_start` writes again rather than
  rely on; `PRID=0xCDCD` is the PRO CPU.

The stub's first act is to give up the window and the ROM's vectors:

```
rsr   a9, ps ; wsr a9, excsave1     ; keep anything worth keeping in an SR
movi  a9, 1  ; wsr a9, windowstart
movi  a9, 0  ; wsr a9, windowbase ; rsync
movi  a9, 0x0F ; wsr a9, ps ; rsync ; INTLEVEL=15, EXCM=0, UM=0, WOE=0
```

Writing `WINDOWBASE` renames every address register. Probed: a value kept in
`a12` across that write read back afterwards as `0x80043ab9` instead of the
`PS` it held, so the handover `PS` is parked in `EXCSAVE1` instead. After this
point nothing on the boot path is windowed: the vectors are the image's own
(§5), and nothing calls back into the ROM.

The kernel's `_start` does the same in the other order, and through `a2`:
`PS` = `0x0F` first, then `WINDOWSTART` and `WINDOWBASE`. With PS.WOE = 1 and
EXCM = 0, an instruction naming `a4` or above raises a window overflow when
`WINDOWSTART` has a bit in the panes it reaches above `WINDOWBASE` (§7d), and
`a0`..`a3` are never checked. The probe sequence above names `a9` while the
ROM's PS.WOE is still set, which is safe only for a handover state with no
`WINDOWSTART` bit in the two panes above `WINDOWBASE` (the ROM's 5 and `0x2a`
qualify); writing `PS` first through `a2` relies on nothing the ROM leaves.

### 2c. What the ROM leaves configured

- **Console:** UART0 at 115200 (§4). Writing the FIFO register prints with no
  setup.
- **Flash:** mapped as above. Probed: the word at 0x3C00_0400 (DBUS) and at
  0x4200_0400 (IBUS) both read `0xaedb041d`.
- **Interrupt matrix:** source 27 routed to CPU interrupt 5, every other map
  register reading 6 (§6c). The ROM's `Uart_Init` writes it
  (`l32r a2, (600c206c) ; movi.n a3, 5 ; s32i a3, a2, 0` at 0x4004_8B2D).
- **Watchdogs, on the emulator:** `RTC_CNTL_WDTCONFIG0` (0x6000_8098),
  `RTC_CNTL_SWD_CONF` (0x6000_80B4), `TIMG0_WDTCONFIG0` (0x6001_F048) and
  `TIMG1_WDTCONFIG0` (0x6002_0048) all read `0x00000000` at handover. The ROM
  owns a routine, `disable_default_watchdog` (0x4004_3714), that clears
  `RTC_CNTL_WDTCONFIG0` bit 12 and `TIMG0_WDTCONFIG0` bit 14; nothing in the
  ROM calls it (its only reference is the `__call_` export at 0x4000_0714).
  For silicon see §11.
- **The second core:** the monitor's `cpu 1` / `info registers` shows
  `PRID=0000abab` at `PC=40043a3e`, which is ROM `main`'s loop polling
  0x600C_0004 for a non-zero entry address to `callx8`. On the emulator the
  APP CPU runs ROM code and waits there. Silicon differs (§11).

### 2d. `-kernel` boots too, without the ROM

`-kernel image.elf` with no drive: QEMU prints `Warning: both -bios and
-kernel arguments specified. Only loading the the -kernel file.`, loads the
ELF's segments and starts at `e_entry`. The ROM is not loaded, so there is no
ROM banner. A flash-linked ELF (LMA 0x4200_0000) printed nothing, because no
flash is attached and nothing maps one. A RAM-linked ELF (entry 0x4037_8008,
all segments in IRAM/DRAM) ran and printed through UART0 with no ROM
configuration. Its state at entry:

```
a1=0x00000000  ps=0x0000001f  windowbase=0  windowstart=0
vecbase=0x40000000 (no ROM behind it)  prid=0xcdcd
atomctl=0x00000028
```

So a `-kernel` image must set its own stack and `VECBASE` before it can take
any exception, and its own `ATOMCTL` before its first `s32c1i`: `0x28` is one
of the values that fault (§8), and the compiler's word atomics are inline
`s32c1i`. The direct-boot flash image is the form a real part boots, and it
inherits the ROM's `0x15`.

### 2e. The watchdogs `_start` switches off

On the emulator every watchdog reads off at handover (§2c); on silicon the
RTC watchdog, TIMG0's watchdog and the super watchdog are expected live after
a flash boot (§11). `boot.S`'s `_start` therefore, right after it leaves the
ROM's register window and before any copy:

```
RTC watchdog    key 0x50D83AA1 -> 0x6000_80B0 (WDTWPROTECT)
                0          -> 0x6000_8098 (WDTCONFIG0: off, every stage)
                0          -> 0x6000_80B0 (locked again)
TIMG0 watchdog  key 0x50D83AA1 -> 0x6001_F064 (WDTWPROTECT)
                0          -> 0x6001_F048 (WDTCONFIG0: off)
                0          -> 0x6001_F064
super watchdog  key 0x8F1D312A -> 0x6000_80B8 (SWD_WPROTECT)
                SWD_CONF (0x6000_80B4) |= 1 << 31 (auto-feed)
                0          -> 0x6000_80B8
```

Where each number comes from. The registers are the ones Espressif's register
headers for the part name, `soc/rtc_cntl_reg.h` (the RTC watchdog and the
super watchdog) and `soc/timer_group_reg.h` (the TIMG watchdogs), in
ESP-IDF's `components/soc/esp32s3/include/`. Neither header was at hand when
these were written, so each value was read from one of two other sources:

- esptool's ESP32-S3 target (`esptool/targets/esp32s3.py`, version 5.1.0),
  which names the RTC block's base 0x6000_8000, `RTC_CNTL_WDTCONFIG0_REG`
  (+0x98), `RTC_CNTL_WDTWPROTECT_REG` (+0xB0) and its key 0x50D83AA1,
  `RTC_CNTL_SWD_CONF_REG` (+0xB4), its auto-feed bit 31,
  `RTC_CNTL_SWD_WPROTECT_REG` (+0xB8) and its key 0x8F1D312A. Its
  `disable_watchdogs` is the RTC and super-watchdog sequence above: unlock,
  write the config word 0, relock; unlock, set the auto-feed bit, relock.
- The mask ROM's ELF (`esp32s3_rev0_rom.elf`, from Espressif's
  `esp-rom-elfs` 20241011): its symbol `TIMERG0` is 0x6001_F000;
  `disable_default_watchdog` (0x4004_3714) clears bit 14 of 0x6001_F048
  (TIMG0's WDTCONFIG0) and bit 12 of 0x6000_8098 (the RTC's), the two
  flash-boot enables; `wdt_hal_write_protect_disable` (0x4004_7944) writes
  0x50D83AA1 to a TIMG block's +0x64 and to the RTC block's +0xB0;
  `wdt_hal_deinit` (0x4004_7748) clears bit 31 (the enable) of a TIMG
  block's +0x48. Writing the whole TIMG0 config word 0 clears both bits.

TIMG1's watchdog is left alone: the ROM's `disable_default_watchdog` names
only TIMG0's and the RTC's, so nothing suggests the ROM starts it.

Probed on the emulator: the smoke passes with the writes in place, and the
emulator's watchdog registers read 0 before them (§2c). Probed on silicon
(ESP32-S3 rev v0.2, ROM `esp32s3-20210327`): no reset follows in 120 s of
running, nor after a kernel fault (§11). Which of the watchdogs was live
before the writes was not read.

---

## 3. Memory map (monitor `info mtree`, machine `esp32s3`)

```
0x2000_0000-0x201D_4BFF  esp-rgb-vram          NOT SILICON: display extension
0x2100_0000-0x2100_001B  display.esp.rgb       NOT SILICON
0x3C00_0000-0x3DFF_FFFF  esp32s3.dcache        flash DBUS window (32 MiB)
0x3FC8_0000-0x3FDE_FFFF  esp32s3.dram    ram
0x3FF0_0000-0x3FF1_FFFF  esp32s3.drom    rom   alias of irom + 0x40000
0x4000_0000-0x4005_FFFF  esp32s3.irom    rom   mask ROM (one per CPU)
0x4037_0000-0x403E_FFFF  esp32s3.iram    ram
0x4200_0000-0x43FF_FFFF  esp32s3.icache        flash IBUS window
                                               (alias of the dcache window)
0x5000_0000-0x5000_1FFF  esp32s3.rtcslow ram
0x6000_0000-0x600D_0FFF  esp32s3.iomem         peripheral window
0x600F_E000-0x600F_FFFF  esp32s3.rtcfast ram
```

Peripherals inside the window (gaps fall through to `esp32s3.iomem`, which
reads 0 and drops writes, §9):

```
0x6000_0000-0x6000_007B  esp_soc.uart          UART0 (the console)
0x6000_2000-0x6000_23FF  ssi.esp32s3.spi
0x6000_4000-0x6000_4FFF  esp32.gpio
0x6000_7000-0x6000_7203  nvram.esp.efuse
0x6000_8000-0x6000_81FF  misc.esp32s3.rtc_cntl
0x6001_0000-0x6001_007B  esp_soc.uart          UART1
0x6001_F000-0x6001_F0FF  timer.esp.timg        TIMG0
0x6002_0000-0x6002_00FF  timer.esp.timg        TIMG1
0x6002_3000-0x6002_30FF  esp.systimer          SYSTIMER
0x6002_6000-0x6002_63FF  esp32s3.apbctrl  ram
0x6002_B000-0x6002_B1FF  esp32.twai
0x6002_E000-0x6002_E07B  esp_soc.uart          a third UART
0x6003_507C-0x6003_507F  misc.esp32s3.rng
0x6003_8000-0x6003_8083  misc.esp32c3.usb_serial_jtag
0x6003_A000-0x6003_A0BB  misc.esp.aes
0x6003_B000-0x6003_B0FF  misc.esp.sha
0x6003_C000-0x6003_C833  misc.esp.rsa
0x6003_D000-0x6003_DE23  misc.esp.ds
0x6003_E000-0x6003_E0FE  misc.esp.hmac
0x6003_F000-0x6003_F40F  esp32s3.gdma
0x600C_0000-0x600C_00A3  esp32s3.soc.clk
0x600C_1000-0x600C_1FFF  misc.esp32s3.pms
0x600C_2000-0x600C_2FFF  misc.esp32s3.intmatrix
0x600C_4000-0x600C_57FF  esp32s3.icache        cache control
0x600C_C000-0x600C_C05F  misc.esp32s3.xts_aes
0x600C_D000-0x600C_D7FF  open_eth              NOT SILICON
```

`esp32s3.iomux` (0x6000_9000) and `esp32s3.rmt` (0x6001_6000) appear at
priority -1000 and are hidden under `esp32s3.iomem` in the flat view, so
they are not reachable. There is no LEDC device (§9).

### 3a. RAM: two arrays on the emulator, one SRAM on silicon

`esp32s3.dram` and `esp32s3.iram` are two `ram` regions, not an alias.
Probed: `0xA5A5A5A5` stored at DRAM 0x3FC9_0000 and `0x5A5A5A5A` at IRAM
0x4038_0000 each read back their own value. Both arrays also extend past
silicon's ranges: stores at 0x3FD0_0000 and 0x403E_0000 read back.

Silicon's ranges, from esptool's ESP32-S3 target
(`esptool/targets/esp32s3.py`, `MEMORY_MAP`, esptool 5.1.0):

```
[0x3FC88000, 0x3FD00000, "DRAM"]     [0x40370000, 0x403E0000, "IRAM"]
[0x42000000, 0x42800000, "IROM"]     [0x3C000000, 0x3D000000, "DROM"]
[0x50000000, 0x50002000, "RTC_DATA"] [0x600FE000, 0x60100000, "RTC_DRAM"]
```

On silicon most of IRAM and DRAM are one SRAM seen through two buses (the
ESP32-S3 Technical Reference Manual, System and Memory chapter): IRAM
0x4037_8000 + n is DRAM 0x3FC8_8000 + n, an offset of 0x006F_0000
(`hal.IRAM_DRAM_OFFSET`). The emulator does not model that, so a layout
that overlaps an IRAM range with its DRAM twin works here and corrupts
itself there. The stub keeps IRAM at 0x4037_8000 (16 KiB) and DRAM from
0x3FC8_C000, which stay disjoint under either reading of the alias, and
keeps out of 0x4037_0000-0x4037_7FFF, whose use on silicon this file did
not establish.

Probed on silicon (ESP32-S3 rev v0.2, ROM `esp32s3-20210327`), by
`bus_probe` at the bottom and the top word of the process range, in both
directions: a word stored at IRAM 0x403A_0000 and then `0xc3d2e1f0` stored
at DRAM 0x3FCB_0000 reads back `0xc3d2e1f0` at IRAM 0x403A_0000; a word
stored at DRAM 0x3FCE_FFFC and then `0xc3d2e1f0` stored at IRAM
0x403D_FFFC reads back `0xc3d2e1f0` at DRAM 0x3FCE_FFFC. Each pair of
addresses names one word. The same steps here read back the first word
stored (`not shared`). Between those two words the alias is the manual's
claim; 0x4037_0000-0x4037_7FFF stays unprobed.

### 3b. The ROM's RAM

From the ROM ELF's section headers, the ROM's `.bss`/`.data`/stacks occupy
0x3FCD_7E04-0x3FCE_FFFF: `.bss_shared_bufs` starts at 0x3FCD_7E04 and the
last section, `.data.interface.common`, is 4 bytes at 0x3FCE_FFFC. The
handover stack is inside it. 0x3FCF_0000 and above is not the ROM's. An image
that never calls the ROM may reuse the range once off the ROM's stack.

### 3c. What the bus allows

Probed with a recovering kernel vector, one access at a time:

```
load  0x1000_0000 (unmapped)          EXCCAUSE 28 LoadProhibited,
                                      EXCVADDR 0x10000000
store 0x4210_0000 (flash IBUS)        EXCCAUSE 15 LoadStoreAddrError
load  0x4210_0000 (flash IBUS)        ok   (literal loads from XIP code work)
load  0x3C00_0400 (flash DBUS)        ok
store 0x4000_0000 (mask ROM)          ok, no fault
load  32-bit at 0x3FC9_0001           ok   (no alignment exception)
load  16-bit at 0x3FC9_0001           ok
load/store 8-bit at 0x4038_0001 IRAM  ok
call code copied into DRAM 0x3FCA_0000  ok, returned 42
call code copied into IRAM 0x4039_0000  ok, returned 42
```

The last five are emulator generosity that silicon may not share; the table
below says which of them the part shares (§11).
These were probed on an image booted through the ROM, which sets up the
cache and its region attributes first; a `-kernel` image was not probed for
them.

On silicon (ESP32-S3 rev v0.2, ROM `esp32s3-20210327`), from the smoke's
board images: the `bus_probe` stage, which makes each access in kernel mode
and carries on past a refusal (`README.md` has its transcript), booted twice
with byte-identical output, and the accesses the kernel itself makes. A
refusal is EXCCAUSE and EXCVADDR as the probe printed them; every EXCVADDR
was the access's own address. The emulator column is the same probe under
the emulator.

```
access                                  emulator     silicon
load+store 32-bit, IRAM 0x403A_0000     ok           ok
  and 0x403D_FFFC (process range)
the alias, IRAM 0x403A_0000 against     not shared   shared (§3a)
  DRAM 0x3FCB_0000, and DRAM
  0x3FCE_FFFC against IRAM 0x403D_FFFC
load+store 32, 16 and 8-bit, DRAM       ok           ok
  0x3FCB_0000 and 0x3FCE_FFFC
load 32-bit unaligned, DRAM 0x3FCB_0001 ok, the      ok, the same:
                                        bytes at     0xa5963ca5
                                        +1..+4
s32c1i, DRAM 0x3FCB_0010, ATOMCTL 0x15  ok           ok (§8)
load 8-bit, IRAM 0x403A_0021            ok           refused, 3
load 16-bit, IRAM 0x403A_0022           ok           refused, 3
store 8-bit, IRAM 0x403A_0021           ok           refused, 3 (and the
                                                     loader's first `s8i`
                                                     at 0x403A_0000)
store 16-bit, IRAM 0x403A_0022          ok           refused, 3
call code word-stored at IRAM           ok           ok, returned
  0x403A_0100, kernel mode                           0x0000102a
call the same code at DRAM 0x3FCB_0100  ok           refused, 2
load 32-bit, flash IBUS (`l32r`, and    ok           ok
  `bus_probe` at the payload)
load 8-bit, flash IBUS (payload + 1)    ok           refused, 3
load 32 and 8-bit, flash DBUS (region   ok           ok
  table; strings, the loader's image)
store 32-bit, IRAM 0x4037_8000 up       ok           ok, and the vectors
  (the vectors and the trap path)                    copied there run
load and store, DRAM 0x3FC8_C000 up     ok           ok (the kernel runs
  (the kernel's .data, .bss, stack)                  on them)
user mode at word-stored IRAM code      ok           ok
  0x403A_010C: `l32r`, 8-bit store
  and load at DRAM 0x3FCB_0400, syscall
```

Six rows differ, all of them refusals on the part: sub-word loads and stores
at IRAM addresses (cause 3, `LoadStoreError`), an instruction fetch at a
DRAM address (cause 2, `InstructionFetchError`, §5c) and a byte load through
the flash IBUS window (cause 3). The alias row differs too, as §3a says. No
emulator case can show any of them; §11 says what each means.

---

## 4. Console: UART0 at 0x6000_0000

`esp_soc.uart`, the same model as the C3's, at the same base:

```
+0x00  UART_FIFO    write a byte to transmit
+0x14  UART_CLKDIV  0x0030015B after ROM init
+0x1C  UART_STATUS  0x00000000, also immediately after a burst
+0x20  UART_CONF0   0x0400001C after ROM init
```

`UART_CLKDIV` is the same value the C3 file decodes as 40 MHz / 115200, so the
ROM leaves the console configured and a byte store to `UART_FIFO` is the whole
sink. `UART_STATUS` reads 0 right after a burst of several hundred bytes, all
of which arrived: as on the C3 model, the TX FIFO count is never reported and
a poll on it cannot spin here; on silicon the FIFO fills. Under `-kernel`
(no ROM) the same store prints too: the model ignores the divisor.

Probed on silicon (ESP32-S3 rev v0.2, ROM `esp32s3-20210327`): the FIFO
fills. A kernel fault line stored straight after a burst of console lines,
with no wait for room, reached the host as 34 of its bytes; every line
printed through the console's poll arrived whole. So every writer polls
`UART_STATUS`'s TXFIFO_CNT (bits 25:16) against the 128-byte depth before
each store: the console (`lib.saw`) without a bound, and `kernel_fault`
(`sink.c`) with one, so that a UART which never drains cannot keep the
report from reaching its halt.

**USB-Serial-JTAG** (0x6003_8000, `misc.esp32c3.usb_serial_jtag`): probed by
writing `USJ: hello\n` byte by byte to `+0x00` (EP1) and 1 to `+0x04`
(EP1_CONF, write-done). Nothing appeared on UART0's chardev or on three more
`-serial file:` chardevs, and EP1_CONF reads `0x00000000` before and after.
The emulator gives no second console here; on silicon USB-Serial-JTAG is the
dev-board console and its registers are for a real board.

### 4a. The console on a board

**UART0** is the console this kernel drives. The ROM leaves it at 115200
baud (`UART_CLKDIV` above), clocked by the crystal (§14a), and no image
moves that clock or rewrites the divisor the ROM wrote (§14d), so a monitor
at 115200 8N1 on the board's USB-to-UART bridge reads it. The ROM's banner
arrives the same way and is the first check.

**USB-Serial-JTAG** is not driven, and is a written plan. A board whose only
USB port is the chip's native one shows the ROM's banner there (the ROM
prints to both, eFuses permitting) and then nothing from the kernel. The
transmit path, read from the ROM ELF's `usb_uart_device_tx_one_char`
(0x4004_903C) and `usb_uart_device_tx_flush` (0x4004_909C):

```
0x6003_8000  EP1       store a byte to queue it
0x6003_8004  EP1_CONF  bit 1: room for a byte (poll before each store)
                       bit 0: write 1 to send what is queued
```

The ROM bounds its wait for bit 1 (a counted retry with a delay between
reads) and, once a wait runs out, records that no host is reading
(`usb_uart_connected`, 0x3FCE_F130) and stops trying. A kernel sink needs the
same bound, or an unplugged cable hangs the kernel in its first print, and
it needs selecting at link time the way `_sos_semihosting` is (§10), so the
UART path the emulator checks is untouched. Neither is built: nothing here
can run it, and a board with a USB-to-UART bridge does not need it.

---

## 5. Exceptions, vectors, privilege

### 5a. The vector table

Offsets from `VECBASE`, read from the ROM ELF's own table at 0x4000_0000
(`_WindowVectors_text_start` 0x4000_0000, `_Level2Vector` 0x4000_0180, ...):

```
0x000  WindowOverflow4     0x040  WindowUnderflow4
0x080  WindowOverflow8     0x0C0  WindowUnderflow8
0x100  WindowOverflow12    0x140  WindowUnderflow12
0x180  Level2   0x1C0  Level3   0x200  Level4   0x240  Level5
0x280  Debug    0x2C0  NMI
0x300  Kernel   (exception or level-1 interrupt with PS.UM=0)
0x340  User     (exception or level-1 interrupt with PS.UM=1)
0x3C0  Double   (exception with PS.EXCM=1)
reset  0x4000_0400 in the ROM, not relocatable by VECBASE
```

Probed: the stub's table at 0x4037_8400 (`wsr vecbase`) receives `syscall`
at +0x340, level-1 interrupts at +0x340, kernel-mode faults at +0x300,
level-3, -4 and -5 interrupts at +0x1C0, +0x200 and +0x240, and a CPU
interrupt routed to 14 at +0x2C0 (§6c). `VECBASE` accepted `0xFFFFFFFF` and
read it back unmasked; the stub aligns the table to 1 KiB anyway.

What the kernel's table (`kernel/boot.S`) does at each: the six window
vectors spill and fill a windowed thread's frames in its own context (§7d);
the user vector takes the Alloca exception (`EXCCAUSE` 5) in place and
everything else into the trap entry (§7c); the double vector sends a fault a
window handler took for a user thread through the user vector as that
thread's own, and stops the machine for any other (§7d); every other vector
stops the machine through `kernel_fault`.

### 5b. `syscall`

Probed by the stub (§12): from user code (PS.UM=1), `syscall` arrives at the
user vector with `EXCCAUSE = 1`; `EPC1` is the address of the `syscall`
instruction itself, and the PS inside the handler is `0x00000030` (UM=1,
EXCM=1, INTLEVEL=0). The handler returns past it with `EPC1 += 3` and
`rfe`, which clears EXCM. The same instruction with PS.UM=0 arrives at the
kernel vector instead, `EXCCAUSE = 1`.

### 5c. Causes seen

```
0   IllegalInstruction      `ill`
1   Syscall
3   LoadStoreError          s32c1i with ATOMCTL = 0 (§8)
4   Level1Interrupt
15  LoadStoreAddrError      store to the flash IBUS window
20  InstFetchProhibited     `callx0` to 0x1000_0000
28  LoadProhibited          load from 0x1000_0000
32  Coprocessor0Disabled    `wfr f0, a2` with CPENABLE = 0
```

The numbers are what the probes read on the emulator. The names are the
Xtensa ISA's, as esp-idf's `xtensa/corebits.h` spells them; `StoreProhibited`
is 29 there and was not seen.

On silicon (ESP32-S3 rev v0.2, ROM `esp32s3-20210327`), `bus_probe`'s
refusals (§3c):

```
2   InstructionFetchError   `callx0` to code at DRAM 0x3FCB_0100
3   LoadStoreError          8- and 16-bit load and store at IRAM
                            0x403A_0021/0x403A_0022; 8-bit load through
                            the flash IBUS window
```

The name for 2 is the third entry of the ROM's own exception-name strings,
which list the causes in order from 0 (`strings` of the ROM ELF,
`~/.espressif/tools/esp-rom-elfs/20241011/esp32s3_rev0_rom.elf`:
`IllegalInstruction`, `Syscall`, `InstructionFetchError`, `LoadStoreError`,
...). The emulator answers the same DRAM fetch with no exception at all; its
20 above is a fetch from an unmapped address, which no board has made.

### 5d. PS, EXCM and the interrupt levels

- `XCHAL_EXCM_LEVEL` is 3. Probed: a level-3 software interrupt (INTSET bit
  29) stayed pending while PS.EXCM=1, INTLEVEL=0, and was taken as soon as
  EXCM was cleared; a level-4 interrupt (SYSTIMER routed to CPU 24) was taken
  with EXCM=1 (PS in the handler `0x14`), and so was a level-5 one (CCOMPARE2,
  CPU 16). A follow-up level-5 control with EXCM=0 recorded no interrupt in
  one run; that run is unexplained and the EXCM result rests on levels 3 and
  4.
- INTLEVEL masks at and below: a level-3 interrupt with INTLEVEL=3 was not
  taken and with INTLEVEL=2 was (handler PS `0x13`).
- The level table, from the ROM's `Xthal_intlevel` (32 bytes at 0x3FF1_8C00):

```
cpu int  0-10:1  11:3  12:1  13:1  14:7(NMI)  15:3  16:5  17:1  18:1
        19:2  20:2  21:2  22:3  23:3  24:4  25:4  26:5  27:3  28:4  29:3
        30:4  31:5
```

### 5e. Privilege: there is none in the core

- `PS.RING` is not implemented. Probed: writing `PS = 0x000400CF` (RING=3)
  read back `0x0004000F`.
- No instruction is privileged. Probed: with PS.UM=1, `rsr vecbase` and
  `wsr intenable` execute without an exception.
- clang's feature list for `esp32s3` (§1a) has `regprotect` and no feature
  naming an MMU or MPU.

So PS.UM selects a vector, not a privilege: user code can rewrite `PS`,
`VECBASE` and every SR. The first profile is **Flat**: the kernel's checks on
the syscall path are real, memory protection is not. The S3's
memory-protection hardware lies outside the core (`misc.esp32s3.pms` and the
World Controller); this file did not probe whether either could isolate a
user world.

---

## 6. Interrupts and the timer

### 6a. Clocks

Probed: four windows of 16,000,000 SYSTIMER ticks, timestamped on the host as
their lines arrived:

```
MARK 1 systimer+=16000064 ccount+=39998800  host_dt=1.000s
MARK 2 systimer+=16000032 ccount+=40000000  host_dt=1.000s
MARK 3 systimer+=16001120 ccount+=40002840  host_dt=1.000s
```

SYSTIMER advances at **16 MHz** and `CCOUNT` at **40 MHz**, both on host time
(not instruction count). The register offsets in the C3 file's SYSTIMER
section are the ones every probe here used, and they behave as there: `CONF`
reads `0x46000000` at handover, and the `UNIT0_OP` update/valid handshake,
`UNIT0_VALUE_LO`, `TARGET0_HI`/`LO`, `TARGET0_CONF`, `COMP0_LOAD` and
`INT_ENA`/`INT_RAW`/`INT_CLR`/`INT_ST` all work.

### 6b. The core timers

`CCOMPARE0..2` exist and drive CPU interrupts 6, 15 and 16. Probed with
INTENABLE=0: all three set 20,000 cycles ahead raised `INTERRUPT =
0x00018040`; rewriting each comparator cleared it (`0x00000000`). Their levels
are 1, 3 and 5 (§5d). `INTSET` reaches only bits 7 and 29: `INTSET =
0xFFFFFFFF` read back `INTERRUPT = 0x20000080`; those are the two software
interrupts.

### 6c. The interrupt matrix (0x600C_2000)

One map register per source at `+4*source` for the PRO CPU (`+0x800` for the
APP CPU: ROM `intr_matrix_set`, 0x4004_356C, picks 0x600C_2800 when its CPU
argument is non-zero). At handover every PRO-CPU map register reads 6 except
source 27 (UART0), which reads 5. 6 is CCOMPARE0's internal interrupt, so a
source left at 6 reaches nothing.

**SYSTIMER TARGET0 is source 57.** Probed by bisection: route a half of the
source range to CPU 23, arm a one-shot alarm, wait 400,000 cycles, read
`INTERRUPT`; then source 57 alone gave `INTERRUPT = 0x00800000`.

Each CPU interrupt with source 57 routed to it, alarm fired, then
`INT_CLR`, then `INTCLEAR` of that bit (`Y` = pending):

```
cpu int:  0 1 2 3 4 5 8 9 12 13 17 18 19 20 21 23 24 25 26 27 31
          driven, falls with the source           -> level-triggered
cpu int:  10 22 28 30
          driven, stays after the source clears,
          cleared by INTCLEAR                     -> edge-triggered
cpu int:  6 7 11 15 16 29   never driven          -> internal
cpu int:  14                NMI: routing to it took the NMI vector at once
```

In one of two runs of this table CPU 5 read not-driven; in the other it read
like 0..4. An earlier sweep that waited 40,000 cycles per source found no
source at all, where 400,000 found 57. Arm-to-pending latency is not bounded
in CCOUNT on this emulator, so take the interrupt rather than poll with a
short deadline.

**The stub's recipe** (a level-1, level-triggered line, so ticks arrive at
the user vector with `EXCCAUSE = 4`):

```
map[57]    = 1                      SYSTIMER TARGET0 -> CPU int 1
INTCLEAR   = 1<<1 ; INTENABLE = 1<<1
TARGET0 one-shot at now + 160000    10 ms
on the tick: SYSTIMER INT_CLR bit 0 (lowers the line), re-arm
```

Three ticks arrived, each with the interrupted user PC (§12). `PS.INTLEVEL`
is 0 in user mode and the kernel runs at 15, so ticks are only ever taken
from user mode.

**Rerouting does not lower a line.** Probed by the kernel's `external_irq`
smoke case with the mask written as a reroute: UART0 (source 27) routed to CPU
interrupt 2 and asserting (its TX-FIFO-empty interrupt enabled), taken once,
then its map register rewritten to 6. The next trap read

```
INTERRUPT=00000004 map27=00000006
```

and so did every one after it: CPU interrupt 2 stayed pending with no source
routed to it, and the trap repeated forever. The emulator moves a level when
the source changes, not when the route does.

**Why: the installed model.** The emulator here is Espressif's
`esp_develop_9.2.2_20250817`. Its matrix (`hw/xtensa/esp32s3_intc.c` as of
espressif/qemu `e0bfd39`) does two things:

- a write to a map register stores the number and moves no CPU interrupt's
  level;
- a source's level change is sent only to the CPU interrupt the source is
  routed to at that moment.

espressif/qemu `175bab5` ("drive each CPU line as the OR of its sources",
on esp-develop) replaces both: each CPU interrupt's level becomes the OR of
the sources routed to it, recomputed on every level change and every route
write. That is the combinational matrix silicon has. Read from the two
sources; the fixed build has not been run here.

Four probes on the installed build, each a scratch kernel printing
`INTERRUPT` after each step (UART0's level is raised and lowered through its
`INT_ENA`, and its model sends its level again on every register access,
console writes included):

```
UART0 asserting, 27 -> 2, then 27 -> 6         INTERRUPT=00000004
  then UART0 lowered, raised, lowered (27 -> 6) INTERRUPT=00000004
  then 27 -> 2, UART0 low                      INTERRUPT=00000000
UART0 asserting on 3, then 27 -> 6             INTERRUPT=00000008
  then source 5, quiet, -> 3                   INTERRUPT=00000008
SYSTIMER alarm fired with 57 -> 6, then 57 -> 3
  (SYSTIMER sends nothing more)                INTERRUPT=00000000
a HAL that routed to 6 on mask:
  2 freed from asserting 27, given to line 5    irq_poll answered 5
```

So on this build a CPU interrupt left high by a source routed away stays high
until some source routed to it sends a level, and a source already asserting
when it is routed somewhere new does not raise its new CPU interrupt until its
device model sends its level again. UART0's model does on every access;
SYSTIMER's does not.

**What the HAL does about it.** A line is given a CPU interrupt from a table
of nine (`DEVICE_CPU_INTS`: 2, 3, 4, 8, 9, 12, 13, 17, 18) when it is
unmasked, and keeps it, routed, while masked: INTENABLE is the mask, and the
source's level goes on reaching the CPU interrupt it will be enabled on again.
An unmask that finds no entry unassigned takes one back from a masked line
(its source to 6, its assignment cleared), preferring one whose `INTERRUPT`
bit is clear: the HAL cannot tell a line whose Interrupt is gone from one
awaiting its driver's ack, and a quiet entry is one where moving the route
loses nothing and leaves no level behind for its next line. The limit is at
most nine lines unmasked at once (`DEVICE_CPU_INT_COUNT`), never a count of
lines ever bound; the kernel unmasks a line only for a live Interrupt, whose
slab (`MAX_INTERRUPTS`) holds fewer, and for its selftest line.

A take-back happens only once more distinct lines have been unmasked than
there are CPU interrupts free. What it can cost: on silicon nothing, since
the matrix is combinational and a source still asserting raises its new CPU
interrupt at the route write (read from Espressif's fix and the model, not
probed on a part); on this emulator, a masked line that is still bound, still
asserting, and loses its CPU interrupt to a take-back does not fire at its
next unmask unless its device model sends its level again. The smoke's
`irq_takeback` walks the table through each of these steps.

SYSTIMER's periodic mode (`TARGET0_CONF` bit 30 with the period in the low
bits) is accepted (readback `0x40003e80` for period 16000), but a polling
loop counted 3 alarms in 5.5 periods; the stub re-arms a one-shot instead.

### 6d. `waiti` and idle

Probed:

```
waiti 0, EXCM=0, CCOMPARE1 (level 3) armed     woke, interrupt taken
waiti 0, EXCM=0, SYSTIMER on CPU 23 (level 3)  woke, interrupt taken
waiti 4, EXCM=0, level-3 SYSTIMER pending       slept on: woke only when a
                                                level-5 CCOMPARE2 backstop
                                                fired, 0x436f20 cycles later,
                                                with INTERRUPT = 0x00810000
waiti 0, EXCM=1, CCOMPARE1 (level 3)            never woke (run killed)
```

`waiti n` sets INTLEVEL to n and wakes only for an interrupt it will then
take; a masked pending interrupt does not end it. So the C3's idle shape
(sleep with interrupts masked, wake, poll) is not available: an idle loop
either sleeps at a level that lets the tick in and takes it in kernel mode,
or spins reading the `INTERRUPT` SR, which shows pending bits whether or not
they are enabled (§6b, §6c).

---

## 7. The trap frame, and windowed threads

### 7a. What the core has

Probed by reading each register on the emulator (no exception for any):
`SAR`, `LBEG`/`LEND`/`LCOUNT` (the ROM left a live loop at handover),
`SCOMPARE1`, `THREADPTR` (`rur`), `ACCLO`/`ACCHI`/`M0..M3` (MAC16), `BR`
(booleans), `MISC0..3`, `CCOMPARE0..2`. The FPU is coprocessor 0 (`wfr` with
CPENABLE=0 gave cause 32); `rur fcr`/`rur fsr` execute with CPENABLE set.
`esp-clang/.../esp32s3/include/xtensa/config/core-isa.h` gives
`XCHAL_HAVE_WINDOWED 1`, `XCHAL_NUM_AREGS 64`, `XCHAL_HAVE_LOOPS 1`,
`XCHAL_HAVE_FP 1`, `XCHAL_HAVE_DFP 0`.

### 7b. What compiled code touches

The same C sources (shifts of 32 and 64 bits, word and 8-byte atomics,
`__thread`, float compare and select, counted loops, clz, min, sext, 64-bit
multiply) compiled at `-O0`, `-O2` and `-Os` with the §1a flags. Every
special-register access in the output:

```
-O0: rur threadptr ; wsr scompare1     -O2, -Os: wsr scompare1
SAR through ssl/sll/src at every level
```

No `loop`, MAC16, boolean, FP or `ee.*` instruction appears. `THREADPTR` is
read only for a C `__thread` variable. Booleans and FP registers appear only
when `+fp` is left on (`olt.s b0` / `bt b0`). The backend has a
`--disable-xtensa-hwloops` option and `--force-hardware-loops` crashes it
(`Cannot select: intrinsic %llvm.set.loop.iterations`), so no code shape seen
here emits `loop`; that none ever does is not established.

### 7c. The frame

```
F_PC 0 (EPC1)   F_PS 4   F_A0..F_A15 8..68        the window that trapped
F_SAR 72   F_LBEG 76   F_LEND 80   F_LCOUNT 84
F_EXCCAUSE 88   F_EXCVADDR 92   F_SCOMPARE1 96   F_THREADPTR 100
F_WINDOWBASE 104   F_WINDOWSTART 108
F_AR 112..300      48 words: word k is AR[(4 * WINDOWBASE + 16 + k) mod 64],
                   panes WINDOWBASE + 4 to + 15
FRAME_BYTES 304    (76 words; the kernel's per-thread stride stays 512)
```

The minimum a switch must save for a call0 thread with `-fp` is `a0..a15`,
`PC`, `PS`, `SAR` and `SCOMPARE1`; a windowed thread's state is the whole
register file, `WINDOWBASE`, `WINDOWSTART` and PS's window fields. The frame
holds all of it for every thread, whichever convention it runs: the save
and the restore are straight-line and the same for every trap, so the
kernel needs no idea of a thread's convention, and every resume loads all
64 physical registers, so nothing of the kernel's or another thread's stays
readable (the smoke's `register_file_clean` reads all 64 at a thread's
first instruction). `LBEG`/`LEND`/`LCOUNT` are saved because user code built
elsewhere may loop, and `THREADPTR` because it is per-thread by definition.
MAC16, `BR`, `MISC0..3` and the FPU need saving only if a thread is allowed
to use them; with `CPENABLE = 0` an FP instruction traps (cause 32), which is
where a lazy FP switch would hook in. The kernel never writes a user stack.

The entry and exit:

```
user vector:  xsr a1, excsave1         ; a1 = this thread's frame
              s32i a0 -> F_A0 ; EXCCAUSE 5 -> the Alloca path (§7d)
trap entry:   save a2..a15, then excsave1 -> F_A1, epc1, ps, sar,
                lbeg, lend, lcount, exccause, excvaddr, scompare1,
                threadptr, windowbase, windowstart
              wsr lcount <- 0          ; see below
              4 times: mov a13, a1 ; rotw 3 ; a4..a15 -> F_AR + 48 * i
              wsr ps <- 0x0F           ; the kernel, on window WINDOWBASE + 12
              a2 = frame, a3 = exccause, a1 = kernel stack ; callx0 ktrap
resume:       a1 = returned frame ; restore SRs but lcount ; wsr epc1
              wsr ps ; isync ; wsr lcount ; isync
              wsr windowstart ; wsr excsave1 <- frame
              wsr windowbase <- WINDOWBASE + 12 ; rsync ; rsr a1, excsave1
              4 times, top down: a4..a15 <- F_AR + 48 * i ; rotw -3 ;
                mov a1, a13
              restore a0, a2..a15 ; l32i a1, F_A1 ; rfe
```

`EXCSAVE1` holds the running thread's frame address, which makes the restore
path the whole context switch and the first entry to user mode (a frame with
`PS = UM|EXCM` and `rfe`). The kernel runs with PS.UM=0, so its own faults go
to the kernel vector (and with EXCM=1, to the double vector) and are fatal
without a mode witness. `LCOUNT` is zeroed on entry because the zero-overhead
loop is address-triggered: with `LCOUNT` non-zero, kernel code reaching the
user's `LEND` address (shared code in a flat image) would branch to `LBEG`.
For the same reason it is restored last, once the frame's PS has put EXCM
back: the loop-back is taken only with EXCM clear, so no kernel instruction
runs with a live count.

The window state the thread left needs no care on the way in: the trap
arrives with PS.EXCM = 1, which turns every window check off, and the entry
rotates relative to wherever WINDOWBASE is, carrying the frame's address in
`a13` (already saved) across each `rotw 3`, which renames `a12..a15` as
`a0..a3`. The kernel then runs on window WINDOWBASE + 12 with the thread's
`WINDOWSTART` still in the register; with PS.WOE = 0 neither is read. On the
way out the frame's PS (EXCM = 1) is written before WINDOWBASE, so no window
check runs until `rfe`; the frame's address rides the rename in `EXCSAVE1`,
and after each `rotw -3` it is `a13` of the group about to be loaded, so it
moves to `a1` first.

The cost, counted on the assembled code (straight-line, the user vector to
`callx0 ktrap`, and `sos_resume_frame` to `rfe`):

```
                         26-word frame   76-word frame
vector to ktrap                50              112
resume to rfe                  37              100
round trip                     87              212
.text.vectors (IRAM)        1303 B          1762 B
```

Every added instruction but the user vector's cause test is a load, a store,
a `rotw`, a `mov` or a special register access. A window exception costs its
handler, 5 to 14 instructions, and no kernel entry; an Alloca exception about
25 and an underflow handler.

### 7d. Windowed threads

A process may be call0 or windowed (C built with a package's
`target-features = "+windowed"`); the kernel stays call0 and runs both. The
rules the kernel's handlers rest on, as this core and the emulator apply
them. Each was checked against the mask ROM's own handlers at 0x4000_0000,
ESP-IDF v5.4.1's `xtensa_vectors.S` and Espressif QEMU's `win_helper.c`, and
is run by the cases below; the ISA manual itself was not read.

- **Registers.** `aN` is physical AR[(4 * WINDOWBASE + N) mod 64]: 16 panes
  of four. WINDOWSTART has a bit per pane, set where a live frame begins.
- **The overflow check.** With PS.WOE = 1 and PS.EXCM = 0, an instruction
  naming `aN`, N >= 4, raises a window overflow if a WINDOWSTART bit is set
  in panes WINDOWBASE + 1 to + N/4 (rounded down). `a0`..`a3` are never
  checked, and with EXCM = 1 or WOE = 0 nothing is. The vector is
  WindowOverflow4, 8 or 12 by the distance from the frame found to the next
  set bit, its call size. `rfwo` clears the frame's WINDOWSTART bit, returns
  WINDOWBASE to PS.OWB and re-executes the instruction.
- **`entry as, n`** needs PS.WOE = 1 and EXCM = 0 (an IllegalInstruction on
  the emulator otherwise), rotates WINDOWBASE by PS.CALLINC, sets that pane's
  WINDOWSTART bit and writes the caller's `a1` less `n` to the new `a1`.
  `callN` sets CALLINC to N/4 and puts the return address, with N/4 in bits
  31:30, in the caller's `aN`.
- **`retw`.** n = a0[31:30]. It is illegal (IllegalInstruction) when n is 0,
  and when n disagrees with the distance to the nearest live frame behind
  it, one to three panes back, when there is one. A `retw` whose caller is
  live clears the returning frame's WINDOWSTART bit and rotates back by n;
  one whose caller is spilled (its bit clear) raises WindowUnderflow(4n) with
  WINDOWBASE at the caller, and `rfwu` sets the caller's bit, returns
  WINDOWBASE to PS.OWB and re-executes the `retw`.
- **`movsp`** raises the Alloca exception (`EXCCAUSE` 5, through the general
  vector) when WINDOWSTART bits WINDOWBASE - 1 to - 3 are all clear: the
  caller is spilled.
- **A double exception** (an exception with PS.EXCM = 1) goes to VECBASE +
  0x3C0 with DEPC its PC and `EXCCAUSE`/`EXCVADDR` its own, leaving EPC1 and
  PS alone.

**Where a spill goes.** A frame's `a0..a3` go to the 16 bytes below its
callee's stack pointer (the base save area, which the windowed ABI reserves
below every frame's stack pointer). A call8 frame's `a4..a7`, or a call12
frame's `a4..a11`, go to the 16 or 32 bytes below its caller's stack pointer
less 16 (the extra save area, which `entry` allocates at the top of the
frame), and the caller's stack pointer is read from the caller's own base
save area: overflow spills the oldest frame first, so the caller is always
spilled before its callee.

**The handlers.** The six window vectors (§5a) are those sequences, the mask
ROM's instruction for instruction: 5 to 14 word accesses through the
thread's own registers and one `rfwo` or `rfwu`, with PS.EXCM = 1 (so no
interrupt is taken inside one) and no kernel entry. The Alloca exception is
handled from the user vector, also without entering the kernel: the
faulting window's WINDOWBASE and `a0` are kept, the window is rotated one
pane back into the spilled caller's dead registers, PS.OWB is set to the
faulting window, the window is rotated to the caller's base by the call size
in the faulting window's `a0` (0 read as call4), and the underflow handler
for that size fills the caller and returns to the `movsp`.

**The start state.** Every thread starts in one state both conventions run
from (`lib.saw`'s `frame_init`), and no image declares its convention:

```
PS            0x0005_0030   UM, EXCM (rfe clears it), INTLEVEL 0,
                            WOE = 1, CALLINC = 1
WINDOWBASE 0, WINDOWSTART 1
a1            the stack top
a2, a6        the entry argument
a4            0x7FFF_FFFC
every other register 0
```

A call0 entry finds its argument in `a2` and its return address 0, as
before; WINDOWSTART names only its own pane, so nothing it does overflows,
and it executes no `entry`, `callN` or `retw`, so WOE and CALLINC are never
read. A windowed entry that opens with `entry` runs as a function `call4`
reached: its `a2` is the old `a6`, its `a0` the old `a4`, and pane 0 is a
live caller it never returns into, spilled first when the register file
wraps, into the 16 bytes below the entry's stack pointer. That spill stores
the stack top where the entry's own call8 or call12 spill reads its
caller's stack pointer, so its extra save area is the top of its own `entry`
allocation, and nothing is written at or above the stack top. An entry that
returns executes `retw` with `a0` = 0x7FFF_FFFC: a call4 return, an ordinary
one (after an underflow, if pane 0 was spilled), to 0x7FFF_FFFC under the
top bits of a flash or IRAM PC, where nothing is mapped, so the fetch faults
(`instruction-address-error` on the emulator), the windowed form of a call0
entry's return to 0.

**An entry point that does not open with `entry` must make only `call4`
calls from the frame it starts in.** Its first frame is then the pane it
starts in; a call8 or call12 from there makes that pane a call8 or call12
frame, whose spill reads its caller's stack pointer at the stack top less
12, a word nothing wrote, and the process faults at the first wrap of the
register file. A `call4` makes it a call4 frame, whose spill reads nothing.
Every compiled function opens with `entry`, so only hand-written entries are
bound by this, and "no image declares its convention" holds for an entry
that opens with `entry` or keeps to `call4`.

**A fault inside a window handler.** A spill to an address that takes no
store, or a fill from one that answers no load, is a double exception with
DEPC in the six window vectors. With PS.UM = 1 it is the running thread's
bad access to its own stack: the double vector sends it through the user
vector as an ordinary trap, with `EXCCAUSE` and `EXCVADDR` the access's and
EPC1 the user instruction whose window exception it was, and the kernel ends
the process with the access's own reason (`store-access-fault`,
`load-access-fault`). The thread is never resumed, so the double path keeps
none of its registers, in a special register or anywhere a later thread
could read. Any other double exception stops the machine: the same fault
with PS.UM = 0, and a fault outside the window handlers with PS.UM = 1, which
a process that points `EXCSAVE1` at nothing before a trap causes (§5e; the
smoke's `excsave_fault`).

**What a windowed process links.** `user/syscall.c` unchanged, compiled
windowed: its register variables name the registers of the window that
executes `syscall`, which are the frame's `a2..a8`, so the kernel side does
not change. Blade passes the triple's `-windowed` and then the manifest's
`+windowed` to each C unit, the last winning, and the integrated assembler
then accepts `rsr windowbase` and the windowed instructions in the unit's
inline assembly; a `.S` file takes them under either. No Saw is compiled
windowed: the compiler at the pinned release uses a frame pointer that is an
argument register under the windowed ABI.

**The cases** (`make sos-smoke-esp32s3`): `windowed_process` (overflow and
underflow at each call size past the register file, a system call from 30
frames deep, `movsp` under a call8 caller, two threads preempted deep in
chains and between a call and its callee's `entry`), `windowed_alloca` (the
Alloca exception in a first frame and under call4, call8 and call12
callers), `register_file_clean`, `windowed_child_exit` (a windowed child
beside a call0 root), `windowed_entry_returns`, and a spill fault and a fill
fault through each of the six handlers (`windowed_spill_fault4`, `8`, `12`
and `windowed_fill_fault4`, `8`, `12`).

### 7e. The call0 calling convention

Read from the compiler's output for a set of C functions at `-O2` with the
§1a flags:

```
a0        return address, set by `callx0`; a function that calls saves it
a1        stack pointer; frames seen were 32 and 48 bytes: 16-byte aligned
a2..a7    the first six argument words; a 64-bit argument takes two,
          low word first
stack     the seventh and later argument words, at a1+0, a1+4, ... on entry
a2..a5    a result of up to four words: a 32-bit value in a2, a 64-bit
          value in a2:a3 (low:high), a struct of two, three or four words
          in a2, a3, a4, a5 in field order
a2        on entry, for a struct result of five words or more: the address
          to write it to; the first argument then arrives in a3
a8..a11   scratch: used without saving
a12..a15  callee-saved: a function that keeps four values across calls
          stores all four in its frame and restores them
```

A trap frame saves all sixteen regardless (§7c), since a trap can land
anywhere. What the convention fixes for the HAL is the hand-written edges:
the trap entry's call into `ktrap` passes its arguments in `a2` and `a3` and
reads the returned frame from `a2`, and a syscall stub that is an ordinary
call0 function receives its arguments in `a2..a7`.

### 7f. Interrupts are masked in the kernel

The kernel never takes an interrupt, whatever is armed and whenever it comes
due. Every interrupt it enables is a level-1 CPU interrupt (§6c), and every
instruction it executes runs with level 1 masked:

- **From the ROM to `_start`'s `wsr ps`.** The handover PS is `0x00060320`,
  INTLEVEL 0, but INTENABLE is 0 (§2b), so nothing can be taken. The first
  instructions of `_start` write PS = `0x0F` (INTLEVEL 15, EXCM 0, UM 0,
  WOE 0) before leaving the register window (§2b); `intc_init`, which is
  what first sets an INTENABLE bit, runs much later, from `kmain`.
- **The whole kernel.** Nothing in the kernel writes PS apart from the trap
  entry, the restore path and the Alloca path, all in `boot.S`; no C leaf in
  `sink.c` touches it. So PS stays `0x0F` through `kmain`, the loader,
  `start_tick` and every `ktrap`.
- **The trap entry.** A trap arrives with PS.EXCM = 1, which masks levels up
  to `XCHAL_EXCM_LEVEL` (3, §5d), so the save sequence is not interruptible;
  it then writes PS = `0x0F` before calling `ktrap`.
- **The window handlers and the Alloca path** run with PS.EXCM = 1 from the
  exception to their `rfwo` or `rfwu`; the Alloca path writes PS.OWB and
  keeps EXCM. A tick due meanwhile is taken at the instruction they return
  to, from a consistent window state.
- **The restore path, and the first entry to user mode.** `sos_resume_frame`
  writes the frame's PS, which carries EXCM = 1 (a saved PS always does, and
  `frame_init` builds `0x0005_0030`), so level 1 stays masked until `rfe`
  clears EXCM in the same instruction that enters user mode at INTLEVEL 0.

Probed by a scratch HAL that prints PS at `kmain` and at `resume_frame` (in
`start_process`, after `start_tick`, immediately before the first entry to
user mode), in a scratch kernel that arms a 1 ms tick in `kmain` before the
root image is loaded, booting the `map_into_child` images (the largest root
image, with a child and a pool):

```
PROBE kmain ps=0x0000000f
PROBE resume_frame ps=0x0000000f intenable=0x00000002 interrupt=0x00000000
...
PROBE resume_frame ps=0x0000000f intenable=0x00000002 interrupt=0x00000002
```

The second `resume_frame` line is the tick pending and enabled (CPU interrupt
1) at PS = `0x0F`, not taken. Booted 30 times: 30 clean ends, no kernel
fault. The control is the same build with the kernel's PS written as `0x00`
(INTLEVEL 0): 30 boots, 30 kernel faults, each
`SOS-S3: kernel fault vector=0x00000300 exccause=0x00000004`, a level-1
interrupt taken at the kernel vector. So the masking is what keeps the kernel
clean, not the timing of the load.

Unlike the ESP32-C3's emulator, this one delivers the matrix interrupt to
user mode (the smoke's `timer_tick` takes it at the payload's PC), so the
scheduler tick preempts a process here: the smoke's `tick_spinner` case
reaches root's Timer wake past a child that never traps, and runs into the
harness deadline with the image's "no tick" flag set.

---

## 8. Word atomics: S32C1I and ATOMCTL

`s32c1i` stores its register only if the memory word equals `SCOMPARE1`, and
returns the old word either way. Probed by the stub on DRAM:

```
STUB: cas(5->7) on 5: old=5 now=7
STUB: cas(5->9) on 7: old=7 now=7
```

`ATOMCTL` is `0x15` at handover (the ROM's value). Probed with each setting,
on DRAM 0x3FC9_0000 and IRAM 0x4038_0000, the two alike:

```
ATOMCTL 0x00 0x04 0x10 0x28 0x3C   s32c1i -> EXCCAUSE 3 LoadStoreError
ATOMCTL 0x01 0x02 0x03 0x15 0x2A 0x3F   old=5 now=7
```

The emulator reads bits [1:0] only: `s32c1i` faults when they are zero and
works otherwise, whatever the other fields hold. Without the ROM the register
starts at `0x28`, a faulting value (§2d). Keep the ROM's `0x15`.

On silicon (ESP32-S3 rev v0.2, ROM `esp32s3-20210327`): `ATOMCTL` reads
`0x15` after the ROM, and `s32c1i` works at a DRAM address under it.
`bus_probe` stores 5 at DRAM 0x3FCB_0010, swaps it for 7 with `s32c1i`
(the old word comes back as 5) and loads 7 back. So word atomics at DRAM
addresses need no `ATOMCTL` of the kernel's. `s32c1i` at an IRAM address,
and the other `ATOMCTL` values, are unprobed on silicon.

---

## 9. GPIO, LEDC, the rest

**GPIO** (`esp32.gpio`, 0x6000_4000) models one register. Probed:
`GPIO_STRAP` (`+0x38`, esptool's `GPIO_STRAP_REG = 0x60004038`) reads
`0x00000004`. Every other register tried reads 0 before and after a write:
`ENABLE` (`+0x20`), `OUT` (`+0x04`), `OUT_W1TS`/`OUT_W1TC`, `IN`/`IN1`
(`+0x3C`/`+0x40`), `PIN2` with a rising-edge interrupt enabled (`+0x7C`),
`STATUS` (`+0x44`, still 0 after toggling the output), `FUNC2_OUT_SEL`
(`+0x55C`). So an output write goes nowhere, an input reads 0, and no GPIO
interrupt is raised. An LED cannot be observed under this emulator.

**LEDC** (silicon's PWM, 0x6001_9000) has no device: the address falls in
`esp32s3.iomem`, `LEDC_CONF` (`+0xD0`) and `CH0_CONF0` (`+0x00`) drop writes
and read 0, and `-d unimp,guest_errors` logs nothing for them. RMT is hidden
the same way (§3). No PWM device exists here.

---

## 10. Stopping the emulator

The ESP32-S3 has no finisher device. Xtensa semihosting gives the guest one
door. Probed:

```
simcall with a2 = 1 (SYS_exit), a3 = 3
  QEMU run with -semihosting       -> QEMU exits, status 3
  QEMU run without it              -> stderr "SIMCALL but semihosting is
                                      disabled"; the instruction returns,
                                      a2 unchanged, the guest runs on
```

So a harness can read the verdict from the exit status when it passes
`-semihosting`, and must still read the transcript, and kill QEMU on a
deadline, when the guest never gets that far. On silicon `simcall` without a
debugger attached is not probed; the stub uses it only at the end.

What `simcall` does on a part with no debugger attached is not in any source
on this machine, so a board image never executes it. `sos_sim_exit` reads
the linker constant `_sos_semihosting`: `esp32s3.ld` provides 1, and the
runner's `--emit-flash` links a board image with 0, which turns the call into
a return. Every stop then ends in its own quiet loop after its line
(`SOS-S3: halt pass`, `halt fail code=...`, `kernel fault ...`), with the
watchdogs already off (§2e), so a board halts where it stopped and does not
reset. Probed by booting an emitted board image under the emulator: the
transcript ends with the halt line and QEMU runs on until it is killed.

---

## 11. Where silicon differs, and where it may

Each entry says what the emulator does, what silicon does or may do instead,
and what the tree is prepared with. What a part showed is marked
*On silicon* and names the board: an ESP32-S3 rev v0.2 (QFN56, 40 MHz
crystal) whose ROM is `esp32s3-20210327`, its console UART0 through the
board's USB-to-UART bridge, running the smoke's board images in
`README.md`'s order. What no part has shown yet is marked *Unprobed on
silicon*, and "prepared for" never means probed. `README.md`'s "First run on
a board" is the order to find out in.

- **Watchdogs.** The emulator leaves every watchdog config register 0
  (§2c). The ROM's flash path never calls its own
  `disable_default_watchdog` (RTC_CNTL_WDTCONFIG0 bit 12, TIMG0_WDTCONFIG0
  bit 14), and esptool's S3 target disables the RTC watchdog and sets the
  super-watchdog auto-feed (`RTC_CNTL_SWD_CONF |= 1<<31` under key
  `0x8F1D312A`, `esptool/targets/esp32s3.py`) when the chip is attached
  through USB-Serial-JTAG, with the comment "the RTC WDT and SWD watchdog are
  not reset". A kernel on silicon should expect the RTC watchdog, TIMG0's
  watchdog and the super watchdog to be live after direct boot.
  *Prepared for:* `_start` switches off the RTC and TIMG0 watchdogs and sets
  the super watchdog to feed itself before anything else (§2e).
  *On silicon (rev v0.2, ROM `esp32s3-20210327`):* no reset in 120 s of
  running `timer_tick`, and none after a kernel fault: each capture of a
  faulting stage shows one `ESP-ROM:` banner and then silence. Which
  watchdogs were live before `_start`'s writes was not read.
  *Unprobed on silicon:* the XTAL32K watchdog and the brownout reset; the
  kernel writes neither.
- **Direct boot can be fused off.** The ROM skips it when secure boot is on or
  eFuse 0x6000_703C bit 1 is set (§2a). The emulator's eFuse has it clear.
  *Prepared for:* the README's first step reads `DIS_DIRECT_BOOT` (the
  espefuse name of that bit, in espefuse's ESP32-S3 eFuse table,
  `espefuse/efuse_defs/esp32s3.yaml`), `SECURE_BOOT_EN` and
  `SPI_BOOT_CRYPT_CNT` (flash encryption, which would hand the ROM
  ciphertext) with a read-only command.
  *On silicon (rev v0.2, ROM `esp32s3-20210327`):* the three read `False`,
  `False` and `Disable`, and the ROM direct-boots the image (the magic at
  0x400, the entry at 0x408) and prints no loader lines of its own.
- **Clock.** CCOUNT runs at 40 MHz here whatever the clock registers say,
  and SYSCLK_CONF reads one fixed word that names the PLL (§14e); what the
  ROM leaves the CPU clock at on silicon is unprobed. SYSTIMER's 16 MHz is
  the clock to time with. *Prepared for:* the kernel times only with
  SYSTIMER and the UART divisor is the ROM's. An image at the default clock
  key, `rom`, changes no clock; one at `xtal40` sets the crystal's
  pre-divider to 1 and prints the CPU's speed measured before and after
  (§14). *On silicon (rev v0.2, ROM `esp32s3-20210327`):* code runs far
  slower than here: `timer_tick`'s payload spins a fixed count of
  instructions, which takes on the order of half a minute on the part and
  well under a second here. Ticks and the UART are unaffected. The CPU clock
  itself was not read; §14h is where the board run records it.
- **SRAM.** IRAM/DRAM are separate arrays here and one SRAM through two buses
  on silicon; both arrays here are also larger than silicon's (§3a).
  *Prepared for:* no range the image uses is the twin of another (§13);
  `hal.SHARED_IRAM_BASE`, `SHARED_IRAM_TOP` and `IRAM_DRAM_OFFSET` name the
  alias. *On silicon (rev v0.2, ROM `esp32s3-20210327`):* a word stored
  through one name reads back through the other, both ways, at the bottom
  and the top word of the process range (§3a); `bus_probe` prints `shared`
  where the emulator prints `not shared`. *Unprobed on silicon:* the words
  between those two, which rest on the manual, and 0x4037_0000-0x4037_7FFF.
- **Access rules the emulator forgives** (§3c): unaligned loads, byte access
  to IRAM addresses, executing from DRAM addresses, data loads from the flash
  IBUS window (which every `l32r` from XIP code is), stores to the mask ROM.
  esptool's S3 memory map lists only DRAM, 0x3FC8_8000 - 0x3FD0_0000, as
  `BYTE_ACCESSIBLE` (`MEMORY_MAP` in `esptool/targets/esp32s3.py`), and its
  own S3 RAM stub keeps its code at IRAM 0x4037_8000 and its data and
  `.bss` at DRAM 0x3FCA_0000 and above
  (`esptool/targets/stub_flasher/1/esp32s3.json`).
  *Prepared for:* a root or child image is read through the data window
  (`esp32s3.ld` puts each in a flash slot linked at its data-window
  address), as are `.rodata` and the region table; the kernel's own data and
  stack are DRAM; the vectors are copied into IRAM with word stores only.
  Literal loads stay in IBUS, beside their code. A process's code is used in
  place in IBUS and its data is at DRAM addresses (§13).
  *On silicon (rev v0.2, ROM `esp32s3-20210327`):* §3c's second table. Of
  the five, the part allows one, refuses two, splits one by width, and was
  not asked the last:
  - unaligned word loads at DRAM addresses work, as here, and read the four
    bytes at the address;
  - 8- and 16-bit loads and stores at IRAM addresses are refused with
    `LoadStoreError` (cause 3) at the access's own address; word loads and
    stores there work, and code stored there by words runs;
  - an instruction fetch at a DRAM address is refused with
    `InstructionFetchError` (cause 2), so code runs only at its IRAM name;
  - a word load through the flash IBUS window works, so `l32r` from XIP code
    does, and an 8-bit load through it is refused (cause 3); 8- and 32-bit
    loads through the data window work.
  *Unprobed on silicon:* stores to the mask ROM; nothing in the tree makes
  one. The sub-word refusals were seen in kernel mode; user mode was asked
  only for word loads at IRAM and byte access at DRAM, which work.
- **ATOMCTL.** Only bits [1:0] matter here (§8). *Prepared for:* the kernel
  leaves the value the ROM hands over (`0x15` here, §2b) and writes none of
  its own. The compiler's word atomics are inline `s32c1i` (a process's
  `debug_print` executes one on its string), so a `LoadStoreError` (cause 3)
  whose EPC1 is an `s32c1i` would mean silicon wants another value for
  internal SRAM. *On silicon (rev v0.2, ROM `esp32s3-20210327`):* ATOMCTL
  reads `0x15` and `s32c1i` works at a DRAM address under it (§8).
  *Unprobed on silicon:* `s32c1i` at an IRAM address, and other ATOMCTL
  values.
- **PIE** (`ee.*`): `ee.zero.q q0` executed here with `CPENABLE = 0`, no
  exception. esp-idf's S3 `tie.h` makes PIE coprocessor 3 (`cop_ai`,
  `XCHAL_CP_MASK` 0x09), so expect `CPENABLE` bit 3 to gate it on silicon.
  Nothing this tree builds emits a PIE instruction.
- **The CPU's configuration ID.** `CONFIGID1` reads `0x23090f1f` here, which
  is `XCHAL_HW_CONFIGID1` in esp-idf's S3 `core-isa.h`. The ROM ELF's
  `xthals_hw_configid1` is `0x23086d67`, the configuration that ROM was built
  against (`CONFIGID0` agrees: `0xc2f0fffe` both). Nothing reads it.
- **User mode, `syscall` and the timer interrupt.** Here a `syscall` and a
  level-1 interrupt taken in user mode arrive at the user vector (§5b, §7f),
  and SYSTIMER TARGET0 reaches CPU interrupt 1 through the matrix (§6c).
  *On silicon (rev v0.2, ROM `esp32s3-20210327`):* the same: `umode_syscall`
  and `timer_tick` pass, so the first entry to user mode, the vector table in
  IRAM, the frame save and restore, the matrix route and execution in place
  from the IBUS window all hold on the part. `bus_probe`'s last step enters
  user mode at code stored by words into process IRAM, which loads a literal
  with `l32r`, stores and loads a byte at a DRAM address and makes system
  calls to the end.
- **Rerouting a source.** Here a CPU interrupt stays pending after its
  asserting source is routed away, and a route write raises nothing (§6c).
  Silicon's matrix is combinational, as Espressif's fixed model is, but that
  is read, not probed. The HAL moves a route only on a take-back, and is
  correct under either answer except for what §6c says a take-back can cost
  here.
- **Process memory** (§13). A process's code and read-only data are used in
  place in flash, the code at its instruction-window address and the
  read-only data at its data-window address, and its writable segments,
  arena and stack are at DRAM addresses. Nothing of a process sits at an
  IRAM address, and the loader writes nothing but DRAM. The emulator serves
  every access this needs, and the byte loads in the instruction window it
  must never make (§3c).
  *On silicon (rev v0.2, ROM `esp32s3-20210327`):* with every process
  segment at IRAM addresses, the loader's first byte store into root's
  region was refused: `root_server_boot` stopped with a kernel fault, cause
  3, EXCVADDR 0x403a0000, EPC1 the `s8i` in `kcore.mem.copy_bytes`.
  *On silicon (rev v0.2, ROM `esp32s3-20210327`), with the layout above:*
  `root_server_boot` and `image_held` run to their end, one ROM banner and
  no reset in each capture. Root's code is fetched in user mode from its
  flash slot, `l32r` loads its literals there, and its string literal is
  read in user mode a byte at a time through the data window; a child's code
  runs in place from the child's slot, twice. The layout also rests on
  accesses `bus_probe` showed: every access width, unaligned loads and
  `s32c1i` at DRAM addresses of the process range (§3c).
  *Unprobed on silicon:* that a process's own byte load in the instruction
  window is refused as the kernel's is; nothing in this layout makes one,
  and the core has no privilege that could tell the modes apart.
- **Flash past the first pages** (§13). This layout reads root's image at
  flash 0x10_0000, a child's at 0x18_0000 and the kernel's read-only data at
  0x20_0000, through both windows, so the kernel's first string is a byte
  load at 0x3C20_0000. The ROM's direct boot maps 0x200 pages of 64 KiB at
  each window before it enters the image, as read from its code (§2a), which
  covers every one of them; the emulator serves them all, which every smoke
  case shows. A board image is a little over 2 MiB (`--emit-flash` prints
  its size). *On silicon (rev v0.2, ROM `esp32s3-20210327`, 8 MiB of flash
  by `flash-id`):* the README's six stages run to their end with this
  layout, so the part serves the kernel's strings at 0x20_0000 through the
  data window (`boot`), root's slot at 0x10_0000 through both windows
  (`root_server_boot`) and the child's at 0x18_0000 (`image_held`).
- **The interrupt matrix reset value.** Unrouted map registers read 6 here,
  with the ROM or without it (a `-kernel` image reads 6 at sources 0 and 27),
  so 6 is the emulator's reset value and not something the ROM writes.
  `intc_init` writes every map register itself, so the value does not matter.
- **The second core.** Here the APP CPU runs ROM code and polls for an entry
  address (§2c). On silicon core 1 leaves reset held in reset with its clock
  gated (esp-idf's `soc/system_reg.h`: `SYSTEM_CONTROL_CORE_1_RESETING`
  defaults to 1 and `SYSTEM_CONTROL_CORE_1_CLKGATE_EN` to 0), so an entry
  address alone does not start it. The kernel never starts it; a core held in
  reset touches nothing.
- **`VECBASE`** read back `0xFFFFFFFF` unmasked here (§5a). The table is 1 KiB
  aligned, which is what the ISA asks, so a masked register reads the same.
  *On silicon (rev v0.2, ROM `esp32s3-20210327`):* the table at
  0x4037_8400 receives `syscall` and level-1 interrupts from user mode.
- **`waiti`.** The kernel never executes it (§6d): idle is a poll loop, so
  whether a part's `waiti` wakes the way the emulator's does cannot matter
  for a first boot.
- **The ROM banner.** Its third line carries the reset reason and the strap
  word (`boot:0x4` here, where `GPIO_STRAP` reads 4), so it differs on a
  board strapped otherwise. *On silicon (rev v0.2, ROM
  `esp32s3-20210327`):* `rst:0x1 (POWERON),boot:0x8 (SPI_FAST_FLASH_BOOT)`
  after power-on; the first two lines read as they do here.
- **Consoles.** UART0's FIFO never fills here (§4); USB-Serial-JTAG prints
  nothing here. *Prepared for:* UART0 at the ROM's 115200 (§4a); every
  writer polls for FIFO room before each store. USB-Serial-JTAG is a written
  plan (§4a). *On silicon (rev v0.2, ROM `esp32s3-20210327`):* UART0 through
  the board's USB-to-UART bridge prints every kernel line at the ROM's
  divisor with no clock setup of ours, and the FIFO fills (§4). A kernel
  fault a scratch image took printed its whole line, 111 bytes, through
  `kernel_fault`'s wait for FIFO room, on one boot. *Unprobed on silicon:*
  USB-Serial-JTAG.
- **`simcall`.** Answered here under `-semihosting`; unknown with no debugger.
  *Prepared for:* a board image never executes it (§10). *On silicon (rev
  v0.2, ROM `esp32s3-20210327`):* every stop seen ends in its quiet loop:
  `boot`, `timer_tick` and `bus_probe` stay on their halt line, and a kernel
  fault stays on its fault line.
- **GPIO and LEDC** are absent here (§9): the first LED is a real-board test.

---

## 12. The stub's transcript

Booted from a flash image as §1e describes, with `-semihosting`:

```
ESP-ROM:esp32s3-20210327
Build:Mar 27 2021
rst:0x1 (POWERON),boot:0x4 (SPI_FLASH_BOOT)
STUB: up on xtensa (ESP32-S3), call0
STUB: rom handover ps=0x00060320 atomctl=0x00000015 vecbase=0x40378400
STUB: cas(5->7) on 5: old=5 now=7
STUB: cas(5->9) on 7: old=7 now=7
STUB: entering user mode
STUB: syscall 1 from pc=0x42001c82 ps=0x00000030
STUB: user back from syscall, a2=0x0000600d
STUB: syscall 2 from pc=0x42001dfd ps=0x00000030
STUB: tick 1 pc=0x42001e03
STUB: tick 2 pc=0x42001e03
STUB: tick 3 pc=0x42001e03
STUB: syscall 3 from pc=0x42001e0d ps=0x00000030
STUB: halt pass
--- qemu: exit status 0
```

The `pc` values are link addresses and move with the code. The ROM's first
two lines are fixed text on this emulator and its third carries the strap
word (§11). On the handover line `ps` and `atomctl` are the values
the ROM left; `vecbase` is the stub's own table, printed after the stub
installed it (the ROM's is in §2b). Without `-semihosting` the transcript is
the same and the harness kills QEMU after `STUB: halt pass`.

---

## 13. Processes

Not a probe of the emulator so much as the layout the HAL chose over §3, and
what the kernel's own smoke cases showed about it (`make
sos-smoke-esp32s3`, the process cases in `tools/sos_runner.py`).

### 13a. The layout

A process's code and read-only data are used in place in flash, and only
what it writes takes RAM (spec §5.6):

```
flash (offset, instruction window, data window)
0x00_0000  0x4200_0000  0x3C00_0000  the kernel: magic, entry, code, the
                                     load images of the vectors and .data
0x10_0000  0x4210_0000  0x3C10_0000  root's image slot (512 KiB)
0x18_0000  0x4218_0000  0x3C18_0000  a child's image slot (512 KiB)
0x20_0000  0x4220_0000  0x3C20_0000  the kernel's .rodata, the region table

RAM
IRAM 0x4037_8000 - 0x4037_BFFF  kernel vectors + trap path
DRAM 0x3FC8_C000 - 0x3FCA_FFFF  kernel .data + .bss (arena, 64K stack)
DRAM 0x3FCB_0000 - 0x3FCC_7FFF  root's region: writable data + arena, the
                                top 16 KiB its stack (`user/root.ld`)
DRAM 0x3FCC_8000 - 0x3FCD_FFFF  a child's region, the same shape
                                (`user/child.ld`)
DRAM 0x3FCE_0000 - 0x3FCE_FFFF  the RAM pool a case may ask for
```

`kernel/esp32s3.ld` places each part and fails the link if the kernel's code
reaches root's slot or an image overruns its slot. The kernel holds every
image by its data-window address: the payload bounds, a region table's blob
rows, and so every read the loader makes of a header, a record or a
segment's bytes. Its own reads of flash are all through the data window, so
it makes none of the byte loads in the instruction window that a part
refuses (§3c).

Each image is a Blade-built sosimg of three segments: a 24-byte header, three
24-byte records, then the segments' bytes in order on 4-byte boundaries.
`user/root.ld` and `user/child.ld` link to that packing:

```
slot + 0x60          code and literal pools, R+X: linked at the slot's
                     instruction-window address plus 0x60, used in place
right behind it      read-only data, R: linked at the data-window address of
                     its own bytes, used in place
the region's base    .data, .bss, the arena, R+W: DRAM, copied
the region's top     the stack the kernel grants, 16 KiB, DRAM
```

The build holds the link addresses to the bytes three times: the scripts'
`ASSERT`s (the code at the slot plus 0x60, the read-only data right behind
it, each of the three sections non-empty, since an empty one would take a
record out of the table), the runner, which reads each image back out of the
flash image it built and refuses the build when a segment is not linked at
its own bytes (`check_in_place` in `tools/sos_runner.py`, which names both
addresses), and the loader, which uses a segment in place only when
`hal.same_bytes` says its link address names its bytes and would copy every
other one, but on this board refuses any segment it would copy to an address
outside process RAM (`hal.segment_bus_ok`): an executable segment not in
place, and a read-only or writable one linked in flash but not at its own
bytes.

No range above is the DRAM twin of another (§3a's alias, IRAM + n against
DRAM 0x3FC8_8000 + n). The process regions include the ROM's RAM (§3b):
nothing writes there until the kernel loads a process, long after the boot
path has left the ROM's stack. Nothing of a process sits at an IRAM address.

A board image (`--emit-flash`) is the flash image cut after its last placed
byte, which is the kernel's read-only data at 0x20_0000, so it is a little
over 2 MiB whatever the case; the gaps between the parts are 0xFF, which is
erased flash.

### 13b. What the cases showed

- The loader reads a root image and a child's through the data window, uses
  their code and read-only data in place, and copies only their writable
  segments into DRAM. Root reports an `entry` in the instruction window on
  its load line.
- A process's code runs from the flash instruction window in user mode,
  `l32r` from its literal pools included, and its byte loads of string
  literals go through the data window. Under the emulator its stack, data
  and arena at DRAM addresses take loads and stores of every width,
  `s32c1i` included (the inline word atomics, §8), as the part's do (§3c).
- A system call's buffer in a process's code, in the instruction window, is
  refused without the kernel touching it, and a string literal used in place
  in the data window is a source (`copy_from_code_refused`). A thread's entry
  is judged by the rows that permit execute, with the code outside the
  process's region (`thread_entry_not_exec`).
- A child runs out of its image in flash after its launcher released the
  image's handle, and the image's bytes go back to their pool only at the
  child's teardown (`image_held`). A region over flash is refused as a
  destination and as a slab donation (`flash_region`).
- A child's image allocation in flash, released before the child runs, is
  held by the child's code row and comes back after the child's death, and
  donating it while a second child's row grants it meets the region rule
  first (`image_rows_held_flash`). A program's initialized writable data is
  copied out of flash into DRAM with its values (`data_init`).
- The loader refuses an image whose code is linked in DRAM, whose code is in
  the instruction window but not at its own bytes, whose code is in place at
  its data-window address, whose read-only data is in place at its
  instruction-window address, with a writable segment linked in flash, or
  whose grant would run past the image; the build refuses a root image linked
  for the child's slot (the smoke's `code_in_dram`, `code_not_in_place`,
  `code_in_dbus`, `rodata_in_ibus`, `writable_in_flash`, `grant_past_image`,
  `slot_mismatch`). The emulator fetched at the data window and served the
  byte-addressed data at the instruction window when a mutant let those two
  images load, so only the loader keeps them off a part.
- Two processes run at two link addresses in one address space, and a child
  started from a blob runs at the destination its region-table row names.
- Two threads of one process, preempted by the tick in the middle of a spin
  that holds a0, a2..a13, `SAR`, `SCOMPARE1`, `THREADPTR`, `LBEG`, `LEND`,
  `LCOUNT` (non-zero, with `LEND` at an address never executed) and, by
  turns, a14 or a15 as the spin's counter, find every one as they left it
  (`frame_hold`, `tests/frame-hold/hold.c`).
- A process binds and releases more distinct lines than there are device CPU
  interrupts (§6c) and goes on binding; the slab's `NoResource` is the limit
  it meets (`irq_line_cycle`).

The emulator serves a byte load in the instruction window, which a part
refuses, so the smoke cannot show that nothing makes one; the layout keeps
every byte load out of that window by construction (§11). On the rev v0.2
board (ROM `esp32s3-20210327`) `root_server_boot` and `image_held` run to
their end with this layout (§11, `README.md`).

---

## 14. The system clock

What makes the CPU's clock on this chip, what the ROM leaves, the sequence
ESP-IDF runs to change it, and what the kernel takes of that sequence. Every
register and value below is from ESP-IDF at tag v5.4.1 or from the ROM ELF
(`esp32s3_rev0_rom.elf`, §2a), each cited by file and function; "IDF" below
is `components/` of that tag.

### 14a. The fields, and what the ROM leaves

```
SYSTEM_SYSCLK_CONF_REG  0x600C_0060   IDF soc/esp32s3/register/soc/system_reg.h
  SOC_CLK_SEL  [11:10]  R/W  reset 0   0 the crystal, 1 the PLL, 2 RC_FAST
                                       (hal/esp32s3/include/hal/clk_tree_ll.h,
                                       clk_ll_cpu_set_src)
  PRE_DIV_CNT  [9:0]    R/W  reset 1   the crystal's divider less one
                                       (clk_ll_cpu_set_divider)
  CLK_XTAL_FREQ [18:12] RO             the crystal in MHz
  CLK_DIV_EN   [19]     RO   reset 0 in the header; reads 1 on the part
                                       (§14h)
SYSTEM_CPU_PER_CONF_REG 0x600C_0010
  PLL_FREQ_SEL [2]      R/W  reset 1   0 the PLL at 320 MHz, 1 at 480 MHz
  CPUPERIOD_SEL [1:0]   R/W  reset 0   the PLL's division for the CPU:
                                       0 80 MHz, 1 160, 2 240
                                       (clk_ll_cpu_set_freq_mhz_from_pll)
SPI_MEM_CORE_CLK_SEL_REG(0) 0x6000_30EC [1:0] reset 0
                                       on the PLL at 480 MHz: 0 80 MHz,
                                       1 120, 2 160, 3 240 (spi_mem_reg.h);
                                       nothing says what it is on the crystal
UART0 UART_CLK_CONF_REG 0x6000_0078   soc/esp32s3/register/soc/uart_reg.h
  SCLK_SEL     [21:20]  R/W  reset 3   1 APB, 2 RC_FAST, 3 the crystal
  SCLK_DIV_NUM [19:12]  R/W  reset 1
```

APB follows the CPU on the crystal and is 80 MHz on the PLL:
`rtc_clk_cpu_freq_to_xtal` calls `rtc_clk_apb_freq_update(cpu_freq * MHZ)`
and `rtc_clk_cpu_freq_to_pll_mhz` calls it with `80 * MHZ`
(esp_hw_support/port/esp32s3/rtc_clk.c). SYSTIMER counts the crystal
whatever the CPU runs at (`systimer_ll_get_clock_source` answers
`SYSTIMER_CLK_SRC_XTAL`, hal/esp32s3/include/hal/systimer_ll.h).

**What the ROM leaves**, read from its code:

- No literal in the ROM names SYSTEM_CPU_PER_CONF_REG, SPI0's
  CORE_CLK_SEL or RTC_CNTL_DATE_REG. SYSTEM_SYSCLK_CONF_REG is named once,
  in the pool at 0x4004_5314 that `ets_get_xtal_div` (0x4004_5318),
  `ets_set_xtal_div`, `ets_get_apb_freq` and `ets_get_xtal_freq` (its
  `l32r` at 0x4004_535B) read; nothing on the flash-boot path writes it. A
  literal search is not proof (a base can be computed), so the `xtal40`
  line prints both words as it finds them.
- **UART0 is clocked by the crystal.** `Uart_Init` (0x4004_89F0) stores
  0x0340_0000 to UART0 + 0x78 (UART_CLK_CONF) and ORs in 0x0030_0000:
  SCLK_SEL 3, the crystal, with no pre-divide and the clock enables set,
  0x0370_0000. It then divides 0x2625_A000 (640,000,000, which is 40 MHz
  times 16; the literal at 0x4004_7F20) by the baud and `uart_div_modify`
  (0x4004_8964) stores the quotient to UART_CLKDIV: 5555, the integer 347 in
  [11:0] and the sixteenths, 3, in [23:20], `0x0030_015B` as §4 reads it.
  So the console's clock is the crystal, not APB, and a change of the CPU's
  clock does not move it: 40 MHz / 347.1875 is 115,212 baud.
- **The CPU runs at 20 MHz: the crystal divided by two.** The sources
  disagree. ESP-IDF's own bootloader says the ROM runs at 40 MHz after a
  power-on reset (`bootloader_clock_configure`, bootloader_support/src/
  bootloader_clock_init.c: "not needed on power on reset, when ROM
  bootloader is running at 40 MHz"), while ESP-IDF's
  `clk_ll_cpu_get_divider` reads the reset PRE_DIV_CNT of 1 as a divide by
  2, which is 20 MHz, and the ROM's `ets_get_xtal_div` divides only when
  CLK_DIV_EN is set. The part settles it (§14h): CLK_DIV_EN reads 1 with
  PRE_DIV_CNT at 1, and the `xtal40` image's line, which measures the CPU
  before it stores any clock word (§14d), reads 20 MHz.

### 14b. ESP-IDF's sequence, and what is taken

ESP-IDF's second-stage bootloader goes from the ROM's clock to 80 MHz with
`bootloader_clock_configure` → `rtc_clk_init` (esp_hw_support/port/esp32s3/
rtc_clk_init.c) → `rtc_clk_cpu_freq_set_config` (rtc_clk.c); an application
reaches 160 and 240 MHz with the same `rtc_clk_cpu_freq_set_config`, after
`rtc_init` (rtc_init.c) has set the LDO voltages. `rtc_clk_cpu_freq_set_config`
takes the crystal path (`rtc_clk_cpu_freq_to_xtal`) for a speed at or below
the crystal, and the PLL path (`rtc_clk_bbpll_enable`,
`rtc_clk_bbpll_configure`, `rtc_clk_cpu_freq_to_pll_mhz`) for 80, 160 and
240 MHz, which `rtc_clk_cpu_freq_mhz_to_config` maps to the PLL at 480 MHz
divided by 6, 3 and 2. The bootloader calls `rtc_clk_init` only when the
reset was not a CPU software reset or APB reads below 80 MHz (the condition
around the call in bootloader_clock_init.c). Every step, in order:

```
step                     register, field = value              taken by xtal40
-- bootloader_clock_configure (bootloader_clock_init.c) -----------------------
B1 esp_rom_output_tx_    poll UART0 until the FIFO is empty   yes: the drain,
   wait_idle(0)          and the transmitter idle: STATUS     the same test
                         TXFIFO_CNT 0, FSM_STATUS
                         ST_UTX_OUT 0. On this chip it is the
                         ROM's uart_tx_wait_idle
                         (esp_rom/esp32s3/ld/esp32s3.rom.api.ld)
B2 rtc_clk_init(cfg), cpu_freq_mhz = CPU_CLK_FREQ_MHZ_BTLD = 80 (soc/soc.h)
B3 RTC_CNTL_INT_ENA = 0, RTC_CNTL_INT_CLR = ~0                no: no RTC
                                                              interrupt is used
-- rtc_clk_init (rtc_clk_init.c) -----------------------------------------------
R1 RTC_CNTL_REG.SCK_DCAP, RTC_CNTL_CLK_CONF.CK8M_DFREQ        no: tune the RC
   (the 150 kHz and 8 MHz RC oscillators)                     oscillators, not
                                                              the CPU's clock
R2 RTC_CNTL_SLOW_CLK_CONF.ANA_CLK_DIV, RTC_CNTL_CLK_CONF.     no: the same
   CK8M_DIV_SEL (rtc_clk_divider_set, rtc_clk_8m_divider_set)
R3 ANA_CONFIG_REG 0x6000_E044 [17:8] = 0x3FF                  no: PLL only
   (regi2c_ctrl_ll_i2c_reset), then [17] = 0
   (regi2c_ctrl_ll_i2c_bbpll_enable; soc/regi2c_defs.h)
R4 esp_rom_output_tx_wait_idle(0)                             as B1
R5 RTC_XTAL_FREQ_REG (RTC_CNTL_STORE4) = 40 | 40 << 16        no: bookkeeping
   (rtc_clk_xtal_freq_update), and the APB frequency          for ROM and IDF
   in a variable                                              functions this
                                                              kernel never
                                                              calls, in an
                                                              RTC-retained word
R6 rtc_clk_cpu_freq_set_config, below
R7 CCOUNT rescaled to the new speed (esp_cpu_set_cycle_count) no: nothing reads
                                                              CCOUNT across the
                                                              switch; the
                                                              measurement
                                                              brackets each side
R8 rtc_clk_8m_enable, rtc_clk_fast_src_set,                   no: the RTC fast
   rtc_clk_slow_src_set (RTC_CNTL registers)                  and slow clocks
-- rtc_clk_cpu_freq_to_xtal(40, 1), the crystal path (rtc_clk.c) --------------
X1 esp_rom_set_cpu_ticks_per_us(40)                           no: the ROM delay
                                                              loop's variable;
                                                              the kernel times
                                                              with SYSTIMER
X2 SYSCLK_CONF.PRE_DIV_CNT = 0 (clk_ll_cpu_set_divider(1))    yes: one store
                                                              from IRAM
X3 SYSCLK_CONF.PRE_DIV_CNT = div - 1 = 0                      the same value:
                                                              that store
X4 SYSCLK_CONF.SOC_CLK_SEL = 0 (clk_ll_cpu_set_src)           yes, a second
                                                              store from IRAM,
                                                              made only when
                                                              the source read
                                                              is not the
                                                              crystal
X5 the APB frequency variable                                 no: software
X6 only from 240 MHz: I2C_DIG_REG EXT_RTC_DREG, EXT_DIG_DREG  no: from the PLL
   lowered (regi2c), 40 us                                    the CPU never
                                                              starts at 240
X7 RTC_CNTL_DATE_REG.SLAVE_PD [18:13] = DEFAULT_LDO_SLAVE     no: see below
   0x7 (esp_hw_support/port/esp32s3/include/soc/rtc.h)
   then rtc_clk_cpu_freq_set_config itself (rtc_clk.c), when  no: power only
   the old source was the PLL and no BBPLL consumer holds
   it (s_bbpll_digi_consumers_ref_count), calls
   rtc_clk_bbpll_disable: RTC_CNTL_OPTIONS0
   BB_I2C_FORCE_PD, BBPLL_FORCE_PD, BBPLL_I2C_FORCE_PD set
-- rtc_clk_bbpll_enable (rtc_clk.c, clk_tree_ll.h) -----------------------------
P1 RTC_CNTL_OPTIONS0 0x6000_8000: clear BB_I2C_FORCE_PD [6],  not built
   BBPLL_I2C_FORCE_PD [8], BBPLL_FORCE_PD [10] (each reset 0)
-- rtc_clk_bbpll_configure(40, 480) -------------------------------------------
P2 CPU_PER_CONF.PLL_FREQ_SEL = 1 (clk_ll_bbpll_set_freq_mhz)  not built
P3 I2C_MST_ANA_CONF0 0x6000_E040: clear [2] STOP_FORCE_HIGH,  not built
   set [3] STOP_FORCE_LOW (regi2c_ctrl_ll_bbpll_calibration_start)
P4 the BBPLL's analog words, block 0x66, host 1               not built
   (clk_ll_bbpll_set_config; soc/regi2c_bbpll.h):
     reg 4 MODE_HF = 0x6B
     reg 2 OC_REF_DIV = (DCHGP 5 << 4) | DIV_REF 0 = 0x50
     reg 3 OC_DIV_7_0 = 8
     reg 5 OC_DR1 [2:0] = 0, OC_DR3 [6:4] = 0 (masked)
     reg 6 OC_DCUR = (1 << 6) | (3 << 4) | 3 = 0x73
     reg 9 OC_VCO_DBIAS [1:0] = 3 (masked)
   each through ROM `rom_i2c_writeReg` / `rom_i2c_writeReg_Mask`
P5 poll I2C_MST_ANA_CONF0 [24] BBPLL_CAL_DONE                 not built
P6 esp_rom_delay_us(10)                                       not built
P7 I2C_MST_ANA_CONF0: clear [3], set [2] (calibration_stop)   not built
-- rtc_clk_cpu_freq_to_pll_mhz(f), f = 80, 160 or 240 -------------------------
Q1 240 only, going up: I2C_DIG_REG (block 0x6D) reg 4         not built
   EXT_RTC_DREG [4:0] = g_rtc_dbias_pvt_240m, reg 6
   EXT_DIG_DREG [4:0] = g_dig_dbias_pvt_240m (regi2c), 40 us;
   the values come from eFuse calibration (rtc_init.c,
   rtc_set_stored_dbias; 28 by default)
Q2 going up: RTC_CNTL_DATE_REG.SLAVE_PD = 0x7 >> (f / 80)     not built
Q3 CPU_PER_CONF.CPUPERIOD_SEL = 0, 1, 2 for 80, 160, 240      not built
Q4 SYSCLK_CONF.PRE_DIV_CNT = 0                                not built
Q5 SYSCLK_CONF.SOC_CLK_SEL = 1                                not built
Q6 the APB variable (80 MHz), esp_rom_set_cpu_ticks_per_us    not built
Q7 going down: the reverse of Q1 and Q2                       not built
```

ESP-IDF's REGI2C_WRITE and REGI2C_WRITE_MASK on this chip are
`esp_rom_regi2c_write` and `esp_rom_regi2c_write_mask`
(esp_hw_support/include/esp_private/regi2c_ctrl.h), which
esp_rom/esp32s3/ld/esp32s3.rom.api.ld provides as `rom_i2c_writeReg` and
`rom_i2c_writeReg_Mask`; esp32s3.rom.ld puts those at 0x4000_5D60 and
0x4000_5D6C, which are jump-table entries (`l32r a9; jx a9`) into bodies at
0x4003_5880 and 0x4003_58D8 that open with `entry a1, 32` and `entry a1,
48`: windowed functions.

**What is left out of `xtal40`, and why that is safe:**

- X7's SLAVE_PD. RTC_CNTL_DATE_REG's field resets to 0, all six LDO slaves
  on (rtc_cntl_reg.h, "LDO SLAVE ... default: 6'd0", and the register's
  default 0x2101271 has 0 in [18:13]); the ROM names the register nowhere,
  so it runs the chip with all six on. ESP-IDF writes 0x7 on the crystal to
  power three down, which saves power; more slaves on is the direction
  ESP-IDF takes before raising the clock (Q2), so leaving them all on is the
  side that cannot droop. It is also an RTC register, which this kernel
  writes only for the watchdogs (§2e). The line prints the word.
- R5's RTC_XTAL_FREQ_REG is a store word that survives every reset but a
  power cycle, read back by ESP-IDF and ROM functions this kernel never
  calls; and X1, X5, R7 keep software values in step.
- R1, R2, R8 configure the RTC's own oscillators and its fast and slow
  clocks, which neither the CPU, APB, SYSTIMER nor UART0 reads.
- X6 and the BBPLL power-down apply only when leaving the PLL; on the part
  the CPU starts on the crystal (the emulator's word says PLL, §14e).

### 14c. Why the PLL speeds are not built

Every PLL speed needs steps P1 to P7, and 160 and 240 MHz need more:

- **The analog words.** P4 writes six registers of the BBPLL's analog block
  over the chip's internal I2C, and ESP-IDF does it only through the ROM's
  `rom_i2c_writeReg`/`_Mask`, which are windowed. This kernel is call0 and
  runs no windowed code of its own (§7d), so it would need either a
  windowed-call trampoline into the ROM, which must also leave the ROM's
  function table in its RAM (`l32r a8; l32i a8, a8, 0` at the body's head)
  intact, or the I2C master's protocol re-derived from the ROM's
  disassembly, which no ESP-IDF source for this chip states.
- **RTC writes.** P1 clears power-down bits in RTC_CNTL_OPTIONS0 (each
  resets to 0, so it may be a no-op that only a read can show), and Q2
  writes SLAVE_PD; both are RTC registers beyond `_start`'s.
- **The voltage.** 240 MHz raises the digital LDO through I2C_DIG_REG to a
  value ESP-IDF reads from eFuse calibration (Q1). 160 MHz is reached in an
  application only after `rtc_init` has set the LDO to
  `g_dig_dbias_pvt_non_240m` (rtc_init.c), which the bootloader's 80 MHz
  never does; the ROM's own setting is unread.

So 80 MHz is the PLL speed with the fewest steps, and each of those steps
is an analog or RTC write. They are not built here.

### 14d. What each key sets

The key is chosen per image at build time (`s3clock`, `keys/clock-*/`);
`rom` is the default everywhere. The case key `"clock"` sets it, and
`--emit-flash CASE --clock KEY` overrides it for a board image.

```
key     registers written, in order                    from
rom     none                                           -
xtal40  SYSTIMER_CONF |= CLK_EN | UNIT0_WORK_EN        what the kernel's own
        (0x6002_3000), and UNIT0_OP's UPDATE for       timer writes anyway
        each reading
        UART0 CLK_CONF = 0x0370_0000 and UART_CLKDIV   only if SCLK_SEL does not
        = 0x0030_015B: the ROM's words                 read 3; never on a part
                                                       (§14a)
        SYSCLK_CONF = read & ~0x3FF (PRE_DIV_CNT 0),   X2/X3, from IRAM
        by `sos_clock_store`
        SYSCLK_CONF = that & ~0xC00 (SOC_CLK_SEL 0)    X4, from IRAM, only if
                                                       the source read is the
                                                       PLL; RC_FAST or 3 stops
                                                       before any clock word
                                                       is written (SYSTIMER's
                                                       two rows above have
                                                       been)
```

Clocks before and after, for each reading of the memory-SPI core clock on
the crystal, which no source states and no run has measured: H1 the
crystal, H2 a PLL-derived 80 MHz (the core clock ESP-IDF's low-speed flash
mode assumes, `FLASH_LOW_SPEED_CORE_CLOCK_MHZ` in esp_hw_support/port/
esp32s3/mspi_timing_config.c), H3 the system clock after the pre-divider.
The flash's divider is the ROM's: `spi_flash_boot_attach` (0x4004_A8CC)
calls `SPI_init(5, 4)` (0x4004_A4A8), which stores one word to SPI1's and
SPI0's SPI_MEM_CLOCK_REG (0x6000_2014 and 0x6000_3014, its pool at
0x4004_A480), divide by 4. No key writes it.

```
                 CPU        APB        UART0                flash
rom              20         = CPU      crystal, 115,212 bd  H1 10, H2 20,
                                                            H3 5 MHz
xtal40           40         40         crystal, 115,212 bd  H1 10, H2 20,
                                                            H3 10 MHz
```

The console stays at the ROM's 115200 for every key because its clock is
the crystal, which no key moves, and its divisor is the ROM's; `xtal40`
waits for the transmitter to go idle before its stores and prints nothing
until after them. Flash execution through the cache stays valid because
the divider, the read mode and the dummy cycles the ROM set are untouched
and the flash's clock after the switch is at most 20 MHz in every reading:
ESP-IDF tunes the timing of a flash in STR mode only above 80 MHz
(`MSPI_TIMING_FLASH_NEEDS_TUNING`, esp_hw_support/port/esp32s3/
mspi_timing_tuning_configs.h), and the switch's own store runs from IRAM.

SYSTIMER is the crystal's, so ticks, Timers and `Clock.now` do not move.
The TIMG watchdogs count APB-derived clocks (`TIMER_SRC_CLK_APB` is the
timer groups' default, soc/esp32s3/include/soc/clk_tree_defs.h); `_start`
writes TIMG0's config 0, and TIMG1's is left as §2e says. The RTC watchdog
is off and the super watchdog feeds itself (§2e), so no period anything
waits on moves with a key.

**Every register a key writes is volatile.** SYSCLK_CONF, UART0's and
SYSTIMER's registers are reset by a chip reset (power, or the EN pin, which
the board's reset button and esptool pull) and by a system reset. A CPU-only
reset (a TIMG watchdog's) leaves the clock as it was, as ESP-IDF's
bootloader notes ("in case of TG WDT reset, CPU may still be running at >80
MHZ"); the watchdogs are off, so nothing here makes one. Nothing written is
RTC-retained or non-volatile, and no eFuse is read or written.

**A wrong value on a part.** A PRE_DIV_CNT too large slows the CPU, and
under H3 the flash with it, down to 40 MHz / 1024; a SOC_CLK_SEL of 3 names
no source. Either looks like a hang: no line after the ROM's banner, or one
that crawls. A wrong UART word garbles the console and stops nothing. The
part stays that way until reset: the board's reset button or `esptool`'s
EN-pin reset is a chip reset that restores every word, and the ROM's
download mode (strapping GPIO0 low at that reset) runs before any image
code, so `write-flash` puts a good image back.

### 14e. The emulator

Booted by the smoke's `boot_xtal40` and `kill_churn_xtal40` (§14g):

```
register             what the emulator does
SYSCLK_CONF          reads 0x000a8401 before and after both stores: the PLL
                     selected, PRE_DIV_CNT 1, the crystal 40, CLK_DIV_EN set;
                     a store is dropped. So `xtal40` takes its two-store path
                     here and its one-store path on a part
CPU_PER_CONF         reads 0
UART0 + 0x78         reads 0x15122500 before and after a store: the model has
                     a date word there, not UART_CLK_CONF, so `xtal40` writes
                     the ROM's two words, and the model drops the first and
                     ignores the divisor (§4)
UART0 FSM_STATUS     reads 0: the transmitter is always idle
SPI0 0x6000_30EC,    read 0: SPI0 falls in `esp32s3.iomem` (§3)
  0x6000_3014
RTC_CNTL_DATE        reads 0
SYSTIMER             modelled (§6a)
CCOUNT               40 MHz of host time whatever is stored: `mhz=` reads
                     about 40.00 on both sides
```

Its line, up to the two speeds and after them, is asserted; the speeds are
host time and are not.

### 14f. The profile: where a round of `kill_churn` goes

`kill_churn_profile` (`tests/kill-churn-profile`) is `kill_churn`'s two
hundred rounds with root reading the Monotonic clock at each phase's edge
and printing the totals once, at the end, in microseconds: `create`
(`process_create`), `give` (the role Event, a CALL round's inlet, the boot
System), `start`, `settle` (yielding until the child holds still), `kill`
(`kill` and `get_status`), `release` (dropping the round's handles), and
`all` (each round from its first reading to its last). Root prints nothing
per round; its seven readings per round are system calls inside the phases
they bound.

**The console's share is measured, not computed.** The kernel prints its
156-byte teardown line in every kill. At 115200 a byte leaves the FIFO
every 86.8 us, so the line takes 13.5 ms to send, but the CPU waits only for
FIFO room: 128 bytes go in at once, so a round otherwise longer than 13.5
ms waits about (156 - 128) x 86.8 us = 2.4 ms, and a shorter one waits for
the last round's line too. That depends on the round's own length, which is
what is being measured, so arithmetic would be a model. The image is built
with the console stall count (`s3stall`, `keys/stall-on`): from the moment
the tick is first armed, the console times every wait for FIFO room against
SYSTIMER and counts the bytes, and the clean stop prints both. During the
rounds the teardown line is the only output, so the stall is all inside
`kill`; `kill` minus the stall is the kill's own work. Root waits 50 ms
before its line, so the UART has drained, and its line fits the FIFO, so it
adds no stall. A kernel built with the line off would measure the same thing
in a second run and need a build key in `kernel/core`; the count needs
neither, and the gate's kernel carries no instrumentation either way: the
count is only in an image built with its key.

The kernel's own work in `create` is not split further. On the part it is
the largest phase, a little over a third of a round (§14h).

### 14g. The images

The smoke's clock cases are `boot_xtal40` (the kernel alone at `xtal40`: the
line, the banner, the refusal of an empty image) and `kill_churn_xtal40`
(`kill_churn` at `xtal40`). `kill_churn_profile` is board-only: the smoke
prints its reason and builds nothing for it. A board image of each is
`--emit-flash CASE --clock KEY --out PATH`.

An image at the `rom` key is the bytes it is without the keys: every image
the smoke builds at the defaults has the SHA-256 it had before them.

### 14h. Measured on the part

From a board run of the images in §14g, ESP32-S3 rev v0.2, ROM
`esp32s3-20210327`. The clock line, the same words in both `xtal40` images:

```
sysclk     0x000a8001 -> 0x000a8000   SOC_CLK_SEL 0 (the crystal),
                                      CLK_XTAL_FREQ 40, CLK_DIV_EN 1,
                                      PRE_DIV_CNT 1 -> 0
cpu_per    0x0000000c -> 0x0000000c   PLL_FREQ_SEL 1, CPUPERIOD_SEL 0
uart_clk   0x03700000                 SCLK_SEL 3, the crystal
mspi_core  0x00000000                 CORE_CLK_SEL 0
mspi_clock 0x00030103                 the ROM's divide by 4
rtc_date   0x02101271

image                        mhz before   mhz after
kill_churn, xtal40           19.46        40.01
kill_churn_profile, xtal40   19.54        40.01
```

So the ROM leaves the CPU on the crystal divided by two, and one store
makes it the crystal. The figure before the store reads about 2.5% under
the 20 MHz the registers say, and the one after reads true. Why is not
shown by a run; the first SYSTIMER snapshot after its clock is enabled may
take longer than the later ones, which would cost the first measurement
that many cycles.

`kill_churn`, from the reset to `200 rounds, every create succeeded`:
26.05 s at `rom`, 13.44 s at `xtal40`.

`kill_churn_profile`, in microseconds over the two hundred rounds, and the
console's stall:

```
          create    give     start   settle   kill     release  all
rom       10383015  3312665  786219  7408246  5651505  1390653  28932304
xtal40     5074566  1487127  379971  3577771  3002108   506008  14027553

          stalled   bytes written after the tick was armed
rom         29483   31316
xtal40     208906   31314
```

Every phase is about twice as fast at twice the clock, and the console's
wait for FIFO room is under two percent of the run at either.

---

## 15. The board probe

`tests/xtensa/s3_mem_probe.saw`, the smoke's `s3_mem_probe`, reads the
registers the boot ROM leaves and prints them: the eFuse, the clock, the
memory-SPI controllers, the caches, the MMU and the pads. It is the first look
at a part before anything drives its PSRAM or its pads, and its words are what
a board description's clock and pad facts will be written from.

It writes nothing beyond what every image of this tree writes (`_start`'s
registers, the kernel's RAM, the console's FIFO). It does not call
`intc_init`, does not touch SYSTIMER (a read of its counter takes a write of
its snapshot bit), sends no command on the memory-SPI bus, routes no
interrupt, writes no pad, clock, cache or MMU register, and leaves the clock
at the `rom` key's. What holds that for an image is a trace of the built
image's device writes under the emulator, made before it is flashed: it
must write no device address an ordinary boot does not. Every load goes
through one accessor whose view is a `ReadOnly` field, so a store through it
is a compile error; and the runner refuses the source if it takes a direct
route to a store (a second view, an unsafe type named outside the accessor,
a HAL call but the one that halts, an import beyond the console and the HAL,
`extern`, `asm`), which catches a mistake but cannot prove the property: a
raw pointer from a safe name and an index reach any address
(`check_read_only_probe`).

`python3 tools/sos_runner.py --board esp32s3 --emit-flash s3_mem_probe --out
PATH` builds the flashable image. Every address is the header's: a register's
address in ESP-IDF's `soc` headers (`reg_base.h` base plus the register's
offset), named in the probe's file by the header's own symbol.

### 15a. The lines

Each word is printed as `name=0x%08x`. In order:

```
SOS memprobe: efuse sys3= sys4= PSRAM_CAP= PSRAM_VENDOR=
SOS memprobe: clock strap= sdio_conf= sysclk= cpu_per= perip_clk_en0=
              perip_rst_en0=
SOS memprobe: spi0 clock= sram_clk= core_clk_sel= ctrl= user= user1= user2=
              misc=
SOS memprobe: spi0 fctrl= sctrl= sram_cmd= drd= dwr= ddr= smem_ddr= smem_ac=
              date=
SOS memprobe: spi1 clock= ctrl= user= user1= user2= misc= fctrl= ddr= cmd=
SOS memprobe: cache dcache_ctrl= dcache_ctrl1= dcache_autoload= icache_ctrl=
SOS memprobe: mmu 0= 32= 127= 128= 255= 256= 383= 511=
SOS memprobe: iomux pin_ctrl= 14= 21= 26= 27= 28= 29= 30= 31= 32=
SOS memprobe: iomux 33= 34= 35= 36= 37= 41= 42= 47=
SOS memprobe: insel 7= 8= 9= 10= 11=
SOS memprobe: misc pad_hold= ace0= rtc_pad21= rtc_pad21_mux_sel=
              rom_dummy_ptr= rom_dummy_spi0= rom_dummy_spi1=
SOS-S3: halt pass
```

(a wrapped line is one line.) What each word decides:

- `sys4`, `PSRAM_CAP`, `PSRAM_VENDOR`: the in-package PSRAM's size and
  vendor, as the eFuse says them (`EFUSE_RD_MAC_SPI_SYS_4_REG`, the two
  fields at bits [4:3] and [8:7]). The code for 8 MiB is 1 and for an AP
  3.3 V part is 1. `sys3` is the word before it.
- `sysclk` is the whole of `SYSTEM_SYSCLK_CONF_REG`: `SOC_CLK_SEL` [11:10],
  `PRE_DIV_CNT` [9:0], `CLK_DIV_EN` bit 19 and `CLK_XTAL_FREQ`. `cpu_per` is
  the whole of `SYSTEM_CPU_PER_CONF_REG` (`PLL_FREQ_SEL`, `CPUPERIOD_SEL`).
  With `core_clk_sel` these are every field that sets the CPU's and the
  memory-SPI core's clock. They say what clock the ROM left; §14h has the
  words the part printed for them.
- `spi0 clock`, `spi0 sram_clk`, `spi1 clock`: the dividers of the flash,
  the PSRAM's cache-fill side and SPI1; they say what the ROM's flash driver
  set. `core_clk_sel` is the one core clock both controllers share.
- `spi0 smem_ac`, `smem_ddr`, `sram_cmd`, `drd`, `dwr`, `fctrl`, `sctrl`:
  what SPI0 holds for the PSRAM side before anything configures it.
  `spi0 date` carries the clock-pad drive enable (`SPICLK_PAD_DRV_CTL_EN`).
- `spi1 user`, `user1`, `user2`, `misc`, `fctrl`, `ddr`, `cmd`: SPI1's
  command setup as the ROM left it, and whether a command is pending (`cmd`).
- `cache`: the data cache's size, ways and line (`dcache_ctrl`), whether its
  bus is shut (`dcache_ctrl1` bit 0), whether an autoload is in flight
  (`dcache_autoload`, DONE at bit 3) and the instruction cache's shape.
- `mmu`: the translation table's entries, which pages the ROM mapped. An
  entry is a page number, bit 14 set for invalid, bit 15 set for PSRAM; the
  entries 256 to 383 are the ones a PSRAM map would replace.
- `iomux pin_ctrl`, and a pad's word per pin: the function, pulls, input
  enable and drive of the flash's pads (27 to 32; pad 30's drive is the
  clock's), the PSRAM's (26, 33 to 37), and the pins the board's GPIO work
  uses (14, 21, 41, 42, 47).
- `insel`: whether the GPIO matrix's input selects for signals 7 to 11
  (the PSRAM's D4 to D7 and DQS) route straight from the IO MUX (bit 7
  clear).
- `strap`: the pad straps (`GPIO_STRAP_REG`); `sdio_conf`: the VDD_SPI
  supply's source and force bits (`RTC_CNTL_SDIO_CONF_REG`), with `strap`
  and the eFuse, what voltage the package runs at.
- `perip_clk_en0`, `perip_rst_en0`: which peripherals' clocks run and which
  are held in reset before any driver turns one on.
- `pad_hold`: whether any pad is held (`RTC_CNTL_DIG_PAD_HOLD_REG`); `ace0`:
  the PSRAM ECC attribute (`SYSCON_SRAM_ACE0_ATTR_REG`, bit 8);
  `rtc_pad21` and `rtc_pad21_mux_sel` (bit 19): whether RTC pad 21 is routed
  to the RTC domain or to the IO MUX.
- `rom_dummy_ptr`, `rom_dummy_spi0`, `rom_dummy_spi1`: the ROM's pointer to
  its flash-driver state, and the extra dummy bytes it keeps there for SPI0
  and SPI1 (at offsets 24 and 25 of what it points to). Not a register. The
  pointer is read at 0x3FCE_FFE4 and followed only if it is a word address in
  SRAM; otherwise the line says `rom_dummy=unreadable`.

### 15b. On the emulator

The eFuse reads 0 (the smoke gives it no file); `spi0`, `iomux` and `insel`
read 0 (no model); `spi1 clock` and `ctrl` are `0x00030103` and `0x00208000`,
the words the ROM's flash driver writes; the cache controller reads its reset
values (`dcache_autoload` is `0x00000008`, DONE, the others 0); each `mmu`
entry holds its own index. `sysclk` reads `0x000a8401` (the emulator runs the
PLL) and `cpu_per` 0. The smoke asserts all of these but the last two, and
prints every word it does not assert.

### 15c. On the part

The cells are empty until a board run fills them. The clock words are in
§14h and are not repeated here; this run's `sysclk`, `cpu_per`,
`core_clk_sel` and `spi1 clock` should agree with them.

| word | part |
|---|---|
| `sys3`, `sys4` | |
| `PSRAM_CAP`, `PSRAM_VENDOR` | |
| `strap`, `sdio_conf` | |
| `sysclk`, `cpu_per`, `core_clk_sel` | |
| `perip_clk_en0`, `perip_rst_en0` | |
| `spi0 clock`, `sram_clk`, `ctrl`, `user`, `user1`, `user2`, `misc` | |
| `spi0 fctrl`, `sctrl`, `sram_cmd`, `drd`, `dwr` | |
| `spi0 ddr`, `smem_ddr`, `smem_ac`, `date` | |
| `spi1 clock`, `ctrl`, `user`, `user1`, `user2` | |
| `spi1 misc`, `fctrl`, `ddr`, `cmd` | |
| `dcache_ctrl`, `dcache_ctrl1`, `dcache_autoload`, `icache_ctrl` | |
| `mmu` 0, 32, 127, 128, 255, 256, 383, 511 | |
| `iomux pin_ctrl`, pads 14, 21 | |
| `iomux` pads 26 to 32 (CS1, then the flash's) | |
| `iomux` pads 33 to 37 (the PSRAM's D4 to D7, DQS) | |
| `iomux` pads 41, 42, 47 | |
| `insel` 7 to 11 | |
| `pad_hold`, `ace0` | |
| `rtc_pad21`, `rtc_pad21_mux_sel` | |
| `rom_dummy_ptr`, `rom_dummy_spi0`, `rom_dummy_spi1` | |
