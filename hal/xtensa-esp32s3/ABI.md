# hal/xtensa-esp32s3 — the board facts

The record of what the ESP32-S3 emulator does, probe by probe, for the Xtensa
HAL that will be written against it. Nothing here is from a datasheet unless it
says so: each fact carries the probe that produced it (what was run and the
output that matters) or a cited file. "The emulator" means the QEMU below;
"silicon" means an ESP32-S3 part, and no claim in this file was probed on one.
Where the two can disagree, the section says so, and §11 collects every such
place.

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

So every output section that holds code lists `*(.literal .literal.*)` ahead of
`*(.text .text.*)`, and one output section's code must lie within 256 KiB above
its pools. The stub's script, which links cleanly:

```
.text : { *(.literal.start) *(.text.start)
          *(.literal .literal.*) *(.text .text.*) } > FLASH
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
  the FPU is off (`CPENABLE=0`); `PRID=0xCDCD` is the PRO CPU.

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

### 3a. RAM on the emulator is two separate arrays

`esp32s3.dram` and `esp32s3.iram` are two `ram` regions, not an alias.
Probed: `0xA5A5A5A5` stored at DRAM 0x3FC9_0000 and `0x5A5A5A5A` at IRAM
0x4038_0000 each read back their own value. Both arrays also extend past
silicon's ranges: stores at 0x3FD0_0000 and 0x403E_0000 read back.

Silicon's ranges, from esptool's ESP32-S3 target
(`esptool/targets/esp32s3.py`, `MEMORY_MAP`, in the ESP-IDF Python
environment under `~/.espressif/python_env`):

```
[0x3FC88000, 0x3FD00000, "DRAM"]     [0x40370000, 0x403E0000, "IRAM"]
[0x42000000, 0x42800000, "IROM"]     [0x3C000000, 0x3D000000, "DROM"]
[0x50000000, 0x50002000, "RTC_DATA"] [0x600FE000, 0x60100000, "RTC_DRAM"]
```

On silicon most of IRAM and DRAM are one SRAM seen through two buses (the
ESP32-S3 Technical Reference Manual, System and Memory chapter); the
emulator does not model that, so a layout that overlaps an IRAM range with
its DRAM twin works here and corrupts itself there. The stub keeps IRAM at
0x4037_8000 (16 KiB) and DRAM from 0x3FC8_C000, which stay disjoint under
either reading of the alias, and keeps out of 0x4037_0000-0x4037_7FFF, whose
use on silicon this file did not establish. Both points are for a real board
to confirm (§11).

### 3b. The ROM's RAM

From the ROM ELF's section headers, the ROM's `.bss`/`.data`/stacks occupy
0x3FCD_7E04-0x3FCE_FFFF: `.bss_shared_bufs` starts at 0x3FCD_7E04 and the
last section, `.data.interface.common`, is 4 bytes at 0x3FCE_FFFC. The
handover stack is inside it. 0x3FCF_0000 and above is not the ROM's. An image
that never calls the ROM may reuse the range once off the ROM's stack.

### 3c. What the bus allows (emulator)

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

The last five are emulator generosity that silicon may not share (§11).
These were probed on an image booted through the ROM, which sets up the
cache and its region attributes first; a `-kernel` image was not probed for
them.

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

**USB-Serial-JTAG** (0x6003_8000, `misc.esp32c3.usb_serial_jtag`): probed by
writing `USJ: hello\n` byte by byte to `+0x00` (EP1) and 1 to `+0x04`
(EP1_CONF, write-done). Nothing appeared on UART0's chardev or on three more
`-serial file:` chardevs, and EP1_CONF reads `0x00000000` before and after.
The emulator gives no second console here; on silicon USB-Serial-JTAG is the
dev-board console and its registers are for a real board.

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
28  LoadProhibited          load from 0x1000_0000
32  Coprocessor0Disabled    `wfr f0, a2` with CPENABLE = 0
```

The numbers are what the probes read. The names are the Xtensa ISA's, as
esp-idf's `xtensa/corebits.h` spells them; `StoreProhibited` is 29 there and
was not seen.

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

## 7. The trap frame under call0

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

### 7c. The frame the stub uses

```
F_PC 0 (EPC1)   F_PS 4   F_A0..F_A15 8..68
F_SAR 72   F_LBEG 76   F_LEND 80   F_LCOUNT 84
F_EXCCAUSE 88   F_EXCVADDR 92   F_SCOMPARE1 96   F_THREADPTR 100
FRAME_BYTES 104
```

The minimum a switch must save under call0 with `-fp` is `a0..a15`, `PC`,
`PS`, `SAR` and `SCOMPARE1`. The stub also saves `LBEG`/`LEND`/`LCOUNT`
(cheap, and user code built elsewhere may loop) and `THREADPTR` (per-thread
by definition). MAC16, `BR`, `MISC0..3` and the FPU need saving only if a
thread is allowed to use them; with `CPENABLE = 0` an FP instruction traps
(cause 32), which is where a lazy FP switch would hook in.

The entry and exit, as probed:

```
user vector:  xsr a1, excsave1         ; a1 = this thread's frame
              save a0, a2..a15, then excsave1 -> F_A1, epc1, ps, sar,
                lbeg, lend, lcount, exccause, excvaddr, scompare1, threadptr
              wsr lcount <- 0          ; see below
              a2 = frame, a3 = exccause, a1 = kernel stack ; callx0 ktrap
resume:       a1 = returned frame ; restore SRs ; wsr epc1 ; wsr ps ; isync
              restore a0, a2..a15 ; wsr a1, excsave1 ; l32i a1, F_A1 ; rfe
```

`EXCSAVE1` holds the running thread's frame address, which makes the restore
path the whole context switch and the first entry to user mode (a frame with
`PS = UM|EXCM` and `rfe`). The kernel runs with PS.UM=0, so its own faults go
to the kernel vector (and with EXCM=1, to the double vector) and are fatal
without a mode witness. `LCOUNT` is zeroed on entry because the zero-overhead
loop is address-triggered: with `LCOUNT` non-zero, kernel code reaching the
user's `LEND` address (shared code in a flat image) would branch to `LBEG`.

### 7d. What the windowed ABI would add

Under the windowed ABI the trap and switch paths would also have to: keep
the six window vectors live (§5a) instead of fatal; spill every live window
frame of the outgoing thread to its stack before a switch (the ROM's own
`xthal_window_spill` at 0x4005_3E74 does this, windowed); save and restore
`WINDOWBASE`, `WINDOWSTART` and `PS.WOE/CALLINC/OWB` per thread; and enter
the handler without letting a window overflow fire before the frame is safe.
The physical register file is 64 (`XCHAL_NUM_AREGS`), so a full spill is up
to 60 words. Under call0 none of this exists: one window, never rotated after
boot (§2b).

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
starts at `0x28`, a faulting value (§2d). Keep the ROM's `0x15`; which values
silicon honours for internal SRAM is for a real board.

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

---

## 11. Where silicon may differ (none of this probed on silicon)

- **Watchdogs.** The emulator leaves every watchdog config register 0
  (§2c). The ROM's flash path never calls its own
  `disable_default_watchdog` (RTC_CNTL_WDTCONFIG0 bit 12, TIMG0_WDTCONFIG0
  bit 14), and esptool's S3 target disables the RTC watchdog and sets the
  super-watchdog auto-feed (`RTC_CNTL_SWD_CONF |= 1<<31` under key
  `0x8F1D312A`, `esptool/targets/esp32s3.py`) when the chip is attached
  through USB-Serial-JTAG, with the comment "the RTC WDT and SWD watchdog are
  not reset". Until a real board says otherwise, a kernel on silicon should
  expect the RTC watchdog, TIMG0's watchdog and the super watchdog to be live
  after direct boot, and disable or feed them.
- **Direct boot can be fused off.** The ROM skips it when secure boot is on or
  eFuse 0x6000_703C bit 1 is set (§2a). The emulator's eFuse has it clear.
  Read a part's eFuses before relying on direct boot.
- **Clock.** CCOUNT runs at 40 MHz here; what the ROM leaves the CPU clock at
  on silicon, and whether anything changes it, is unprobed. SYSTIMER's 16 MHz
  is the clock to time with.
- **SRAM.** IRAM/DRAM are separate arrays here and one SRAM through two buses
  on silicon; both arrays here are also larger than silicon's (§3a).
- **Access rules the emulator forgives** (§3c): unaligned loads, byte access
  to IRAM addresses, executing from DRAM addresses, data loads from the flash
  IBUS window (which every `l32r` from XIP code is), stores to the mask ROM.
- **ATOMCTL.** Only bits [1:0] matter here (§8).
- **PIE** (`ee.*`): `ee.zero.q q0` executed here with `CPENABLE = 0`, no
  exception. esp-idf's S3 `tie.h` makes PIE coprocessor 3 (`cop_ai`,
  `XCHAL_CP_MASK` 0x09), so expect `CPENABLE` bit 3 to gate it on silicon.
- **The CPU's configuration ID.** `CONFIGID1` reads `0x23090f1f` here, which
  is `XCHAL_HW_CONFIGID1` in esp-idf's S3 `core-isa.h`. The ROM ELF's
  `xthals_hw_configid1` is `0x23086d67`, the configuration that ROM was built
  against (`CONFIGID0` agrees: `0xc2f0fffe` both).
- **The interrupt matrix reset value.** Unrouted map registers read 6 here,
  with the ROM or without it (a `-kernel` image reads 6 at sources 0 and 27),
  so 6 is the emulator's reset value and not something the ROM writes.
- **The second core.** Here the APP CPU runs ROM code and polls for an entry
  address (§2c). On silicon core 1 leaves reset held in reset with its clock
  gated (esp-idf's `soc/system_reg.h`: `SYSTEM_CONTROL_CORE_1_RESETING`
  defaults to 1 and `SYSTEM_CONTROL_CORE_1_CLKGATE_EN` to 0), so an entry
  address alone does not start it.
- **`VECBASE`** read back `0xFFFFFFFF` unmasked here (§5a).
- **The ROM banner.** Its third line carries the reset reason and the strap
  word (`boot:0x4` here, where `GPIO_STRAP` reads 4), so it differs on a
  board strapped otherwise.
- **Consoles.** UART0's FIFO never fills here (§4); USB-Serial-JTAG prints
  nothing here.
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
