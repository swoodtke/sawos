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
where a lazy FP switch would hook in. The trap path never writes a user
stack; the window vectors (§7d) do, through the thread's own registers.

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
                         26-word frame   76-word frame
vector to ktrap                50              112
resume to rfe                  37              100
round trip                     87              212
.text.vectors (IRAM)        1303 B          1777 B
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
  words in the smoke and the part's in a board image (§16a); every wait is
  bounded and every stop is reached by the fault build (§16e). *Unprobed on
  silicon:* the whole bring-up (§16f).

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
DRAM 0x3FC8_C000 - 0x3FCA_FFFF  kernel .data + .bss (arena, 64K stack)
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
path, `xtal40`'s, the S, P and Q rows the PLL path, `pll80`'s and
`pll160`'s, and the L rows `pll160`'s alone. The L rows are `rtc_init`'s
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
                                                              after P7
L6 reg 6 EXT_DIG_DREG [4:0] = g_dig_dbias_pvt_non_240m        pll160: yes;
                                                              then 40 us
                                                              (Q1's wait)
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
Q1 240 only, going up: I2C_DIG_REG (block 0x6D) reg 4         no: 240 only
   EXT_RTC_DREG [4:0] = g_rtc_dbias_pvt_240m, reg 6
   EXT_DIG_DREG [4:0] = g_dig_dbias_pvt_240m (regi2c), 40 us;
   the values come from eFuse calibration (rtc_init.c,
   rtc_set_stored_dbias; 28 by default)
Q2 going up: RTC_CNTL_DATE_REG.SLAVE_PD = 0x7 >> (f / 80),    no: see below
   3 at 80, 1 at 160
Q3 CPU_PER_CONF.CPUPERIOD_SEL = 0, 1, 2 for 80, 160, 240      yes: 0 at pll80,
   (clk_ll_cpu_set_freq_mhz_from_pll)                         1 at pll160, a
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

`pll160` holds each value to the range ESP-IDF's own arithmetic gives it
before it writes anything: the digital one below 240 MHz 3 to 31 (HVT 1 to 31,
plus 2, and never past the search's 31), the RTC one 15 to 31 (the searches'
first value and their answer when nothing is found). A value outside it comes
from no calibration and no default; the key prints its `not taken` line (§14d)
and writes nothing. Inside it, the key writes what ESP-IDF would write on the
part, and nothing else checks the voltage: a part's settings are read on it
first (§15d).

The two writes are made after P7, where an application makes them after its
bootloader has brought the PLL up, and in `rtc_init`'s order, RTC first. Each
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
  cannot droop. It is also an RTC register, which this kernel writes only for
  the watchdogs (§2e) and P1. Each line prints the word.
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
- X6, Q1 and Q7 apply only at 240 MHz or going down; the BBPLL's power-down
  applies only when leaving the PLL. On the part the CPU starts on the
  crystal (the emulator's word says PLL, §14e).
- R3's first write holds off the buses of the blocks in ANA_CONFIG [17:8]
  (`ANA_CONFIG_S` 8, `ANA_CONFIG_M` 0x3FF, regi2c_defs.h); the ROM's read
  stores above hold off every other bit's too, the SAR ADC's (bit 18,
  `I2C_SAR_M`) among them. The kernel uses none of those blocks.
- Q7. The keys are written for a CPU the ROM left on the crystal. On the
  kept path, a CPU already on the PLL, each stores its own CPUPERIOD_SEL
  whatever it read. A CPU found at 240 MHz would go down to 80 under
  `pll80` without Q7's voltage steps; under `pll160` it would get the two
  `non_240m` writes, but before the clock goes down, where Q7 makes them
  after it (`rtc_clk_cpu_freq_to_pll_mhz`, rtc_clk.c). The ROM leaves the
  crystal, so nothing reaches either.

### 14c. 160 MHz, and what is not built: 240

`pll80` is the PLL's first speed and `pll160` its second: the same sequence
with CPUPERIOD_SEL 1 at Q3, after `rtc_init`'s two LDO writes (L4 to L6),
which an application makes before it reaches 160 MHz and ESP-IDF v5.4.1's
bootloader never makes for its 80 MHz. (Earlier versions did: at v5.1
`rtc_clk_cpu_freq_to_pll_mhz` wrote both LDOs, RTC first, and waited 40 us
before every switch below 240 MHz, the bootloader's to 80 included.) 240
MHz is not built. It is the same again with
CPUPERIOD_SEL 2, and more:

- **The voltage.** 240 MHz raises both LDOs through I2C_DIG_REG to
  `g_rtc_dbias_pvt_240m` and `g_dig_dbias_pvt_240m` and waits 40 us (Q1),
  and puts the `non_240m` values back when leaving it (X6, Q7). `s3dbias`
  computes both pairs; only `pll160`'s is written.
- **The slaves.** Q2 opens five slaves at 160 and six at 240; all six are on
  as the ROM leaves them, so nothing changes at either.

At every PLL speed **the flash** does not move: the memory-SPI core clock
follows CORE_CLK_SEL and the PLL, not CPUPERIOD_SEL (`spi_mem_reg.h`), so it
stays 80 MHz and the flash 20 MHz.

### 14d. What each key sets

The key is chosen per image at build time (`s3clock`, `keys/clock-*/`); `rom`
is the default everywhere. The case key `"clock"` sets it, and `--emit-flash
CASE --clock KEY` overrides it for a board image. The keys that write share
`s3clockkit` (`keys/clock-common/`): the console's drain, the measurement, the
line's writer, and the PLL keys' bring-up and switch. Each starts SYSTIMER and
takes one snapshot it throws away before it measures (§14h).

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
pll80   SYSTIMER and UART0 as xtal40                   as xtal40
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
        SYSTIMER, UART0 and the bring-up as pll80      as pll80
        on a timeout nothing below is written
        BBPLL register 8 read through the ROM          for the line
        I2C_DIG_REG regs 4 and 6 read through the ROM  for the line
        I2C_DIG_REG reg 4 [4:0] = the RTC setting,     L5, through
          then reg 6 [4:0] = the digital one           rom_i2c_writeReg_Mask
        SYSTIMER read for 40 us                        Q1's wait
        I2C_DIG_REG regs 4 and 6 read again            for the line
        CPU_PER_CONF = read & ~0x3 | 0x1               Q3, from IRAM
          (CPUPERIOD_SEL 1)
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
no reading that those settings hold at 40 or 80 MHz, so the flash stays at
the 20 MHz the ROM's own divider names.

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
MHZ"); the watchdogs are off, so nothing here makes one. `pll80` and `pll160`
also write the analog master's words, the BBPLL's analog registers and one RTC
register, RTC_CNTL_OPTIONS0, whose three BBPLL power-down bits they clear.
Each of those bits resets to 0, so on a part that reads them 0 the store
writes the word back unchanged. `pll160` writes two analog registers more, the
LDO settings EXT_RTC_DREG and EXT_DIG_DREG, and reads the eFuse. A power cycle
restores all of them. An EN-pin reset restores the clock words: on the part
each reset through the serial port's RTS line printed `rst:0x1 (POWERON)`, and
a run that found `sysclk=0x000a8001` followed one that had left 0x000a8400
(§14h). It restores the two LDO fields: `kill_churn_profile` at `pll160` was
written after `kill_churn` at `pll160` had raised them, with a reset through
RTS between and no power cycle, and its line read the ROM's 0x18 in both
before its writes (§14h). Whether it restores OPTIONS0, ANA_CONFIG and the
BBPLL's words has not been observed, and the runs so far cannot show it:
OPTIONS0's three bits read 0 before every run, which is both their reset value
and the value the bring-up leaves, so a reset that kept them would read the
same. It is the reference manual's to say, not ESP-IDF's sources'; an image
that does not write them leaves them as it finds them. The PLL powered and set
to 480 MHz, and the LDOs at the eFuse's settings, are the state ESP-IDF runs
every application below 240 MHz in. Nothing written is non-volatile, and the
eFuse is read and never written or burnt.

**A wrong value on a part.** A PRE_DIV_CNT too large slows the CPU, and under
H3 the flash with it, down to 40 MHz / 1024; a SOC_CLK_SEL of 3 names no
source. A PLL set to the wrong frequency, or switched to before it has locked,
clocks the CPU, APB and the flash wrongly. Any of these looks like a hang: no
line after the ROM's banner, or one that crawls; the console's clock is the
crystal, so whatever does print is readable. `pll80` switches only after the
PLL has reported its calibration done, and prints its `not taken` line instead
when it has not. An LDO setting too low for the clock makes the digital core
fail at 160 MHz, which looks the same, or like faults no case explains;
`pll160` writes the settings ESP-IDF writes on the part, read there first
(§15d), and a power cycle puts the ROM's back. A wrong UART word garbles the
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
                     about 40.00 on both sides
```

Each key's line, up to the two speeds and after them, is asserted; the
speeds are host time and are not. `boot_pll160` runs `pll160` on the
uncalibrated eFuse (`ldo=default`, 27 and 27) and `boot_pll160_calibrated`
on a calibrated one, block version 1.2 with negative slopes and voltages and
HVT 24 (`ldo=efuse`, 26 and 30: the image builder's tests hold those to
ESP-IDF's arithmetic). `pll160_refused` hands the key settings no eFuse can
give (a digital 32): it prints its `not taken` line, SYSTIMER's CONF still
reads its reset value 0x4600_0000, which the key's first step past the check
changes, and the boot goes on.

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
at 16 MHz (a stalled host only lengthens it); and no store by
`pll160_refused` to the analog master, ANA_CONFIG, ANA_CONFIG2, either
clock word or SYSTIMER's CONF.

`pll_bringup` runs the bring-up itself, a
harness kernel that calls `sos_pll_bringup`: its seven ROM calls go through
the stub and return, the calibration is never reported done, the bring-up
answers its timeout (1) after the bound, and RTC_CNTL_OPTIONS0's three
power-down bits, set first, come back clear with the bits beside them kept
(`options0=0x1c000000`). The timeout's outcome does not depend on the
host's speed: CAL_DONE never comes, and the bound only says when to stop.

**What the emulator cannot judge**, and only a part can: the analog words (P4:
values, order, and that the ROM's functions deliver them; that L5's and L6's
reach the LDOs, and their field's width: the ROM's masked write clears the
field it is given in what it read and ORs the data in whole, so a field of
[3:0] would send the same command where the register's bit 4 is clear or the
setting's is set, as in the emulator (the register reads 0) and on this part
(settings 0x1c and 0x1d); a setting below 16 over the ROM's 0x18 would go out
16 high), R3, P2, P3, P6 and P7, which land in
registers the emulator drops; the voltage the LDO settings give and the CPU's
running at 160 MHz on it; the calibration and the PLL's lock (P5's done path
and `bbpll=`); the speed the three clock stores give; the wait's own length,
since one load of SYSTIMER takes a few hundred of its counts under the
emulator, so the span holds that 40 us passed between the writes and the
switch but a wait asked for a single count can span them too; the bring-up's
path through `pll80` and `pll160` (the emulator's source is the PLL); that the
bring-up and the stub need IRAM; the flash at 20 MHz; and the snapshot thrown
away before a measurement.

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

The smoke's clock cases are `boot_xtal40`, `boot_pll80`, `boot_pll160` and
`boot_pll160_calibrated` (the kernel alone at the key: the line, the banner,
the refusal of an empty image), `kill_churn_xtal40`, `kill_churn_pll80` and
`kill_churn_pll160` (`kill_churn` at the key), `pll_bringup` (the bring-up)
and `pll160_refused` (the LDO settings' stop) (§14e). `kill_churn_profile` is
board-only: the smoke prints its reason and builds nothing for it. A board
image of each is `--emit-flash CASE --clock KEY --out PATH`.

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
above took their first SYSTIMER snapshot inside the first measurement, just
after enabling its clock; if that first handshake is the slow one, the
measurement counts its time in SYSTIMER's interval and not in CCOUNT's. The
keys take one snapshot first and throw it away (`systimer_start`), so an
`xtal40` image built that way reads its figure before the store without the
first handshake (the table below).

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
on this part: HVT + 2, and the RTC setting that search gives for it.

---

## 16. The PSRAM's command layer

The part's 8 MiB octal PSRAM sits on the memory-SPI controller the flash
uses, on its own chip select (CS1, pad 26) and data pads (the flash's four
plus 33 to 37). Bringing it up means setting those pads, the controller's
PSRAM timing and clocks, and the chip's mode register 0, with commands sent
on SPI1; a command sent with the flash's chip select enabled goes to the
flash. This section is the command layer: the bring-up's checks and its
commands, steps S0 to S10, and the measurement that settles the memory-SPI
core clock. Not built here: SPI0's PSRAM phases, the cache's map of the
PSRAM, the read-back test and the board that brings the PSRAM up at boot
(S11 onward). No image of the tree calls it but its harnesses.

- `kernel/mspi.c` makes every step and every check, in one call from IRAM
  (`sos_psram_bringup`), and returns at the first check that fails with a
  record of what it read; `sos_psram_time_reads` is the measurement.
- `kernel/lib.saw`'s PSRAM section passes the description, starts and reads
  SYSTIMER around the measurement, and prints the line a stop's record says
  (`psram_stop_line`).
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
unlikely reading; no source states it. Board stage P2 measures it.

What each clock key leaves (§14d, §14h), what S0.3 compares, and the bus at
the divider every key uses, divide by 4:

```
key       SYSCLK_CONF  CPU_PER_CONF  CORE_CLK_SEL  core clock          bus at
          & 0x80FFF    & 7                                             divide 4
rom       0x00080001   0x4           0             H1 40, H2 80, H3 20  10, 20,
                                                   MHz                  5 MHz
xtal40    0x00080000   0x4           0             H1 40, H2 80, H3 40  10, 20,
                                                                        10 MHz
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
20 MHz is a later choice, made once P2 has measured the core clock under
the key the board uses: 4 under `pll80`, and under `rom` 2, 4 or 1 for H1,
H2 or H3.

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

**The measurement's reads** are 2 and 10 bytes of the array and no other
length (`sos_psram_time_reads` stops with 0x61 on any other), not 64: at
5 MHz a 64-byte read holds the chip select low for 45 to 55 clocks, about
10 us, longer than any mode-register command and than the cache fill the
divider is chosen to avoid at that speed; a 10-byte read is about the
length of a mode-register command. The 8 bytes more are 4 SPI clocks in
octal DTR, so over 1,000 reads the difference is 4 x 4 x 1,000 core clocks,
3,200 to 12,800 SYSTIMER ticks at 80 down to 20 MHz, and the core clock in
MHz is 4 x 4 x 1,000 x 16 over the difference in ticks. The chip's own
chip-select-low limit is in AP Memory's datasheet, which was not read.

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
time  S5, S6; per read a command 0x0000 (address 0,     IDF OPI_PSRAM_SYNC_
reads   dummy 18, 2 or 10 bytes in); S10                 READ, OCT_PSRAM_RD_
                                                         DUMMY_BITLEN
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
0x61 time   ... a timing read of 0x........ bytes; the measurement reads 2
            or 10                                    (before S5, nothing sent)
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
changed, so the stops before it need no S10. 0x61 is the measurement's own:
it asked for a length it never sends.

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
- The timing reads finish on host time: the measurement prints a number
  that means nothing here.

**The fault build.** Every register read in `mspi.c` goes through `rd`;
built with `-DSOS_PSRAM_FAULT`, `rd` consults the DRAM record
`sos_psram_fault`, filled in by the harness before the call: the read at
that step (`mspi.c`'s STEP_* values), in that command (1 for the call's
first), of that address, returns the record's value that many times. Writes
always reach the register. The record also counts the reads it answered,
the commands started and the answers read. The case key `psram_fault`
builds an image's profile with the define; the runner refuses any image
that defines `sos_psram_fault` unless it is `psram_fault.saw`'s, and any
board image that defines it (`check_psram_fault`; the cases
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
psram_mode                        P2; MR0 0x09 -> 0x28; as before        -
psram_identity_commands           fault build, no fault: 4 commands,     -
                                  4 answers read
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
psram_stop_read_length            the measurement asked for 64 bytes     0x61
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
cycle undoes every one; a chip reset all but MR0, which the next bring-up
reads at the same dummy either way. They are built with `python3
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

(each wrapped line is one line). MR0 is the chip's power-on value after a
power cycle and 0x28 after an earlier P2; both are expected.

**P2, `psram_mode`.**

```
SOS psram: MR0=<v> before, 0x28 written, MR0=0x00000028 after
SOS psram: 1000 reads of 2 bytes took <v> ticks, of 10 bytes <v> ticks
SOS psram: core clock <v> MHz by 4 clocks x 4 per read
SOS psram: SPI1 MISC=0x00000002 clock=0x00030103 after the call, as before it
SOS-S3: halt pass
```

The core clock it prints names the reading of §16b under KEY: at `rom`, 40,
80 or 20 MHz is H1, H2 or H3; at `xtal40`, 40 is H1 or H3 and 80 is H2; at
`pll80` it should be 80. A value far from all of them ends the series until
it is understood.

| image | part |
|---|---|
| P1 at `rom` | |
| P1 after P2 | |
| P2 at `rom` | |
| P2 at `pll80` | |
