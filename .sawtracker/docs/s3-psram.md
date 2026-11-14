# SO-102: the ESP32-S3's PSRAM (design)

For the owner and the adversarial reviewer, before anything is built.
Written against main at `891909e2`. Nothing in the repository was changed.

It takes in the owner's ruling of Nov 14 on the fallback: a board described
as having PSRAM brings it up or stops, and the part must match its
description exactly. Section 5 is written to that ruling and replaces the
fallback the brief first asked for.

Sources, all public, read for this design:

- **[IDF]** ESP-IDF tag `v5.4.1`, fetched from github.com/espressif/esp-idf:
  `components/esp_psram/esp32s3/esp_psram_impl_octal.c`,
  `components/esp_psram/esp_psram.c`, `components/esp_psram/linker.lf`,
  `components/esp_psram/esp32s3/Kconfig.spiram`,
  `components/esp_hw_support/mspi_timing_tuning.c`,
  `components/esp_hw_support/port/esp32s3/mspi_timing_config.c` and
  `mspi_timing_tuning_configs.h`,
  `components/esp_hw_support/port/esp32s3/rtc_init.c`,
  `components/hal/esp32s3/include/hal/` `mmu_ll.h`, `cache_ll.h` and
  `mspi_timing_tuning_ll.h`,
  `components/soc/esp32s3/include/soc/{ext_mem_defs,soc,spi_pins}.h`,
  `components/soc/esp32s3/register/soc/{spi_mem,extmem,io_mux,efuse,syscon,
  rtc_cntl,system,reg_base}_reg.h`, `components/efuse/esp32s3/
  esp_efuse_table.csv`, `components/esp_rom/esp32s3/include/esp32s3/rom/
  opi_flash.h`, `components/esp_rom/include/esp_rom_spiflash.h`,
  `components/esp_system/port/cpu_start.c`,
  `components/esp_system/ld/esp32s3/memory.ld.in`,
  `components/spi_flash/flash_ops.c`.
- **[ROM]** The mask ROM's ELF, `~/.espressif/tools/esp-rom-elfs/20241011/
  esp32s3_rev0_rom.elf`, disassembled here with the installed
  `xtensa-esp32s3-elf-objdump`: `esp_rom_opiflash_exec_cmd` (0x4004_E058),
  `esp_rom_spi_cmd_start` (0x4004_E0D0), `esp_rom_spi_cmd_config`
  (0x4004_DD9C), `esp_rom_spi_set_op_mode` (0x4004_DBF4) and its jump table
  at 0x3FF1_90EC, `esp_rom_spi_reset_rw_mode` (0x4004_DB94),
  `esp_rom_opiflash_backup_reg`/`recover_reg`, `esp_rom_spi_set_dtr_swap_mode`
  (0x4004_E1A0), `esp_rom_opiflash_pin_config` (0x4004_D3CC) with its tables
  at 0x3FF1_90A8, and the GPIO pad helpers `gpio_pad_pullup`,
  `gpio_pad_input_enable`, `gpio_pad_unhold`, `gpio_bypass_matrix_in`.
- **[QEMU]** espressif/qemu at tag `esp-develop-9.2.2-20250817` (the
  installed emulator's build): `hw/xtensa/esp32s3.c`,
  `hw/misc/esp32s3_cache.c`, `hw/misc/ssi_psram.c`, `hw/ssi/esp32s3_spi.c`,
  `hw/nvram/esp_efuse.c`.
- **[RUN]** My own runs of the installed emulator (section 6).
- **[TREE]** This repository.

Not read: the ESP32-S3 Technical Reference Manual, and the AP Memory
datasheet for the octal PSRAM. Where a fact would come from them, the text
says so.

---

## 0. Verdict

1. **The bring-up is small and every write in it is volatile.** About forty
   register writes and five bus commands, all to the memory-SPI controller,
   the IO MUX, the cache's MMU table and the RAM chip's mode register. Each
   value is taken from ESP-IDF's octal driver or the ROM's own code (§2).
2. **The flash is never addressed.** Every command goes out on SPI1 with chip
   select 0 disabled in `SPI_MEM_MISC_REG(1)`, read back before the command
   starts; no flash-side register of SPI0 is written, and no IO MUX pad the
   flash uses is touched. A mutant that leaves CS0 on is caught by the
   emulator's flash trace (§6.4). Nothing writes an eFuse, an RTC register,
   or any non-volatile state of either chip; a chip reset undoes everything
   (§2.5).
3. **No tuning, no clock change.** The RAM runs at MSPI core / 4 (20 MHz if
   the core clock is 80 MHz, 10 MHz if it is the crystal), which ESP-IDF
   never tunes (it tunes octal PSRAM only above 40 MHz). The first board
   stage reads which (§3).
4. **PSRAM maps at DBUS 0x3D00_0000, 8 MiB** (MMU entries 256 to 383). The
   flash window the HAL accepts shrinks from 32 MiB to 16 MiB, so no PSRAM
   address is ever taken for flash. Processes' writable memory, stacks and
   pools go there; their code stays in flash; the kernel stays in SRAM
   (§4).
5. **No fallback.** Per the owner's ruling: the PSRAM board checks the
   eFuse, the chip's identity, its size, the mode write, the map and a full
   read-back, in that order, and stops on the first that disagrees with the
   description, with one line naming what it read and what the description
   says (§5).
6. **Two boards, one HAL.** `esp32s3` describes no PSRAM and never touches
   CS1; `esp32s3-r8` describes the 8 MiB octal AP part and stops if the
   part is not that. The second is a facade over the first, as
   `hal/riscv32-flat` is over `hal/riscv32` (§5.4).
7. **The emulator models enough to test almost all of it** (run, §6): with
   `-m 8M -global ssi_psram.is_octal=true` and an eFuse file it answers the
   mode registers as an AP part, maps PSRAM pages through the MMU, serves
   every access width, and its flash model's trace shows whether CS0 was
   ever driven. It does not model SPI0, the IO MUX, line modes, timing or
   the cache itself, so those are the part's to show.
8. **Speed.** A miss is a serial burst: an estimated 1.5 to 4 us, against
   one cycle for SRAM. Fine for bulk; a process with deadlines is pinned to
   SRAM, which keeps its stack and data fast but not its read-only data,
   which shares the data cache with PSRAM traffic (§7).
9. **Eight board stages**, the first only reading (§9), and **four units**
   (§10), the first of which can run beside SO-77's U0 now.

---

## 1. Today

### 1.1 What the ROM and `boot.S` leave on the memory-SPI controller

- **The ROM's direct boot** maps 32 MiB of flash at both windows before it
  enters the image: `Cache_Ibus_MMU_Set(0, 0x42000000, 0, 64, 0x200, 0)`
  and `Cache_Dbus_MMU_Set(0, 0x3C000000, 0, 64, 0x200, 0)` in
  `ets_run_direct_boot` (0x4004_56B4), then `callx8 0x4200_0408`
  (`hal/xtensa-esp32s3/ABI.md` §2a). Nothing else in that path configures
  the controller as far as ABI.md records.
- **One MMU table serves both buses.** The S3 has 512 entries of 64 KiB, and
  entry n is linear address n x 64 KiB, which is both DBUS 0x3C00_0000 + n x
  64 KiB and IBUS 0x4200_0000 + n x 64 KiB ([IDF] `ext_mem_defs.h`: "I/D
  share the MMU linear address range", `SOC_MMU_ENTRY_NUM 512`,
  `DR_REG_MMU_TABLE 0x600C5000` in `soc.h`). An entry is the physical page
  number, bit 14 set for invalid, bit 15 set for PSRAM
  (`SOC_MMU_INVALID`, `SOC_MMU_ACCESS_SPIRAM`; `mmu_ll_write_entry`). So
  the ROM's two calls write the same 512 entries, each a flash page.
  [RUN] under the emulator: entries 0..4, 128, 252, 256, 376, 384 and 500
  read their own index, flash type, valid (§6.2).
- **`boot.S`'s `_start`** writes no MSPI, cache or MMU register. It leaves
  the register window, clears `CPENABLE`, quiets the watchdogs, copies the
  vectors to IRAM and `.data` to DRAM through the data window, zeroes
  `.bss`, sets `VECBASE` and calls `kmain` ([TREE] `kernel/boot.S`).
- **The kernel** writes none of them either: `kernel/main.saw`'s `kmain`
  prints the banner, calls `hal.intc_init()` and `load_root_and_enter`.
- **What the part's SPI0/SPI1 registers hold at handover is unknown.** The
  emulator does not model SPI0 at all (0x6000_3000 falls in `esp32s3.iomem`;
  [RUN] every SPI0 register reads 0). SPI1 reads, under the emulator,
  `CTRL=0x00208000 CLOCK=0x00030103 USER=0x800000c0 USER1=0x5c000007
  USER2=0x70000000 MISC=0x00000000`, which is the emulator's model after the
  ROM's one flash command, not a statement about silicon. The reset values
  in [IDF] `spi_mem_reg.h` are `SPI_MEM_CLOCK_REG` and
  `SPI_MEM_SRAM_CLK_REG` divider fields N=3, H=1, L=3 (divide by 4),
  `SPI_MEM_CORE_CLK_SEL` 0, `SPI_MEM_MISC_REG` `CS1_DIS`=1, `CS0_DIS`=0.
  Board stage P0 reads the real values (§9).
- **The flash** is quad, 8 MiB, manufacturer 0x20, device 0x4017, 3.3 V
  (`esptool flash-id` on the owner's part, SO-76 c6).

### 1.2 The address windows in use

```
0x4200_0000 + 32 MiB  IBUS, flash, instruction fetch   (lib.saw FLASH_IBUS)
0x3C00_0000 + 32 MiB  DBUS, flash, data reads          (lib.saw FLASH_DBUS)
                      both FLASH_WINDOW_LEN = 0x0200_0000
```

`hal/xtensa-esp32s3/kernel/lib.saw`: `in_flash_window`, `same_bytes`
(an IBUS address and the DBUS address of the same flash byte name the same
bytes) and `segment_bus_ok` (code only in place in IBUS; read-only data in
place in DBUS or copied to process RAM; writable data copied to process
RAM). The flash image itself is a little over 2 MiB (`esp32s3.ld`: root's
slot at 0x10_0000, a child's at 0x18_0000, `.drom` at 0x20_0000).

### 1.3 Where the kernel and processes live

```
IRAM 0x4037_8000 - 0x4037_BFFF  vectors + trap path           esp32s3.ld IRAM
DRAM 0x3FC8_C000 - 0x3FCA_FFFF  kernel .data, .bss, arena,
                                64 KiB stack                   esp32s3.ld DRAM
DRAM 0x3FCB_0000 - 0x3FCC_7FFF  root's region (96 KiB)         user/root.ld
DRAM 0x3FCC_8000 - 0x3FCD_FFFF  a child's region (96 KiB)      user/child.ld
DRAM 0x3FCE_0000 - 0x3FCE_FFFF  the pool (64 KiB)              runner pool_base
flash, in place                 every process's code and
                                read-only data                  ABI.md §13
```

Two internal-SRAM ranges the layout already avoids are the caches' own
memory: the instruction cache uses 0x4037_0000-0x4037_7FFF and the data
cache the top of DRAM, 0x3FCF_0000-0x3FCF_FFFF ([IDF] `memory.ld.in`,
lines 18-25 and `DCACHE_SIZE 0x10000`). `RAM_TOP` stops at 0x3FCF_0000. So
PSRAM traffic, which lives in the data cache, costs no SRAM.

### 1.4 What "RAM" means to the kernel on this board

One range, `RAM_BASE` 0x3FCB_0000 to `RAM_TOP` 0x3FCF_0000 ([TREE]
`lib.saw`). `map_target_ok` and `map_source_ok` both answer "lies inside
it"; `segment_bus_ok`'s copy branch answers the same. `map_source_ok` is
asked for a `ProcessCreate` destination, a slab donation and a mapping's
backing (`kernel/core/dispatch.saw` around lines 1104, 1281, 3158),
`map_target_ok` for a mapping's target (line 3072). The boot region table's
`Ram` rows are minted without a range check (`kernel/core/process.saw`,
`mint_boot_regions`); they are judged when used. Addresses are physical and
identity everywhere (`phys_to_virt`, `map_place`), and `prot_update`
asserts that a row's `base` and `pa` name the same bytes.

---

## 2. The bring-up sequence

### 2.1 What ESP-IDF does, and what this design keeps

[IDF] `esp_psram_impl_enable` (octal driver) runs, in order:

1. `s_init_psram_pins`: GPIO26 to function SPICS1 with drive 3; SPI0's
   `SMEM_SPICLK_FUN_DRV` to 3. The octal data pins were set earlier by
   `esp_mspi_pin_init` (`flash_ops.c`), which calls the ROM's
   `esp_rom_opiflash_pin_config`.
2. `s_set_psram_cs_timing`: SPI0's `SPI_MEM_SPI_SMEM_AC_REG`.
3. `s_configure_psram_ecc`: ECC off.
4. `mspi_timing_enter_low_speed_mode(true)`: core clock 80 MHz, flash and
   PSRAM clocks 20 MHz on SPI0 and SPI1, tuning registers cleared.
5. `SPI_MEM_DDR_REG(1)` variable dummy on; `esp_rom_spi_set_dtr_swap_mode(1,
   false, false)`.
6. `s_init_psram_mode_reg`: read MR0, set latency fixed, read latency code
   2, drive strength 0, write MR0.
7. `s_get_psram_mode_reg`: read MR0..MR4 and MR8; refuse a vendor other
   than 0x0D; size from MR2's density.
8. `mspi_timing_psram_tuning`, `mspi_timing_enter_high_speed_mode`, then
   restore SPI1 for the flash driver.
9. `s_config_psram_spi_phases`: SPI0's PSRAM command, address, dummy, DDR
   and octal settings; `Cache_Resume_DCache(0)`.

Then `esp_psram_init` maps the pages (`s_psram_mapping`:
`mmu_hal_map_region` with target PSRAM, `cache_ll_l1_enable_bus`), and
`cpu_start.c` runs `esp_psram_extram_test` when `CONFIG_SPIRAM_MEMTEST`
is set. The whole octal driver is `noflash` ([IDF] `esp_psram/linker.lf`):
it runs from IRAM.

**Kept:** 1 (CS1 pin), the data pins from the ROM's pin routine but only for
the PSRAM's own pads, 2, 3, 5, 6, 7, 9, the mapping and the test.
**Dropped:** step 4 and step 8. Both write SPI0's flash clock, the shared
core clock and the shared timing registers ([IDF] `mspi_timing_config.c`,
`mspi_timing_enter_low_speed_mode`'s comment: "SPI0 and SPI1 share the
din_num and din_mode regs"). Dropping them is what keeps every flash-side
register untouched. The cost is that the RAM runs at whatever clock the
divider gives (§3).

**Mechanism:** the ROM's routines are windowed and keep their state in ROM
RAM at 0x3FCE_FFE4-0x3FCE_FFF8 (`rom_spiflash_legacy_data`,
`rom_spi_usr_cmd_legacy_funcs`, `rom_opiflash_cmd_def`, from the ROM
ELF's symbols), which lies in today's process range. The kernel is call0
and calls no ROM code (ABI.md §2b). So the design writes the registers
itself, as the ROM does, and §6.3 shows the words it composes equal the
ROM's own on the emulator.

### 2.2 Where it runs

- A new HAL seam, `hal.memory_init()`, called by `kmain` after the banner
  and before `hal.intc_init()`. Every other HAL answers it with nothing. The
  S3's PSRAM board runs the sequence there; its stops print through the
  console, after the banner, before anything is loaded.
- Steps S1 to S12 below are C in a new file
  (`hal/xtensa-esp32s3/kernel/mspi.c`), placed in `.iram` beside the trap
  path, reading and writing only registers, its own stack and a result
  record in DRAM. While it runs there are no flash fetches and no
  flash-backed data loads, so SPI0 is idle on the bus while SPI1 sends to
  the RAM chip. That is the reason ESP-IDF's driver is `noflash`; this
  design does not rely on SPI0/SPI1 arbitration, which I did not find
  documented in the sources read. Interrupts are masked throughout, as
  everywhere in this kernel (ABI.md §7f); no other bus master runs (the APP
  CPU is held in reset, ABI.md §11; no DMA is used).
- The checks, the stop lines, the read-back test and the publishing of the
  range are Saw in `lib.saw`, on the record the C part leaves.

### 2.3 The steps

Register addresses from [IDF] `reg_base.h` (`SPI1` 0x6000_2000, `SPI0`
0x6000_3000, `IO_MUX` 0x6000_9000, `EFUSE` 0x6000_7000, `SYSCON`
0x6002_6000, `EXTMEM` 0x600C_4000) and `soc.h` (MMU table 0x600C_5000).
"RMW" is read, change the named fields, write. "If skipped or wrong" says
what happens; "CS0" says whether chip select 0 can be driven in that step.

**S0. Read, change nothing.**
- eFuse `EFUSE_RD_MAC_SPI_SYS_4_REG` (0x6000_7054): `PSRAM_CAP` bits [4:3]
  must be 1 ("8M"), `PSRAM_VENDOR` bits [8:7] must be 1 ("AP_3v3")
  ([IDF] `efuse_reg.h`; meanings from `esp_efuse_table.csv`, BLK1 bits 131
  and 135). This is what `esptool flash-id` printed as "Embedded PSRAM 8MB
  (AP_3v3)" on the owner's part (SO-76 c6).
- `RTC_CNTL_DIG_PAD_HOLD_REG` (0x6000_80DC): the hold bits the ROM's
  `gpio_pad_unhold` clears for pads 22 and up (bit `pad - 21`) must be
  clear for pads 26 and 33 to 37. The design checks rather than writes, so
  no RTC-domain register is ever written.
- `SPI_MEM_CMD_REG(1)` (0x6000_2000) must read 0 (SPI1 idle).
- If skipped: on a part without in-package PSRAM the next steps would drive
  pins and send commands to nothing; the identity check would still stop
  it, later and with less to say. CS0: no command is sent.

**S1. The PSRAM's own pads.** ([ROM] `esp_rom_opiflash_pin_config`, default
branch, table at 0x3FF1_90A8: pads 30, 29, 32, 31, 28, 27 at function 0 are
the flash's; pads 33..36 function 4 (SPIIO4..7) and 37 function 4 (SPIDQS);
input signals 7..11; [IDF] `spi_pins.h` names 26 CS1, 33..36 D4..D7, 37
DQS; `io_mux_reg.h` field positions.)
- IO MUX pad 26 (0x6000_906C), RMW: `MCU_SEL` [14:12] = 0 (`FUNC_SPICS1`),
  `FUN_DRV` [11:10] = 3 ([IDF] `s_init_psram_pins`).
- IO MUX pads 33..37 (0x6000_9088 + 4 x (pad - 33)), RMW: `MCU_SEL` = 4,
  `FUN_PD` (bit 7) = 0, `FUN_PU` (bit 8) = 1, `FUN_IE` (bit 9) = 1 ([ROM]
  `gpio_pad_pullup`, `gpio_pad_input_enable`).
- GPIO `FUNCn_IN_SEL_CFG` for n = 7..11 (0x6000_4154 + 4n), RMW: bit 7
  (`SIG_IN_SEL`) = 0, so the signals come from the IO MUX directly ([ROM]
  `gpio_bypass_matrix_in`: `(sig + 0x18001055) << 2`, clears bit 7).
- **Not done:** the ROM routine also rewrites pads 27..32, the flash's, to
  the function they already have. This design never writes them.
- If wrong: an index of 27..32 would take a flash pad off the flash, and the
  next instruction fetch from flash would fail: a kernel fault, or a hang,
  until reset. A wrong function on 33..37 makes the identity read return
  garbage, and S8 stops. CS0: no command is sent.

**S2. PSRAM chip-select timing, SPI0** ([IDF] `s_set_psram_cs_timing`).
`SPI_MEM_SPI_SMEM_AC_REG(0)` (0x6000_30DC), RMW: `CS_SETUP` (bit 0) = 1,
`CS_HOLD` (bit 1) = 1, `CS_SETUP_TIME` [6:2] = 3, `CS_HOLD_TIME` [11:7] =
3, `CS_HOLD_DELAY` [30:25] = 2. The driver's comment: SPI0 and SPI1 share
these for PSRAM. If wrong: marginal or failed PSRAM transfers, caught by
S8 or S14. This register governs CS1 only ([IDF] field descriptions:
"SMEM", Ext_RAM). CS0: no command.

**S3. ECC off** ([IDF] `s_configure_psram_ecc`, the `#else` branch).
`SYSCON_SRAM_ACE0_ATTR_REG` (0x6002_6058) bit 8 clear. Its reset value is
0x0FF ([IDF] `syscon_reg.h`), so bit 8 is already clear; the step is a
check that writes only if it finds it set. If it were set, the controller
would expect ECC bytes the chip does not have, and S14 stops.

**S4. PSRAM clock, SPI0.** `SPI_MEM_SRAM_CLK_REG(0)` (0x6000_3050) = the
divider word (§3); the reset word is `0x00030103`, divide by 4 ([IDF]
`spi_mem_reg.h` defaults; the encoding in `mspi_timing_ll_set_psram_clock`).
`SPI_MEM_CORE_CLK_SEL_REG` is not written: it is shared with the flash.
If wrong: too fast fails S8 or S14; too slow risks the chip's maximum
chip-select-low time (§3).

**S5. Prepare SPI1** ([ROM] `esp_rom_opiflash_backup_reg`; [IDF] step 5).
Save `CTRL` (+0x08), `USER` (+0x18), `USER1` (+0x1C), `USER2` (+0x20),
`ADDR` (+0x04), `MOSI_DLEN` (+0x24), `MISO_DLEN` (+0x28), `CACHE_FCTRL`
(+0x3C), `DDR` (+0xE0), `MISC` (+0x34) of SPI1, to restore in S10. The ROM
saves only the first four and DDR; this design saves every register it
changes. Then `SPI_MEM_DDR_REG(1)`: clear `WDAT_SWP` (bit 3) and `RDAT_SWP`
(bit 2) ([ROM] `esp_rom_spi_set_dtr_swap_mode`: `and -13`), set `VAR_DUMMY`
(bit 1) ([IDF] step 5). CS0: no command.

**S6. The chip-select gate.** `SPI_MEM_MISC_REG(1)` (0x6000_2034), RMW:
bits [1:0] = 0b01, that is `CS0_DIS` = 1, `CS1_DIS` = 0; read it back;
anything but 0b01 in bits [1:0] stops before any command. This is exactly
what the ROM's `esp_rom_spi_cmd_start` does with the `cs_en_mask` BIT(1)
that ESP-IDF passes for every PSRAM command: `MISC = (MISC & ~3) |
(~mask & 3)` before setting `SPI_MEM_USR`, and after the command `MISC |=
2` then `MISC &= ~1` ([ROM] 0x4004_E0E2-0x4004_E148). The gate is checked
again immediately before each command's start (S7).

**S7. One SPI1 user command** (used by S8 and S9). Values are the ROM's
`esp_rom_spi_set_op_mode(1, ESP_ROM_SPIFLASH_OPI_DTR_MODE)` (mode 7, [ROM]
jump table entry 7 = 0x4004_DD2F) and `esp_rom_spi_cmd_config`, with the
arguments ESP-IDF passes ([IDF] `s_init_psram_mode_reg`,
`s_get_psram_mode_reg`):
1. Wait for `SPI_MEM_CMD_REG(1)` to read 0.
2. `CTRL(1)` = `(CTRL & 0xFE6F9C0F) | 0x2270`: clear every read-mode bit,
   set `FASTRD_MODE` (13), `FCMD_OCT` (9), `FADDR_OCT` (6), `FDIN_OCT` (5),
   `FDOUT_OCT` (4) ([ROM] `esp_rom_spi_reset_rw_mode` mask and mode-7
   entry; [IDF] `spi_mem_reg.h` bit names).
3. `USER(1)`: clear bits [15:12] (`FWRITE_*`), set `USR_COMMAND` (31),
   `USR_ADDR` (30), and for a read `USR_DUMMY` (29) and `USR_MISO` (28),
   for a write `USR_MOSI` (27); keep bits [11:0].
4. `USER1(1)` = `(31 << 26) | (dummy - 1)`, or `31 << 26` with no dummy.
5. `USER2(1)` = `(15 << 28) | command`.
6. `ADDR(1)` = the register address; `CACHE_FCTRL(1)` bit 1 set (32-bit
   address, as the ROM does when `addrBitLen == 32`).
7. `MOSI_DLEN(1)` / `MISO_DLEN(1)` = bits - 1; `W0` (+0x58) = data to send.
8. Read `MISC(1)` again: bits [1:0] must be 0b01, or stop without sending.
9. `CMD(1)` = `0x0004_0000`, `SPI_MEM_USR` alone; poll until it reads 0.
10. Read the answer from `W0`.
- The command word written to `CMD(1)` is a constant with one bit set. Its
  neighbours are the controller's built-in flash commands ([IDF]
  `spi_mem_reg.h`: bit 22 chip erase, 23 block erase, 24 sector erase, 25
  page program, 26 write status, 30 write enable, 21 deep power-down).
  None is ever written. Each of the destructive ones also needs the flash's
  write-enable latch, which only a write-enable (bit 30, or command 0x06 to
  CS0) sets, and this design sends neither.
- CS0: this is the only step that drives a chip select, and it is gated
  twice (sub-steps 8 and S6).

**S8. Identity, before anything is written to the chip** ([IDF]
`s_get_psram_mode_reg`, `s_print_psram_info`). ESP-IDF writes MR0 first and
checks the vendor after; this design reads and checks first, so a part that
is not the described one receives no write.
1. Read MR0/MR1: command 0x4040 (`OPI_PSRAM_REG_READ`), address 0, dummy 8
   (`OCT_PSRAM_RD_REG_DUMMY_BITLEN`, 2 x (5 - 1)), 16 bits in. ESP-IDF
   reads MR0 with this same dummy before it has set the latency
   (`s_init_psram_mode_reg`), so the default latency serves it.
2. The same shape at address 2 (MR2/MR3, 16 bits), 4 (MR4, 8 bits) and 8
   (MR8, 8 bits).
3. Checks, in order: MR1 bits [4:0] = 0x0D (AP Memory,
   `OCT_PSRAM_VENDOR_ID`); MR2 bit 7 = 1 (good die); MR2 bits [2:0] = 3
   (8 MiB; the density table 1 = 4 MiB, 3 = 8, 5 = 16, 7 = 32, 6 = 64 MiB).
   MR2 [4:3] (generation), MR3 bit 6 (VCC: 1 = 3 V) and bit 5 (refresh), MR4
   and MR8 are printed, not checked (question 3).

**S9. Mode register 0** ([IDF] `s_init_psram_mode_reg`, values from
`esp_psram_impl_enable`: `lt = 1`, `read_latency = 2`, `drive_str = 0`).
1. MR0' = (MR0 & 0xC0) | 0x28: bits [1:0] drive strength 0, [4:2] read
   latency code 2, bit 5 fixed latency (`opi_psram_mode_reg_t`).
2. Write: command 0xC0C0 (`OPI_PSRAM_REG_WRITE`), address 0, no dummy, 16
   bits out, `W0` = (MR1 << 8) | MR0' (the driver writes MR0 and the MR1
   it read).
3. Read MR0/MR1 again; MR0 & 0x3F must equal 0x28.
- If wrong: a latency the controller does not expect corrupts every later
  transfer; S9.3 or S14 stops. MR0 is volatile (the chip is a DRAM; the
  emulator's model and ESP-IDF write it on every boot). ECC's MR8 write is
  not done (ECC is off).

**S10. Restore SPI1.** Every register saved in S5 written back, `MISC(1)`
last, so SPI1 is left with `CS1_DIS` = 1, `CS0_DIS` = 0, which is its reset
state and what the ROM leaves after a command.

**S11. SPI0's PSRAM access** ([IDF] `s_config_psram_spi_phases`), all RMW of
SPI0's PSRAM-side registers:
- `SPI_MEM_CACHE_SCTRL_REG(0)` (+0x40): `CACHE_USR_SCMD_4BYTE` (0) = 1,
  `USR_WR_SRAM_DUMMY` (3) = 1, `USR_RD_SRAM_DUMMY` (4) = 1,
  `CACHE_SRAM_USR_RCMD` (5) = 1, `SRAM_RDUMMY_CYCLELEN` [11:6] = 17
  (`OCT_PSRAM_RD_DUMMY_BITLEN` 2 x (10 - 1), minus 1),
  `SRAM_ADDR_BITLEN` [19:14] = 31, `CACHE_SRAM_USR_WCMD` (20) = 1,
  `SRAM_OCT` (21) = 1, `SRAM_WDUMMY_CYCLELEN` [27:22] = 7 (2 x (5 - 1),
  minus 1).
- `SPI_MEM_SRAM_DRD_CMD_REG(0)` (+0x48) = `0xF000_0000`: 16-bit command
  0x0000 (`OPI_PSRAM_SYNC_READ`).
- `SPI_MEM_SRAM_DWR_CMD_REG(0)` (+0x4C) = `0xF000_8080`: 16-bit command
  0x8080 (`OPI_PSRAM_SYNC_WRITE`).
- `SPI_MEM_SPI_SMEM_DDR_REG(0)` (+0xE4): `VAR_DUMMY` (1) = 1, `RDAT_SWP` (2)
  = 0, `WDAT_SWP` (3) = 0, `DDR_EN` (0) = 1.
- `SPI_MEM_SRAM_CMD_REG(0)` (+0x44): set `SDIN_OCT` (18), `SDOUT_OCT` (19),
  `SADDR_OCT` (20), `SCMD_OCT` (21), `SDUMMY_OUT` (22).
- Not done: `Cache_Resume_DCache(0)`. Nothing here suspends the cache;
  ESP-IDF resumes one its own earlier code may have suspended.
- These registers are SPI0's PSRAM ("SRAM", "SMEM") side; none of SPI0's
  flash registers (`CTRL`, `CLOCK`, `USER*`, `CACHE_FCTRL`, `DDR`,
  `DIN_*`, `DOUT_*`, `CORE_CLK_SEL`) is written. That separation is read
  from the field descriptions in `spi_mem_reg.h`, not from the TRM.
- If wrong: cache fills from PSRAM return garbage; S14 stops. No PSRAM
  page is mapped yet, so no access reaches CS1 while these change.

**S12. The map** ([IDF] `mmu_ll_write_entry`, `s_psram_mapping`). For n = 0
to 127: MMU entry 256 + n (0x600C_5400 + 4n) = `0x8000 | n`; read each
back. Entries 256..383 are DBUS 0x3D00_0000-0x3D7F_FFFF.
- Coexistence with flash: entries 0..255 stay the ROM's flash pages; the
  flash image uses entries 0..32 today (flash below 0x20_1000 + `.drom`).
  The replaced entries named flash pages 256..383, past the 8 MiB chip, which
  nothing reads.
- Cache operations: none. ESP-IDF maps PSRAM without suspending the cache
  (`s_psram_mapping` calls `mmu_hal_map_region` directly). No line for
  these virtual addresses can be in the data cache, since nothing has read
  them since reset; so no invalidate is needed. The data bus is already
  enabled (flash data reads work); `EXTMEM_DCACHE_CTRL1` bit 0 is read and
  must be 0 (`cache_ll_l1_enable_bus` would clear it).
- The same entries are IBUS 0x4300_0000-0x437F_FFFF ([RUN] §6.2: the word
  stored at 0x3D00_0000 read back at 0x4300_0000). This is why the HAL's
  flash window shrinks to 16 MiB (§4), so no IBUS address of PSRAM is taken
  for flash code.
- If wrong: an index below 256 would replace the kernel's own flash pages:
  the next fetch or `.rodata` read faults. Volatile; a reset restores the
  ROM's map.

**S13. Done with the controller.** From here no MSPI, IO MUX or MMU register
is written until the machine stops.

**S14. The read-back test** ([IDF] `s_test_psram`): for every eighth word
of the 8 MiB, write `index ^ 0xAAAAAAAA`; then read every one of them back
and count mismatches. 262,144 writes and reads through the write-back data
cache. Because the span is 128 times the largest data cache (64 KiB), every
written line has been evicted to the chip before the read pass reaches it,
and every read is a miss served by the chip. A density smaller than claimed
aliases and fails here even if S8 were fooled. ESP-IDF runs this by default
(`CONFIG_SPIRAM_MEMTEST`), which its comment prices at "about a second" per
4 MiB at its own clock. The test leaves the PSRAM holding the pattern;
nothing relies on PSRAM contents at boot.

**S15. Publish.** The HAL's PSRAM range answers present (§4), and the
kernel prints `SOS-S3: psram 8 MiB at 0x3d000000, ...` (§5.2).

### 2.4 Every step in which CS0 could be driven

Only S7 drives a chip select at all; S8 and S9 call it six times (MR0/1,
MR2/3, MR4 and MR8 reads, the MR0 write, the MR0/1 read-back). Each
starts only after `MISC(1)` bits [1:0] read back 0b01 at sub-step 8, and S6
read it back once before the first. Cache accesses go out on SPI0, which
selects CS1 for an MMU entry with bit 15 set and CS0 for a flash entry by
hardware; S12 writes only entries 256..383 and reads each back. No other
step drives either chip select. On the emulator, the flash model's trace
shows the flash selected exactly once per boot (the ROM's own 0xAB command)
with the gate in place, and selected and sent 0x40 with the gate removed
(§6.4).

### 2.5 What a reset does not undo, and non-volatile state

- **Nothing in the sequence writes non-volatile state.** No eFuse register is
  written: the design only reads `EFUSE_RD_*` (programming needs
  `EFUSE_CONF` and `EFUSE_CMD`, which nothing here names). No command
  reaches the flash, so its status registers, its OTP and its array are
  never addressed. The PSRAM has no non-volatile state; its mode register
  write is undone at power-off.
- **What survives a chip reset:** only MR0 of the PSRAM, until power-off,
  which no ROM path reads (the ROM never selects CS1). No RTC-domain
  register is written (S0 checks the pad hold instead of clearing it), so
  nothing survives in the RTC domain either.
- **What a chip reset restores:** the board's RST button and esptool's
  auto-reset both pull EN, a power-on reset of the digital system: SPI0 and
  SPI1 registers, IO MUX, GPIO matrix, the cache and its MMU table go back
  to their reset values, and the ROM's direct boot writes the 512 flash
  entries again (§1.1).
- **Download mode** is the ROM's, entered by strap (GPIO0 low at reset)
  before any byte of the image runs; nothing in the image can prevent it,
  and nothing here touches `DIS_DOWNLOAD_MODE` or any eFuse. So the flash
  stays writable by `write-flash 0x0` whatever a stage does.

---

## 3. Timing

- **The bus speed** is the MSPI core clock divided by `SRAM_CLK`'s divider.
  The core clock is 80 MHz with `CORE_CLK_SEL` = 0 when the system clock is
  the PLL ([IDF] `spi_mem_reg.h`, `SPI_MEM_CORE_CLK_SEL` description); what
  it is with the system clock on the crystal is not in the sources read, and
  ABI.md §11 says the CPU clock the ROM leaves is unknown. So:
  - **Stage P0 reads** `SYSTEM_SYSCLK_CONF_REG` (0x600C_0060, `SOC_CLK_SEL`
    [11:10]), `CORE_CLK_SEL`, SPI0's `CLOCK` and `SRAM_CLK`, and measures
    CCOUNT against SYSTIMER.
  - **The divider is a HAL constant set from that reading**, aiming at
    20 MHz, the speed ESP-IDF itself initializes the chip at
    (`mspi_timing_enter_low_speed_mode`: `set_psram_clock(20, ...)`): 4 if
    the core is 80 MHz, 2 if it is 40 MHz. Never faster than 40 MHz.
- **Tuning is not needed.** ESP-IDF tunes octal (DTR) PSRAM only when its
  clock is above 40 MHz: `MSPI_TIMING_PSRAM_NEEDS_TUNING
  (MSPI_TIMING_PSRAM_MODULE_CLOCK > 40)` for `MSPI_TIMING_PSRAM_DTR_MODE`
  ([IDF] `mspi_timing_tuning_configs.h`). Its slowest offered octal speed
  is 40 MHz (`Kconfig.spiram`, `SPIRAM_SPEED`, default 40M).
- **The one timing risk at low speed is the chip's maximum chip-select-low
  time** (a DRAM must refresh, so a burst may not hold CS low too long).
  The figure is in AP Memory's datasheet, which I did not read. A 32-byte
  line costs about 1 + 2 + 10..20 + 16 = 29..39 clocks (command, address,
  read latency with and without the doubled variable latency, data at two
  bytes per clock in DTR): about 1.5..2 us at 20 MHz and 3..4 us at
  10 MHz. A 64-byte line adds 16 clocks. If the datasheet's limit is near
  4 us, 10 MHz with 64-byte lines is outside it. That is why the divider
  aims at 20 MHz, and why P0 also reads the data cache's line size
  (`EXTMEM_DCACHE_CTRL_REG` [4:3]). **The datasheet figure must be read
  before unit C is briefed.**
- **A part that fails** any check stops with its line (§5). There is no
  retry at another speed: a retry would be guessing, and the owner's rule
  is that the part matches its description or the kernel says why not.

---

## 4. The memory map with PSRAM

### 4.1 The ranges the HAL publishes (for SO-95's `sos_board_info` and
`board.toml`)

```
name    use      speed  base         len     board
kernel  kernel   fast   0x3FC8_C000  144 KiB both   kernel .data/.bss/arena/
                                                    stack; slabs; linked areas
sram    process  fast   0x3FCB_0000  256 KiB both   regions and pools
psram   process  slow   0x3D00_0000    8 MiB r8     regions and pools
default process: sram on esp32s3, psram on esp32s3-r8; pools the same
```

No `present = "probed"`: a range is present because the board's
description says so, and the HAL verifies it at boot or stops (§5). Root's
region stays where SO-95 rule 5.10 puts it (SRAM); nothing in this design
requires that any more, because the bring-up now precedes the loader.

### 4.2 What changes in `lib.saw`

- `FLASH_WINDOW_LEN` becomes `0x0100_0000` (16 MiB) on both boards. Then
  `in_flash_window` can never accept a PSRAM address in either alias,
  `same_bytes` never pairs 0x4300_0000 with 0x3D00_0000, and
  `segment_bus_ok`'s executable branch never accepts PSRAM code. Nothing
  linked today is above 4 MiB.
- `RAM_BASE`/`RAM_TOP` keep meaning the SRAM process range (root's script,
  `bus_probe`, the runner use them). New: `PSRAM_BASE` = 0x3D00_0000 and a
  function `psram_bytes()`, 0 in the base HAL and 8 MiB in the r8 facade
  (a function so the facade can override it, the reason `lib.saw` gives for
  the `prot_*` seam). `psram_bytes()` answers 0 until S15 has run, so
  nothing can treat PSRAM as RAM before it is proven.
- `map_source_ok(base, top)` and `map_target_ok(base, top)`: inside the SRAM
  range, or inside the PSRAM range when `psram_bytes()` is non-zero. A range
  spanning both is refused (they are not adjacent).
- `segment_bus_ok`'s copy branch: the same test. Executable segments stay
  flash-only and in place, as today.
- `memory_init()`: no-op in the base HAL; the sequence in the facade.

### 4.3 The boot region table and the runner

- A new arch entry, `ESP32S3_R8_ARCH`, beside `ESP32S3_ARCH`
  (`tools/sos_runner.py`): `hal_native` the facade, `child_region_base`
  0x3D00_0000 for hand-assembled children, `pool_base` in PSRAM, and the
  emulator arguments of §6.5. Blade-built children keep the SRAM child
  slot until SO-95's U4 generates link addresses, because their `.data`
  is linked by `user/child.ld` at 0x3FCC_8000; unit D adds one PSRAM child
  script for one test package.
- `check_in_place`'s `window = 32 * 1024 * 1024` becomes the HAL's 16 MiB.
- Rows: the destination and pool rows carry PSRAM addresses on the r8
  board. A row there on the plain board is minted and then refused at use
  by `map_source_ok`; SO-95's builder refuses it earlier, since the plain
  board names no `psram` range.

### 4.4 How a process linked for PSRAM runs

Its code and read-only data run in place in flash as today (IBUS and DBUS,
§1.2). Its writable segments are copied by the loader to their link
addresses in PSRAM (the loader's byte stores go through the data cache; a
DBUS address takes every width, [RUN] §6.2 for the emulator, stage P4 for
the part), its arena is in its `.bss`, and the kernel grants its stack at
the top of its destination, also PSRAM. Its pools are PSRAM.

### 4.5 The kernel's view

- **Copy doors and `row.pa`.** Identity, as everywhere on this board: a
  PSRAM row's `base` and `pa` are the same DBUS address, and the kernel
  dereferences it directly. Nothing changes in kind.
- **Interrupts masked and cache misses.** The kernel always runs masked
  (ABI.md §7f), so every kernel access to process memory in PSRAM, the
  loader's copies and every copy door, takes its misses masked. A miss
  stalls the core; it raises no exception, so this is correct, but it
  lengthens masked time: about one miss per 32 bytes copied, so an
  estimated 0.2 to 0.5 ms per 4 KiB (§7). The kernel's own data, slabs,
  stacks and trap frames stay in SRAM, so the trap entry, the scheduler and
  the switch touch no PSRAM.
- **No cache maintenance.** One core, no DMA, and one path (the data cache)
  to every PSRAM byte: nothing to write back or invalidate.

---

## 5. Checks and stops (the owner's ruling)

### 5.1 The rule

The part must match its description exactly. A board description that
names PSRAM brings it up or stops; it never continues in SRAM and never
infers a size. A description that names no PSRAM never touches the
controller, the PSRAM pads or CS1. Where else the rule applies in this
design: the eFuse words, the vendor, the good-die bit, the density, the
mode write and the map are each compared with the description, and the
first that differs stops the boot (§5.2).

### 5.2 What is checked, in order, and each stop's line

All stops print one line, then `exit_fail` prints its halt line and the
board halts in its quiet loop (ABI.md §10). The halt code is the step's
own, so the smoke can assert which step stopped. `0x...` stands for the
value read. Before the first line, the banner `SOS M1: kernel up on xtensa
(ESP32-S3)` has already printed.

```
S0  SOS-S3: psram stop: efuse word 0x60007054 = 0x........ says PSRAM_CAP=0x.
    PSRAM_VENDOR=0x., the board says 0x1 (8 MiB) and 0x1 (AP 3.3 V)
S0  SOS-S3: psram stop: pads held, RTC_CNTL_DIG_PAD_HOLD = 0x........, the
    board needs pads 26 and 33-37 free
S0  SOS-S3: psram stop: SPI1 busy, SPI_MEM_CMD = 0x........ before any
    command
S3  SOS-S3: psram stop: SYSCON_SRAM_ACE0_ATTR = 0x........ still has ECC on
    after clearing it
S6  SOS-S3: psram stop: SPI1 MISC = 0x........ after selecting CS1 alone;
    nothing was sent
S8  SOS-S3: psram stop: MR1 vendor = 0x.., the board says 0x0d (AP Memory)
S8  SOS-S3: psram stop: MR2 = 0x.. has the good-die bit clear, the board
    says a good die
S8  SOS-S3: psram stop: MR2 density = 0x. (N MiB), the board says 0x3
    (8 MiB)
S9  SOS-S3: psram stop: MR0 = 0x.. after writing 0x28
S12 SOS-S3: psram stop: MMU entry 0x... reads 0x........, wrote 0x........
S12 SOS-S3: psram stop: data bus off, EXTMEM_DCACHE_CTRL1 = 0x........
S14 SOS-S3: psram stop: read-back at 0x3d...... read 0x........, wrote
    0x........ (0x........ of 0x00040000 words wrong)
```

Each is printed as one line (wrapped here), in the order the steps run.
The vendor stop comes first among the identity checks because a missing or
mis-wired chip reads 0 or 0xFF everywhere, and the vendor line says so
plainly. No part should ever print the S6 line; it is kept because it is
the check that guards the flash.

On success, one line:

```
SOS-S3: psram 8 MiB at 0x3d000000, MR0=0x28 MR1=0x0d MR2=0x.. MR3=0x..,
read-back ok
```

### 5.3 The state of a halted part

- SPI1 is restored to what S5 saved, ending with `MISC(1)` at its reset
  state (S10 runs on every stop after S6, before the line prints).
- SPI0's flash registers, the core clock, the flash's pads and MMU entries
  0..255 were never written, so the kernel's own code and strings are still
  served from flash and the stop line prints.
- The PSRAM may hold a mode-register value and the test pattern, both
  volatile.
- The flash was never selected after the ROM's own boot read, so its
  contents and status registers are as written by `write-flash`.
- The watchdogs are off (`_start`), so the part halts on its line and does
  not reset-loop. RST or esptool's auto-reset is a chip reset (§2.5); the
  ROM then boots or enters download mode exactly as it would on a fresh
  part, and `write-flash 0x0` works.

### 5.4 One board or two

**Recommended: two boards, one HAL.**
- `esp32s3`: today's HAL directory and `board.toml`, no `psram` range.
  Its kernel never runs S1..S15; it runs on any S3 part, the R8 included.
- `esp32s3-r8`: a facade directory (as `hal/riscv32-flat` is over
  `hal/riscv32`: SO-95 rule 3.1 gives each board one `board.toml` in its
  HAL directory, so a facade is a board) that re-exports the S3 HAL and
  overrides `memory_init` and `psram_bytes`. Its `board.toml` names the
  `psram` range, its default process range `psram`, and the identity it
  expects (8 MiB, AP, octal).
- A Freenove kit with an N8R8 module and one with an N8 module are then two
  boards. One description with a "variant" key would need the kernel to
  learn the variant some other way (a boot flag), which reintroduces a
  runtime choice the owner has ruled out.
- SO-95 revision 4's `present = "probed"` and its "refuse a boot row in an
  absent range" become unnecessary: the range is present by description and
  proven at boot, or the boot stops before any row is used. Question 2 asks
  whether the plain board should also refuse a part that has PSRAM.

---

## 6. The emulator (run)

### 6.1 How it was run

The suite lock was taken for every run (`mkdir
/private/tmp/claude-501/sawos-suite-lock`, `rmdir` after, foreground). The
probe lives in `/private/tmp/claude-501/-Users-shawn-Projects-sawos/
34c0d76b-723c-482d-939f-716d368f6e5c/scratchpad/SO-102/probe/`:
`probe.c` (windowed C that keeps the ROM's window and stack and calls ROM
functions by address; a scratch probe, unlike the kernel), `entry.S` (the
direct-boot magic and `entry a1, 32; call8 probe_main`), `probe.ld`,
`mkimg.py` (a 4 MiB flash image as ABI.md §1e), `run.sh` and `runt.sh`.
Built with the installed esp-clang and its `ld.lld`. Each run:

```
qemu-system-xtensa -machine esp32s3 -nographic -semihosting \
    -drive file=probe.bin,if=mtd,format=raw [extra]
```

with extra arguments: none (`nopsram`); `-m 8M` (`quad8`); `-m 8M -global
ssi_psram.is_octal=true` (`oct8`); and for §6.4 and §6.5 also `-trace
'm25p80_*' -D trace.txt` and `-drive file=efuse-work.bin,if=none,
format=raw,id=efuse -global driver=nvram.esp32s3.efuse,property=drive,
value=efuse`.

### 6.2 What a guest sees

Read in [QEMU] `esp32s3.c`: `-m` sizes the PSRAM (`esp32s3_fixup_ram_size`
rounds to 2, 4, 8, 16 or 32 MiB; `default_ram_size = 0`, so no `-m` means
no PSRAM); `esp32s3_machine_init_psram` attaches an `ssi_psram` on SPI1's
bus at CS1 and does not set `is_octal`, whose default is false
(`ssi_psram.c`), so the machine's PSRAM is a quad model unless
`-global ssi_psram.is_octal=true` is given. The cache model translates an
MMU entry with the PSRAM type bit into `esp32s3.cache.psram_as`, the model's
memory (`esp32s3_mmu_region_translate`), whatever SPI0 holds.

Run, `oct8` (abridged; full output `probe/out-oct8.txt`):

```
P ours: err=0 mr0/1=0x0d09 mr2/3=0x2093 mr4=0x20 mr8=0x05
P ours after MR0 write: err=0 mr0/1=0x0d28
P rom exec_cmd: mr0=0x28 mr1=0x0d
P rom exec_cmd: mr2=0x93 mr3=0x20
P misc after: spi1=0x00000002
P mapped: mmu[256]=0x00008000 mmu[383]=0x0000807f
P read 0x3d000000 before write = 0x00000000
P read back lo=0xa5a5c3c3 hi=0x5a5a3c3c
P byte store at +1: word=0xa5a577c3
P ibus alias 0x43000000 = 0xa5a577c3
P lo after write at +4MiB = 0xa5a577c3 mid=0x11112222
P end
status=0
```

- MR1 0x0D (AP), MR2 0x93 (good die, generation 3, density 3 = 8 MiB), MR0
  0x09 by default, and the MR0 write takes. The ROM's own
  `esp_rom_opiflash_exec_cmd`, called with ESP-IDF's arguments, reads the
  same.
- `quad8` (no `is_octal`): every MR read returns 0. `nopsram`: MR reads
  return 0, and the first load at 0x3D00_0000 after mapping takes
  `LoadStoreError` (EXCCAUSE 3, EXCVADDR 0x3d000000). Memory, byte stores
  and the IBUS alias behave as in `oct8` under `quad8`.
- SPI0 reads 0 in every register; IO MUX pads 26..37 read 0; the data cache
  control registers read 0 and the PSRAM miss counter stays 0.

### 6.3 The register words against the ROM's

`esp_rom_spi_set_op_mode(1, 7)` then `esp_rom_spi_cmd_config(1, &cmd)` for
an MR0 read, then the design's own S7 for the same command, each followed
by a dump of SPI1:

```
P rom words: ctrl=0x0020a270 user=0xf00000c0 user1=0x7c000007
             user2=0xf0004040 addr=0x00000000 miso=0x0000000f
             fctrl=0x00000002 ddr=0x00000001
P our words: ctrl=0x0020a270 user=0xf00000c0 user1=0x7c000007
             user2=0xf0004040 addr=0x00000000 miso=0x0000000f
             fctrl=0x00000002 ddr=0x00000003
```

Equal but `DDR`, where the design also sets `VAR_DUMMY` as ESP-IDF does
before its commands (§2.3 S5). The ROM's code is the real mask ROM, so these
words are what it composes on silicon from the same starting values; the
starting values themselves are the emulator's.

### 6.4 The chip-select gate, observed

Two builds of a probe that sends one MR read through the design's S7 and
stops, one as designed and one with `CS0_DIS` left 0 (`-DMUTANT_CS0`), run
with the flash model's trace:

```
gate in place:  m25p80 ... new command:0xab ... m25p80_select deselect
                (the ROM's one command at boot; nothing after)
gate removed:   ... m25p80_select select
                m25p80_command_decoded new command:0x40
                m25p80_read_data ... (x8)
                m25p80_select deselect
```

Both builds read MR0/1 = 0x0d09. So the emulator distinguishes a sequence
that addresses the flash from one that does not, and a smoke case can
assert "the flash is selected once per boot, by the ROM", which no test of
the PSRAM's answers alone can show.

### 6.5 The eFuse words

`EFUSE_RD_MAC_SPI_SYS_4` reads 0 with no eFuse file and 0x00000088
(`PSRAM_CAP` 1, `PSRAM_VENDOR` 1) with a 1 KiB file holding 0x88 at byte 40
(the model's file is `ESPEfuseBlocks`: six words of block 0, then block 1,
so word 4 of block 1 is at byte 40; [QEMU] `esp_efuse.c`,
`esp_offset_of_block`). The file was unchanged after the run; the runner
should still give each run its own copy.

### 6.6 What the smoke can then test, and what is left to the part

Each item: what the emulator can show, then the board stage that shows it
on the part.

- **The eFuse check and its stop:** yes, with and without the file; P0, P1.
- **The CS gate (the flash never selected):** yes, by the trace; on the part
  not observable directly, only by every later stage still booting.
- **MR identity, the MR0 write, each identity stop:** yes, with `-m 4M`,
  `-m 16M`, the quad model and no `-m` for the stops; P1, P2, P7.
- **Pad functions, the input bypass, CS1's drive:** no, the IO MUX and the
  GPIO matrix are not modeled; P1.
- **Octal DTR line mode, dummy cycles, clocks:** no, SPI1 transfers are
  byte-serial and the mode bits are ignored; P1, P2.
- **SPI0's PSRAM configuration:** no, SPI0 is not modeled; P3.
- **The MMU entries, the map, the IBUS alias, the stop on a bad entry:**
  yes; P3.
- **The read-back test passing:** trivially (no cache, no chip); P4.
- **The read-back stop:** yes, by a harness kernel that maps two entries to
  one page; on the part, P7 shows a stop path.
- **Every access width in PSRAM:** byte and word stores and loads ran here
  (§6.2); halfword, unaligned and `s32c1i` were not run but are expected to
  behave as at DRAM addresses (ABI.md §3c, §8); P4.
- **Processes, stacks and pools in PSRAM:** yes (memory semantics); P6.
- **Speed, misses, write-back:** no; P5.

So the smoke runs processes from PSRAM, and the part shows the electrical
and timing half.

---

## 7. Speed

- **A miss** costs one PSRAM burst: by the estimate in §3, 1.5 to 2 us at
  20 MHz for a 32-byte line, plus as much again when the evicted line is
  dirty. Against one core cycle for SRAM, and at a CPU clock still unknown
  (ABI.md §11), that is two orders of magnitude per miss. A hit costs as
  SRAM does.
- **For a process:** a stack that stays within a few KiB, and data used
  repeatedly, live in the cache and cost little after the first touch. A
  scan over a large buffer pays a miss per line. A process with deadlines is
  pinned to SRAM (SO-95's `ram = "fast"`), which keeps its stack and data
  out of the cache entirely.
- **What pinning does not do:** a pinned process's read-only data is still in
  flash behind the same data cache, so another process's PSRAM traffic can
  evict it; its code is behind the instruction cache, which PSRAM data does
  not use. And the kernel copies into PSRAM with interrupts masked (§4.5),
  so a PSRAM process's large system calls delay everyone's interrupts,
  pinned processes included.
- **Measured on the part** (stage P5), with SYSTIMER (16 MHz) and
  `EXTMEM_DBUS_ACS_SPIRAM_MISS_CNT` (0x600C_40D4) and
  `EXTMEM_DBUS_ACS_CNT` (0x600C_40D8), cleared through
  `EXTMEM_CACHE_ACS_CNT_CLR_REG` (0x600C_40C4):
  1. a 1 MiB walk at 32-byte stride, reads only, in PSRAM, then the same in
     SRAM: time per access and misses;
  2. the same walk writing, which adds the write-back of dirty lines;
  3. a 16 KiB loop repeated (fits the cache): the hit cost;
  4. a kernel copy door of 4 KiB into PSRAM: masked time per call;
  5. the full read-back test's duration (also printed by P4).
  Each prints numbers; none is asserted (CLAUDE.md, host time).

---

## 8. An attack on this design

### 8.1 Ways the part could be left unable to boot our image or be flashed

Each: how it could happen, how likely, and the way back.

1. **A command reaches the flash and changes its status register or its
   array.** Needs CS0 driven (gated twice, §2.4) and a write-enable first,
   which nothing sends. Very low. If it happened, block-protect bits could
   make `write-flash` fail; esptool can clear them, but that is a
   status-register write outside the owner's limit, so it would be the
   owner's decision.
2. **A wrong IO MUX index takes a flash pad** (a coding error in S1). Low;
   the reviewer checks the indices against the ROM's table. A chip reset
   restores the pad.
3. **A wrong MMU index replaces the kernel's flash pages** (a coding error
   in S12). Low; the entry read-back does not catch a wrong index, the next
   fetch faults. A chip reset restores the map.
4. **PSRAM and the flash drive a shared line at once.** Only if the
   controller drove D0-D3 for CS1 while the flash drove them; with CS0 high
   the flash's outputs are off. Very low. Chip reset.
5. **The flash's HOLD#/WP# toggled by octal data while CS0 is high.** On a
   quad flash whose quad-enable bit is clear, IO2 and IO3 are HOLD# and WP#;
   HOLD# is ignored with CS# high and WP# only guards status writes. From
   general SPI-flash behaviour, not this chip's datasheet. Low, and harmless
   if so.
6. **VDD_SPI at 1.8 V for a 3.3 V flash.** Nothing here writes
   `RTC_CNTL_SDIO_CONF`; the voltage is the GPIO45 strap or an eFuse
   (§8.4). Not from this code.
7. **A reset loop.** The watchdogs are off and every stop halts. None.
8. **Download mode lost.** Needs an eFuse or a damaged flash. Not from this
   code.

### 8.2 What a process can do with PSRAM that it could not before

The tier is flat: a process can already write any register, including SPI1
(and so erase the flash), the MMU table and the IO MUX (ABI.md §5e,
README). PSRAM adds:
- **Executable PSRAM.** A process can write code into PSRAM through DBUS and
  jump to its IBUS alias at 0x4300_0000 + offset, since one MMU table serves
  both buses. On the plain board it could already execute code it wrote
  into IRAM (ABI.md §3c, `bus_probe`). Not a new class. The loader never
  accepts PSRAM code (§4.2).
- **Damage to every PSRAM process at once.** Rewriting SPI0's PSRAM
  registers, MR0, or the MMU entries 256..383 corrupts or unmaps every
  process in PSRAM, and a kernel access to them then faults in kernel mode,
  which stops the machine. On the plain board a process could already
  overwrite every other process's SRAM. Same class, wider blast.
- **Timing interference** with pinned processes through the shared data
  cache and the kernel's masked copies (§7). New, and the reason pinning is
  weaker than it sounds.

### 8.3 The kernel disabling or reconfiguring the cache

The kernel never does, after S12. If a later unit writes flash at run time
(an update, a log), ESP-IDF's rule applies: SPI1 flash operations run with
the caches suspended, so for their duration no PSRAM byte is reachable; the
kernel must then not touch PSRAM, every process in PSRAM is stalled, and
suspension must keep the dirty lines (`Cache_Suspend_DCache`, not a
disable-and-invalidate). That unit needs its own design; this one adds no
cache operation after boot.

### 8.4 Power

The PSRAM is inside the package and shares VDD_SPI with the flash. VDD_SPI
is 3.3 V or 1.8 V by the GPIO45 strap unless an eFuse forces it ([IDF]
`rtc_init.c`, `rtc_vddsdio_get_config`: `RTC_CNTL_SDIO_FORCE`, else
`GPIO_STRAP` bit 5 picks 1.8 V). The part's eFuse says AP_3v3 and the flash
is 3.3 V, so GPIO45 must read low at reset: a board fact for the pin plan,
not something the kernel sets. The kernel writes no supply register; P0
prints `RTC_CNTL_SDIO_CONF_REG` (0x6000_807C) and `GPIO_STRAP` so the board
record holds what set it. Current: the PSRAM's active draw is in its
datasheet, not read; the USB supply already powers the flash on the same
rail.

---

## 9. Board stages

Every stage: an image built with `--emit-flash`, sent to the reviewer with
its SHA-256 and the expected text; the only write is `write-flash 0x0`; no
eFuse burn or write, no erase; if the port is busy, say so and stop.
Flashing only on the owner's word. "Emulator" lines are what the smoke
asserts; `<v>` is a value the part prints and the record keeps.

**P0. `s3_mem_probe` (reads only; changes nothing).** A harness kernel that
loads and prints registers and stores nothing but UART bytes. No CS1, no
command, no MMU write. Expected:

```
SOS memprobe: efuse sys3=<v> sys4=<v> PSRAM_CAP=<v> PSRAM_VENDOR=<v>
SOS memprobe: strap=<v> sdio_conf=<v> sysclk=<v> cpu_per=<v>
SOS memprobe: spi0 clock=<v> sram_clk=<v> core_clk_sel=<v> ctrl=<v>
              user=<v> user1=<v> user2=<v> misc=<v> fctrl=<v> sctrl=<v>
              sram_cmd=<v> drd=<v> dwr=<v> ddr=<v> smem_ddr=<v>
              smem_ac=<v> date=<v>
SOS memprobe: spi1 (the same fields)
SOS memprobe: dcache ctrl=<v> ctrl1=<v> autoload=<v> icache ctrl=<v>
SOS memprobe: mmu 0=<v> 32=<v> 127=<v> 128=<v> 255=<v> 256=<v> 383=<v>
              511=<v>
SOS memprobe: iomux pin_ctrl=<v> 26=<v> 27=<v> ... 37=<v> in_sel 7..11=<v>
SOS memprobe: pad_hold=<v> ace0=<v> rom_dummy_plus spi0=<v> spi1=<v>
SOS memprobe: ccount per 1600000 systimer ticks=<v>
SOS-S3: halt pass
```

(each wrapped line is one line.) It tells the owner, before any
description claims it: whether the eFuse says 8 MiB AP 3.3 V; the clock
divider and core clock (§3); the data cache's size and line; that MMU
entries 256..383 are the ROM's flash pages; the pads' state; the VDD_SPI
source; and the ROM's extra dummy byte per SPI (`rom_spiflash_legacy_data`,
pointer at 0x3FCE_FFE4, bytes at +24). It can be a section of SO-77's U0
probe if that is not yet built.

**P1. `psram_identity`.** S0, S1, S2, S5, S6, S8 (reads and checks only),
S10. No write to the chip, no SPI0 change, no map.

```
SOS psram: efuse PSRAM_CAP=0x1 PSRAM_VENDOR=0x1
SOS psram: SPI1 MISC=0x00000001 before the first command
SOS psram: MR0=<v> MR1=0x0d MR2=<v> MR3=<v> MR4=<v> MR8=<v>
SOS psram: SPI1 MISC=0x00000002 after
SOS-S3: halt pass
```

Emulator: `MR0=0x09 MR1=0x0d MR2=0x93 MR3=0x20 MR4=0x20 MR8=0x05`. On the
part MR1 must be 0x0d and MR2 must have bit 7 set and bits [2:0] = 3; any
other answer ends the series until it is understood. The next stage's flash
also shows the flash untouched.

**P2. `psram_mode`.** P1 plus the MR0 write and its read-back.

```
SOS psram: MR0=<v> before, 0x28 written, MR0=0x28 after
SOS-S3: halt pass
```

(the low six bits; the top two are kept.)

**P3. `psram_word`.** P2 plus S3, S4, S11, S12, then one word: store
0x5aa5c33c at 0x3D00_0000, store to 128 KiB of other PSRAM lines to evict
it, load it back.

```
SOS psram: mapped 0x3d000000-0x3d7fffff, entries 0x100-0x17f read back
SOS psram: word at 0x3d000000 0x5aa5c33c after eviction, misses=<v>
SOS-S3: halt pass
```

The first stage in which a cache fill reaches the chip.

**P4. `psram_test`.** The full sequence, S0..S15, then a width probe in the
`bus_probe` style at PSRAM addresses: byte, halfword and word stores and
loads, an unaligned word load, `s32c1i` under the ROM's `ATOMCTL` 0x15, and
a word load at the IBUS alias.

```
SOS-S3: psram 8 MiB at 0x3d000000, MR0=0x28 MR1=0x0d MR2=<v> MR3=<v>,
read-back ok
SOS psram: test took <v> us
SOS probe: byte store and load, PSRAM at 0x3d000003
SOS probe: ok 0x00000096
... (one pair per access, as README's bus_probe stage)
SOS probe: word store, then s32c1i swapping it, PSRAM at 0x3d000010
SOS probe: ok 0x00000005
SOS-S3: halt pass
```

`s32c1i` on cached external memory is unprobed anywhere; a refusal there
(cause 3) would mean processes in PSRAM cannot use the compiler's inline
word atomics, and the layout would need SRAM for any process that does.

**P5. `psram_speed`.** P4's bring-up, then §7's five measurements, printed.

**P6. `psram_process`.** The r8 kernel with `image_held` (a hand-assembled
child with its stack at the top of a PSRAM destination) and unit D's
Blade-built child whose `.data`, `.bss`, arena and stack are in PSRAM, and
a pool in PSRAM. Expected: the success line, then the case's own text as in
README's stages 5 and 6, then `SOS-S3: halt pass`.

**P7. `psram_wrong_size`.** The r8 kernel built with a description that
says 16 MiB. Expected:

```
SOS-S3: psram stop: MR2 density = 0x3 (8 MiB), the board says 0x5 (16 MiB)
SOS-S3: halt fail code=<the S8 density code>
```

Then the plain board's `root_server_boot` is flashed and runs as in README
stage 5: the stop path works on the part, and a halted bring-up leaves
nothing behind.

---

## 10. Units of work

**A. The read-only probe (small; Sonnet).** `tests/s3_mem_probe.saw` (or a
section of SO-77's `s3_periph_probe`), its smoke case asserting the
emulator's values, the board stage P0, the results recorded in ABI.md.
Proves what the part leaves; decides §3's divider. Can run beside SO-77's
U0 now, or be U0.

**B. The command layer (medium; Sonnet, Opus review of the register
words).** `hal/xtensa-esp32s3/kernel/mspi.c` in `.iram`: S0, S1, S2, S5,
S6, S7, S8, S9, S10; `esp32s3.ld` adds `.text.iram`/`.literal.iram` to
`.iram`. Harness kernels `psram_identity` and `psram_mode`. Runner: an arch
key for extra QEMU arguments, a per-run eFuse file, and a trace assertion
("the flash model is selected exactly once"). Smoke cases: the identity
lines; the stops for no eFuse file, the quad model, no `-m`, `-m 4M` and
`-m 16M`; the trace case. Its mutation check: removing the gate turns the
trace case red. Board stages P1, P2.

**C. The map, the ranges and the board (medium-large; Opus for the spec
text, Sonnet to build).** S3, S4, S11..S15; the `hal.memory_init()` seam in
`kernel/main.saw` and a no-op in every HAL; spec §5b's HAL list and §5b.1's
S3 row; the facade board `esp32s3-r8` with `psram_bytes`, the two-range
`map_source_ok`/`map_target_ok`/`segment_bus_ok`, `FLASH_WINDOW_LEN` 16 MiB
on both boards; `ESP32S3_R8_ARCH` with pool and hand-assembled child in
PSRAM; smoke cases `psram_boot`, `psram_probe` (widths), the MMU and
read-back stops (a harness kernel that maps two entries to one page), the
gate's process cases on the r8 board; ABI.md gains a PSRAM section and §11
entries. Board stages P3, P4, P5, P7. Needs the AP datasheet's
chip-select-low limit first (§3).

**D. A process in PSRAM (small-medium; Sonnet).** `user/child_psram.ld`, one
test package naming it, its smoke case on the r8 board; spec §5.6's "a
writable one copied to DRAM" becomes "to process RAM"; board stage P6.

**Against SO-95 and SO-77.** SO-95's order puts its U3 (the description
formats) beside SO-102: unit A and B fit beside U3, and U3 writes both
boards' `board.toml` with §4.1's ranges and no `probed`. SO-95's U4
(generated link addresses) comes after unit C, and then makes unit D's
hand-written PSRAM script one of the generated ones. SO-77's U0 and unit A
share a harness and a flash. The box's first run still needs none of this
(SO-95 §6.5).

---

## 11. Alternatives, questions, and what was checked

### 11.1 Alternatives rejected

- **Calling the ROM's PSRAM routines from a windowed shim at boot.** The ROM
  is Espressif's own code, but it is windowed, the kernel is call0, and the
  routines read function tables in ROM RAM at 0x3FCE_FFE4-0x3FCE_FFF8,
  inside the process range. The register-level path is a dozen writes per
  command and composes the ROM's words exactly (§6.3).
- **ESP-IDF's full sequence with low/high-speed modes and tuning.** It
  writes SPI0's flash clock, the shared core clock and the shared timing
  registers. Not needed at 40 MHz or below, and it is the part of the
  sequence that can disturb the flash.
- **An automatic fallback to SRAM, or a second layout.** Ruled out by the
  owner.
- **Running the bring-up in `boot.S` before `kmain`.** Its stops would print
  before the banner, without the console. The HAL seam after the banner
  keeps every line in the kernel's voice, and the C part still runs from
  IRAM.
- **Running the SPI1 commands from flash.** ESP-IDF does not (`noflash`), and
  the sources read do not say how SPI0 and SPI1 share the bus.
- **ECC.** It costs 1/16 of the size and adds page-corner rules
  (`s_configure_psram_ecc`), for no gain at this speed.
- **Executing processes from PSRAM.** Not needed (code runs in place from
  flash), and it would need instruction and data cache coherence.
- **PSRAM at 0x3C80_0000, right above an 8 MiB flash.** It would tie the
  PSRAM's address to the flash's size; 0x3D00_0000 holds for flash up to
  16 MiB.
- **A per-process MMU mapping at one link address.** Noted in the issue as a
  different tier of port; not proposed.

### 11.2 Questions for the owner

1. **Two boards (`esp32s3`, `esp32s3-r8`), the second a facade?**
   Recommended: yes (§5.4).
2. **Should the plain board refuse a part whose eFuse says it has PSRAM?**
   Recommended: no. The plain board claims nothing about PSRAM, and the rule
   is that every claim holds; refusing would also stop the owner's part from
   running the plain image, which is the control in P7.
3. **What the identity check compares.** Recommended: the eFuse capacity
   and vendor, MR1's vendor, MR2's good-die bit and density; print MR3's
   voltage bit and the generation, not check them. The emulator's model
   reports 1.8 V where the part's eFuse says 3.3 V, so checking the voltage
   would need the emulator to describe a different chip than the board.
4. **The PSRAM clock.** Recommended: 20 MHz, set from P0's reading, with no
   PLL or core-clock change in this unit.
5. **The read-back test at every boot.** Recommended: the full 8 MiB, as
   ESP-IDF's default does, priced by P4; reduce it only if the measured time
   hurts.
6. **Root's region.** Recommended: stays in SRAM by default (SO-95 rule
   5.10), now a choice rather than a constraint.
7. **The board stages.** Recommended: P0 first, folded into SO-77's U0 if U0
   has not run; P1..P7 one flash each, in order, each only after the last
   was read.

### 11.3 What I verified by running

- The emulator's PSRAM: switched on by `-m`, octal only with
  `-global ssi_psram.is_octal=true`; mode registers as in §6.2; the MR0
  write; the map at entries 256..383; every width; the IBUS alias; the fault
  without PSRAM; the eFuse word with and without a file (§6).
- The design's SPI1 words equal the ROM's own for an MR read (§6.3), and the
  ROM's `esp_rom_opiflash_exec_cmd` reads the same registers through the
  same model.
- The CS gate: the flash model is selected once (by the ROM) with it, and
  sent 0x40 without it (§6.4).
- The emulator's ROM state: MMU entries identity-mapped to flash; SPI0 and
  the IO MUX not modeled (§6.2).

### 11.4 What I read

Every register address, field, value and command in §2 and §3, from the
files and functions named there: ESP-IDF v5.4.1 and the ROM ELF's
disassembly. The QEMU model's behaviour in §6.2 and §6.5 from its source at
the installed tag, then confirmed by the runs.

### 11.5 What I could not check

- Every value on silicon: what the ROM leaves in SPI0 and SPI1, the MSPI
  core clock, the data cache's size and line, the pads' state, the eFuse
  words on this part (P0 reads them).
- The AP Memory datasheet: the chip-select-low limit (§3), the mode
  register meanings beyond ESP-IDF's struct, the chip's current draw.
- The S3 Technical Reference Manual: how SPI0 and SPI1 arbitrate, and that
  SPI0's PSRAM-side registers govern only CS1 (taken from field
  descriptions).
- `s32c1i` and unaligned loads on cached PSRAM (P4).
- The flash chip's own datasheet (HOLD#/WP# behaviour, §8.1).
- Whether pads 33 and 34 are brought out on the Freenove board; on the
  WROOM-1 module they are not pins of the module, which I read in no
  source here and which the board's pinout should confirm before any pin
  plan uses them. The pin plan's "35 to 37 clear" is narrower than what the
  octal PSRAM uses: 26 (CS1) and 33 to 37.
- The miss costs in §3 and §7 are estimates from ESP-IDF's latency
  constants, not measurements.
