# SO-102: the ESP32-S3's PSRAM (design)

For the owner and the adversarial reviewer, before anything is built. This
is revision 2, written against main at `891909e2`. Nothing in the
repository was changed. Section 12 answers the reviewer's six threads on
revision 1.

It takes in the owner's ruling of Nov 14 on the fallback: a board described
as having PSRAM brings it up or stops, and the part must match its
description exactly. Section 5 is written to that ruling.

Sources, all public, read for this design:

- **[IDF]** ESP-IDF tag `v5.4.1`, fetched from github.com/espressif/esp-idf:
  `components/esp_psram/esp32s3/esp_psram_impl_octal.c`,
  `components/esp_psram/esp_psram.c`, `components/esp_psram/linker.lf`,
  `components/esp_psram/esp32s3/Kconfig.spiram`,
  `components/esp_hw_support/mspi_timing_tuning.c`,
  `components/esp_hw_support/port/esp32s3/mspi_timing_config.c` and
  `mspi_timing_tuning_configs.h`,
  `components/esp_hw_support/port/esp32s3/rtc_init.c`,
  `components/esp_system/port/soc/esp32s3/clk.c`,
  `components/hal/mmu_hal.c`,
  `components/hal/esp32s3/include/hal/` `mmu_ll.h`, `cache_ll.h` and
  `mspi_timing_tuning_ll.h`,
  `components/soc/esp32s3/include/soc/` `ext_mem_defs.h`, `soc.h`,
  `soc_caps.h`, `spi_pins.h`,
  `components/soc/esp32s3/register/soc/` `spi_mem_reg.h`, `extmem_reg.h`,
  `io_mux_reg.h`, `efuse_reg.h`, `syscon_reg.h`, `rtc_cntl_reg.h`,
  `system_reg.h`, `reg_base.h`,
  `components/efuse/esp32s3/esp_efuse_table.csv`,
  `components/esp_rom/esp32s3/include/esp32s3/rom/opi_flash.h`,
  `components/esp_rom/include/esp_rom_spiflash.h`,
  `components/esp_mm/esp_mmu_map.c`,
  `components/esp_system/port/cpu_start.c`,
  `components/esp_system/ld/esp32s3/memory.ld.in`,
  `components/spi_flash/flash_ops.c`.
- **[ROM]** The mask ROM's ELF, `~/.espressif/tools/esp-rom-elfs/20241011/
  esp32s3_rev0_rom.elf`, disassembled with the installed
  `xtensa-esp32s3-elf-objdump`: `main` (0x4004_3A2C),
  `spi_flash_boot_attach` (0x4004_A8CC), `SPI_init` (0x4004_A4A8),
  `spi_common_set_flash_cs_timing` (0x4004_A3A4), `spi_dummy_len_fix`
  (0x4004_94F4), `esp_rom_spiflash_config_clk` (0x4004_A9B0),
  `ets_run_flash_bootloader` (0x4004_578C), `esp_rom_opiflash_exec_cmd`
  (0x4004_E058), `esp_rom_spi_cmd_start` (0x4004_E0D0),
  `esp_rom_spi_cmd_config` (0x4004_DD9C), `esp_rom_spi_set_op_mode`
  (0x4004_DBF4) and its jump table at 0x3FF1_90EC,
  `esp_rom_spi_reset_rw_mode` (0x4004_DB94), `esp_rom_opiflash_backup_reg`
  and `recover_reg`, `esp_rom_spi_set_dtr_swap_mode` (0x4004_E1A0),
  `esp_rom_opiflash_pin_config` (0x4004_D3CC) with its tables at
  0x3FF1_90A8, and the pad helpers `gpio_pad_pullup`,
  `gpio_pad_input_enable`, `gpio_pad_unhold`, `gpio_bypass_matrix_in`.
- **[QEMU]** espressif/qemu at tag `esp-develop-9.2.2-20250817` (the
  installed emulator's build): `hw/xtensa/esp32s3.c`,
  `hw/misc/esp32s3_cache.c`, `hw/misc/ssi_psram.c`, `hw/ssi/esp32s3_spi.c`,
  `hw/nvram/esp_efuse.c`.
- **[RUN]** My own runs of the installed emulator and of the installed
  compiler and linker (sections 2.2 and 6).
- **[TREE]** This repository.

Not read: the ESP32-S3 Technical Reference Manual and the AP Memory
datasheet for the octal PSRAM. Where a fact would come from them, the text
says so.

---

## 0. Verdict

1. **The bring-up is small and every write in it is volatile.** About fifty
   register writes and six commands to the RAM chip, all to the memory-SPI
   controller, the IO MUX, the GPIO input matrix, the cache's MMU table and
   the RAM chip's mode register. Each value is taken from ESP-IDF's octal
   driver or the ROM's own code (§2). Every write ESP-IDF makes on its way
   to PSRAM is listed, kept or dropped with its reason (§2.1).
2. **The flash is never addressed.** Every command goes out on SPI1 with chip
   select 0 disabled in `SPI_MEM_MISC_REG(1)`, read back before each
   command starts. No flash-side register of SPI0 is written, nor the
   shared core clock, nor any pad the flash uses. A mutant that leaves CS0
   on is caught by the emulator's flash trace (§6.4). Nothing writes an
   eFuse, an RTC register, or any non-volatile state of either chip (§2.5).
3. **One call does the whole controller part, from IRAM, and compares as it
   goes.** Identity is checked before anything is written to the chip, every
   wait is bounded and has its own stop, and nothing in flash runs between
   the first command and the last restore (§2.2). A build check fails if
   any piece of that code, its constants, a jump table or a compiler helper
   lands in flash; it was run against a scratch build and its five mutants
   (§2.2).
4. **No tuning, no clock change, and a contract with whoever changes the
   clock.** The ROM's own boot path leaves SPI0's and SPI1's clocks at
   divide by 4 (§1.1) and, by its code, the system clock on the crystal.
   The commands and the cache's PSRAM fills run at a divider this design
   sets on SPI1 and on SPI0's PSRAM side, aiming at 20 MHz; SPI1's is
   restored after. ESP-IDF tunes octal PSRAM only above 40 MHz. What the
   memory-SPI core clock is on the crystal is not in the sources and is
   read on the part. A later switch to the PLL (SO-103's K4) must come
   before `hal.memory_init()`, the description names the clock, and S0.3
   stops on any difference (§3).
5. **PSRAM maps at DBUS 0x3D00_0000, 8 MiB** (MMU entries 256 to 383). The
   flash window the HAL accepts shrinks from 32 MiB to 16 MiB. Processes'
   writable memory, stacks and pools go there; their code stays in flash;
   the kernel stays in SRAM (§4).
6. **No fallback.** The PSRAM board stops at the first of seventeen checks
   that disagrees with its description, with one line naming what it read
   and what the description says (§5.2).
7. **Two boards, one HAL.** `esp32s3` describes no PSRAM and never touches
   CS1; `esp32s3-r8` describes the 8 MiB octal AP part (§5.4).
8. **The emulator models enough to test almost all of it** (run, §6): the
   mode registers of an AP part, the map, every width, the eFuse words from
   a file, and in its flash trace whether CS0 was ever driven. Not SPI0, the
   IO MUX, line modes, timing or the cache itself.
9. **Speed.** A miss is a serial burst, an estimated 1.5 to 4 us against one
   cycle for SRAM (§7). Pinning a process to SRAM keeps its stack and data
   fast, not its read-only data.
10. **Eight board stages**, the first writing nothing beyond what every
    image's `_start` writes (§9), and **four units** (§10).

---

## 1. Today

### 1.1 What the ROM and `boot.S` leave on the memory-SPI controller

- **The ROM's flash boot path** ([ROM]): `main` reads the strap, calls
  `spi_flash_boot_attach(spiconfig, 0)` then `ets_run_flash_bootloader`
  (0x4004_3AAC-0x4004_3AB6). `spi_flash_boot_attach` calls
  `esp_rom_config_pad_power_select`, `SelectSpiFunction` (quad flash),
  `SPI_init(5, 4)`, `esp_rom_spiflash_config_readmode(5, ...)` and
  `SPI_WakeUp`.
- **`SPI_init(mode, freqdiv = 4)`** writes, by literal address,
  `SPI_MEM_CLOCK_REG(1)` (0x6000_2014) and `SPI_MEM_CLOCK_REG(0)`
  (0x6000_3014) with the same word: for a divider of 4, `(3 << 16) |
  (1 << 8) | 3` = `0x0003_0103` (0x4004_A538-0x4004_A56B). It writes
  `SPI_MEM_CTRL_REG(1)` = `0x0020_8000`. Through
  `spi_common_set_flash_cs_timing` it also writes SPI0's PSRAM chip-select
  timing, `SPI_MEM_SPI_SMEM_AC_REG(0)`: `CS_SETUP` and `CS_HOLD` set,
  `CS_HOLD_TIME` 2, `CS_SETUP_TIME` 1 (0x4004_A3DE-0x4004_A421). It calls
  `spi_dummy_len_fix` for SPI0 and SPI1, which stores 0 in the ROM's
  per-SPI extra-dummy byte whatever it finds (0x4004_94F4-0x4004_950B).
- **The direct-boot branch** of `ets_run_flash_bootloader` (byte 0 not
  0xE9) never reaches `ets_config_flash_by_image_hdr`, the one routine that
  changes the clock from an image header (its only call, 0x4004_58A7, is on
  the esptool-header branch). So at handover SPI0's and SPI1's flash clocks
  are divide by 4 of the MSPI core clock, from the ROM's code.
- **What the ROM's code does not name:** `SPI_MEM_SRAM_CLK_REG(0)`
  (0x6000_3050) and `SPI_MEM_CORE_CLK_SEL_REG` (0x6000_30EC) appear nowhere
  as a literal in the ROM's disassembly, so they are presumably at their
  reset values: `SRAM_CLK` divide by 4, `CORE_CLK_SEL` 0 ([IDF]
  `spi_mem_reg.h` defaults). Some ROM routines compute an SPI base from a
  number (`esp_rom_spiflash_config_clk` does), so a literal search is not
  proof; stage P0 reads both.
- **The cache's MMU.** One table of 512 entries of 64 KiB serves both buses:
  entry n is DBUS 0x3C00_0000 + n x 64 KiB and IBUS 0x4200_0000 + n x 64 KiB
  ([IDF] `ext_mem_defs.h`: "I/D share the MMU linear address range";
  `SOC_MMU_ENTRY_NUM 512`; `DR_REG_MMU_TABLE 0x600C5000` in `soc.h`). An
  entry is a page number, bit 14 set for invalid, bit 15 set for PSRAM
  (`mmu_ll_write_entry`). `ets_run_direct_boot` maps all 512 to flash pages
  0..511 (ABI.md §2a). [RUN] under the emulator, every entry read holds its
  own index, flash, valid (§6.2).
- **`boot.S`'s `_start`** writes no MSPI, cache, MMU or IO MUX register
  ([TREE] `kernel/boot.S`); nor does `kmain` (`kernel/main.saw`).
- **The emulator** runs the real ROM, and its SPI1 reads
  `CLOCK=0x00030103 CTRL=0x00208000`, as `SPI_init` writes them. It does not
  model SPI0 (every SPI0 register reads 0, [RUN]).
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
RAM). The flash image is a little over 2 MiB (`esp32s3.ld`: root's slot at
0x10_0000, a child's at 0x18_0000, `.drom` at 0x20_0000).

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

The caches' own memory is internal SRAM the layout already avoids: the
instruction cache uses 0x4037_0000-0x4037_7FFF and the data cache
0x3FCF_0000-0x3FCF_FFFF ([IDF] `memory.ld.in`, lines 18-25 and
`DCACHE_SIZE 0x10000`). `RAM_TOP` stops at 0x3FCF_0000. PSRAM traffic,
which lives in the data cache, costs no SRAM.

### 1.4 What "RAM" means to the kernel on this board

One range, `RAM_BASE` 0x3FCB_0000 to `RAM_TOP` 0x3FCF_0000 ([TREE]
`lib.saw`). `map_target_ok`, `map_source_ok` and `segment_bus_ok`'s copy
branch all answer "lies inside it". `map_source_ok` is asked for a
`ProcessCreate` destination, a slab donation and a mapping's backing
(`kernel/core/dispatch.saw` around lines 1104, 1281, 3158), `map_target_ok`
for a mapping's target (line 3072). The boot region table's `Ram` rows are
minted without a range check (`kernel/core/process.saw`,
`mint_boot_regions`); they are judged when used. Addresses are identity
everywhere (`phys_to_virt`, `map_place`), and `prot_update` asserts that a
row's `base` and `pa` name the same bytes.

---

## 2. The bring-up sequence

### 2.1 Every write ESP-IDF makes on its way to PSRAM

The reference configuration is ESP-IDF v5.4.1's defaults for an ESP32-S3
with octal PSRAM: `SPIRAM_MODE_OCT`, `SPIRAM_SPEED_40M` (the default,
`Kconfig.spiram`), ECC off, `SPIRAM_MEMTEST` on, no instructions or
read-only data in PSRAM, a quad flash at 80 MHz STR. In that configuration
neither the flash nor the PSRAM needs tuning
(`mspi_timing_tuning_configs.h`: flash STR tunes above 80 MHz, PSRAM DTR
above 40 MHz) and the MSPI core clock is 80 MHz (`MSPI_TIMING_CORE_CLOCK_MHZ
80`, the `#else` branch).

The path is `call_start_cpu0` in `cpu_start.c` from the cache setup to the
memory test. Each row: the write, the function that makes it, and what this
design does with it. "Kept" names the step of §2.3; "dropped" gives the
reason and the state the register is then left in; "n/a" is a write that
belongs to something other than the PSRAM (the flash, the heap, the
bootloader's own map).

**A. Cache configuration, before the pins** (`cpu_start.c`, S3 branch)

| write | from | here |
|---|---|---|
| instruction cache size, ways, line (EXTMEM) | `rom_config_instruction_cache_mode` | dropped: the ROM's configuration stays; changing a cache's shape while executing through it is what this design avoids. P0 reads `EXTMEM_ICACHE_CTRL_REG`. |
| data cache suspend | `Cache_Suspend_DCache` | dropped with the next row. |
| data cache size, ways, line (EXTMEM) | `rom_config_data_cache_mode` | dropped: the ROM's shape stays; P0 reads `EXTMEM_DCACHE_CTRL_REG` (size bit 2, block size [4:3]). The read-back test (S14) spans 128 times the largest data cache, so it holds for any shape. |
| data cache resume | `Cache_Resume_DCache` | dropped: nothing suspends. |
| ROM driver's MMU split | `Cache_Set_IDROM_MMU_Size` | n/a: bookkeeping for the ROM's mmap driver, which this kernel never calls. |

**B. Pins** (`esp_mspi_pin_init`, `flash_ops.c`)

| write | from | here |
|---|---|---|
| pads 33-37: pull-up on, pull-down off, `MCU_SEL` 4, input enable | `esp_rom_opiflash_pin_config` (ROM, default branch) | kept, S1. |
| pads 33-37: hold cleared in `RTC_CNTL_DIG_PAD_HOLD_REG` | same, `gpio_pad_unhold` | dropped in favour of a check, S0.2: no RTC-domain register is written; a held pad stops the boot. At power-on no pad is held. |
| `GPIO_FUNCn_IN_SEL_CFG` bit 7 cleared, n = 7..11 (D4-D7, DQS) | same, `gpio_bypass_matrix_in` | kept, S1. |
| pads 27-32 (the flash's HD, WP, CS0, CLK, Q, D): pull-up, `MCU_SEL` 0, input enable; bypass for signals 0-3 | same | dropped: the flash's pads, which `SelectSpiFunction` has already set for the running flash. They stay as the ROM's boot left them; P0 prints their IO MUX words. |
| `SPI_MEM_DATE_REG(0)`: `SPICLK_PAD_DRV_CTL_EN` = 1, `SMEM_SPICLK_FUN_DRV` = 3, `FMEM_SPICLK_FUN_DRV` = 3 | `mspi_timing_set_pin_drive_strength` -> `mspi_timing_ll_set_all_pin_drive(0, 3)` | dropped: the register sets the flash's clock-pad drive too. Left at its reset value (`CTL_EN` 0), the clock pad's drive comes from pad 30's own IO MUX `FUN_DRV` for both chips (`spi_mem_reg.h`, `SPICLK_PAD_DRV_CTL_EN` description); P0 prints it. |
| pads 27, 28, 31, 32: `FUN_DRV` = 3 | same | dropped: the flash's data pads (shared with the PSRAM's D0-D3). They keep the drive the flash already runs on at this clock; P0 prints it. |
| pads 33-37: `FUN_DRV` = 3 | same | kept, S1. |

ESP-IDF's comment on the drive: "For now, set them all to 3. Need to check
after QVL test results are out." Its driver runs these pads at 40 to 120
MHz; this design runs them at 20 MHz or less (§3), with the flash's pads at
the drive the flash itself uses. Stage P1 is the evidence.

**C. Between the pins and the PSRAM** (`cpu_start.c`)

| write | from | here |
|---|---|---|
| flash ID read on SPI1 | `bootloader_flash_update_id` | n/a: a command to the flash. |
| RTC power, regulators, reference calibration | `esp_rtc_init` -> `rtc_init` (`clk.c`, `rtc_init.c`) | dropped: whole-chip RTC-domain setup; ESP-IDF's comment ties it to the timing tuning that follows, which this design does not do. No RTC register is written here. |
| flash chip state (HPM) | `spi_flash_init_chip_state` | n/a: the flash. |
| flash tuning | `mspi_timing_flash_tuning` | n/a: the flash, and empty at 80 MHz STR. |
| none (software map of regions) | `esp_mmu_map_init` | n/a. |

**D. `esp_psram_impl_enable`** (`esp_psram_impl_octal.c`), in its order

| write | from | here |
|---|---|---|
| pad 26 `MCU_SEL` = 0 (`FUNC_SPICS1`) | `s_init_psram_pins` | kept, S1. |
| pad 26 `FUN_DRV` = 3 | `s_init_psram_pins` | kept, S1. |
| `SPI_MEM_DATE_REG(0)` `SMEM_SPICLK_FUN_DRV` = 3 | `s_init_psram_pins` | dropped: inert while `SPICLK_PAD_DRV_CTL_EN` is 0, and this design leaves that bit (row B). |
| `SPI_MEM_SPI_SMEM_AC_REG(0)`: `CS_HOLD`, `CS_SETUP` = 1; `CS_HOLD_TIME` 3; `CS_SETUP_TIME` 3; `CS_HOLD_DELAY` 2 | `s_set_psram_cs_timing` | kept, S2. The ROM's `SPI_init` already wrote this register (§1.1). |
| `SYSCON_SRAM_ACE0_ATTR_REG` bit 8 cleared | `s_configure_psram_ecc` (ECC off) | kept, S3, as check-then-clear. |
| `SPI_MEM_CORE_CLK_SEL_REG` = 0 (80 MHz) | `mspi_timing_enter_low_speed_mode` -> `mspi_timing_config_set_flash_clock` and `_set_psram_clock` -> `mspi_timing_ll_set_core_clock(0, 80)` | dropped: shared by SPI0 and SPI1 and by both chips. The ROM's code never names it, so it holds its reset value 0, the same value ESP-IDF writes here. S0.3 reads it and stops if it is not the value P0 recorded. |
| `SPI_MEM_CLOCK_REG(0)` = divide by 4 | same, `mspi_timing_ll_set_flash_clock(0, 4)` | dropped: SPI0's flash clock. Left at the ROM's `0x0003_0103` (§1.1), which is the same value. |
| `SPI_MEM_CLOCK_REG(1)` = divide by 4 | same, `mspi_timing_ll_set_flash_clock(1, 4)` (the `control_spi1` flag) | kept, S5: saved, set to the PSRAM divider, restored at S10. The commands of S8 and S9 run at this clock. |
| `SPI_MEM_SRAM_CLK_REG(0)` = divide by 4 | same, `mspi_timing_ll_set_psram_clock(0, 4)` | kept, S4, at the divider of §3. |
| flash and PSRAM tuning registers cleared | `mspi_timing_*_config_clear_tuning_regs` | n/a: only when tuning is needed, which it is not. |
| `SPI_MEM_DDR_REG(1)` `VAR_DUMMY` = 1 | `esp_psram_impl_enable` | kept, S5. |
| `SPI_MEM_DDR_REG(1)` `WDAT_SWP`, `RDAT_SWP` = 0 | `esp_rom_spi_set_dtr_swap_mode(1, false, false)` | kept, S5. |
| SPI1 user command, MR0 read (CTRL, USER, USER1, USER2, ADDR, CACHE_FCTRL bit 1, MISO_DLEN, MISC, CMD; then USER, USER1, USER2, CTRL, DDR restored) | `s_init_psram_mode_reg` -> `esp_rom_opiflash_exec_cmd` | kept, S7 and S8.1. |
| the ROM's extra dummy, added to every command's dummy | `opi_flash_conf_cmd` (inside `exec_cmd`) | dropped: the dummy is 8 exactly. The ROM's `spi_dummy_len_fix` stores 0 there at boot (§1.1), so the ROM would add 0; P0 prints the byte. |
| SPI1 user command, MR0 write | `s_init_psram_mode_reg` | kept, S9 (after identity, not before: §2.3). |
| SPI1 user commands, MR8 read and write | `s_init_psram_mode_reg`, ECC only | n/a: ECC off. |
| SPI1 user commands, MR0/1, MR2/3, MR4, MR8 reads | `s_get_psram_mode_reg` | kept, S8. |
| none | `mspi_timing_psram_tuning` | n/a: empty at 40 MHz. |
| `SPI_MEM_CORE_CLK_SEL_REG` = 0 | `mspi_timing_enter_high_speed_mode` | dropped, as in the low-speed row. |
| `SPI_MEM_CLOCK_REG(0)` and `(1)` = flash speed (80 MHz: `CLK_EQU_SYSCLK`) | same, `set_flash_clock(80, ...)` | dropped: raises the flash's clock. SPI0's stays at the ROM's divide by 4; SPI1's is restored at S10 to the ROM's. |
| `SPI_MEM_SRAM_CLK_REG(0)` = 40 MHz (divide by 2) | same, `set_psram_clock(40, ...)` | replaced by S4's divider (20 MHz target, §3). |
| tuning registers set | same, only when tuning | n/a. |
| `SPI_MEM_DDR_REG(1)` `VAR_DUMMY` cleared | `spi_flash_set_rom_required_regs` | n/a: octal flash only; this flash is quad. S10 restores the whole of `DDR(1)` anyway. |
| `SPI_MEM_CACHE_FCTRL_REG(1)` `CACHE_USR_CMD_4BYTE` = 0 | `spi_flash_set_vendor_required_regs` (quad-flash branch: "set back MSPI registers after Octal PSRAM initialization") | kept as S10's restore of `CACHE_FCTRL(1)` to its value before S5, which undoes the same bit S7 sets. |
| SPI0 PSRAM phases: `CACHE_SCTRL(0)`, `SRAM_DRD_CMD(0)`, `SRAM_DWR_CMD(0)`, `SMEM_DDR(0)`, `SRAM_CMD(0)` | `s_config_psram_spi_phases` | kept, S11, every field. |
| data cache resume | `s_config_psram_spi_phases` -> `Cache_Resume_DCache(0)` | dropped: nothing suspends the cache. |

**E. The map and the test** (`esp_psram.c`, `cpu_start.c`)

| write | from | here |
|---|---|---|
| MMU entries for the PSRAM pages, `page | BIT(15)` | `s_psram_mapping` -> `mmu_hal_map_region` -> `mmu_ll_write_entry` (no other write: `mmu_hal.c`) | kept, S12, entries 256..383. |
| `EXTMEM_DCACHE_CTRL1_REG` `SHUT_CORE0_BUS` (and core 1's) cleared | `cache_ll_l1_enable_bus` | kept as a check for core 0 (S12.3): the data bus is already on, since flash data reads work. Core 1's: n/a, held in reset (ABI.md §11). |
| every eighth word of the PSRAM, written then read | `esp_psram_extram_test` -> `s_test_psram` | kept, S14. |
| heap regions | `esp_psram_extram_add_to_heap_allocator` | n/a. |
| `.ext_ram.bss` zeroed | `esp_psram_bss_init` | n/a. |
| every MMU entry invalidated, by the second-stage bootloader | `mmu_hal_init` -> `mmu_hal_unmap_all` | n/a: this kernel has no second-stage loader. The ROM's flash entries outside the kernel's window (33..255, 384..511) stay mapped to flash pages past the image, which nothing in the kernel reads. |

Nothing ESP-IDF writes on this path is absent from the tables. What the
design adds that ESP-IDF does not do: the eFuse, pad-hold, core-clock and
idle checks of S0, the read-back of the gate before each command, the
bounds on every wait, the read-back of every MMU entry, and the save and
restore of every SPI1 register S5 to S9 touch, `CLOCK(1)`, `ADDR(1)`,
`MOSI_DLEN(1)`, `MISO_DLEN(1)`, `CACHE_FCTRL(1)` and `MISC(1)` included.

**Mechanism.** The ROM's routines are windowed and keep their state in ROM
RAM at 0x3FCE_FFE4-0x3FCE_FFF8 (`rom_spiflash_legacy_data`,
`rom_spi_usr_cmd_legacy_funcs`, `rom_opiflash_cmd_def`), inside today's
process range; the kernel is call0 and calls no ROM code (ABI.md §2b). So
the design writes the registers itself, and §6.3 shows the words it
composes equal the ROM's own on the emulator.

### 2.2 Where it runs, and how the build proves it

**One call.** A new HAL seam, `hal.memory_init()`, is called by `kmain` after
the banner and before `hal.intc_init()`; every other HAL answers it with
nothing. On the PSRAM board it makes one call into C,
`sos_psram_bringup(expect, result, last_step)`, which runs S0 to S12 and
returns. Every comparison of S0 to S12 is made inside that call, between
the steps, so:
- nothing is written to the RAM chip before its identity has been compared
  with the description (S8 before S9), and nothing after a failed check
  runs at all;
- no code in flash runs between S1 and the return, so SPI0 fetches nothing
  while SPI1 holds the gate or a command is in flight;
- the record the call fills in names the step that stopped (its code), the
  value read, the value expected and one more word (the command, or the
  entry); Saw prints the line of §5.2 from it and calls `exit_fail`.

`expect` holds the description's facts (the eFuse codes, the vendor, the
density, the divider word, and the system clock it was chosen for:
`SOC_CLK_SEL`, `PLL_FREQ_SEL`, `CORE_CLK_SEL`); `result` is the
record; `last_step` lets the harness kernels of stages P1 and P2 end the
call after S8 or S9 (S10 still runs). After the call returns, Saw runs
S14 (the read-back test, from flash, with SPI1 restored and idle) and S15.

**What must be in IRAM or DRAM.** `hal/xtensa-esp32s3/kernel/mspi.c`, built
with the board's C flags (`ESP32S3_ARCH["cc_args"]`: call0, no FPU,
`-ffunction-sections`, `-fdata-sections`, `-fno-builtin`):
1. Every function in the file carries `section(".text.iram")`. The compiler
   then puts each function's literal pool in `.literal.iram` ([RUN], below),
   and `esp32s3.ld`'s `.iram` output section gains
   `*(.literal.iram .text.iram)` beside the vectors, so both are copied to
   IRAM by `_start` with the vectors.
2. No `const` data. A constant that is not an immediate or a literal-pool
   word lives in a non-const `static` (`.data`, copied to DRAM by `_start`)
   or on the stack. No `switch` dense enough for a jump table (the compiler
   puts those in `.rodata`), no string, no struct copy or array initializer
   large enough to become `memset` or `memcpy`, no 64-bit division.
3. No call out of the file: the UART line of S7.9 is written by the file's
   own loop, not the kernel's console.
4. `expect` and `result` are DRAM: Saw passes the addresses of two statics.
   The call's first act compares the two addresses with the DRAM range,
   without loading through them, and returns stop 0x50 if either is
   elsewhere (a flash constant would be fetched through SPI0).
5. The registers it touches are peripherals (0x6000_0000-0x600D_0FFF) and
   the MMU table; the PSRAM itself is not touched by the C part.

**The check.** A runner function beside `check_in_place`,
`check_iram(object, kernel_elf)`, run on every S3 kernel build, both
boards. It fails, naming each finding, when:
- the object has an allocated section of non-zero size other than
  `.text.iram*`, `.literal.iram*`, `.data*` or `.bss*` (a `.rodata` table,
  a jump table, a string, or a function that lost its attribute);
- the object references a symbol it does not define (a compiler helper,
  `memset`/`memcpy`, any kernel function);
- in the linked kernel, a symbol the object defines lies outside IRAM
  (0x4037_8000-0x4037_BFFF) and DRAM (0x3FC8_C000-0x3FCA_FFFF).

**Run** ([RUN], scratch, `SO-102/iramcheck/`): a 200-line stand-in for the
command layer (`mspi.c`: the bounded poll, the S7 command, two MR reads and
two comparisons), compiled with the board's flags and esp-clang, linked by
the installed `ld.lld` with a script shaped like `esp32s3.ld`'s, and checked
by `check_iram.py`:

```
good        sections .literal.iram 0x60, .text.iram 0x1d3       iram check: ok
            linked:  sos_psram_bringup 0x40378060, command 0x40378100,
                     wait_idle 0x40378208, its data 0x3fc8c000
MUT_TABLE   a 4-entry const table      .rodata.cst4; its symbol at
                                        0x3c100000                  FAIL
MUT_SWITCH  a dense switch             .rodata..Lswitch.table...    FAIL
MUT_HELPER  a 64-bit division          references __udivdi3        FAIL
MUT_MEMSET  a zeroed struct and array  references memset           FAIL
MUT_NOIRAM  the poll without the       .literal.wait_idle,
            attribute                  .text.wait_idle; wait_idle at
                                        0x42000230                  FAIL
```

Unit B carries the check with these mutations as its own mutation checks.

### 2.3 The steps

Register addresses from [IDF] `reg_base.h` (`SPI1` 0x6000_2000, `SPI0`
0x6000_3000, `IO_MUX` 0x6000_9000, `GPIO` 0x6000_4000, `EFUSE` 0x6000_7000,
`RTC_CNTL` 0x6000_8000, `SYSCON` 0x6002_6000, `EXTMEM` 0x600C_4000) and
`soc.h` (MMU table 0x600C_5000). "RMW" is read, change the named fields,
write. Each step says what goes wrong if it is skipped or wrong, and
whether chip select 0 can be driven in it. Stop codes are §5.2's.

**S0. Read, change nothing.**
1. eFuse `EFUSE_RD_MAC_SPI_SYS_4_REG` (0x6000_7054): `PSRAM_CAP` bits [4:3]
   and `PSRAM_VENDOR` bits [8:7] must equal the description's codes (8 MiB
   is `PSRAM_CAP` 1, AP 3.3 V is `PSRAM_VENDOR` 1; [IDF] `efuse_reg.h`, the
   meanings in `esp_efuse_table.csv`, BLK1 bits 131 and 135). `esptool
   flash-id` printed "Embedded PSRAM 8MB (AP_3v3)" on the owner's part
   (SO-76 c6). Stop 0x51.
2. `RTC_CNTL_DIG_PAD_HOLD_REG` (0x6000_80DC): the bits the ROM's
   `gpio_pad_unhold` clears for pads 22 and up (bit `pad - 21`) must be
   clear for pads 26 and 33 to 37. Stop 0x52.
3. The system clock must be the one the description's divider was chosen
   for (§3.3): `SYSTEM_SYSCLK_CONF_REG` (0x600C_0060) `SOC_CLK_SEL` [11:10],
   `SYSTEM_CPU_PER_CONF_REG` (0x600C_0010) `PLL_FREQ_SEL` bit 2, and
   `SPI_MEM_CORE_CLK_SEL_REG` (0x6000_30EC) bits [1:0] must each equal the
   description's (today: 0, 1, 0, the reset values, which P0 confirms).
   Stop 0x53.
4. `SPI_MEM_CMD_REG(1)` must read 0 within the poll bound (S7's
   `wait_idle`). Stop 0x54.
- If skipped: on a part without in-package PSRAM the next steps would drive
  pads and send commands to nothing; S8 would still stop it, later and with
  less to say. CS0: nothing is sent.

**S1. The PSRAM's own pads** ([ROM] `esp_rom_opiflash_pin_config`, default
branch, tables at 0x3FF1_90A8: pads 30, 29, 32, 31, 28, 27 at function 0 are
the flash's; pads 33..36 function 4 (SPIIO4..7), pad 37 function 4 (SPIDQS);
input signals 7..11. [IDF] `spi_pins.h`: 26 CS1, 33..36 D4..D7, 37 DQS;
`io_mux_reg.h` field positions.)
- IO MUX pad 26 (0x6000_906C), RMW: `MCU_SEL` [14:12] = 0 (`FUNC_SPICS1`),
  `FUN_DRV` [11:10] = 3 ([IDF] `s_init_psram_pins`).
- IO MUX pads 33..37 (0x6000_9088 + 4 x (pad - 33)), RMW: `MCU_SEL` = 4,
  `FUN_PD` (bit 7) = 0, `FUN_PU` (bit 8) = 1, `FUN_IE` (bit 9) = 1 ([ROM]
  `gpio_pad_pullup`, `gpio_pad_input_enable`), `FUN_DRV` = 3 ([IDF]
  `mspi_timing_ll_set_all_pin_drive`).
- GPIO `FUNCn_IN_SEL_CFG` for n = 7..11 (0x6000_4154 + 4n), RMW: bit 7
  (`SIG_IN_SEL`) = 0, the signal straight from the IO MUX ([ROM]
  `gpio_bypass_matrix_in`: address `(sig + 0x18001055) << 2`, clears bit
  7).
- Pads 27..32 are never written (§2.1 row B).
- If wrong: an index of 27..32 would take a flash pad off the flash and the
  next fetch from flash would fail: a kernel fault or a hang until reset. A
  wrong function on 33..37 makes S8 read garbage and stop. CS0: nothing is
  sent.

**S2. PSRAM chip-select timing, SPI0** ([IDF] `s_set_psram_cs_timing`).
`SPI_MEM_SPI_SMEM_AC_REG(0)` (0x6000_30DC), RMW: `CS_SETUP` (bit 0) = 1,
`CS_HOLD` (bit 1) = 1, `CS_SETUP_TIME` [6:2] = 3, `CS_HOLD_TIME` [11:7] = 3,
`CS_HOLD_DELAY` [30:25] = 2. The ROM's boot already writes this register
(§1.1); its fields are the PSRAM's ("SMEM", Ext_RAM, in `spi_mem_reg.h`).
The driver's comment: SPI0 and SPI1 share these for PSRAM. If wrong:
marginal transfers, which S8 or S14 stop. CS0: nothing is sent.

**S3. ECC off** ([IDF] `s_configure_psram_ecc`, the `#else` branch).
`SYSCON_SRAM_ACE0_ATTR_REG` (0x6002_6058) bit 8: its reset value is 0x0FF
([IDF] `syscon_reg.h`), so bit 8 is clear and nothing is written; if it is
set, clear it and read again. Stop 0x55 if it stays set. If it were left
set the controller would expect ECC bytes the chip does not hold, and S14
would stop.

**S4. PSRAM clock for cache fills, SPI0.** `SPI_MEM_SRAM_CLK_REG(0)`
(0x6000_3050) = the description's divider word (§3), encoded as
`mspi_timing_ll_set_psram_clock` encodes it. `CORE_CLK_SEL` is not written.
If wrong: too fast fails S14; too slow risks the chip's maximum
chip-select-low time (§3). CS0: nothing is sent.

**S5. Prepare SPI1** ([ROM] `esp_rom_opiflash_backup_reg`; [IDF] row D).
1. Save `CLOCK` (+0x14), `CTRL` (+0x08), `USER` (+0x18), `USER1` (+0x1C),
   `USER2` (+0x20), `ADDR` (+0x04), `MOSI_DLEN` (+0x24), `MISO_DLEN` (+0x28),
   `CACHE_FCTRL` (+0x3C), `DDR` (+0xE0) and `MISC` (+0x34) of SPI1, in a
   DRAM static. The ROM saves only `USER`, `USER1`, `USER2`, `CTRL` and
   `DDR`.
2. `SPI_MEM_CLOCK_REG(1)` (0x6000_2014) = the same divider word as S4.
   "When SPI1 accesses to flash or Ext_RAM, f_SPI_CLK = f_MSPI_CORE_CLK /
   (SPI_MEM_CLKCNT_N + 1)" ([IDF] `spi_mem_reg.h`): this is the clock of
   every command of S8 and S9. It is SPI1's own register; the cache uses
   SPI0's. At handover it holds `0x0003_0103` from the ROM's `SPI_init`
   (§1.1), so with a core clock of 80 MHz the write changes nothing; it is
   written so the commands' clock is the design's and not an inheritance.
3. `SPI_MEM_DDR_REG(1)`: clear `WDAT_SWP` (bit 3) and `RDAT_SWP` (bit 2)
   ([ROM] `esp_rom_spi_set_dtr_swap_mode`: `and -13`), set `VAR_DUMMY`
   (bit 1) ([IDF] `esp_psram_impl_enable`).
- CS0: nothing is sent.

**S6. The chip-select gate.** `SPI_MEM_MISC_REG(1)` (0x6000_2034), RMW: bits
[1:0] = 0b01, `CS0_DIS` = 1, `CS1_DIS` = 0; read it back; anything but 0b01
in bits [1:0] stops (0x56) before any command, after S10. This is what the
ROM's `esp_rom_spi_cmd_start` does with the `cs_en_mask` BIT(1) ESP-IDF
passes for every PSRAM command: `MISC = (MISC & ~3) | (~mask & 3)` before
setting `SPI_MEM_USR`, and after it `MISC |= 2` then `MISC &= ~1` ([ROM]
0x4004_E0E2-0x4004_E148).

**S7. One SPI1 user command** (used by S8 and S9). Values from the ROM's
`esp_rom_spi_set_op_mode(1, ESP_ROM_SPIFLASH_OPI_DTR_MODE)` (mode 7, [ROM]
jump table entry 7 = 0x4004_DD2F) and `esp_rom_spi_cmd_config`, with the
arguments ESP-IDF passes ([IDF] `s_init_psram_mode_reg`,
`s_get_psram_mode_reg`). Every wait is `wait_idle`: up to 100,000 reads of
`SPI_MEM_CMD_REG(1)`, returning as soon as it reads 0 (the bound, §2.4).
1. `wait_idle`, or stop 0x57 naming the command about to be sent.
2. `CTRL(1)` = `(CTRL & 0xFE6F9C0F) | 0x2270`: clear every read-mode bit,
   set `FASTRD_MODE` (13), `FCMD_OCT` (9), `FADDR_OCT` (6), `FDIN_OCT` (5),
   `FDOUT_OCT` (4) ([ROM] `esp_rom_spi_reset_rw_mode`'s mask and the mode-7
   entry; [IDF] `spi_mem_reg.h` bit names).
3. `USER(1)`: clear bits [15:12] (`FWRITE_*`) and [31:24]; set
   `USR_COMMAND` (31), `USR_ADDR` (30), and for a read `USR_DUMMY` (29) and
   `USR_MISO` (28), for a write `USR_MOSI` (27); keep bits [11:0].
4. `USER1(1)` = `(31 << 26) | (dummy - 1)`, or `31 << 26` with no dummy.
5. `USER2(1)` = `(15 << 28) | command`.
6. `ADDR(1)` = the register address; `CACHE_FCTRL(1)` bit 1 set (a 32-bit
   address, as the ROM sets it when `addrBitLen == 32`).
7. `MOSI_DLEN(1)` / `MISO_DLEN(1)` = bits - 1; `W0` (+0x58) = data to send.
8. `MISC(1)` bits [1:0] must still read 0b01, or stop 0x58 without sending.
9. `CMD(1)` = `0x0004_0000`, `SPI_MEM_USR` alone; then `wait_idle`. If the
   bound runs out, stop 0x59: the call writes its own short line to UART0
   from IRAM first (`SOS-S3: psram: SPI1 command 0x.... did not finish`,
   waiting for FIFO room with a bound, as `sink.c`'s `kernel_fault` does),
   then returns **without S10**: `MISC(1)` keeps CS0 disabled, so a transfer
   still under way cannot be redirected to the flash by a restore. The
   short line is there because a controller stuck mid-command might also
   stall SPI0's fetches of the Saw code that prints the full line; whether
   it can is not in the sources read.
10. Read the answer from `W0`.
- The command word is a constant with one bit set. Its neighbours are the
  controller's built-in flash commands ([IDF] `spi_mem_reg.h`: bit 21 deep
  power-down, 22 chip erase, 23 block erase, 24 sector erase, 25 page
  program, 26 write status, 30 write enable); none is ever written. Each
  destructive one also needs the flash's write-enable latch, which only
  write-enable (bit 30, or command 0x06 to CS0) sets; neither is sent.
- CS0: this is the only step that drives a chip select. It is gated twice
  (S6 and sub-step 8).

**S8. Identity, before anything is written to the chip** ([IDF]
`s_get_psram_mode_reg`, `s_print_psram_info`; ESP-IDF writes MR0 first and
checks after, this design checks first).
1. Read MR0/MR1: command 0x4040 (`OPI_PSRAM_REG_READ`), address 0, dummy 8
   (`OCT_PSRAM_RD_REG_DUMMY_BITLEN`, 2 x (5 - 1)), 16 bits in. Check MR1 bits
   [4:0] = the description's vendor (0x0D, AP Memory,
   `OCT_PSRAM_VENDOR_ID`). Stop 0x5A.
2. Read MR2/MR3 (address 2, 16 bits). Check MR2 bit 7 = 1 (good die), stop
   0x5B; MR2 bits [2:0] = the description's density (3 for 8 MiB; the table
   1 = 4 MiB, 3 = 8, 5 = 16, 7 = 32, 6 = 64), stop 0x5C.
3. Read MR4 (address 4, 8 bits) and MR8 (address 8, 8 bits): recorded and
   printed, not checked, with MR2 [4:3] (generation) and MR3 bits 6 (VCC:
   1 = 3 V) and 5 (refresh) (question 3).
- **The dummy length on a warm boot.** After a power cycle MR0 holds the
  chip's default; after any earlier boot of this kernel without one, it
  holds 0x28 (S9), since a chip reset does not reset the RAM chip (§2.5).
  The register reads use dummy 8 in both cases, with `VAR_DUMMY` set on SPI1
  (S5). ESP-IDF does the same: its `esp_psram_impl_enable` sets
  `VAR_DUMMY` and reads MR0 with this dummy on every boot, including after
  `esp_restart`, which resets the chip's digital system and not the PSRAM.
  That precedent is read, not run. Stages P1 and P2 run this read both after
  power-on and after an earlier P2 (§9).

**S9. Mode register 0** ([IDF] `s_init_psram_mode_reg`; values from
`esp_psram_impl_enable`: `lt = 1`, `read_latency = 2`, `drive_str = 0`).
1. MR0' = (MR0 & 0xC0) | 0x28: bits [1:0] drive strength 0, [4:2] read
   latency code 2, bit 5 fixed latency (`opi_psram_mode_reg_t`).
2. Write: command 0xC0C0 (`OPI_PSRAM_REG_WRITE`), address 0, no dummy, 16
   bits out, `W0` = (MR1 << 8) | MR0' (the driver writes MR0 and the MR1 it
   read).
3. Read MR0/MR1 again; MR0 & 0x3F must be 0x28. Stop 0x5D.
- If wrong: a latency the controller does not expect corrupts every later
  transfer; 3 or S14 stops. MR0 is volatile. The MR8 write is ECC's and not
  done.

**S10. Restore SPI1.** Every register saved in S5 written back, `CLOCK(1)`
among them, `MISC(1)` last, so SPI1 ends as the ROM left it, with
`CS1_DIS` = 1 and `CS0_DIS` = 0. It runs on success and on every stop after
S5, except stop 0x59 (S7.9).

**S11. SPI0's PSRAM access** ([IDF] `s_config_psram_spi_phases`), all RMW
of SPI0's PSRAM-side registers:
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
- None of SPI0's flash registers (`CTRL`, `CLOCK`, `USER*`, `CACHE_FCTRL`,
  `DDR`, `DIN_*`, `DOUT_*`, `CORE_CLK_SEL`) is written. That the PSRAM-side
  registers govern only CS1 is read from the field descriptions in
  `spi_mem_reg.h`, not from the TRM.
- If wrong: cache fills from PSRAM return garbage and S14 stops. No PSRAM
  page is mapped yet, so no access reaches CS1 while these change.

**S12. The map** ([IDF] `mmu_ll_write_entry`, `s_psram_mapping`).
1. For n = 0 to 127: MMU entry 256 + n (0x600C_5400 + 4n) = `0x8000 | n`.
2. Read each back. Stop 0x5E on the first that differs.
3. `EXTMEM_DCACHE_CTRL1_REG` (0x600C_4004) bit 0 (`SHUT_CORE0_BUS`) must be
   0. Stop 0x5F.
- Entries 0..255 stay the ROM's flash pages; the image uses entries 0..32
  today. The entries replaced named flash pages 256..383, past the 8 MiB
  chip, which nothing reads.
- No cache operation: ESP-IDF maps PSRAM without suspending the cache
  (`s_psram_mapping` calls `mmu_hal_map_region`, which writes entries and
  nothing else, `mmu_hal.c`). No line for these virtual addresses can be in
  the data cache, since nothing has read them since reset.
- The same entries are IBUS 0x4300_0000-0x437F_FFFF ([RUN] §6.2), which is
  why the HAL's flash window shrinks to 16 MiB (§4).
- If wrong: an index below 256 would replace the kernel's own flash pages,
  and the next fetch or `.rodata` read faults. A chip reset restores the
  ROM's map.

**S13. Return.** From here nothing writes an MSPI, IO MUX, GPIO matrix or
MMU register until the machine stops.

**S14. The read-back test** (Saw; [IDF] `s_test_psram`). For every eighth
word of the 8 MiB, write `index ^ 0xAAAAAAAA`; then read each back and
count mismatches: 262,144 writes and reads through the write-back data
cache. The span is 128 times the largest data cache (64 KiB), so every
written line has been evicted to the chip before the read pass reaches it,
and every read is a miss the chip serves. A density smaller than claimed
aliases and fails here even if S8 were fooled. Stop 0x60. ESP-IDF runs this
by default (`CONFIG_SPIRAM_MEMTEST`) and prices it at "about a second" per
4 MiB at its own clock.

**S15. Publish.** `psram_bytes()` answers 8 MiB from here on (§4), and the
kernel prints the success line of §5.2.

### 2.4 Chip select 0, and the waits

**Where CS0 could be driven.** Only S7 drives a chip select. S8 and S9 call
it six times (MR0/1, MR2/3, MR4 and MR8 reads, the MR0 write, the MR0/1
read-back). Each starts only after `MISC(1)` bits [1:0] read back 0b01 at
S7.8, and S6 read them back once before the first. Cache accesses go out on
SPI0, which selects CS1 for an MMU entry with bit 15 set and CS0 for a
flash entry; S12 writes only entries 256..383 and reads each back. On the
emulator the flash is selected once per boot, by the ROM, with the gate in
place, and is sent 0x40 with the gate removed (§6.4).

**The bound.** `wait_idle` reads `CMD(1)` up to 100,000 times. A command of
S8 or S9 is under 100 SPI clocks (command 1, address 2, latency up to 20,
data up to 2), under 10 us at 10 MHz. One iteration is a peripheral read
and a branch, several core cycles, so 100,000 iterations is more than a
millisecond at any clock the S3 runs at (240 MHz at most), a hundred times
the longest command; and at the slowest loop rate seen on the owner's part
(about 2.8 million iterations a second, SO-77 §6) it is about 36 ms, so a
stuck controller stops in well under a second. The waits are S0.4, S7.1 and
S7.9, each with its own stop.

### 2.5 What a reset does not undo, and non-volatile state

- **Nothing in the sequence writes non-volatile state.** No eFuse register is
  written: only `EFUSE_RD_*` is read (programming needs `EFUSE_CONF` and
  `EFUSE_CMD`, which nothing here names). No command reaches the flash, so
  its status registers, OTP and array are never addressed. The PSRAM holds
  nothing across power-off.
- **What survives a chip reset:** only the PSRAM's MR0 (0x28) and its
  contents, until power-off; the ROM never selects CS1, and S8 reads MR0
  correctly either way (S8, the warm-boot note). The sequence writes no
  RTC-domain register. (`_start` writes the RTC and super watchdogs' RTC
  registers on every image, as it does today; §9 P0.)
- **What a chip reset restores:** RST and esptool's auto-reset pull EN, a
  power-on reset of the digital system: SPI0, SPI1, IO MUX, GPIO matrix,
  the cache and its MMU table return to reset values, and the ROM's boot
  writes `SPI_init`'s words and the 512 flash entries again (§1.1).
- **Download mode** is the ROM's, chosen by strap (GPIO0 low at reset)
  before any byte of the image runs; nothing in the image can prevent it,
  and no eFuse is touched. `write-flash 0x0` works whatever a stage did.

---

## 3. Timing and the system clock

### 3.1 What the memory-SPI core clock comes from

- **Two clocks, one divider.** Commands on SPI1 run at
  `SPI_MEM_CLOCK_REG(1)` (S5.2) and cache fills from PSRAM on SPI0 at
  `SPI_MEM_SRAM_CLK_REG(0)` (S4); the flash's cache fills run at
  `SPI_MEM_CLOCK_REG(0)`. Each is the MSPI core clock over its divider
  ([IDF] `spi_mem_reg.h`: "f_SPI_CLK = f_MSPI_CORE_CLK/(SPI_MEM_CLKCNT_N+1)",
  and the same for `SCLKCNT_N`).
- **The system clock is the PLL:** the MSPI core clock is set by
  `SPI_MEM_CORE_CLK_SEL` and the PLL's frequency. With the PLL at 480 MHz,
  `CORE_CLK_SEL` 0, 1, 2, 3 give 80, 120, 160, 240 MHz; at 320 MHz, 0 and 1
  give 80, 2 gives 160 (`spi_mem_reg.h`, `SPI_MEM_CORE_CLK_SEL`'s
  description). The PLL's frequency is `SYSTEM_PLL_FREQ_SEL`
  (`SYSTEM_CPU_PER_CONF_REG` bit 2: 0 = 320 MHz, 1 = 480 MHz; [IDF]
  `clk_tree_ll.h`, `clk_ll_bbpll_get_freq_mhz`), and the system clock source
  is `SYSTEM_SOC_CLK_SEL` (`SYSTEM_SYSCLK_CONF_REG` 0x600C_0060 bits [11:10]:
  0 crystal, 1 PLL, 2 RC_FAST; `clk_ll_cpu_get_src`).
- **The system clock is the crystal:** the sources read do not say what the
  MSPI core clock is. `CORE_CLK_SEL`'s description covers only the PLL.
  ESP-IDF, before it switches the CPU to a source at or below the crystal's
  frequency, puts MSPI into its low-speed mode (`clk_utils.c`,
  `esp_clk_utils_mspi_speed_mode_sync_before_cpu_freq_switching`, S3
  branch, calling `mspi_timing_change_speed_mode_cache_safe(true)`), which
  computes its dividers as if the core were 80 MHz (`mspi_timing_config.c`,
  `FLASH_LOW_SPEED_CORE_CLOCK_MHZ`), and after a switch above the crystal
  it restores high-speed mode. That treats the switch as a change of the
  memory-SPI clock, and does not say by how much. Two readings are open: H1,
  on the crystal the core clock is the crystal's 40 MHz; H2, it stays at a
  PLL-derived 80 MHz. **Only the part can settle it** (§3.4).
- **What the ROM leaves.** The ROM's literal references to
  `SYSTEM_SYSCLK_CONF_REG` are all in `ets_get_xtal_div`, `ets_set_xtal_div`,
  `ets_get_xtal_freq` and `ets_get_apb_freq` ([ROM], 0x4004_5318-0x4004_537F),
  which read it or set the crystal's pre-divider; none sets `SOC_CLK_SEL`.
  So on direct boot the system clock is presumably the crystal (its reset
  value 0) and the PLL frequency select its reset value 1 (480 MHz), and
  this kernel writes neither. A literal search is not proof; P0 reads both.

### 3.2 The divider, and what it rests on

- **The assumption.** The PSRAM divider is chosen for the system clock that
  is in force when `hal.memory_init()` runs, and that clock does not change
  for the life of the image. Today that is the crystal, presumably, because
  nothing in the tree switches it.
- **The target.** 20 MHz, the speed at which ESP-IDF first talks to the chip
  (`mspi_timing_enter_low_speed_mode`: `set_psram_clock(20, ...)`), never
  above 40 MHz, above which ESP-IDF tunes octal PSRAM
  (`MSPI_TIMING_PSRAM_NEEDS_TUNING (MSPI_TIMING_PSRAM_MODULE_CLOCK > 40)`
  for DTR, `mspi_timing_tuning_configs.h`). On the crystal that is divide by
  2 under H1 and by 4 under H2.
- **If a later unit switches to the PLL with this sequence unchanged**
  (`CORE_CLK_SEL` left 0, so the core becomes 80 MHz at either PLL
  frequency):

  ```
                       on the crystal        after the switch (core 80 MHz)
                       H1 (40)   H2 (80)     from H1's divider  from H2's
  flash, ROM's div 4   10 MHz    20 MHz      20 MHz             20 MHz
  PSRAM, div for 20    2: 20     4: 20       40 MHz             20 MHz
  ```

  Under H1 the PSRAM doubles to 40 MHz, the edge of what ESP-IDF runs
  untuned, a speed this design never tested; the flash doubles to 20 MHz,
  the speed of ESP-IDF's own low-speed mode. If the switch also raised
  `CORE_CLK_SEL` (to 1 at 480 MHz, 120 MHz core), the H1 divider would put
  the PSRAM at 60 MHz untuned and the flash at 30 MHz: transfers fail, the
  kernel faults in its next PSRAM or flash access. Under H2 nothing moves
  unless `CORE_CLK_SEL` does.

### 3.3 The contract with whoever switches the clock

Three ways to keep a clock switch and this sequence from disagreeing
silently:

1. **The switch comes first, and the description names the clock.** A
   clock-switching unit (SO-103's K4) runs in its own seam,
   `hal.clock_init()`, called by `kmain` before `hal.memory_init()`; nothing
   after `memory_init` writes `SOC_CLK_SEL`, `PLL_FREQ_SEL` or
   `CORE_CLK_SEL`. The board description states the three (§4.1's
   `[clock]`), the PSRAM divider is chosen for them, and S0.3 compares all
   three with the description before anything is written. Cost: K4 owns the
   flash's clock across its own switch (it runs from IRAM and shows the
   ROM's flash divider is valid at the new core clock, which by the table
   above it is: 20 MHz); a board with Wi-Fi and one without are two
   descriptions, as the PSRAM boards already are. No run-time change of a
   PSRAM register ever happens.
2. **`memory_init` records the clock, and one HAL function owns every later
   switch.** `hal.set_system_clock()` refuses unless it also rewrites
   `SRAM_CLK(0)` (and the flash's divider) with the data cache frozen and no
   PSRAM access in flight. Cost: a run-time reconfiguration of a live PSRAM
   bus, which ESP-IDF does with a cache freeze and IPC stalls
   (`mspi_timing_change_speed_mode_cache_safe`); the riskiest operation in
   either design, done while processes have data in PSRAM.
3. **The divider is chosen for the PLL case from the start.** Divide by 4
   (20 MHz at an 80 MHz core) whatever the source. Cost: under H1 the PSRAM
   runs at 10 MHz until a switch, inside §3.4's chip-select-low worry, and
   any `CORE_CLK_SEL` above 0 still breaks it silently.

**Recommended: 1.** It keeps the one-time, before-any-process shape of this
whole design, and its check is a comparison, not a reconfiguration. The
stop, at S0.3, before any write:

```
0x53 S0.3  SOS-S3: psram stop: system clock SOC_CLK_SEL=0x. PLL_FREQ_SEL=0x.
           CORE_CLK_SEL=0x., the board's PSRAM divider is for 0x. 0x. 0x.
```

A rule only a review can hold is still needed: nothing writes the three
registers after `memory_init`. A runner check can back it: the HAL's sources
name `SYSTEM_SYSCLK_CONF_REG`, `SYSTEM_CPU_PER_CONF_REG` and
`SPI_MEM_CORE_CLK_SEL_REG` only in the file that implements `clock_init`
and in read-only probes.

### 3.4 The chip and the speed

- **The chip's maximum chip-select-low time.** A DRAM must refresh, so a
  burst may not hold CS low too long; the figure is in AP Memory's
  datasheet, which I did not read. A 32-byte line costs about 1 + 2 + 10..20
  + 16 = 29..39 clocks (command, address, read latency with and without the
  doubled variable latency, data at two bytes per clock): 1.5..2 us at
  20 MHz, 3..4 us at 10 MHz; a 64-byte line adds 16 clocks. If the limit is
  near 4 us, 10 MHz with 64-byte lines is outside it: one more reason for
  20 MHz, and for P0 reading the line size (`EXTMEM_DCACHE_CTRL_REG`
  [4:3]). The figure must be read before unit C is briefed.
- **Measuring the bus clock on the part.** No register reports the MSPI core
  clock. Stage P1 times one MR read in CCOUNT cycles (a special register,
  read with no write); with SO-77 U0's CPU clock and the command's known
  length in SPI clocks, that gives the SPI1 clock, and so H1 or H2.
- **A part that fails** a check stops with its line (§5). No retry at
  another speed: that would be guessing.

---

## 4. The memory map with PSRAM

### 4.1 The ranges the HAL publishes

For SO-95's `sos_board_info` and `board.toml`, in its current spellings
(a range is `base` and `len`, sizes are integers in bytes):

```toml
# hal/xtensa-esp32s3-r8/board.toml, the RAM part
[ram.kernel]
use = "kernel"
base = 0x3FC8_C000
len = 147456          # 144 KiB: kernel .data, .bss, arena, stack, slabs

[ram.sram]
use = "process"
speed = "fast"
base = 0x3FCB_0000
len = 262144          # 256 KiB

[ram.psram]
use = "process"
speed = "slow"
base = 0x3D00_0000
len = 8388608         # 8 MiB

[ram.default]
process = "psram"
pool = "psram"

[clock]               # the system clock the PSRAM divider is chosen for
soc_clk_sel = 0       # crystal (SYSTEM_SYSCLK_CONF SOC_CLK_SEL)
pll_freq_sel = 1      # 480 MHz, the reset value, unused on the crystal
core_clk_sel = 0      # SPI_MEM_CORE_CLK_SEL
```

`[clock]` is new and is this design's proposal to SO-95: the three values
S0.3 compares, today the reset values P0 is expected to confirm. A board
that switches to the PLL (SO-103's K4) states the values after its switch,
and its PSRAM divider is chosen for them (§3.3).

`esp32s3`'s file has the first two ranges and `process = "sram"`,
`pool = "sram"`. No range carries `present = "probed"`: a range is present
because the board says so, and the HAL proves it at boot or stops (§5).
The identity the HAL compares (the eFuse codes, the vendor, the density, the
divider and the system clock it was chosen for) is HAL data in the facade's
`lib.saw`, since the
kernel needs it (SO-95 rule 3.1); `board.toml` restates only the range,
generated from or checked against the export. Root's region stays where
SO-95 rule 5.10 puts it, SRAM; nothing in this design requires that any
more, because the bring-up precedes the loader.

### 4.2 What changes in `lib.saw`

- `FLASH_WINDOW_LEN` becomes `0x0100_0000` (16 MiB) on both boards: then
  `in_flash_window` never accepts a PSRAM address in either alias,
  `same_bytes` never pairs 0x4300_0000 with 0x3D00_0000, and
  `segment_bus_ok`'s executable branch never accepts PSRAM code. Nothing
  linked today is above 4 MiB.
- `RAM_BASE`/`RAM_TOP` keep meaning the SRAM process range. New:
  `PSRAM_BASE` = 0x3D00_0000 and a function `psram_bytes()`, 0 in the base
  HAL, and in the r8 facade 0 until S15 and 8 MiB after (a function so the
  facade can override it, the reason `lib.saw` gives for the `prot_*`
  seam).
- `map_source_ok` and `map_target_ok`: inside the SRAM range, or inside the
  PSRAM range when `psram_bytes()` is non-zero. A range spanning both is
  refused (they are not adjacent). `segment_bus_ok`'s copy branch: the same.
  Executable segments stay flash-only and in place.
- `memory_init()`: no-op in the base HAL; §2.2's call in the facade.

### 4.3 The boot region table and the runner

- `ESP32S3_R8_ARCH`, beside `ESP32S3_ARCH` in `tools/sos_runner.py`:
  `hal_native` the facade, `child_region_base` 0x3D00_0000 for
  hand-assembled children, `pool_base` in PSRAM, and the emulator arguments
  of §6.5. Blade-built children keep the SRAM child slot until SO-95's U4
  generates link addresses (their `.data` is linked by `user/child.ld` at
  0x3FCC_8000); unit D adds one PSRAM child script for one test package.
- `check_in_place`'s `window = 32 * 1024 * 1024` becomes the HAL's 16 MiB;
  `check_iram` (§2.2) is new.
- On the plain board a row at a PSRAM address is minted and then refused at
  use by `map_source_ok`; SO-95's builder refuses it earlier, since that
  board names no `psram` range.

### 4.4 How a process linked for PSRAM runs

Its code and read-only data run in place in flash as today. Its writable
segments are copied by the loader to their link addresses in PSRAM (the
loader's byte stores go through the data cache; a DBUS address takes every
width, [RUN] §6.2, stage P4 on the part), its arena is in its `.bss`, and
the kernel grants its stack at the top of its destination. Its pools are
PSRAM.

### 4.5 The kernel's view

- **Copy doors and `row.pa`.** Identity: a PSRAM row's `base` and `pa` are
  the same DBUS address, and the kernel dereferences it directly.
- **Interrupts masked and cache misses.** The kernel always runs masked
  (ABI.md §7f), so every kernel access to process memory in PSRAM, the
  loader's copies and every copy door, takes its misses masked. A miss
  stalls the core and raises no exception; it lengthens masked time, about
  one miss per 32 bytes copied, an estimated 0.2 to 0.5 ms per 4 KiB
  (§7). The kernel's own data, slabs, stacks and trap frames stay in SRAM,
  so the trap entry, the scheduler and the switch touch no PSRAM.
- **No cache maintenance.** One core, no DMA, one path (the data cache) to
  every PSRAM byte.

---

## 5. Checks and stops (the owner's ruling)

### 5.1 The rule

The part must match its description exactly. A board description that
names PSRAM brings it up or stops; it never continues in SRAM and never
infers a size. A description that names no PSRAM never touches the
controller, the PSRAM pads or CS1. In this design the rule is applied to
the eFuse words, the core clock select, the vendor, the good-die bit, the
density, the mode write and the map; each is compared with the
description, and the first that differs stops the boot.

### 5.2 What is checked, in order, and each stop's line

This is the order of the code: `sos_psram_bringup` makes the checks from
0x50 to 0x5F in the order listed, in IRAM, and returns at the first that
fails; Saw then prints the line and calls `exit_fail` with the code, so the
smoke can assert which step stopped. 0x60 is Saw's, after the call. Before
the first line the banner `SOS M1: kernel up on xtensa (ESP32-S3)` has
printed. `0x...` stands for a value read; each line is printed as one line
(wrapped here). "S10 first" says SPI1 is restored before the line.

```
0x50 entry  SOS-S3: psram stop: bring-up record at 0x........ or
            expectation at 0x........ is not in DRAM
0x51 S0.1   SOS-S3: psram stop: efuse word 0x60007054 = 0x........ says
            PSRAM_CAP=0x. PSRAM_VENDOR=0x., the board says 0x. (N MiB)
            and 0x. (AP 3.3 V)
0x52 S0.2   SOS-S3: psram stop: pads held, RTC_CNTL_DIG_PAD_HOLD =
            0x........, the board needs pads 26 and 33-37 free
0x53 S0.3   SOS-S3: psram stop: system clock SOC_CLK_SEL=0x.
            PLL_FREQ_SEL=0x. CORE_CLK_SEL=0x., the board's PSRAM divider
            is for 0x. 0x. 0x.
0x54 S0.4   SOS-S3: psram stop: SPI1 busy before any command,
            SPI_MEM_CMD = 0x........ after 100000 polls
0x55 S3     SOS-S3: psram stop: SYSCON_SRAM_ACE0_ATTR = 0x........ still
            has ECC on after clearing it
0x56 S6     SOS-S3: psram stop: SPI1 MISC = 0x........ after selecting
            CS1 alone; nothing was sent                         (S10 first)
0x57 S7.1   SOS-S3: psram stop: SPI1 busy before command 0x.... (MRn
            read|MR0 write), SPI_MEM_CMD = 0x........ after 100000 polls
                                                                (S10 first)
0x58 S7.8   SOS-S3: psram stop: SPI1 MISC = 0x........ before command
            0x.... (...); not sent                              (S10 first)
0x59 S7.9   SOS-S3: psram stop: SPI1 command 0x.... (...) did not finish,
            SPI_MEM_CMD = 0x........ after 100000 polls; CS0 left
            disabled                                       (no S10; the
            call has already written its own short line, S7.9)
0x5A S8.1   SOS-S3: psram stop: MR1 vendor = 0x.., the board says 0x0d
            (AP Memory)                                         (S10 first)
0x5B S8.2   SOS-S3: psram stop: MR2 = 0x.. has the good-die bit clear,
            the board says a good die                           (S10 first)
0x5C S8.2   SOS-S3: psram stop: MR2 density = 0x. (N MiB), the board says
            0x. (N MiB)                                         (S10 first)
0x5D S9.3   SOS-S3: psram stop: MR0 = 0x.. after writing 0x28   (S10 first)
0x5E S12.2  SOS-S3: psram stop: MMU entry 0x... reads 0x........, wrote
            0x........
0x5F S12.3  SOS-S3: psram stop: data bus off, EXTMEM_DCACHE_CTRL1 =
            0x........
0x60 S14    SOS-S3: psram stop: read-back at 0x3d...... read 0x........,
            wrote 0x........ (0x........ of 0x00040000 words wrong)
```

The vendor stop comes first among the identity checks because a missing or
mis-wired chip reads 0 or 0xFF everywhere, and the vendor line says so.
No part should ever print 0x56 or 0x58; they are kept because they guard
the flash.

On success, one line, then the boot goes on:

```
SOS-S3: psram 8 MiB at 0x3d000000, MR0=0x28 MR1=0x0d MR2=0x.. MR3=0x..,
read-back ok
```

### 5.3 The state of a halted part

- After every stop from 0x56 on except 0x59, SPI1 is back to what S5 saved,
  ending with `MISC(1)` as the ROM left it. Before S5 it was never changed.
  After 0x59, `MISC(1)` keeps CS0 disabled, deliberately (S7.9).
- SPI0's flash registers, the core clock, the flash's pads and MMU entries
  0..255 were never written, so the kernel's code and strings are still
  served from flash and the stop line prints (for 0x59, see S7.9).
- The PSRAM may hold a mode-register value and the test pattern, both gone
  at power-off.
- The flash was never selected after the ROM's own boot reads.
- The watchdogs are off (`_start`), so the part halts on its line and does
  not reset-loop. RST or esptool's auto-reset is a chip reset (§2.5); the
  ROM then boots or enters download mode as on a fresh part.

### 5.4 One board or two

**Recommended: two boards, one HAL.**
- `esp32s3`: today's HAL directory and `board.toml`, no `psram` range. Its
  kernel never calls `sos_psram_bringup`; it runs on any S3 part, the R8
  included.
- `esp32s3-r8`: its own board, a facade directory (as `hal/riscv32-flat` is
  over `hal/riscv32`: SO-95 rule 3.1 gives each board one `board.toml` in
  its HAL directory) that re-exports the S3 HAL and overrides
  `memory_init` and `psram_bytes`, with §4.1's `board.toml`.
- A Freenove kit with an N8R8 module and one with an N8 module are then two
  boards. A "variant" key in one description would need the kernel to learn
  the variant another way (a boot flag), a runtime choice the owner has
  ruled out.
- SO-95's earlier `present = "probed"` and its "refuse a boot row in an
  absent range" are not needed: the range is present by description and
  proven at boot, or the boot stops before any row is used.

---

## 6. The emulator (run)

### 6.1 How it was run

The suite lock was taken for every QEMU run (`mkdir
/private/tmp/claude-501/sawos-suite-lock`, `rmdir` after, foreground). The
probe lives in `/private/tmp/claude-501/-Users-shawn-Projects-sawos/
34c0d76b-723c-482d-939f-716d368f6e5c/scratchpad/SO-102/probe/`: `probe.c`
(windowed C that keeps the ROM's window and stack and calls ROM functions
by address; a scratch probe, unlike the kernel), `entry.S` (the
direct-boot magic and `entry a1, 32; call8 probe_main`), `probe.ld`,
`mkimg.py` (a 4 MiB flash image as ABI.md §1e; the images were deleted
after the runs and rebuild from it), `run.sh` and `runt.sh`. Each run:

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
MMU entry with the PSRAM bit into the model's memory
(`esp32s3_mmu_region_translate`), whatever SPI0 holds.

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
- `quad8`: every MR read returns 0. `nopsram`: MR reads return 0, and the
  first load at 0x3D00_0000 after mapping takes `LoadStoreError` (EXCCAUSE
  3, EXCVADDR 0x3d000000).
- SPI0 reads 0 in every register; IO MUX pads 26..37 read 0; the data cache
  control registers read 0 and the PSRAM miss counter stays 0. SPI1's
  `CMD` reads 0 at once after `SPI_MEM_USR` is written (the model runs the
  transfer inside the write), so no wait can run out here.

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
before its commands. The ROM's code is the real mask ROM, so these are the
words it composes on silicon from the same starting values; the starting
values are the emulator's, which here are `SPI_init`'s (§1.1).

### 6.4 The chip-select gate, observed

Two builds of a probe that sends one MR read through S7 and stops, one as
designed and one with `CS0_DIS` left 0 (`-DMUTANT_CS0`), run with the flash
model's trace:

```
gate in place:  m25p80 ... new command:0xab ... m25p80_select deselect
                (the ROM's one command at boot; nothing after)
gate removed:   ... m25p80_select select
                m25p80_command_decoded new command:0x40
                m25p80_read_data ... (x8)
                m25p80_select deselect
```

Both read MR0/1 = 0x0d09. A smoke case can therefore assert "the flash is
selected once per boot, by the ROM", which no test of the PSRAM's answers
alone can show.

### 6.5 The eFuse words

`EFUSE_RD_MAC_SPI_SYS_4` reads 0 with no eFuse file and 0x00000088
(`PSRAM_CAP` 1, `PSRAM_VENDOR` 1) with a 1 KiB file holding 0x88 at byte 40
(the model's file is `ESPEfuseBlocks`: six words of block 0, then block 1,
so word 4 of block 1 is at byte 40; [QEMU] `esp_efuse.c`,
`esp_offset_of_block`). The file was unchanged after the run; the runner
should still give each run its own copy.

### 6.6 What the smoke asserts, and what is left to the part

Each item: what the emulator can show, then the board stage that shows it
on the part.

- **The command layer reaches nothing in flash:** `check_iram` on every S3
  build, a build check, not an emulator run (§2.2); its mutants in unit B.
- **The eFuse check and its stop:** yes, with and without the file; P0, P1.
- **The CS gate (the flash never selected):** yes, by the trace; on the part
  only by every later stage still booting.
- **The system-clock stop (0x53):** the emulator reads `CORE_CLK_SEL` 0
  (SPI0 not modeled) and its own values for the two SYSTEM registers
  (`esp32s3.soc.clk` at 0x600C_0000; not read for this design), so the
  passing case runs once unit B records what the emulator answers; the stop
  is shown by a harness description that expects another value. P1 on the
  part.
- **MR identity, the MR0 write, each identity stop:** yes, with `-m 4M` and
  `-m 16M` (an 8 MiB eFuse file and a density that disagrees), the quad
  model, and no `-m`; P1, P2.
- **The bounded waits (0x54, 0x57, 0x59) and the gate re-check (0x58):** not
  by the device, which completes every command at once and stores `MISC` as
  written. The poll and the compare are one routine that takes the
  register's address; a harness kernel runs it against a DRAM word that
  never reads 0, and against one that reads 0 at once, and asserts each
  stop line and the C side's short line. Not shown on the part, by design.
- **Pad functions, the input bypass, CS1's drive:** no, the IO MUX and the
  GPIO matrix are not modeled; P1.
- **Octal DTR line mode, dummy cycles, the SPI1 clock:** no, SPI1 transfers
  are byte-serial and mode and clock bits are ignored; P1, P2.
- **SPI0's PSRAM configuration:** no, SPI0 is not modeled; P3.
- **The MMU entries, the map, the IBUS alias, the stop on a bad entry:**
  yes; P3, P4.
- **The read-back test passing:** trivially (no cache, no chip); P4.
- **The read-back stop:** yes, by a harness kernel that maps two entries to
  one page.
- **Every access width in PSRAM:** byte and word stores and loads ran here
  (§6.2); halfword, unaligned and `s32c1i` were not run and are expected to
  behave as at DRAM addresses (ABI.md §3c, §8); P4.
- **Processes, stacks and pools in PSRAM:** yes (memory semantics); P6.
- **Speed, misses, write-back:** no; P5.

---

## 7. Speed

- **A miss** costs one PSRAM burst: by §3's estimate, 1.5 to 2 us at 20 MHz
  for a 32-byte line, plus as much again when the evicted line is dirty.
  Against one core cycle for SRAM, at a CPU clock still unknown (ABI.md
  §11), that is two orders of magnitude per miss. A hit costs as SRAM does.
- **For a process:** a stack within a few KiB, and data used repeatedly,
  live in the cache after the first touch. A scan over a large buffer pays
  a miss per line. A process with deadlines is pinned to SRAM (SO-95's
  `ram = "fast"`).
- **What pinning does not do:** a pinned process's read-only data is still
  in flash behind the same data cache, so another process's PSRAM traffic
  can evict it; its code is behind the instruction cache, which PSRAM data
  does not use. And the kernel copies into PSRAM with interrupts masked
  (§4.5), so a PSRAM process's large system calls delay every process's
  interrupts, pinned ones included.
- **Measured on the part** (stage P5), with SYSTIMER and
  `EXTMEM_DBUS_ACS_SPIRAM_MISS_CNT` (0x600C_40D4) and
  `EXTMEM_DBUS_ACS_CNT` (0x600C_40D8), cleared through
  `EXTMEM_CACHE_ACS_CNT_CLR_REG` (0x600C_40C4):
  1. a 1 MiB walk at 32-byte stride, reads only, in PSRAM, then in SRAM;
  2. the same walk writing, which adds the write-back of dirty lines;
  3. a 16 KiB loop repeated (fits the cache): the hit cost;
  4. a kernel copy door of 4 KiB into PSRAM: masked time per call;
  5. the read-back test's duration (also printed by P4).
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
3. **A wrong MMU index replaces the kernel's flash pages** (a coding error in
   S12). Low; the entry read-back does not catch a wrong index, the next
   fetch faults. A chip reset restores the map.
4. **A slip puts part of the command path in flash**, so SPI0 fetches while
   SPI1 holds the gate. Caught by `check_iram` at build time (§2.2); the
   emulator could not show it.
5. **A controller stuck mid-command.** Bounded; stop 0x59, its own short
   line from IRAM, CS0 left disabled (S7.9). Chip reset.
6. **PSRAM and the flash drive a shared line at once.** Only if the
   controller drove D0-D3 for CS1 while the flash drove them; with CS0 high
   the flash's outputs are off. Very low. Chip reset.
7. **The flash's HOLD#/WP# toggled by octal data while CS0 is high.** On a
   quad flash whose quad-enable bit is clear, IO2 and IO3 are HOLD# and WP#;
   HOLD# is ignored with CS# high and WP# only guards status writes. From
   general SPI-flash behaviour, not this chip's datasheet. Low, and
   harmless if so.
8. **VDD_SPI at 1.8 V for a 3.3 V flash.** Nothing here writes
   `RTC_CNTL_SDIO_CONF`; the voltage is the GPIO45 strap or an eFuse (§8.4).
   Not from this code.
9. **A reset loop.** The watchdogs are off and every stop halts. None.
10. **Download mode lost.** Needs an eFuse or a damaged flash. Not from this
    code.

### 8.2 What a process can do with PSRAM that it could not before

The tier is flat: a process can already write any register, including SPI1
(and so erase the flash), the MMU table and the IO MUX (ABI.md §5e,
README). PSRAM adds:
- **Executable PSRAM.** A process can write code into PSRAM through DBUS and
  jump to its IBUS alias, since one MMU table serves both buses. On the
  plain board it could already execute code it wrote into IRAM (ABI.md §3c,
  `bus_probe`). Not a new class. The loader never accepts PSRAM code.
- **Damage to every PSRAM process at once.** Rewriting SPI0's PSRAM
  registers, MR0 or the MMU entries 256..383 corrupts or unmaps every
  process in PSRAM, and a kernel access to them then faults in kernel mode,
  which stops the machine. Same class as overwriting another process's
  SRAM; a wider blast.
- **Timing interference** with pinned processes through the shared data
  cache and the kernel's masked copies (§7). New, and the reason pinning is
  weaker than it sounds.

### 8.3 The kernel disabling or reconfiguring the cache

The kernel never does after S12. If a later unit writes flash at run time,
ESP-IDF's rule applies: SPI1 flash operations run with the caches
suspended, so no PSRAM byte is reachable meanwhile; the kernel must then not
touch PSRAM, every PSRAM process is stalled, and suspension must keep the
dirty lines (`Cache_Suspend_DCache`, not disable-and-invalidate). That unit
needs its own design.

### 8.4 Power

The PSRAM is inside the package and shares VDD_SPI with the flash. VDD_SPI
is 3.3 V or 1.8 V by the GPIO45 strap unless an eFuse forces it ([IDF]
`rtc_init.c`, `rtc_vddsdio_get_config`: `RTC_CNTL_SDIO_FORCE`, else
`GPIO_STRAP` bit 5 picks 1.8 V). The part's eFuse says AP_3v3 and the flash
is 3.3 V, so GPIO45 must read low at reset: a pin-plan fact the kernel does
not set. The kernel writes no supply register; P0 prints
`RTC_CNTL_SDIO_CONF_REG` (0x6000_807C) and `GPIO_STRAP`. The PSRAM's
current is in its datasheet, not read.

---

## 9. Board stages

Every stage: an image built with `--emit-flash`, sent to the reviewer with
its SHA-256 and the expected text; the only write is `write-flash 0x0`; no
eFuse burn or write, no erase; if the port is busy, say so and stop.
Flashing only on the owner's word. "Emulator" values are what the smoke
asserts; `<v>` is a value the part prints and the record keeps.

**What every image writes.** `_start` writes, on every image of this tree:
the core's special registers (window, PS, `CPENABLE`, `VECBASE`,
`EXCSAVE1`); the RTC watchdog's key, config and lock, TIMG0's watchdog's
key, config and lock, and the super watchdog's key, auto-feed bit and lock
(ABI.md §2e; the RTC ones are RTC-domain registers); IRAM (the vectors) and
DRAM (the kernel's `.data` and `.bss`). The console writes UART0's FIFO.
All of it is volatile.

**P0. `s3_mem_probe`: reads, and writes nothing beyond what every image
writes.** A harness kernel whose `kmain` loads registers and prints them,
then halts. It does not call `intc_init`, does not start or read SYSTIMER
(which takes a write of `UNIT0_OP`'s snapshot bit and may take one of
`SYSTIMER_CONF`), sends no command, touches no MMU entry. Expected:

```
SOS memprobe: efuse sys3=<v> sys4=<v> PSRAM_CAP=<v> PSRAM_VENDOR=<v>
SOS memprobe: strap=<v> sdio_conf=<v> sysclk=<v> cpu_per=<v>
SOS memprobe: spi0 clock=<v> sram_clk=<v> core_clk_sel=<v> ctrl=<v>
              user=<v> user1=<v> user2=<v> misc=<v> fctrl=<v> sctrl=<v>
              sram_cmd=<v> drd=<v> dwr=<v> ddr=<v> smem_ddr=<v>
              smem_ac=<v> date=<v>
SOS memprobe: spi1 clock=<v> ctrl=<v> user=<v> user1=<v> user2=<v>
              misc=<v> fctrl=<v> ddr=<v> cmd=<v>
SOS memprobe: dcache ctrl=<v> ctrl1=<v> autoload=<v> icache ctrl=<v>
SOS memprobe: mmu 0=<v> 32=<v> 127=<v> 128=<v> 255=<v> 256=<v> 383=<v>
              511=<v>
SOS memprobe: iomux pin_ctrl=<v> 26=<v> 27=<v> ... 37=<v> in_sel 7..11=<v>
SOS memprobe: pad_hold=<v> ace0=<v> rom_dummy_plus spi0=<v> spi1=<v>
SOS-S3: halt pass
```

(each wrapped line is one line.) What it decides: whether the eFuse says
8 MiB AP 3.3 V; the system clock as the ROM leaves it, `sysclk`
(`SOC_CLK_SEL`), `cpu_per` (`PLL_FREQ_SEL`) and `core_clk_sel`, which are
the three facts §3.3's description states and S0.3 compares, and which with
SO-77 U0's clock measurement and P1's command timing fix §3's divider;
`spi1 clock`, `spi0 clock` and `sram_clk`, which the ROM's code says are
`0x00030103` (§1.1) and this confirms; the data cache's size
and line; that entries 256..383 are the ROM's flash pages; the pads' state
and drive (pad 30's `FUN_DRV` is the clock drive for both chips); the
VDD_SPI source; and the ROM's extra-dummy bytes (`rom_spiflash_legacy_data`,
pointer at 0x3FCE_FFE4, bytes at +24 and +25), which the ROM's code says
are 0. Emulator: SPI0, IO MUX and cache words read 0, SPI1 as §1.1, the
eFuse word 0x88 with the file.

**P1. `psram_identity`.** The bring-up call with `last_step` = S8: S0 to S6
and S8's reads and checks, then S10. No write to the chip, no SPI0 change,
no map.

```
SOS psram: efuse PSRAM_CAP=0x1 PSRAM_VENDOR=0x1
SOS psram: sysclk=<v> cpu_per=<v> core_clk_sel=<v> spi0 clock=<v>
           sram_clk=<v> spi1 clock=<v>
SOS psram: SPI1 MISC=0x00000001 before the first command
SOS psram: MR0=<v> MR1=0x0d MR2=<v> MR3=<v> MR4=<v> MR8=<v>
SOS psram: MR0/MR1 read took <v> CCOUNT cycles
SOS psram: SPI1 MISC=<v> clock=<v> after, as before S5
SOS-S3: halt pass
```

The clock line is printed by `memory_init` on every PSRAM stage, after any
clock switch an image makes (which by §3.3 comes first), so P1 to P7 each
show the clocks the bus actually ran at. The cycle count is CCOUNT read
around one command (a special register, read with no write); with the CPU
clock and the command's length in SPI clocks it gives SPI1's clock and
settles §3.1's H1 or H2. It is printed, never asserted.

Emulator: `MR0=0x09 MR1=0x0d MR2=0x93 MR3=0x20 MR4=0x20 MR8=0x05`. On the
part MR1 must be 0x0d and MR2 must have bit 7 set and bits [2:0] = 3.
**MR0** is the chip's power-on default after a power cycle, and 0x28 after
any boot of P2 or a later stage without one; both are expected, and the
record notes which. Run P1 once after a power cycle and once after P2.

**P2. `psram_mode`.** `last_step` = S9: P1 plus the MR0 write and its
read-back.

```
SOS psram: MR0=<v> before, 0x28 written, MR0=0x28 after
SOS-S3: halt pass
```

(`<v>` as in P1; the low six bits; the top two are kept.)

**P3. `psram_word`.** The full call, S0 to S12, then one word: store
0x5aa5c33c at 0x3D00_0000, store to 128 KiB of other PSRAM lines to evict
it, load it back.

```
SOS psram: mapped 0x3d000000-0x3d7fffff, entries 0x100-0x17f read back
SOS psram: word at 0x3d000000 0x5aa5c33c after eviction, misses=<v>
SOS-S3: halt pass
```

The first stage in which a cache fill reaches the chip.

**P4. `psram_test`.** The whole sequence, S0 to S15, then a width probe in
`bus_probe`'s style at PSRAM addresses: byte, halfword and word stores and
loads, an unaligned word load, `s32c1i` under the ROM's `ATOMCTL` 0x15,
and the instruction-bus alias. The alias step stores 0xc3d2e1f0 at
0x3D00_0100, writes 128 KiB of other PSRAM lines so the data cache evicts
it to the chip, and then loads the word at 0x4300_0100: one MMU table, two
caches, and the instruction cache fills from the chip, so without the
eviction it would read what the chip held before the store.

```
SOS-S3: psram 8 MiB at 0x3d000000, MR0=0x28 MR1=0x0d MR2=<v> MR3=<v>,
read-back ok
SOS psram: test took <v> us
SOS probe: byte store and load, PSRAM at 0x3d000003
SOS probe: ok 0x00000096
... (one pair per access, as README's bus_probe stage)
SOS probe: word store, then s32c1i swapping it, PSRAM at 0x3d000010
SOS probe: ok 0x00000005
SOS probe: word at 0x3d000100 evicted, word load at 0x43000100
SOS probe: ok 0xc3d2e1f0
SOS-S3: halt pass
```

`s32c1i` on cached external memory is unprobed anywhere; a refusal (cause
3) would mean processes in PSRAM cannot use the compiler's inline word
atomics, and any process that uses them would need SRAM.

**P5. `psram_speed`.** P4's bring-up, then §7's five measurements, printed.

**P6. `psram_process`.** The r8 kernel with `image_held` (a hand-assembled
child with its stack at the top of a PSRAM destination), unit D's
Blade-built child whose `.data`, `.bss`, arena and stack are in PSRAM, and
a pool in PSRAM. Expected: the success line, then the case's own text as
README's stages 5 and 6, then `SOS-S3: halt pass`.

**P7. `psram_wrong_size`.** The r8 kernel built with a description that says
16 MiB. One description fact, the size, sets both the eFuse code it expects
(`PSRAM_CAP` 3, "16M") and the density (5); the eFuse check runs first, so
the part stops there, before any command:

```
SOS-S3: psram stop: efuse word 0x60007054 = <v> says PSRAM_CAP=0x1
PSRAM_VENDOR=0x1, the board says 0x3 (16 MiB) and 0x1 (AP 3.3 V)
SOS-S3: halt fail code=<0x51's status>
```

The density stop (0x5C) cannot be reached on this part from a description,
since the eFuse and the chip agree; it is the emulator's (`-m 16M` with an
8 MiB eFuse file). Then the plain board's `root_server_boot` is flashed and
runs as README stage 5: a stop leaves nothing behind.

---

## 10. Units of work

**A. The read-only probe (small; Sonnet).** `tests/s3_mem_probe.saw` (or a
section of SO-77's `s3_periph_probe`), its smoke case asserting the
emulator's values, board stage P0, the results recorded in ABI.md. Proves
what the part leaves; with SO-77 U0's clock measurement, decides §3's
divider and the expected `CORE_CLK_SEL`. Can run beside SO-77's U0 now, or
be part of it.

**B. The command layer (medium; Sonnet, Opus review of the register
words).** `hal/xtensa-esp32s3/kernel/mspi.c` under §2.2's rules:
`sos_psram_bringup` with S0 to S10 (and `last_step`), the record, the
bounded `wait_idle` taking a register address, the IRAM short line;
`esp32s3.ld`'s `.iram` gains `*(.literal.iram .text.iram)`. The runner's
`check_iram` with its five mutations as mutation checks. Harness kernels
`psram_identity`, `psram_mode` and the wait harness of §6.6. Runner: an arch
key for extra QEMU arguments, a per-run eFuse file, and the trace assertion
("the flash model is selected exactly once"). Smoke cases: the identity
lines; the stops 0x51 to 0x5D that the emulator can reach (§6.6); the trace
case, whose mutation (the gate removed) turns it red. Board stages P1, P2.

**C. The map, the ranges and the board (medium-large; Opus for the spec
text, Sonnet to build).** S11, S12 in the call; S14, S15 in Saw; the
`hal.memory_init()` seam in `kernel/main.saw` and a no-op in every HAL,
with spec text that any clock switch (a `hal.clock_init()`, SO-103's) runs
before it and nothing writes the three clock registers after it (§3.3), and
the runner check that only `clock_init`'s file names them;
spec §5b's HAL list and §5b.1's S3 row; the `esp32s3-r8` facade board with
`psram_bytes`, the two-range checks, `FLASH_WINDOW_LEN` 16 MiB on both
boards; `ESP32S3_R8_ARCH` with a pool and a hand-assembled child in PSRAM;
smoke cases `psram_boot`, `psram_probe` (widths), the MMU and read-back
stops, the gate's process cases on the r8 board; ABI.md gains a PSRAM
section and §11 entries. Board stages P3, P4, P5, P7. Needs the AP
datasheet's chip-select-low limit first (§3).

**D. A process in PSRAM (small-medium; Sonnet).** `user/child_psram.ld`, one
test package naming it, its smoke case on the r8 board; spec §5.6's "a
writable one copied to DRAM" becomes "to process RAM"; board stage P6.

**Against SO-95 and SO-77.** SO-95's order puts its U3 (the description
formats) beside SO-102: units A and B fit beside U3, and U3 writes both
boards' `board.toml` with §4.1's ranges. SO-95's U4 (generated link
addresses) comes after unit C and then makes unit D's PSRAM script one of
the generated ones. SO-77's U0 and unit A share a harness and a flash; U0
also gives §3 its clock measurement. SO-103's K4, if built, adds
`hal.clock_init()` before `memory_init`, changes `[clock]` in the boards
that use it, and owns the flash's clock across its own switch (§3.3). The
box's first run needs none of
this (SO-95 §6.5).

---

## 11. Alternatives, questions, and what was checked

### 11.1 Alternatives rejected

- **Calling the ROM's PSRAM routines from a windowed shim at boot.** The ROM
  is windowed, the kernel call0, and the routines keep function tables in
  ROM RAM inside the process range. The register-level path composes the
  ROM's words exactly (§6.3).
- **ESP-IDF's low/high-speed modes and tuning.** They write SPI0's flash
  clock, the shared core clock and the shared timing registers; not needed
  at 40 MHz or below (§2.1).
- **Saw calling the C part step by step and comparing between calls.** It
  would run flash code between commands while SPI1 holds the gate; the one
  call keeps every comparison in IRAM (§2.2).
- **A C part that runs to the end and reports.** It would write MR0 and map
  pages before any check was read; the call returns at the first failed
  check instead.
- **An automatic fallback to SRAM, or a second layout.** Ruled out by the
  owner.
- **Running the bring-up in `boot.S` before `kmain`.** Its stops would print
  before the banner, without the console.
- **ECC.** 1/16 of the size and page-corner rules, for no gain here.
- **Executing processes from PSRAM.** Not needed; it would need instruction
  and data cache coherence.
- **PSRAM at 0x3C80_0000.** It would tie the PSRAM's address to the flash's
  size; 0x3D00_0000 holds for flash up to 16 MiB.
- **A per-process MMU mapping at one link address.** A different tier of
  port; not proposed.

### 11.2 Questions for the owner

1. **Two boards (`esp32s3`, `esp32s3-r8`), the second a facade?**
   Recommended: yes (§5.4).
2. **Should the plain board refuse a part whose eFuse says it has PSRAM?**
   Recommended: no. It claims nothing about PSRAM, and the rule is that
   every claim holds; refusing would also stop the plain image running on
   the owner's part, which is P7's control.
3. **What the identity check compares.** Recommended: the eFuse capacity and
   vendor, MR1's vendor, MR2's good-die bit and density; print MR3's voltage
   bit and the generation without checking them (the emulator's model
   reports 1.8 V where the part's eFuse says 3.3 V).
4. **The PSRAM clock.** Recommended: 20 MHz, from P0 and SO-77 U0's
   readings, with no PLL or core-clock change in this work.
5. **The read-back test at every boot.** Recommended: the full 8 MiB, as
   ESP-IDF's default does, priced by P4.
6. **Root's region.** Recommended: SRAM by default (SO-95 rule 5.10), now a
   choice rather than a constraint.
7. **The board stages.** Recommended: P0 first, with or inside SO-77's U0;
   then P1 to P7 one flash each, in order, each after the last was read, P1
   twice (after a power cycle and after P2).
8. **The clock contract with a later PLL switch.** Recommended: the switch
   runs before `memory_init`, the description names the clock, S0.3 stops on
   any difference, and nothing changes the clock after (§3.3, option 1).

### 11.3 What I verified by running

- The emulator's PSRAM: switched on by `-m`, octal only with
  `-global ssi_psram.is_octal=true`; the mode registers, the MR0 write, the
  map at entries 256..383, every width run, the IBUS alias, the fault with
  no PSRAM, the eFuse word with and without a file (§6).
- The design's SPI1 words equal the ROM's own for an MR read (§6.3).
- The CS gate: the flash model is selected once (by the ROM) with it, and
  sent 0x40 without it (§6.4).
- The IRAM rules and `check_iram`: a scratch command layer built with the
  board's flags lands wholly in IRAM and DRAM, and five mutants (a const
  table, a jump table, a 64-bit division, a zeroed struct, a function
  without the attribute) each fail the check (§2.2).

### 11.4 What I read

Every register address, field, value and command in §2 and §3, from the
files and functions named there: ESP-IDF v5.4.1 and the ROM ELF's
disassembly, including the ROM's boot path's clock words (§1.1). The QEMU
model's behaviour from its source at the installed tag, then confirmed by
the runs.

### 11.5 What I could not check

- Every value on silicon: SPI0 and SPI1 at handover (the ROM's code gives
  the clocks; P0 confirms), `SRAM_CLK` and `CORE_CLK_SEL` (no literal in
  the ROM; P0), the MSPI core clock, the data cache's shape, the pads' state
  and drive, the eFuse words on this part.
- That the ROM's boot writes nothing else to SPI1's clock through a computed
  address after `SPI_init`; the literal search covers only literal
  addresses.
- The AP Memory datasheet: the chip-select-low limit (§3), the mode
  registers beyond ESP-IDF's struct, the chip's current draw, and whether a
  register read at dummy 8 is right for every MR0 a warm chip can hold (S8;
  ESP-IDF's warm restart is the precedent, read not run).
- The S3 Technical Reference Manual: how SPI0 and SPI1 arbitrate (the
  design does not rely on it), whether a stuck SPI1 command stalls SPI0
  (S7.9), and that SPI0's PSRAM-side registers govern only CS1.
- That the pads run correctly at the drives the ROM left (P1).
- `s32c1i`, halfword and unaligned access on cached PSRAM (P4).
- The flash chip's own datasheet (HOLD#/WP#, §8.1).
- Whether pads 33 and 34 are brought out on the Freenove board. The pin
  plan's "35 to 37 clear" is narrower than what the octal PSRAM uses:
  26 (CS1) and 33 to 37; and GPIO45 must read low at reset.
- The miss costs in §3 and §7 are estimates from ESP-IDF's latency
  constants, not measurements.
- `check_iram` ran against a stand-in, not the real `mspi.c`, and against a
  link script shaped like `esp32s3.ld`, not `esp32s3.ld` itself.
- What the MSPI core clock is while the system clock is the crystal (§3.1,
  H1 or H2): open until P1 times a command on the part. Until then the
  divider, and so the PSRAM's and the flash's speeds before and after any
  PLL switch, are known only in the table of §3.2.
- That the ROM leaves the system clock on the crystal and the PLL select at
  480 MHz: inferred from the ROM's literal references (§3.1); P0 reads it.
- What the emulator's `esp32s3.soc.clk` answers for the two SYSTEM
  registers S0.3 reads; unit B records it.

---

## 12. Response to the review

| thread | verdict | change, and where |
|---|---|---|
| t1, SPI1's clock | accepted | S5.2 saves, sets and S10 restores `SPI_MEM_CLOCK_REG(1)`; §3 names both clocks. Its value at handover is now shown from the ROM's code: `SPI_init(5, 4)` on the direct-boot path writes `0x0003_0103` to both `CLOCK(0)` and `CLOCK(1)`, and the direct-boot branch never reaches the header routine that changes it (§1.1). The concern that the ROM might leave divide by 1 or 2 does not arise on this ROM's direct boot; the explicit write stays. P0 prints `spi1 clock` for that reason. |
| t1, ESP-IDF's writes not listed | accepted | §2.1 is now every write on the path, each kept, dropped or n/a. `SMEM_SPICLK_FUN_DRV` is dropped as inert while `SPICLK_PAD_DRV_CTL_EN` is 0; `mspi_timing_set_pin_drive_strength`'s writes are split: drive 3 kept for pads 33-37, `SPI_MEM_DATE_REG(0)` and pads 27, 28, 31, 32 dropped as the flash's, with the drive they run at (the ROM's, printed by P0). |
| t2, what reaches flash | accepted | §2.2 states what must be in IRAM or DRAM and why, and the `check_iram` build check. Run against a scratch build and five mutants, each caught. The six commands are made inside the one IRAM call (t3). |
| t3, which side compares | accepted | The third of the reviewer's options: every comparison of S0 to S12 is in the C call, which returns at the first failure with the record; Saw prints. Identity (S8) precedes the MR0 write (S9); P1 and P2 end the call with `last_step`. §2.2, §5.2. |
| t4, unbounded polls | accepted | Every wait is bounded at 100,000 reads (§2.4 says why that number), with stops 0x54, 0x57 and 0x59. For 0x59 the call writes its own short line from IRAM and does not restore `MISC(1)`, so CS0 stays disabled while a transfer may still be under way (S7.9). §5.2 re-derived in code order. |
| t5, P7's first difference | accepted | P7 now expects the eFuse stop (0x51); the density stop is the emulator's. One description fact, the size, sets both expected codes (§9 P7). |
| t5, MR0 after P2 | accepted | S8 says the read is made at dummy 8 whatever MR0 holds, with `VAR_DUMMY` on, citing ESP-IDF's boot-time read on every boot including after `esp_restart` (read, not run); P1 and P2 expect either value and P1 runs twice. |
| t6, P4's alias load | accepted | P4 evicts the line before loading at the alias and asserts the value; why is stated (§9 P4). |
| SO-103 t2, the PLL switch and the MSPI clock | accepted | §3 is rewritten: what the MSPI core clock comes from on the PLL (`spi_mem_reg.h`, `clk_tree_ll.h`) and that on the crystal it is not in the sources (H1, H2, settled by P1's timing); that the ROM sets no `SOC_CLK_SEL` (§3.1); the divider's assumption and the speeds before and after a switch, in numbers (§3.2); three contracts, option 1 recommended, its stop at S0.3 extended to all three registers (§3.3, §5.2); `[clock]` in the description (§4.1); P0 and every PSRAM stage print the clocks (§9). |
| t6, P0 not write-free | accepted | P0 no longer touches SYSTIMER or `intc_init`; the clock measurement is SO-77 U0's. §9 states exactly what every image writes (`_start`'s registers, IRAM, DRAM, UART) and that P0 writes nothing more. |

Nothing in the review is disputed.
