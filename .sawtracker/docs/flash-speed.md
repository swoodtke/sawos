# SO-147: the ESP32-S3's flash at full speed (design)

For the owner and the reviewer, before anything is built. Written against
main at `6ca48b20`. Nothing in the repository was changed.

Today the flash runs at 20 MHz in single-line slow read under every clock
key: the memory-SPI core clock is 80 MHz on the PLL and SPI0 divides it by 4
(ABI.md §14c, §14h). Processes and the kernel's own code run in place from
flash (`esp32s3.ld`), so every instruction-cache and data-cache miss waits
on it.

Sources, all public, read for this design:

- **[IDF]** ESP-IDF tag `v5.4.1`, `components/` of it, fetched from
  github.com/espressif/esp-idf:
  `bootloader_support/bootloader_flash/src/bootloader_flash_config_esp32s3.c`,
  `bootloader_support/bootloader_flash/src/bootloader_flash.c`,
  `bootloader_support/bootloader_flash/src/flash_qio_mode.c`,
  `bootloader_support/src/esp32s3/bootloader_esp32s3.c`,
  `bootloader_support/include/esp_app_format.h`,
  `spi_flash/spi_flash_chip_generic.c`, `spi_flash/spi_flash_chip_drivers.c`,
  `spi_flash/esp_flash_api.c`, `spi_flash/flash_ops.c`,
  `spi_flash/include/spi_flash/spi_flash_defs.h`,
  `spi_flash/esp32s3/Kconfig.flash_freq`, `esptool_py/Kconfig.projbuild`,
  `esp_hw_support/mspi_timing_tuning.c`,
  `esp_hw_support/port/esp32s3/mspi_timing_config.c` and
  `mspi_timing_tuning_configs.h`,
  `hal/esp32s3/include/hal/mspi_timing_tuning_ll.h`, `cache_ll.h`,
  `efuse_ll.h`, `esp_rom/esp32s3/include/esp32s3/rom/spi_flash.h`,
  `esp_rom/esp32s3/include/esp32s3/rom/cache.h`,
  `esp_rom/patches/esp_rom_cache_esp32s2_esp32s3.c`,
  `esp_rom/esp32s3/ld/esp32s3.rom.ld`, `esp_system/port/cpu_start.c`,
  `esp_psram/esp32s3/esp_psram_impl_octal.c`,
  `soc/esp32s3/register/soc/` `spi_mem_reg.h`, `efuse_reg.h`, `extmem_reg.h`,
  `soc/esp32s3/include/soc/soc_caps.h`.
- **[ROM]** The mask ROM's ELF, `~/.espressif/tools/esp-rom-elfs/20241011/
  esp32s3_rev0_rom.elf`, disassembled with the installed
  `xtensa-esp32s3-elf-objdump`: `main` around 0x4004_3AAC,
  `spi_flash_boot_attach` (0x4004_A8CC), `esp_rom_spiflash_config_readmode`
  (0x4004_A820) and its table `CSWTCH$195` (0x3FF1_E060), `Enable_QMode`
  (0x4004_A0D4), `Disable_QMode` (0x4004_A128), `spi_cache_mode_switch`
  (0x4004_A190), `esp_rom_spiflash_config_clk` (0x4004_A9B0),
  `spi_dummy_len_fix` (0x4004_94F4), `esp_rom_spiflash_select_qio_pins`
  (0x4004_95C4), `spi_common_set_dummy_output` (0x4004_A458),
  `ets_config_flash_by_image_hdr` (0x4004_559C).
- **[QEMU]** espressif/qemu at tag `esp-develop-9.2.2-20250817`, the
  installed emulator's build: `hw/xtensa/esp32s3.c`, `hw/block/m25p80.c`,
  `hw/misc/esp32s3_cache.c`.
- **[ESPTOOL]** esptool 5.1.0 as installed in `~/.espressif/python_env`:
  `esptool/cmds.py`, `_update_image_flash_params`.
- **[SFDP]** Linux v6.12 `drivers/mtd/spi-nor/sfdp.h` and `sfdp.c`, which
  quote JESD216B's field definitions.
- **[TREE]** This repository: ABI.md §2, §14 to §16, `kernel/boot.S`,
  `kernel/mspi.c`, `kernel/esp32s3.ld`, `keys/`, `board.toml`,
  `tools/sosimage/src/flash.saw`, `board_image.saw`; the SO-102 design
  (`.leadwork/SO-102-design.md`); SO-76's comments c5 and c6 (the board's
  first contact and `flash-id`); SO-132.p2's brief (`pll240`).

Not read: the flash chip's datasheet (XMC XM25QH64C, by its ID), the
module's datasheet, the ESP32-S3 Technical Reference Manual and JESD216
itself. Where a fact would come from them, the text says so.

---

## 0. Verdict

1. **The flash is an XMC quad part, and ESP-IDF knows it.** esptool read
   manufacturer 0x20, device 0x4017, 8 MiB, quad, 3.3 V on the owner's
   part (SO-76 c6). ESP-IDF's strict XMC match takes that ID
   (`is_xmc_chip_strict`: vendor 0x20, 0x40, 0x13 to 0x20) and runs it under
   its generic chip driver. ESP-IDF v5.4.1 runs a quad flash on this chip at
   20, 40 or 80 MHz in DIO, QIO and the slower modes, 80 MHz being its
   default; 120 MHz needs the flash's high-performance mode and tuning (§1).
2. **The target is QIO at 80 MHz, reached through DIO at 80 MHz.** DIO
   needs no write to the flash chip at all; QIO needs the flash's QE bit,
   which a read-only probe reads first. 40 MHz images are built beside each
   80 MHz stage and run only if 80 hangs, to tell a timing failure from a
   sequence failure (§2).
3. **Every write the switch makes is volatile, and is to SPI0 and two pads.**
   SPI0's CTRL, CTRL2, USER, USER1, USER2 and CLOCK, and for QIO pads 27 and
   28 and their two input selects: the stores the ROM's own QIO path and
   ESP-IDF's bootloader make, each cited (§3). No ROM routine is called for
   them, because the ROM's read-mode routine writes the flash's QE bit
   itself (`Enable_QMode`, `Disable_QMode`).
4. **The QE bit.** QIO needs it set; DIO does not. If the probe reads it set,
   nothing is ever written to the flash. If it reads clear, the one write
   that sets it is non-volatile, ESP-IDF's own (WREN, then `01h` with two
   bytes, status register 2 as read with bit 1 set), and is a separate
   harness stage the owner must approve; no product image ever sends a
   command that writes the flash (§3.3).
5. **The core clock does not move.** Only SPI0's flash divider changes,
   divide by 4 to divide by 1 of the 80 MHz core. The PSRAM's divider and
   SPI1's are untouched, so SO-102 §3.3's contract holds as written. At 80
   MHz STR with the PSRAM at 40 MHz or less ESP-IDF tunes nothing, so no
   timing tuning is needed (§4).
6. **No image header is involved.** Our image direct-boots: byte 0 is 0xFF,
   and the ROM's header routine, the only place it reads a mode or speed,
   is never reached. `sosimage flash` writes no header and nothing changes
   there (§5).
7. **The switch runs from IRAM as a clock-seam key, `s3flash`**, after the
   clock key and only on a PLL key; every check it makes stops before its
   first store, and after the stores it reads a canary through the data
   cache and puts SPI0 back if it differs. The emulator models SPI1's
   commands to the flash and none of SPI0, so the smoke holds the stores by
   trace and the decisions by descriptions and a fault build; only the part
   judges the switch (§6).
8. **Five board stages**, the first reading only, and the measure is
   `kill_churn` at `pll240` with the flash at 20 MHz, DIO 80 and QIO 80
   (§7). **Four units**, two of them Opus, one conditional on the owner's
   ruling on QE (§9).

---

## 1. The module's flash

### 1.1 What is known

- **The part** (SO-76 c6, esptool v5.6.0 `flash-id`): ESP32-S3 (QFN56)
  revision v0.2, embedded PSRAM 8 MB (AP_3v3), flash manufacturer 0x20,
  device 0x4017, 8 MB, quad, 3.3 V. The module's marking was never recorded
  (SO-76 c4 asked for it), so "N8R8" here means what esptool and the eFuse
  say: an 8 MB quad flash beside an 8 MB octal PSRAM.
- **The chip.** 0x20 is XMC's JEDEC code in ESP-IDF (`XMC_VENDOR_ID_1 0x20`,
  `bootloader_flash.c`), and `is_xmc_chip_strict` matches 0x20 / 0x40 /
  0x13..0x20, which takes 0x17. So `bootloader_flash_xmc_startup` would skip
  its recovery flow ("XMC chip detected by RDID ... skip"), and ESP-IDF's
  application drives it with the generic chip driver
  (`spi_flash_chip_drivers.c`: "FM, Winbond and XMC chips are supposed to be
  supported by this chip driver"). The capacity code 0x17 is 2^23 bytes.
- **Quad, not octal.** The eFuse's `FLASH_TYPE` (`EFUSE_RD_REPEAT_DATA3_REG`
  0x6000_703C bit 9, "0: four lines. 1: eight lines", `efuse_reg.h`;
  `efuse_ll_get_flash_type`) decides octal flash, and esptool reported
  quad. The probe reads the bit.
- **The modes ESP-IDF runs a quad flash in on this chip**: QIO, QOUT, DIO,
  DOUT, fast read and slow read (`esp_image_spi_mode_t`,
  `esp_app_format.h`; the ROM's `CSWTCH$195` table has one CTRL word for
  each). Its speeds for the S3 are 20, 40 and 80 MHz, 80 the default, and
  120 MHz only with the flash's high-performance mode or an octal flash
  (`spi_flash/esp32s3/Kconfig.flash_freq`: `ESPTOOLPY_FLASHFREQ_120M`
  depends on `SPI_FLASH_HPM_ON || ESPTOOLPY_OCT_FLASH`). Its default mode is
  DIO (`esptool_py/Kconfig.projbuild`, `default ESPTOOLPY_FLASHMODE_DIO`).
- **Slow read cannot go to 80 MHz.** The ROM leaves the flash on `03h`,
  READ, which ESP-IDF marks "Speed limited" (`CMD_READ`,
  `spi_flash_defs.h`): `03h` has no dummy cycles, so a flash bounds its
  clock below the fast reads'. Raising the clock means changing the read
  command, which is why this design is a mode change and not a divider
  change.
- **What the part has already shown.** Its first boot, before any of our
  images, printed `mode:DIO, clock div:1` (SO-76 c5): the ROM read the
  factory image's header and ran the flash in DIO at divide by 1 of the
  memory-SPI core clock, CPU on the crystal. What that core clock is on the
  crystal is SO-102's open reading (H1 40, H2 80, H3 20 MHz). If the PSRAM's
  stage P2 at `rom` settles H2, that banner is the ROM reading this flash
  in DIO at 80 MHz, with no extra dummy and the pads as the ROM sets them.
- **What the ROM leaves in SPI0 on our direct boot** (ABI.md §15c): CLOCK
  `0x00030103` (divide by 4), CTRL `0x00200000` (no fast-read or
  multi-line bit: `03h` on one line; WP_REG only), USER `0x000000c0`,
  USER1 `0x5c000007`, USER2 `0x70000003` (an 8-bit `03h`), CACHE_FCTRL
  `0x00000005`, DDR 0, CORE_CLK_SEL 0, the ROM's extra dummy 0 for both
  controllers. The pads 29 to 32 are at function 0 with drive 2 and
  pull-ups; 27 and 28 (HD, WP) are GPIOs. Not yet read: CTRL1, CTRL2,
  TIMING_CALI, DIN_MODE, DIN_NUM, the input selects of signals 2 and 3, and
  the instruction cache's autoload word.

### 1.2 The probe: reading what the part has, writing nothing

Stage F0, `s3_flash_probe`, is the first image to send commands to the flash
chip. It reads, and every command it sends is a read:

```
command   what it returns                    IDF name, source
9Fh       JEDEC ID, 3 bytes                  CMD_RDID, bootloader_read_flash_id
05h       status register 1                  CMD_RDSR
35h       status register 2 (QE is bit 1)    CMD_RDSR2; the default entry of
                                             bootloader_flash_qe_support_list
15h       status register 3                  RDSR3 (flash_qio_mode.c,
                                             bootloader_read_status_8b_rdsr3)
5Ah       SFDP, 24-bit address, 8 dummy      CMD_RDSFDP,
          cycles, up to 4 bytes a command    bootloader_flash_read_sfdp
```

It sends them on SPI1 as ESP-IDF's bootloader does
(`bootloader_flash_execute_command_common`): CTRL(1) cleared but WP_REG,
command phase 8 bits, the address and dummy phases only for `5Ah`, MISO
length the answer's, then `SPI_MEM_CMD(1)` = `SPI_MEM_USR` (bit 18) alone,
and a bounded wait. Chip select 0 is the flash: `SPI_MEM_MISC(1)` must read
`0b10` (CS1 disabled, CS0 enabled), which is the ROM's word and the one the
PSRAM layer's S10 puts back (ABI.md §15c, §16c). The probe reads MISC and
stops if it is anything else; it never writes MISC. Every SPI1 word it
touches is saved first and written back last, as `mspi.c`'s S5 and S10 do.

The bits beside `SPI_MEM_USR` in `SPI_MEM_CMD(1)` start the controller's
built-in flash commands (write enable, write status, erase, program, deep
power-down; ABI.md §16c). None is written. No `06h` (write enable) is sent,
so the flash's write-enable latch stays clear and a write opcode sent by
mistake would be ignored by the chip; `50h` (write enable for the volatile
status register), `B9h` and `79h` (deep and ultra-deep power-down) are never
sent either.

**What SFDP gives, read-only.** SFDP is the chip describing itself (JEDEC
JESD216). The probe reads the header at 0, `0x50444653` ("SFDP"), the
revision and the number of parameter headers, then the first parameter
header at 8 (ID `0xFF00`, the Basic Flash Parameter Table, its length in
DWORDs and its 24-bit pointer), then BFPT DWORDs 1, 3, 4, 15 and 16
([SFDP] `struct sfdp_header`, `struct sfdp_parameter_header`,
`SFDP_BFPT_ID`). From them ([SFDP] `sfdp_bfpt_reads`, `sfdp.h`):

- DWORD 1 bit 20: 1-2-2 fast read (DIO) supported; bit 21: 1-4-4 (QIO);
- DWORD 4 bits [31:16]: the 1-2-2 read's opcode [31:24], mode clocks
  [23:21] and dummy clocks [20:16]; DWORD 3 bits [15:0]: the 1-4-4 read's,
  in the same layout;
- DWORD 15 bits [22:20], QER, how QE is set: 001b, 100b and 101b are "bit 1
  of status register 2, set via Write Status with two data bytes where bit
  1 of the second byte is one" (they differ in whether a one-byte write
  clears status register 2), 010b is bit 6 of status register 1, 011b bit
  7 of status register 2 through `3Eh`, 000b no QE bit.

ESP-IDF counts the mode clocks as dummy: 4 for DIO and 6 for QIO
(`SPI_FLASH_DIO_DUMMY_BITLEN`, `SPI_FLASH_QIO_DUMMY_BITLEN`, and the ROM's
USER1 values in §3). SFDP's mode-plus-dummy clocks for `BBh` and `EBh`
should equal those. A table shorter than 15 DWORDs (JESD216 before rev A)
has no QER; the line then says so.

The SFDP addresses are read in the order above, each 4 bytes, nine commands
in all; a signature that does not read `0x50444653` ends the SFDP reads and the
line prints `sfdp=none`. ESP-IDF reads SFDP from XMC parts itself
(`bootloader_flash_xmc_startup` reads SFDP address 0x10 for the
manufacturer), so the command is one this chip is expected to answer.

**The lines** (each wrapped line is one line):

```
SOS flashprobe: efuse repeat_data3=0x........ flash_type=0
SOS flashprobe: id jedec=0x00204017 sr1=0x.. sr2=0x.. sr3=0x..
SOS flashprobe: sfdp signature=0x50444653 rev=1.N nph=N bfpt rev=1.N
                len=N ptr=0x......
SOS flashprobe: bfpt dw1=0x........ dw3=0x........ dw4=0x........
                dw15=0x........ dw16=0x........
SOS flashprobe: decoded dio=yes op=0xbb wait=4 qio=yes op=0xeb wait=6
                qer=0bNNN qe=set|clear
SOS flashprobe: spi0 clock= ctrl= ctrl1= ctrl2= user= user1= user2= fctrl=
                ddr= cali= din_mode= din_num= date= core_clk_sel=
SOS flashprobe: pads 27= 28= 29= 30= 31= 32= insel 2= 3=
SOS flashprobe: cache icache_autoload= dcache_autoload=
SOS flashprobe: SPI1 MISC=0x00000002 clock=0x00030103 after the reads, as
                before them
SOS-S3: halt pass
```

`wait` is mode plus dummy clocks. `qe` is status register 2's bit 1, where
ESP-IDF looks on this ID (the default entry, `bootloader_read_status_8b_
rdsr2`, QIE bit 1; the generic driver's `spi_flash_chip_generic_get_io_mode`,
"bit 1 (QE) of RDSR2 (35h) result").

**What each word decides:**

- `jedec`: the description the switch checks (§6.3); it must be
  `0x00204017`, as esptool read.
- `sr2` bit 1: whether QIO needs a write (§3.3). `sr1`'s BP bits, `sr1` bit
  7 (SRP0) and `sr2` bit 0 (SRP1) are recorded: they matter only to a
  status-register write, which they could block or make permanent.
- `dw1`, `dw3`, `dw4`: that the chip claims 1-2-2 and 1-4-4 reads with the
  opcodes and clocks the ROM's QIO and DIO words use.
- `dw15`'s QER: which write sets QE, if one is ever needed.
- `spi0 ...`: the ROM-left words the switch's description holds (§6.3),
  including the ones §15c did not read.
- `insel 2`, `3`: the two input selects QIO changes.
- the autoload words: that no cache fetch can start on its own during the
  switch (§6.1).
- `flash_type`: 0 confirms quad.

**The clock it runs at.** F0 is built at the `rom` key. Its commands run at
SPI1's divide by 4 of the core clock: 10, 20 or 5 MHz under H1, H2, H3,
inside every read command's speed.

---

## 2. The target

**QIO at 80 MHz, through DIO at 80 MHz first.** Cache line fills, by clocks
on the wire (command, address, mode and dummy, data), for the 16-byte lines
the part's caches use (ABI.md §15c):

```
mode, clock      command  address  wait  data  clocks  time per line
slow read, 20    8        24       0     128   160     8.0 us
DIO, 40          8        12       4     64    88      2.2 us
QIO, 40          8        6        6     32    52      1.3 us
DIO, 80          8        12       4     64    88      1.1 us
QIO, 80          8        6        6     32    52      0.65 us
```

plus the chip-select setup and hold and whatever the cache adds per fill,
which no source states. So DIO 80 is about seven times the ROM's rate per
miss and QIO 80 about twelve; QIO over DIO is 1.7.

- **Why DIO first.** DIO needs no write to the flash chip in any case: IO2
  and IO3 stay WP# and HOLD#, held high by the pads' pull-ups while 27 and
  28 are GPIOs. It is ESP-IDF's default mode. It isolates the clock and the
  sequence from the QE question, and it is the mode the ROM ran this part
  in at its first boot.
- **Why not 40 first.** Every write is volatile, and a failure looks like a
  hang that a reset undoes (§8), so a step at 40 buys safety the design
  does not need; ESP-IDF's tuning tables say 80 MHz STR needs no tuning on
  this chip (§4.2). But a hang at 80 would not say whether the clock or the
  sequence is wrong, so each 80 MHz stage has a 40 MHz image built beside it
  from the same commit, run only if 80 hangs: 40 passing means the timing
  at 80; 40 hanging too means the sequence.
- **Why not 120.** It needs a core clock of 120 MHz, which moves the PSRAM's
  divider (SO-102 §3.3), tuning, and the flash's high-performance mode,
  which on some chips is a status-register write (`spi_flash_init_chip_
  state`, `CONFIG_SPI_FLASH_HPM_ON`). Not now.

---

## 3. Every write, in order

### 3.1 ESP-IDF's sequence, and what is taken

ESP-IDF reaches QIO 80 on this chip in three places: the ROM's header path,
which sets the clock from the second-stage bootloader's image header before
the bootloader runs; the bootloader's `bootloader_init_spi_flash`; and the
application's start. Every step, in its order, and what the switch takes.
"V" is volatile: reset by a chip reset, and SPI0's words rewritten by the
ROM's attach at every boot (§8). "NV" is the flash chip's non-volatile
state.

```
step  register or command, field = value        source                taken
-- ROM: ets_config_flash_by_image_hdr (0x4004_559C), header byte 3 = 0xF ---
H1  SPI_MEM_CLOCK(0) 0x6000_3014 = 0x8000_0000  ROM esp_rom_spiflash_ yes, V,
    (CLK_EQU_SYSCLK, divide by 1)               config_clk(1, 0);     last
                                                IDF mspi_timing_ll_
                                                set_flash_clock(0, 1)
H2  SPI_MEM_CLOCK(1) 0x6000_2014, the same      config_clk(1, 1)      no: SPI1
                                                                      keeps
                                                                      divide 4
H3  the ROM's extra-dummy bytes (RAM) = 0       spi_dummy_len_fix     no: RAM;
                                                                      read 0
-- IDF bootloader_init_spi_flash (bootloader_flash_config_esp32s3.c) --------
B1  pads 30, 31, 32, 29, 27, 28 drive 1         bootloader_configure_ no: the
                                                spi_pins(1)           ROM's 2
B2  CTRL(0) 0x6000_3008 |= FDUMMY_OUT [3]       bootloader_flash_set_ CTRL(0):
    | Q_POL [18] | D_POL [19]; CTRL(1) too      dummy_out             yes, V
B3  USER(0) |= CS_HOLD [6] | CS_SETUP [7];      bootloader_flash_cs_  yes, V;
    CTRL2(0) 0x6000_3010 CS_HOLD_TIME [9:5]     timing_config, its    USER(0)
    = 0, CS_SETUP_TIME [4:0] = 0                non-octal branch      already
                                                                      has both
B4  flash: 7Ah, resume                          bootloader_spi_flash_ no
                                                resume
B5  flash: WREN, then 01h with two bytes        bootloader_flash_     no: NV
    clearing the block-protect bits, only if    unlock
    any is set
B6  flash: 35h; if bit 1 clear, WREN and 31h    bootloader_enable_    read: yes;
    with it set; read back                      qio_mode, its         write: no,
                                                enable_qio_mode,      NV (§3.3)
                                                default entry
B7  DDR(0) 0x6000_30E0 = 0, DDR(1) = 0          ROM config_readmode   no: DDR(0)
                                                0x4004_A820           checked 0
B8  CTRL(1), CTRL(0) &= 0xFE6F_9C0F             the same              CTRL(0):
                                                                      yes, V
B9  flash: if 35h bit 1 clear: CTRL(1) |=       ROM Enable_QMode      no: NV
    WRSR_2B, WREN, 01h with SR | 0x200          0x4004_A0D4, called
                                                for QIO and QOUT
B10 CTRL(1), CTRL(0) |= 0x0100_2000 (QIO:       ROM CSWTCH$195 at     CTRL(0):
    FREAD_QIO [24], FASTRD_MODE [13]) or        0x3FF1_E060           yes, V
    0x0080_2000 (DIO: FREAD_DIO [23])
B11 USER(0) 0x6000_3018 &= ~USR_MOSI [27],      ROM spi_cache_mode_   yes, V
    |= 0x7000_0000; USER1(0) 0x6000_301C        switch 0x4004_A190
    [31:26] = 23 (24-bit address), [5:0] =
    5 for QIO, 3 for DIO (6 or 4 cycles);
    USER2(0) 0x6000_3020 [15:0] = 0xEB or 0xBB
B12 QIO only: IO_MUX_GPIO27 0x6000_9070 and     ROM esp_rom_spiflash_ QIO: yes,
    GPIO28 0x6000_9074 MCU_SEL [14:12] = 0      select_qio_pins       V
    (SPIHD, SPIWP); GPIO_FUNC2_IN_SEL_CFG       0x4004_95C4, its
    0x6000_415C and FUNC3 0x6000_4160 bit 7     default-pin branch
    cleared (gpio_bypass_matrix_in)
B13 flash: 04h, WRDI                            bootloader_enable_wp  no
-- IDF application start (cpu_start.c) -------------------------------------
A1  esp_mspi_pin_init: the octal pins, then     flash_ops.c,          no: the
    drive 3 on 27, 28, 31 to 37 and the clock   mspi_timing_set_pin_  PSRAM's S1
    pad (mspi_timing_ll_set_all_pin_drive)      drive_strength        owns 27,
                                                                      28, 33-37
A2  spi_flash_init_chip_state: the flash's      flash_ops.c           no: only
    high-performance mode                                             >80 MHz
A3  mspi_timing_flash_tuning                    empty unless          nothing to
                                                MSPI_TIMING_FLASH_    take, §4.2
                                                NEEDS_TUNING
A4  with PSRAM: mspi_timing_enter_high_speed_   mspi_timing_tuning.c, no: the
    mode: CORE_CLK_SEL for the core clock, the  mspi_timing_config.c  core and
    flash and PSRAM dividers                                          its select
                                                                      stay
```

`spi_cache_mode_switch`'s QIO branch, read from [ROM]: USER(0) `&
0xF7FF_FFFF`, then `| 0x7000_0000`; USER1(0) `(& 0x03FF_FFFF) |
0x5C00_0000`, then `[5:0]` = the ROM's extra dummy for SPI0 (byte 24 of its
flash-driver state, 0 on the part) plus 5; USER2(0) `(& 0xFFFF_0000) |
0xEB`. Its DIO branch is the same with plus 3 and `0xBB`. `CSWTCH$195`'s six
words are `0x0100_2000` (QIO), `0x0010_2000` (QOUT), `0x0080_2000` (DIO),
`0x0000_6000` (DOUT), `0x0000_2000` (fast read) and 0 (slow read). The ROM's
`spi_common_set_dummy_output` calls `spi_set_dummy_output` on both
controllers for QIO and DIO alone; B2 is ESP-IDF's explicit form of it.

### 3.2 The switch's stores, in order

From IRAM (§6.1), with the clock key's switch already made:

```
#   address        store                                    QIO   DIO
S1  CTRL(0)        |= 0x000C_0008 (FDUMMY_OUT, Q_POL,       yes   yes
    0x6000_3008    D_POL)
S2  CTRL2(0)       [9:5] = 0, [4:0] = 0, only if either     yes   yes
    0x6000_3010    reads non-zero
S3  IO_MUX 27, 28  MCU_SEL [14:12] = 0                      yes   no
    0x6000_9070/74
S4  insel 2, 3     bit 7 cleared                            yes   no
    0x6000_415C/60
S5  CTRL(0)        (& 0xFE6F_9C0F) | 0x0100_2000 (QIO)      yes   yes
                   or 0x0080_2000 (DIO)
S6  USER(0)        (& 0xF7FF_FFFF) | 0x7000_0000            yes   yes
S7  USER1(0)       ((& 0x03FF_FFFF) | 0x5C00_0000) with     yes   yes
                   [5:0] = 5 (QIO) or 3 (DIO)
S8  USER2(0)       (& 0xFFFF_0000) | 0xEB (QIO) or 0xBB     yes   yes
S9  CLOCK(0)       = 0x8000_0000                            yes   yes
```

From the part's words (§1.1) these give, for DIO: CTRL `0x002C_0008` after
S1 and `0x00AC_2008` after S5, USER `0x7000_00C0`, USER1 `0x5C00_0003`,
USER2 `0x7000_00BB`, CLOCK `0x8000_0000`; for QIO: CTRL `0x012C_2008`, USER1
`0x5C00_0005`, USER2 `0x7000_00EB`, pads 27 and 28 `0x0000_0B00` (from
`0x1B00`), the rest as DIO. The part read USER(0) as `0x000000c0` after the
ROM's own boot stored `| 0x5000_0000` into it (`spi_cache_mode_switch`'s
slow-read branch), so SPI0 does not appear to keep USER's [31:27]; the
switch makes the ROM's store all the same, as ESP-IDF's ROM path does, and
the line prints what reads back.

**Why this order.** No flash read happens between the stores, so the order
is only what the trace holds. It is chosen so that each group leaves a
configuration ESP-IDF itself runs: the dummy output and chip-select timing
are ESP-IDF's in every mode; the read mode with the clock still at divide 4
is ESP-IDF's low-speed mode, which changes the clock and keeps the read mode
(`mspi_timing_enter_low_speed_mode`); and the clock is the one store that
makes it fast, made last. S1 precedes S5 for a reason of its own: in QIO
and DIO the mode bits M7..M0 follow the address on the data lines, and a
flash takes M5..M4 = 10b as "continuous read", after which it reads the
next command's first byte as address bits. With FDUMMY_OUT and both
polarities high the controller drives those lines high (`SPI_MEM_FDUMMY_OUT`,
"In the DUMMY phase the signal level of SPI bus is output by the SPI0
controller"), so M5..M4 is 11b; in QIO, M5 and M4 go out on IO1 and IO0,
which Q_POL and D_POL set. Without it they would float on the pads'
pull-ups (§8.1, item 3).

**After the stores**, the canary read (§6.2); on a mismatch the switch puts
CLOCK(0) back first, then USER2, USER1, USER, CTRL, the input selects, the
pads and CTRL2, each to the word it read before S1.

### 3.3 The QE bit

- **Needed for QIO only.** QE turns the flash's WP# and HOLD# pins into IO2
  and IO3. In QIO the controller drives and samples data on them; with QE
  clear the flash treats IO3 as HOLD#, and data low on it pauses the flash
  mid-read. DIO uses IO0 and IO1 alone.
- **Whether the part has it**: the probe's `sr2` bit 1. It may well be set:
  the factory image's bootloader ran before ours, and a QIO-built ESP-IDF
  bootloader sets QE on its first boot (`bootloader_enable_qio_mode`), but
  the banner shows only that the factory image's header said DIO. The
  probe decides; nothing here assumes.
- **Every boot checks it.** The `qio80` key reads `35h` before any store and
  stops with its line if bit 1 is clear (§6.3, 0x77). The kernel never
  writes it.
- **If it is clear, the one write** (stage F4, a harness only, built only
  after the owner's word):

  ```
  1. 05h, 35h: read SR1 and SR2; stop unless SR2 bit 1 is clear and SRP1
     (SR2 bit 0) and SRP0 (SR1 bit 7) are clear
  2. 06h: write enable; 05h until WEL (bit 1) reads set, bounded
  3. 01h with two bytes: SR1 as read, SR2 as read | 0x02
  4. 05h until WIP (bit 0) reads clear, bounded by SYSTIMER at 100 ms
  5. 05h, 35h: read back; SR1 must equal step 1's, SR2 step 1's | 0x02
  ```

  This is ESP-IDF's write for this chip. Its bootloader says so in
  `bootloader_flash_unlock`: "For common behaviour, like XMC chips, Use
  01H+2Bytes to write both SR1 and SR2", keeping QE; its application's
  generic driver sets QE with the same command (`spi_flash_chip_generic_
  set_io_mode`: `spi_flash_common_write_status_16b_wrsr`, QE bit 9 of the
  16-bit status); and the ROM's `Enable_QMode` sets `WRSR_2B` and writes
  `01h` with bit 9 too. ESP-IDF's bootloader table would try `31h` first for
  this ID, through its default entry, which names GigaDevice, FM and BY
  parts and not XMC. The harness is built only if the probe's QER is 001b,
  100b or 101b, each of which JESD216B defines as this two-byte write
  ([SFDP]), or the table is absent and the owner accepts ESP-IDF's XMC
  statement instead.
- **What makes it non-volatile and what that costs.** Status register 2 is
  kept through power cycles; that is the point of it. QE set is the state
  every QIO-flashed ESP32 module is left in, and it affects only what IO2
  and IO3 mean: the ROM's one-line boot and esptool's download mode do not
  use them. The ROM's own routine that clears the protection bits keeps
  it ("Clear all SR bits except QE bit", `esp_rom_spiflash_clear_bp`,
  rom/spi_flash.h). What esptool's flasher sends to the status register
  was not read; `qio80` reads QE at every boot, so a reflash that cleared
  it would show as the key's `not taken` line, not as a failure.
- **Writing it every boot instead** (the volatile status register, `50h`
  then `01h`) is not taken: whether this chip has one is in its datasheet,
  not read; ESP-IDF never uses it; and it would put a status-register write
  in every product boot, where the one-time write leaves every product
  image sending reads alone.

---

## 4. The shared core clock and the PSRAM

### 4.1 What moves

Only SPI0's flash divider: `SPI_MEM_CLOCK(0)` from divide by 4 to divide by
1. The core clock is `SPI_MEM_CORE_CLK_SEL` and the PLL; on the PLL at 480
MHz, 0 is 80 MHz (`spi_mem_reg.h`, `SPI_MEM_CORE_CLK_SEL`), and no key writes
it. So the flash goes to 80 MHz and nothing else does:

```
clock                 register          today        with a flash key
core                  CORE_CLK_SEL 0    80 MHz       80 MHz
flash, cache fills    CLOCK(0)          divide 4: 20 divide 1: 80
SPI1 commands         CLOCK(1)          divide 4: 20 divide 4: 20
PSRAM, cache fills    SRAM_CLK(0)       divide 4: 20 divide 4: 20
```

ESP-IDF's arithmetic agrees: with the PSRAM at 40 MHz or less no PSRAM
tuning is needed, and with the flash at 80 MHz STR no flash tuning, so the
core clock is 80 (`mspi_timing_tuning_configs.h`, the `#else` branch,
`MSPI_TIMING_CORE_CLOCK_MHZ 80`) and the flash divider 80 / 80 = 1
(`mspi_timing_config_set_flash_clock`). ESP-IDF's own N8R8 configuration
with the PSRAM at 40 MHz is this one.

**SO-102 §3.3's contract holds unchanged.** It forbids any write after
`memory_init` to `SYSCLK_CONF`, `CPU_PER_CONF` and `CORE_CLK_SEL`; the flash
key writes none of them, reads them, and runs in the clock seam before
`memory_init`. The PSRAM's divider was chosen for the core clock, which does
not move, and S0.3's comparison sees the same words. The runner's rule that
only the clock key's file and read-only probes name those three registers
gains the flash key's module as a reader. What does change for the PSRAM is
text: P1's line prints `spi0 clock=0x00030103`, which becomes
`0x80000000` in an image built with a flash key.

**Pads 27 and 28** are the flash's IO3 and IO2 and the octal PSRAM's D3 and
D2. The QIO key gives them function 0 and the direct input select (S3, S4);
the PSRAM's S1 then gives them function 0, the pull-up, input and drive 3
(ABI.md §16c), the same function with ESP-IDF's PSRAM drive. Either order is
consistent, and the flash key runs first.

**Only the PLL keys.** On the crystal the core clock is one of SO-102's
three readings, and at divide 1 the flash would run at 40, 80 or 20 MHz.
The flash keys are refused at build time with the `rom` and `xtal40` clock
keys (§6.3), and the switch compares the clock words with its description
before any store.

### 4.2 Timing tuning

Not needed, by ESP-IDF's rule for this chip: a flash in STR mode is tuned
only above 80 MHz (`MSPI_TIMING_FLASH_NEEDS_TUNING (MSPI_TIMING_FLASH_MODULE_
CLOCK > 80)`, `mspi_timing_tuning_configs.h`), "Module with speed lower than
120M SDR doesn't need to be tuned" (the same file), and the extra dummy
for a flash on the IO MUX at 80 MHz is 0 (`FLASH_IO_MATRIX_DUMMY_80M 0`,
`bootloader_flash_config_esp32s3.c`). So `TIMING_CALI`, `DIN_MODE` and
`DIN_NUM` stay 0, which the description holds them to.

Had it been needed, it would still write nothing non-volatile:
`mspi_timing_flash_tuning` reads 64 bytes at the bootloader's offset at 20
MHz as reference, then re-reads them under each candidate input delay and
dummy (`MSPI_TIMING_TUNING_MSPI_DIN_DUMMY`), setting only SPI0's and SPI1's
`DIN_MODE`, `DIN_NUM` and `TIMING_CALI`.

The ESP-IDF application freezes the caches around a speed change
(`mspi_timing_change_speed_mode_cache_safe`, `cache_hal_freeze`), because
another core or an interrupt could fetch meanwhile. ESP-IDF's bootloader
makes the same stores (B2 to B12) from IRAM with the cache running and no
freeze. The kernel runs one core with interrupts masked, and the switch runs
from IRAM on DRAM data, as the bootloader does; the probe's autoload words
show that nothing fetches on its own (§6.1).

---

## 5. The image header

- **What `sosimage flash` writes.** No header. Flash bytes 0 to 0x3FF are
  0xFF, the direct-boot magic is at 0x400 and 0x404, and the first
  instruction at 0x408 (`esp32s3.ld`, ABI.md §2a). The builder refuses an
  image whose byte 0 is 0xE9 (`flash.saw`, `ESPTOOL_HEADER_BYTE`), and its
  `esptool` line is `write-flash 0x0 FILE` with no mode or speed option
  (`board_image.saw`).
- **What the ROM does on this path.** `main` calls `spi_flash_boot_attach`
  with 0 as its second and third arguments (0x4004_3AAF to 0x4004_3AB3), so
  it runs `SPI_init(5, 4)` and `esp_rom_spiflash_config_readmode(5, 0)`:
  slow read, divide by 4, and, because the second argument is 0, no call of
  `Disable_QMode`. Then `ets_run_flash_bootloader` tests byte 0, finds it
  is not 0xE9, and takes the direct boot (ABI.md §2a). The only call of
  `ets_config_flash_by_image_hdr` (0x4004_58A7) is on the 0xE9 branch, so
  the ROM never reads a mode or a speed from our image.
- **What it would do with a header**, for the record and for §8:
  `ets_config_flash_by_image_hdr` takes byte 2 as the mode (above 10 it
  prints and spins), byte 3's low nibble as the speed (0, 1, 2 give divide
  2, 3, 4; 3 and above divide 1: `ESP_IMAGE_SPI_SPEED_DIV_1` is 0xF), calls
  `esp_rom_spiflash_config_clk` on both controllers, and then
  `esp_rom_spiflash_config_readmode(mode, ...)`, which for QIO and QOUT
  calls `Enable_QMode`: a non-volatile QE write from the ROM when QE is
  clear.
- **What esptool does.** `write-flash` changes a header only at the
  bootloader offset, only when some mode, speed or size option is not
  `keep` (the default), and only when byte 0 is the image magic; otherwise
  it prints "doesn't look like an image file, so not changing any flash
  settings" ([ESPTOOL] `_update_image_flash_params`). Our image is left as
  built.
- **Whether they change.** No. The speed comes from the flash key, not a
  header, and an esptool header would forfeit the direct boot.

---

## 6. Where it runs, its stops, the emulator, the smoke

### 6.1 Where

- **The key.** A new keyed module, `s3flash` (`keys/flash-*`), values `rom`
  (the default: no code, the image the bytes it is today), `dio80`,
  `qio80`, and `dio40`, `qio40` for diagnosis. `board.toml` gains
  `[keys.flash]`; a product chooses it in its description's `[keys]`; the
  runner takes a case's `"flash"` and `--emit-flash CASE --flash KEY`, as it
  takes `"clock"` and `--clock` (ABI.md §14d).
- **The seam.** The HAL's `clock_init`, which `kmain` calls first, becomes
  the clock key's `clock_init` followed by the flash key's `flash_init`, so
  the flash switch comes after the CPU's and before anything else reads
  flash for long, and before `memory_init` (SO-102 §3.3).
- **The code.** The checks and stores are one C call, `sos_flash_switch`,
  and the probe's reads one more, `sos_flash_read`, both in `kernel/mspi.c`
  beside the PSRAM's layer and under its rules: every function in
  `.text.iram`, no `const` data, no jump table, no call out of the file,
  every register read through `rd` (so the fault build reaches every stop),
  and `check_iram` covering them. They share the PSRAM layer's command
  primitive with the chip select made a parameter compared, never written:
  the PSRAM entries require MISC `0b01` (which their S6 writes) and the
  flash entries require `0b10` (which nothing writes). The canary and the
  timing regions are in `.drom`, read through the data window.
- **Why IRAM.** From S1 to S9 the flash is not readable in any one mode:
  the CTRL, USER and CLOCK words disagree between stores. The kernel's code
  is in flash (`esp32s3.ld`), so every instruction of the call and every
  literal must be in IRAM, and every datum in DRAM. Interrupts are masked
  in the kernel (ABI.md §7f), and the part has one running core (ABI.md
  §11).
- **No cache fetch on its own.** `EXTMEM_ICACHE_AUTOLOAD_CTRL_REG`
  (0x600C_40A0) and `EXTMEM_DCACHE_AUTOLOAD_CTRL_REG` (0x600C_404C) bit 2,
  AUTOLOAD_ENA, reset 0 (`extmem_reg.h`); the switch reads both and stops if
  either is set. The last flash fetch before the stores is the call itself,
  complete before it executes.
- **IRAM's room.** IRAM holds the vectors, the trap path, the clock store,
  the PLL bring-up, the windowed-call stub and the PSRAM layer in 16 KiB
  (`esp32s3.ld`); the link fails if the switch does not fit, and the unit
  reports the size the link map prints.

### 6.2 The canary and the timed reads

- **The canary**: 64 bytes in a section of their own in `.drom`, aligned to
  64 and read by nothing else, so no line of it is in the data cache before
  the switch. Their pattern toggles each data line from byte to byte
  (`0x55`, `0xAA`, `0x00`, `0xFF`, `0x0F`, `0xF0`, `0x33`, `0xCC`, then a
  count), and the IRAM code regenerates it by formula rather than from a
  table, which `check_iram` forbids. After S9 the switch reads the 64 bytes
  through the data window and compares. Wrong data, a wrong mode or a
  HOLD#-paused flash all read as a mismatch: the cache loads whatever the
  lines carry and does not hang on it. On a mismatch the switch puts SPI0
  back (§3.2), writes its short line to UART0 from IRAM, and returns; the
  canary's lines stay in the data cache with the wrong bytes, and nothing
  else reads them.
- **The timed reads**, for the record: 4 KiB at 16-byte stride from one
  never-read region before S1, and from a second after the canary, each
  bracketed by SYSTIMER (which the clock key has started), printed in
  microseconds. 256 line fills: about 2 ms in slow read at 20 MHz, about
  0.3 ms in DIO 80, about 0.2 ms in QIO 80 by §2's table, plus overheads.
  Printed, never asserted (CLAUDE.md, host time).

### 6.3 The description, the checks and the stops

The switch compares before its first store, as the PSRAM's S0 does, against
a description of eight words in DRAM: the three clock words masked as S0.3
masks them; the JEDEC ID; SPI0's ROM-left CLOCK, CTRL, USER1 and USER2;
and whether DDR, TIMING_CALI, DIN_MODE and DIN_NUM must read 0. The part's
description carries the part's words (§1.1, completed by F0) and the
PLL keys' clock words (ABI.md §16b); the emulator's carries its own, which
read differently (§6.4). As for the PSRAM's (ABI.md §16a), a board image
carries the part's and an emulator image the emulator's; for a product, the
part's live in the board description and `sosimage` writes the emulator's
for `run` and `test`, the part's for `flash`.

**Build time.** Each clock key states the core clock it leaves as a
constant (`MSPI_CORE_MHZ`: 0 for `rom` and `xtal40`, which do not know it,
80 for the PLL keys), and every flash key but `rom` asserts it is 80 with a
`static_assert`, so a flash key with `rom` or `xtal40` fails to compile with
the assertion's text.

**The stops**, in the order the call makes them. A "not taken" line ends
the switch before any store and the boot goes on at the ROM's 20 MHz slow
read, as the clock keys' do (ABI.md §14d):

```
0x79 entry  SOS-S3: flash KEY not taken: the description at 0x........ or
            the record at 0x........ is not in DRAM
0x70 clock  ... the memory-SPI core clock is not the one the key is for:
            SYSCLK_CONF&0x80fff=0x........ CPU_PER_CONF&7=0x.
            CORE_CLK_SEL=0x., the description says 0x........ 0x. 0x.
0x71 SPI0   ... SPI0 is not as the ROM leaves it: clock=0x........
            ctrl=0x........ user1=0x........ user2=0x........ ddr=0x........
            cali=0x........ din_mode=0x........ din_num=0x........, the
            description says ...
0x72 cache  ... a cache autoload is on: icache=0x........ dcache=0x........
0x73 gate   ... SPI1 MISC=0x........, the flash's commands need chip select
            0 alone (0x00000002); nothing was sent
0x74 busy   ... SPI1 busy before the first command, SPI_MEM_CMD=0x........
            after 100000 polls; SPI1 put back
0x76 id     ... the flash's JEDEC ID is 0x00......, the description says
            0x00204017; SPI1 put back
0x77 QE     ... status register 2 is 0x.., QE (bit 1) clear; QIO needs it
            set and this kernel never writes it; SPI1 put back
```

After the stores:

```
0x78 canary SOS-S3: flash KEY: canary mismatch           (from IRAM)
            SOS-S3: flash KEY not taken: the canary read 0x........ at +N
            where 0x........ was expected; SPI0 put back to the ROM's words
```

And one that halts, as the PSRAM's 0x59 does:

```
0x75 stuck  SOS-S3: flash: SPI1 command 0x.. did not finish   (from IRAM)
            ... SPI1 command 0x.. did not finish, SPI_MEM_CMD=0x........
            after 100000 polls; halted
```

A read command that never finishes leaves SPI1 mid-transfer with chip
select 0 low; an SPI0 fetch to resume Saw code would share the bus with it,
so the call writes its short line from IRAM and the caller halts. A reset
clears it.

**The line** when taken (one line):

```
SOS-S3: flash KEY jedec=0x00204017 sr2=0x.. clock=0x00030103->0x80000000
ctrl=0x00200000->0x........ ctrl2=0x........->0x........
user=0x000000c0->0x........ user1=0x5c000007->0x........
user2=0x70000003->0x........ canary=ok read4k=<v>us-><v>us
```

The words after `->` are read back after the stores; the part's are the
board stage's expected text (§7), and the emulator reads 0 for all of them.

### 6.4 The emulator

What the installed emulator (espressif/qemu `esp-develop-9.2.2-20250817`)
does, by its source:

- **The flash chip** is QEMU's `m25p80` model on SPI1's bus, chosen by the
  image's size (`esp32s3_init_spi_flash`): a 4 MiB image, which a smoke
  image is (ABI.md §1e), gets `gd25q32`, JEDEC `0xC84016`, a GigaDevice
  part. It answers `9Fh` with that ID and `05h` with its modelled bits (0
  at boot). It does not answer `35h` for a GigaDevice part (`decode_new_cmd`'s
  `RDCR_EQIO` case has arms for Spansion, Macronix and Winbond only), so
  status register 2 reads 0; `15h` returns its volatile configuration byte;
  and `5Ah` falls through to "Unknown cmd" and reads 0, since `gd25q32` has
  no `sfdp_read`. Every command it decodes appears in its
  `m25p80_command_decoded` trace, the one the PSRAM cases already hold.
- **SPI0 is not modelled**: its registers read 0 and stores are dropped
  (ABI.md §3, §14e), as are the IO MUX's.
- **The cache reads the image file directly** (`esp32s3_cache.c`:
  `blk_pread(s->flash_blk, ...)` on a miss), whatever SPI0 holds. So under
  the emulator a switch, right or wrong, changes nothing: the canary always
  matches and the boot goes on.

So the emulator holds what the switch decides and what it stores, not
whether it works:

- the probe's transcript whole, and its flash trace exactly `0xab` (the
  ROM's) and then the probe's reads, nothing else;
- every store of §3.2 by the device-write trace (`expect_stores`, in order,
  with their values) and `device_untouched` for CORE_CLK_SEL, CLOCK(1),
  SRAM_CLK(0), MISC(1) and every pad but 27 and 28;
- each stop, by a description that differs or by the fault build feeding
  the read the stop is about.

### 6.5 The smoke's cases

```
case                          how                                   holds
s3_flash_probe                F0 under the emulator                 lines; the
                                                                    flash trace
boot_flash_dio80              pll80, the emulator's description     S1, S2,
                                                                    S5-S9 in
                                                                    order
boot_flash_qio80_qe_clear     qio80; SR2 reads 0 here               0x77; no
                                                                    SPI0 store
boot_flash_qio80              fault build: SR2 read as 0x02         S1-S9
boot_flash_part_words_dio80   fault build: SPI0 reads the part's    the part's
boot_flash_part_words_qio80   words, the part's description         words (§3.2)
flash_stop_description        description at 0x3c200000             0x79
flash_stop_clock              described CORE_CLK_SEL 1              0x70
flash_stop_spi0               SPI0's CTRL read with FASTRD set      0x71
flash_stop_autoload           ICACHE_AUTOLOAD_CTRL read with ENA    0x72
flash_stop_gate               MISC(1) read as 0b01                  0x73
flash_stop_busy               SPI_MEM_CMD(1) busy before            0x74
flash_stop_not_finished       busy after the ID command             0x75, halt
flash_stop_identity           the emulator's ID, the part's         0x76
                              description
flash_stop_canary             the comparison fed a wrong word       0x78; the
                                                                    put-back
                                                                    stores
flash_key_at_rom_refused      dio80 with the rom clock key          build error
kill_churn_flash_dio80        kill_churn at pll80, dio80            as kill_
                                                                    churn
```

Every check needs a case that goes red when the check is removed or
inverted. The mutants, each run alone: the dummy output store dropped or
moved after S5; the clock store moved first; CLOCK written `0x0000_0000`
(an encoding of divide 1 that is not ESP-IDF's); the QE check removed
(`boot_flash_qio80_qe_clear` then sees SPI0 stores); a write opcode (`01h`,
`06h`, `31h`, `50h`, `B9h`, `79h`) added to the probe's reads (the flash
trace); a store to MISC(1) added to a flash entry (`device_untouched`);
a flash function out of `.text.iram` (`check_iram`); a call of
`esp_rom_spiflash_config_readmode` added (the reference check below).

**A runner check, new**: a kernel image may not reference the ROM's routines
that write the flash's status register or array, by address or through
their jump-table entries: `esp_rom_spiflash_config_readmode` (0x4004_A820,
`__call_` 0x4000_0AA4), `Enable_QMode`, `Disable_QMode`,
`esp_rom_spiflash_write_status` (0x4004_9C40), `esp_rom_spiflash_unlock`
and `_esp_rom_spiflash_unlock`, and the ROM's write and erase entries; the
unit takes the full list from `esp32s3.rom.ld`. `config_readmode` is on it
because its QIO path calls `Enable_QMode` and its DIO path, with a non-zero
second argument, `Disable_QMode`: both are status-register writes.

---

## 7. Board stages

Every stage: an image built with `--emit-flash`, sent to the reviewer with
its SHA-256 and the expected text; the only write is `write-flash 0x0`; no
eFuse burn or write, no erase. F4 alone sends a write command to the flash
chip, and is built only after the owner's word. Images for one comparison
are built from one commit and run one after another, since `kill_churn`
moves by a few percent between trees and days (ABI.md §14h).

Preconditions: SO-132.p2's `pll240` has passed on the part, and SO-102's
P2 at `pll80` has read a core clock of 80 MHz. If P2 reads anything else,
§4's premise is wrong and the series does not start.

**F0, `s3_flash_probe` at `rom`.** Expected: §1.2's lines with `jedec=
0x00204017`, `flash_type=0`, SPI0's words as §1.1 has them where §15c read
them, the SPI1 line `as before them`, `halt pass`. It decides: whether QIO
needs F4 (`sr2` bit 1); the description's remaining words (CTRL2,
TIMING_CALI, DIN_*); the input selects 2 and 3; that the chip claims
`BBh` with 4 wait clocks and `EBh` with 6, and how QE is set. A JEDEC ID
other than `0x00204017`, a QER outside 001b, 100b and 101b, SRP0 or SRP1
set, or SPI0 words other than §15c's end the series until understood.

**F1, `boot_flash_dio80` at `pll80`.** The kernel alone with the key:
`pll80`'s line, then

```
SOS-S3: flash dio80 jedec=0x00204017 sr2=0x.. clock=0x00030103->0x80000000
ctrl=0x00200000->0x00ac2008 ctrl2=0x........->0x........
user=0x000000c0->0x........ user1=0x5c000007->0x5c000003
user2=0x70000003->0x700000bb canary=ok read4k=<v>us-><v>us
```

then the banner and the refusal of an empty image, as `boot_pll80`. It
decides that DIO 80 reads the flash (`canary=ok`, and every line after it
is printed by code fetched at 80 MHz). A hang after `pll80`'s line, or the
canary's line: run `boot_flash_dio40`, built beside it; 40 passing puts the
fault in timing at 80 (drive or input delay, a design question), 40 hanging
too puts it in the sequence.

**F2, `boot_flash_qio80` at `pll80`**, only if F0 read QE set. As F1 with
`qio80`, `ctrl=...->0x012c2008`, `user1=...->0x5c000005`, `user2=...->
0x700000eb`, and `boot_flash_qio40` beside it.

**F3, the measure, at `pll240`.** `kill_churn` and `kill_churn_profile`,
each at the `rom` flash key (the control: SO-132's images, rebuilt from
this commit), at `dio80`, and at `qio80` if F2 passed. Expected text:
`kill_churn`'s own, the clock line, the flash line, `200 rounds, every
create succeeded`. Recorded: the time from reset to `200 rounds` for each,
and the profile's seven phases with the console's stall. ABI.md §14h found
that settling and killing hardly moved from 80 to 160 MHz (0.98, 0.97);
those are the phases to watch.

**F4, `flash_qe_write`**, only if F0 read QE clear, the owner has approved
it, and QIO is wanted after F3's DIO numbers. Its lines:

```
SOS flashqe: before sr1=0x.. sr2=0x.. (QE clear, SRP0 and SRP1 clear)
SOS flashqe: wrote 01h sr1=0x.. sr2=0x.. after WREN, WEL set
SOS flashqe: after sr1=0x.. sr2=0x.. QE set, WIP clear after <v> us
SOS-S3: halt pass
```

Then F0 again after a power cycle (USB unplugged), which shows QE held,
then F2 and F3's QIO rows.

```
stage  image                    key           writes to the flash chip
F0     s3_flash_probe           rom           none (reads)
F1     boot_flash_dio80 (40)    pll80, dio80  none (reads)
F2     boot_flash_qio80 (40)    pll80, qio80  none (reads)
F3     kill_churn(_profile)     pll240, rom,  none (reads)
                                dio80, qio80
F4     flash_qe_write           rom           one status write, if approved
```

---

## 8. An attack on this design

### 8.1 Ways the part could be left unable to boot our image or be flashed

Each: how it could happen, how likely, what prevents it, and the way back.

1. **A write command reaches the flash.** A status write could set block
   protection, which makes `write-flash` fail until something clears it,
   or SRP1 and SRP0 together, which on many parts lock the status register
   for good; an erase or program would destroy our image. Prevented: the
   flash entries send only `9Fh`, `05h`, `35h`, `15h`, `5Ah`; `06h` is
   never sent, so the write-enable latch stays clear and the chip ignores a
   write opcode sent by mistake; no built-in command bit of `SPI_MEM_CMD(1)`
   is written; the flash trace in every flash case lists exactly what
   reaches the chip, and the mutants above add each opcode. F4 is the only
   exception and checks SRP0 and SRP1 before writing (below). Very low.
2. **A power-down command.** `B9h` makes the flash stop answering until
   `ABh`, which the ROM sends at every boot (the flash trace's `0xab`), so a
   reset undoes it; XMC's `79h` ultra-deep power-down needs `FFh` and 2 ms
   to leave (`bootloader_flash_xmc_startup`), which the ROM does not send,
   so it would survive every reset but a power cycle and make the flash
   unreadable to download mode as well. Never sent; the trace holds it.
   Way back: a power cycle.
3. **The flash left in continuous-read mode.** A `BBh` or `EBh` read whose
   mode bits M5..M4 are 10b makes the next transfer's first byte an
   address; the ROM's `ABh` and `03h` at the next reset, and esptool's
   commands in download mode, would then be misread, and only a power cycle
   clears it. Prevented by S1, the dummy output with both polarities high,
   made before the read mode, which keeps M5..M4 at 11b as ESP-IDF's
   bootloader does (§3.2); the trace holds S1 before S5, and a mutant that
   drops it or moves it after S5 goes red. Low. Way back: a power cycle.
4. **QIO with QE clear.** The flash pauses on HOLD#, reads return garbage.
   Prevented by the `35h` check at every boot (0x77) before any store; if
   it slipped, the canary catches it and puts SPI0 back. Nothing
   non-volatile is involved; a reset restores SPI0.
5. **A divider or mode the flash cannot follow.** At 80 MHz without tuning,
   or with the pads' drive too weak, reads come back wrong: the canary
   catches it and puts SPI0 back; if a fill wedges instead, the part looks
   hung after the key's first line. A chip reset (the button, or esptool's
   EN reset) runs the ROM's attach, which rewrites CLOCK(0) and SPI0's read
   mode (`SPI_init`, `config_readmode(5, 0)`). The 40 MHz image tells the
   cause.
6. **A core clock other than 80 MHz** (a flash key with a crystal key, or a
   later key raising CORE_CLK_SEL), so divide 1 runs the flash at 120 to
   240 MHz. Prevented at build time by the `static_assert` and at boot by
   the clock words against the description (0x70). Way back: a reset.
7. **The switch partly in flash**, so SPI0 fetches through a half-written
   configuration. Caught by `check_iram` and its mutants at build time.
8. **A ROM routine with a hidden write.** The ROM's `config_readmode` calls
   `Enable_QMode` for QIO and `Disable_QMode` for DIO with a non-zero
   second argument; both write the status register. The kernel never calls
   them; the runner refuses an image that references them (§6.5).
9. **An image header after all.** An image with 0xE9 at byte 0 and QIO in
   byte 2 would have the ROM write QE itself on its next boot (§5).
   `sosimage` refuses 0xE9 at byte 0, and esptool does not add a header.
10. **F4's write going wrong.** A power loss during the write (milliseconds)
    could leave the status register corrupt; a wrong second byte could set
    SRP1, or a lock bit if one read 0 and was written 1. Prevented: the
    write is the register as read with bit 1 set, so no bit that read 0 is
    written 1 but QE; it is refused if SRP0 or SRP1 read set, or if QER is
    not one of the three two-byte codes; WIP is waited for with a bound;
    the read-back must match. Low. Way back if it fails: the owner's
    decision, since repairing a status register is itself a status write.
11. **A CPU-only reset** (a TIMG watchdog's; all are off, ABI.md §2e) leaves
    SPI0 at 80 MHz. The ROM's attach rewrites CLOCK(0), CTRL's read-mode
    bits and USER1 and USER2 for slow read at every boot, and keeps
    FDUMMY_OUT and the polarities (its mask `0xFE6F_9C0F` does not clear
    bits 3, 18, 19), which slow read ignores. It boots.
12. **Download mode lost.** Needs an eFuse, a protected or damaged flash, or
    a flash left unable to answer (items 1 to 3). QE set does not affect
    it: download mode and the ROM's boot read on one line, and every
    QIO-flashed module carries QE set.

### 8.2 What a process can do that it could not before

Nothing new. On the flat tier a process can already write SPI0, SPI1 and the
IO MUX (ABI.md §5e; SO-102 §8.2), including erasing the flash. The flash
key only changes SPI0's configuration once, before any process exists.

### 8.3 With the PSRAM

The PSRAM's command layer saves and restores the SPI1 words it uses and
never selects chip select 0; the flash entries never select chip select 1.
Neither writes the other's SPI0 side: the flash key writes SPI0's flash
words, the PSRAM's S2 and S4 SPI0's PSRAM words. Pads 27 and 28 are written
by both, to the same function (§4.1). In QIO the flash drives IO2 and IO3
only while chip select 0 is low, and the octal PSRAM drives D2 and D3 only
while chip select 1 is; the controller selects one at a time.

---

## 9. Units of work and questions

### 9.1 Units

```
unit  what                                                  size  model
U1    the flash's command reads and the probe: the          M     Opus
      chip-select parameter in mspi.c's primitive
      (compared, never written for the flash),
      `sos_flash_read`, the fault build's reach,
      `tests/xtensa/s3_flash_probe.saw`, the smoke case
      and its flash trace, `check_iram` over the new
      functions, the ROM-reference check, ABI.md §17a-c
U2    the key: `s3flash` (`rom`, `dio80`, `qio80`,          L     Opus
      `dio40`, `qio40`), `sos_flash_switch` (§3.2, §6.2,
      §6.3), `MSPI_CORE_MHZ` in the clock keys and the
      `static_assert`, the description (runner and
      `sosimage` by destination), `--flash` and the
      case key, `board.toml`'s `[keys.flash]`, §6.5's
      cases and mutants, ABI.md §17d-g
U3    the board stages F0 to F3 and ABI.md §17h's           S     lead,
      numbers                                                     reviewer
U4    `flash_qe_write` (§3.3, F4), only on the owner's      S     Opus
      approval after F0 and F3
```

Order: U1, then F0, then U2 with F0's words in its description, then F1 to
F3, then the owner's ruling, then U4 and F4 if it says so. U1 and U2 do
not overlap: U2's switch uses U1's command path for its ID and QE reads.

Both main units are Opus: U1 is the first code to send commands with the
flash's chip select enabled, and U2 decides what the part's flash path is
at every boot; the judgment in each is what keeps a write off the chip.

### 9.2 Questions for the owner

1. **If F0 reads QE clear: one non-volatile QE write, DIO only, or a
   volatile write at every boot?** Recommended: measure DIO 80 first (F3);
   then, if QIO's gain is wanted, approve the single write as its own
   harness stage (F4): it is the write every ESP-IDF QIO image makes on its
   first boot, made ESP-IDF's way for this chip, and after it no product
   image ever sends a command that writes the flash. Not the volatile
   per-boot write, which would put a status write in every boot. If F0
   reads QE set, the question does not arise.
2. **The default.** Recommended: `rom` stays the flash key's default, as for
   the clock, and a product opts in with `[keys] flash = "dio80"` or
   `"qio80"`; the build refuses either without a PLL clock key.
3. **The 40 MHz images.** Recommended: build them beside each 80 MHz stage
   and run them only if 80 hangs, so a failure costs one more run, not a
   rebuild and a round trip.
4. **A cross-check with esptool's read-only commands** (`read-flash-status`,
   `read-flash-sfdp`), outside the standing `write-flash` approval.
   Recommended: no; F0 reads the same with our own image, and adds what the
   ROM leaves in SPI0. Ask only if F0's reads disagree with `flash-id`.

---

## 10. Alternatives, what was checked, what was not

### 10.1 Alternatives rejected

- **Calling the ROM's `esp_rom_spiflash_config_readmode`.** It makes the
  same SPI0 stores, but it writes QE itself for QIO and clears it for DIO
  with a non-zero second argument (§3.1, B9), writes SPI1's CTRL and DDR,
  and is a windowed call. One wrong argument would be a non-volatile write.
- **Raising the divider alone.** `03h` is speed-limited (§1.1).
- **A header-configured boot.** Forfeits the direct boot, and the ROM's
  header path writes QE on its own (§5).
- **Drive 3 on the flash's pads now**, as ESP-IDF's PSRAM-board application
  sets. The ROM's 2 lies between ESP-IDF's bootloader (1, at the same 80
  MHz) and that (3), and the PSRAM's S1 already moves 27 and 28. Changing
  it would add a variable to the first 80 MHz stage; the 40 MHz image tells
  whether drive needs a look.
- **Freezing the caches** around the stores, as ESP-IDF's application does
  through ROM functions it patches (`esp_rom_cache_esp32s2_esp32s3.c`).
  ESP-IDF's bootloader makes the same stores without; one core, interrupts
  masked, IRAM and DRAM only, autoload read off.
- **120 MHz** (§2).

### 10.2 What I verified

- The ROM's direct-boot attach passes 0 as `config_readmode`'s second
  argument, so it never calls `Disable_QMode` (0x4004_3AAF, 0x4004_A905);
  `config_readmode`'s QIO path calls `Enable_QMode`, which reads `35h`,
  tests bit 9 of the 16-bit status (`0x200`) and writes `01h` with two
  bytes after setting `WRSR_2B`; `spi_cache_mode_switch`'s QIO and DIO
  words; `CSWTCH$195`'s six words; `config_clk`'s divide-1 word
  `0x8000_0000`; `spi_dummy_len_fix` stores 0; `select_qio_pins`'s pad and
  input-select stores; the header routine's speed decoding.
- ESP-IDF's QE table, unlock, generic driver, tuning rules, core-clock
  selection and pad drives, as cited.
- The emulator's flash model's answers and the cache's direct reads, from
  its source.
- esptool 5.1.0's header rule, from its source.

I did not run the emulator: every emulator fact here is from its source,
and U1's first run records the probe's transcript and trace.

### 10.3 What I could not check

- **The chip's datasheet**: its maximum clock for `BBh` and `EBh` at 3.3 V,
  its tSHSL against ESP-IDF's chip-select timing, whether it has a volatile
  status register (`50h`), what its status register 3 holds, and its own
  description of `01h` with two bytes. F0's SFDP read is the chip's own
  statement of the read modes and QER, and ESP-IDF's XMC handling is the
  rest.
- **JESD216 itself**: the QER and read-settings layouts are as Linux's
  `sfdp.h` quotes them from rev B.
- **Which of USER(0)'s bits SPI0 keeps.** The part read `0x000000c0` after
  the ROM's slow-read store of `| 0x5000_0000`; the TRM would say. The
  switch writes the ROM's words and prints what reads back.
- **The core clock under the crystal keys** (SO-102 H1 to H3), and so
  whether the factory banner's `clock div:1` was 80 MHz. P2 settles it; the
  design does not depend on it, since it requires a PLL key.
- **The module's marking**, never recorded.
