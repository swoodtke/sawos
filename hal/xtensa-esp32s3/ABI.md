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
offset 0x20_0000, after root's image slot and the children's (§13), so a board
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

**A console claim** (spec §9a) is taken back at every stop through `kcore`'s
`sos_console_stop`: `exit_pass`, `exit_fail`, `sos_rt_abort`, the
out-of-CPU-interrupts stop and the refused windowed call call it before they
write, and `kernel_fault` after its line, through `sos_fault_console_stop`,
which bounds the console's polls as `kernel_fault`'s own are, so that the
report reaches the console whatever the Saw side's state and a UART that never
drains cannot keep it from the halt. With a claim standing it calls
`console_reclaim`, which disables the CPU interrupt UART0's source (27) is
routed to, leaving the route as `irq_mask` does, and touches no UART
register: the write path is a count read and a store, which need no enable
bit, and no FIFO is reset, so the claimant's stored bytes go out ahead of the
kernel's.

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

### 4b. UART0 under a driver

A process that serves UART0 takes the 4 KiB page at 0x6000_0000 as its
window and interrupt matrix source 27 as its line (`board.toml`; §6c found
27 by probing and §9b's header count gives the same). `drivers/esp32s3io`
holds its numbers and the order of each service, and its hosted tests record
every register access. The register file is ESP-IDF v5.4.1's: offsets and
fields from `components/soc/esp32s3/register/soc/uart_reg.h`, the FIFO's depth
from `soc_caps.h` (`SOC_UART_FIFO_LEN`, 128) and the access rules from
`components/hal/esp32s3/include/hal/uart_ll.h`.

```
+0x00  FIFO      a read pops one received byte; a 32-bit store queues one
+0x08  INT_ST    raised and enabled (read-only)
+0x0C  INT_ENA   which interrupts reach the line
+0x10  INT_CLR   a bit stored here clears that interrupt's raw bit
+0x1C  STATUS    RXFIFO_CNT bits 9:0, TXFIFO_CNT bits 25:16
+0x24  CONF1     RXFIFO_FULL_THRHD 9:0, TXFIFO_EMPTY_THRHD 19:10, RX_TOUT_EN 23
+0x60  MEM_CONF  RX_TOUT_THRHD 26:17, in bit times
```

The interrupt bits, the same in all three: RXFIFO_FULL 0, TXFIFO_EMPTY 1,
PARITY_ERR 2, FRM_ERR 3, RXFIFO_OVF 4, BRK_DET 7, RXFIFO_TOUT 8. The receive
set a driver takes is every one of them but TXFIFO_EMPTY (0x19D); the
transmit one is enabled only while bytes wait to be sent.

**Not touched.** `CLKDIV` and `CLK_CONF` are the console key's (§14i), and
`CONF0`'s FIFO resets are left alone: the transmit FIFO may hold the kernel's
last bytes and the receive FIFO bytes typed before the driver started, which
the driver delivers.

**Bring-up**, in order, after the three values pass the driver's checks (a
receive threshold of 1 to 127, a transmit threshold of 1 to 128, a timeout of
1 to 1023 bit times; a refusal writes nothing): `INT_CLR` of the receive set;
`CONF0` read, then written with `ERR_WR_MASK` (bit 26) clear and every other
bit as read; `MEM_CONF` the same with the timeout and `RX_SIZE` and `TX_SIZE`
(bits 3:1 and 6:4) at one 128-byte block each; `CONF1` the same with the two
thresholds, RX_TOUT_EN, and `DIS_RX_DAT_OVF`, `RX_TOUT_FLOW_DIS` and
`RX_FLOW_EN` (bits 20 to 22) clear; then `INT_ENA` of the receive set. The
clear comes first so a timeout that bytes left in the FIFO raise while the
registers are configured is not erased. Each register keeps every bit the
driver does not rely on, except `INT_ENA`, which the driver owns whole and
writes as a word; each field written is masked to its width, so a value past
it cannot reach `MEM_FORCE_PD` (MEM_CONF bit 27) or the field beside it.

**What the driver relies on, it writes**, whatever the reset value: a damaged
byte stored (`ERR_WR_MASK` 0, so its error is reported at a mark after it),
the overflow detected and no flow control (`CONF1` bits 20 to 22 at 0), and
128-byte FIFOs. The emulator's `CONF0` reads `0x0400_001C` after the ROM's
boot (§4), `ERR_WR_MASK` set, and its `MEM_CONF` reads 0 (below), though its
FIFOs are 128 bytes whatever it says. Left alone are the frame's format in
`CONF0` and the divisor, which are the console key's.

**The receive service**, before the line is acknowledged: `INT_ST` read; for
any receive bit, in this order, `INT_CLR` of the events it showed (parity,
frame, overflow, break) before any byte is popped; `STATUS.RXFIFO_CNT` read
and that many `FIFO` reads, the count read again until it is 0; `INT_CLR` of
the levels it showed (FULL, TOUT) and the drain once more into the same
batch. An event that rises while draining is therefore never erased by a
clear that came after it, and stays raised for the next service; and the byte
that landed at the level's clear is in the batch. No bit the service did not
see is cleared. Every register access comes before the acknowledgement (spec
§9). A timeout is treated as "look": it may be raised with the FIFO empty.

**The transmit service** stores whole words to `FIFO` after one `STATUS`
read, as many as 128 less `TXFIFO_CNT`, and never reads `FIFO`, whose read
pops the receiver. TXFIFO_EMPTY rises when the FIFO holds fewer bytes than
`TXFIFO_EMPTY_THRHD`, and its raw bit sets itself again for as long as that
holds (`uart_reg.h`: R/WTC/SS), so a clear holds only once the FIFO is above
the threshold. ESP-IDF clears after the fill. A driver over `uartcore` clears
before each top-up and fills after, the order that loses no wakeup on a UART
that latches the cause instead (`uartcore`'s `device.saw`); here that costs
one more interrupt per top-up, which finds the FIFO above the threshold.

**The emulator** (`esp32s3_soc.uart`, over `esp_soc.uart`, in Espressif's
QEMU 9.2.2). A call0 program booted with `-kernel` and polling with no
interrupts printed `STATUS` and `INT_RAW` whenever either changed while the
host typed into the terminal, then popped the receive FIFO, then stored 100
bytes and read the transmit side at once:

```
at reset           CONF1=0  MEM_CONF=0  INT_RAW=0x4003
INT_CLR of all     INT_RAW=0x4003           (a held condition re-sets)
thresholds set     INT_RAW=0x4002, then 0x4102 with RXFIFO_CNT=0
'A' typed          RXFIFO_CNT=1    INT_RAW=0x4102   (timeout; 1 is below 4)
6 bytes typed      RXFIFO_CNT=7    INT_RAW=0x4103   (full; 7 is above 4)
200 bytes typed    RXFIFO_CNT 0x60, then 0x80 (128), INT_RAW=0x4103, no bit 4
pop 128            the bytes in order; then RXFIFO_CNT=1, the 129th
100-byte burst     TXFIFO_CNT=0  FSM_STATUS=0  INT_RAW=0x4002
```

- Typed bytes enter a 128-byte receive FIFO in order and `RXFIFO_CNT` counts
  them. RXFIFO_FULL follows the threshold.
- RXFIFO_TOUT is modelled, and was raised with the FIFO empty right after it
  was enabled. A driver treats it as "look" and does not assume a byte;
  whether the part does the same is not checked.
- There is no hardware overflow: the model stops taking input while its FIFO
  is full and the rest waits in the host's pipe. RXFIFO_OVF never rose, so it
  is the part's alone.
- Transmit is immediate: `TXFIFO_CNT` and `FSM_STATUS` read 0 after a burst
  (§4), so TXFIFO_EMPTY is raised whenever enabled. That is how the
  `external_irq` smoke case raises line 27, and the line follows `INT_ENA`
  (§6c).
- The reset values are not the part's. `CONF1` and `MEM_CONF` read 0 where the
  register file gives 96, 96 and 10, and with a receive threshold of 0 the
  full bit reads raised on an empty FIFO.
- The terminal sends whole bytes, so no parity or frame error can be made, and
  whether the model would report one is not checked.

What only a part can check: a transmit FIFO that holds bytes, so the empty
threshold's timing; a receive FIFO that overflows at a high rate; the timeout
with a person at the terminal; and the errors.

**The driver** (`drivers/svc-uart-esp32s3`) is these orders as `uartcore`'s
device: `INT_ST`'s bits map onto the core's causes (FULL and TOUT both onto
its receive level, which it takes as "look"), `STATUS`'s two counts are the
FIFOs' fills, and the transmit level is `TXFIFO_EMPTY_THRHD`, which it
writes 1 to 128 whatever the core asks. It brings the UART up with
`start_uart` at its build input's thresholds, attaches source 27 and its
connection's outlet to one Waiter, claims the console (spec §9a), and serves
from one thread at `Rt0`; every register access of an interrupt comes before
the line's acknowledgement. It releases the console once every client is gone
and the ring and the FIFO have drained.

What the S3 smoke's `uart_s3_*` cases showed on the emulator:

- Bytes typed at once as the emulator starts all arrive
  (`uart_s3_read_early`): the typist writes them before the guest has run
  far, though the case cannot say how far, so whether they wait in the FIFO
  through the ROM's boot is not shown.
- A byte typed alone, below the receive threshold, arrives through the
  timeout (`uart_s3_read`); more bytes than the threshold typed at once raise
  the full cause (`uart_s3_full`), which the receive service clears with the
  timeout's.
- The transmit count reads 0 after every fill, so a `Write` longer than the
  ring is held by construction and only the fill cap paces the fills
  (`uart_s3_hold`); every byte queued is sent.
- An overflow of the driver's own ring is reported at one mark with its count
  (`uart_s3_lost`). The FIFO itself never overflows here (above).
- Four `Read`s are held and a fifth answered `Busy`, and a dropped claim frees
  its slot (`uart_s3_held`); four `Sync`s held pause the driver's intake until
  one is answered (`uart_s3_syncs`).
- A driver whose claim is refused, because another process holds the console,
  exits 2 without serving (`uart_s3_claim_refused`).
- A stop under the claim (a root's panic, `root_panic_claimed`) writes its
  lines after the driver's last bytes, with nothing lost between.

What only a part can show: the FIFO draining at the wire's rate, so a
nonzero `TXFIFO_CNT`, a ring that fills under one writer and the empty
threshold's timing; `RXFIFO_OVF` at a high rate; the timeout with a person
at the terminal; parity, frame and break errors reported as `Garbled`; a stop
taking the console back while the FIFO still holds the driver's bytes; the
extra interrupt per top-up that clearing TXFIFO_EMPTY before the fill costs
(the report's `interrupts=`); the bits the bring-up writes, read back; and
what the console costs the CPU against the kernel's synchronous console.

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
everything else into the trap entry (§7c); the kernel vector takes the
Alloca exception windowed code under `sos_windowed_call` raises (§7g) and the
interrupt that ends the idle `waiti` (§6d), and stops the machine for
anything else; the double vector sends a fault a
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

Cause 32 is not a fault. Every thread runs with `CPENABLE = 0` until its first
FP instruction, which raises it, and the kernel claims it as that thread's
first use (§7c): `lib.saw`'s `fp_trap` loads the thread's FP state, enables
coprocessor 0 and resumes at the same instruction, and the kernel prints
`SOS: fp first use`, as it does for every FP trap it claims. Cause 32 from a
thread that has already used the FPU comes from a process that cleared
`CPENABLE` itself: the FPU still holds the thread's state, which the kernel
knows from its record of whose area it last loaded, so `fp_trap` enables
coprocessor 0 again, loads nothing, and the thread runs on. From an active
thread whose state the FPU does not hold, which no switch leaves, it is a
kernel bug and stops the machine, status 6 (`SOS-S3: kernel bug: coprocessor
0 disabled for an active thread whose state the FPU does not hold`).

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

What a read of the counter costs is measured by `systimer_read`
(`tests/xtensa/systimer_read.saw`), a board stage whose image is built at
`rom`, `pll80` and `pll240`. After `clock_init` it prints CONF, UNIT0_OP and
TARGET0_CONF (the block has no UNIT0_CONF) before CONF's start-up write and
after the rounds, then, as least and greatest over 8 rounds, in CCOUNT
cycles: `timer_now_ns` once and ten back to back; the write of UPDATE to
UNIT0_OP up to VALUE_VALID, with the number of reads of UNIT0_OP it took;
UNIT0_VALUE_LO read twice 1,000 cycles apart with no UPDATE between; and two
snapshots with a CCOUNT spin of 100 us, and of 1 ms, at the CPU clock
`psram_cpu_passes` measures between them: the ticks between the snapshots,
the spin's cycles, and the cycles from before the first snapshot to after the
second. Each snapshot is UPDATE, VALUE_VALID, then the two halves, as
`Systimer.now` takes it; ESP-IDF's `systimer_hal_get_counter_value` does the
same and reads the low half again to catch a change between the halves. The
emulator's lines are host time and none is held.

Every kernel read of the counter runs with interrupts masked (§7f):
`timer_now_ns`, whose callers are the Monotonic clock read, the Timer
objects' catch-up, the tick's epoch, deadline programming, the timer
interrupt's handler and the waits' deadline test; and `Systimer.expired`, by
way of `timer_pending` and the idle loop's `timer_due`.
`timer_set_deadline_ns` reads and writes CONF and the comparator and takes no
snapshot.

Measured on the part (the r8 board), least to greatest over 8 rounds:

```
                                         rom         pll80       pll240
CPU clock, kHz                           19,999      80,000      240,001
VALUE_LO moved, no UPDATE, 1000 cycles   0-0         0-0         0-0
UPDATE to VALUE_VALID, polls             1-2         1-2         1-3
UPDATE to VALUE_VALID, cycles            63-2953     64-3078     78-8329
1 ms spin, ticks between snapshots       16099-16109 16027-16029 16012-16013
100 us spin, ticks between snapshots     1715-2518   1627-1831   1612-2140
timer_now_ns once, cycles                123-4802    123-4802    173-16468
timer_now_ns ten in a row, cycles        1200-1609   1200-1609   1719-5530
CONF before the start-up write           0x46000000  0xc6000000  0xc6000000
```

After the rounds CONF is 0xc6000000, UNIT0_OP 0x20000000 and TARGET0_CONF 0
at every key.

SYSTIMER counts at 16 MHz at every key: the 1 ms spins see about 16,000
ticks. The value registers hold the last snapshot until the next UPDATE,
and VALUE_VALID is set within one to three polls of UNIT0_OP. A read that
finds the code warm costs about 120 cycles at 80 MHz and 170 at 240: ten
in a row take 1,200 and 1,719. The greatest of the rounds is much larger
(one read at `pll240` took 16,468 cycles, about 69 us). The harness prints
only each measurement's least and greatest, so which round holds a greatest
is not known.

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
is 0 in user mode and the kernel runs at 15, so ticks are taken from user
mode, and in the kernel only at the idle `waiti` (§6d).

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

**What is built: the kernel sleeps, and takes the interrupt that wakes it.**

- **The level.** Every CPU interrupt the kernel enables is level 1 and
  level-triggered: the tick's (1) and the nine a device line may be given
  (`DEVICE_CPU_INTS`, §6c), so `waiti 0` is the one level that lets every
  line in. With PS.UM = 0 a level-1 interrupt arrives at the kernel vector
  (+0x300) with `EXCCAUSE` 4. No CPU interrupt of level 2 to 5 is enabled,
  nor the NMI (14), so their vectors stay fatal.
- **The sleep.** `wait_for_irq` (`lib.saw`) calls `boot.S`'s
  `sos_idle_wait`, a call0 leaf in IRAM: `waiti 0`, then `ret` at
  `sos_idle_resume`. The interrupt that ends the sleep has EPC1 =
  `sos_idle_resume`, the instruction after the `waiti`; one pending already
  is taken at once.
- **The entry.** The kernel vector, past its Alloca test, compares EPC1 with
  `sos_idle_resume`. Equal, it adds one to `sos_idle_wakes`, writes PS =
  `0x1F` (INTLEVEL 15, EXCM 1, UM 0, WOE 0) and returns with `rfe`, which
  leaves PS = `0x0F` at the `ret`. It uses a0, through `EXCSAVE1`, which the
  kernel holds nothing in by then (§7c), and a8, a scratch register of the
  leaf. Nothing is delivered: the line, level-triggered, stays pending and
  masked, and the idle loop's `irq_poll` finds it as it finds any line.
  Anything else at the kernel vector stops the machine as before: an
  interrupt taken anywhere else in the kernel is `SOS-S3: kernel fault
  vector=0x00000300 exccause=0x00000004`, status 0x8C. No EXCCAUSE test is
  needed: the instruction at `sos_idle_resume` is a `ret` in IRAM, which
  raises nothing, so EPC1 there is the wake.
- **An expired deadline is not slept on.** `wait_for_irq` returns without
  sleeping while the timer reads expired (`irq_poll`'s own test), so the
  idle poll delivers the timer at once, without a round trip through the
  kernel vector. It is not needed to wake at all: SYSTIMER's alarm fires for
  a target already behind the counter. On the emulator, probed: a scratch
  kernel set TARGET0 to 1 ns, and to 1 ms behind the counter, and read
  `INT_RAW = 1` and `INTERRUPT = 0x00000002` 20 ms later in both, as for a
  target 2 ms ahead. On the part, by ESP-IDF v5.4.1's code (read, not
  probed): the ESP32-S3 sets `SOC_SYSTIMER_ALARM_MISS_COMPENSATE`
  (`soc/esp32s3/include/soc/soc_caps.h:378`, "Systimer peripheral can
  generate interrupt immediately" for a target already passed), so
  `systimer_hal_set_alarm_target` takes its first form
  (`hal/systimer_hal.c:78`: write the target, enable the alarm); the form
  that reprograms a target found behind the counter (`:89`) is compiled only
  for chips without that capability. The part's `idle_wake` showed the
  guard taken: a first arm whose wait came after its deadline read `did not
  sleep`.

The smoke's `idle_wake` drives the seam in the idle loop's order (wait,
then poll): a deadline 1 ms ahead and UART0's line each end a sleep, and a
deadline behind the counter is answered with none, by `hal.idle_wakes()`;
PS after each wait reads `0x0F`, so the vector's write-back is held to
INTLEVEL 15 and not merely to a level that masks level 1.
The timer's step arms its deadline again until a wait is one the `waiti`
ended: a wait reached after the deadline is answered without a sleep. On
the part (rev v0.2, the `rom` clock key) a single arm's wait came after its
deadline in each of three boots, which the emulator's never has; nothing is
printed between the arm and the wait, and the path between them runs there
for the first time, its code fetched through the flash cache.
`kernel_irq_fault` takes the interrupt at INTLEVEL 0 in a kernel leaf and
`kernel_irq_stray_waiti` at a `waiti` of the test's own; both stop at the
kernel vector.

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
F_BR 304   F_ACCLO 308   F_ACCHI 312   F_M0..F_M3 316..328
F_MISC0..F_MISC3 332..344
TRAP_FRAME_BYTES 348  (87 words; the thread's FP area follows it, below)
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
`BR` (the Boolean option's, which the FP compares write), MAC16's `ACCLO`,
`ACCHI` and `M0..M3`, and `MISC0..3` are no coprocessor's, so any thread can
read and write them without a trap: the frame carries them, so each thread
keeps its own across a switch and a new thread finds them zero (the smoke's
`s3_xregs_hold` and `s3_xregs_clean`). They are the words ESP-IDF's frame
keeps beside `SCOMPARE1` and `THREADPTR` (`XT_STK_EXTRA`), and the four MISC
registers, which it keeps nowhere. The FPU's state lives in the FP area
below, which a switch moves and a trap does not. The trap path never writes
a user stack; the window vectors (§7d) do, through the thread's own
registers.

**The FP area.** A thread's slot holds its frame and, after it, its FP area:

```
TRAP_FRAME_BYTES 348   the frame above
FP area at 348:  f0..f15 0..60   FCR 64   FSR 68   active 72
FP_AREA_BYTES 76
FRAME_BYTES 424        frame and area: within the kernel's 512-byte stride
```

`lib.saw` states the offsets once and `boot.S`'s `sos_fp_save` and
`sos_fp_load` use them; a `static_assert` there holds `FRAME_BYTES` within
512, the kernel's stride unit (`kcore.limits`' `THREAD_FRAME_STRIDE` is
`FRAME_BYTES` rounded up to it). Whose area the FPU holds is one word for the
machine, not one per area: `sos_fp_load` writes the area's address into
`boot.S`'s `sos_fp_owner_area`, and nothing else writes it or the FP
registers.

**The first-use rule.** The FPU is coprocessor 0, and `CPENABLE` bit 0 gates
it for every mode alike, there being no privilege (§5e). The kernel executes
no FP instruction outside `sos_fp_save` and `sos_fp_load`: sawc emits none
for this triple, the kernel's C is built with the `-fp` target feature
(§1a), and the runner disassembles every S3 kernel image and refuses one
with an FP or PIE instruction anywhere else (`_check_s3_kernel_fp_free`).
The check decides which bytes are code before it decides which are literal
pools: a sized function's bytes, and whatever is reached from a symbol
through fall-through and PC-relative targets, are code, and a literal is a
word an `l32r` of that code loads. It reads the generic Xtensa table, which
decodes every special register the kernel uses, and reads a line that table
cannot decode again in the ESP32-S3's, which knows the PIE ops; a line
neither decodes, or bytes that are neither code, a literal nor padding, are
refused too. The smoke's `fp_planted` cases plant an FP move, an `add.s`, a
`wur.fcr`, a PIE `ee.zero.q` and an FP move in unsized code, and each is
refused. So a trap never disturbs a thread's FP state, and a trap that
resumes the same thread saves and loads none of it.

- `frame_init` zeroes the area with the frame, so a new thread is not
  active. `_start` writes `CPENABLE = 0`.
- A thread's first FP instruction raises cause 32 (§5c). `fp_trap` marks it
  active and loads its area into f0..f15, `FCR` and `FSR`, which leaves
  coprocessor 0 enabled, and the thread resumes at that instruction.
  Enabling always loads: the FPU holds the last active thread's values,
  possibly another process's, and the area is zero until the thread's first
  switch-out, so a first use reads zeros.
- At a switch `kcore.sched.pick_next` calls `fp_switch_out`, which saves the
  outgoing thread's state if it is active, before the idle wait and before an
  exited thread's slot is freed; `run_thread` calls `fp_switch_in`, which
  loads the incoming thread's state if it is active and otherwise writes
  `CPENABLE = 0`. A thread that never uses the FPU costs one compare at a
  switch-out and a `CPENABLE` write at a switch-in.
- The save and the load set `CPENABLE` bit 0 themselves before their first FP
  instruction, and a switch to an inactive thread writes the register
  whatever the kernel last wrote: a process can write `CPENABLE` (§5e), so
  the kernel trusts no copy of it. An active thread that clears it takes
  cause 32 at its next FP instruction, and `fp_trap` sets it again with
  nothing loaded, the FPU holding that thread's state (§5c); a switch in
  between saves and loads as for any active thread. A thread that sets it
  before its first use runs FP instructions without being active: it reads
  the last active thread's f0..f15, `FCR` and `FSR`, possibly another
  process's, and keeps none of its own across a switch. That is within the
  Flat tier's disclaimer, which cannot deny the write.

The smoke's cases: `s3_fp_threads` (two threads keep their values),
`s3_fp_leak` (a child's first use reads zeros with root's values in the
FPU), `s3_fp_inactive` (a thread switched out before its first use is not
saved), `s3_fp_idle_teardown` (the save before the idle wait), `s3_fp_reuse`
(a slot whose last thread used the FPU comes back zeroed),
`s3_fp_cpenable_cleared` (an active thread that clears `CPENABLE`, with and
without a switch before its next use), `fp_owner` (the trap against the
record of whose area the FPU holds, the kernel bug included) and the
`fp_planted` refusals.

The entry and exit:

```
user vector:  xsr a1, excsave1         ; a1 = this thread's frame
              s32i a0 -> F_A0 ; EXCCAUSE 5 -> the Alloca path (§7d)
trap entry:   save a2..a15, then excsave1 -> F_A1, epc1, ps, sar,
                lbeg, lend, lcount, exccause, excvaddr, scompare1,
                threadptr, windowbase, windowstart, br, acclo, acchi,
                m0..m3, misc0..misc3
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
For the same reason it is restored only once the frame's PS has put EXCM
back: the loop-back is taken only with EXCM clear, so no kernel instruction
runs with a live count. WINDOWSTART, `EXCSAVE1` and WINDOWBASE are written
after it, under the same PS.

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
                         26-word frame   87-word frame
vector to ktrap                50              134
resume to rfe                  37              122
round trip                     87              256
.text.vectors (IRAM)        1303 B          1960 B
```

`sos_windowed_call` (§7g) is 144 bytes of IRAM and two literal words, in an
image that calls it, and its close 9 bytes of flash.

Every added instruction is a load, a store, a `rotw`, a `mov` or a special
register access, but for the user vector's cause test and the resume's `addi
a2, a2, 12` and `rsync`. A window exception costs its handler, 5 to 14
instructions with its `rfwo` or `rfwu`, and no kernel entry; an Alloca
exception about 25 and an underflow handler.

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

**The handlers.** The six window vectors (§5a) are those sequences: 4, 9 or 13
word accesses through the thread's own registers and one `rfwo` or `rfwu`,
with PS.EXCM = 1 (so no interrupt is taken inside one) and no kernel entry.
Four are the mask ROM's instruction for instruction (0x4000_0000 on);
Underflow8 and Underflow12 differ in order: the ROM's load `a0` first, these
load `a1` first, before the load that goes through it. A fill from below an
address that answers no load therefore faults on the `a1` word, the stack
pointer less 12, and `windowed_fill_fault8` and `12` expect that address (tval
0x1000_0004, where the ROM's order gives 0x1000_0000). The Alloca exception is
handled from the user vector, also without entering the kernel: the faulting
window's WINDOWBASE and `a0` are kept, the window is rotated one pane back
into the spilled caller's dead registers, PS.OWB is set to the faulting
window, the window is rotated to the caller's base by the call size in the
faulting window's `a0` (0 read as call4), and the underflow handler for that
size fills the caller and returns to the `movsp`.

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
12, a word nothing wrote, at the first wrap of the register file. The
process faults there if that word is an address that takes no store; if the
process put a writable address there, the spill writes 16 or 32 bytes
through it. A `call4` makes it a call4 frame, whose spill reads nothing.
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
inline assembly; a `.S` file takes them under either. A Saw process is
built windowed the same way: Blade hands sawc the manifest's list as
`--target-features +windowed`, and sawc compiles the package's whole module
graph windowed (the program, `sos`, `sosrt` and its panic path) with no
frame pointer, beside `rt/common_c/support.c` and the stub compiled
windowed by the same list. Every compiled function opens with `entry`,
`_start` included, so the call4 rule above never binds a Saw entry.

**The cases** (`make sos-smoke-esp32s3`): `windowed_process` (overflow and
underflow at each call size past the register file, a system call from 30
frames deep, `movsp` under a call8 caller, two threads preempted deep in
chains and between a call and its callee's `entry`), `windowed_alloca` (the
Alloca exception in a first frame and under call4, call8 and call12
callers), `register_file_clean`, `windowed_child_exit` (a windowed child
beside a call0 root), `windowed_entry_returns`, and a spill fault and a fill
fault through each of the six handlers (`windowed_spill_fault4`, `8`, `12`
and `windowed_fill_fault4`, `8`, `12`). Three run windowed Saw (`tests/winsaw`
holds their recursion): `windowed_saw_threads` (a root: a chain of 100
frames with a system call from its bottom, and two threads 64 frames down
crossing by tick at the bottom, each chain's answer checked),
`windowed_saw_child` (a windowed Saw child beside a call0 Saw root) and
`windowed_saw_fault` (that child's load fault 64 frames down, ended as its
own).

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

The kernel never takes an interrupt but at the idle `waiti` (§6d), whatever
is armed and whenever it comes due. Every interrupt it enables is a level-1
CPU interrupt (§6c), and every instruction it executes but that `waiti` runs
with level 1 masked:

- **From the ROM to `_start`'s `wsr ps`.** The handover PS is `0x00060320`,
  INTLEVEL 0, but INTENABLE is 0 (§2b), so nothing can be taken. The first
  instructions of `_start` write PS = `0x0F` (INTLEVEL 15, EXCM 0, UM 0,
  WOE 0) before leaving the register window (§2b); `intc_init`, which is
  what first sets an INTENABLE bit, runs much later, from `kmain`.
- **The whole kernel.** Nothing in the kernel writes PS apart from the trap
  entry, the restore path, the Alloca path, `sos_windowed_call`, and the
  idle sleep's `waiti 0` and the kernel vector's return from it (§6d), all
  in `boot.S`; no C leaf in `sink.c` touches it. So PS stays `0x0F` through
  `kmain`, the loader, `start_tick` and every `ktrap`, but for a windowed
  call (§7g), which sets WOE and keeps INTLEVEL 15, and puts PS back, and
  for the idle `waiti` itself, after which the kernel vector writes INTLEVEL
  15 back before it returns.
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

### 7g. The kernel calling a windowed function

`sos_windowed_call(fn, a, b, c, d, e, f)` (`kernel/boot.S`) is how the
call0 kernel calls a windowed function, which every mask-ROM function is
(§1d): a call0 function that `callx8`s `fn` with six arguments, every
argument register a `callx8` passes, and returns its `a2`. The sixth is the
call0 caller's seventh argument word, on its stack (§7e); a callee reads
only the arguments it takes, so a caller may pass fewer. It sits in IRAM
(an `.iram.text.*` section, which the link drops from an image that never
calls it), so the PLL's bring-up can call the ROM without a fetch through
the flash cache (§14d).

- **Only before the loader.** The ROM's functions read tables in the ROM's
  RAM (0x3FCD_7E04 up, §3b): the phy table `rom_i2c_writeReg` calls through,
  `rom_phyFuns` at 0x3FCE_F3D4, is inside the RAM pool a case may give to
  processes (§13a). Called after a process could have written there, a ROM
  function would jump through a word a process chose. So the loader's first
  read of the root image (`lib.saw`'s `payload_start`) closes the stub, and a
  call after that stops the machine before `fn` runs:
  `SOS-S3: kernel bug: windowed call to 0x... after the loader`, status 6.
  The check is in the stub, the one way in.
- **The window state.** For the call the stub's frame is the only live
  window (WINDOWSTART = 1 << WINDOWBASE): the kernel's WINDOWSTART after a
  trap is the thread's, and a stale bit would make the callee's first
  reference spill a frame that does not exist. PS gets WOE = 1 with EXCM = 0
  (`entry` needs both) and keeps INTLEVEL 15, so no interrupt is taken during
  the call. Both are written back as they were afterwards; nothing in the
  kernel reads either while WOE is 0, and `rom_call` observes the
  WINDOWSTART put back. Every register the stub names lies in its own window
  or its callee's, where WINDOWSTART has no bit, so no window exception is
  raised outside the call.
- **Registers.** `callx8` makes the callee's window the stub's `a8..a23`, so
  the stub keeps `a12..a15`, which call0 preserves, in its 64-byte frame
  (`rom_call` holds each across a callee that writes them); its `a0..a7`,
  its return address among them, are a call8 caller's, which the windowed
  callee keeps. The 48 registers outside the stub's window are the callee's
  to clobber, so the kernel keeps nothing there (`rom_call` calls from
  WINDOWBASE 1 too, with its caller's stack pointer kept in memory), and the
  next resume reloads all 64.
- **Spills.** The stub's frame is a call8 window, so the six handlers spill
  and fill it like any other when the callee nests deep enough: the stub
  writes its top into the word at its `a1` less 12, which is where its spill
  reads its caller's stack pointer, so its extra save area is bytes 32..47
  of its own 64. A fault in such a spill or fill is the kernel's (PS.UM = 0)
  and stops the machine at the double vector. An Alloca exception under the
  stub (a callee's `movsp` with its caller spilled) arrives at the kernel
  vector, which handles it as the user vector does, with the faulting
  window's `a0` parked in `EXCSAVE1`, which the kernel does not otherwise
  use. The PLL path's ROM functions nest at most four `callx8` frames below
  the stub and execute no `movsp`, `rotw`, `rsil`, `waiti` or PS write (read
  from the ROM's disassembly); neither case is reached by them.
- **The return.** `retw` takes the top two bits of its target from its own
  PC, so the stub sits where they are 01, as IRAM is.

The cases: `rom_call` (the ROM's `crc32_le`; a chain deep enough to spill and
fill the stub's frame; `a12..a15` held; six arguments; PS inside the call
and after it; the Alloca exception under the stub; the chain and the Alloca
again from WINDOWBASE 1; the ROM again with every WINDOWSTART bit set, which
is set again after), `rom_spill_fault` (a spill fault under the stub stops
the machine at 0x3C0) and `rom_call_refused`.

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

### 9a. On the emulator

**GPIO** (`esp32.gpio`, 0x6000_4000) models one register. Probed:
`GPIO_STRAP` (`+0x38`, esptool's `GPIO_STRAP_REG = 0x60004038`) reads
`0x00000004`. Every other register tried reads 0 before and after a write:
`ENABLE` (`+0x20`), `OUT` (`+0x04`), `OUT_W1TS`/`OUT_W1TC`, `IN`/`IN1`
(`+0x3C`/`+0x40`), `PIN2` with a rising-edge interrupt enabled (`+0x7C`),
`STATUS` (`+0x44`, still 0 after toggling the output), `FUNC2_OUT_SEL`
(`+0x55C`). The second bank is the same: `OUT1` (`+0x10`) and `ENABLE1`
(`+0x2C`) read 0 after a plain write of `0x600` and after the same word
written to `OUT1_W1TS` (`+0x14`) or `ENABLE1_W1TS` (`+0x30`). So an output
write goes nowhere, an input reads 0, no GPIO interrupt is raised, and no
read of a level or an enable shows what was written to it. An LED cannot be
observed under this emulator.

**The IO MUX** (0x6000_9000), `PERIP_CLK_EN0` and `PERIP_RST_EN0` read 0
before and after a write, as GPIO does.

**LEDC** (silicon's PWM, 0x6001_9000) has no device: the address falls in
`esp32s3.iomem`, `LEDC_CONF` (`+0xD0`) and `CH0_CONF0` (`+0x00`) drop writes
and read 0, and `-d unimp,guest_errors` logs nothing for them. RMT is hidden
the same way (§3). No PWM device exists here.

### 9b. The blocks, and where each number is from

Every number in 9b to 9f is from ESP-IDF's `soc` headers for the chip
(`components/soc/esp32s3/`: `reg_base.h`, `gpio_reg.h`, `gpio_struct.h`,
`gpio_sig_map.h`, `io_mux_reg.h`, `ledc_reg.h`, `ledc_struct.h`,
`system_reg.h`, `rtc_io_reg.h`, `soc_caps.h`, `soc.h`; at v5.4.1 the register
headers are under `register/soc/` and the interrupt sources in
`interrupts.h`, at v5.1 all under `include/soc/` with the sources in
`periph_defs.h`), from its HAL (`components/hal/esp32s3/include/hal/`:
`gpio_ll.h`, `ledc_ll.h`, `clk_gate_ll.h`) and LEDC driver (`ledc.c`, under
`components/esp_driver_ledc/src/` at v5.4.1 and `components/driver/ledc/` at
v5.1), or from Espressif's `esp32s3.svd` for a field's meaning and reset
value. Each was read at both tags, and they agree but where 9f says. The
words "as the part leaves them" are §15c's.

```
block      base         registers used   the board's device
GPIO       0x6000_4000  0x004..0x62B     GPIO, line 16
IO MUX     0x6000_9000  0x004..0x0C7     IO_MUX
LEDC       0x6001_9000  0x000..0x0D3     LEDC, line 35
SYSTEM     0x600C_0000  0x018, 0x020     SYSTEM
```

Each is a 4 KiB window. The interrupt matrix's sources, counted from the
header's enumeration: `ETS_GPIO_INTR_SOURCE` is 16, a level; LEDC's is 35.
The same count gives UART0 27 and SYSTIMER target 0 57, the two §6c found by
probing. SYSTEM's next block, SENSITIVE, is at 0x600C_1000; SYSTEM also holds
the CPU's clock registers (§14).

### 9c. The IO MUX

Pad n's word is at `0x004 + 4n`, n 0 to 48; the header's named pads agree (1
at 0x08, 14 at 0x3C, 21 at 0x58, 41 at 0xA8 `MTDI`, 42 at 0xAC `MTMS`, 47 at
0xC0 `SPICLK_P`).

```
bit  15     FILTER_EN   filters pulses shorter than two cycles of a clock the
                        header does not name: nanoseconds, not contact bounce
bits 14:12  MCU_SEL     the pad's function; GPIO is function 1 on every pad
                        (PIN_FUNC_GPIO); pads 19 and 20 want more (9d)
bits 11:10  FUN_DRV     drive: 0 ~5 mA, 1 ~10 mA, 2 ~20 mA, 3 ~40 mA (SVD);
                        on pads 17 and 18 the two bits are the other way
                        round, 1 ~20 mA and 2 ~10 mA
bit   9     FUN_IE      input enable
bit   8     FUN_PU      pull-up
bit   7     FUN_PD      pull-down
bits  6:0   the sleep copies
```

The words a driver writes: an input with its pull-up `0x1B00`, with its
pull-down `0x1A80`, floating `0x1A00`; an output `0x1800`; an output whose
input is read back (a servo's pad, a loopback) `0x1A00`. Each is function 1,
~20 mA. On pads 17 and 18 each has its two drive bits the other way round
(an output `0x1400`, a servo's pad `0x1600`): v5.4.1's
`gpio_ll_set_drive_capability` swaps them there ("DRV = 1 and DRV = 2
register bits are flipped for IO17, IO18 on the target"); v5.1's does not.

As the part leaves them: pad 14 `0x0A00` (function 0, IE, 20 mA, no pull),
pad 21 `0x0800` (function 0, no IE), pads 41 and 42 `0x0A00` (function 0,
which on these two is JTAG, IE, no pull), pad 47 `0x1A00` (function 1, IE).
No pad read has a pull, and the SVD's single reset word for the array,
`0x0B00`, is not what the part shows. Pads 14, 21, 41 and 42 are not GPIO
until a driver writes `MCU_SEL` 1, and a line on 41 or 42 floats until then.

GPIO 0 to 21 are also RTC pads. The part reads RTC pad 21's `MUX_SEL`
(`RTC_IO_RTC_PAD21_REG` bit 19) as 0, so the digital IO MUX has the pad; the
word is `0x5000_0000`, `DRV` 2 and `RDE` 1, the RTC side's pull-down at its
default. The digital IO MUX's pulls are its own on this chip
(`SOC_GPIO_SUPPORT_RTC_INDEPENDENT`), and ESP-IDF sets a digital pad's pulls
through the IO MUX alone (`gpio_ll_pullup_en`). Whether `RDE` still loads the
pad while `MUX_SEL` is 0 is not stated anywhere here.

### 9d. GPIO

```
off    register             use
0x004  OUT / 0x008 W1TS / 0x00C W1TC         output level, pins 0-31
0x010  OUT1 / 0x014 W1TS / 0x018 W1TC        pins 32-53
0x020  ENABLE / 0x024 W1TS / 0x028 W1TC      output enable, pins 0-31
0x02C  ENABLE1 / 0x030 W1TS / 0x034 W1TC     pins 32-53
0x038  STRAP                (read-only)
0x03C  IN / 0x040 IN1       pad input, pins 0-31 and 32-53 (read-only)
0x044  STATUS / 0x048 W1TS / 0x04C W1TC      interrupt status, pins 0-31
0x050  STATUS1 / 0x054 W1TS / 0x058 W1TC     pins 32-53
0x05C  PCPU_INT             status of pins enabled for the CPU, 0-31 (RO)
0x068  PCPU_INT1            the same, 32-53 (RO)
0x074  PINn                 0x074 + 4n: pin n's interrupt configuration
0x554  FUNCn_OUT_SEL_CFG    0x554 + 4n, n 0-53: pad n's output signal
0x62C  CLOCK_GATE           bit 0 the GPIO clock, reset 1
```

`PINn`: `INT_ENA` (bits 17:13), whose bit 13 is the CPU's interrupt and bit
14 the CPU's NMI (`GPIO_LL_INTR_ENA`, `GPIO_LL_NMI_INTR_ENA`, bits 0 and 1 of
the field, at both tags); `INT_TYPE` (bits 9:7), 0 off, 1 rising, 2 falling, 3
any edge, 4 low level, 5 high level (`gpio_types.h`); `PAD_DRIVER` (bit 2),
open drain. `PCPU_INT` and `PCPU_INT1` are both cores' status on this chip;
`STATUS_W1TC` and `STATUS1_W1TC` clear it (`gpio_ll_clear_intr_status`). The
source is the level `PCPU_INT & INT_ENA`, which falls when the status is
cleared: the kernel's mask on delivery and rearm on ack (spec §9) serve it
as they are.

`FUNCn_OUT_SEL_CFG`, reset `0x100`: `OUT_SEL` (bits 8:0) the signal, 256 the
plain GPIO signal (`OUT`/`ENABLE`); `OUT_INV_SEL` (bit 9); `OEN_SEL` (bit
10), whose 1 takes the output enable from `ENABLE[n]` and whose 0 from the
signal's peripheral (SVD); `OEN_INV_SEL` (bit 11). LEDC's channels 0 to 7
are signals 73 to 80 (`LEDC_LS_SIG_OUT0_IDX`). So a plain output's select is
`0x500`, LEDC channel 0's `0x449`. At function 1 with the select at `0x100`
a pad is ESP-IDF's plain GPIO output: its data from `OUT`, its enable from
`ENABLE` (`gpio_ll_output_enable` sets the `ENABLE` bit; the `0x100` is
v5.1's `gpio_ll_output_disable`, and at v5.4.1 the same write is
`gpio_ll_matrix_out_default`).

The valid GPIOs are 0 to 48 but 22 to 25, and every one can drive
(`SOC_GPIO_VALID_GPIO_MASK`, `SOC_GPIO_VALID_OUTPUT_GPIO_MASK`). 26 to 32 are
the SPI bus the code runs from (`SPICS1`, `SPIHD`, `SPIWP`, `SPICS0`,
`SPICLK`, `SPIQ`, `SPID`) and 43 and 44 UART0's (`U0TXD`, `U0RXD`), the
console. GPIO 26 to 48 are powered from VDD_SPI.

Pads 19 and 20 are the USB-Serial-JTAG's D- and D+
(`USB_INT_PHY0_DM_GPIO_NUM`, `USB_INT_PHY0_DP_GPIO_NUM`). ESP-IDF's
`gpio_ll_func_sel` clears `USB_SERIAL_JTAG_USB_PAD_ENABLE` before it selects a
function on either, at v5.1 and at v5.4.1, and its `gpio_ll_pullup_dis`
overrides pad 20's D+ pull-up. The drivers here write no USB_SERIAL_JTAG
register, so they refuse 19 and 20 as held by the chip, as they do the
console's pads, and the IO MUX word alone never configures either.

**The order that drives no level nobody wrote.** A plain output: its enable
cleared, its level, its output select, its IO MUX word, then its enable set.
The select comes before the IO MUX word: with the enable clear and `OEN_SEL`
1 the select drives nothing, while a pad given function 1 under another
select would. A pad routed to a peripheral: its select to `0x100`, its IO
MUX word, then its select to the signal. ESP-IDF writes the IO MUX function
first: v5.1's `ledc_set_pin` selects the GPIO function, sets the direction
(`gpio_set_direction`, whose `gpio_output_enable` connects
`SIG_GPIO_OUT_IDX`), then connects the signal; v5.4.1's `_ledc_set_pin`
selects the function, reserves the pin (`esp_gpio_reserve`), then connects
the signal. The signal last is ESP-IDF's order; the select to `0x100` first
is this tree's, for the plain output's reason: `gpio_ll_iomux_out` writes
`OEN_SEL` for a pad in another function, so the enable select is live for a
pad that is not yet GPIO, and a pad given function 1 under a select left by
an earlier run could drive.

### 9e. LEDC, and its clock

LEDC has low-speed mode only: 8 channels, 4 timers, a 14-bit counter
(`SOC_LEDC_CHANNEL_NUM`, `SOC_LEDC_TIMER_BIT_WIDTH`,
`SOC_LEDC_SUPPORT_XTAL_CLOCK`).

```
off           register         fields
0x00 + 0x14c  CHc_CONF0        1:0 TIMER_SEL, 2 SIG_OUT_EN, 3 IDLE_LV,
                               4 PARA_UP (write-only)
0x04 + 0x14c  CHc_HPOINT       13:0
0x08 + 0x14c  CHc_DUTY         18:0; the low 4 bits are a fraction
0x0C + 0x14c  CHc_CONF1        31 DUTY_START, 30 DUTY_INC, 29:20 DUTY_NUM,
                               19:10 DUTY_CYCLE, 9:0 DUTY_SCALE
0x10 + 0x14c  CHc_DUTY_R       18:0 the duty in force (read-only)
0xA0 + 8t     TIMERt_CONF      3:0 DUTY_RES, 21:4 CLK_DIV (10.8 fixed
                               point), 22 PAUSE, 23 RST (resets to 1),
                               24 TICK_SEL, 25 PARA_UP (write-only)
0xA4 + 8t     TIMERt_VALUE     13:0 the counter (read-only)
0xD0          CONF             1:0 APB_CLK_SEL, 31 CLK_EN
```

Channel 8's `CONF0` slot would be `TIMER0_CONF`, so a channel is checked
before an address is formed from it. `APB_CLK_SEL` selects every timer's
clock: 1 APB_CLK, 2 RC_FAST_CLK, 3 XTAL_CLK; its reset 0 selects nothing, and
ESP-IDF selects one before it touches a channel (`ledc.c`). `TICK_SEL` 0 is
that clock. `IDLE_LV` is the output while `SIG_OUT_EN` is 0, and is not
among the fields `PARA_UP` latches (SVD). A fixed duty
(`ledc_set_duty_with_hpoint`, `_ledc_update_duty`) is `HPOINT`, `DUTY` =
counts << 4, `CONF1` = `0xC010_0400`, then `CONF0` with `SIG_OUT_EN |
PARA_UP`; a stop (`ledc_stop`) is `CONF1` with `DUTY_START` clear and `CONF0`
with `SIG_OUT_EN` clear and `PARA_UP`. A new setting takes effect at the next
PWM cycle, so an update never cuts a pulse short.

**The clock.** XTAL_CLK is the 40 MHz crystal (`XTAL_CLK_FREQ`; esptool
reported a 40 MHz crystal on the part), and it reaches the peripherals
undivided under every clock key: UART0 runs from it at the ROM's divisor
under `rom`, `xtal40` and `pll80` alike, and SYSTIMER counts it at 16 MHz
(§14). APB follows the CPU's clock (20, 40 or 80 MHz by key), so a divider
computed on APB would change the frame with the key. On XTAL_CLK, 14 bits
and a frame of F us, the divider field is `F * 5 / 8`: 12500 for 20 ms,
exactly. The field runs from 256 (1.0) to 0x3FFFF, a frame of 409.6 us to
419 428 us. LEDC on XTAL_CLK while APB runs at 20 MHz (`rom`) is a
combination ESP-IDF never runs; no source forbids it.

**SYSTEM's gate.** `PERIP_CLK_EN0` (0x018) bit 11 is `LEDC_CLK_EN` and
`PERIP_RST_EN0` (0x020) bit 11 `LEDC_RST`: enabling LEDC is setting the
first and clearing the second (`periph_module_enable`, `clk_gate_ll.h`). The
part reads `0xf9c1e06f` and `0x00000000`: LEDC gated, as the SVD's reset
value says, and not held in reset. Both words gate every peripheral on the
chip (bit 1 the flash's SPI, bit 2 UART0, bit 29 SYSTIMER), so a set or a
clear of one bit reads the word first; a plain store would turn the flash,
the console and the tick off.

### 9f. What rests on one source

- **The SVD alone:** the drive strengths, but for pads 17 and 18's swapped
  bits, which are v5.4.1's HAL alone (9c);
  the meaning of `OEN_SEL`, `IDLE_LV`, `CLK_EN` and `TICK_SEL`; which fields
  `PARA_UP` latches. The SVD's `PERIP_CLK_EN0` reset value is confirmed on
  the part; its IO MUX reset value is contradicted by the part for every pad
  read.
- **ESP-IDF's HAL alone:** the `INT_TYPE` values, the LEDC sequences, and
  that a plain output's enable is `ENABLE[n]` with its select at `0x100`.
  The function that writes the `0x100` differs by tag (9d).
- **Header defaults not read on the part:** `OUT1` and `ENABLE` reset to 0.
- **No source:** whether LEDC drives the peripheral output enable that
  `OEN_SEL` 0 selects; what a pad in function 0 drives on 41 and 42; which
  clock `FILTER_EN` counts; whether RTC pad 21's `RDE` loads the pad while
  the digital IO MUX has it.

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
- **The FPU, `BR` and MAC16.** *Here:* `wfr` with `CPENABLE = 0` raises
  cause 32 (§5c), at the user vector with EPC1 the instruction, and the
  smoke's FP cases run the whole first-use path on it: f0..f15, `FCR`'s
  rounding mode and `FSR`'s flags hold what is written and survive the save
  and the load. `rsr br` executes with `CPENABLE = 0`, with no trap, and
  reads the last value written, another process's included: probed by a
  thread whose first instruction read `BR`, which found the value a thread
  of another process had left and took the first-use trap only at its next
  instruction, an `rfr`. That is why the trap frame carries `BR`, and
  MAC16's registers and `MISC0..3` with it (§7c), which no coprocessor gates
  either. *Prepared for:* that `BR` and MAC16 are outside coprocessor 0 on
  the part too, as the ISA's Boolean and MAC16 options have them and
  ESP-IDF's frame assumes. *Unprobed on silicon:* cause 32 itself, which of
  `FCR`'s and `FSR`'s bits are writable, and whether `rur fcr` and `rur fsr`
  trap with `CPENABLE = 0`; the save and the load set bit 0 before either,
  so the answer changes nothing the kernel does.
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
  flash 0x10_0000, a child's from 0x18_0000 and the kernel's read-only data at
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
- **`waiti`.** The kernel sleeps in `waiti 0` when idle (§6d). Here it
  wakes for the tick's interrupt and a device line's, and takes each at the
  kernel vector with EPC1 the instruction after it. A deadline already
  behind the counter is not slept on: the poll delivers it without a wake
  (§6d). *On silicon (rev v0.2, ROM `esp32s3-20210327`):* `idle_wake` ran to
  its end with no fault line, UART0's line woke the `waiti`, and a deadline
  armed 1 ms ahead was behind the counter by the first wait, which read `did
  not sleep`, in each of three boots; `kill_churn` at `pll80`, which never
  idles in its rounds, ran to its end.
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
- **The PSRAM's bus.** The emulator models the octal chip's mode registers
  and array, the eFuse word from a file and the flash's chip select (§16e),
  but not SPI0, the IO MUX, the GPIO matrix, line modes, dummy cycles or
  any clock: a command finishes inside the store that starts it, and SPI1's
  MISC reads 0 at boot where a part reads `0x00000002` (§15c). The system
  and CPU clock words read the PLL at 320 MHz under every key (§14e).
  *Prepared for:* the bring-up's description carries the emulator's clock
  words in an image for the emulator and the part's in a board image
  (§16a, §17b); every wait is bounded and every stop is reached by the
  fault build (§16e). *On silicon (rev v0.2, ROM `esp32s3-20210327`):* S0
  to S10 and the measurement, stages P1 and P2 (§16f). *Unprobed on
  silicon:* SPI0's PSRAM side, the map and the read-back test, stages P3 on
  (§17f).
- **The PSRAM's cache.** Here the cache's model translates every access to
  an MMU entry with the PSRAM bit straight into the chip's memory, whatever
  SPI0 holds: there is no cache in between, nothing is evicted or written
  back, a rewritten entry takes effect at the next access, and EXTMEM's
  access and miss counters read 0 (§17d). On a part every PSRAM access goes
  through the data cache, a miss is a burst on the memory-SPI bus at the
  divider S4 set, a dirty line is written back when evicted, and the
  instruction cache fills from the chip through the same table.
  *Prepared for:* the read-back test spans many times the largest data
  cache, so on a part its reads are fills from the chip (S14); stage P3
  evicts the one word it reads back, and P4 evicts the word it loads through
  the instruction bus (§17f). *On silicon (rev v0.2, ROM
  `esp32s3-20210327`):* stages P3, P4, P5 and P7 at `pll80` printed §17f's
  text: the word read back after its eviction, the instruction-bus alias
  read from the chip, and the read-back test passed over 8 MiB.
  *Unprobed on silicon:* the other clock keys.
- **`s32c1i` on the PSRAM.** Here it swaps a PSRAM word as it does a DRAM
  word (`psram_probe`). On a part the PSRAM is external memory behind the
  cache. ESP-IDF never uses `s32c1i` there: v5.4.1's
  `esp_cpu_compare_and_set` (esp_hw_support/cpu.c) takes an interrupts-off
  lock in internal RAM for an external-RAM address, "the native CAS
  instruction cannot be used". That rule dates from the ESP32, whose
  PSRAM "hardware ... does not support this operation" (the FreeRTOS port's
  `uxPortCompareSetExtram`, ESP-IDF v3.0), and was carried into the
  architecture-wide code every Xtensa target with PSRAM takes; the ESP32-S3
  errata name no fault of atomics or of `s32c1i`. *Prepared for:* stage
  P8 (§17g) races `s32c1i` increments on PSRAM words from four threads
  under a 1 ms tick, with the data cache made to evict and refill their
  lines between bursts and swaps made on evicted lines, beside a plain
  increment that must lose counts; the `esp32s3-r8` board's PSRAM range
  states word atomics on its result, so the image builder may place a
  process or a heap's pool there (§17e). *On silicon (rev v0.2, ROM
  `esp32s3-20210327`):* P4's `s32c1i` at 0x3d000010, under ATOMCTL 0x15,
  answered the old word 5 and left 7, on the one core the kernel runs; P8
  at `pll80` and `pll240` kept every count exact while the plain control
  lost 1296 and 1535 of 16384, with about 196,800 PSRAM misses at each
  (§17g's table). *Unprobed on silicon:* any second core, and a bus master
  other than the core (DMA) writing a word a `s32c1i` reads.
- **A buffer in PSRAM under the kernel's copy doors.** A heap's pool, or a
  process's region, may be in the PSRAM (§17e), so a buffer a process hands the
  kernel may be too. The doors (`copy_in`, `copy_out`) move a record or a
  message body one byte at a time with interrupts masked, and a byte in a line
  the data cache does not hold costs a fill from the chip, a burst on the
  memory-SPI bus (the cache bullet above). What bounds the masked time is the
  door's length: the longest single copy from a process's buffer is a message
  body, at most `PIPE_BODY_BYTES` (128, `kernel/abi/src/ops.saw`), copied by
  `Pipe` send, post and call; a panic's text is read through the same door in
  chunks of 32 bytes, at most `PANIC_TEXT_BYTES` (256) in all, with the console
  written between them. The records the other ops copy are a few words. A bulk
  copy, the loader's of an image, moves no process's buffer and strides with
  preemption points (`kcore.preempt`). So a PSRAM buffer adds, to a door's
  masked time, a fill for each line its bytes touch, at most the lines 128 or
  256 bytes cover. *Prepared for:* stage P9 (§17f), the door's byte loop over a
  PSRAM buffer whose lines a walk of four times the cache has evicted, against
  the same loop over SRAM, at `pll80` and `pll240`. *On silicon (rev v0.2):* P9
  at both keys, in cycles of the CPU clock, the fewest and the most of 8 rounds
  where they varied:

```
                                       pll80          pll240
copy_in 32, PSRAM evicted              540-1869       908-6893
copy_in 32, SRAM                       370-371        370-371
copy_in 128, PSRAM evicted             2106           3410
copy_in 128, SRAM                      1426           1426
copy_in 256, PSRAM evicted             4194           6746
copy_in 256, SRAM                      2834           2834
copy_in 128, PSRAM lines held          1426-1427      1426-1427
copy_out 128, PSRAM evicted            2074           3360
copy_out 128, SRAM                     1426           1426-1428
```

  The clock lines read sysclk 0x00080400, cpu_per 0x4 and 0x6, and the cycles
  convert at the key's clock, 80.00 and 240.02 MHz. The SRAM loop costs about 11
  cycles a byte, the same at both clocks, so SRAM takes 3 times as long at 80
  MHz as at 240. A body of 128 bytes from evicted PSRAM adds 680 cycles at 80
  MHz and 1984 at 240, 8.5 and 8.3 us: the fill time is the memory-SPI bus's,
  not the CPU's. In time, the masked copy of a body is 26 us from evicted PSRAM
  against 18 us from SRAM at 80 MHz, and 14 us against 6 us at 240; a panic's
  256 bytes read in chunks are 52 us against 35 us at 80 MHz and 28 us against
  12 us at 240 (the console's writes between chunks come on top). A 32-byte
  chunk from evicted PSRAM varied between 7 and 23 us at 80 MHz and 4 and 29 us
  at 240; the harness does not say which round or what made the largest.
- **The flash's reads.** The emulator's flash is a 4 MiB GigaDevice model
  with no SFDP and no status register 2, and its SPI1 reads MISC 0 at boot
  (§18b); the part's flash is an 8 MiB XMC part by esptool's ID, and its
  MISC reads `0b10`. *Prepared for:* a read compares MISC(1) with `0b10` and
  never writes it, so the emulator's images store the part's word first and
  a board image does not (§18b); the probe prints every word it reads, and
  reads past `9Fh` only a flash of the part's ID (§18c). *On silicon (rev
  v0.2, ROM `esp32s3-20210327`):* F0 read the part's ID, status registers
  and SFDP tables and put SPI1 back (§18c).

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
0x18_0000  0x4218_0000  0x3C18_0000  child 0's image slot (128 KiB)
0x1A_0000  0x421A_0000  0x3C1A_0000  child 1's, then 2's and 3's
0x20_0000  0x4220_0000  0x3C20_0000  the kernel's .rodata, the region table

RAM
IRAM 0x4037_8000 - 0x4037_BFFF  kernel vectors + trap path
DRAM 0x3FC8_C000 - 0x3FCA_FFFF  kernel .data + .bss (arena, 16K stack)
DRAM 0x3FCB_0000 - 0x3FCC_7FFF  root's region: writable data + arena, the
                                top 16 KiB its stack
DRAM 0x3FCC_8000 - ...          the other processes' regions, the same shape
```

A slot is one of the `count` equal parts of the board's slot range
(`board.toml`'s `[flash] slots`), and child i of an image takes slot i, so an
image has at most `count` processes besides root; the builder refuses one with
more. The image builder writes the slots for each image into the
`image_slots.ld` the kernel's script INCLUDEs (`tools/sosimage/src/layout.saw`).
The kernel's read-only data begins at the end of the slot range, behind every
slot, so it lies at one offset whatever an image's process count, and a slot
an image does not use is erased flash.

A process's region is the RAM it takes: on this board, where code and
read-only data stay in flash, its writable segment (`.data`, `.bss`, the
arena) rounded up to 16 bytes, with the 16 KiB stack on top. That is the
in-place case of the rule a copied board's region follows with every segment
in it. A test image's regions are the runner's; for one built from a
description the builder measures each process by a first link and places the
regions one after another from root's top (`tools/sosimage/src/layout.saw`).

`kernel/esp32s3.ld` places each part and fails the link if the kernel's code
reaches root's slot or an image overruns its slot. The kernel holds every
image by its data-window address: the payload bounds, a region table's blob
rows, and so every read the loader makes of a header, a record or a
segment's bytes. Its own reads of flash are all through the data window, so
it makes none of the byte loads in the instruction window that a part
refuses (§3c).

Each image is a Blade-built sosimg of three segments: a 24-byte header, three
24-byte records, then the segments' bytes in order on 4-byte boundaries.
`user/user.ld`, the one script every process is linked through, links to
that packing:

```
slot + 0x60          code and literal pools, R+X: linked at the slot's
                     instruction-window address plus 0x60, used in place
right behind it      read-only data, R: linked at the data-window address of
                     its own bytes, used in place
the region's base    .data, .bss, the arena, R+W: DRAM, copied
the region's top     the stack the kernel grants, 16 KiB, DRAM
```

The build holds the link addresses to the bytes three times: the script's
`ASSERT`s (the code at the slot plus 0x60, the read-only data right behind
it, each of the three sections non-empty, since an empty one would take a
record out of the table), the image builder, which reads each image back out
of the flash image it built and refuses the build when a segment is not
linked at its own bytes (`check_in_place` in `tools/sosimage/src/flash.saw`,
which names both addresses), and the loader, which uses a segment in place
only when `hal.same_bytes` says its link address names its bytes and would
copy every other one, but on this board refuses any segment it would copy to
an address outside process RAM (`hal.segment_bus_ok`): an executable segment
not in place, and a read-only or writable one linked in flash but not at its
own bytes.

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
RTC_CNTL_OPTIONS0_REG 0x6000_8000     soc/esp32s3/register/soc/rtc_cntl_reg.h
  BBPLL_FORCE_PD [10], BBPLL_I2C_FORCE_PD [8], BB_I2C_FORCE_PD [6]
                        R/W  reset 0   the BBPLL and its analog bus held
                                       powered down (clk_ll_bbpll_enable,
                                       clk_ll_bbpll_disable)
I2C_MST_ANA_CONF0_REG 0x6000_E040     soc/esp32s3/include/soc/regi2c_defs.h
  BBPLL_STOP_FORCE_HIGH [2], BBPLL_STOP_FORCE_LOW [3]
                                       the BBPLL's calibration held stopped
                                       or let run (regi2c_ctrl_ll.h)
  BBPLL_CAL_DONE [24]                  the calibration has finished
ANA_CONFIG_REG 0x6000_E044 [17:8]     a bit per analog block, holding its
                                       bus off; [17] is the BBPLL's
                                       (regi2c_defs.h, I2C_BBPLL_M)
```

The BBPLL's own registers are on the chip's internal analog bus, block 0x66
(`I2C_BBPLL`, soc/esp32s3/include/soc/regi2c_bbpll.h), reached through the
analog master at 0x6000_E000; ESP-IDF writes them only through the mask
ROM's `rom_i2c_writeReg` and `rom_i2c_writeReg_Mask` (below).

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
around the call in bootloader_clock_init.c). An application's `rtc_init`
sets the two LDOs from the eFuse at startup (`esp_rtc_init`, esp_system/port/
soc/esp32s3/clk.c), before `esp_clk_init` moves the CPU past the
bootloader's 80 MHz. Every step, in order, and what each key takes of it: the
B and R rows belong to every key that writes, the X rows are the crystal
path, `xtal40`'s, the S, P and Q rows the PLL path, `pll80`'s, `pll160`'s
and `pll240`'s, and the L rows `pll160`'s alone. The L rows are `rtc_init`'s
steps on the LDO block (I2C_DIG_REG) and the settings it computes for it,
not the whole function: the rest of it sets RTC_CNTL's own power and timer
words (RTC_CNTL_PVTMON_PU and the wake-up timers first; under
`pwrctl_init`, RTC_CNTL_REGULATOR_FORCE_PU, RTC_CNTL_DBOOST_FORCE_PU,
RTC_CNTL_FORCE_PU and RTC_CNTL_I2C_RESET_POR_FORCE_PD, among more), which
the CPU-frequency path does not make and no key takes.

```
step                     register, field = value              taken
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
R3 ANA_CONFIG_REG 0x6000_E044 [17:8] = 0x3FF                  pll80: yes, in
   (regi2c_ctrl_ll_i2c_reset), then [17] = 0                  the bring-up;
   (regi2c_ctrl_ll_i2c_bbpll_enable; soc/regi2c_defs.h)       xtal40: no
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
X6 only from 240 MHz, after X4: I2C_DIG_REG EXT_RTC_DREG,     no: xtal40 stops
   EXT_DIG_DREG lowered to the non_240m pair (regi2c), 40 us  on a CPU found at
                                                              240 (below)
X7 RTC_CNTL_DATE_REG.SLAVE_PD [18:13] = DEFAULT_LDO_SLAVE     no: see below
   0x7 (esp_hw_support/port/esp32s3/include/soc/rtc.h)
   then rtc_clk_cpu_freq_set_config itself (rtc_clk.c), when  no: power only
   the old source was the PLL and no BBPLL consumer holds
   it (s_bbpll_digi_consumers_ref_count), calls
   rtc_clk_bbpll_disable: RTC_CNTL_OPTIONS0
   BB_I2C_FORCE_PD, BBPLL_FORCE_PD, BBPLL_I2C_FORCE_PD set
-- rtc_init (rtc_init.c), in an application ------------------------------------
L1 I2C_DIG_REG (block 0x6D, host 1; soc/esp32s3/include/soc/   no: below
   regi2c_dig_reg.h) reg 13: XPD_RTC_REG [2] = 0,
   XPD_DIG_REG [3] = 0
L2 reg 5 EXT_RTC_DREG_SLEEP [4:0] = RTC_CNTL_DBIAS_1V10, 23    no: below
   (esp_hw_support/port/esp32s3/include/soc/rtc.h), then
   reg 4 EXT_RTC_DREG [4:0] = 23: "Reset RTC bias to default
   value (needed if waking up from deep sleep)"
L3 on a power-on reset only (`cali_ocode`, set by             no: below
   esp_rtc_init): the bandgap's OCode, from the eFuse
   (set_ocode_by_efuse) or measured (calibrate_ocode)
L4 rtc_set_stored_dbias: the four g_*_dbias_pvt_* values      yes: computed
   from the eFuse's calibration, or the defaults 28, 28,       the same way
   27, 27 (below)                                              (`s3dbias`)
L5 reg 4 EXT_RTC_DREG [4:0] = g_rtc_dbias_pvt_non_240m        pll160: yes,
                                                              after P7;
                                                              pll240: no, Q1
                                                              writes the field
L6 reg 6 EXT_DIG_DREG [4:0] = g_dig_dbias_pvt_non_240m        pll160: yes;
                                                              then 40 us
                                                              (Q1's wait);
                                                              pll240: as L5
-- rtc_clk_cpu_freq_set_config, the PLL path (rtc_clk.c) ----------------------
S1 P1 to P7 only when the old source, clk_ll_cpu_get_src,     yes: the same
   is not the PLL; then the Q rows                            test, on the
                                                              source read first
-- rtc_clk_bbpll_enable (rtc_clk.c, clk_tree_ll.h) -----------------------------
P1 RTC_CNTL_OPTIONS0 0x6000_8000: clear BB_I2C_FORCE_PD [6],  yes, in the
   BBPLL_I2C_FORCE_PD [8], BBPLL_FORCE_PD [10], each reset 0  bring-up
   (clk_ll_bbpll_enable)
-- rtc_clk_bbpll_configure(40, 480) -------------------------------------------
P2 CPU_PER_CONF.PLL_FREQ_SEL = 1 (clk_ll_bbpll_set_freq_mhz)  yes, in the
                                                              bring-up
P3 I2C_MST_ANA_CONF0 0x6000_E040: clear [2] STOP_FORCE_HIGH,  yes, two stores
   set [3] STOP_FORCE_LOW (regi2c_ctrl_ll_bbpll_calibration_
   start)
P4 the BBPLL's analog words, block 0x66, host 1, in order     yes: through the
   (clk_ll_bbpll_set_config(480, 40); soc/regi2c_bbpll.h):    ROM's functions,
     reg 4 MODE_HF = 0x6B                       writeReg      by way of
     reg 2 OC_REF_DIV = (DCHGP 5 << 4) | DIV_REF 0            sos_windowed_call
           = 0x50                               writeReg
     reg 3 OC_DIV_7_0 = 8                       writeReg
     reg 5 [2:0] OC_DR1 = 0                     writeReg_Mask
     reg 5 [6:4] OC_DR3 = 0                     writeReg_Mask
     reg 6 OC_DCUR = (DLREF_SEL 1 << 6)
           | (DHREF_SEL 3 << 4) | DCUR 3 = 0x73 writeReg
     reg 9 [1:0] OC_VCO_DBIAS = 3               writeReg_Mask
P5 poll I2C_MST_ANA_CONF0 [24] BBPLL_CAL_DONE                 yes, bounded
                                                              (below)
P6 esp_rom_delay_us(10)                                       yes: 400 CCOUNT
                                                              cycles, at least
                                                              10 us at 40 MHz
                                                              or less
P7 I2C_MST_ANA_CONF0: clear [3], set [2] (calibration_stop)   yes, after a
                                                              timeout too
-- rtc_clk_cpu_freq_to_pll_mhz(f), f = 80, 160 or 240 -------------------------
Q1 240 only, going up: I2C_DIG_REG (block 0x6D) reg 4         pll240: yes,
   EXT_RTC_DREG [4:0] = g_rtc_dbias_pvt_240m, reg 6           after P7, RTC
   EXT_DIG_DREG [4:0] = g_dig_dbias_pvt_240m (regi2c), 40 us; first, then
   the values come from eFuse calibration (rtc_init.c,        40 us; also on
   rtc_set_stored_dbias; 28 by default)                       a CPU found at
                                                              240 (below)
Q2 going up: RTC_CNTL_DATE_REG.SLAVE_PD = 0x7 >> (f / 80),    no: see below
   3 at 80, 1 at 160, 0 at 240
Q3 CPU_PER_CONF.CPUPERIOD_SEL = 0, 1, 2 for 80, 160, 240      yes: 0 at pll80,
   (clk_ll_cpu_set_freq_mhz_from_pll)                         1 at pll160, 2
                                                              at pll240, a
                                                              store from IRAM
Q4 SYSCLK_CONF.PRE_DIV_CNT = 0 (clk_ll_cpu_set_divider(1))    yes, a store
                                                              from IRAM
Q5 SYSCLK_CONF.SOC_CLK_SEL = 1 (clk_ll_cpu_set_src)           yes, a store
                                                              from IRAM
Q6 the APB variable (80 MHz), esp_rom_set_cpu_ticks_per_us    no: software
Q7 going down: the reverse of Q1 and Q2                       no: see below
```

ESP-IDF's REGI2C_WRITE and REGI2C_WRITE_MASK on this chip are
`esp_rom_regi2c_write` and `esp_rom_regi2c_write_mask`
(esp_hw_support/include/esp_private/regi2c_ctrl.h), which
esp_rom/esp32s3/ld/esp32s3.rom.api.ld provides as `rom_i2c_writeReg` and
`rom_i2c_writeReg_Mask`; esp32s3.rom.ld puts those at 0x4000_5D60 and
0x4000_5D6C, which are jump-table entries (`l32r a9; jx a9`) into bodies at
0x4003_5880 and 0x4003_58D8 that open with `entry a1, 32` and `entry a1,
48`: windowed functions. Read from the ROM's disassembly:
`rom_i2c_writeReg(block, host, reg, data)` calls, through the phy table,
`rom_chip_i2c_writeReg`, which takes the host from `rom_get_i2c_hostid(block)`
rather than its argument, waits for the analog master's busy bit (bit 25 of
the master's word at 0x6000_E000 plus 4 times the host), and writes the
command there; `rom_get_i2c_hostid` also sets ANA_CONFIG2_REG (0x6000_E048)
to `(word & 0xFFFE_000F) | 0x0001_FF40` on every call. `rom_i2c_writeReg_Mask
(block, host, reg, msb, lsb, data)` reads the register with `rom_i2c_readReg`
and writes it back with the field replaced. The read stores to ANA_CONFIG
(0x6000_E044) first: `rom_chip_i2c_readReg_org` (0x4003_54FC) writes the
complement of the block's read mask (`rom_get_i2c_read_mask`), which for the
BBPLL, bit 17, is 0xFFFD_FFFF, every analog block's bus held off but the
BBPLL's. So after each masked write of P4, and after the key's read of the
BBPLL's register 8, ANA_CONFIG holds that word, not the one R3 left.
ESP-IDF v5.4.1 has no replacement for these routines on this chip
(`esp_rom/patches` has none for it), so its applications leave the same
word. In a bootloader build ESP-IDF
calls them bare; an application wraps them in a critical section, which the
kernel, at INTLEVEL 15 with one core, has.

**The bring-up runs from IRAM** (`sos_pll_bringup`, `kernel/boot.S`): P1, R3
and P2 to P7, in that order. ESP-IDF makes R3 before P1: `rtc_clk_init` calls
`regi2c_ctrl_ll_i2c_reset` and `regi2c_ctrl_ll_i2c_bbpll_enable` before
`rtc_clk_cpu_freq_set_config` reaches `rtc_clk_bbpll_enable`. Both orders make
P1 and R3 before the analog bus's first use (P4), and the part locks in this
one (§14h). The ROM is reached through `sos_windowed_call`, which is in IRAM
too, so no instruction is fetched through the flash cache while the PLL is
powered, set and calibrated. Whether the memory-SPI core clock comes from the
PLL while the CPU is on the crystal is not in the sources (§14d's H readings),
and ESP-IDF keeps the code that powers the BBPLL down and up again in IRAM
"because disabling BBPLL may influence the cache"
(esp_system/port/soc/esp32s3/clk.c, `recalib_bbpll`); its bootloader runs the
whole sequence from RAM. The ROM functions run from the mask ROM, and the data
the bring-up reaches is the kernel stack and the ROM's phy table (§7g), neither
in flash. Once P7 is made the PLL is calibrated, and the key continues from
flash; Q3 to Q5, which move the CPU's clock, are each a store by
`sos_clock_store` from IRAM.

**The LDO settings (L4 to L6).** `rtc_set_stored_dbias` reads the block
version (BLK_VERSION_MAJOR, EFUSE_RD_SYS_PART1_DATA4 [1:0]; BLK_VERSION_MINOR,
EFUSE_RD_MAC_SPI_SYS_3 [26:24]) and, at versions 0.1, 1.1 and every one from
1.2 on (its test, word for word: `(major <= 1 && minor == 1) || major > 1 ||
(major == 1 && minor >= 2)`),
the calibration: K_RTC_LDO and K_DIG_LDO, each LDO's slope against its dbias
(SYS_4 [19:13] and [26:20]); V_RTC_DBIAS20 and V_DIG_DBIAS20, each LDO's
voltage at dbias 20 (SYS_4 [31:27] with SYS_5 [2:0] above it, and SYS_5
[10:3]); and DIG_DBIAS_HVT, the digital dbias found sufficient for 240 MHz
(SYS_5 [15:11]) (soc/esp32s3/register/soc/efuse_reg.h,
hal/esp32s3/include/hal/efuse_ll.h). With an HVT of 0, or any other version,
the values stay the defaults: 28 for both LDOs at 240 MHz and 27 below
it. Otherwise the digital value below 240 MHz is the smaller of HVT + 2 and
the least dbias from 15 whose digital voltage reaches 1.3 V (31 when none
does), and the RTC value the least dbias from 15 whose RTC voltage is within
25 mV below the digital one (31 when none is): `get_dig1v3_dbias_by_efuse`
and `get_rtc_dbias_by_efuse`, with the mid slopes and voltages of rtc.h
(`K_RTC_MID_MUL10000` 198, `K_DIG_MID_MUL10000` 211, `V_RTC_MID_MUL10000`
10181, `V_DIG_MID_MUL10000` 10841). `hal/xtensa-esp32s3/dbias/` (`s3dbias`)
computes the same; the image builder's tests hold it to that arithmetic,
case by case (`tools/sosimage/tests/s3_dbias.saw`).

`pll160` and `pll240` each hold all four values to the range ESP-IDF's own
arithmetic gives them before they write anything: the digital one below 240
MHz 3 to 31 (HVT 1 to 31, plus 2, and never past the search's 31), the
digital one at 240 MHz 4 to 31 (plus 3), each RTC one 15 to 31 (the searches'
first value and their answer when nothing is found). A value outside it comes
from no calibration and no default, so neither key trusts the other three;
the key prints its `not taken` line (§14d) and writes nothing. Inside it, the
key writes what ESP-IDF would write on the part, and nothing else checks the
voltage: a part's settings are read on it first (§15d).

`pll160`'s two writes are made after P7, where an application makes them
after its bootloader has brought the PLL up, and in `rtc_init`'s order, RTC
first. `pll240`'s are Q1's, the `240m` pair, made at the same place and in
the same order, which is also `rtc_clk_cpu_freq_to_pll_mhz`'s: ESP-IDF makes
Q1 inside that function, after `rtc_clk_cpu_freq_set_config` has run P1 to
P7, and before Q3. It does not make L5 and L6 first: in an application they
set the same two fields long before, and Q1 replaces both before the clock
moves, so on the way from the crystal the fields go from the ROM's settings
to the `240m` pair in one step each. Each
is a `rom_i2c_writeReg_Mask` of bits [4:0], so each reads its register first,
which stores ANA_CONFIG = 0xFFFF_BFFF: every block's bus held off but bit 14,
the one the ROM's read mask (`rom_get_i2c_read_mask`) names for this block;
the emulator runs the ROM's code and its trace shows the word. That word stays
after the switch, where `pll80`'s bring-up leaves 0xFFFD_FFFF; the BBPLL,
calibrated by then, is not reached again. The key then waits 40 us of SYSTIMER
before it moves the CPU's clock: `rtc_clk_cpu_freq_to_pll_mhz` waits that long
after it moves an LDO (Q1), and in an application `rtc_init` runs long before
the switch. The writes are made from flash, through `sos_windowed_call` in
IRAM, with the CPU on the clock it had; they move the digital core's voltage
and no clock.

**P5 is bounded.** ESP-IDF waits for CAL_DONE without a bound. The bring-up
waits at most 4,000,000 CCOUNT cycles, which with the CPU on the crystal at
40 MHz or less is at least 100 ms; no source states how long the
calibration takes. Past the bound it makes P7 and answers a timeout, nothing
switches to the PLL, and `pll80` prints its `not taken` line (§14d) and lets
the boot go on at the clock the ROM left. Every write made before the
timeout stays: P1's, R3's, P2's, P3's, P4's seven words, P7's, and the ROM's
own stores to ANA_CONFIG, ANA_CONFIG2 and the analog master.

**What is left out, and why that is safe:**

- X7's and Q2's SLAVE_PD. RTC_CNTL_DATE_REG's field resets to 0, all six LDO
  slaves on (rtc_cntl_reg.h, "LDO SLAVE ... default: 6'd0", and the register's
  default 0x2101271 has 0 in [18:13]); the ROM names the register nowhere, so
  it runs the chip with all six on, and the part reads 0x02101271 (§14h).
  ESP-IDF writes 0x7 on the crystal to power three down, 0x3 at 80 MHz to
  power two down and 0x1 at 160 to power one down ("CPU frequency <= 40M :
  just open 3 LDO slaves; CPU frequency = 80M : open 4 LDO slaves; CPU
  frequency = 160M : open 5 LDO slaves", `rtc_clk_cpu_freq_to_pll_mhz`).
  Slaves damp the LDO's voltage change at a switch, and ESP-IDF opens more of
  them the faster the CPU runs, so all six at 80 or 160 MHz is the side that
  cannot droop. At 240 MHz ESP-IDF writes 0x0 ("CPU frequency = 240M : open
  6 LDO slaves"), the field's reset value, which the part already holds. It
  is also an RTC register, which this kernel writes only for the watchdogs
  (§2e) and P1. Each line prints the word.
- L1, L2 and L3, `rtc_init`'s setup of the LDO block at every start, which
  the CPU-frequency path (`rtc_clk_cpu_freq_set_config`) does not make:
  L1's two XPD bits, which `rtc_init` clears whatever the speed; L2's sleep
  setting, which applies only in deep sleep ("needed if waking up from deep
  sleep"), and its 1.10 V for EXT_RTC_DREG, which L5 replaces in the same
  function; and L3's OCode, the bandgap's offset, calibrated on a power-on
  reset only. None of them is a step of the ramp, and leaving them leaves the
  block as the key finds it.
- R5's RTC_XTAL_FREQ_REG is a store word that survives every reset but a
  power cycle, read back by ESP-IDF and ROM functions this kernel never
  calls; and X1, X5, Q6, R7 keep software values in step.
- R1, R2, R8 configure the RTC's own oscillators and its fast and slow
  clocks, which neither the CPU, APB, SYSTIMER nor UART0 reads.
- X6 and Q7 apply only coming down from 240 MHz, which no key does (below);
  the BBPLL's power-down applies only when leaving the PLL. On the part the
  CPU starts on the crystal (the emulator's word says PLL, §14e).
- R3's first write holds off the buses of the blocks in ANA_CONFIG [17:8]
  (`ANA_CONFIG_S` 8, `ANA_CONFIG_M` 0x3FF, regi2c_defs.h); the ROM's read
  stores above hold off every other bit's too, the SAR ADC's (bit 18,
  `I2C_SAR_M`) among them. The kernel uses none of those blocks.
- X6 and Q7, by a stop. The keys are written for a CPU the ROM left on the
  crystal. On the kept path, a CPU already on the PLL, each stores its own
  CPUPERIOD_SEL whatever it read; a part reaches it only when something
  left the CPU on the PLL, as a CPU-only reset after a PLL key would
  (§14d). ESP-IDF comes down from 240 MHz in one order, the
  clock first and then both LDOs to the `non_240m` pair and 40 us
  (`rtc_clk_cpu_freq_to_pll_mhz` and `rtc_clk_cpu_freq_to_xtal`, rtc_clk.c).
  Without a stop, `pll80` and `xtal40` would lower the clock and leave the
  voltage at 240's, and `pll160` would make its two `non_240m` writes
  before the clock comes down, with the CPU still at 240 on the lower
  voltage. So each of the three reads the clock words first and, on a CPU at
  240 (SOC_CLK_SEL the PLL and CPUPERIOD_SEL 2, whatever PLL_FREQ_SEL
  holds), prints its `not taken` line (§14d) and writes nothing: no clock
  word, no LDO, not SYSTIMER's CONF; the boot goes on at 240. The stop is
  the smaller change of the two the order allows: one test of two words the
  keys already read, shared by the three in `s3clockkit` (`at_240`,
  `write_not_lowered`). Following Q7's order would give `pll80` and `xtal40`
  the eFuse reads and the two LDO writes they do not make today and
  `pll160` a second order for its writes, on a path the ROM never takes and
  the emulator, whose CPU_PER_CONF reads 0, cannot show. On the crystal
  CPUPERIOD_SEL clocks nothing, so a CPU found there is not at 240 whatever
  the field holds.
- Q1 on a CPU found at 240 MHz. `pll240` on the kept path writes Q1's two
  settings and waits whatever speed the CPU was found at: going up from 80
  or 160 that is ESP-IDF's Q1, and at 240 already, where ESP-IDF, not going
  up, writes nothing, it writes the pair ESP-IDF holds at 240. It moves no
  clock before the two writes, so the voltage is at 240's settings before
  CPUPERIOD_SEL is stored again. Nor does the kept path read PLL_FREQ_SEL:
  CPUPERIOD_SEL 2 with the PLL at 320 MHz is "an undetermined state"
  (`clk_ll_cpu_get_freq_mhz_from_pll`, clk_tree_ll.h), which ESP-IDF's
  `rtc_clk_cpu_freq_get_config` logs as unsupported and no key here tests
  for. Every bring-up sets 480 (P2),
  and the emulator's CPU_PER_CONF reads PLL_FREQ_SEL 0 on every run (§14e),
  so a stop there would refuse every emulator run of `pll240`.

### 14c. 160 and 240 MHz

`pll80` is the PLL's first speed, `pll160` its second and `pll240` its
third. `pll160` is the same sequence with CPUPERIOD_SEL 1 at Q3, after
`rtc_init`'s two LDO writes (L4 to L6), which an application makes before it
reaches 160 MHz and ESP-IDF v5.4.1's bootloader never makes for its 80 MHz.
(Earlier versions did: at v5.1 `rtc_clk_cpu_freq_to_pll_mhz` wrote both
LDOs, RTC first, and waited 40 us before every switch below 240 MHz, the
bootloader's to 80 included.) `pll240` is the same again with CPUPERIOD_SEL
2, and more:

- **The voltage.** 240 MHz raises both LDOs through I2C_DIG_REG to
  `g_rtc_dbias_pvt_240m` and `g_dig_dbias_pvt_240m`, RTC first, and waits
  40 us (Q1), after P7 and before Q3. `s3dbias` computes both pairs;
  `pll160` writes the `non_240m` pair and `pll240` the `240m` pair. On the
  board's part those are 28 and 29 for 160 MHz and 29 and 30 for 240, RTC
  then digital (§15d).
- **Going down.** ESP-IDF puts the `non_240m` values back when it leaves
  240 (X6, Q7). No key leaves it: each below 240 stops on a CPU found
  there, before it writes anything (§14b, "X6 and Q7, by a stop").
- **The slaves.** Q2 opens five slaves at 160 and six at 240; all six are on
  as the ROM leaves them, so nothing changes at either.

At every PLL speed **the flash** does not move: the memory-SPI core clock
follows CORE_CLK_SEL and the PLL, not CPUPERIOD_SEL (`spi_mem_reg.h`), so it
stays 80 MHz and the flash 20 MHz.

### 14d. What each key sets

The key is chosen per image at build time (`s3clock`, `keys/clock-*/`); `rom`
is the default everywhere. The case key `"clock"` sets it, and `--emit-flash
CASE --clock KEY` overrides it for a board image.

A product's image chooses its keys in its description's `[keys]`, each value
one of the key's in this board's `board.toml`:

```toml
[keys]
clock = "xtal40"
stall = "on"
```

The names and values are this board's: `board.toml`'s `[keys.clock]`,
`[keys.flash]`, `[keys.stall]` and `[keys.console]` list each key's module
(`s3clock`, `s3flash`, `s3stall`, `s3console`) and its values; the flash key
is §18d's and the console key §14i's. A value
names a directory: the module's path in `modules` with the value in place
of its `*`, so `xtal40` is `keys/clock-xtal40` and `on` is
`keys/stall-on`. `sosimage build` and `sosimage flash` refuse a key or a
value the board does not list, by name, and compile the kernel with each
stated key's module mapped to its value's directory (`keyed_profile` in
tools/sosimage/src/product.saw); a key the image leaves out keeps the
profile's directory, which in the profile `tools/product.py` writes is the
default, `rom`, `rom`, `off` and `rom`. So `tools/sawos.py build`, `run`, `test` and
`flash` all build a product's image at its keys, and the build prints them
first (`keys clock=xtal40`).

A key is stated in one place. The smoke's cases give theirs in the case
(`"clock"`, `"flash_key"`, `"console"`, `"console_stall"`) or by
`--emit-flash CASE --clock KEY`, and the runner refuses a case whose
hand-written description states `[keys]` while the case, or `--clock`, names
a key as well, rather than pick one
(`_build_image_file`, the smoke's `keys_stated_twice`). A description's
`[keys]` with a case that names none is built at the description's keys.

The keys that write share `s3clockkit` (`keys/clock-common/`): the console's
drain, the measurement, the line's writer, the stop on a CPU found at 240
MHz, the PLL keys' bring-up and switch, and the LDO writes `pll160` and
`pll240` make. Each starts SYSTIMER and takes one snapshot it throws away
before it measures (§14h).

```
key     registers written, in order                    from
rom     none                                           -
xtal40  nothing, if the clock words read have the CPU  X6, held off: §14b
        at 240 MHz (SOC_CLK_SEL 1, CPUPERIOD_SEL 2)
        SYSTIMER_CONF |= CLK_EN | UNIT0_WORK_EN        what the kernel's own
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
pll80   nothing, on a CPU found at 240 MHz, as xtal40  Q7, held off: §14b
        SYSTIMER and UART0 as xtal40                   as xtal40
        `sos_pll_bringup`, from IRAM, only if the      S1
        source read is the crystal:
          RTC_CNTL_OPTIONS0 &= ~0x540                  P1
          ANA_CONFIG |= 0x3FF << 8, then               R3
            ANA_CONFIG &= ~(1 << 17)
          CPU_PER_CONF |= 0x4 (PLL_FREQ_SEL 1)         P2
          I2C_MST_ANA_CONF0 &= ~0x4, then |= 0x8       P3
          the BBPLL's seven words, through the ROM     P4
          read I2C_MST_ANA_CONF0 until [24] is set,    P5
            at most 4,000,000 cycles
          400 cycles                                   P6
          I2C_MST_ANA_CONF0 &= ~0x8, then |= 0x4       P7
        on a timeout nothing below is written
        BBPLL register 8 read through the ROM          for the line
        CPU_PER_CONF = read & ~0x3 (CPUPERIOD_SEL 0)   Q3, from IRAM
        SYSCLK_CONF = first read & ~0x3FF              Q4, from IRAM
        SYSCLK_CONF = that & ~0xC00 | 0x400            Q5, from IRAM
          (SOC_CLK_SEL 1)
        RC_FAST or 3 stops before the bring-up and any clock word, as
          xtal40 does (SYSTIMER's rows and the first measurement have
          been made)
pll160  nothing, if the LDO settings computed from     L4, held to ESP-IDF's
        the eFuse are outside ESP-IDF's range; it      range first
        reads the four eFuse words first
        nothing, on a CPU found at 240 MHz, as pll80   Q7, held off: §14b
        SYSTIMER, UART0 and the bring-up as pll80      as pll80
        on a timeout nothing below is written
        BBPLL register 8 read through the ROM          for the line
        I2C_DIG_REG regs 4 and 6 read through the ROM  for the line
        I2C_DIG_REG reg 4 [4:0] = the RTC setting,     L5, through
          then reg 6 [4:0] = the digital one           rom_i2c_writeReg_Mask
          (the non_240m pair)
        SYSTIMER read for 40 us                        Q1's wait
        I2C_DIG_REG regs 4 and 6 read again            for the line
        CPU_PER_CONF = read & ~0x3 | 0x1               Q3, from IRAM
          (CPUPERIOD_SEL 1)
        SYSCLK_CONF as pll80                           Q4, Q5, from IRAM
pll240  nothing, if the LDO settings computed from     L4, held to ESP-IDF's
        the eFuse are outside ESP-IDF's range, as      range first
        pll160
        SYSTIMER, UART0 and the bring-up as pll80,     as pll80
          on a CPU found at 240 MHz too
        on a timeout nothing below is written
        BBPLL register 8 read through the ROM          for the line
        I2C_DIG_REG regs 4 and 6 read through the ROM  for the line
        I2C_DIG_REG reg 4 [4:0] = the RTC setting,     Q1, through
          then reg 6 [4:0] = the digital one           rom_i2c_writeReg_Mask
          (the 240m pair)
        SYSTIMER read for 40 us                        Q1's wait
        I2C_DIG_REG regs 4 and 6 read again            for the line
        CPU_PER_CONF = read & ~0x3 | 0x2               Q3, from IRAM
          (CPUPERIOD_SEL 2)
        SYSCLK_CONF as pll80                           Q4, Q5, from IRAM
```

Each of the ROM's analog-bus calls also makes the ROM's own writes (§14b):
ANA_CONFIG2 (0x6000_E048) through `rom_get_i2c_hostid`, the command word of
the analog master at 0x6000_E000 plus 4 times the host, and, for each read
(each masked write, and the line's read of register 8), ANA_CONFIG
(0x6000_E044) = 0xFFFD_FFFF.

Clocks before and after, for each reading of the memory-SPI core clock on
the crystal, which no source states and no run has measured: H1 the
crystal, H2 a PLL-derived 80 MHz (the core clock ESP-IDF's low-speed flash
mode assumes, `FLASH_LOW_SPEED_CORE_CLOCK_MHZ` in esp_hw_support/port/
esp32s3/mspi_timing_config.c), H3 the system clock after the pre-divider.
On the PLL there is one reading: CORE_CLK_SEL 0 with the PLL at 480 MHz is
80 MHz (`SPI_MEM_CORE_CLK_SEL`, soc/esp32s3/register/soc/spi_mem_reg.h).
The flash's divider is the ROM's: `spi_flash_boot_attach` (0x4004_A8CC)
calls `SPI_init(5, 4)` (0x4004_A4A8), which stores one word to SPI1's and
SPI0's SPI_MEM_CLOCK_REG (0x6000_2014 and 0x6000_3014, its pool at
0x4004_A480), divide by 4. No key writes it. SYSTIMER counts the crystal at
16 MHz under every key.

```
                 CPU        APB        UART0                flash
rom              20         = CPU      crystal, 115,212 bd  H1 10, H2 20,
                                                            H3 5 MHz
xtal40           40         40         crystal, 115,212 bd  H1 10, H2 20,
                                                            H3 10 MHz
pll80            80         80         crystal, 115,212 bd  20 MHz
pll160          160         80         crystal, 115,212 bd  20 MHz
pll240          240         80         crystal, 115,212 bd  20 MHz
```

The console stays at the ROM's 115200 for every key because its clock is
the crystal, which no key moves, and its divisor is the ROM's; each key
waits for the transmitter to go idle before its stores and prints nothing
until after them. Flash execution through the cache stays valid because
the divider, the read mode and the dummy cycles the ROM set are untouched
and the flash's clock after the switch is at most 20 MHz in every reading:
ESP-IDF tunes the timing of a flash in STR mode only above 80 MHz
(`MSPI_TIMING_FLASH_NEEDS_TUNING`, esp_hw_support/port/esp32s3/
mspi_timing_tuning_configs.h), and every store that moves the clock runs
from IRAM.

**The flash at `pll80` is 20 MHz, and is not raised.** The core clock at 80
MHz over the ROM's divide by 4 is ESP-IDF's own low-speed mode:
`mspi_timing_enter_low_speed_mode` (esp_hw_support/mspi_timing_tuning.c)
sets the core to 80 MHz and the flash to 20 MHz with the timing tuning
cleared, and `mspi_timing_ll_set_flash_clock(0, 4)` (hal/esp32s3/include/
hal/mspi_timing_tuning_ll.h) encodes that divider as 0x0003_0103, the word
the ROM leaves. The ROM reads its divider the same way: "Pll is 80M, 4 for
20M, 3 for 26.7M, 2 for 40M, 1 for 80M" (`esp_rom_spiflash_fix_dummylen`,
esp_rom/esp32s3/include/esp32s3/rom/spi_flash.h). ESP-IDF's bootloader runs
the flash faster only at the speed its image header names
(`bootloader_flash_clock_config`, divide by 1, 2, 3 or 4 of 80 MHz;
bootloader_support/bootloader_flash/src/bootloader_flash_config_esp32s3.c)
and only together with the read mode, the dummy output, the pads' drive and
the chip-select timing (`bootloader_init_flash_configure`,
`bootloader_flash_set_dummy_out`). The part's board probe read SPI0 in slow
read (SPI_MEM_CTRL 0x0020_0000: no fast-read or multi-line bit) with no
extra dummy bytes from the ROM (the part's run of `s3_mem_probe` read
`rom_dummy_spi0` 0; §15a describes the line); the sources give
no reading that those settings hold at 40 or 80 MHz, so the clock keys
leave the flash at the 20 MHz the ROM's own divider names. Raising it means
changing the read mode with the divider, the dummy output and the
chip-select timing, which is the flash key's switch (§18d, §18e).

The TIMG watchdogs count APB-derived clocks (`TIMER_SRC_CLK_APB` is the
timer groups' default, soc/esp32s3/include/soc/clk_tree_defs.h); `_start`
writes TIMG0's config 0, and TIMG1's is left as §2e says. The RTC watchdog
is off and the super watchdog feeds itself (§2e), so no period anything
waits on moves with a key.

**Every register a key writes is volatile.** SYSCLK_CONF, CPU_PER_CONF,
UART0's and SYSTIMER's registers are reset by a chip reset (power, or the EN
pin, which the board's reset button and esptool pull) and by a system reset. A
CPU-only reset (a TIMG watchdog's) leaves the clock as it was, as ESP-IDF's
bootloader notes ("in case of TG WDT reset, CPU may still be running at >80
MHZ"); the watchdogs are off, so nothing here makes one. The PLL keys also
write the analog master's words, the BBPLL's analog registers and one RTC
register, RTC_CNTL_OPTIONS0, whose three BBPLL power-down bits they clear.
Each of those bits resets to 0, so on a part that reads them 0 the store
writes the word back unchanged. `pll160` and `pll240` write two analog
registers more, the LDO settings EXT_RTC_DREG and EXT_DIG_DREG, and read the
eFuse. A power cycle restores all of them. An EN-pin reset restores the
clock words: on the part each reset through the serial port's RTS line
printed `rst:0x1 (POWERON)`, and a run that found `sysclk=0x000a8001`
followed one that had left 0x000a8400 (§14h). It restores the two LDO
fields: `kill_churn_profile` at `pll160` was written after `kill_churn` at
`pll160` had raised them, with a reset through
RTS between and no power cycle, and its line read the ROM's 0x18 in both
before its writes (§14h). Whether it restores OPTIONS0, ANA_CONFIG and the
BBPLL's words has not been observed, and the runs so far cannot show it:
OPTIONS0's three bits read 0 before every run, which is both their reset value
and the value the bring-up leaves, so a reset that kept them would read the
same. It is the reference manual's to say, not ESP-IDF's sources'; an image
that does not write them leaves them as it finds them. The PLL powered and set
to 480 MHz, and the LDOs at the eFuse's settings, are the state ESP-IDF runs
every application in: the `non_240m` pair below 240 MHz and the `240m` pair
at it. Nothing written is non-volatile, and the eFuse is read and never
written or burnt.

**A wrong value on a part.** A PRE_DIV_CNT too large slows the CPU, and under
H3 the flash with it, down to 40 MHz / 1024; a SOC_CLK_SEL of 3 names no
source. A PLL set to the wrong frequency, or switched to before it has locked,
clocks the CPU, APB and the flash wrongly. Any of these looks like a hang: no
line after the ROM's banner, or one that crawls; the console's clock is the
crystal, so whatever does print is readable. `pll80` switches only after the
PLL has reported its calibration done, and prints its `not taken` line instead
when it has not. An LDO setting too low for the clock makes the digital core
fail at 160 or 240 MHz, which looks the same, or like faults no case
explains; `pll160` and `pll240` write the settings ESP-IDF writes on the
part, read there first (§15d), each before its clock moves, and a power
cycle puts the ROM's back. A wrong UART word garbles the
console and stops nothing. The part stays that way until reset: the board's
reset button or `esptool`'s EN-pin reset is a chip reset that restores every
clock word, and the ROM's download mode (strapping GPIO0 low at that reset)
runs before any image code, so `write-flash` puts a good image back.

**The line.** `xtal40`'s and `pll80`'s each print one line after the switch:
the clock words before and after, the measured speed before and after, and
the words the change could move. `pll80` adds `options0=` (before and
after), `bbpll=` (the BBPLL's register 8 after the bring-up, read through
`rom_i2c_readReg`: OR_LOCK is bit 7 and OR_CAL_END bit 6; `kept` when the
CPU was already on the PLL) and `ana_conf0=` (I2C_MST_ANA_CONF0 after P7,
CAL_DONE at bit 24). When the calibration is not reported done it prints
instead

```
SOS-S3: clock pll80 not taken: the BBPLL did not report its calibration
done; the CPU stays on the crystal sysclk=... options0=...->...
ana_conf0=... mhz=...
```

(one line) and the boot goes on at the clock the ROM left.

`pll160`'s line is `pll80`'s, named `pll160`, with five words added at its
end: `ldo=` (where the settings came from: `default`, the eFuse with no PVT
scheme; `unburnt`, the scheme's block version with no HVT; `efuse`, computed
from the calibration), `dig=` and `rtc=` (the settings written), and
`ext_dig_dreg=` and `ext_rtc_dreg=` (each LDO register's bits [4:0], read
through `rom_i2c_readReg` before its write and after the 40 us wait). Its
`not taken` line for the BBPLL is `pll80`'s; for LDO settings out of range
it prints, before anything is written,

```
SOS-S3: clock pll160 not taken: the LDO settings are outside ESP-IDF's
range; nothing is written ldo=... dig=... rtc=...
```

(one line), and the boot goes on at the clock the ROM left.

`pll240`'s lines are `pll160`'s, named `pll240`, with the `240m` pair in
`dig=` and `rtc=`, and CPUPERIOD_SEL 2 stored in the word its `cpu_per=`
reads after the switch.

`xtal40`, `pll80` and `pll160` handed a CPU at 240 MHz (§14b, "X6 and Q7,
by a stop") print, before anything is written,

```
SOS-S3: clock pll80 not taken: the CPU was found at 240 MHz, which this key
does not lower; nothing is written sysclk=... cpu_per=...
```

(one line, each named for its key), and the boot goes on at 240 MHz. The
line has no measured speed: the measurement starts SYSTIMER, a write.

### 14e. The emulator

Booted by the smoke's clock cases (§14g):

```
register             what the emulator does
SYSCLK_CONF          reads 0x000a8401 before and after every store: the PLL
                     selected, PRE_DIV_CNT 1, the crystal 40, CLK_DIV_EN set;
                     a store is dropped. So `xtal40` takes its two-store path
                     here and its one-store path on a part, and `pll80` finds
                     the CPU on the PLL and keeps it (S1): its bring-up does
                     not run here
CPU_PER_CONF         reads 0; a store is dropped
UART0 + 0x78         reads 0x15122500 before and after a store: the model has
                     a date word there, not UART_CLK_CONF, so both keys write
                     the ROM's two words, and the model drops the first and
                     ignores the divisor (§4)
UART0 FSM_STATUS     reads 0: the transmitter is always idle
SPI0 0x6000_30EC,    read 0: SPI0 falls in `esp32s3.iomem` (§3)
  0x6000_3014
RTC_CNTL_DATE        reads 0
RTC_CNTL_OPTIONS0    reads 0 at boot, and keeps what is stored
0x6000_E000 to       the analog master, I2C_MST_ANA_CONF0, ANA_CONFIG and
  0x6000_E048        ANA_CONFIG2: in `esp32s3.iomem`, read 0 and drop every
                     store. There is no analog block, so CAL_DONE is never
                     set
the ROM's analog-    run, being the ROM's own code: each finds the master's
  bus functions      busy bit clear and returns; `rom_i2c_readReg` answers 0,
                     so `pll160`'s LDO registers read 0 before and after
the eFuse            reads 0, unless the case gives the emulator a file of
                     the read registers (a case's `efuse`), whose words it
                     answers from EFUSE_RD_WR_DIS (0x6000_702C) on
SYSTIMER             modelled (§6a)
CCOUNT               40 MHz of host time whatever is stored: `mhz=` reads
                     about 40.00 on both sides; on the instruction-counted
                     clock (below) it reads what the count gives
```

Each key's line, up to the two speeds and after them, is asserted; the
speeds are host time and are not. `boot_pll160` runs `pll160` on the
uncalibrated eFuse (`ldo=default`, 27 and 27) and `boot_pll160_calibrated`
on a calibrated one, block version 1.2 with negative slopes and voltages and
HVT 24 (`ldo=efuse`, 26 and 30: the image builder's tests hold those to
ESP-IDF's arithmetic). `pll160_refused` hands the key settings no eFuse can
give (a digital 32): it prints its `not taken` line, SYSTIMER's CONF still
reads its reset value 0x4600_0000, which the key's first step past the check
changes, and the boot goes on. `boot_pll240`, `boot_pll240_calibrated` and
`pll240_refused` are the same three at `pll240`: the defaults 28 and 28, the
calibrated eFuse's `240m` pair, 27 digital and 31 RTC (each differing from
its `non_240m` value), and a digital setting at 240 MHz of 32. The emulator's
clock words have the CPU on the PLL at 80 MHz, so `boot_pll240` is ESP-IDF's
path going up from 80.

The stop on a CPU at 240 MHz has cases of its own, since the emulator's
CPU_PER_CONF reads 0: `tests/xtensa/clock_found_240.saw` hands a key the
words a part reads with the CPU at 240 (`sysclk` 0x000a8400, `cpu_per`
0x0000000e) through its `clock_init_found`. `xtal40_found_at_240`,
`pll80_found_at_240` and `pll160_found_at_240` print the stop's line and
store nothing to the analog master, ANA_CONFIG, ANA_CONFIG2, either clock
word, SYSTIMER's CONF or RTC_CNTL_OPTIONS0; `pll240_found_at_240` takes its
switch, with `boot_pll240`'s stores. `xtal40_found_crystal`
(`clock_found_crystal.saw`) hands `xtal40` CPUPERIOD_SEL 2 on the crystal,
where it clocks nothing, and the key takes its switch.

The emulator drops those stores and reads back nothing they wrote, so these
cases hold them by a trace of the run's device accesses (QEMU's
`memory_region_ops_write` and `memory_region_ops_read`; a case's
`expect_stores`, `expect_counter_span` and `device_untouched`):
`boot_pll80`'s three clock stores, CPUPERIOD_SEL 0 and SYSCLK_CONF twice;
`boot_pll160`'s and `boot_pll160_calibrated`'s SYSTIMER CONF store, the
key's first past the check, then the two LDO writes, as the ROM's command
words to the analog master with the settings in them, RTC first, then at
least two SYSTIMER snapshots of the wait, then the clock stores with
CPUPERIOD_SEL 1; the wait's loads of SYSTIMER's UNIT0 between the digital
write and CPU_PER_CONF's store, which must span at least 640 counts, 40 us
at 16 MHz, and step at most 32 counts, 2 us, from one load to the next; the
same for `boot_pll240`,
`boot_pll240_calibrated` and `pll240_found_at_240` with the `240m` pair and
CPUPERIOD_SEL 2, which is ESP-IDF v5.4.1's `rtc_clk_cpu_freq_to_pll_mhz(240)`
going up: Q1's two writes, RTC first, and its 40 us, then Q3 to Q5; and no
store by `pll160_refused` or `pll240_refused` to the analog master,
ANA_CONFIG, ANA_CONFIG2, either clock word or SYSTIMER's CONF.

**The wait's length.** A case that reads a span runs on the emulator's
instruction-counted clock (QEMU's `-icount`, `ICOUNT_SHIFT` in
tools/sos_runner.py): every instruction advances the virtual clock SYSTIMER's
model counts by a fixed time, and nothing ties that clock to the host's, so
a run reads the same counts however loaded the host is. On the host's clock
one handshake with SYSTIMER can take hundreds of counts, and a single pass
of the wait's loop would clear 640 whatever the wait asked for. On the
counted clock a pass steps a few counts, and the step bound holds that: a
wait for t counts spans at least t and less than t + 32, so a wait of 608
counts (38 us) or less is red, and a removed one makes no load at all. What
the cases do not hold is a wait between 609 and 639 counts.

`pll_bringup` runs the bring-up itself, a
harness kernel that calls `sos_pll_bringup`: its seven ROM calls go through
the stub and return, the calibration is never reported done, the bring-up
answers its timeout (1) after the bound, and RTC_CNTL_OPTIONS0's three
power-down bits, set first, come back clear with the bits beside them kept
(`options0=0x1c000000`). The timeout's outcome does not depend on the
host's speed: CAL_DONE never comes, and the bound only says when to stop.

**What the emulator cannot judge**, and only a part can: the analog words (P4:
values, order, and that the ROM's functions deliver them; that L5's, L6's
and Q1's writes reach the LDOs, and their field's width: the ROM's masked
write clears the field it is given in what it read and ORs the data in
whole, so a field of
[3:0] would send the same command where the register's bit 4 is clear or the
setting's is set, as in the emulator (the register reads 0) and on this part
(settings 0x1c and 0x1d); a setting below 16 over the ROM's 0x18 would go out
16 high), R3, P2, P3, P6 and P7, which land in
registers the emulator drops; the voltage the LDO settings give and the CPU's
running at 160 or 240 MHz on it; the calibration and the PLL's lock (P5's
done path and `bbpll=`); the speed the three clock stores give; the wait's
threshold within its last 31 counts, and its length on the part, which
counts the part's own SYSTIMER; the bring-up's
path through the PLL keys (the emulator's source is the PLL); that the
bring-up and the stub need IRAM; the flash at 20 MHz; the snapshot thrown
away before a measurement; and the measurement's warm-up pass, and its
two-sided shape as a whole: on the counted clock there is no cold fetch, so
the one-sided measurement it replaced reads the same there, and only the
part tells them apart (§14h).

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

The smoke's clock cases are `boot_xtal40`, `boot_pll80`, `boot_pll160`,
`boot_pll160_calibrated`, `boot_pll240` and `boot_pll240_calibrated` (the
kernel alone at the key: the line, the banner, the refusal of an empty
image), `kill_churn_xtal40`, `kill_churn_pll80`, `kill_churn_pll160` and
`kill_churn_pll240` (`kill_churn` at the key), `pll_bringup` (the bring-up),
`pll160_refused` and `pll240_refused` (the LDO settings' stop), and
`xtal40_found_at_240`, `pll80_found_at_240`, `pll160_found_at_240`,
`pll240_found_at_240` and `xtal40_found_crystal` (the stop on a CPU at 240
MHz) (§14e). `kill_churn_profile` is board-only: the smoke prints its reason
and builds nothing for it. A board image of each is `--emit-flash CASE
--clock KEY --out PATH`.

An image at the `rom` key is the bytes it is without the keys: it compiles
no key's code, and the link drops the IRAM code only a key calls
(`sos_clock_store`, `sos_pll_bringup`) and the stub, which only a case that
calls the ROM keeps.

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
the 20 MHz the registers say, and the one after reads true. The images
above were read by a one-sided measurement: the two ends of the interval
ran differently (a snapshot then CCOUNT at the start, a polling loop then
CCOUNT at the end) in one cold pass. Flash fetches and the first slow
SYSTIMER handshake landed at the start and were counted late, which reads
low. The keys now measure as the PSRAM's P2 does (`cpu_centi_mhz`,
`psram_cpu_passes`): both ends through the same sampler, a warm-up pass that
fills the caches, and the second pass used, over at least 5 ms of SYSTIMER.
The rows above were read before that and are not remeasured. On the part,
`kill_churn` at `pll80` built with the two-sided measurement reads
`mhz=20.00->80.00` on four boots running, and 5.78 s from the reset to `200
rounds`, where the one-sided images of the same hour read 19.60 to 19.81
before. The case `clock_mhz_icount` holds the figure's arithmetic on the
emulator's instruction-counted clock.

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

**At 80 MHz**, from the `pll80` images and an `xtal40` `kill_churn` built
from the same tree (§14d's line), each written and reset through the serial
port's RTS line, which the ROM reports as `rst:0x1 (POWERON)`:

```
pll80 line   both images
sysclk       0x000a8001 -> 0x000a8400   SOC_CLK_SEL 1 (the PLL),
                                        CLK_DIV_EN reads 1 on the PLL too
cpu_per      0x0000000c -> 0x0000000c   CPUPERIOD_SEL 0, 80 MHz
options0     0x1c00a000 -> 0x1c00a000   the three power-down bits already 0
bbpll        0x000000c3                 OR_LOCK [7] and OR_CAL_END [6] set
ana_conf0    0x2100e404                 CAL_DONE [24] set, [2] set, [3] clear
uart_clk     0x03700000
mspi_core    0x00000000
mspi_clock   0x00030103
rtc_date     0x02101271                 SLAVE_PD [18:13] 0, all six on

image                        mhz before   mhz after   reset to `200 rounds`
kill_churn, pll80            19.84        80.01        7.88 s
kill_churn_profile, pll80    19.76        80.01        -
kill_churn, xtal40           19.84        40.01       14.17 s
```

The figure before the store now reads about 1% under 20 MHz where the first
images read 2.5% under: the discarded snapshot accounts for part of it.
The `xtal40` image from this tree took 14.17 s where the first took
13.44 s, with the same clock words.

```
          create    give     start   settle   kill     release  all
pll80      2463616  1102880  319297  2027393  1682409   312024   7907622

          stalled   bytes written after the tick was armed
pll80      335750   31313
```

Against the crystal's row above, the whole run takes 0.56 of the time:
creating a child 0.49, settling and killing about 0.56, releasing 0.62,
giving 0.74 and starting 0.84. The flash's clock stays 20 MHz under every
PLL speed (§14c); how much of each phase waits on it has not been measured.

**At 160 MHz**, from the `pll160` images and a `pll80` `kill_churn` built
from the same tree as the control, written and reset as above. The part's
eFuse gives the settings §15d shows, and the line reads the ROM's 0x18 in
both LDO fields before the writes:

```
pll160 line   both images
sysclk        0x000a8001 -> 0x000a8400   as pll80
cpu_per       0x0000000c -> 0x0000000d   CPUPERIOD_SEL 1, 160 MHz
options0, bbpll, ana_conf0, uart_clk, mspi_core, mspi_clock, rtc_date
                                         pll80's words, as above
ldo           efuse, dig 0x1d, rtc 0x1c
ext_dig_dreg  0x18 -> 0x1d
ext_rtc_dreg  0x18 -> 0x1c

image                         mhz before   mhz after   reset to `200 rounds`
kill_churn, pll160            19.84        160.01       6.319 s
kill_churn_profile, pll160    19.76        160.01       -
kill_churn, pll80             19.92         80.01       7.528 s
```

A trace of each image under the emulator, given this part's four eFuse
words, shows the analog master taking the two writes, the read of its
register each masked write makes first, and the four reads the line makes,
six reads in all, each command to block 0x6D, and nothing else; one store to
CPU_PER_CONF, with CPUPERIOD_SEL 1; and no store to the eFuse controller,
RTC_CNTL_OPTIONS0 or RTC_CNTL_DATE.

```
          create    give     start   settle   kill     release  all
pll160     1772175   756490  199579  1994585  1638724   265876   6627431

          stalled   bytes written after the tick was armed
pll160     361342   31312
```

Against `pll80`'s row above, twice the CPU's clock takes 0.84 of the time,
to `200 rounds` and in the profile alike: creating a child 0.72, giving
0.69, starting 0.62, releasing 0.85, while settling (0.98) and killing
(0.97) hardly move. The two `pll80` rows were taken from different trees and
days, which move this case by a few percent on their own (7.88 s, then 7.53 s
to `200 rounds`). The flash stays at 20 MHz.

**At 240 MHz**, from the `pll240` images and a `pll80` `kill_churn` built
from the same tree as the control, written and reset as above, all three in
one capture session; each ran to `SOS-S3: halt pass`. The part's eFuse gives
the `240m` pair §15d shows, and the line reads the ROM's 0x18 in both LDO
fields before the writes:

```
pll240 line   both images
sysclk        0x000a8001 -> 0x000a8400   as pll80
cpu_per       0x0000000c -> 0x0000000e   CPUPERIOD_SEL 2, 240 MHz
options0, bbpll, ana_conf0, uart_clk, mspi_core, mspi_clock, rtc_date
                                         pll80's words, as above
ldo           efuse, dig 0x1e, rtc 0x1d
ext_dig_dreg  0x18 -> 0x1e
ext_rtc_dreg  0x18 -> 0x1d

image                         mhz before   mhz after   reset to `200 rounds`
kill_churn, pll240            19.92        240.01       5.526 s
kill_churn_profile, pll240    19.76        240.00       -
kill_churn, pll80             19.76         80.01       5.766 s
```

Three times the CPU's clock gains about 4% on `kill_churn` against this
session's `pll80` control: 5.526 s against 5.766 s, 0.96 of the time. The
control itself took 5.766 s where the `pll80` rows above took 7.88 s and
7.53 s, from older trees; only rows from one session compare.

```
          create    give     start   settle   kill     release  all
pll240     1086465   942393  226796  1869331  1591091   317618   6033696

          stalled   bytes written after the tick was armed
pll240     370564   31312
```

Against `pll160`'s row above, which is from another tree and session,
creating a child takes 0.61 of the time, settling 0.94, killing 0.97 and the
whole 0.91, while giving (1.25), starting (1.14) and releasing (1.20) take
longer. So the run is bound by something the CPU's clock does not move. The
flash, which stays at 20 MHz under every key (§14c) and holds the code the
kernel and every process run through the cache, is the likely candidate; how
much of each phase waits on it has not been measured.

### 14i. The console key

The console's rate is a build key, `s3console` (`keys/console-*/`), with
`s3consolekit` (`keys/console-common/`) for the keys that write. `rom`, the
default, writes nothing: UART0 stays at the 115200 the mask ROM left (§4a).
`b921600`, `b2000000` and `b3000000` put the console at that rate. The case
key `"console"` sets it, `--emit-flash CASE --console KEY` overrides it for
a board image, and an image's `[keys] console = "b2000000"` chooses it;
§14d says how a key is stated and refused.

**Why a rate.** The board's console goes through a USB-UART bridge on UART0's
two pins, and at 115200 an image that prints much is bound by the wire: the
console's stall is over half of `kill_churn_profile`'s round at the faster
flash keys (§14f, §18g). The bridge is a WCH CH343P (USB 1A86:55D3), at USB
full speed and rated by WCH into the megabaud range, so the part's UART0 is
the limit; a board run says which of the rates works end to end (below).

**The divisor.** UART0's clock is the crystal (§14a), 40 MHz, and stays so
under every clock key. UART_CLKDIV holds the integer part of the divisor in
[11:0] and its sixteenths in [23:20]. ESP-IDF v5.4.1's `uart_ll_set_baudrate`
for the S3 (components/hal/esp32s3/include/hal/uart_ll.h) divides the source
clock by `ceil(clock / (4095 * baud))` into SCLK_DIV_NUM, which is 1 for every
baud from 9,768 up (so SCLK_SEL and CLK_CONF are left alone: the key writes
the divisor alone), then stores `(clock << 4) / (baud * sclk_div)`, truncated,
as the divisor, its low four bits the sixteenths. The S3's UART has no
register-update step, so the store is the whole switch. `clkdiv_for` is that
quotient; the ROM's own 115200 is the same arithmetic (§14a).

```
key       16 * 40 MHz / baud   integer  sixteenths  UART_CLKDIV   actual baud  error
rom       5555.6 -> 5555       347      3           0x0030_015B   115,211.5    +0.010 %
b921600   694.4  -> 694       43       6           0x0060_002B   922,190.2    +0.064 %
b2000000  320                  20       0           0x0000_0014   2,000,000    0
b3000000  213.3  -> 213       13       5           0x0050_000D   3,004,695    +0.157 %
```

A UART tolerates a few percent between the ends; the largest error here is
0.157 percent.

**The switch, and where it runs.** `clock_init` is the console key's first,
then the clock key's, then the flash key's. The console key is first so that
every line the keys and the kernel print is at its rate, the clock key's and
the flash key's too, and because it needs nothing from them: UART0 is on the
crystal, which no CPU clock key moves, so the divisor holds through the clock
key's switch, and the clock key's own drain and `console_on_crystal` then
find UART0 on the crystal and write nothing. The sequence:

1. Read UART0's CLK_CONF (+0x78). If its SCLK_SEL ([21:20]) is not 3, the
   crystal, or any of SCLK_DIV_NUM [19:12], SCLK_DIV_A [11:6] and SCLK_DIV_B
   [5:0] is set (the divisor assumes the crystal undivided, as the ROM leaves
   it; the field resets to 1, so a word read is checked), print `SOS-S3:
   console KEY not taken: UART0's clock is not the undivided crystal,
   uart_clk=0x...; the console stays at the rate it has` at the rate the
   console has and write nothing. A clock key that follows puts the console
   back on the ROM's words (§14d) as it does under `rom`, so the console is
   at 115200 in that case.
2. Drain: wait until UART_STATUS's TXFIFO_CNT is 0 and UART_FSM_STATUS's
   ST_UTX_OUT is 0, the test the ROM's `uart_tx_wait_idle` makes, so no byte
   of the ROM's banner or the kernel's is cut off at the old rate.
3. Store the key's divisor to UART_CLKDIV, the one store.
4. Print one line at the new rate: `SOS-S3: console KEY clkdiv=0x0030015b->0x...
   uart_clk=0x03700000`, the divisor before and after, and CLK_CONF.

The ROM's own output (its `ESP-ROM:` banner) stays at 115200, so a capture
opened at the key's rate sees it as noise before the key's line.

**On the emulator.** The emulator's serial is not rate-limited, so the key
changes no transcript but for its line. Its UART0 + 0x78 reads a date word,
`0x15122500` (§14e), whose SCLK_SEL reads 1, so the shipping kernel at a key
that writes prints the `not taken` line and stores nothing to UART0 but console
bytes (`boot_console_*`). The words are handed in through the harness
`tests/xtensa/console_found.saw` (the key's `console_init_found`): three
selects refused, the crystal with each pre-divide field set refused
(`0x03701000`, `0x03700040`, `0x03700001`), then the ROM's `0x03700000` taken
(`console_found_*`). The emulator's UART0 always reads idle, so the drain's
two tests are held on a stand-in UART the harness lies with: the kit's
`drain` runs over `UartState`, once with the FIFO reading occupied for 3 polls
and once with the transmitter reading busy for 4, and must poll until each
lie ends; the two fields' decoding (`fifo_count_of`, `tx_state_of`) is held on
crafted words: each field alone set, its neighbours alone set, and the field
full. Every
store the kernel makes to UART0 but its FIFO is held in order
(`expect_uart_stores`), and the drain is held by the loads of UART_STATUS and
UART_FSM_STATUS between the last console byte and the store (`expect_uart_drain`).
`boot_console_b921600_xtal40` holds a refused key before a clock key that
writes: the clock key's two stores put the ROM's words back.

**On the part.** `sosimage flash` prints the rate a capture opens beside the
SHA-256, the board and the `esptool` line (`console 921600 baud 8N1`; 115200
for an image with no console key stated), and `--emit-flash` prints it too.
The stages, which need the board and are the lead's:

```
C1  `console_line` at each key that writes (pll240, dio80), captured at its
    rate: the key's line, then the 256-character line whole, every byte
C2  `kill_churn_profile` at each of the four keys: the stall share of `all`
    against the key's rate
```

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
image's device writes under the emulator, made before it is flashed: it must
write no device address an ordinary boot does not. Every load goes through one
accessor whose view is a `ReadOnly` field, so a store through it is a compile
error; and the runner refuses the source if it takes a direct route to a store
(a second view, an unsafe type named outside the accessor, a HAL call but the
one that halts, an import beyond the console, the HAL and a module of plain
computation (§15d), `extern`, `asm`), which catches a mistake but cannot prove
the property: a raw pointer from a safe name and an index reach any address
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

From a board run of the probe, ESP32-S3 rev v0.2, ROM `esp32s3-20210327`,
after a power-on reset; esptool read the same part as "Embedded PSRAM 8MB
(AP_3v3)". The clock words agree with §14h. Each field is read with the
positions ESP-IDF v5.4.1's headers give: `efuse_reg.h`, `system_reg.h`,
`spi_mem_reg.h`, `extmem_reg.h`, `io_mux_reg.h` (MCU_SEL [14:12], FUN_DRV
[11:10], FUN_IE [9], FUN_PU [8], FUN_PD [7]) and `gpio_reg.h`.
The eFuse words `sys5` and `part1_data4` are from the calibration probe's
run (§15d), on the same part.

| word | part |
|---|---|
| `sys3`, `sys4` | `0x03080000`, `0x64d0a6c8` |
| `sys5`, `part1_data4` (§15d) | `0x7400dc90`, `0x33c979b1` |
| `PSRAM_CAP`, `PSRAM_VENDOR` | 1 (8 MiB), 1 (AP at 3.3 V) |
| `strap`, `sdio_conf` | `0x00000008`, `0x0ab0be0a` |
| `sysclk`, `cpu_per`, `core_clk_sel` | `0x000a8001`, `0x0000000c`, 0: the crystal over 2, as §14h |
| `perip_clk_en0`, `perip_rst_en0` | `0xf9c1e06f`, 0 |
| `spi0 clock`, `sram_clk`, `ctrl`, `user`, `user1`, `user2`, `misc` | `0x00030103` and `0x00030103`, both divide by 4; `ctrl` `0x00200000`, no fast-read and no multi-line bit: the flash in slow read on one line; `user` `0x000000c0`, USR_DUMMY [29] clear, so no dummy is sent whatever `user1` holds; `user1` `0x5c000007` (a 24-bit address, a dummy field of 8 cycles); `user2` `0x70000003` (an 8-bit command, 0x03 READ); `misc` 0 |
| `spi0 fctrl`, `sctrl`, `sram_cmd`, `drd`, `dwr` | `0x00000005` (CACHE_REQ_EN, CACHE_FLASH_USR_CMD), `0x0055c070`, CACHE_SCTRL's reset default: CACHE_SRAM_USR_RCMD and CACHE_SRAM_USR_WCMD set, a 24-bit address (SRAM_ADDR_BITLEN 23), a read dummy of 2 cycles (USR_RD_SRAM_DUMMY [4] set, SRAM_RDUMMY_CYCLELEN 1, which holds the cycles less one), no write dummy (USR_WR_SRAM_DUMMY [3] clear, though SRAM_WDUMMY_CYCLELEN holds 1), SRAM_OCT clear, 0, 0, 0: the PSRAM's commands not set |
| `spi0 ddr`, `smem_ddr`, `smem_ac`, `date` | 0, `0x00003020`; `smem_ac` `0x0000b107`, CS_SETUP and CS_HOLD set, CS_SETUP_TIME 1, CS_HOLD_TIME 2, CS_HOLD_DELAY 0, the ROM's `SPI_init` words (§16c's S2 changes them); `date` `0x02101040`, SPICLK_PAD_DRV_CTL_EN [4] clear, so the clock pad's drive is pad 30's own `FUN_DRV` |
| `spi1 clock`, `ctrl`, `user`, `user1`, `user2` | `0x00030103`, `0x00208000`, `0x80000000`, `0x5c000007`, `0x70000000` |
| `spi1 misc`, `fctrl`, `ddr`, `cmd` | `0x00000002` (CS1_DIS: chip select 1 off, 0 on), 0, 0, 0: idle |
| `dcache_ctrl`, `dcache_ctrl1`, `dcache_autoload`, `icache_ctrl` | `0x00000001`: the data cache on, 32 KiB, 16-byte lines; 0, its bus open; `0x00000008`, no autoload; `0x00000001`: the instruction cache on, 16 KiB, 16-byte lines |
| `mmu` 0, 32, 127, 128, 255, 256, 383, 511 | each entry its own index: flash pages 0 to 511, valid |
| `iomux pin_ctrl`, pads 14, 21 | `0x0000a7ff`; `0x00000a00`, `0x00000800` |
| `iomux` pads 26 to 32 (CS1, then the flash's) | 26, 27, 28 `0x00001b00`: function 1 (GPIO), pull-up, input, drive 2; 29 to 32 `0x00000b00`: function 0 (SPICS0, SPICLK, SPIQ, SPID), pull-up, input, drive 2 |
| `iomux` pads 33 to 37 (the PSRAM's D4 to D7, DQS) | `0x00000a00` each: function 0 (`FUNC_GPIO3n_GPIO3n_0`), no pull, input, drive 2. Not the PSRAM's function 4: S1 sets it |
| `iomux` pads 41, 42, 47 | `0x00000a00`, `0x00000a00`, `0x00001a00` |
| `insel` 7 to 11 | `0x0000003c` each: SIG_IN_SEL [7] clear, the signal straight from the IO MUX |
| `pad_hold`, `ace0` | 0, no pad held; `0x000000ff`, ECC [8] off |
| `rtc_pad21`, `rtc_pad21_mux_sel` | `0x50000000`, 0 |
| `rom_dummy_ptr`, `rom_dummy_spi0`, `rom_dummy_spi1` | `0x3fcef6a4`, 0, 0: the ROM adds no dummy cycle to either controller's commands |

What the bring-up (§16) takes from it: the eFuse is the 8 MiB AP part at
3.3 V; no pad is held and ECC is off, so S0.2 and S3 pass and S3 writes
nothing; SPI1 is idle with chip select 1 off, which S10 puts back; the pads
the PSRAM needs are not yet at its functions (26, 27 and 28 are GPIOs, 33
to 37 are at function 0) and S1 is what sets them; 27 and 28 are the
flash's HD and WP, which the ROM's boot leaves GPIOs because the flash reads
on one line, and the octal bus's D3 and D2, so S1 gives them function 0 as
ESP-IDF's pin setup does; the pads the flash reads on (29 to 32) are the
ROM's and S1 does not touch them; and the ROM's extra dummy is 0.

Two departures from ESP-IDF's drive, both on the flash's pads. IDF's
`esp_mspi_pin_init` follows the ROM's pin setup with
`mspi_timing_set_pin_drive_strength`, which is
`mspi_timing_ll_set_all_pin_drive(0, 3)` (hal/esp32s3/include/hal/
mspi_timing_tuning_ll.h): drive 3 on pads 27, 28, 31, 32 and 33 to 37, and
on the clock pad through SPI_MEM_DATE_REG(0). S1 gives drive 3 to 27 and
28, now the PSRAM bus's, and to 33 to 37, but leaves 31 and 32 at the
ROM's 2, since the running flash reads on them; and it leaves the clock
pad as it is, pad 30's own 2 (SPICLK_PAD_DRV_CTL_EN clear), where IDF sets
3 for both chips.

### 15d. The eFuse calibration probe

`tests/xtensa/s3_dbias_probe.saw`, the smoke's `s3_dbias_probe`, reads the
four eFuse read registers that hold the LDO calibration and prints them,
their fields, and the settings ESP-IDF would choose from them (§14b, L4).
It is the look at a part before any image writes a voltage. It is held to
everything `s3_mem_probe` is held to, and its one import beyond the console
and the HAL, `s3dbias`, is held to having no unsafe code, no `extern` and no
import of its own (`check_pure_module`). Its trace under the emulator writes
what `s3_mem_probe`'s does, store for store, but the console's bytes.

```
SOS dbiasprobe: efuse sys3= sys4= sys5= part1_data4=
SOS dbiasprobe: fields blk_version_major= blk_version_minor= k_rtc_ldo=
                k_dig_ldo= v_rtc_dbias20= v_dig_dbias20= dig_dbias_hvt=
                pvt=yes|no
SOS dbiasprobe: dbias source= dig1v3= dig_240m= rtc_240m= dig_non_240m=
                rtc_non_240m= check=ok|refused
```

`source` is `default`, `unburnt` or `efuse` (§14d), `dig1v3` the 1.3 V
search's answer, and `check` whether each setting is in the range ESP-IDF's
arithmetic gives it.

On the emulator the eFuse reads 0: block version 0.0, no PVT scheme, so the
settings are the defaults, 28, 28, 27 and 27. `s3_dbias_probe_calibrated`
gives it the calibrated file `boot_pll160_calibrated` boots on (§14e); the
smoke asserts both transcripts whole.

On the part (ESP32-S3 rev v0.2, ROM `esp32s3-20210327`):

```
sys3 0x03080000  sys4 0x64d0a6c8  sys5 0x7400dc90  part1_data4 0x33c979b1
blk_version 1.3, so the PVT scheme
k_rtc_ldo 0x05 (+5)   k_dig_ldo 0x4d (-13)
v_rtc_dbias20 0x0c (+12)   v_dig_dbias20 0x92 (-18)   dig_dbias_hvt 27
source efuse   dig1v3 31 (no dbias up to 30 reaches 1.3 V)
dig_240m 30   rtc_240m 29   dig_non_240m 29   rtc_non_240m 28   check ok
```

So `pll160` writes 29 (0x1d) to EXT_DIG_DREG and 28 (0x1c) to EXT_RTC_DREG
on this part: HVT + 2, and the RTC setting that search gives for it; and
`pll240` writes 30 (0x1e) and 29 (0x1d): HVT + 3, and its RTC setting.

---

## 16. The PSRAM's command layer

The part's 8 MiB octal PSRAM sits on the memory-SPI controller the flash
uses, on its own chip select (CS1, pad 26) and data pads (the flash's four
plus 33 to 37). Bringing it up means setting those pads, the controller's
PSRAM timing and clocks, and the chip's mode register 0, with commands sent
on SPI1; a command sent with the flash's chip select enabled goes to the
flash. This section is the command layer: the bring-up's checks and its
commands, steps S0 to S10, and the measurement that settles the memory-SPI
core clock. SPI0's PSRAM side, the cache's map, the read-back test and the
`esp32s3-r8` board that brings the PSRAM up at every boot (S11 to S15) are
§17's. The flash's reads share its command primitive (§18a).

- `kernel/mspi.c` makes every step and every check, in one call from IRAM
  (`sos_psram_bringup`), and returns at the first check that fails with a
  record of what it read; `sos_psram_time_reads` is the measurement.
- `kernel/lib.saw`'s PSRAM section passes the description, measures the
  CPU's clock against SYSTIMER for the measurement (`psram_cpu_passes`),
  and prints the line a stop's record says (`psram_stop_line`). The CPU's
  clock is CCOUNT's advance over at least 5 ms of SYSTIMER, the second of
  two passes, each end of the interval taken by the same instructions
  (SYSTIMER's snapshot, then CCOUNT), so the time between the two reads
  drops out; the first pass takes SYSTIMER's first snapshot, which can be
  slower than the rest, and fills the caches, since a first fetch from the
  flash at a 20 MHz CPU costs a thousand cycles or more.
- `tests/xtensa/psram_identity.saw` (S0 to S8, S10), `psram_mode.saw` (S0 to
  S10, then the measurement) and `psram_fault.saw` (the fault build, §16e)
  are the harness kernels; the first two are board stages P1 and P2 (§16f).

### 16a. The part, and the description

The part (§15c): the eFuse's PSRAM_CAP 1 (8 MiB) and PSRAM_VENDOR 1 (AP at
3.3 V), as esptool reports it. The chip the bring-up must then find: MR1's
vendor 0x0D (AP Memory, IDF `OCT_PSRAM_VENDOR_ID`), MR2's good-die bit set
and its density 3 (8 MiB). The bring-up compares each with a description
and stops at the first that differs; it never infers a size.

The description is eight words (`mspi.c`'s `struct sos_psram_expect`,
`lib.saw`'s `PsramExpect`): the two eFuse codes, the vendor, the density,
the divider word, and the three clock words the divider was chosen for,
masked as S0.3 compares them. Here it is the harness's, written by the
runner beside each image (`_psram_case_asm`, `PSRAM_DESCRIPTION`,
`PSRAM_PART_CLOCK`, `PSRAM_EMULATOR_CLOCK` in `tools/sos_runner.py`): the
part's codes, the divider word `0x00030103`, and the clock words of the
image's target. A board image (`--emit-flash`) carries the part's words at
the image's clock key; a smoke image carries the emulator's, which no key
moves (§14e). The two are different machines as far as S0.3 is concerned,
and the rule that the part matches its description exactly holds for each.

### 16b. The memory-SPI core clock under each clock key, and the divider

Commands on SPI1 run at SPI_MEM_CLOCK_REG(1), and cache fills from the PSRAM
at SPI0's SPI_MEM_SRAM_CLK_REG; each is the memory-SPI core clock over its
divider ("f_SPI_CLK = f_MSPI_CORE_CLK / (SPI_MEM_CLKCNT_N + 1)", IDF
`spi_mem_reg.h`). The core clock is set by SPI_MEM_CORE_CLK_SEL and the PLL
when the CPU runs on the PLL: at 480 MHz, 0 gives 80 MHz (the same header,
`SPI_MEM_CORE_CLK_SEL`). On the crystal the sources do not say; three
readings stay open, H1 the crystal (40 MHz), H2 a PLL-derived 80 MHz, H3 the
system clock after the pre-divider. ESP-IDF v5.4.1's
`mspi_timing_tuning_ll.h` for this part defines
`MSPI_TIMING_LL_FLASH_CPU_CLK_SRC_BINDED` as 1, a bare define with no
comment and nothing at that tag using it. Its name suggests that the
memory-SPI clock's source is bound to the CPU's, which would make H2 the
unlikely reading; no source states it. Board stage P2 measured it on the
part (§16f): **H3**. On the crystal the memory-SPI core clock is the CPU's,
the system clock after the pre-divider: 20 MHz under `rom`, and so 40 MHz
under `xtal40`. Under `pll80` it is 80 MHz, as `SPI_MEM_CORE_CLK_SEL` says.

What each clock key leaves (§14d, §14h), what S0.3 compares, and the bus at
the divider every key uses, divide by 4:

```
key       SYSCLK_CONF  CPU_PER_CONF  CORE_CLK_SEL  core clock          bus at
          & 0x80FFF    & 7                                             divide 4
rom       0x00080001   0x4           0             20 MHz, the CPU's    5 MHz
xtal40    0x00080000   0x4           0             40 MHz, the CPU's    10 MHz
pll80     0x00080400   0x4           0             80 MHz: the PLL at   20 MHz
                                                   480, CORE_CLK_SEL 0
pll160    0x00080400   0x5           0             80 MHz, as pll80     20 MHz
emulator  0x00080401   0x0           0             no clock modelled    -
```

`rom`'s and `xtal40`'s words are the part's as §14h prints them; `pll80`'s
and `pll160`'s are their lines' after the switch (`sysclk` `0x000a8400`,
CLK_DIV_EN reading 1 on the PLL too; `cpu_per` `0x0000000c` and
`0x0000000d`). `mspi_core` read 0 and
`mspi_clock` `0x00030103` under every key on the part. The emulator's words
read as the PLL at 320 MHz with CPUPERIOD_SEL 0 under every key, since it
drops every store to both registers.

So divide by 4 serves every key the smoke and the board use: the bus runs at
5 to 20 MHz, never above the 20 MHz ESP-IDF first talks to the chip at
(`mspi_timing_enter_low_speed_mode`'s `set_psram_clock(20, ...)`) and never
above the 40 MHz past which it tunes octal PSRAM
(`MSPI_TIMING_PSRAM_NEEDS_TUNING`, `mspi_timing_tuning_configs.h`). It is
also the ROM's flash divider and SRAM_CLK's reset value, so S4's and S5's
writes leave the words the part already holds. No key is refused; a clock
key the runner has no part's words for is refused by name when a board
image is built for it (`_psram_case_asm`). The divider for cache fills at
20 MHz follows from the reading P2 measured: 4 under `pll80` and
`pll160` and 2 under `xtal40`. Under `rom` it would be 1 (SCLK_EQU_SYSCLK),
which the part read shifted, so `rom` takes 2, a 10 MHz bus (§17b).

A clock change after the bring-up would move every number above with it;
no image of this tree makes one (§14d's keys run first, in `clock_init`),
and S0.3 stops on any difference between the clock in force and the one the
description names.

**The bound.** Every wait reads SPI_MEM_CMD(1) at most `POLL_BOUND`,
100,000, times. A mode-register command is under 100 SPI clocks (command 1,
address 2, latency up to 20, data up to 2), under 20 us at 5 MHz. A read is
a peripheral load and a branch, several core cycles, so the bound is over a
millisecond at any CPU clock the part runs (240 MHz at most): fifty times
the slowest command, and it ends a stuck controller's wait well inside a
second at the slowest loop rate seen on the part.

**The measurement** (`sos_psram_time_reads`; `psram_mode.saw` runs it
four times and measures the CPU's clock first, with `psram_cpu_passes`)
finds how many CCOUNT cycles after its start one command ends, from IRAM
under the gate, by
bisection: a probe starts the command, spins until CCOUNT has advanced by
the probe's spin, reads SPI_MEM_CMD(1) once, then waits for the command as
S7.9 does; the answer is the least spin whose read finds the command
finished, between 0 and `SPIN_LIMIT` (4096), a command still busy at the
limit being stop 0x62. The command and the system run from IRAM on one
crystal, so a command takes the same cycles every time and the bisection's
error is the spin loop's step, a few cycles. Nothing else is in the
interval: no flash fetch (the Saw that calls it runs from flash, and an
instruction-cache fill from the flash, read on one line, takes hundreds of
SPI clocks), and no polling loop, whose period, a peripheral load and a
branch, would otherwise round every command's end up to its next read and
can hide a difference of a few core clocks altogether. There are four
measurements, each bisected `SEARCHES` (16) times by `psram_mode.saw`:

```
which  command                                      divider  differs from
1      array read, 0x0000, dummy 18, 2 bytes        4        -
2      the same, 10 bytes                           4        1 by 4 SPI clocks
3      MR0/MR1 read, 0x4040, dummy 8, 2 bytes (S8)  4        -
4      the same                                     8        3 by N x 4 core
                                                             clocks
```

- **The length.** In octal DTR the 8 bytes more are 4 SPI clocks, 16 core
  clocks at divide by 4: at a CPU of K kHz the core clock is 16 x K over
  the difference in cycles. That holds if the length reaches the wire: the
  read's MISO_DLEN(1) is `bits - 1`, `0x4f` for 10 bytes, as IDF's
  `spimem_flash_ll_set_miso_bitlen` writes it, which the smoke's trace
  holds; the wire is the part's to show.
- **The divider.** The MR0/MR1 read at divide by 8 takes 4 core clocks more
  for each of its N SPI clocks, chip-select setup and hold included, so the
  difference is N x 4 core clocks whatever the read costs besides. N is 1
  (command) + 2 (address) + 4 or 8 (dummy, as the controller counts it in
  DTR) + 1 (data) + 0 to 8 (setup and hold): 8 to 20. The image prints X,
  the N the difference gives if the core clock is 80 MHz: N under `pll80`,
  whose core clock is 80 MHz by `SPI_MEM_CORE_CLK_SEL`; under `rom`, N for
  H2, 2N for H1 and 4N for H3. So `rom`'s X over `pll80`'s is 1, 2 or 4
  whatever N is, and the two images settle the reading between them.

Expected, by reading, at a 20 MHz CPU under `rom` and 80 MHz under `pll80`
(core clock C, SPI clock C/4 for the fast measurements, C/8 for the slow
one):

```
                   C       length's difference   divider's difference
rom, H3            20 MHz  16 cycles             N x 4 cycles    X = 4N
rom, H1            40 MHz   8 cycles             N x 2 cycles    X = 2N
rom, H2            80 MHz   4 cycles             N cycles        X = N
pll80              80 MHz  16 cycles             N x 4 cycles    X = N
```

**The chip select.** AP Memory's datasheet for this chip (APS6408L-OBMx,
revision 3.9) limits the time chip select stays low, tCEM, to
8 us at standard temperature (-40 to 85 C; 3 us extended), so that the
chip can refresh its array; revisions before 3.6 gave 4 us and 1 us. The
longest of the measurement's commands, at the slowest reading (H3, a
20 MHz core):

```
command                         clocks   SPI clock   chip select low
MR0/MR1 read at divide by 8     8 to 20  2.5 MHz     3.2 to 8.0 us
array read, 10 bytes, divide 4  17 to 34 5 MHz      3.4 to 6.8 us
```

Both stay within 8 us; under H1 or H2 they take half or a quarter of it.
The limit protects the array's contents, which P2 never relies on: the
measurement reads the mode registers and an array nothing has written, and
the data it reads is not looked at.

### 16c. The steps, their writes and their stops

Register addresses are ESP-IDF v5.4.1's (`reg_base.h` bases plus each
header's offsets); `mspi.c` names the header and field at each. "ROM" is the
mask ROM's disassembly (§2a). Nothing below writes an eFuse, an RTC
register, the flash, SPI0's flash-side registers, SPI_MEM_CORE_CLK_SEL or
the pads the flash reads on (29 to 32). Pads 27 and 28, the flash's HD and
WP, are the octal bus's D3 and D2, and S1 takes them over.

```
step  writes, in order                                  source
entry the description and the record are DRAM            soc.h SOC_DRAM_LOW,
      (0x3FC8_8000 to 0x3FD0_0000), by address alone     SOC_DRAM_HIGH
S0.1  read EFUSE_RD_MAC_SPI_SYS_4 (0x6000_7054):         efuse_reg.h; the codes
      PSRAM_CAP [4:3], PSRAM_VENDOR [8:7]                esp_efuse_table.csv
S0.2  read RTC_CNTL_DIG_PAD_HOLD (0x6000_80DC):          ROM gpio_pad_unhold
      bits 5 to 7 and 12 to 16 (pads 26 to 28, 33 to     (0x4005_0220-023E)
      37, the pads S1 writes)
S0.3  read SYSCLK_CONF (0x600C_0060), CPU_PER_CONF       system_reg.h,
      (0x600C_0010), CORE_CLK_SEL (0x6000_30EC)          spi_mem_reg.h
S0.4  read SPI_MEM_CMD(1) until 0, bounded
S1    IO_MUX_GPIO26 (0x6000_906C): MCU_SEL 0 (SPICS1),   IDF s_init_psram_pins
        FUN_DRV 3
      IO_MUX_GPIO27, 28 (0x6000_9070, 0x6000_9074):      IDF esp_mspi_pin_init
        MCU_SEL 0 (SPIHD, SPIWP), FUN_PD 0, FUN_PU 1,    (spi_flash/flash_ops.c)
        FUN_IE 1, FUN_DRV 3                              -> ROM esp_rom_
                                                         opiflash_pin_config,
                                                         its default branch;
                                                         gpio_pad_pullup,
                                                         _input_enable; IDF
                                                         mspi_timing_ll_set_
                                                         all_pin_drive
      IO_MUX_GPIO33..37 (0x6000_9088 + 4n): MCU_SEL 4,   ROM esp_rom_opiflash_
        FUN_PD 0, FUN_PU 1, FUN_IE 1, FUN_DRV 3          pin_config (tables at
                                                         0x3FF1_90A8),
                                                         gpio_pad_pullup,
                                                         _input_enable; IDF
                                                         mspi_timing_ll_set_
                                                         all_pin_drive
      GPIO_FUNCn_IN_SEL_CFG, n 2, 3 and 7..11            ROM gpio_bypass_
        (0x6000_4154 + 4n): SIG_IN_SEL [7] cleared       matrix_in; the
                                                         signals of the ROM's
                                                         table for pads 27,
                                                         28 and 33 to 37
S2    SPI_MEM_SPI_SMEM_AC(0) (0x6000_30DC): CS_SETUP,    IDF s_set_psram_cs_
        CS_HOLD 1; CS_SETUP_TIME 3; CS_HOLD_TIME 3;      timing (OCT_PSRAM_*)
        CS_HOLD_DELAY 2
S3    SYSCON_SRAM_ACE0_ATTR (0x6002_6058) bit 8          IDF s_configure_psram_
        cleared, only if it reads set                    ecc (ECC off)
S4    SPI_MEM_SRAM_CLK(0) (0x6000_3050) = the divider    IDF mspi_timing_ll_
                                                         set_psram_clock
S5    save SPI1's CLOCK, CTRL, USER, USER1, USER2,       ROM esp_rom_opiflash_
        ADDR, MOSI_DLEN, MISO_DLEN, CACHE_FCTRL, DDR,    backup_reg (it saves
        MISC                                             five of them)
      SPI_MEM_CLOCK(1) (0x6000_2014) = the divider       IDF mspi_timing_ll_
                                                         set_flash_clock(1, _)
      SPI_MEM_DDR(1) (0x6000_20E0): DDR_CMD_DIS [4],     ROM set_op_mode mode 7
        WDAT_SWP [3], RDAT_SWP [2] cleared; VAR_DUMMY    (0x4004_DD48-DD69),
        [1], DDR_EN [0] set                              set_dtr_swap_mode;
                                                         IDF esp_psram_impl_
                                                         enable (VAR_DUMMY)
S6    SPI_MEM_MISC(1) (0x6000_2034) [1:0] = 0b01:        ROM esp_rom_spi_cmd_
        CS0_DIS 1, CS1_DIS 0; read back                  start (0x4004_E0E2-
                                                         E148)
S7    per command: wait; CTRL(1) = (CTRL & 0xFE6F9C0F)   ROM reset_rw_mode's
        | 0x2270 (FASTRD_MODE, FCMD_OCT, FADDR_OCT,      mask, mode 7's 0x2270
        FDIN_OCT, FDOUT_OCT)                             (0x4004_DD3D)
      USER(1) = (USER & 0x00FF0FFF) | COMMAND, ADDR,     ROM esp_rom_spi_cmd_
        and DUMMY, MISO or MOSI as the command needs     config
      USER1(1) = (31 << 26) | (dummy - 1);               the same; IDF
        USER2(1) = (15 << 28) | command                  s_init_psram_mode_reg
      ADDR(1) = address; CACHE_FCTRL(1) |= bit 1         the same (32-bit
                                                         address)
      MOSI_DLEN(1), W0(1) for a write; MISO_DLEN(1)
        for a read
      MISC(1) read again; then CMD(1) = SPI_MEM_USR      ROM esp_rom_spi_cmd_
        (bit 18) alone; wait; W0(1) read                 start
S8    MR0/MR1 read (0x4040, address 0, dummy 8, 16 bits) IDF s_get_psram_mode_
      MR2/MR3 read (address 2), MR4 and MR8 reads        reg, OPI_PSRAM_REG_
        (addresses 4, 8; 8 bits)                         READ, OCT_PSRAM_RD_
                                                         REG_DUMMY_BITLEN
S9    MR0 write (0xC0C0, address 0, 16 bits:             IDF s_init_psram_mode_
        MR1 << 8 | (MR0 & 0xC0) | 0x28); MR0/MR1 read    reg, esp_psram_impl_
        again                                            enable (lt 1, latency
                                                         2, drive 0)
S10   every word S5 saved written back, MISC last
time  S5 at the measurement's clock word, S6; per     IDF OPI_PSRAM_SYNC_
reads   probe S7 to the start, CCOUNT spun, CMD(1)       READ, OCT_PSRAM_RD_
        read once, S7.9's wait: 0x0000 (address 0,       DUMMY_BITLEN; the
        dummy 18, 2 or 10 bytes in) or 0x4040 (address  MR0/MR1 read as S8
        0, dummy 8, 16 bits in); S10
```

S5's write to DDR(1) is the whole of the mode-7 entry of the ROM's
`esp_rom_spi_set_op_mode`, which ESP-IDF calls for every PSRAM command:
besides the CTRL bits S7 sets, it clears DDR_CMD_DIS and sets DDR_EN, so
the command, address and data phases run on both clock edges. With
VAR_DUMMY set, the word from the reset value is `0x00000003`.

The only command word ever written to SPI_MEM_CMD(1) is SPI_MEM_USR. The
bits beside it start the controller's built-in flash commands (21 deep
power-down, 22 chip erase, 23 block erase, 24 sector erase, 25 page
program, 26 write status, 30 write enable); none is written, and each
destructive one also needs the flash's write-enable latch, which nothing
sets.

**The stops**, in the order the call makes them. Saw prints the line from
the record, then the caller halts with the code as its status. "S10" says
SPI1 is put back before the call returns.

```
0x50 entry  SOS-S3: psram stop: bring-up record at 0x........ or expectation
            at 0x........ is not in DRAM
0x51 S0.1   ... efuse word 0x60007054 = 0x........ says PSRAM_CAP=0x.
            PSRAM_VENDOR=0x., the board says 0x. (N MiB) and 0x. (AP n V)
0x52 S0.2   ... pads held, RTC_CNTL_DIG_PAD_HOLD = 0x........, the board
            needs pads 26-28 and 33-37 free
0x53 S0.3   ... clock SYSCLK_CONF=0x........ CPU_PER_CONF=0x........
            CORE_CLK_SEL=0x., the board's PSRAM divider is for
            SYSCLK_CONF&0x80fff=0x........ CPU_PER_CONF&7=0x. CORE_CLK_SEL=0x.
0x54 S0.4   ... SPI1 busy before any command, SPI_MEM_CMD = 0x........ after
            100000 polls
0x55 S3     ... SYSCON_SRAM_ACE0_ATTR = 0x........ still has ECC on after
            clearing it
0x56 S6     ... SPI1 MISC = 0x........ after selecting CS1 alone; nothing
            was sent                                                  (S10)
0x57 S7.1   ... SPI1 busy before command 0x.... (name), SPI_MEM_CMD =
            0x........ after 100000 polls                             (S10)
0x58 S7.8   ... SPI1 MISC = 0x........ before command 0x.... (name); not
            sent                                                      (S10)
0x59 S7.9   SOS-S3: psram: SPI1 command 0x.... did not finish   (from IRAM)
            ... SPI1 command 0x.... (name) did not finish, SPI_MEM_CMD =
            0x........ after 100000 polls; CS0 left disabled       (no S10)
0x5A S8.1   ... MR1 vendor = 0x.., the board says 0x0d (AP Memory)    (S10)
0x5B S8.2   ... MR2 = 0x.. has the good-die bit clear, the board says a
            good die                                                  (S10)
0x5C S8.2   ... MR2 density = 0x. (N MiB), the board says 0x. (N MiB) (S10)
0x5D S9.3   ... MR0 = 0x.. after writing 0x28                         (S10)
0x61 time   ... a timing measurement 0x........; the measurements are 1
            to 4                                     (before S5, nothing sent)
0x62 time   ... SPI1 command 0x.... (name) still busy 4096 cycles after it
            started, SPI_MEM_CMD = 0x........                         (S10)
```

A command's name is `MR0/MR1 read`, `MR2/MR3 read`, `MR4 read`, `MR8 read`,
`MR0 write` or `array read`. After 0x59 the call leaves MISC(1) holding the
gate, chip select 0 disabled, so a transfer still under way cannot be
turned towards the flash by a restore; its own line comes first, written to
UART0 from IRAM, in case a controller stuck mid-command also stalls SPI0's
fetches of the Saw that prints the full one. 0x57 restores SPI1 although
SPI_MEM_CMD(1) reads busy, because no command of this call is under way
then: the wait after the previous one read it finished, so the busy word is
not a transfer of ours to CS1, while at 0x59 the command the call started
may still be on the bus with CS1 selected. Before S5 nothing on SPI1 has
changed, so the stops before it need no S10. 0x61 and 0x62 are the
measurement's own: it was asked for a measurement it does not make, or a
probe at the spin limit found the command still busy (it then finished, so
S10 runs).

### 16d. Nothing in flash: `check_iram`

From S6 to S10 no instruction and no data may be fetched through the flash
cache: SPI1 holds the gate or a command is under way. So `mspi.c`'s
functions are all in `.text.iram`, whose literal pools the compiler puts in
`.literal.iram`, and `esp32s3.ld`'s `.iram` holds both, copied to IRAM by
`_start` with the vectors; it has no `const` data, no string, no jump
table, no call out of the file, and its one line of text is a `.data`
array named into its section, so the compiler cannot promote it to
`.rodata`. Each of its sections is dropped by the link when nothing calls
the layer, so every other image is the bytes it was without it.

`check_iram` (`tools/sos_runner.py`) holds that on every S3 image the smoke
links and every board image: it refuses, naming each finding, an `mspi.o`
with an allocated section of non-zero size other than `.text.iram*`,
`.literal.iram*`, `.data*` or `.bss*`; one that references a symbol it does
not define; and a linked image in which a symbol `mspi.o` defines lies
outside IRAM (0x4037_8000 to 0x4037_BFFF) and the kernel's DRAM
(0x3FC8_C000 to 0x3FCA_FFFF). The smoke's `iram_mutant_*` cases build
`mspi.c` with one slip added (`tests/xtensa/iram_mutants.c`) and require
the check to refuse each with its finding: a constant table and a dense
switch (`.rodata`), a 64-bit division (`__udivdi3`), a zeroed array
(`memset`), a function without its section (`.text.sos_psram_mutant`), and
the link script with its `.literal.iram .text.iram` line removed
(`sos_psram_bringup` linked in flash). `iram_check_clean` requires it to
pass the layer as it is, through the same path.

### 16e. The emulator and the smoke

The emulator's PSRAM is a model on SPI1's bus at CS1 (`hw/misc/ssi_psram.c`
in espressif/qemu): `-m` sizes it (no `-m`, no chip), and it is a quad chip
unless `-global ssi_psram.is_octal=true` is given; then its mode registers
read as an AP chip's at power-on (MR0 0x09, MR1 0x0d, MR2 0x93, MR3 0x20,
MR4 0x20, MR8 0x05), MR0 takes a write, and the density field follows `-m`
(4 MiB 1, 8 MiB 3, 16 MiB 5). The arch entry's `psram_qemu_args` are the
part's, `-m 8M -global ssi_psram.is_octal=true`. Found on these runs:

- SPI1 runs a user command inside the store to SPI_MEM_CMD and reads
  SPI_MEM_CMD 0 at once, so no wait can run out here; the fault build is
  what reaches the waits' stops.
- SPI1's MISC reads 0 at boot (the part reads `0x00000002`) and keeps what
  is stored, as do the other words S5 saves: a stop without S10 shows them
  changed (`psram_stop_not_finished`).
- SPI0, the IO MUX and the GPIO matrix are not modelled and read 0 (§15b),
  so what S1, S2 and S4 write cannot be read back here. The emulator's
  `memory_region_ops_write` trace shows every store all the same, by its
  whole address (the IO MUX lands in `esp32s3.iomem`, the input selects in
  `esp32.gpio`, SPI0's words in `esp32s3.iomem`, SPI1's in
  `ssi.esp32s3.spi`), and a case's `device_writes` holds the last store to
  each address it names, its `device_stores` a store among them for a word
  a later step puts back, and its `device_untouched` an address the kernel
  stores nothing to: no store from `_start`'s first, the RTC watchdog's key
  (§2e), on. A `kernel_writes` row is a `device_writes` row whose address
  must also be stored to from that first store on, so a word the ROM left
  at the expected value does not pass for the image's. The ROM's boot
  stores to pads 27 to 32 before the kernel runs, leaving 29 and 30 at 0
  and 31 and 32 at `0x200`; the last store to 27 and 28 is S1's.
- The eFuse word is read from a file given with `-drive file=F,if=none,
  format=raw,id=efuse -global driver=nvram.esp32s3.efuse,property=drive,
  value=efuse`: six words of block 0, then block 1, so
  EFUSE_RD_MAC_SPI_SYS_4 is bytes 40 to 43 (`0x88` for the part). With no
  file it reads 0. The runner writes a fresh file for every run.
- The flash model's trace (`-trace m25p80_command_decoded -D FILE`) shows
  every command the flash decodes. On every PSRAM run it is the ROM's one
  at boot, `0xab`; with the gate removed (MISC [1:0] written 0b00) each MR
  read reaches the flash too, as `0x40`.
- A command ends inside the store that starts it, so every bisection of
  the measurement answers 0 and `psram_mode` prints both differences as
  not measured. The CPU's kHz is held: the case runs on the emulator's
  instruction-counted clock (the case key `icount`, `-icount
  shift=ICOUNT_SHIFT`), where CCOUNT (40 MHz) and SYSTIMER (16 MHz) count
  the same virtual time, so it reads the same on every run of one image,
  within a few percent of 40000, and is held to 36000 to 44000
  (`expect_number`): a unit, a window or an end out of place moves it by a
  factor. On the host's clock it is host time (`psram_mode_pll80`). What
  the measurement stores to SPI1 is held all the same: CLOCK(1) at divide
  by 8, `0x00070307`, and MISO_DLEN(1) at 80 bits, `0x4f`, for the long
  array read.

**The fault build.** Every register read in `mspi.c` goes through `rd`;
built with `-DSOS_PSRAM_FAULT`, `rd` consults the DRAM record
`sos_psram_fault`, filled in by the harness before the call: the read at
that step (`mspi.c`'s STEP_* values), in that command (1 for the call's
first), of that address, returns the record's value that many times. Writes
always reach the register. The record also counts the reads it answered,
the commands started and the answers read. The case key `psram_fault`
builds an image's profile with the define; the runner refuses any image
that defines `sos_psram_fault` unless it is `psram_fault.saw`'s or the
flash read's harness, `flash_fault.saw` (§18b), and any board image that
defines it (`check_psram_fault`; the cases
`psram_fault_outside_harness` and `psram_fault_board_image`). An image
the checks refuse is deleted from the build directory, and the board
image's case requires that it is gone.

**The cases.** Each PSRAM case boots with the case's description, its
emulator arguments and eFuse file, and asserts its lines, its halt and the
flash's trace (`flash_commands`, `["0xab"]`). "As before" is the SPI1 line
`SPI1 MISC=0x00000000 clock=0x00030103 after the call, as before it`, every
word S5 saves reading as it did before the call: untouched for a stop
before S5, put back for one after. `psram_identity` also holds every word
S1 stores, from the 0 each pad and input select reads here: pad 26
`0x0c00`, pads 27 and 28 `0x0f00`, pads 33 to 37 `0x4f00`, the input
selects 0; that pads 29 to 32 keep the ROM's words and that the kernel
stores nothing to them, to pad 38, or to the input selects of signals 4 to
6 and 12; and the four `_word` cases feed S1 the part's own words for pads
27 and 28 (`0x1b00`, and for 28 `0x1b80`, its pull-down on too) and for
signals 2 and 3's input selects (`0xbc`, bit 7 set), and hold what it
stores (`0x0f00`, `0x3c`). `psram_identity` holds S2's and S4's stores to
SPI0 too, from the 0 its words read here: SPI_MEM_SPI_SMEM_AC(0)
`0x0400018f` (CS_SETUP, CS_HOLD, CS_SETUP_TIME 3, CS_HOLD_TIME 3,
CS_HOLD_DELAY 2) and SPI_MEM_SRAM_CLK(0) `0x00030103`; and S5's store of
`0x3` to SPI_MEM_DDR(1) (VAR_DUMMY and DDR_EN, the swaps and DDR_CMD_DIS
clear), which S10 then puts back to 0; and two of S7's for a register
read, SPI_MEM_CTRL(1) `0x0020a270` (the octal bits over the ROM's
`0x00208000`) and SPI_MEM_USER1(1) `0x7c000007` (a 32-bit address, 8 dummy
cycles).

```
case                              how                                   stop
psram_identity                    P1; the identity lines; as before      -
psram_identity_pll80              the same at `pll80`, the key run first -
psram_mode                        P2; MR0 0x09 -> 0x28; the four         -
                                  measurements at 0, SPI1's divide by 8
                                  and 80-bit length stored; as before
psram_mode_pll80                  the same at `pll80`, the key run first -
psram_identity_commands           fault build, no fault: 4 commands,     -
                                  4 answers read
psram_time_commands               one bisection of the MR0/MR1 read: 14  -
                                  probes, no answer read, answer 0
psram_time_bisect                 the same, the probe at spin 2048 read  -
                                  busy: 13 probes, answer 2049
psram_pad27_word                  S1 on `0x1b00`: `0x0f00` stored        -
psram_pad28_word                  S1 on `0x1b80`: `0x0f00` stored        -
psram_insel2_word                 S1 on `0xbc`: `0x3c` stored            -
psram_insel3_word                 S1 on `0xbc`: `0x3c` stored            -
psram_identity_divider_2          P1 at divide by 2, `0x00010001`: SPI1  -
                                  ran at it and its clock is put back
psram_stop_expect_in_flash        description at 0x3c200000              0x50
psram_stop_record_in_flash        record at 0x3c200000                   0x50
psram_stop_time_reads_in_flash    the measurement, description in flash  0x50
psram_stop_expect_past_dram       description at 0x3fcffff0, its end     0x50
                                  past DRAM's
psram_stop_measurement            the measurement asked for number 5     0x61
psram_stop_still_busy             the first probe, at the spin limit,    0x62
                                  read busy
psram_stop_efuse_none             no eFuse file                          0x51
psram_stop_efuse_16m              PSRAM_CAP 3                            0x51
psram_stop_efuse_1v8              PSRAM_VENDOR 2                         0x51
psram_stop_pad_held               S0.2's read: pad 26 held               0x52
psram_stop_pad33_held             S0.2's read: pad 33 held               0x52
psram_stop_pad37_held             S0.2's read: pad 37 held               0x52
psram_stop_pad27_held             S0.2's read: pad 27 held               0x52
psram_stop_pad28_held             S0.2's read: pad 28 held               0x52
psram_stop_clock_pre_div          described SYSCLK_CONF with PRE_DIV 0   0x53
psram_stop_clock_cpu_per          described CPU_PER_CONF 4               0x53
psram_stop_clock_core             described CORE_CLK_SEL 1               0x53
psram_stop_busy_before            S0.4's reads: busy, every one          0x54
psram_stop_ecc                    S3's reads: ECC on, both               0x55
psram_stop_gate                   S6's reads of MISC: 0b00               0x56
psram_stop_busy_command           S7.1's reads in command 2: busy;       0x57
                                  command 1 ran
psram_stop_gate_lost              S7.8's read in command 1: 0b00; no     0x58
                                  command started, no answer read
psram_stop_not_finished           S7.9's reads in command 1: busy; the   0x59
                                  call's line first, MISC left 0b01
psram_stop_vendor_quad            `-m 8M`, the quad model: MR1 0         0x5A
psram_stop_vendor_none            no `-m`: no chip                       0x5A
psram_stop_die                    MR2's answer with bit 7 clear          0x5B
psram_stop_density_4m             `-m 4M`, octal                         0x5C
psram_stop_density_16m            `-m 16M`, octal                        0x5C
psram_stop_mr0                    the sixth command's answer: MR0 0x09   0x5D
```

Every stop but 0x59 is followed by SPI1 as before. No case reaches 0x52,
0x54 to 0x59 or 0x5B as a real event: the hardware conditions behind them
(a held pad, ECC stuck on, a hung controller, the gate not taking) are read
in the sources, not tested; their code paths are what the fault build
tests.

**What the emulator cannot show**, and only a part can: the words S1, S2,
S4 and S5 store taking effect, which the cases hold only as stores (the
pads' functions, pulls and drives, read back by P1's pads line; SPI0's
chip-select timing and PSRAM clock; octal DTR on SPI1); the dummy cycles
and SPI1's clock on the wire (S7); the chip's answers at power-on and after
an earlier MR0 write; and the measured core clock.

### 16f. The board stages

Both images write the PSRAM's pads, SPI0's PSRAM timing and clock, SPI1
(put back), and, for P2, the chip's MR0; nothing non-volatile. A power
cycle undoes every one, and so does a reset through EN (esptool's, or the
button: `rst:0x1 (POWERON)`), MR0 included: on the part every P1 after a
P2 and a reset reads MR0 0x09, its power-on value. They are built with
`python3
tools/sos_runner.py --board esp32s3 --emit-flash psram_identity|psram_mode
--clock KEY --out PATH`, the description carrying the part's clock words at
KEY (§16b).

**P1, `psram_identity`.** Expected, the cycle count printed for the record:

```
SOS psram: efuse PSRAM_CAP=0x00000001 PSRAM_VENDOR=0x00000001
SOS psram: sysclk=<as §14h for KEY> cpu_per=0x0000000c
           core_clk_sel=0x00000000 spi0 clock=0x00030103
           sram_clk=0x00030103 spi1 clock=0x00030103
SOS psram: SPI1 MISC=0x00000001 before the first command
SOS psram: MR0=<v> MR1=0x0000000d MR2=<bit 7 set, [2:0] 3> MR3=<v>
           MR4=<v> MR8=<v>
SOS psram: MR0/MR1 read took <v> CCOUNT cycles
SOS psram: pads 26=0x00000f00 27=0x00000f00 28=0x00000f00 33=0x00004f00
           34=0x00004f00 35=0x00004f00 36=0x00004f00 37=0x00004f00 insel
           2=0x0000003c 3=0x0000003c 7=0x0000003c 8=0x0000003c
           9=0x0000003c 10=0x0000003c 11=0x0000003c
SOS psram: SPI1 MISC=0x00000002 clock=0x00030103 after the call, as before it
SOS-S3: halt pass
```

The pads line is S1's words read back on the part, from §15c's: pad 26
from the GPIO function to SPICS1 with drive 3; 27 and 28 from the GPIO
function to SPIHD and SPIWP with drive 3, their pull-up and input kept;
33 to 37 to function 4 with the pull-up and drive 3; and every input
select with bit 7 clear. Signals 2 and 3's selects were not read by the probe, so `3c`
there is the expectation from signals 7 to 11's.

(each wrapped line is one line). MR0 is the chip's power-on value, 0x09.
The part's MR3 is 0x60 (bit 6, VCC 3 V; bit 5, the fast-refresh flag) and
its MR4 0x40 (write latency code 010, 5, the power-on default), where the
emulator reads 0x20 for both.

**P2, `psram_mode`.** The measurement of §16b; every number but MR0's is
the part's:

```
SOS psram: MR0=0x00000009 before, 0x28 written, MR0=0x00000028 after
SOS psram: CPU <K> kHz by SYSTIMER, <Y> cycles in <T> ticks; the first
           pass <K1> kHz, <Y1> cycles in <T1> ticks
SOS psram: array read ends <A> and <B> cycles after its start for 2 and 10
           bytes at divide by 4
SOS psram: MR0/MR1 read ends <C> cycles after its start at divide by 4,
           <D> at divide by 8
SOS psram: core clock <F> MHz by 4 SPI clocks x 4 a read
SOS psram: MR0/MR1 read <X> SPI clocks if the core clock is 80 MHz
SOS psram: SPI1 MISC=0x00000002 clock=0x00030103 after the call, as before it
SOS-S3: halt pass
```

(each wrapped line is one line). K is CCOUNT's advance Y over T of
SYSTIMER's ticks, at least 80000 (5 ms), times 16000 over T: the second of
two passes (§16), the first, K1, printed beside it from Y1 and T1, so the
run shows what a pass that starts cold reads against one that does not.
A to D are CCOUNT cycles, the mean of 16 bisections; F is 16 x K over 1000 x (B - A) per bisection, and X is
(D - C) per bisection x 80 over 4 x K / 1000. A difference that is not
positive prints `core clock not measured: the 10-byte read ended no later`
or `MR0/MR1 read not measured: it ended no later at divide by 8` in its
place. Read together:

- `pll80`'s X is the read's N, 8 to 20 (§16b), and its F 80 when the
  length reaches the wire.
- `rom`'s X over `pll80`'s is 1, 2 or 4: H2, H1 or H3. Its F, if
  measured, names the same reading: 80, 40 or 20 MHz. At a 20 MHz CPU the
  length's 16 core clocks are 16, 8 or 4 cycles, so F's error is a quarter
  under H3 and grows under the others; X is the deciding number.
- At `xtal40`, the same with H1 and H3 both 40 MHz.
- D - C is the part of the MR0/MR1 read at divide by 4 that is SPI
  clocks, in CPU cycles; beside P1's count for the whole command (setup,
  start, wait and answer) it says how much of that is the bus.

A ratio far from 1, 2 or 4, or an X outside 8 to 20 at `pll80`, ends the
series until it is understood.

**The reading is H3.** On the part every interval is the same number of CPU
cycles at `rom` and at `pll80` (72 and 86, 51 and 107), so the memory-SPI
core clock moves with the CPU's: at `rom` it is the CPU's clock after the
pre-divider, 20 MHz, and at `pll80` it is 80 MHz. The MR0/MR1 read is 14
SPI clocks (`pll80`'s X), 56 cycles of it at divide by 4 against P1's
0x12b (299) for the whole command; `rom`'s X is 56.0, four times
`pll80`'s. The length's 4 SPI clocks read 14 cycles where 16 are expected,
within the bisection's step, so F comes out 91.4 MHz at `pll80` and 22.9 at
`rom`: H3's 80 and 20, not H1's or H2's. Each first pass reads lower than
the second, most at `rom` (19290 kHz against 19999), where a cold fetch
from the one-line flash costs the most cycles; the second pass is the one
P2 uses.

| image | part |
|---|---|
| P1 at `rom` | MR0 0x09, MR1 0x0d, MR2 0x93, MR3 0x60, MR4 0x40, MR8 0x05; MR0/MR1 read 0x12b CCOUNT cycles; pads and SPI1 as above |
| P1 at `pll80` | as at `rom`, with `sysclk=0x000a8400`; MR0/MR1 read 0x12b CCOUNT cycles |
| P1 after P2 and a reset | MR0 0x09; the rest as P1 at `rom` |
| P2 at `rom` | MR0 0x09 -> 0x28; A 72.0, B 86.0, C 51.0, D 107.0; K 19999 (first pass 19290), F 22.9, X 56.0; H3, the core at 20 MHz |
| P2 at `pll80` | MR0 0x09 -> 0x28; A 72.0, B 86.0, C 51.0, D 107.0; K 80000 (first pass 79555), F 91.4, X 14.0; H3, the core at 80 MHz |

---

## 17. The PSRAM mapped, tested and published: the `esp32s3-r8` board

The part's 8 MiB of octal PSRAM becomes process RAM in four more steps after
§16's: SPI0's PSRAM side (S11) and the cache's map (S12), both in the same
call from IRAM; the read-back test (S14) and the PSRAM published (S15), in
Saw. A board described as having PSRAM runs them at every boot, from
`memory_init`, and either publishes the PSRAM or stops the machine; it
never boots on without it. The `esp32s3` board describes none and never
runs them.

- `kernel/mspi.c` makes S11 and S12 when `last_step` reaches them
  (`STEP_S11`, `STEP_S12`), after S10 has put SPI1 back.
- `kernel/lib.saw`'s last section is the Saw half of the whole bring-up:
  the description (`psram_expect`, `psram_description`), the read-back test
  (`psram_read_back`), its stop line and the success line, and `psram_up`:
  the call through S12 (`psram_map_up`), then the test and its line
  (`psram_test_up`). Its `memory_init` writes nothing, and its
  `psram_bytes` answers 0.
- `../xtensa-esp32s3-r8/` is the `esp32s3-r8` board: a facade that
  re-exports this HAL as the module `s3hal` and answers `memory_init`,
  `psram_bytes`, `map_target_ok`, `map_source_ok` and `segment_bus_ok`
  (its README). Its `board.toml` is this board's with the PSRAM's range.

### 17a. The order at boot, and the clock

`kmain` calls `clock_init`, prints the banner, then calls `memory_init`
before the interrupt controller and the loader. Any clock change is
`clock_init`'s, the image's clock key's (§14d), and nothing after
`memory_init` writes SYSTEM_SYSCLK_CONF, SYSTEM_CPU_PER_CONF or
SPI_MEM_CORE_CLK_SEL: the PSRAM's bus is the memory-SPI core clock over the
divider S4 wrote, and a clock change would move it under every process in
PSRAM. The runner's `check_clock_registers` holds the sources of this HAL
and of the facade to it: a file that names one of the three registers in
code, by address or by name, is a clock key's module, `boot.S` (whose BBPLL
bring-up only the PLL keys call), or `mspi.c`, which names each by its
`#define` and reads it with `rd` alone (S0.3); and `kernel/main.saw` calls
`hal.clock_init()` before `hal.memory_init()`. Its cases are the
`clock_registers_*` rows of §17d.

### 17b. The description

The description is the facade's, since the kernel needs it at boot, and has
three parts:

- **The size**, 8 MiB. One fact sets both codes the part is held to: the
  eFuse's PSRAM_CAP (1 for 8 MiB, 3 for 16) and MR2's density (3 for
  8 MiB, 5 for 16). AP Memory's vendor (0x0D) and the eFuse's PSRAM_VENDOR
  1 (AP at 3.3 V) are fixed (`psram_expect`).
- **The clock**, the image's clock key's: each key's module states the
  words it leaves on the part (`PART_SYSCLK`, `PART_CPU_PER`,
  `PART_CORE_CLK_SEL`, from §14h) and the memory-SPI core clock they make
  (`MSPI_CORE_MHZ`, §16b). A board image carries the part's words, and an
  image for the emulator the emulator's (§16b's table), chosen by the
  linker constant that tells the two apart, `_sos_semihosting` (§10): 0 in
  a board image (`board_image`). The two machines answer S0.3 differently,
  and the part must match its description exactly.
- **The divider**, for a 20 MHz bus: the key's core clock over 20, and
  never less than 2, as IDF's `mspi_timing_ll_set_psram_clock` encodes it
  (`psram_divider_word`, `PSRAM_DIVIDER_LEAST`). 20 MHz is the speed
  ESP-IDF first talks to the chip at, half the 40 MHz past which it tunes
  octal PSRAM. Divide by 1, a bus at the memory-SPI core clock itself, is
  never used: at `rom`, whose core clock is 20 MHz, stage P3 at divide by 1
  read MR2 back as 0x09, the mode registers shifted, and stopped at S8.2
  (0x5B), and nothing here tunes the controller's input timing, as ESP-IDF
  does before it raises a bus that far. So `rom` runs the bus at 10 MHz,
  divide by 2, as `xtal40` runs cleanly on the part at a 40 MHz core.
- **The chip select at 10 MHz.** A cache fill holds chip select low for the
  command (1 clock), the address (2), the read latency (10 to 20, the
  second figure when a refresh doubles it) and the data at two bytes a
  clock: 21 to 31 clocks for the part's 16-byte line, 29 to 39 for a
  32-byte line and 45 to 55 for 64 bytes. At 10 MHz that is 2.1 to 3.1 us,
  2.9 to 3.9 us and 4.5 to 5.5 us, inside AP Memory's tCEM of 8 us at
  standard temperature (§16b) at every line size the data cache takes; at
  20 MHz, half as long. A write-back is its own transaction, as short.

```
key       part's SYSCLK_CONF  CPU_PER_CONF  core clock  divider  word
          & 0x80FFF           & 7
rom       0x00080001          0x4           20 MHz      2        0x00010001
xtal40    0x00080000          0x4           40 MHz      2        0x00010001
pll80     0x00080400          0x4           80 MHz      4        0x00030103
pll160    0x00080400          0x5           80 MHz      4        0x00030103
pll240    0x00080400          0x6           80 MHz      4        0x00030103
emulator  0x00080401          0x0           -           the key's
```

`pll240`'s are its board run's words, `sysclk` 0x000a8400 and `cpu_per`
0x0000000e. The runner's `PSRAM_PART_CLOCK` holds the same words for the
command layer's harnesses (§16a), whose description keeps divide by 4, the
divider stages P1 and P2 ran at.

### 17c. The steps

```
step  writes, in order                                  source
S11   SPI_MEM_CACHE_SCTRL(0) (0x6000_3040), RMW:        IDF s_config_psram_
        CACHE_USR_SCMD_4BYTE [0], USR_WR_SRAM_DUMMY      spi_phases;
        [3], USR_RD_SRAM_DUMMY [4], CACHE_SRAM_USR_RCMD  OCT_PSRAM_RD_DUMMY_
        [5], CACHE_SRAM_USR_WCMD [20], SRAM_OCT [21]     BITLEN, _WR_DUMMY_,
        set; SRAM_RDUMMY_CYCLELEN [11:6] 17,             _ADDR_BITLEN
        SRAM_ADDR_BITLEN [19:14] 31,
        SRAM_WDUMMY_CYCLELEN [27:22] 7
      SPI_MEM_SRAM_DRD_CMD(0) (0x6000_3048), RMW:        OPI_PSRAM_SYNC_READ
        [31:28] 15, [15:0] 0x0000
      SPI_MEM_SRAM_DWR_CMD(0) (0x6000_304C), RMW:        OPI_PSRAM_SYNC_WRITE
        [31:28] 15, [15:0] 0x8080
      SPI_MEM_SPI_SMEM_DDR(0) (0x6000_30E4), RMW:
        DDR_EN [0], VAR_DUMMY [1] set; RDAT_SWP [2],
        WDAT_SWP [3] clear
      SPI_MEM_SRAM_CMD(0) (0x6000_3044), RMW: SDIN_OCT,
        SDOUT_OCT, SADDR_OCT, SCMD_OCT, SDUMMY_OUT
        [22:18] set
S12   MMU entries 256 + n (0x600C_5400 + 4n) =           IDF mmu_ll_write_
        0x8000 | n, for one entry per 64 KiB of the      entry, s_psram_
        described density (128 for 8 MiB); each read     mapping; ext_mem_
        back; EXTMEM_DCACHE_CTRL1 (0x600C_4004) read:    defs.h; extmem_reg.h
        bits 0 and 1 must be clear                       and cache_ll_l1_
                                                         enable_bus, which
                                                         disagree
S14   (Saw) every eighth word of the described size at   IDF esp_psram.c,
        0x3D00_0000 written with its word index ^        s_test_psram
        0xAAAAAAAA, then each read back
S15   (Saw) `psram_bytes` answers the described size
```

On the part SPI0's PSRAM side reads `sctrl` `0x0055c070`, `smem_ddr`
`0x00003020` and the rest 0 (§15c), so S11 leaves `sctrl` `0x01f7c479`,
`smem_ddr` `0x00003023`, `drd` `0xf0000000`, `dwr` `0xf0008080` and
`sram_cmd` `0x007c0000`. None of SPI0's flash-side words is written, and no
PSRAM page is mapped while these change, so no access reaches chip select 1
then.

The map leaves entries 0 to 255 the ROM's flash pages, which the kernel and
every process's code and read-only data are read through; the replaced
entries named flash pages past the 4 MiB flash, which nothing reads. One
table serves both buses, so the PSRAM's pages are also instruction-bus
0x4300_0000 up. That is why `FLASH_WINDOW_LEN` is 16 MiB on both boards:
`in_flash_window` never takes an address of the PSRAM's under either bus's
name, so `same_bytes` never pairs 0x4300_0000 with 0x3D00_0000 and no code
is used in place there. The boards' `[flash]` windows and the runner's
`ESP32S3_FLASH_WINDOW` are the same 16 MiB, and the build's in-place check
reads them.

No cache operation is made: nothing has read the PSRAM's addresses since
reset, so no line of them is in the data cache, and ESP-IDF maps the PSRAM
the same way. The read-back test spans 8 MiB, many times the largest data
cache (64 KiB), so every word it writes has been written back to the chip
before the read pass reaches it, and every read is a fill from the chip; a
chip smaller than its description aliases, and a later word reads back where
an earlier one was.

**The stops**, after §16c's, in the order the boot makes them. Each prints
its line; `memory_init` then halts with the code as its status.

```
0x5E S12.2  SOS-S3: psram stop: MMU entry 0x... reads 0x........, wrote
            0x........                                (SPI1 already put back)
0x5F S12.3  SOS-S3: psram stop: data bus off, EXTMEM_DCACHE_CTRL1 =
            0x........
0x60 S14    SOS-S3: psram stop: read-back at 0x3d...... read 0x........,
            wrote 0x........ (0x........ of 0x........ words wrong)
```

On success, one line, then the boot goes on:

```
SOS-S3: psram 8 MiB at 0x3d000000, MR0=0x.. MR1=0x.. MR2=0x.. MR3=0x..,
read-back ok
```

MR0 and MR1 are S9's read-back, MR2 and MR3 S8's.

### 17d. The emulator and the smoke

The cache's model (`hw/misc/esp32s3_cache.c`) translates an access through
an entry with the PSRAM bit into the octal chip's memory directly, at once
and every time: no line is held, nothing is evicted, a rewritten entry
takes effect at the next access, and EXTMEM's access and miss counters read
0. SPI0 is not modelled, so S11's words read back 0 and are held as the
stores the trace shows. The `esp32s3-r8` board's runs get the part's PSRAM
and eFuse as §16e's harnesses do, and the flash model's trace, which shows
the ROM's one command (`0xab`) and nothing more on every run.

```
case                        board    what
psram_map                   esp32s3  the fault build through S12, no fault:
                                     six commands, S11's words, the map's
                                     first and last entries stored, and
                                     entries 255 and 384 not
psram_spi0_only             esp32s3  through S11: S11's words, no MMU entry
psram_stop_mmu, _top        esp32s3  entry 300's read-back lied to, and
                                     the last entry's, 383: 0x5E
psram_stop_bus_off, _1      esp32s3  DCACHE_CTRL1 read with bit 0 set, and
                                     with bit 1: 0x5F
psram_clock_<key>           r8       the description at rom, xtal40, pll80,
                                     pll240: the emulator's words and the
                                     key's divider
psram_clock_board_<key>     r8       a board image's at rom, pll80, pll240:
                                     the part's words; booted, it halts in
                                     its quiet loop (`quiet_halt`)
psram_word, _pll80          r8       P3: the call through S12 at the key's
                                     divider, which SRAM_CLK(0) and
                                     CLOCK(1) are stored with; one word
                                     through the map
psram_probe                 r8       P4: `memory_init`; the test's first
                                     and last words, 0xaaaaaaaa and
                                     0xaab55552; every width, `s32c1i` and
                                     the instruction-bus alias
psram_speed                 r8       P5: the measurements' lines
systimer_read               r8       the cost of a SYSTIMER read: the
                                     measurements' lines (§6a)
psram_wrong_size            r8       P7: a 16 MiB description on the 8 MiB
                                     eFuse: 0x51, nothing sent
psram_up_16m                r8       the same description on `-m 16M` and a
                                     16 MiB eFuse: 256 entries, the test over
                                     16 MiB, 16 MiB published
psram_stop_read_back        r8       `psram_map_up`, entries 320 and 383
                                     rewritten to pages 0 and 126, one in
                                     each half, `psram_test_up`: 0x60, 4096
                                     of 262144 words wrong
psram_ranges, _s3           both     the process-RAM and flash rules before
                                     and after `memory_init` (§17e)
psram_boot                  r8       `root_server_boot` on this board: the
                                     success line, then root
psram_boot_no_efuse         r8       the shipping kernel stopped at
                                     `memory_init` (0x51) before root
<gate case>_r8              r8       every process case of the S3's again,
                                     the success line first (§17e)
psram_masked                r8       P9: the copy door's byte loop over an
                                     evicted PSRAM buffer and over SRAM,
                                     CCOUNT around each; the lines only
stock_heap_psram            r8       a data heap in the PSRAM and the
                                     default heap in the SRAM: word atomics
                                     on a block of the first, and every
                                     block of each inside its range (§17e)
psram_stress                r8       P8: `s32c1i` increments on PSRAM words
                                     from four threads under the tick, on
                                     the instruction-counted clock: every
                                     count exact, the plain control short
                                     (§17g)
psram_stress_no_tick        r8       the same image with no tick: every
                                     count exact, the control loses
                                     nothing, and the run fails for it
clock_registers_clean       -        `check_clock_registers` passes the tree
clock_registers_<slip>      -        it refuses a store in `mspi.c`, in this
                                     HAL and in the facade, and `memory_init`
                                     before `clock_init`, and passes a store
                                     a clock key makes
```

What the emulator cannot show: S11's words taking effect, any fill or
write-back, the cost of a miss, `s32c1i` on cached external memory (it
allows it, as it does at DRAM), and the instruction cache filling from the
chip; stages P3 to P5 and P8 are the part's.

### 17e. The PSRAM as process RAM

Once published, the PSRAM is process RAM beside the SRAM range:
`map_target_ok` and `map_source_ok` take a range inside either, and
`segment_bus_ok` a writable or read-only segment copied into either; a range
that reaches into both is refused, since the bytes between them are flash
and the rest of the address space. Code is still used only in place in the
flash instruction window. Before `memory_init` has published it, and on the
`esp32s3` board always, the PSRAM is none of these (`process_ram`,
`psram_ranges`).

The board's `[ram.psram]` range is `slow` and states word atomics, on
stage P8's result (§17g). A process's region holds the runtime's own atomic
words, its locks, its heap table and its start-up flag, and a heap's pool
may hold a program's, so the image builder places a process or a heap's
pool only in a range where word atomics work, and on this board the PSRAM
is one; being slow, it takes no process or heap that declares
`ram = "fast"`. The PSRAM's pads, 33 to 37, are refused to every driver's
configuration on every board, as the flash bus's are (`drivers/esp32s3io`'s
`check_pin`): on a part with octal PSRAM in its package they are wired to
the RAM chip. Root's region is in SRAM (`ROOT_LOAD_BASE`), so the default
process range is, and so is the default pool. In the smoke the Blade-built
processes are placed in SRAM as on the `esp32s3` board, and the two boards
share their images; a hand-assembled child's destination
(`asm_child_region_base` 0x3D00_0000) and the pool (`pool_base`
0x3D04_0000) are PSRAM, so `image_held_r8` runs a child whose stack the
kernel grants in PSRAM, and the pool and heap cases cut PSRAM. Two are left
out, `pool_back` and `pool_odd_len`, whose package carries the `esp32s3`
board's pool base (`tests/poolbase_xtensa.c`) and checks the pool's place
against it, since both boards share their images; so is every case built
from a hand-written image, which names its board, but for
`stock_heap_psram`, whose image names this one, and every case whose build
is refused. The smoke prints the list. A case that boots a harness
kernel of its own runs with the PSRAM not brought up, unless the harness
calls `memory_init`, as `psram_stress`'s does.

### 17f. The board stages

Each is built with `python3 tools/sos_runner.py --board esp32s3 --emit-flash
CASE --clock KEY --out PATH`; the description is the part's at KEY. Every
image writes what §16f's do, then SPI0's PSRAM side and MMU entries 256 to
383, all volatile: a reset through EN puts them back, and the PSRAM loses
everything at power-off. Nothing writes an eFuse or the flash, and the
flash's chip select is never selected after the ROM's own reads. `<v>` is a
value the part prints and the record keeps.

**P3, `psram_word`.** The first image in which a cache fill reaches the chip.

```
SOS psram: described sysclk=<the key's> cpu_per=<the key's>
           core_clk_sel=0x00000000 divider=<the key's>
SOS psram: mapped 0x3d000000-0x3d7fffff, entries 0x00000100-0x0000017f read
           back
SOS psram: word at 0x3d000000 0x5aa5c33c after eviction, misses=<v>
SOS-S3: halt pass
```

`misses` is at least 1 on a part, where the word is filled from the chip.

**P4, `psram_probe`.**

```
SOS-S3: psram 8 MiB at 0x3d000000, MR0=0x28 MR1=0x0d MR2=<v> MR3=<v>,
read-back ok
SOS psram: up and tested in <v> us, published 8388608 bytes
SOS probe: word load, PSRAM at 0x3d000000
SOS probe: ok 0xaaaaaaaa
SOS probe: word load, PSRAM at 0x3d7fffe0
SOS probe: ok 0xaab55552
SOS probe: word store and load, PSRAM at 0x3d000000
SOS probe: ok 0x5a5aa5a5
... (each step, and `ok <value>` or `refused cause=... vaddr=...`)
SOS probe: word store, then s32c1i swapping it, PSRAM at 0x3d000010
SOS probe: ok 0x00000005 | refused cause=<v> vaddr=0x3d000010
SOS probe: word stored at 0x3d000100 and evicted, word load at 0x43000100
SOS probe: ok 0xc3d2e1f0
SOS probe: done
SOS-S3: halt pass
```

(each wrapped line is one line). The emulator's values are `psram_probe`'s.
A refused `s32c1i` means a process in PSRAM cannot use word atomics there,
and the board's PSRAM range keeps stating none.

**P5, `psram_speed`.** P4's bring-up, then the measurements its header
lists, each `<v> ticks, <v> accesses, <v> misses`; the record keeps every
number.

**P8, `psram_stress`.** The word atomics under stress; §17g has its text
and how to read it. Its board images are built at `pll80` and `pll240`.

**P9, `psram_masked`.** What a kernel copy door costs with interrupts
masked over a buffer in PSRAM (§11). Built at `pll80` and `pll240`, its
lines are

```
SOS masked: clock sysclk=<the key's> cpu_per=<the key's>
SOS masked: copy_in 32 bytes, PSRAM, lines evicted: min <v> max <v> cycles
SOS masked: copy_in 32 bytes, SRAM: min <v> max <v> cycles
... (the same at 128 and 256 bytes)
SOS masked: copy_in 128 bytes, PSRAM, lines held: min <v> max <v> cycles
SOS masked: copy_out 128 bytes, PSRAM, lines evicted: min <v> max <v> cycles
SOS masked: copy_out 128 bytes, SRAM: min <v> max <v> cycles
SOS-S3: halt pass
```

The loop is the door's (`kcore.mem.copy_bytes`, which the kernel's package
keeps to itself), run eight times for each line, and timed by CCOUNT around
the loop alone; each count holds the two CCOUNT reads' own cycles, which the
SRAM lines show. A count is in cycles of the key's CPU clock, 80.00 MHz at
`pll80` and 240.02 MHz at `pll240` (the clock lines). The emulator's
counts are host time and its cache has no lines, so the case holds the lines
only.

**P7, `psram_wrong_size`.**

```
SOS-S3: psram stop: efuse word 0x60007054 = <v> says PSRAM_CAP=0x1
PSRAM_VENDOR=0x1, the board says 0x3 (16 MiB) and 0x1 (AP 3.3 V)
SOS psram: published 0x00000000 bytes
SOS-S3: halt fail code=0x00000051
```

Then the `esp32s3` board's `root_server_boot` runs as README's stage 5: a
stop leaves nothing behind.

### 17g. Word atomics on the PSRAM: board stage P8

`s32c1i` is the core's one word atomic, and the compiler's word atomics are
inline `s32c1i` (§8): a process's runtime keeps its locks, its heap table
and its start-up flag with them, and a program's own atomics are the same
instruction. On the part the PSRAM is external memory behind the data cache,
where ESP-IDF never uses `s32c1i` (§11), and P4 showed one swap there. P8 is
the stress the board's `[ram.psram]` `atomics = true` rests on: increments
raced by threads that the tick preempts between their load and their
`s32c1i`, on words whose lines the cache evicts and fills around them.

The case is `psram_stress`. Its kernel, `tests/xtensa/psram_stress.saw`, is
the shipping kernel's boot with `memory_init`, and a 1 ms tick unless the
image's boot flags ask for none. Root, `tests/psram-stress`, is given a pool
row of PSRAM at 0x3D04_0000, 0x2_1000 bytes, and the EXTMEM device (the
board's `[devices.EXTMEM]`), whose data cache counters it clears first and
reads last. It cuts the pool's bytes and maps them: the counters in the
first page, and 128 KiB of other lines after it, the walk, four times the
part's 32 KiB data cache and twice the largest it can be set to (§15c):

```
counter           where                          increments
psram line 0-3    0x3d040000 + 0x40 n, a line    compare-and-swap
                  of its own each
psram shared 0-3  0x3d040100 + 4 n, one line     compare-and-swap
sram 0-1          the process's SRAM, 32 bytes   compare-and-swap
                  apart
plain psram       0x3d040200, a line of its own  load, spin, store
cold psram        0x3d040300, a line of its own  16 single swaps
```

The compare-and-swap (`sos_stress_cas`, `stress.c`) loads the word, spins 8
times, writes the loaded word to SCOMPARE1 and `s32c1i`s it plus one, then
`memw`; when the answer is not the loaded word it counts a retry and starts
again from the load, and after 1000 in a row gives the increment up, so an
`s32c1i` that never swaps ends in a short count rather than a hung loop.
The plain increment is the same loop with `s32i` in place of `s32c1i` and
no retry. The spin makes the window between the load and the store most of
a pass's instructions, so many ticks that land in a burst land in one.

1. **The cold swaps**, from root's initial thread alone, 16 times: the cold
   word is loaded, stored back on even rounds so its line is dirty, a walk
   evicts the line (a read walk after a store, a write walk after a load),
   and one `s32c1i` swaps the loaded word for one more on a line the cache
   no longer holds: a dirty line written back first, or a clean one
   dropped, by turns.
2. **The race**: four threads, 8 rounds each of a burst of 512 passes (one
   compare-and-swap increment of each of the ten atomic counters and one
   plain increment, with no call into the kernel), a walk (a write walk and
   a read walk by turns), and a barrier in SRAM that waits by yielding until
   all four have ended the round, so the bursts run beside each other. A
   tick inside a window lets the other threads move the word, and a walk of
   theirs may evict its line, before the `s32c1i`: it then misses and
   retries, and the plain store loses their increments. Each counter ends
   at 4 x 8 x 512 = 16384.

The verdict passes only if every atomic counter holds 16384, all 16 cold
swaps answered the loaded word, the cold word is 16, and the run raced: a
compare-and-swap retried or the plain counter lost counts. A run with
neither means no tick reached a window, so it raced nothing and fails for
it. Which windows the ticks land in follows the code's layout, so on the
instruction-counted clock the plain counter alone may lose nothing.
Before the race the verdict is held to a tally with each of those four
faults and to a clean one, and says so.

On the emulator the case runs on the instruction-counted clock (`icount`),
where the ticks land at the same instructions on every run of one build, so
the retries and the plain counter's losses do not depend on the host; the
counts are exact on any clock. `psram_stress_no_tick` boots the same image
with no tick: each thread's burst runs alone, nothing retries, the control
loses nothing, and the run fails with `no tick between a load and its
store`. EXTMEM's counters read 0
here.

On the part, at `pll80` and at `pll240`
(`python3 tools/sos_runner.py --board esp32s3 --emit-flash psram_stress
--clock KEY --out PATH`):

```
SOS stress: kernel up, tick 1000 us
SOS-S3: psram 8 MiB at 0x3d000000, MR0=0x28 MR1=0x0d MR2=<v> MR3=<v>,
read-back ok
SOS: root image ok segments=0x00000003 entry=<v> prio=0x01010100
SOS: boot regions=0x00000002
SOS: console handover
SOS stress: 4 threads x 4096 increments of 11 counters, spin 8
SOS stress: 128 KiB walked between bursts
SOS stress: the verdict refused 4 of 4 faults; clean tally: pass
SOS stress: cold s32c1i on an evicted line: 16 of 16, the word 16
SOS stress: psram line 0 at 0x3d040000: 16384 of 16384
... (each atomic counter: 16384 of 16384)
SOS stress: plain psram at 0x3d040200: <v> of 16384, <v> lost
SOS stress: thread 0 retried psram=<v> sram=<v>
... (each thread)
SOS stress: retries psram=<v> sram=<v>
SOS stress: data cache: <v> accesses, <v> PSRAM misses
SOS stress: every atomic count exact, <v> plain lost: pass
SOS-S3: halt pass
```

(each wrapped line is one line; `pll80` and `pll240` print their clock lines
before the first). The PSRAM misses are at least the cold walks' own lines,
16 x 8192 = 131072, since each of those walks spans four times the cache;
the four threads' walks add more. Any other ending is a fail, and the
range states no atomics: a count other than 16384, a cold swap that missed
or a `gave up an increment` line (an `s32c1i` that answered a word other
than the one in memory), a kernel fault whose EPC1 is the `s32c1i` (one the
part refuses), or a `fail` line for no loss (a run that raced nothing,
which says nothing either way).

On the part (rev v0.2) both keys passed, in one capture session:

| | `pll80` | `pll240` |
|---|---|---|
| clock | 20.00 -> 80.00 MHz | 20.00 -> 240.02 MHz |
| cold `s32c1i` on an evicted line | 16 of 16, the word 16 | 16 of 16, the word 16 |
| every atomic counter | 16384 of 16384 | 16384 of 16384 |
| plain PSRAM control | 15088, 1296 lost | 14849, 1535 lost |
| retries, PSRAM / SRAM | 148 / 14 | 32 / 1 |
| data cache accesses / PSRAM misses | 823768 / 196774 | 823494 / 196799 |
---

## 18. The flash's reads

The module's flash sits on chip select 0 of the memory-SPI controller the
PSRAM's command layer drives (§16). Reading what the flash says of itself,
its JEDEC ID, its status registers and its SFDP tables, means sending it
commands on SPI1 with chip select 0 enabled, which no image did before. This
section is how that is done, and that nothing else is: the read, its checks
and its stores (§18a); what holds it on the emulator and in every image
(§18b); and the probe that reads the part, board stage F0 (§18c). Then the
switch of the cache's reads from the ROM's slow read to DIO or QIO at 80 or
40 MHz, which those reads guard: the flash key that chooses it (§18d), the
switch's checks, stores and canary (§18e), what holds it on the emulator
(§18f), and its board stages, F1 to F3 (§18g).

- `kernel/mspi.c`'s `sos_flash_read` sends one read, from IRAM, under the
  rules of §16d, and returns at the first check that fails with a record of
  what it read. It shares the PSRAM's command primitive (S7.1 to S7.10),
  whose chip select is a parameter it compares with SPI_MEM_MISC(1) and
  never writes.
- `kernel/lib.saw`'s flash section passes the record and prints the line a
  stop's record says (`flash_stop_line`).
- `tests/xtensa/s3_flash_probe.saw` is the probe, F0; `flash_fault.saw` is
  the fault build's harness (§18b).
- `kernel/mspi.c`'s `sos_flash_switch` makes the switch, from IRAM, every
  check before its first store; `keys/flash-*/` are the flash key's values
  and `keys/flash-common/` (`s3flashkit`) their description, call and
  lines; `tests/xtensa/flash_switch.saw` is its fault build's harness
  (§18f).

### 18a. The read, its checks and its stores

**The commands.** `sos_flash_read(record, op, addr)` sends `op` if it is one
of five reads and refuses any other opcode before it reads a register:

```
op   what it returns                    IDF's name and source
9Fh  JEDEC ID, 3 bytes                  CMD_RDID, bootloader_read_flash_id
05h  status register 1                  CMD_RDSR
35h  status register 2 (QE is bit 1)    CMD_RDSR2; bootloader_flash_qe_
                                        support_list's default entry
15h  status register 3                  flash_qio_mode.c, bootloader_read_
                                        status_8b_rdsr3
5Ah  SFDP: a 24-bit address, 8 dummy    CMD_RDSFDP, bootloader_flash_read_
     cycles, 4 bytes                    sfdp
```

`9Fh` is every flash's, and ESP-IDF sends it to whatever part it meets.
The other four are not: IDF marks `CMD_RDSR2` "Not all SPI flash uses this
command" (`spi_flash_defs.h`), and its QE table reads MXIC and ISSI parts
with `05h` alone (`flash_qio_mode.c`). So the one caller, the probe, sends
`9Fh` first and the others only to a flash whose ID is `0x00204017` and
whose eFuse FLASH_TYPE is 0, the part's (§18c); and the claim that follows
is a claim about that part. On it, none of the five changes the flash, sets
its write-enable latch or powers it down. Nothing in this tree sends `06h`
(write enable), so the flash's latch stays clear and a write opcode sent by
mistake would be ignored by the chip; `50h` (write enable for the volatile
status register), `B9h` and `79h` (deep and ultra-deep power-down) are
never sent either. The answer's first byte is in [7:0] of the record's
`answer`, the bits past the answer's length cleared, as IDF's
`bootloader_flash_execute_command_common` returns it.

**The checks**, in order, every one before SPI1 is written:

```
code  check                                      line (after "SOS-S3: flash
                                                 stop: ")
0x79  the record lies in DRAM, by its address    the record at 0x........ is
                                                 not in DRAM
0x7A  the opcode is one of the five              0x.. is not one of the
                                                 flash's reads (...); nothing
                                                 was sent
0x73  SPI_MEM_MISC(1) [1:0] reads 0b10: chip     SPI1 MISC=0x........ before
      select 1 disabled, chip select 0, the      command 0x.., the flash's
      flash's, enabled                           commands need chip select 0
                                                 alone (0x00000002); nothing
                                                 was sent
0x7B  SPI_MEM_DDR(1) reads 0: single data rate   SPI1 DDR=0x........ before
                                                 command 0x.., the flash's
                                                 reads need single data rate
                                                 (0); nothing was sent
0x74  SPI_MEM_CMD(1) reads 0 within 100000       SPI1 busy before command
      polls                                      0x.., SPI_MEM_CMD=0x........
                                                 after 100000 polls; nothing
                                                 was sent
```

MISC(1) at `0b10` is the word the ROM's boot leaves on the part (§15c) and
the one the PSRAM's S10 puts back (§16c); a read compares it and never
writes it, so a read can only reach the flash when nothing has turned the
controller towards the PSRAM. DDR(1) at 0 is the ROM's word too: with
DDR_EN set the command would go out on both clock edges, and a flash
sampling one edge would see another opcode.

**The stores**, IDF's `bootloader_flash_execute_command_common` with the
S3's `spimem_flash_ll` helpers, through the primitive's S7.2 to S7.9:

```
step   register (SPI1)       value
save   CLOCK, CTRL, USER, USER1, USER2, ADDR, MOSI_DLEN, MISO_DLEN,
       CACHE_FCTRL, DDR, MISC read (§16c's S5 list)
S7.2   CTRL       0x00200000: WP [21] alone (`ctrl.val = 0`, then
                  `spimem_flash_ll_set_wp_level(true)`): command, address
                  and data on one line, no fast-read bit, no WRSR_2B
S7.3   USER       (USER & 0x00FF0FFF) | USR_COMMAND [31] | USR_MISO [28],
                  and for 5Ah USR_ADDR [30] and USR_DUMMY [29]
S7.4   USER1      5Ah: 0x5c000007, a 24-bit address and 8 dummy cycles;
                  the others 0
S7.5   USER2      0x70000000 | op: an 8-bit command
S7.6   ADDR       5Ah only: the address, in [23:0] (`spimem_flash_ll_set_
                  usr_address`, which on the S3 takes it unshifted)
       CACHE_     CACHE_USR_CMD_4BYTE [1] cleared, as IDF's
       FCTRL      `spimem_flash_ll_set_addr_bitlen` clears it, and the SPI1
                  line modes [8:3] cleared too: that second clear is this
                  code's own, stricter than IDF, whose helper sets only
                  CACHE_USR_CMD_4BYTE, USER1's address length and USER's
                  USR_ADDR; with any of the six set, a read meant for one
                  line would go out on two or four
S7.7   MISO_DLEN  the answer's bits less one: 23, 7 or 31
S7.8   MISC read again; it must still read 0b10 (0x73, SPI1 put back)
S7.9   CMD        SPI_MEM_USR [18] alone; then CMD read until 0
S7.10  W0 read: the answer
put    every saved word but MISC written back, in the save's order
back
```

The only word ever written to SPI_MEM_CMD(1) is SPI_MEM_USR. The bits beside
it start the controller's built-in flash commands (§16c), write enable,
write status, the erases, page program and deep power-down among them; none
is written. SPI1's clock is not written: a read runs at the divider SPI1
holds, the ROM's divide by 4 (§16b), 5 MHz at `rom` and 20 MHz on the PLL
keys. The ROM's extra dummy for SPI1 (§15c, 0 on the part), which IDF adds
to every read's dummy, is not added.

After the save, three ways out besides the answer:

```
0x74  S7.1   SPI1 busy before command 0x.., SPI_MEM_CMD=0x........ after
             100000 polls; nothing was sent, SPI1 put back
0x73  S7.8   SPI1 MISC=0x........ before command 0x.., ...; nothing was
             sent, SPI1 put back
0x75  S7.9   SOS-S3: flash: SPI1 command 0x.. did not finish   (from IRAM)
             SOS-S3: flash stop: SPI1 command 0x.. did not finish,
             SPI_MEM_CMD=0x........ after 100000 polls; SPI1 left as it is
```

A read that does not finish may still be on the bus with chip select 0 low:
the call writes its own line from IRAM and leaves SPI1 alone, and the caller
halts. A reset clears it.

**The primitive.** The PSRAM's S7 is now one primitive (`link_start`,
`link_finish`) given the chip select, CTRL(1)'s and CACHE_FCTRL(1)'s keep
and set masks, and the command's, address's, dummy's and data's lengths.
The PSRAM's commands pass the words S7 always used (§16c), so its stores are
unchanged and §16e's cases hold them as before. The primitive compares
MISC(1) with the command's chip select before every start and writes MISC(1)
never; the PSRAM's gate (S6) is the one store to it in this file.

### 18b. The emulator, and what holds the reads

**The emulator** (espressif/qemu `esp-develop-9.2.2-20250817`, by its
source and these runs):

- The flash is QEMU's `m25p80` model on SPI1's chip select 0, chosen by the
  image's size: a 4 MiB smoke image gets `gd25q32`, a GigaDevice part, JEDEC
  `0xc84016`. It answers `9Fh` with that ID and `05h` with its status, 0 at
  boot, and `15h` with its configuration byte, 0. It does not answer `35h`
  for a GigaDevice part: the command leaves it idle, so the byte clocked in
  for the answer is decoded as a command of its own, `00h`, and status
  register 2 reads 0. `5Ah` is an unknown command to it and reads 0, so the
  probe's SFDP signature reads 0 and it reads no further.
- SPI1 runs a user command inside the store to SPI_MEM_CMD and reads
  SPI_MEM_CMD 0 at once (§16e). It sends the address phase from ADDR's low
  bits, as the S3's IDF writes it for a 24-bit address. It routes a
  command to each chip select MISC(1) leaves enabled.
- MISC(1) reads 0 at boot, where the part reads `0x00000002` (§15c): the
  emulator does not take the word from its ROM's boot. A read compares it
  with `0b10` and would stop at once, so a flash harness's emulator image
  first stores the case's word (`sos_flash_case_setup`, which
  `_flash_case_asm` in `tools/sos_runner.py` writes: `0x2` unless the case
  says otherwise). In a board image the setup stores nothing, whatever the
  case says, and the part's ROM has left `0b10`.
- After its boot SPI1 holds CLOCK `0x00030103`, CTRL `0x00208000`, USER
  `0x800000c0`, USER1 `0x5c000007`, USER2 `0x70000000`, and 0 in ADDR,
  MOSI_DLEN, MISO_DLEN, CACHE_FCTRL and DDR: the words a read writes back.
- SPI0, the IO MUX and the GPIO matrix are not modelled and read 0; the
  caches' autoload words read `0x00000008` (DONE); the eFuse reads 0 with
  no file.

**What holds it**, in every image or every run of the smoke:

- *The flash's own view.* Every run of the S3 smoke traces the commands the
  flash model decodes (`-trace m25p80_command_decoded`), and a run in which
  it decodes anything but `ABh`, the ROM's at boot, the five reads and the
  `00h` the model makes of the byte after `35h` fails, whatever its case
  asserts (`FLASH_COMMANDS_HEARD`). A case's `flash_commands` is the whole
  list in order. With the eFuse's FLASH_TYPE set the emulator's ROM takes
  the flash for an octal one and, before the kernel runs, sends it eight
  `00h`, octal resets (`66h`, `99h`), `06h` twice and `FFh` in place of its
  `ABh`, and leaves SPI1's CTRL at `0x0020a000`; the one case that boots so
  names those commands (`flash_rom_commands`), the trace must begin with
  them, and the rest is held as any run's.
- *SPI1's stores.* A flash case's `expect_spi1_stores` is every store the
  kernel makes to SPI1's page, from `_start`'s first store on, in order
  (`check_spi1_stores`): the setup's MISC(1) word, then each read's stores
  as §18a gives them, its one start with SPI_MEM_USR alone, and the words it
  writes back. A store to MISC(1) from the read, a built-in command bit in
  SPI_MEM_CMD, a word not put back or a CTRL(1) other than WP alone is a
  difference. The emulator does not decode the built-in bits beside
  SPI_MEM_USR, so this list, not the flash's trace, is what holds them.
- *Nothing in flash.* `check_iram` (§16d) covers the read as it covers the
  bring-up: its functions are `mspi.c`'s, all in `.text.iram`, and the
  mutants' link is rooted at `sos_flash_read` too; `iram_mutant_script_flash`
  requires the check to name it when the script's IRAM line is removed.
- *No ROM routine that writes the flash.* `check_rom_flash_references`
  refuses any S3 image whose kernel sections (`.entry`, `.iram`, `.text`,
  `.data`, `.drom`) hold as an aligned word the jump-table entry or the
  body's address of a ROM routine that sends the flash anything but a read
  (`ROM_FLASH_WRITERS`): the status, array and write-enable writers and the
  write-enable clear, the protection setters (`SPILock`, which sets the
  block-protect bits), the wake and the resume, the quad, octal, reset and
  read-mode setters (`esp_rom_spiflash_config_readmode` calls
  `Enable_QMode` or `Disable_QMode`, which write QE), the routines that send
  a command they are given, and the attaches: every one of
  `esp32s3.rom.ld`'s flash groups (opi_flash, spiflash_legacy, hal_soc,
  spi_flash_chips, memspi_host, esp_flash) that does, with its body from
  the ROM's ELF. The check looks for addresses, so a routine is listed
  even when the routines it calls are. This core has no 32-bit immediate,
  so a call of one, through the windowed-call stub or otherwise, loads its
  address from such a word. A refused image is deleted, as for the other
  checks. `rom_flash_reference.saw` holds one routine's entry as a literal
  (`.text`), in a static it writes (`.data`), in a table (`.drom`) and, in
  `rom_flash_reference.S`, after an IRAM function's return (`.iram`), and
  its body's address as a literal: a case for each requires the check to
  name it there. `.entry` has no case: it is `boot.S`'s jump to `_start`
  alone, and no test kernel can put a word in it without changing the boot
  path every image shares.
- *The fault build.* Every register read of the read goes through `rd`
  (§16e), and `flash_fault.saw` is a fault harness beside `psram_fault.saw`:
  the only two kernels that may hold `sos_psram_fault`, and only in an
  emulator image (`check_psram_fault`). A read's steps for the fault record
  are `0xF1` (MISC), `0xF2` (DDR), `0xF3` (the wait before the save), `0xF4`
  (the save), then the primitive's S7 steps.

**The cases**, each with its console, its flash trace and its SPI1 stores:

```
case                          how                                   stop
s3_flash_probe                F0 on the emulator (below)             -
flash_probe_misc_reset        the probe, MISC(1) at the emulator's 0 0x73
flash_probe_misc_psram        MISC(1) at 0b01, the PSRAM's gate      0x73
flash_probe_misc_neither      MISC(1) at 0b11                        0x73
flash_read_rdid               9Fh: answer 0x001640c8, 1 start, 1     -
                              answer read
flash_read_rdsr2              35h: the flash decodes 35h, 00h        -
flash_read_sfdp               5Ah at 0x30: the address phase         -
flash_stop_not_read_NN        06h, 01h, 31h, 50h, B9h, 79h, 02h,     0x7A
                              C7h, 03h: refused, nothing stored
flash_read_sweep              every other byte, 251 calls in one     0x7A
                              boot: each refused, nothing stored,    each
                              the flash hears `ABh` alone
flash_read_user_kept          S7's load of USER read with [31:24]    -
                              and [15:12] set: the stores as ever
flash_read_fctrl_cleared      S7's load of CACHE_FCTRL read with     -
                              [8:3] and [1] set: the stores as ever
flash_probe_other_id          the probe told to read past the        0x7C
                              part's ID on the emulator's flash:
                              9Fh alone sent
flash_probe_flash_type_8      the probe with eFuse FLASH_TYPE set:   0x7C
                              9Fh alone sent
flash_stop_record_in_flash    the record at 0x3c200000               0x79
flash_stop_record_past_dram   the record at 0x3fcffff0               0x79
flash_stop_ddr                0xF2's read: DDR_EN set                0x7B
flash_stop_busy_before        0xF3's reads: busy; nothing stored     0x74
flash_stop_busy_command       S7.1's reads: busy; SPI1 put back      0x74
flash_stop_gate_lost          S7.8's read: 0b01; set up, not         0x73
                              started, put back
flash_stop_not_finished       S7.9's reads: busy; the call's line,   0x75
                              SPI1 left as the read set it
flash_fault_board_image       the harness as a board image           build
rom_flash_reference_entry     a kernel holding config_readmode's     build
                              jump-table entry in `.text`
rom_flash_reference_body      ... its body's address in `.text`      build
rom_flash_reference_data      ... the entry in `.data`               build
rom_flash_reference_drom      ... the entry in `.drom`               build
rom_flash_reference_iram      ... the entry in `.iram`               build
iram_mutant_script_flash      the read linked in flash               finding
```

`s3_flash_probe` on the emulator prints:

```
SOS flashprobe: efuse repeat_data3=0x00000000 flash_type=0
SOS flashprobe: id jedec=0x00c84016 sr1=0x00 sr2=0x00 sr3=0x00
SOS flashprobe: sfdp signature=0x00000000 sfdp=none
SOS flashprobe: decoded sfdp=none qe=clear
SOS flashprobe: spi0 clock=0x00000000 ... core_clk_sel=0x00000000
SOS flashprobe: pads 27=0x00000000 ... insel 2=0x00000000 3=0x00000000
SOS flashprobe: cache icache_autoload=0x00000008 dcache_autoload=0x00000008
SOS flashprobe: SPI1 MISC=0x00000002 clock=0x00030103 after the reads, as
                before them
SOS-S3: halt pass
```

and the flash decodes `ab 9f 05 35 00 15 5a`.

**What the emulator cannot show**, and only a part can: the reads on the
wire at the part's clock, the part's answers (its ID, its status registers,
its SFDP tables), and SPI0's, the pads' and the input selects' words.

### 18c. The flash probe, board stage F0

`tests/xtensa/s3_flash_probe.saw`, the smoke's `s3_flash_probe`, reads what
the module's flash says of itself and what the ROM leaves in SPI0 for it,
and prints both. Every command it sends the flash is one of §18a's reads,
each one call of `hal.flash_read`; every other load goes through one
accessor whose view is a `ReadOnly` field, as `s3_mem_probe`'s does (§15),
so the file stores to no register of its own. It runs the image's clock
key first, as `kmain` does; F0 is built at `rom`, where the reads run at
5 MHz (§16b).

```
python3 tools/sos_runner.py --board esp32s3 --emit-flash s3_flash_probe \
    --out PATH
```

builds the board image: the case block's setup stores nothing, its ID is
the part's, and the image is held to every check of §16d and §18b.

**The ID gate.** The probe sends `9Fh` and then nothing more unless the ID
is the one its case block names and the eFuse's FLASH_TYPE is 0: a board
image's block names the part's, `0x00204017` (`FLASH_PART_JEDEC`), and an
emulator image's its flash model's, `0x00c84016`, unless the case names
another. Otherwise it prints

```
SOS flashprobe: id jedec=0x........ stop: the probe reads further only a
                flash of ID 0x00204017 and flash_type 0
```

then the SPI1 line, and halts with 0x7C, the probe's own code. `05h`,
`35h`, `15h` and `5Ah` are not every flash's commands (§18a), so no other
part is sent them.

**The reads**, in order: `9Fh`; past the gate, `05h`, `35h`, `15h`; then
SFDP, 4 bytes a read, at 0 (the header's signature, "SFDP" as the
little-endian word `0x50444653`), 4 (its minor and major revision and the
0-based count of parameter headers), 8 and 12 (the first parameter header:
the ID's low byte, its revision, the table's length in DWORDs; the table's
24-bit pointer and the ID's high byte), then the basic table's DWORDs 1, 3,
4, 15 and 16 at the pointer plus 4 x (n - 1): nine SFDP reads in all
(JESD216's layouts as Linux's `drivers/mtd/spi-nor/sfdp.c` and `sfdp.h`
quote them). A signature other than `0x50444653` ends the SFDP reads; a
first parameter header whose ID is not `0xFF00`, the basic table's, ends
them too; a table shorter than 15 or 16 DWORDs has no DWORD 15 or 16, and
the line prints `none` for it.

**The lines** (each wrapped line is one line):

```
SOS flashprobe: efuse repeat_data3= flash_type=
SOS flashprobe: id jedec= sr1= sr2= sr3=
SOS flashprobe: sfdp signature= rev= nph= bfpt rev= len= ptr=
SOS flashprobe: bfpt dw1= dw3= dw4= dw15= dw16=
SOS flashprobe: decoded dio= op= wait= qio= op= wait= qer= qe=
SOS flashprobe: spi0 clock= ctrl= ctrl1= ctrl2= user= user1= user2= fctrl=
                ddr= cali= din_mode= din_num= date= core_clk_sel=
SOS flashprobe: pads 27= 28= 29= 30= 31= 32= insel 2= 3=
SOS flashprobe: cache icache_autoload= dcache_autoload=
SOS flashprobe: SPI1 MISC= clock= after the reads, as before them
SOS-S3: halt pass
```

- `repeat_data3`: EFUSE_RD_REPEAT_DATA3_REG; `flash_type` its bit 9,
  EFUSE_FLASH_TYPE, 0 for a four-line flash.
- `jedec`: the ID as IDF's `bootloader_read_flash_id` forms it, manufacturer
  in [23:16]. esptool read 0x20 0x4017 on the part, an XMC part ESP-IDF's
  `is_xmc_chip_strict` takes.
- `sr1`, `sr2`, `sr3`: the status registers. `sr2` bit 1 is QE, where
  ESP-IDF looks for this ID; `sr1` bit 7 (SRP0) and `sr2` bit 0 (SRP1) would
  lock the status register against a write.
- `rev`, `nph`, the basic table's `rev`, `len` and `ptr`: the header's and
  the first parameter header's fields.
- `dw1`, `dw3`, `dw4`, `dw15`, `dw16`: the basic table's DWORDs, whole.
- `decoded`: `dio` and `qio` are DWORD 1's bits 20 (1-2-2 fast read) and 21
  (1-4-4); each read's `op` and `wait`, the mode clocks plus the wait
  states, are DWORD 4's [31:16] for 1-2-2 and DWORD 3's [15:0] for 1-4-4
  (opcode [15:8], mode clocks [7:5], wait states [4:0] of the half); `qer`
  is DWORD 15's [22:20], how QE is set; `qe` is `sr2` bit 1. With no basic
  table the line is `decoded sfdp=none qe=...`.
- `spi0`: SPI_MEM_<NAME>_REG(0) for CLOCK, CTRL, CTRL1, CTRL2, USER, USER1,
  USER2, CACHE_FCTRL, DDR, TIMING_CALI, DIN_MODE, DIN_NUM, DATE and
  CORE_CLK_SEL: the ROM's words for its slow read, including those §15c did
  not read.
- `pads`: IO_MUX_GPIO27 to 32, the flash's HD, WP, CS0, CLK, Q and D;
  `insel 2`, `3`: GPIO_FUNC2_IN_SEL_CFG and FUNC3, SPIHD's and SPIWP's
  input selects.
- `cache`: EXTMEM_ICACHE_AUTOLOAD_CTRL_REG and
  EXTMEM_DCACHE_AUTOLOAD_CTRL_REG; AUTOLOAD_ENA is bit 2.
- The SPI1 line: MISC(1) and CLOCK(1) after the reads, and whether every
  word a read saves reads as it did before the first.

A stop prints its line (§18a), then the SPI1 line, and halts with its code.

**On the part** (ESP32-S3 rev v0.2, the module of §15c), at `rom`, F0
printed (each wrapped line is one line):

```
SOS flashprobe: efuse repeat_data3=0x00000100 flash_type=0
SOS flashprobe: id jedec=0x00204017 sr1=0x00 sr2=0x02 sr3=0x20
SOS flashprobe: sfdp signature=0x50444653 rev=1.6 nph=2 bfpt rev=1.6
                len=16 ptr=0x000030
SOS flashprobe: bfpt dw1=0xfff920e5 dw3=0x6b08eb44 dw4=0xbb423b08
                dw15=0xff4df619 dw16=0x80c010e9
SOS flashprobe: decoded dio=yes op=0xbb wait=4 qio=yes op=0xeb wait=6
                qer=0b100 qe=set
SOS flashprobe: spi0 clock=0x00030103 ctrl=0x00200000 ctrl1=0x00000000
                ctrl2=0x00002c41 user=0x000000c0 user1=0x5c000007
                user2=0x70000003 fctrl=0x00000005 ddr=0x00000000
                cali=0x00000000 din_mode=0x00000000 din_num=0x00000000
                date=0x02101040 core_clk_sel=0x00000000
SOS flashprobe: pads 27=0x00001b00 28=0x00001b00 29=0x00000b00
                30=0x00000b00 31=0x00000b00 32=0x00000b00 insel
                2=0x0000003c 3=0x0000003c
SOS flashprobe: cache icache_autoload=0x00000008 dcache_autoload=0x00000008
SOS flashprobe: SPI1 MISC=0x00000002 clock=0x00030103 after the reads, as
                before them
SOS-S3: halt pass
```

- The ID is the part's, `0x00204017`, and the flash four-line by the
  eFuse.
- SR1 is 0: no block-protect bit, SRP0 clear. SR2 is `0x02`: QE set, SRP1
  clear. SR3 is `0x20`.
- The basic table: DWORD 1's bits 20 and 21 set (1-2-2 and 1-4-4); DWORD
  4's [31:16] `0xbb42`, `BBh` with 2 mode clocks and 2 wait states; DWORD
  3's [15:0] `0xeb44`, `EBh` with 2 and 4; DWORD 15's QER 100b, QE bit 1 of
  status register 2, written with two bytes.
- The SPI0 words are §15c's where it read them; CTRL2 reads `0x00002c41`,
  CTRL1, TIMING_CALI, DIN_MODE and DIN_NUM 0. Signals 2's and 3's input
  selects read `0x3c`, bit 7 clear.

That run was of an image built before the ID gate, whose passing text is
the same. A JEDEC ID other than `0x00204017`, `flash_type` 1, SRP0 or SRP1
set, a QER outside 001b, 100b and 101b, SPI0 words other than §15c's, or a
SPI1 line that is not `as before them` is a finding to understand before
anything changes the flash's mode; the first two the probe stops on
itself.

### 18d. The flash key

The cache reads the flash as the mask ROM leaves it: `03h`, slow read, on
one line, at divide by 4 of the memory-SPI core clock (§14d, §15c), 20 MHz
on the PLL. The flash key (`s3flash`, `keys/flash-*/`) chooses how the
cache reads it instead, for the image it is built into:

```
key    the cache's read                       clock          needs
rom    03h on one line, the ROM's             core / 4       -
dio80  BBh, address and data on two lines,    core / 1:      a clock key on
       2 mode and 2 wait clocks               80 MHz         the PLL
qio80  EBh, address and data on four lines,   core / 1:      a clock key on
       2 mode and 4 wait clocks               80 MHz         the PLL; QE set
dio40  dio80's                                core / 2:      as dio80
                                              40 MHz
qio40  qio80's                                core / 2:      as qio80
                                              40 MHz
```

`rom` is the default everywhere and writes nothing: an image at it compiles
none of the switch, and its kernel's code is the bytes it is without the
key. The two 40 MHz keys make the 80 MHz keys' stores at half the clock:
they are built beside them to tell a failure of timing at 80 MHz from a
failure of the sequence (§18g). The read modes, their opcodes and their
wait clocks are ESP-IDF's for this part and what its SFDP table claims
(§18c): `BBh` with 4 clocks of mode and wait, `EBh` with 6.

**The seam.** The HAL's `clock_init`, which `kmain` calls first, is the
clock key's `clock_init`, then the flash key's `flash_init`
(`kernel/lib.saw`). The switch is made for the memory-SPI core clock the
clock key leaves, before anything else reads the flash for long, and before
`memory_init`, after which nothing moves that clock (§17a). It writes none
of SYSTEM_SYSCLK_CONF, SYSTEM_CPU_PER_CONF and SPI_MEM_CORE_CLK_SEL, so the
PSRAM's contract holds as it is: the PSRAM's divider was chosen for a core
clock the flash key does not move (§16b, §17b). It moves SPI0's flash
divider alone; SPI1's, which the flash's reads and the PSRAM's commands run
at, and SPI0's PSRAM divider stay as they were:

```
clock                 register         rom flash key   any other
core                  CORE_CLK_SEL 0   80 MHz          80 MHz
the cache's flash     CLOCK(0)         / 4: 20 MHz     / 1: 80, / 2: 40
SPI1's commands       CLOCK(1)         / 4: 20 MHz     / 4: 20 MHz
the cache's PSRAM     SRAM_CLK(0)      the PSRAM's     the PSRAM's
```

(at a clock key on the PLL). `check_clock_registers` holds the flash keys
to it as it holds every file but the clock keys' (§17a): the flash kit is
not `clock_init`'s file, and `mspi.c` reads the three words with `rd`
alone.

**On the PLL only.** At divide by 1 the flash runs at the memory-SPI core
clock itself: 80 MHz on the PLL at 480 MHz with CORE_CLK_SEL 0, the speed
ESP-IDF runs this part's flash at with no timing tuning, since it tunes a
flash in STR mode only above 80 MHz (`MSPI_TIMING_FLASH_NEEDS_TUNING`,
esp_hw_support/port/esp32s3/mspi_timing_tuning_configs.h). On the crystal
the core clock is not the PLL's (§16b). Each clock key states the core
clock it leaves, `MSPI_CORE_MHZ` (§16b), and each flash key but `rom`
asserts it is 80, so with `rom` or `xtal40` the kernel's compile stops at
the key's assertion:

```
static assertion failed: dio80 needs a clock key on the PLL: pll80, pll160
or pll240
```

(one line). At boot the switch holds the clock words to its description
(0x70, §18e), so a clock key that was not taken leaves the flash as the ROM
left it.

**Choosing it.** board.toml's `[keys.flash]` lists the values. A product's
image chooses one in its `[keys]`, beside a clock key on the PLL, and
`sosimage` maps `s3flash` to the value's directory as it maps `s3clock`
(§14d):

```toml
[keys]
clock = "pll240"
flash = "dio80"
```

The smoke's case key is `"flash_key"`, since a flash harness's `"flash"` is
its block (§18b), and `--emit-flash CASE --flash KEY` overrides it for a
board image, as `--clock` overrides the clock key. A description that
states `[keys]` for a case that names a flash key is refused, as for the
others (§14d). The keys that switch share `s3flashkit`
(`keys/flash-common/`): the description, the call and the lines, written
with `s3clockkit`'s console writer, which every clock key on the PLL
compiles in.

### 18e. The switch

`kernel/mspi.c`'s `sos_flash_switch(expect, record, mode, regions)` makes
every check and every store in one call from IRAM, under §16d's rules, and
returns at the first check that fails with a record of what it read.
`mode` is the key's: bit 0 the four lines, bit 1 half the clock. `regions`
is the canary and the timed regions (below). `s3flashkit`'s `flash_switch`
passes the description and prints the line; its `flash_init_at` is each
key's `flash_init`.

**The description** is eight words (`struct sos_flash_expect`,
`FlashExpect`): the three clock words, masked as the PSRAM's S0.3 masks
them; the flash's JEDEC ID; and SPI0's CLOCK, CTRL, USER1 and USER2 as the
ROM leaves them. A board image carries the part's: its clock key's words
(`s3clock`'s `PART_SYSCLK`, `PART_CPU_PER`, `PART_CORE_CLK_SEL`, §17b) and
F0's (§18c). An image for the emulator carries the emulator's. The linker
constant that tells the two apart, `_sos_semihosting` (§10), chooses
(`board_image`), so the image's destination decides: a product's `flash`
links the part's and its `run` and `test` the emulator's, as the smoke's
`--emit-flash` and its runs do.

```
word              the part (F0)               the emulator
SYSCLK & 0x80FFF  the clock key's (§17b)      0x00080401
CPU_PER & 7       the clock key's             0
CORE_CLK_SEL & 3  0                           0
JEDEC ID          0x00204017                  0x00c84016
SPI0 CLOCK        0x00030103                  0
SPI0 CTRL         0x00200000                  0
SPI0 USER1        0x5c000007                  0
SPI0 USER2        0x70000003                  0
```

**The steps**, in order:

```
step  what                                                 stop
W1    the description and the record lie in DRAM, by       0x79
      their addresses
W2    the three clock words as described                   0x70
      SPI0's CLOCK, CTRL, USER1 and USER2 as described,    0x71
      and DDR, TIMING_CALI, DIN_MODE and DIN_NUM 0:
      single data rate, no input delay; CTRL2, USER, pads
      27 and 28 and the input selects of signals 2 and 3
      read for W5
      neither cache's autoload on: AUTOLOAD_ENA [2] of     0x72
      EXTMEM_ICACHE_AUTOLOAD_CTRL_REG (0x600C_40A0) and
      EXTMEM_DCACHE_AUTOLOAD_CTRL_REG (0x600C_404C) clear
W3    9Fh, the JEDEC ID, as described: a flash read        0x76
      (§18a), with its own stops, 0x73, 0x74, 0x75, 0x7B
      35h, status register 2, read the same way; for four  0x77
      lines its QE (bit 1) must be set
W4    the first timed read
W5    both caches frozen, the instruction cache's first    0x7D
      (below); the stores, below; both caches unfrozen     0x7E
W6    the canary, read through the data cache              0x78
W7    when the canary differs: both caches frozen, every    0x7D
      word W5 stored put back to what W2 read, the clock   0x7E
      first, both unfrozen, then the line from IRAM
W8    the second timed read
W9    the words read again, for the line
```

No stop before W5's stores stores anything to SPI0, the pads or the input
selects.
W3's reads are §18a's, the ID first and status register 2 only to the ID
described, which on a board image is the part's (§18a says why). The flash
is sent those two reads and nothing else: no write enable, no status
write, no power-down. A QIO key finding QE clear stops at 0x77, and the
boot goes on at the ROM's reads: no image writes the flash's status
register.

**The stores** (W5), each word made from what W2 read, in order:

```
#     register             value                                  keys
W5.1  SPI0 CTRL (0x08)     | FDUMMY_OUT [3], Q_POL [18], D_POL    all
                           [19]
W5.2  SPI0 CTRL2 (0x10)    CS_HOLD_TIME [9:5] and CS_SETUP_TIME   all
                           [4:0] 0, stored only if either is not
W5.3  IO_MUX_GPIO27, 28    MCU_SEL [14:12] 0: SPIHD, SPIWP        QIO
      (0x6000_9070, _9074)
W5.4  GPIO_FUNC2_IN_SEL_   SIG_IN_SEL [7] cleared: straight from  QIO
      CFG, FUNC3 (0x6000_  the IO MUX
      415C, _4160)
W5.5  SPI0 CTRL            W5.1's word & 0xFE6F_9C0F, | 0x0080_   all
                           2000 (DIO: FREAD_DIO [23],
                           FASTRD_MODE [13]) or 0x0100_2000 (QIO:
                           FREAD_QIO [24], FASTRD_MODE)
W5.6  SPI0 USER (0x18)     USR_MOSI [27] cleared; USR_ADDR,       all
                           USR_DUMMY, USR_MISO [30:28] set
W5.7  SPI0 USER1 (0x1C)    [31:26] 23, a 24-bit address; [5:0]    all
                           3 (DIO, 4 clocks) or 5 (QIO, 6)
W5.8  SPI0 USER2 (0x20)    [15:0] 0xBB (DIO) or 0xEB (QIO)        all
W5.9  SPI0 CLOCK (0x14)    0x80000000, CLK_EQU_SYSCLK, divide by  all
                           1; or 0x00010001, divide by 2
```

The sources: W5.1 ESP-IDF's `bootloader_flash_set_dummy_out`; W5.2 its
`bootloader_flash_cs_timing_config` for a flash that is not octal; W5.3
and W5.4 the ROM's `esp_rom_spiflash_select_qio_pins` (0x4004_95C4); W5.5
the ROM's `esp_rom_spiflash_config_readmode` (0x4004_A820), its mask and
its table `CSWTCH$195` (0x3FF1_E060); W5.6 to W5.8 the ROM's
`spi_cache_mode_switch` (0x4004_A190), with the ROM's extra dummy for SPI0
at 0 (§15c); W5.9 ESP-IDF's `mspi_timing_ll_set_flash_clock`, whose divide
by 1 is the ROM's `esp_rom_spiflash_config_clk(1, 0)` word too. No ROM
routine is called for any of them: `config_readmode` writes QE itself
(`Enable_QMode`, `Disable_QMode`), and `check_rom_flash_references` refuses
an image that names it (§18b).

**The order.** No flash read is made between the stores, so the order is
what a trace holds. Each group leaves a configuration ESP-IDF itself runs:
the dummy output and the chip-select timing are its in every mode; the
read mode with the clock still at divide by 4 is its low-speed mode; and
the clock, the one store that makes it fast, is last. W5.1 precedes W5.5 for
a reason of its own: in DIO and QIO the read's mode bits M7 to M0 follow
the address on the data lines, and a flash takes M5..M4 = 10b as
"continuous read", after which it takes the next command's first byte for
an address until a power cycle. With FDUMMY_OUT and both polarities set the
controller drives those lines high, so M5..M4 go out as 11b; without it they
would float on the pads' pull-ups.

From the part's words these are, for DIO: CTRL 0x002C_0008 after W5.1 and
0x00AC_2008 after W5.5, CTRL2 0x0000_2C00, USER 0x7000_00C0, USER1
0x5C00_0003, USER2 0x7000_00BB, CLOCK 0x8000_0000. For QIO: CTRL
0x012C_2008 after W5.5, USER1 0x5C00_0005, USER2 0x7000_00EB, pads 27 and
28 0x0000_0B00, the input selects 0x0000_003C (bit 7 already clear), the
rest as DIO. The part read USER back as 0x0000_00C0 after the ROM had
stored its USR_ADDR and USR_MISO bits too (§15c), so the line may show
SPI0 keeping only some of W5.6's bits; it prints what it reads.

**The freeze.** Both caches are frozen around the stores, as ESP-IDF
freezes them around its own MSPI speed change
(`mspi_timing_change_speed_mode_cache_safe`, esp_hw_support/
mspi_timing_tuning.c: `cache_hal_freeze` and `cache_hal_unfreeze` of
`CACHE_TYPE_ALL`, which hal/esp32s3/include/hal/cache_ll.h makes
`Cache_Freeze_ICache_Enable(CACHE_FREEZE_ACK_BUSY)`, then the DCache's, and
the two `Disable`s in the same order). A frozen cache sends SPI0 no miss of
its own, the CPU's, a preload's or an autoload's, and reports FREEZE_DONE
once nothing of it is left on the bus, so no fill begun before W5, the
last of W4's included, is still on SPI0 when its words change. Without it
the part hung with nothing printed after the clock key's line at every
flash key, and switched cleanly when each step of the switch was followed
by a console line and a wait for UART0 to go idle, time enough for the
last fill to end. The switch writes the two EXTMEM words itself, as the
ROM's routines do (disassembled from the ROM's ELF), and calls none of
them:

```
register                     store                              ROM
EXTMEM_ICACHE_FREEZE_REG     read; FREEZE_MODE [1] cleared       Cache_Freeze_
(0x600C_4154)                (CACHE_FREEZE_ACK_BUSY: a miss is   ICache_Enable
                             answered busy); read; FREEZE_ENA    (0x4004_E8A8)
                             [0] set; read until FREEZE_DONE
                             [2] is set
EXTMEM_DCACHE_FREEZE_REG     the same                            Cache_Freeze_
(0x600C_4150)                                                    DCache_Enable
                                                                 (0x4004_E910)
EXTMEM_ICACHE_FREEZE_REG     read; FREEZE_ENA cleared; read      Cache_Freeze_
                             until FREEZE_DONE is clear          ICache_Disable
                                                                 (0x4004_E8E8)
EXTMEM_DCACHE_FREEZE_REG     the same                            Cache_Freeze_
                                                                 DCache_Disable
                                                                 (0x4004_E950)
```

(the fields from soc/esp32s3/register/soc/extmem_reg.h). Each wait reads
at most 100000 times; the ROM's own waits have no bound, so the way out
of each is this code's. A cache that does not report its freeze may yet
freeze after the bound, so its freeze is undone, as an unfreeze, and so is
the instruction cache's when it was the data cache that did not report;
then the switch stops at 0x7D before any store to SPI0. An unfreeze that
does not report, after the stores or as such an undo, is 0x7E: both
caches' unfreezes are tried, the line is written from IRAM, since the
caller's next fetch from flash may then never be answered, and the key
halts. W7 freezes and unfreezes the same way, at steps of its own: a
freeze that does not report there is undone and SPI0 is put back all the
same, with the caches running when the undo reports (0x7D) and with a
cache that may still be frozen when it does not (0x7E, whose line then
does not say the caches run); an unfreeze that does not report is 0x7E. The
record's `state` keeps what was done to SPI0 (stored, put back) and which
cache's freeze or unfreeze did not report, with the freeze words they last
read, and the canary's first wrong word; the 0x7D and 0x7E lines say all
of it. While the caches are frozen nothing of the call reads flash: a miss
would wait until the unfreeze.

**Why IRAM.** From W5.1 to W5.9 the flash is readable in no one mode, and
the kernel's code is in flash (`esp32s3.ld`): so every instruction and
literal of the call is in IRAM, and every datum it reads in DRAM or, for
the canary and the timed regions, read only after W5.9. Interrupts are
masked in the kernel (§7f), one core runs (§11), and W2 has found both
caches' autoload off, so nothing fetches from the flash between the
stores. `check_iram` holds the call to §16d's rules, and its mutants' link
is rooted at `sos_flash_switch` too.

**The canary and the timed reads.** `boot.S`'s `sos_flash_regions` names a
block in `.drom` that starts on a 64-byte boundary and is a multiple of 64
bytes long, so no line of the data cache holds a byte of it and a byte of
anything else, and nothing but the switch reads it. It is 64 bytes of
canary, then two regions of 4 KiB. The canary's bytes are 0x55 0xAA, 0x00
0xFF, 0x0F 0xF0, 0x33 0xCC, each data line low then high from one byte to
the next, then each byte its own offset; W6 compares them word by word
with the same formula (`canary_byte`, no table: §16d). A wrong mode,
wrong data or a flash paused on HOLD# reads as a mismatch: the cache loads
whatever the lines carry and does not hang on it. W4 and W8 each read 4
KiB at 16-byte strides of a region no load has reached, between two
SYSTIMER snapshots (the clock key started SYSTIMER), the first before W5
and the second after W6. Each region is read once, by its own pass and by
nothing else, so every line of it is a miss when the pass reaches it: a
16-byte stride touches every line at any of the data cache's line sizes,
and the two passes make the same number of fills, one for each line of 4
KiB. Nothing else may run between the snapshots: the time any other step
takes there, a console line above all, is counted as the reads'. The block
is in an image only when a flash key other than `rom` names it; the link
drops it from every other.

**Every write is volatile.** SPI0's words, the two pads and the two input
selects are reset by a chip reset, and the ROM's attach rewrites SPI0's
clock and read mode at every boot (`SPI_init`, then
`esp_rom_spiflash_config_readmode(5, 0)`), whose mask keeps FDUMMY_OUT and
the polarities, which slow read ignores. A CPU-only reset would leave SPI0
as the key set it; none happens here (§2e, §14d).

**The lines.** Taken, one line after the clock key's:

```
SOS-S3: flash dio80 jedec=0x00204017 sr2=0x02 clock=0x00030103->0x80000000
ctrl=0x00200000->0x... ctrl2=0x00002c41->0x... user=0x000000c0->0x...
user1=0x5c000007->0x... user2=0x70000003->0x... canary=ok
read4k=<v>us-><v>us
```

(one line), each word before W5 and read again at W9, and for four lines
`pad27=`, `pad28=`, `insel2=` and `insel3=` before `canary=`. `read4k` is
the two timed reads in microseconds, before and after; they follow the
machine's clock and are printed, never judged. Not taken, the boot goes on
at the ROM's reads after one line, `SOS-S3: flash KEY not taken: ` and
the reason:

```
0x79  the description at 0x... or the record at 0x... is not in DRAM
0x70  the memory-SPI core clock is not the one the key is for: sysclk=
      cpu_per= mspi_core=, the description says sysclk= cpu_per=
      mspi_core= under the masks 0x00080fff, 0x7 and 0x3
0x71  SPI0 is not as the ROM leaves it: clock= ctrl= user1= user2= ddr=
      cali= din_mode= din_num=, the description says clock= ctrl= user1=
      user2= and 0 for the rest
0x72  a cache autoload is on: icache= dcache=
0x73  SPI1 MISC=0x... before command 0x.., the flash's commands need chip
      select 0 alone (0x00000002); nothing was sent
0x7B  SPI1 DDR=0x... before command 0x.., the flash's reads need single
      data rate (0); nothing was sent
0x74  SPI1 busy before command 0x.., SPI_MEM_CMD=0x... after 100000 polls;
      nothing was sent[; SPI1 put back]
0x76  the flash's JEDEC ID is 0x..., the description says 0x...; SPI1 put
      back
0x77  status register 2 is 0x.., QE (bit 1) clear; QIO needs it set and
      this kernel never writes it; SPI1 put back
0x78  the canary read 0x... at +0x.. where 0x... was expected; SPI0 put
      back to the ROM's words
0x7D  [the canary read 0x... at +0x.. where 0x... was expected; then ]
      the instruction|data cache did not report its freeze done, its
      freeze word reading 0x... after 100000 polls, and was unfrozen
      again; nothing was stored to SPI0 | SPI0 put back to the ROM's words
      with the caches running
0x7E  [the canary ...; then ] for each cache that did not report: the
      instruction|data cache did not report its unfreeze, its freeze word
      reading 0x... after 100000 polls; or did not report its freeze done,
      ..., and did not report the undo either, reading 0x...; then nothing
      was stored to SPI0 | SPI0 left switched, the canary not read | SPI0
      put back to the ROM's words; halted
```

0x78 follows `SOS-S3: flash KEY: canary mismatch`, written from IRAM once
W7 has put SPI0 back. A read that does not finish stops the machine, as
§18a's does: the read's own line from IRAM, then `SOS-S3: flash KEY not
taken: SPI1 command 0x.. did not finish, SPI_MEM_CMD=0x... after 100000
polls; halted`, and a quiet loop, since SPI1 may still be mid-command with
chip select 0 low. A reset clears it. 0x7E follows `SOS-S3: flash: a
cache did not report its unfreeze`, written from IRAM, and halts the same
way.

### 18f. The emulator and the smoke

The emulator does not model SPI0, the IO MUX or the GPIO matrix: they read
0 and drop what is stored (§18b). Its cache reads the image file directly,
whatever SPI0 holds, so a switch there, right or wrong, changes nothing,
and its MISC(1) reads 0 where the part's ROM leaves 0b10 (§18b). So on the
emulator the shipping kernel at any flash key but `rom` stops at W3's gate
(0x73), stores nothing to SPI0, SPI1 or the pads, and boots on: the
`boot_flash_*` cases hold the seam, the clock key's line then the
switch's, then the banner. The switch's decisions and its stores are held
through a harness, and only the part shows whether its reads work.

`tests/xtensa/flash_switch.saw` is that harness. It is built only with the
fault build (`psram_fault`), and its block (`_flash_switch_case_asm`) says
which switch to make, against which description (the kit's emulator or
part description, any word of it replaced), at which addresses, and which
reads lie. It runs the image's clock key, `pll80`, fills the table of lies,
stores MISC(1)'s part word as `s3_flash_probe`'s does (§18b), and calls
`s3flashkit`'s `flash_switch` as the key would. A lie's address of
0xCA0000NN names the canary's byte NN, which the harness places, since only
the link knows where the canary is. The lies go in the fault
build's table, `sos_flash_fault_table`, after its one record (§16e): up to
16 entries, each a step, a command, an address and a value (0 in any of
the first three: any), each answering every read it matches. With it the
emulator's 0s can read as the part's words, and status register 2 can read
QE set. The switch's steps for the table are 0xE1 (W2's clock words), 0xE2
(its SPI0 words, pads and input selects), 0xE3 (the autoload words), 0xE4
(SYSTIMER), 0xE5 (W5's freeze), 0xE6 (the canary), 0xE7 (W5's unfreeze,
and the undo of W5's freeze), 0xE9 (W9), 0xEA (W7's freeze) and 0xEB (W7's
unfreeze, and the undo of W7's freeze); W3's reads are §18b's steps, the ID
in command 1 and status register 2 in command 2.

`tests/xtensa/flash_key_halt.saw` holds what that harness, which calls the
switch itself, cannot: each key's own `flash_init`. Built at the case's
clock and flash keys with the fault build, it fills the table from the same
block, stores MISC(1)'s word, then runs `hal.clock_init()`, the clock key
and then the flash key against the emulator's description, and prints
`SOS flashkey: the boot went on` if the key returns. Each key's stores hold
the mode it passes; a lie that makes a read never finish (0x75) or an
unfreeze never report (0x7E) holds the key's halt, the run ending at the
emulator's deadline with the switch's line last (`expect_tail`).

**What holds it**, besides the console, each stop's lines held whole: right
after the clock key's line, each to its newline, with nothing between them
(`switch_stop`):

- the flash's commands (`flash_commands`): the ROM's `ABh`, then `9Fh`,
  `35h` and the model's `00h` after it, or fewer where a stop came first;
  and every run's `FLASH_COMMANDS_HEARD` (§18b);
- SPI1's stores (`expect_spi1_stores`): the setup's MISC(1) word and each
  read's stores as §18a gives them;
- the switch's stores (`expect_switch_stores`): every store the kernel made
  to SPI0's page, pads 27 and 28, their input selects and the two caches'
  freeze words, in order, from `_start`'s first store: W5's freeze, its
  stores and its unfreeze, and W7's put-back between its own;
- `device_untouched`: no other pad of the flash's bus or the PSRAM's, and
  no other input select of the PSRAM's signals;
- `check_iram`, rooted at `sos_flash_switch` (`iram_mutant_script_switch`
  requires it to name the call when the script's IRAM line is removed);
  `check_rom_flash_references` on every image; `check_clock_registers`
  over the flash keys (`clock_registers_flash_kit_store`); and
  `check_psram_fault`, which allows the fault record in this harness's
  emulator image as in the other two.

**The cases:**

```
case                                  how                             stop
boot_flash_dio80, _qio80, _dio40,     the shipping kernel at pll80    -
  _qio40                              and the key: W3's gate stops
                                      it, no store, boot goes on
flash_key_dio80_at_rom_refused,       a flash key with rom or xtal40  build
  _qio80_at_xtal40, _dio40_at_xtal40,
  _qio40_at_rom
flash_switch_dio80, _qio80, _dio40,   the harness, every W5 store on  -
  _qio40                              the emulator's words (QIO with
                                      QE read set)
flash_switch_part_words_dio80,        W2 read as F0's words on the    -
  _qio80                              part, the part's description:
                                      W5's words for the part
flash_switch_qio80_qe_clear           QIO with status register 2 as   0x77
                                      the emulator reads it, 0
flash_switch_stop_description_in_     the description in flash, or    0x79
  flash, _description_past_dram,      past DRAM's end; the record
  _record_in_flash, _record_past_dram the same
flash_switch_stop_clock_sysclk,       one clock word described        0x70
  _cpu_per, _core_clk_sel             otherwise
flash_switch_stop_spi0_clock, _ctrl,  one SPI0 word read otherwise    0x71
  _user1, _user2, _ddr, _cali,
  _din_mode, _din_num
flash_switch_stop_autoload_icache,    one autoload word read with     0x72
  _dcache                             AUTOLOAD_ENA
flash_switch_stop_gate                MISC(1) set up as 0b01          0x73
flash_switch_stop_ddr                 DDR(1) read with DDR_EN         0x7B
flash_switch_stop_busy, _busy_        SPI1 busy before the first      0x74
  second_read                         read; in the second's S7.1,
                                      SPI1 put back
flash_switch_stop_not_finished        the ID's read never finishes    0x75
flash_switch_stop_identity            the part's ID described         0x76
flash_switch_stop_canary_dio80,       the canary's first word read    0x78
  _qio80, _part_words_qio80           wrong: W7's put-back, CTRL2's
                                      on the part's words
flash_switch_stop_freeze_icache,      a cache's freeze word read 0    0x7D
  _dcache                             throughout: the freeze undone,
                                      the instruction cache's too
flash_switch_stop_freeze_icache_      the undo of the instruction     0x7E
  undo, _dcache_undo_icache           cache's freeze, or its unfreeze
                                      after the data cache's, not
                                      reported
flash_switch_stop_thaw, _thaw_dcache  a cache's unfreeze after the    0x7E
                                      stores not reported
flash_switch_stop_freeze_dcache_      the data cache's freeze and     0x7E
  undo_dcache                         its own undo not reported
flash_switch_stop_freeze_word_        a freeze not reported, its      0x7D
  nonzero                             word read 0x1
flash_switch_stop_w7_freeze_icache,   W7's freeze of a cache not      0x7D
  _dcache, _canary_second_word        reported: put back unfrozen;
                                      the canary wrong at +0x04
flash_switch_stop_w7_freeze_undo_     W7's freeze, then its undo or   0x7E
  icache, _undo_dcache,               the instruction cache's
  _dcache_ithaw                       unfreeze, not reported: put
                                      back, a cache maybe frozen
flash_switch_stop_w7_thaw,            W7's unfreeze of a cache not    0x7E
  _w7_thaw_dcache                     reported
flash_switch_qio80_qe_fd,             SR2 every bit but QE; QIO at    0x77
  _qio40_qe_clear                     40 MHz with QE clear
flash_switch_insel_bit7_qio80,        W2's input selects, pads and    -
  _pads_mcu_sel_7_qio80,              USER read with bits W5 clears
  _user_bit27_dio80                   set
flash_switch_core_clk_sel_masked      CORE_CLK_SEL read with [31:2]   -
                                      set: [1:0] alone compared
flash_switch_freeze_mode_cleared      the freeze word read with       -
                                      FREEZE_MODE set
flash_switch_read_back                W9's read of the clock lied to  -
flash_switch_stop_canary_second_      every canary word read as the   0x78
  word, _last_word, _dio40            first's; the last word alone
                                      wrong; at dio40
boot_flash_dio80_board_image          the shipping kernel's board     -
                                      image: the part's description,
                                      stopped at W2 (0x70)
flash_key_dio80, _qio80, _dio40,      each key's own flash_init:      -
  _qio40                              its mode's stores
flash_key_halt_not_finished,          the key halts after 0x75 and    halt
  _halt_thaw                          0x7E
flash_key_freeze_goes_on,             the key's boot goes on after    -
  _canary_goes_on                     0x7D and 0x78
psram_core_clk_sel_masked             the PSRAM's S0.3 compares       -
                                      CORE_CLK_SEL's [1:0] alone
kill_churn_flash_dio80                the gate's kill_churn at pll80  -
                                      and dio80
flash_keyed_image                     a description stating pll80     -
                                      and dio80, built and run
flash_key_without_pll_image           one stating dio80 alone         build
keys_stated_twice_flash               a description's [keys] and the  build
                                      case's flash key
```

The product phase holds a product's image that states a flash key with no
clock key to the same refusal (`product_flash_key_needs_pll`).

### 18g. The board stages

Each stage is a board image built from one commit and sent with its
SHA-256 and the text it should print. The only write to the part is
`write-flash 0x0`; no eFuse is burnt or written and nothing is erased. The
images write SPI0's words, two pads and two input selects, all volatile;
the flash chip is sent `9Fh` and `35h` and nothing else.

```
python3 tools/sos_runner.py --board esp32s3 --emit-flash boot_flash_dio80 \
    --out PATH
python3 tools/sos_runner.py --board esp32s3 --emit-flash kill_churn \
    --clock pll240 --flash dio80 --out PATH
```

**F1, `boot_flash_dio80` at `pll80`.** The kernel alone with the key:
`pll80`'s line, then

```
SOS-S3: flash dio80 jedec=0x00204017 sr2=0x02 clock=0x00030103->0x80000000
ctrl=0x00200000->0x00ac2008 ctrl2=0x00002c41->0x00002c00
user=0x000000c0->0x........ user1=0x5c000007->0x5c000003
user2=0x70000003->0x700000bb canary=ok read4k=<v>us-><v>us
```

(one line), then the banner and the refusal of an empty image, as
`boot_pll80`. It decides that DIO at 80 MHz reads the flash: the canary,
and every line after it, printed by code the cache fetched at 80 MHz. A
hang after `pll80`'s line, or the canary's line: then `boot_flash_dio40`,
built beside it, whose line is the same with `dio40` and `clock=...->
0x00010001`. 40 passing puts the fault in the timing at 80 MHz; 40 hanging
too puts it in the sequence.

**F2, `boot_flash_qio80` at `pll80`.** F1 with `qio80`: `ctrl=...->
0x012c2008`, `user1=...->0x5c000005`, `user2=...->0x700000eb`, and
`pad27=0x00001b00->0x00000b00 pad28=0x00001b00->0x00000b00
insel2=0x0000003c->0x0000003c insel3=0x0000003c->0x0000003c` before
`canary=ok`; `boot_flash_qio40` beside it. F0 read QE set (§18c), so the
switch is taken with nothing written to the flash.

**F3, the measure, at `pll240`.** `kill_churn` and `kill_churn_profile`,
each at the `rom` flash key (the control), `dio80` and `qio80`, built from
one commit and run one after another. Expected: each image's own text, the
clock line, the flash line (none at `rom`), and `200 rounds, every create
succeeded`; recorded, the time from reset to `200 rounds` and the profile's
seven phases with the console's stall.

**On the part** (the module of §15c), from one commit with the freeze
(§18e):

- F1, `dio80` at `pll80`: `pll80`'s line, then (one line)

  ```
  SOS-S3: flash dio80 jedec=0x00204017 sr2=0x02 clock=0x00030103->0x80000000
  ctrl=0x00200000->0x00ac2008 ctrl2=0x00002c41->0x00002c00
  user=0x000000c0->0x200000c0 user1=0x5c000007->0x5c000003
  user2=0x70000003->0x700000bb canary=ok read4k=2127.25us->339.43us
  ```

  then the banner and the refusal of an empty image. Every word is §18e's;
  USER reads back with USR_DUMMY [29] alone of W5.6's three bits, so SPI0
  keeps that one of USER's [31:27]. The timed read is 6.3 times as fast.
- F2, `qio80` at `pll80`: F1's words with `ctrl=...->0x012c2008`,
  `user1=...->0x5c000005`, `user2=...->0x700000eb`, pads 27 and 28
  `0x00001b00->0x00000b00` and the input selects `0x0000003c`, and
  `read4k=2127.25us->224.56us`, 9.5 times as fast. QE was set (§18c), and
  nothing was written to the flash.
- The 40 MHz images were not needed.
- Before the freeze, all four 80 and 40 MHz images hung with nothing after
  the clock key's line; the same switch with a console line and a wait for
  UART0 to go idle after each step took it, and so did every image once
  the caches were frozen around the stores.

F3, at `pll240`, in seconds from reset by the capture's clock, and the
profile's phases in microseconds:

```
flash  clock   200     create   give     start   settle   kill     release
key    line    rounds
rom    0.179   6.345   1086239  1021893  263889  1656164  1738204  318174
dio80  1.545   2.989    214562   140944   46582   582516  1647093   52790
qio80  1.546   3.000    159957   100415   33584   560642  1800490   37372

flash  all      console stalled
rom    6084565   368371
dio80  2684488  1371851
qio80  2692463  1596960
```

The profile's phases are the measure of the flash's speed. Creating,
giving, starting and releasing, which read the processes' code and data in
flash, are 5 to 10 times as fast at `dio80` or `qio80`; settling 2.8 to 3
times; killing hardly moves. QIO's gain over DIO is in the phases that read
flash most (create 1.3, give 1.4, start 1.4 times). `all` falls 2.3 times
and does not move between DIO and QIO.

At either flash key `kill_churn` is bound by its console. From `kernel up`
to `200 rounds` it prints 208 lines, 31,554 bytes, which take 2.739 s on
the wire at 115200 baud, 8N1; a capture with timestamped crumbs at `dio80`
stamped that span 2.753 s, the kernel's own crumbs on time throughout.
So about 2.7 s is the floor for a `kill_churn` run, whatever the flash
does, and the time from reset to `200 rounds` measures the wire, not the
flash. The console's stall is that floor showing through: the kernel
waiting for room in UART0's FIFO at the wire's rate, 6 percent of `all`
at `rom` and 51 and 59 percent at `dio80` and `qio80`.

A capture without crumbs stamped the first kernel line 1.545 s after reset
at `pll240` with either flash key, against 0.179 s at `rom` and about
0.2 s for F1 and F2 at `pll80`, and stamped the output from there to
`200 rounds` over 1.44 s, less than the wire takes to carry it. Those
lines left the part on time and were held between the part and the
capture's reads, in the serial bridge or the capture itself; the crumbs,
each sent before the kernel went on, were stamped in step with SYSTIMER.

## 19. The kernel stack's depth

`[_stack_bottom, _stack_top)` (`esp32s3.ld`, `KERNEL_STACK_SIZE`) is the one
stack the boot shim loads and every trap reloads. `kmain` paints it before
anything else uses it (`kcore.stackdepth.paint_kernel_stack`), below the
frame it runs in, and `kernel_stack_peak()` scans from `_stack_bottom` for the
first word that is no longer the paint and answers the bytes from there to
`_stack_top`. `sos_kernel_stack_bottom()`, `sos_kernel_stack_top()` and
`sos_stack_pointer()` in `sink.c` give Saw the two bounds and `a1`, none of
which it can name; `sos_stack_pointer` is a leaf, so the address it reads lies
below its caller's frame.

A harness kernel that calls `enable_stack_report` has the machine's last act
print `SOS: kernel stack peak=0x... size=0x...` and stop with a kernel-bug
report if the peak reached the stack's size. The runner's `stack_peak_*` cases
run gate cases on such a kernel (`tests/stack_peak.saw`) and print the peak
under the case's row; the number is printed and never matched.
