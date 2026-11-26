// SOS Xtensa kernel HAL: the memory-SPI controller's command layer, for the
// octal PSRAM on chip select 1 (../ABI.md §16), for the flash's reads on
// chip select 0 (../ABI.md §18) and for the switch of the flash's cache
// reads to two or four lines at 80 or 40 MHz (../ABI.md §18e).
//
// C because the whole of it must run without one instruction or one byte of
// data fetched from flash: while SPI1 holds the chip-select gate, or a
// command to either chip is under way, nothing may ask SPI0 to fetch from
// the flash. Saw code runs in place from flash, so the bring-up is one call
// into this file, which makes every check between its steps and returns at
// the first that fails, and so is each read of the flash. Saw prints what
// the record says afterwards.
//
// The rules that keep it out of flash, held by the runner's `check_iram`
// (tools/sos_runner.py) on every S3 image that links it:
//   1. every function is in `.text.iram`, so its code and its literal pool
//      (`.literal.iram`) are copied to IRAM with the vectors (`esp32s3.ld`);
//   2. no `const` data, no string, no switch dense enough for a jump table,
//      no struct or array initializer large enough to become `memset` or
//      `memcpy`, no 64-bit division: anything that is not an immediate or a
//      literal-pool word lives in a non-const static, in DRAM;
//   3. no call out of this file: the lines it writes itself (`short_line`,
//      `flash_short_line`) go to UART0 through its own loop.
//
// Every register address and value is cited where it is defined, from
// ESP-IDF v5.4.1 ("IDF", components/ of that tag) or from the mask ROM's
// disassembly (`esp32s3_rev0_rom.elf`, "ROM"). The step names (S0.1 to S12)
// and the stop codes (0x50 to 0x5F, 0x61 and 0x62) are ../ABI.md §16's and
// §17's, the flash's stops (0x73 to 0x75, 0x79 to 0x7B) §18's, and the
// switch's steps (W1 to W9) and stops (0x70 to 0x79, 0x7D, 0x7E) §18e's.
//
// Nothing here writes non-volatile state: the eFuse is only read
// (EFUSE_RD_*), no RTC register is written, the commands sent with chip
// select 0 enabled are the flash's five reads (`flash_read_bits`) and no
// other, and the commands sent to the RAM chip read its mode registers and
// its array and write its mode register 0, which it loses at power-off. The
// cache's map of the PSRAM (S12) is MMU entries from 256 up, which a chip
// reset gives back to the flash. SPI_MEM_MISC(1), which says which chip
// select a command goes to, is written by the PSRAM's gate alone; a flash
// read compares it and never writes it. The switch writes SPI0's flash-side
// words, and for four lines two pads and two input selects, all of which a
// chip reset puts back; the flash chip itself is sent its reads alone.
//
// Built call0 with no FPU like the rest of the kernel (`-windowed`, `-fp`).
//
// The fault build. Every register read goes through `rd`. Built with
// `-DSOS_PSRAM_FAULT` (a harness image only: the runner refuses any other
// image that defines `sos_psram_fault`), `rd` first consults the DRAM record
// `sos_psram_fault` the harness fills in: when the call is at that step, in
// that command, and reads that address, it gets the record's value instead,
// and then the table `sos_flash_fault_table`, whose every entry answers each
// read it matches. Writes always reach the real register, so the whole real
// call runs with the reads lied to.

typedef unsigned int u32;

#define IRAM __attribute__((section(".text.iram")))

// ---- what the caller passes -----------------------------------------------
//
// Both live in DRAM (the entry check stops otherwise), and their layouts are
// mirrored by `lib.saw`'s `PsramExpect` and `PsramRecord`.

// The description the part must match: the eFuse codes, the chip's vendor
// and density, the divider word, and the three clock words the divider was
// chosen for, each masked as S0.3 compares it.
struct sos_psram_expect {
    u32 efuse_cap;          // EFUSE PSRAM_CAP, 1 for 8 MiB
    u32 efuse_vendor;       // EFUSE PSRAM_VENDOR, 1 for AP at 3.3 V
    u32 vendor;             // MR1 [4:0], 0x0D for AP Memory
    u32 density;            // MR2 [2:0], 3 for 8 MiB
    u32 divider;            // SRAM_CLK(0) and CLOCK(1), as IDF encodes them
    u32 sysclk;             // SYSTEM_SYSCLK_CONF & SYSCLK_MASK
    u32 cpu_per;            // SYSTEM_CPU_PER_CONF & CPU_PER_MASK
    u32 core_clk_sel;       // SPI_MEM_CORE_CLK_SEL(0) & CORE_CLK_SEL_MASK
};

// What the call found. `stop` is 0 when every step ran, else the stop code;
// `read`, `expect`, `command` and `address` are the stop's own words. The
// rest is what the steps read, for the lines Saw prints.
struct sos_psram_record {
    u32 stop;
    u32 read;
    u32 expect;
    u32 command;            // the command a stop in S7 names
    u32 address;            // and its address
    u32 efuse;              // S0.1's word
    u32 pad_hold;           // S0.2's word
    u32 sysclk;             // S0.3's three words, whole
    u32 cpu_per;
    u32 core_clk_sel;
    u32 saved;              // 1 once S5 has saved SPI1
    u32 spi1_clock;         // CLOCK(1) read back after S5.2
    u32 misc_gate;          // MISC(1) read back at S6
    u32 mr01;               // S8.1: MR0 in [7:0], MR1 in [15:8]
    u32 mr23;               // S8.2: MR2, MR3
    u32 mr4;                // S8.3
    u32 mr8;
    u32 mr01_after;         // S9.3
    u32 mr_cycles;          // CCOUNT across S8.1's command
    u32 cycles;             // `sos_psram_time_reads`' answer
    u32 mapped;             // S12: the MMU entries written and read back
};

// The steps, as `last_step` names them and as the fault record names a read:
// 0xMN is step SM.N (M and N in hex: S7.10 is 0x7A, S12.2 is 0xC2), 0xM0
// step SM. The reads S7.2 to S7.6 make to change a word are STEP_S7's;
// STEP_S7_PROBE is the measurement's one read of SPI_MEM_CMD(1) between
// S7.9's start and its wait.
#define STEP_S0_1   0x01u
#define STEP_S0_2   0x02u
#define STEP_S0_3   0x03u
#define STEP_S0_4   0x04u
#define STEP_S1     0x10u
#define STEP_S2     0x20u
#define STEP_S3     0x30u
#define STEP_S5     0x50u
#define STEP_S6     0x60u
#define STEP_S7     0x70u
#define STEP_S7_1   0x71u
#define STEP_S7_8   0x78u
#define STEP_S7_9   0x79u
#define STEP_S7_10  0x7Au
#define STEP_S7_PROBE 0x7Bu
#define STEP_S8     0x80u
#define STEP_S9     0x90u
#define STEP_S11    0xB0u
#define STEP_S12    0xC0u
#define STEP_S12_2  0xC2u
#define STEP_S12_3  0xC3u

// The stops, in the order the steps make them (../ABI.md §16c).
#define STOP_NOT_DRAM       0x50u
#define STOP_EFUSE          0x51u
#define STOP_PAD_HELD       0x52u
#define STOP_CLOCK          0x53u
#define STOP_BUSY_BEFORE    0x54u
#define STOP_ECC            0x55u
#define STOP_GATE           0x56u
#define STOP_BUSY_COMMAND   0x57u
#define STOP_GATE_LOST      0x58u
#define STOP_NOT_FINISHED   0x59u
#define STOP_VENDOR         0x5Au
#define STOP_DIE            0x5Bu
#define STOP_DENSITY        0x5Cu
#define STOP_MR0            0x5Du
#define STOP_MMU            0x5Eu
#define STOP_BUS_OFF        0x5Fu
#define STOP_MEASUREMENT    0x61u
#define STOP_STILL_BUSY     0x62u

// What a flash read found (../ABI.md §18a). The first five words are a
// stop's, as the PSRAM's record has them; `answer` is the read's bytes, the
// first received in [7:0]; `saved` is 1 once SPI1's words are saved, so a
// stop after it has put them back; `misc` is SPI_MEM_MISC(1) as the gate
// read it. Mirrored by `lib.saw`'s `FlashRecord`.
struct sos_flash_record {
    u32 stop;
    u32 read;
    u32 expect;
    u32 command;            // the read's opcode
    u32 address;            // its address, for SFDP
    u32 answer;
    u32 saved;
    u32 misc;
};

// A flash read's steps, as the fault record names a read. The command's own
// reads are the PSRAM's S7 steps, the primitive being the same.
#define STEP_F_GATE 0xF1u
#define STEP_F_DDR  0xF2u
#define STEP_F_IDLE 0xF3u
#define STEP_F_SAVE 0xF4u

// A flash read's stops (../ABI.md §18a).
#define STOP_FLASH_GATE         0x73u
#define STOP_FLASH_BUSY         0x74u
#define STOP_FLASH_STUCK        0x75u
#define STOP_FLASH_NOT_DRAM     0x79u
#define STOP_FLASH_NOT_READ     0x7Au
#define STOP_FLASH_DDR          0x7Bu

// The switch's description (../ABI.md §18e): the three clock words the
// switch is for, masked as S0.3 masks them; the flash's JEDEC ID; and SPI0's
// flash-side words as the ROM leaves them. Mirrored by the `s3flashkit`
// module's `FlashExpect`.
struct sos_flash_expect {
    u32 sysclk;             // SYSTEM_SYSCLK_CONF & SYSCLK_MASK
    u32 cpu_per;            // SYSTEM_CPU_PER_CONF & CPU_PER_MASK
    u32 core_clk_sel;       // SPI_MEM_CORE_CLK_SEL(0) & CORE_CLK_SEL_MASK
    u32 jedec;              // manufacturer in [23:16], as IDF forms it
    u32 clock;              // SPI_MEM_CLOCK(0)
    u32 ctrl;               // SPI_MEM_CTRL(0)
    u32 user1;              // SPI_MEM_USER1(0)
    u32 user2;              // SPI_MEM_USER2(0)
};

// The words the switch reads before its stores and again after them, in
// `switch_addrs`' order: SPI0's CLOCK, CTRL, CTRL2, USER, USER1, USER2, DDR,
// TIMING_CALI, DIN_MODE and DIN_NUM, pads 27 and 28, and the input selects
// of signals 2 and 3.
struct sos_flash_words {
    u32 clock;
    u32 ctrl;
    u32 ctrl2;
    u32 user;
    u32 user1;
    u32 user2;
    u32 ddr;
    u32 cali;
    u32 din_mode;
    u32 din_num;
    u32 pad_hd;
    u32 pad_wp;
    u32 insel_hd;
    u32 insel_wp;
};

#define SWITCH_WORDS 14u
#define W_CLOCK     0u
#define W_CTRL      1u
#define W_CTRL2     2u
#define W_USER      3u
#define W_USER1     4u
#define W_USER2     5u
#define W_DDR       6u
#define W_CALI      7u
#define W_DIN_MODE  8u
#define W_DIN_NUM   9u
#define W_PAD_HD    10u
#define W_PAD_WP    11u
#define W_INSEL_HD  12u
#define W_INSEL_WP  13u

// What the switch found. `stop` is 0 when it was made, else the stop code;
// `read`, `expect` and `at` are the stop's own words (`at`: the word's index
// in `sos_flash_words` for 0x71, the byte's offset for 0x78). `state` is
// `STATE_*`: what the switch did to SPI0 and what each cache's freeze and
// unfreeze reported. `ticks_before` and `ticks_after` are the timed reads,
// in SYSTIMER's ticks of 1/16 us. `canary_*` is the canary's first word that
// read wrong, with `STATE_CANARY`; `freeze_word` the freeze word a freeze
// that did not report last read, and `thaw_word_i` and `thaw_word_d` each
// cache's, for an unfreeze that did not report. `flash` is the record of
// the last read the switch sent. Mirrored by `s3flashkit`'s
// `FlashSwitchRecord`.
struct sos_flash_switch_record {
    u32 stop;
    u32 read;
    u32 expect;
    u32 at;
    u32 sysclk;             // W2's three words, whole
    u32 cpu_per;
    u32 core_clk_sel;
    u32 icache_autoload;    // W2's two autoload words
    u32 dcache_autoload;
    u32 jedec;              // W3's ID, manufacturer in [23:16]
    u32 sr2;                // W3's status register 2
    u32 state;
    u32 ticks_before;
    u32 ticks_after;
    u32 canary_at;
    u32 canary_read;
    u32 canary_want;
    u32 freeze_word;
    u32 thaw_word_i;
    u32 thaw_word_d;
    struct sos_flash_words before;
    struct sos_flash_words after;
    struct sos_flash_record flash;
};

// The switch's steps, as the fault record names a read: W2's clock words,
// SPI0's words and the autoload words; W4's and W8's SYSTIMER; W5's freeze
// and its unfreeze (the undo of a freeze that did not report is an
// unfreeze); W6's canary; W9's read-back; W7's freeze and unfreeze. W3's
// reads are a flash read's own steps (`STEP_F_*`, then the primitive's S7
// steps), the ID's in command 1 and status register 2's in command 2.
#define STEP_W_CLOCK    0xE1u
#define STEP_W_SPI0     0xE2u
#define STEP_W_CACHE    0xE3u
#define STEP_W_TIME     0xE4u
#define STEP_W_FREEZE   0xE5u
#define STEP_W_CANARY   0xE6u
#define STEP_W_THAW     0xE7u
#define STEP_W_AFTER    0xE9u
#define STEP_W7_FREEZE  0xEAu
#define STEP_W7_THAW    0xEBu

// The record's `state`: W5's stores made; W7's put-back made; W6's canary
// read wrong; a cache's freeze that did not report FREEZE_DONE, and was
// undone; a cache's unfreeze, or the undo of its freeze, that did not
// report FREEZE_DONE clear.
#define STATE_STORED        0x01u
#define STATE_PUT_BACK      0x02u
#define STATE_CANARY        0x04u
#define STATE_FREEZE_I      0x10u
#define STATE_FREEZE_D      0x20u
#define STATE_THAW_I        0x40u
#define STATE_THAW_D        0x80u
#define STATE_THAWS         (STATE_THAW_I | STATE_THAW_D)

// Status register 2's QE bit, which four lines need set.
#define SR2_QE          0x2u

// The switch's stops (../ABI.md §18e). A flash read's own (0x73 to 0x75,
// 0x7B) are W3's.
#define STOP_SWITCH_CLOCK       0x70u
#define STOP_SWITCH_SPI0        0x71u
#define STOP_SWITCH_AUTOLOAD    0x72u
#define STOP_SWITCH_ID          0x76u
#define STOP_SWITCH_QE          0x77u
#define STOP_SWITCH_CANARY      0x78u
#define STOP_SWITCH_NOT_DRAM    0x79u
#define STOP_SWITCH_FREEZE      0x7Du
#define STOP_SWITCH_THAW        0x7Eu

// The switch's `mode`: bit 0, the cache reads on four lines (QIO, EBh) and
// not two (DIO, BBh); bit 1, the flash at half the memory-SPI core clock and
// not the whole of it.
#define MODE_QUAD           1u
#define MODE_HALF           2u

// ---- registers ------------------------------------------------------------
//
// Bases from IDF soc/esp32s3/register/soc/reg_base.h; offsets from the header
// each line names.

// SoC DRAM, the data bus's window on internal SRAM (IDF soc/esp32s3/include/
// soc/soc.h, SOC_DRAM_LOW, SOC_DRAM_HIGH): what is not fetched through SPI0.
#define DRAM_LOW            0x3FC88000u
#define DRAM_HIGH           0x3FD00000u

// eFuse (efuse_reg.h): EFUSE_RD_MAC_SPI_SYS_4_REG, PSRAM_CAP [4:3] and
// PSRAM_VENDOR [8:7] (EFUSE_PSRAM_CAP_S 3, EFUSE_PSRAM_VENDOR_S 7, two bits
// each; the codes in efuse/esp32s3/esp_efuse_table.csv). Read only.
#define EFUSE_SYS_4         0x60007054u
#define PSRAM_CAP_SHIFT     3u
#define PSRAM_VENDOR_SHIFT  7u
#define EFUSE_FIELD_MASK    3u

// RTC_CNTL_DIG_PAD_HOLD_REG (rtc_cntl_reg.h): the ROM's `gpio_pad_unhold`
// (0x4005_0220-0x4005_023E) clears bit `pad - 21` of this word for a pad
// from 22 to 48. Pads 26 to 28 and 33 to 37, the ones S1 writes, are bits 5
// to 7 and 12 to 16. Read only.
#define DIG_PAD_HOLD        0x600080DCu
#define PSRAM_PADS_HELD     ((0x7u << (26 - 21)) | (0x1Fu << (33 - 21)))

// The system clock (system_reg.h, spi_mem_reg.h): SYSCLK_CONF's SOC_CLK_SEL
// [11:10], PRE_DIV_CNT [9:0] and CLK_DIV_EN [19]; CPU_PER_CONF's
// PLL_FREQ_SEL [2] and CPUPERIOD_SEL [1:0]; SPI_MEM_CORE_CLK_SEL(0) [1:0].
// Read only: no step of this file writes any of them.
#define SYSCLK_CONF         0x600C0060u
#define SYSCLK_MASK         0x00080FFFu
#define CPU_PER_CONF        0x600C0010u
#define CPU_PER_MASK        0x7u
#define CORE_CLK_SEL        0x600030ECu
#define CORE_CLK_SEL_MASK   0x3u

// IO MUX (io_mux_reg.h): IO_MUX_GPIOn_REG is IO_MUX + 4 + 4n; FUN_PD [7],
// FUN_PU [8], FUN_IE [9], FUN_DRV [11:10], MCU_SEL [14:12]. Pad 26 is
// SPICS1 at function 0 (FUNC_SPICS1_SPICS1); pads 27 and 28 are SPIHD and
// SPIWP at function 0 (FUNC_SPIHD_SPIHD, FUNC_SPIWP_SPIWP), the octal bus's
// D3 and D2; pads 33 to 37 are SPIIO4 to SPIIO7 and SPIDQS at function 4
// (FUNC_GPIO33_SPIIO4 ... FUNC_GPIO37_SPIDQS).
#define IO_MUX_GPIO(n)      (0x60009004u + 4u * (n))
#define FUN_PD              (1u << 7)
#define FUN_PU              (1u << 8)
#define FUN_IE              (1u << 9)
#define FUN_DRV_SHIFT       10u
#define FUN_DRV_MASK        (3u << 10)
#define MCU_SEL_SHIFT       12u
#define MCU_SEL_MASK        (7u << 12)
#define PAD_CS1             26u
#define PAD_HD              27u
#define PAD_WP              28u
#define PAD_IO4             33u
#define PAD_DQS             37u
#define FUNC_SPICS1         0u
#define FUNC_SPIHD_WP       0u
#define FUNC_SPIIO          4u
#define PSRAM_PAD_DRIVE     3u

// GPIO_FUNCn_IN_SEL_CFG_REG (gpio_reg.h, GPIO + 0x154 + 4n), SIG_IN_SEL bit
// 7: clear routes signal n straight from the IO MUX. Signals 2 and 3 are
// SPIHD and SPIWP in, the ROM's table's signals for pads 27 and 28; 7 to 11
// are SPIIO4 to SPIIO7 and SPIDQS (the ROM's `esp_rom_opiflash_pin_config`
// tables at 0x3FF1_90A8; `gpio_bypass_matrix_in` clears bit 7 of word
// (sig + 0x18001055) << 2).
#define GPIO_IN_SEL(n)      (0x60004154u + 4u * (n))
#define SIG_IN_SEL          (1u << 7)
#define SIG_HD              2u
#define SIG_WP              3u
#define SIG_FIRST           7u
#define SIG_LAST            11u

// SYSCON_SRAM_ACE0_ATTR_REG (syscon_reg.h), bit 8: the PSRAM's ECC
// (IDF esp_psram/esp32s3/esp_psram_impl_octal.c, OCT_PSRAM_ECC_ENABLE_MASK).
#define SRAM_ACE0_ATTR      0x60026058u
#define ECC_ON              (1u << 8)

// The memory-SPI controllers (spi_mem_reg.h): SPI_MEM_<NAME>_REG(i) is the
// base of SPI1 (i = 1) or SPI0 (i = 0) plus the offset.
#define SPI1                0x60002000u
#define SPI0                0x60003000u
#define SPI_CMD             0x00u
#define SPI_ADDR            0x04u
#define SPI_CTRL            0x08u
#define SPI_CTRL2           0x10u
#define SPI_CLOCK           0x14u
#define SPI_USER            0x18u
#define SPI_USER1           0x1Cu
#define SPI_USER2           0x20u
#define SPI_MOSI_DLEN       0x24u
#define SPI_MISO_DLEN       0x28u
#define SPI_MISC            0x34u
#define SPI_CACHE_FCTRL     0x3Cu
#define SPI_CACHE_SCTRL     0x40u
#define SPI_SRAM_CMD        0x44u
#define SPI_SRAM_DRD_CMD    0x48u
#define SPI_SRAM_DWR_CMD    0x4Cu
#define SPI_SRAM_CLK        0x50u
#define SPI_W0              0x58u
#define SPI_TIMING_CALI     0xA8u
#define SPI_DIN_MODE        0xACu
#define SPI_DIN_NUM         0xB0u
#define SPI_SMEM_AC         0xDCu
#define SPI_DDR             0xE0u
#define SPI_SMEM_DDR        0xE4u

// SPI_MEM_CMD: SPI_MEM_USR (bit 18) starts a user command and reads back 0
// when it has finished. The bits beside it are the controller's built-in
// flash commands; none is ever written.
#define SPI_MEM_USR         (1u << 18)

// SPI_MEM_MISC: CS0_DIS (bit 0), CS1_DIS (bit 1). The gate is bits [1:0] =
// 0b01: chip select 0, the flash's, disabled, and chip select 1 enabled, as
// the ROM's `esp_rom_spi_cmd_start` sets them for IDF's mask BIT(1)
// (0x4004_E0E2-0x4004_E148).
#define MISC_CS_MASK        3u
#define MISC_GATE           1u

// The flash's: [1:0] = 0b10, chip select 1 disabled and chip select 0, the
// flash's, enabled. It is the word the ROM's boot leaves (../ABI.md §15c)
// and the one the PSRAM's S10 puts back; a flash read requires it and does
// not write it.
#define MISC_FLASH          2u

// SPI_MEM_SPI_SMEM_AC(0), the PSRAM's chip-select timing, shared by SPI0 and
// SPI1 (IDF `s_set_psram_cs_timing`): CS_SETUP [0], CS_HOLD [1],
// CS_SETUP_TIME [6:2] = 3, CS_HOLD_TIME [11:7] = 3, CS_HOLD_DELAY [30:25] = 2
// (OCT_PSRAM_CS_SETUP_TIME, _HOLD_TIME, _HOLD_DELAY).
#define SMEM_AC_FIELDS      (3u | (0x1Fu << 2) | (0x1Fu << 7) | (0x3Fu << 25))
#define SMEM_AC_PSRAM       (3u | (3u << 2) | (3u << 7) | (2u << 25))

// SPI_MEM_DDR(1) (the SPI_FMEM_* fields): DDR_EN [0], VAR_DUMMY [1],
// RDAT_SWP [2], WDAT_SWP [3], DDR_CMD_DIS [4]. The ROM's
// `esp_rom_spi_set_op_mode` for octal DTR (mode 7, its jump table's entry
// at 0x4004_DD2F) clears DDR_CMD_DIS and sets DDR_EN (0x4004_DD48-0x4004_DD69);
// `esp_rom_spi_set_dtr_swap_mode(1, false, false)` clears both swaps; IDF's
// `esp_psram_impl_enable` sets VAR_DUMMY.
#define DDR_CLEAR           ((1u << 4) | (1u << 3) | (1u << 2))
#define DDR_SET             ((1u << 1) | (1u << 0))

// SPI_MEM_CTRL(1) for octal DTR: the ROM's `esp_rom_spi_reset_rw_mode` mask
// and mode 7's bits, FASTRD_MODE [13], FCMD_OCT [9], FADDR_OCT [6],
// FDIN_OCT [5], FDOUT_OCT [4] (0x4004_DD3D's literal 0x2270).
#define CTRL_KEEP           0xFE6F9C0Fu
#define CTRL_OCTAL          0x00002270u

// SPI_MEM_CTRL(0) for the cache's reads (W5.1, W5.5): FDUMMY_OUT [3], Q_POL
// [18] and D_POL [19], IDF's `bootloader_flash_set_dummy_out`: in the dummy
// phase the controller drives the lines, high, so the read's mode bits
// M5..M4 go out as 11b and never ask the flash for continuous read. Then the
// read mode, over `CTRL_KEEP` (the ROM's `esp_rom_spiflash_config_readmode`
// mask, which keeps those three bits): FASTRD_MODE [13] with FREAD_DIO [23]
// or FREAD_QIO [24], the ROM's `CSWTCH$195` words for DIO and QIO
// (0x3FF1_E060).
#define CTRL_DUMMY_OUT      ((1u << 3) | (1u << 18) | (1u << 19))
#define CTRL_DIO            0x00802000u
#define CTRL_QIO            0x01002000u

// SPI_MEM_CTRL2(0) (W5): CS_SETUP_TIME [4:0] and CS_HOLD_TIME [9:5], both
// 0 in IDF's `bootloader_flash_cs_timing_config` for a flash that is not
// octal; written only when either reads otherwise.
#define CTRL2_CS_TIMES      0x3FFu

// SPI0's USER, USER1 and USER2 for the cache's reads (W5.6 to W5.8), the ROM's
// `spi_cache_mode_switch` (0x4004_A190) for QIO and DIO: USER's USR_MOSI
// [27] cleared and USR_ADDR, USR_DUMMY and USR_MISO [30:28] set; USER1's
// address length [31:26] 23, a 24-bit address, over the rest of its word
// but the dummy, and its dummy [5:0] the wait less one, 5 for QIO's 6 and 3
// for DIO's 4 (the ROM adds its extra dummy for SPI0, 0 on the part); and
// USER2's command [15:0] EBh or BBh over its 16-bit length.
#define USER_CACHE_SET      0x70000000u
#define USER1_CACHE_KEEP    0x03FFFFC0u
#define USER1_CACHE_ADDR    0x5C000000u
#define USER1_DUMMY_QIO     5u
#define USER1_DUMMY_DIO     3u
#define USER2_CACHE_KEEP    0xFFFF0000u
#define FLASH_FAST_QIO      0xEBu
#define FLASH_FAST_DIO      0xBBu

// SPI_MEM_CLOCK(0) for the flash (W5.9, the last store), as IDF's
// `mspi_timing_ll_set_flash_clock` encodes a divider: CLK_EQU_SYSCLK [31]
// alone for 1, the ROM's `esp_rom_spiflash_config_clk(1, 0)` word too; for
// 2, CLKCNT_N [23:16] and CLKCNT_L [7:0] 1 and CLKCNT_H [15:8] 0.
#define CLOCK_DIV_1         0x80000000u
#define CLOCK_DIV_2         0x00010001u

// EXTMEM_ICACHE_AUTOLOAD_CTRL_REG and EXTMEM_DCACHE_AUTOLOAD_CTRL_REG
// (extmem_reg.h), AUTOLOAD_ENA bit 2: a cache that fetches on its own. Read
// only.
#define ICACHE_AUTOLOAD     0x600C40A0u
#define DCACHE_AUTOLOAD     0x600C404Cu
#define AUTOLOAD_ENA        (1u << 2)

// EXTMEM_ICACHE_FREEZE_REG (0x600C_4154) and EXTMEM_DCACHE_FREEZE_REG
// (0x600C_4150) (extmem_reg.h): FREEZE_ENA [0] freezes the cache, so no
// miss of it, the CPU's, a preload's or an autoload's, reaches SPI0;
// FREEZE_MODE [1], 0, answers a CPU's miss busy until the cache is
// unfrozen, IDF's CACHE_FREEZE_ACK_BUSY (rom/cache.h); FREEZE_DONE [2],
// read only, is set once the cache has stopped and clear once it runs again.
// The ROM's `Cache_Freeze_ICache_Enable` (0x4004_E8A8) clears FREEZE_MODE
// for ACK_BUSY in one store, sets FREEZE_ENA in a second and reads until
// FREEZE_DONE is set; `Cache_Freeze_ICache_Disable` (0x4004_E8E8) clears
// FREEZE_ENA and reads until FREEZE_DONE is clear; the DCache's
// (0x4004_E910, 0x4004_E950) are the same on their own word.
#define ICACHE_FREEZE       0x600C4154u
#define DCACHE_FREEZE       0x600C4150u
#define FREEZE_ENA          (1u << 0)
#define FREEZE_MODE_ERROR   (1u << 1)
#define FREEZE_DONE         (1u << 2)

// The canary and the timed regions (../ABI.md §18e): the caller's block in
// the flash's data window, 64 bytes of canary, then two regions of 4 KiB,
// each read at 16-byte strides. The canary's bytes are `canary_byte`'s.
#define CANARY_BYTES        64u
#define TIMED_BYTES         4096u
#define TIMED_STRIDE        16u
// The canary's first eight bytes in pairs, each byte then its complement:
// 0x55 0xAA, 0x00 0xFF, 0x0F 0xF0, 0x33 0xCC.
#define CANARY_PAIRS        0x330F0055u
#define CANARY_PATTERN      8u

// SYSTIMER (systimer_reg.h): UNIT0_OP's UPDATE [30] latches UNIT0's count,
// VALUE_VALID [29] says it is latched, and UNIT0_VALUE_LO holds its low
// word, ticks of 1/16 us. The clock keys start the counter.
#define SYSTIMER_UNIT0_OP   0x60023004u
#define SYSTIMER_UPDATE     (1u << 30)
#define SYSTIMER_VALID      (1u << 29)
#define SYSTIMER_UNIT0_LO   0x60023044u

// SPI_MEM_CTRL(1) for a flash read: every bit clear but WP [21], as IDF's
// `bootloader_flash_execute_command_common` writes it (`ctrl.val = 0`, then
// `spimem_flash_ll_set_wp_level(true)`): command, address and data on one
// line, no fast-read mode, no two-byte status write.
#define CTRL_WP             (1u << 21)

// SPI_MEM_USER(1): USR_COMMAND [31], USR_ADDR [30], USR_DUMMY [29],
// USR_MISO [28], USR_MOSI [27]; [15:12] are the FWRITE_* line modes and
// [31:24] the phases, both cleared; the rest kept.
#define USER_KEEP           0x00FF0FFFu
#define USR_COMMAND         (1u << 31)
#define USR_ADDR            (1u << 30)
#define USR_DUMMY           (1u << 29)
#define USR_MISO            (1u << 28)
#define USR_MOSI            (1u << 27)

// SPI_MEM_USER1(1): USR_ADDR_BITLEN [31:26] = 31, a 32-bit address;
// USR_DUMMY_CYCLELEN [5:0] = cycles - 1. SPI_MEM_USER2(1):
// USR_COMMAND_BITLEN [31:28] = 15, a 16-bit command, and the command in
// [15:0]. SPI_MEM_CACHE_FCTRL(1) bit 1, CACHE_USR_CMD_4BYTE: the ROM's
// `esp_rom_spi_cmd_config` sets it for a 32-bit address.
#define USER1_ADDR_SHIFT    26u
#define USER2_CMD_SHIFT     28u
#define FCTRL_ADDR_32       (1u << 1)

// SPI_MEM_CACHE_FCTRL(1)'s line modes for SPI1's commands: FDIN_DUAL [3],
// FDOUT_DUAL [4], FADDR_DUAL [5], FDIN_QUAD [6], FDOUT_QUAD [7], FADDR_QUAD
// [8] ("When SPI1 accesses to flash or Ext_RAM, set this bit to enable 2-bm
// / 4-bm in DIN / DOUT / ADDR phase"). A flash read clears CACHE_USR_CMD_4BYTE
// as IDF's `spimem_flash_ll_set_addr_bitlen` does for any address but a
// 32-bit one, and clears these six as well. That second clear is this
// file's own, stricter than IDF, whose helper sets only CACHE_USR_CMD_4BYTE,
// USER1's address length and USER's USR_ADDR: with any of them set, a read
// meant for one line would go out on two or four.
#define FCTRL_LINES         (0x3Fu << 3)

// The flash's reads, the only commands sent with chip select 0 enabled
// (IDF spi_flash/include/spi_flash/spi_flash_defs.h, bootloader_flash.c,
// flash_qio_mode.c): the JEDEC ID, status registers 1, 2 and 3, and SFDP,
// which takes a 24-bit address and 8 dummy cycles
// (`bootloader_flash_read_sfdp`).
#define FLASH_RDID          0x9Fu
#define FLASH_RDSR          0x05u
#define FLASH_RDSR2         0x35u
#define FLASH_RDSR3         0x15u
#define FLASH_RDSFDP        0x5Au
#define SFDP_ADDR_BITS      24u
#define SFDP_DUMMY          8u

// The RAM chip's commands and mode registers (IDF esp_psram_impl_octal.c:
// OPI_PSRAM_REG_READ, OPI_PSRAM_REG_WRITE, OPI_PSRAM_SYNC_READ;
// OCT_PSRAM_RD_REG_DUMMY_BITLEN 2 x (5 - 1), OCT_PSRAM_RD_DUMMY_BITLEN
// 2 x (10 - 1); `opi_psram_mode_reg_t`'s fields).
#define CMD_REG_READ        0x4040u
#define CMD_REG_WRITE       0xC0C0u
#define CMD_SYNC_READ       0x0000u
#define CMD_SYNC_WRITE      0x8080u
#define REG_DUMMY           8u
#define ARRAY_DUMMY         18u
#define ARRAY_WRITE_DUMMY   8u
#define MR1_VENDOR_MASK     0x1Fu
#define MR2_GOOD_DIE        0x80u
#define MR2_DENSITY_MASK    0x07u
// MR0: drive strength [1:0] 0, read latency code [4:2] 2, fixed latency
// [5] 1; [7:6] kept (IDF `esp_psram_impl_enable`'s lt 1, read_latency 2,
// drive_str 0).
#define MR0_KEEP            0xC0u
#define MR0_MODE            0x28u
#define MR0_MODE_MASK       0x3Fu

// S11, SPI0's PSRAM side, which the cache's fills and write-backs to chip
// select 1 use (IDF `s_config_psram_spi_phases`; spi_mem_reg.h). Every word
// is read, its fields changed, and written back:
//   SPI_MEM_CACHE_SCTRL(0): CACHE_USR_SCMD_4BYTE [0], USR_WR_SRAM_DUMMY [3],
//     USR_RD_SRAM_DUMMY [4], CACHE_SRAM_USR_RCMD [5], CACHE_SRAM_USR_WCMD
//     [20] and SRAM_OCT [21] set; SRAM_RDUMMY_CYCLELEN [11:6] the array
//     read's dummy less one (OCT_PSRAM_RD_DUMMY_BITLEN, 2 x (10 - 1)),
//     SRAM_ADDR_BITLEN [19:14] 31 (OCT_PSRAM_ADDR_BITLEN 32, less one) and
//     SRAM_WDUMMY_CYCLELEN [27:22] the array write's dummy less one
//     (OCT_PSRAM_WR_DUMMY_BITLEN, 2 x (5 - 1));
//   SPI_MEM_SRAM_DRD_CMD(0) and SPI_MEM_SRAM_DWR_CMD(0): the command's bit
//     length less one [31:28], 15, and the command [15:0],
//     OPI_PSRAM_SYNC_READ and OPI_PSRAM_SYNC_WRITE;
//   SPI_MEM_SPI_SMEM_DDR(0): SMEM_DDR_EN [0] and SMEM_VAR_DUMMY [1] set,
//     SMEM_DDR_RDAT_SWP [2] and SMEM_DDR_WDAT_SWP [3] clear;
//   SPI_MEM_SRAM_CMD(0): SDIN_OCT [18], SDOUT_OCT [19], SADDR_OCT [20],
//     SCMD_OCT [21] and SDUMMY_OUT [22] set.
// None of SPI0's flash-side words is written.
#define SCTRL_SET           ((1u << 0) | (1u << 3) | (1u << 4) | (1u << 5) \
                             | (1u << 20) | (1u << 21))
#define SCTRL_FIELDS        ((0x3Fu << 6) | (0x3Fu << 14) | (0x3Fu << 22))
#define SCTRL_LENGTHS       (((ARRAY_DUMMY - 1u) << 6) | (31u << 14) \
                             | ((ARRAY_WRITE_DUMMY - 1u) << 22))
#define SRAM_CMD_FIELDS     ((0xFu << 28) | 0xFFFFu)
#define SRAM_CMD_16         (15u << 28)
#define SMEM_DDR_SET        ((1u << 1) | (1u << 0))
#define SMEM_DDR_CLEAR      ((1u << 3) | (1u << 2))
#define SRAM_CMD_OCTAL      ((1u << 18) | (1u << 19) | (1u << 20) \
                             | (1u << 21) | (1u << 22))

// S12, the cache's map (IDF soc.h DR_REG_MMU_TABLE; ext_mem_defs.h;
// hal/esp32s3/include/hal/mmu_ll.h `mmu_ll_write_entry`): one table of 512
// words serves both buses, entry n naming the 64 KiB page at data-bus
// 0x3C00_0000 + n x 64 KiB and instruction-bus 0x4200_0000 + n x 64 KiB. An
// entry is a page number, with SOC_MMU_ACCESS_SPIRAM (bit 15) for a page of
// the PSRAM and SOC_MMU_INVALID (bit 14) clear for a valid one. The entries
// below 256 are the flash's, which the kernel runs from; the PSRAM's pages
// go in from entry 256, data-bus 0x3D00_0000, one for each 64 KiB of the
// described density: `MMU_PAGES_PER_DENSITY << (density >> 1)`, 64 entries
// for MR2's code 1 (4 MiB), 128 for 3 (8 MiB) and 256 for 5 (16 MiB), the
// last filling the table. No entry past the table is written: a description
// larger than what is left there maps to the table's end, and the read-back
// test over the described size then faults at the first address the map
// does not reach.
#define MMU_TABLE           0x600C5000u
#define MMU_ENTRIES         512u
#define MMU_PSRAM_FIRST     256u
#define MMU_ACCESS_SPIRAM   (1u << 15)
#define MMU_PAGES_PER_DENSITY 64u

// EXTMEM_DCACHE_CTRL1_REG: a bit set shuts one core's data bus to the cache,
// and nothing the cache maps is reachable from that core. Which bit is core
// 0's, the core the kernel runs on, the sources disagree: extmem_reg.h names
// bit 0 DCACHE_SHUT_CORE0_BUS and bit 1 DCACHE_SHUT_CORE1_BUS, and
// hal/esp32s3/include/hal/cache_ll.h's `cache_ll_l1_enable_bus` clears bit
// 1 for cache id 0 and bit 0 for cache id 1. So both bits are held clear.
// Read only.
#define DCACHE_CTRL1        0x600C4004u
#define DCACHE_SHUT_BUS     3u

// Every wait reads SPI_MEM_CMD(1) at most this many times (../ABI.md §16b
// says why the number).
#define POLL_BOUND          100000u

// The measurement (../ABI.md §16b): how long after its start one command
// ends, in CCOUNT cycles, found by bisection. Each probe starts the command,
// spins until CCOUNT has advanced by the probe's spin, reads
// SPI_MEM_CMD(1) once, then waits for the command as S7.9 does; the answer
// is the least spin whose read finds it finished. A command still busy
// after SPIN_LIMIT cycles is stop 0x62. The bisection makes at most
// log2(SPIN_LIMIT) + 1 probes after the first, at the limit.
#define SPIN_LIMIT          4096u

// The four measurements, by the number the caller passes: the array read of
// 2 and of 10 bytes at divide by 4, and the MR0/MR1 read at divide by 4 and
// by 8. The clock words are IDF's encoding
// (`mspi_timing_ll_set_flash_clock`): CLKCNT_N [23:16] and CLKCNT_L [7:0]
// the divider less one, CLKCNT_H [15:8] half the divider less one.
#define TIME_ARRAY_SHORT    1u
#define TIME_ARRAY_LONG     2u
#define TIME_MR_FAST        3u
#define TIME_MR_SLOW        4u
#define ARRAY_SHORT_BITS    16u
#define ARRAY_LONG_BITS     80u
#define MR_BITS             16u
#define CLOCK_DIV_4         0x00030103u
#define CLOCK_DIV_8         0x00070307u

// UART0's FIFO and status, as `sink.c`'s `kernel_fault` writes them, with the
// same bounded wait for room per byte.
#define UART0_FIFO          0x60000000u
#define UART0_STATUS        0x6000001Cu
#define UART_TXFIFO_SHIFT   16u
#define UART_TXFIFO_MASK    0x3FFu
#define UART_TX_DEPTH       128u
#define UART_POLLS          (1u << 16)

// ---- the fault record ------------------------------------------------------

#ifdef SOS_PSRAM_FAULT
// The harness fills it in before the call: the read at `step`, of `addr`, in
// the command numbered `command` (1 for the call's first, 0 for any or for a
// read outside a command), returns `value`, `count` times (0xFFFFFFFF: every
// time). The call counts what happened.
struct sos_psram_fault {
    u32 step;
    u32 command;
    u32 addr;
    u32 value;
    u32 count;
    u32 hits;               // reads answered from the record
    u32 starts;             // commands started (SPI_MEM_USR written)
    u32 w0_reads;           // reads of W0(1), the commands' answers
};

struct sos_psram_fault sos_psram_fault;

IRAM u32 sos_psram_fault_record(void) {
    return (u32)(unsigned long)&sos_psram_fault;
}

// More reads to lie to, for a harness that needs a whole machine's words
// (the switch's, ../ABI.md §18f): each of the first `count` entries answers
// every read that matches its `step`, `command` and `addr` (0 in any of the
// three: any) with its `value`, after the record above has had its turn.
// The call counts the reads it answered.
#define FAULT_WORDS 16u
struct sos_flash_fault_word {
    u32 step;
    u32 command;
    u32 addr;
    u32 value;
};

struct sos_flash_fault_table {
    u32 count;
    u32 hits;
    struct sos_flash_fault_word words[FAULT_WORDS];
};

struct sos_flash_fault_table sos_flash_fault_table;

IRAM u32 sos_flash_fault_words(void) {
    return (u32)(unsigned long)&sos_flash_fault_table;
}
#endif

// The number of the command under way in this call: 1 for the first.
static u32 command_number;

// SPI1's words as S5 found them, in `SAVED`'s order, for S10.
#define SAVED 11u
static u32 saved_spi1[SAVED];

// S5's registers, in the order S10 writes them back: MISC last, so the gate
// is the last thing undone. A flash read saves the same words and writes
// back all but the last, `SAVED_BUT_MISC`, since it never writes MISC. In
// `.data` by name, as `short_line` is.
#define SAVED_BUT_MISC (SAVED - 1u)
__attribute__((section(".data.sos_psram_saved_offsets")))
static u32 saved_offsets[SAVED] = {
    SPI_CLOCK, SPI_CTRL, SPI_USER, SPI_USER1, SPI_USER2, SPI_ADDR,
    SPI_MOSI_DLEN, SPI_MISO_DLEN, SPI_CACHE_FCTRL, SPI_DDR, SPI_MISC,
};

// The lines written by a command that does not finish, before the call
// returns: the PSRAM's command in four hex digits, the flash's in two, both
// at `SHORT_LINE_DIGITS`. In `.data` by name: a static nothing writes may
// otherwise be promoted to `.rodata`, which is flash.
#define SHORT_LINE_DIGITS   30u
__attribute__((section(".data.sos_psram_short_line")))
static char short_line[] =
    "SOS-S3: psram: SPI1 command 0x.... did not finish\n";
__attribute__((section(".data.sos_flash_short_line")))
static char flash_short_line[] =
    "SOS-S3: flash: SPI1 command 0x.. did not finish\n";

// The line the switch writes when its canary reads wrong, once SPI0 is put
// back, with the key's name written in: `d` or `q` at `CANARY_LINE_LINES`
// and `8` or `4` at `CANARY_LINE_SPEED`.
#define CANARY_LINE_LINES   14u
#define CANARY_LINE_SPEED   17u
__attribute__((section(".data.sos_flash_canary_line")))
static char canary_line[] =
    "SOS-S3: flash dio80: canary mismatch\n";

// The line the switch writes when a cache does not report its unfreeze:
// the caller's next fetch from flash may then never be answered.
__attribute__((section(".data.sos_flash_thaw_line")))
static char thaw_line[] =
    "SOS-S3: flash: a cache did not report its unfreeze\n";

// The words `sos_flash_switch` reads before and after its stores, in
// `struct sos_flash_words`' order. In `.data` by name, as `short_line` is.
__attribute__((section(".data.sos_flash_switch_addrs")))
static u32 switch_addrs[SWITCH_WORDS] = {
    SPI0 + SPI_CLOCK, SPI0 + SPI_CTRL, SPI0 + SPI_CTRL2, SPI0 + SPI_USER,
    SPI0 + SPI_USER1, SPI0 + SPI_USER2, SPI0 + SPI_DDR, SPI0 + SPI_TIMING_CALI,
    SPI0 + SPI_DIN_MODE, SPI0 + SPI_DIN_NUM, IO_MUX_GPIO(PAD_HD),
    IO_MUX_GPIO(PAD_WP), GPIO_IN_SEL(SIG_HD), GPIO_IN_SEL(SIG_WP),
};

// ---- access -----------------------------------------------------------------

#ifdef SOS_PSRAM_FAULT
// The fault table's answer for the read at `step` of `addr`, or `v`, what
// the register read.
static __attribute__((noinline)) IRAM u32 fault_word(u32 step, u32 addr,
                                                     u32 v) {
    struct sos_flash_fault_table *t = &sos_flash_fault_table;
    for (u32 i = 0; i < t->count && i < FAULT_WORDS; i++) {
        struct sos_flash_fault_word *w = &t->words[i];
        if ((w->step == 0 || w->step == step)
            && (w->command == 0 || w->command == command_number)
            && (w->addr == 0 || w->addr == addr)) {
            t->hits++;
            return w->value;
        }
    }
    return v;
}
#endif

static inline IRAM u32 rd(u32 step, u32 addr) {
    u32 v = *(volatile u32 *)(unsigned long)addr;
#ifdef SOS_PSRAM_FAULT
    struct sos_psram_fault *f = &sos_psram_fault;
    if (addr == SPI1 + SPI_W0) {
        f->w0_reads++;
    }
    if (f->count != 0 && f->step == step && f->addr == addr
        && (f->command == 0 || f->command == command_number)) {
        if (f->count != 0xFFFFFFFFu) {
            f->count--;
        }
        f->hits++;
        return f->value;
    }
    if (sos_flash_fault_table.count != 0) {
        return fault_word(step, addr, v);
    }
#else
    (void)step;
#endif
    return v;
}

static inline IRAM void wr(u32 addr, u32 v) {
    *(volatile u32 *)(unsigned long)addr = v;
#ifdef SOS_PSRAM_FAULT
    if (addr == SPI1 + SPI_CMD && (v & SPI_MEM_USR)) {
        sos_psram_fault.starts++;
    }
#endif
}

static inline IRAM u32 ccount(void) {
    u32 v;
    __asm__ volatile("rsr %0, ccount" : "=a"(v));
    return v;
}

static IRAM u32 stop(struct sos_psram_record *r, u32 code, u32 read,
                     u32 expect) {
    r->stop = code;
    r->read = read;
    r->expect = expect;
    return code;
}

// ---- the short lines --------------------------------------------------------

static IRAM void uart_byte(u32 b) {
    for (u32 polls = 0; polls < UART_POLLS; polls++) {
        u32 cnt = (*(volatile u32 *)UART0_STATUS >> UART_TXFIFO_SHIFT)
                  & UART_TXFIFO_MASK;
        if (cnt < UART_TX_DEPTH) {
            break;
        }
    }
    *(volatile u32 *)UART0_FIFO = b;
}

// `line`, `len` bytes, with `command`'s low `digits` hex digits written in at
// `SHORT_LINE_DIGITS`.
static IRAM void short_line_out(char *line, u32 len, u32 digits,
                                u32 command) {
    for (u32 i = 0; i < digits; i++) {
        u32 d = (command >> (4 * (digits - 1 - i))) & 0xFu;
        line[SHORT_LINE_DIGITS + i] = (char)(d < 10 ? '0' + d
                                                    : 'a' + d - 10);
    }
    for (u32 i = 0; i < len; i++) {
        uart_byte((u32)(unsigned char)line[i]);
    }
}

// ---- the steps --------------------------------------------------------------

// The bounded wait: SPI_MEM_CMD(1) read until it is 0, at most POLL_BOUND
// times. Answers 1 when idle; otherwise leaves the last word read in `*read`
// and answers 0.
static IRAM u32 poll_idle(u32 step, u32 *read) {
    u32 v = 0;
    for (u32 n = 0; n < POLL_BOUND; n++) {
        v = rd(step, SPI1 + SPI_CMD);
        if (v == 0) {
            return 1;
        }
    }
    *read = v;
    return 0;
}

// The same, leaving the last word in `r->read`.
static IRAM u32 wait_idle(struct sos_psram_record *r, u32 step) {
    return poll_idle(step, &r->read);
}

// Every word of `saved_offsets` read into `saved_spi1`.
static IRAM void save_spi1(u32 step) {
    for (u32 i = 0; i < SAVED; i++) {
        saved_spi1[i] = rd(step, SPI1 + saved_offsets[i]);
    }
}

// S5: save SPI1, put the commands' clock word and DTR mode on it.
static IRAM void prepare_spi1(u32 clock, struct sos_psram_record *r) {
    save_spi1(STEP_S5);
    r->saved = 1;
    wr(SPI1 + SPI_CLOCK, clock);
    r->spi1_clock = rd(STEP_S5, SPI1 + SPI_CLOCK);
    u32 ddr = rd(STEP_S5, SPI1 + SPI_DDR);
    wr(SPI1 + SPI_DDR, (ddr & ~DDR_CLEAR) | DDR_SET);
}

// The first `count` words `save_spi1` saved, written back in its order: all
// of them (`SAVED`, S10), MISC last, or all but MISC (`SAVED_BUT_MISC`).
static IRAM void restore_spi1(u32 count) {
    for (u32 i = 0; i < count; i++) {
        wr(SPI1 + saved_offsets[i], saved_spi1[i]);
    }
}

// S6: the gate. Answers 1 when MISC(1) reads back 0b01 in [1:0].
static IRAM u32 take_gate(struct sos_psram_record *r) {
    u32 misc = rd(STEP_S6, SPI1 + SPI_MISC);
    wr(SPI1 + SPI_MISC, (misc & ~MISC_CS_MASK) | MISC_GATE);
    u32 back = rd(STEP_S6, SPI1 + SPI_MISC);
    r->misc_gate = back;
    if ((back & MISC_CS_MASK) != MISC_GATE) {
        stop(r, STOP_GATE, back, MISC_GATE);
        return 0;
    }
    return 1;
}

// One SPI1 user command, as `link_start` sends it. `cs` is the chip select
// it goes to, as MISC(1) [1:0] must read for it: `MISC_GATE`, the PSRAM's,
// or `MISC_FLASH`; `link_start` compares MISC(1) with it and never writes
// MISC(1). CTRL(1) and CACHE_FCTRL(1) become (word & keep) | set. The
// command, the address and the dummy are `cmd_bits`, `addr_bits` (0: no
// address phase) and `dummy` cycles (0: none); `out_bits` and `in_bits` are
// the data phases, 0 for none. The callers set every field one by one, so
// no initializer becomes a copy from `.rodata`.
struct cmd {
    u32 cs;
    u32 ctrl_keep;
    u32 ctrl_set;
    u32 fctrl_keep;
    u32 fctrl_set;
    u32 cmd;
    u32 cmd_bits;
    u32 addr;
    u32 addr_bits;
    u32 dummy;
    u32 out_bits;
    u32 out;
    u32 in_bits;
};

// What `link_start` and `link_finish` answer besides 0: SPI1 busy before
// the command (nothing started), MISC(1) not reading the command's chip
// select (nothing started), the command not finished.
#define LINK_BUSY   1u
#define LINK_GATE   2u
#define LINK_STUCK  3u

// S7.1 to S7.9's start: the command `c` set up and started. Answers 0, or
// `LINK_BUSY` or `LINK_GATE` with the word it read in `*read`.
static IRAM u32 link_start(struct cmd *c, u32 *read) {
    command_number++;
    // S7.1
    if (!poll_idle(STEP_S7_1, read)) {
        return LINK_BUSY;
    }
    // S7.2
    u32 ctrl = rd(STEP_S7, SPI1 + SPI_CTRL);
    wr(SPI1 + SPI_CTRL, (ctrl & c->ctrl_keep) | c->ctrl_set);
    // S7.3
    u32 phases = USR_COMMAND;
    if (c->addr_bits != 0) {
        phases |= USR_ADDR;
    }
    if (c->dummy != 0) {
        phases |= USR_DUMMY;
    }
    if (c->in_bits != 0) {
        phases |= USR_MISO;
    }
    if (c->out_bits != 0) {
        phases |= USR_MOSI;
    }
    u32 user = rd(STEP_S7, SPI1 + SPI_USER);
    wr(SPI1 + SPI_USER, (user & USER_KEEP) | phases);
    // S7.4, S7.5
    u32 addr_field = c->addr_bits != 0 ? c->addr_bits - 1 : 0;
    wr(SPI1 + SPI_USER1, (addr_field << USER1_ADDR_SHIFT)
                         | (c->dummy != 0 ? c->dummy - 1 : 0));
    wr(SPI1 + SPI_USER2, ((c->cmd_bits - 1) << USER2_CMD_SHIFT) | c->cmd);
    // S7.6
    if (c->addr_bits != 0) {
        wr(SPI1 + SPI_ADDR, c->addr);
    }
    u32 fctrl = rd(STEP_S7, SPI1 + SPI_CACHE_FCTRL);
    wr(SPI1 + SPI_CACHE_FCTRL, (fctrl & c->fctrl_keep) | c->fctrl_set);
    // S7.7
    if (c->out_bits != 0) {
        wr(SPI1 + SPI_MOSI_DLEN, c->out_bits - 1);
        wr(SPI1 + SPI_W0, c->out);
    }
    if (c->in_bits != 0) {
        wr(SPI1 + SPI_MISO_DLEN, c->in_bits - 1);
    }
    // S7.8
    u32 misc = rd(STEP_S7_8, SPI1 + SPI_MISC);
    if ((misc & MISC_CS_MASK) != c->cs) {
        *read = misc;
        return LINK_GATE;
    }
    wr(SPI1 + SPI_CMD, SPI_MEM_USR);
    return 0;
}

// S7.9's wait and S7.10, for the command `link_start` started. A command that
// does not finish says so from here first, in its chip select's line, and
// answers `LINK_STUCK` with the word read in `*read`. The answer is W0(1),
// read when `answer` is not 0.
static IRAM u32 link_finish(struct cmd *c, u32 *read, u32 *answer) {
    if (!poll_idle(STEP_S7_9, read)) {
        if (c->cs == MISC_FLASH) {
            short_line_out(flash_short_line, sizeof(flash_short_line) - 1,
                           2, c->cmd);
        } else {
            short_line_out(short_line, sizeof(short_line) - 1, 4, c->cmd);
        }
        return LINK_STUCK;
    }
    // S7.10
    if (answer != 0) {
        *answer = rd(STEP_S7_10, SPI1 + SPI_W0);
    }
    return 0;
}

// S7.1 to S7.9's start for the PSRAM: one command to chip select 1 in octal
// DTR, a 16-bit command and a 32-bit address, described in `c`. Answers 0,
// or the stop code with the record filled in.
static IRAM u32 command_start(struct sos_psram_record *r, struct cmd *c,
                              u32 cmd, u32 addr, u32 dummy, u32 out_bits,
                              u32 out, u32 in_bits) {
    r->command = cmd;
    r->address = addr;
    c->cs = MISC_GATE;
    c->ctrl_keep = CTRL_KEEP;
    c->ctrl_set = CTRL_OCTAL;
    c->fctrl_keep = 0xFFFFFFFFu;
    c->fctrl_set = FCTRL_ADDR_32;
    c->cmd = cmd;
    c->cmd_bits = 16;
    c->addr = addr;
    c->addr_bits = 32;
    c->dummy = dummy;
    c->out_bits = out_bits;
    c->out = out;
    c->in_bits = in_bits;
    u32 code = link_start(c, &r->read);
    if (code == LINK_BUSY) {
        return stop(r, STOP_BUSY_COMMAND, r->read, 0);
    }
    if (code == LINK_GATE) {
        return stop(r, STOP_GATE_LOST, r->read, MISC_GATE);
    }
    return 0;
}

// S7.9's wait and S7.10 for the PSRAM's command: one that does not finish
// leaves the gate as it is, so a transfer still under way cannot be turned
// towards the flash by a restore.
static IRAM u32 command_finish(struct sos_psram_record *r, struct cmd *c,
                               u32 *answer) {
    if (link_finish(c, &r->read, answer)) {
        return stop(r, STOP_NOT_FINISHED, r->read, 0);
    }
    return 0;
}

// S7: one command, started and waited for. Answers 0 or the stop code.
static IRAM u32 command(struct sos_psram_record *r, u32 cmd, u32 addr,
                       u32 dummy, u32 out_bits, u32 out, u32 in_bits,
                       u32 *answer) {
    struct cmd c;
    u32 code = command_start(r, &c, cmd, addr, dummy, out_bits, out,
                             in_bits);
    if (code) {
        return code;
    }
    return command_finish(r, &c, answer);
}

// The checks before anything is written: the entry check, then S0.1 to
// S0.4. Answers 0 or the stop code.
static IRAM u32 checks_before(struct sos_psram_expect *e,
                              struct sos_psram_record *r) {
    // S0.1
    u32 efuse = rd(STEP_S0_1, EFUSE_SYS_4);
    r->efuse = efuse;
    if (((efuse >> PSRAM_CAP_SHIFT) & EFUSE_FIELD_MASK) != e->efuse_cap
        || ((efuse >> PSRAM_VENDOR_SHIFT) & EFUSE_FIELD_MASK)
           != e->efuse_vendor) {
        return stop(r, STOP_EFUSE, efuse,
                    (e->efuse_cap << PSRAM_CAP_SHIFT)
                    | (e->efuse_vendor << PSRAM_VENDOR_SHIFT));
    }
    // S0.2
    u32 hold = rd(STEP_S0_2, DIG_PAD_HOLD);
    r->pad_hold = hold;
    if (hold & PSRAM_PADS_HELD) {
        return stop(r, STOP_PAD_HELD, hold, 0);
    }
    // S0.3
    r->sysclk = rd(STEP_S0_3, SYSCLK_CONF);
    r->cpu_per = rd(STEP_S0_3, CPU_PER_CONF);
    r->core_clk_sel = rd(STEP_S0_3, CORE_CLK_SEL);
    if ((r->sysclk & SYSCLK_MASK) != e->sysclk
        || (r->cpu_per & CPU_PER_MASK) != e->cpu_per
        || (r->core_clk_sel & CORE_CLK_SEL_MASK) != e->core_clk_sel) {
        return stop(r, STOP_CLOCK, r->sysclk, e->sysclk);
    }
    // S0.4
    if (!wait_idle(r, STEP_S0_4)) {
        return stop(r, STOP_BUSY_BEFORE, r->read, 0);
    }
    return 0;
}

// S1 to S4: the PSRAM's pads, its chip-select timing, ECC off, its clock for
// cache fills. Answers 0 or the stop code.
static IRAM u32 configure(struct sos_psram_expect *e,
                          struct sos_psram_record *r) {
    // S1: pad 26, then pads 27 and 28, then pads 33 to 37, then the input
    // bypass.
    u32 cs1 = rd(STEP_S1, IO_MUX_GPIO(PAD_CS1));
    wr(IO_MUX_GPIO(PAD_CS1),
       (cs1 & ~(MCU_SEL_MASK | FUN_DRV_MASK))
       | (FUNC_SPICS1 << MCU_SEL_SHIFT) | (PSRAM_PAD_DRIVE << FUN_DRV_SHIFT));
    // Pads 27 and 28 are the flash's HD and WP and the octal bus's D3 and
    // D2. The ROM's boot leaves them GPIOs when the flash reads on one line,
    // which never drives them; IDF's `esp_mspi_pin_init` (spi_flash/
    // flash_ops.c) calls `esp_rom_opiflash_pin_config` for octal PSRAM,
    // whose default branch gives each pad of its table a pull-up
    // (`gpio_pad_pullup`: FUN_PD cleared, FUN_PU set), its function, the
    // signal's bypass and input enable (`gpio_pad_input_enable`: FUN_IE).
    // This does the same for these two, and gives them drive 3 as it does
    // the PSRAM's other data pads: IDF follows the ROM call with
    // `mspi_timing_set_pin_drive_strength`, whose
    // `mspi_timing_ll_set_all_pin_drive(0, 3)` (hal/esp32s3/include/hal/
    // mspi_timing_tuning_ll.h) sets it on these pads among others. Pads 29
    // to 32, which the flash reads on, are never written, their drive
    // included.
    for (u32 pad = PAD_HD; pad <= PAD_WP; pad++) {
        u32 v = rd(STEP_S1, IO_MUX_GPIO(pad));
        wr(IO_MUX_GPIO(pad),
           (v & ~(MCU_SEL_MASK | FUN_DRV_MASK | FUN_PD))
           | (FUNC_SPIHD_WP << MCU_SEL_SHIFT) | FUN_PU | FUN_IE
           | (PSRAM_PAD_DRIVE << FUN_DRV_SHIFT));
    }
    for (u32 pad = PAD_IO4; pad <= PAD_DQS; pad++) {
        u32 v = rd(STEP_S1, IO_MUX_GPIO(pad));
        wr(IO_MUX_GPIO(pad),
           (v & ~(MCU_SEL_MASK | FUN_DRV_MASK | FUN_PD))
           | (FUNC_SPIIO << MCU_SEL_SHIFT) | FUN_PU | FUN_IE
           | (PSRAM_PAD_DRIVE << FUN_DRV_SHIFT));
    }
    for (u32 sig = SIG_HD; sig <= SIG_WP; sig++) {
        u32 v = rd(STEP_S1, GPIO_IN_SEL(sig));
        wr(GPIO_IN_SEL(sig), v & ~SIG_IN_SEL);
    }
    for (u32 sig = SIG_FIRST; sig <= SIG_LAST; sig++) {
        u32 v = rd(STEP_S1, GPIO_IN_SEL(sig));
        wr(GPIO_IN_SEL(sig), v & ~SIG_IN_SEL);
    }
    // S2
    u32 ac = rd(STEP_S2, SPI0 + SPI_SMEM_AC);
    wr(SPI0 + SPI_SMEM_AC, (ac & ~SMEM_AC_FIELDS) | SMEM_AC_PSRAM);
    // S3: ECC is off at reset; cleared and read again only if it is on.
    u32 ace0 = rd(STEP_S3, SRAM_ACE0_ATTR);
    if (ace0 & ECC_ON) {
        wr(SRAM_ACE0_ATTR, ace0 & ~ECC_ON);
        ace0 = rd(STEP_S3, SRAM_ACE0_ATTR);
        if (ace0 & ECC_ON) {
            return stop(r, STOP_ECC, ace0, 0);
        }
    }
    // S4
    wr(SPI0 + SPI_SRAM_CLK, e->divider);
    return 0;
}

// S8 and S9 under the gate. Answers 0 or the stop code.
static IRAM u32 identify_and_set(struct sos_psram_expect *e,
                                 struct sos_psram_record *r, u32 last_step) {
    u32 w = 0;
    u32 code;
    // S8.1
    u32 before = ccount();
    code = command(r, CMD_REG_READ, 0, REG_DUMMY, 0, 0, 16, &w);
    r->mr_cycles = ccount() - before;
    if (code) {
        return code;
    }
    r->mr01 = w & 0xFFFFu;
    u32 vendor = (r->mr01 >> 8) & MR1_VENDOR_MASK;
    if (vendor != e->vendor) {
        return stop(r, STOP_VENDOR, vendor, e->vendor);
    }
    // S8.2
    code = command(r, CMD_REG_READ, 2, REG_DUMMY, 0, 0, 16, &w);
    if (code) {
        return code;
    }
    r->mr23 = w & 0xFFFFu;
    u32 mr2 = r->mr23 & 0xFFu;
    if (!(mr2 & MR2_GOOD_DIE)) {
        return stop(r, STOP_DIE, mr2, MR2_GOOD_DIE);
    }
    if ((mr2 & MR2_DENSITY_MASK) != e->density) {
        return stop(r, STOP_DENSITY, mr2 & MR2_DENSITY_MASK, e->density);
    }
    // S8.3: recorded, not checked.
    code = command(r, CMD_REG_READ, 4, REG_DUMMY, 0, 0, 8, &w);
    if (code) {
        return code;
    }
    r->mr4 = w & 0xFFu;
    code = command(r, CMD_REG_READ, 8, REG_DUMMY, 0, 0, 8, &w);
    if (code) {
        return code;
    }
    r->mr8 = w & 0xFFu;
    if (last_step < STEP_S9) {
        return 0;
    }
    // S9
    u32 mr0 = ((r->mr01 & 0xFFu) & MR0_KEEP) | MR0_MODE;
    u32 mr1 = (r->mr01 >> 8) & 0xFFu;
    code = command(r, CMD_REG_WRITE, 0, 0, 16, (mr1 << 8) | mr0, 0, 0);
    if (code) {
        return code;
    }
    code = command(r, CMD_REG_READ, 0, REG_DUMMY, 0, 0, 16, &w);
    if (code) {
        return code;
    }
    r->mr01_after = w & 0xFFFFu;
    if ((r->mr01_after & MR0_MODE_MASK) != MR0_MODE) {
        return stop(r, STOP_MR0, r->mr01_after & 0xFFu, MR0_MODE);
    }
    return 0;
}

// S11: SPI0's PSRAM side, read-modify-write. No PSRAM page is mapped yet, so
// no access reaches chip select 1 while these change.
static IRAM void configure_spi0(void) {
    u32 sctrl = rd(STEP_S11, SPI0 + SPI_CACHE_SCTRL);
    wr(SPI0 + SPI_CACHE_SCTRL,
       (sctrl & ~SCTRL_FIELDS) | SCTRL_SET | SCTRL_LENGTHS);
    u32 drd = rd(STEP_S11, SPI0 + SPI_SRAM_DRD_CMD);
    wr(SPI0 + SPI_SRAM_DRD_CMD,
       (drd & ~SRAM_CMD_FIELDS) | SRAM_CMD_16 | CMD_SYNC_READ);
    u32 dwr = rd(STEP_S11, SPI0 + SPI_SRAM_DWR_CMD);
    wr(SPI0 + SPI_SRAM_DWR_CMD,
       (dwr & ~SRAM_CMD_FIELDS) | SRAM_CMD_16 | CMD_SYNC_WRITE);
    u32 ddr = rd(STEP_S11, SPI0 + SPI_SMEM_DDR);
    wr(SPI0 + SPI_SMEM_DDR, (ddr & ~SMEM_DDR_CLEAR) | SMEM_DDR_SET);
    u32 cmd = rd(STEP_S11, SPI0 + SPI_SRAM_CMD);
    wr(SPI0 + SPI_SRAM_CMD, cmd | SRAM_CMD_OCTAL);
}

// S12: the PSRAM's pages written into the MMU from entry 256, then each read
// back (stop 0x5E at the first that differs, its number in the record's
// `address`), then both data-bus shut bits checked clear (stop 0x5F). No
// cache operation: nothing has read these addresses since reset, so no line
// of them can be in the data cache. Answers 0 or the stop code.
static IRAM u32 map_psram(struct sos_psram_expect *e,
                          struct sos_psram_record *r) {
    u32 pages = MMU_PAGES_PER_DENSITY << (e->density >> 1);
    if (pages > MMU_ENTRIES - MMU_PSRAM_FIRST) {
        pages = MMU_ENTRIES - MMU_PSRAM_FIRST;
    }
    for (u32 n = 0; n < pages; n++) {
        wr(MMU_TABLE + 4u * (MMU_PSRAM_FIRST + n), MMU_ACCESS_SPIRAM | n);
    }
    for (u32 n = 0; n < pages; n++) {
        u32 back = rd(STEP_S12_2, MMU_TABLE + 4u * (MMU_PSRAM_FIRST + n));
        if (back != (MMU_ACCESS_SPIRAM | n)) {
            r->address = MMU_PSRAM_FIRST + n;
            return stop(r, STOP_MMU, back, MMU_ACCESS_SPIRAM | n);
        }
    }
    r->mapped = pages;
    u32 ctrl1 = rd(STEP_S12_3, DCACHE_CTRL1);
    if (ctrl1 & DCACHE_SHUT_BUS) {
        return stop(r, STOP_BUS_OFF, ctrl1, 0);
    }
    return 0;
}

// One probe of the measurement: the command started, CCOUNT left to advance
// by `spin`, SPI_MEM_CMD(1) read once, then the command waited for as S7.9
// waits. `*done` says whether that one read found it finished. Answers 0 or
// the stop code.
static IRAM u32 probe(struct sos_psram_record *r, u32 cmd, u32 dummy,
                      u32 in_bits, u32 spin, u32 *done) {
    struct cmd c;
    u32 code = command_start(r, &c, cmd, 0, dummy, 0, 0, in_bits);
    if (code) {
        return code;
    }
    u32 start = ccount();
    while (ccount() - start < spin) {
    }
    u32 v = rd(STEP_S7_PROBE, SPI1 + SPI_CMD);
    *done = v == 0;
    code = command_finish(r, &c, 0);
    if (code) {
        return code;
    }
    if (spin == SPIN_LIMIT && !*done) {
        return stop(r, STOP_STILL_BUSY, v, 0);
    }
    return 0;
}

// The least spin at which one probe finds the command finished, by
// bisection over [0, SPIN_LIMIT] after a first probe at the limit. Each
// probe reads finished at every spin past the answer, the command taking
// the same time every time. Answers 0 or the stop code.
static IRAM u32 bisect(struct sos_psram_record *r, u32 cmd, u32 dummy,
                       u32 in_bits, u32 *at) {
    u32 done = 0;
    u32 code = probe(r, cmd, dummy, in_bits, SPIN_LIMIT, &done);
    if (code) {
        return code;
    }
    u32 lo = 0;
    u32 hi = SPIN_LIMIT;
    while (lo < hi) {
        u32 mid = (lo + hi) >> 1;
        code = probe(r, cmd, dummy, in_bits, mid, &done);
        if (code) {
            return code;
        }
        if (done) {
            hi = mid;
        } else {
            lo = mid + 1;
        }
    }
    *at = lo;
    return 0;
}

// `searches` bisections of one measurement's command, their answers summed
// into `r->cycles`. Answers 0 or the stop code.
static IRAM u32 measure(struct sos_psram_record *r, u32 cmd, u32 dummy,
                        u32 in_bits, u32 searches) {
    r->cycles = 0;
    for (u32 i = 0; i < searches; i++) {
        u32 at = 0;
        u32 code = bisect(r, cmd, dummy, in_bits, &at);
        if (code) {
            return code;
        }
        r->cycles += at;
    }
    return 0;
}

// What `under_gate` sends: the bring-up's S8 and S9, or a measurement's
// command.
#define GATED_IDENTITY  0u
#define GATED_TIMING    1u

// What `under_gate` needs for either: S8 and S9's last step, or the
// measurement's command and how many times to bisect it.
struct gated {
    u32 what;
    u32 last_step;
    u32 cmd;
    u32 dummy;
    u32 in_bits;
    u32 searches;
};

// S5 at the clock word `clock`, S6, the commands and S10. S10 runs on every
// way out but stop 0x59.
static IRAM u32 under_gate(struct sos_psram_expect *e,
                           struct sos_psram_record *r, u32 clock,
                           struct gated *g) {
    prepare_spi1(clock, r);
    if (!take_gate(r)) {
        restore_spi1(SAVED);
        return r->stop;
    }
    u32 code = g->what == GATED_TIMING
               ? measure(r, g->cmd, g->dummy, g->in_bits, g->searches)
               : identify_and_set(e, r, g->last_step);
    if (code == STOP_NOT_FINISHED) {
        return code;
    }
    restore_spi1(SAVED);
    return code;
}

// The entry check, the call's first act: the description and the record are
// DRAM, judged by their addresses alone, without a load through either.
static IRAM u32 in_dram(u32 addr, u32 len) {
    return addr >= DRAM_LOW && addr <= DRAM_HIGH - len;
}

static IRAM u32 entry_ok(u32 expect_addr, u32 record_addr) {
    if (!in_dram(expect_addr, sizeof(struct sos_psram_expect))
        || !in_dram(record_addr, sizeof(struct sos_psram_record))) {
        return 0;
    }
    struct sos_psram_record *r =
        (struct sos_psram_record *)(unsigned long)record_addr;
    r->stop = 0;
    r->command = 0;
    r->address = 0;
    r->saved = 0;
    r->cycles = 0;
    r->mapped = 0;
    command_number = 0;
    return 1;
}

// ---- the entries ------------------------------------------------------------

// The bring-up, S0 to S12: S8's identity is checked before S9 writes the
// chip, and `last_step` ends it after S8 (STEP_S8), S9 (STEP_S9), S11
// (STEP_S11) or S12 (STEP_S12 or more). S10 puts SPI1 back once the last
// command has run, so S11 and S12 run with SPI1 as the ROM left it. Answers
// 0 or the stop code; for 0x50 nothing is written to the record, which is
// then not known to be RAM.
IRAM u32 sos_psram_bringup(u32 expect_addr, u32 record_addr, u32 last_step) {
    if (!entry_ok(expect_addr, record_addr)) {
        return STOP_NOT_DRAM;
    }
    struct sos_psram_expect *e =
        (struct sos_psram_expect *)(unsigned long)expect_addr;
    struct sos_psram_record *r =
        (struct sos_psram_record *)(unsigned long)record_addr;
    u32 code = checks_before(e, r);
    if (code) {
        return code;
    }
    code = configure(e, r);
    if (code) {
        return code;
    }
    struct gated g;
    g.what = GATED_IDENTITY;
    g.last_step = last_step;
    code = under_gate(e, r, e->divider, &g);
    if (code || last_step < STEP_S11) {
        return code;
    }
    configure_spi0();
    if (last_step < STEP_S12) {
        return 0;
    }
    return map_psram(e, r);
}

// The measurement that settles the memory-SPI core clock (../ABI.md §16b):
// `which` names a command and a divider (TIME_*), sent under the gate,
// between S5 and S10, at that divider instead of the description's; the
// record's `cycles` is the sum over `searches` bisections of how many CCOUNT
// cycles after its start the command ends. The array reads read and the
// mode-register reads read MR0/MR1; nothing is written to the chip. Any other
// `which` is stop 0x61 before SPI1 is touched. Answers 0 or the stop code
// (0x50, 0x56 to 0x59, 0x61, 0x62).
IRAM u32 sos_psram_time_reads(u32 expect_addr, u32 record_addr, u32 which,
                              u32 searches) {
    if (!entry_ok(expect_addr, record_addr)) {
        return STOP_NOT_DRAM;
    }
    struct sos_psram_expect *e =
        (struct sos_psram_expect *)(unsigned long)expect_addr;
    struct sos_psram_record *r =
        (struct sos_psram_record *)(unsigned long)record_addr;
    struct gated g;
    g.what = GATED_TIMING;
    g.last_step = 0;
    g.searches = searches;
    u32 clock = CLOCK_DIV_4;
    if (which == TIME_ARRAY_SHORT || which == TIME_ARRAY_LONG) {
        g.cmd = CMD_SYNC_READ;
        g.dummy = ARRAY_DUMMY;
        g.in_bits = which == TIME_ARRAY_SHORT ? ARRAY_SHORT_BITS
                                              : ARRAY_LONG_BITS;
    } else if (which == TIME_MR_FAST || which == TIME_MR_SLOW) {
        g.cmd = CMD_REG_READ;
        g.dummy = REG_DUMMY;
        g.in_bits = MR_BITS;
        if (which == TIME_MR_SLOW) {
            clock = CLOCK_DIV_8;
        }
    } else {
        return stop(r, STOP_MEASUREMENT, which, TIME_MR_SLOW);
    }
    return under_gate(e, r, clock, &g);
}

// ---- the flash's reads ------------------------------------------------------

// The answer's length in bits of each read the flash is sent, and 0 for any
// other opcode: these five are the only commands this file sends with chip
// select 0 enabled. None changes the flash, sets its write-enable latch or
// powers it down.
static IRAM u32 flash_read_bits(u32 op) {
    if (op == FLASH_RDID) {
        return 24;
    }
    if (op == FLASH_RDSR || op == FLASH_RDSR2 || op == FLASH_RDSR3) {
        return 8;
    }
    if (op == FLASH_RDSFDP) {
        return 32;
    }
    return 0;
}

static IRAM u32 flash_stop(struct sos_flash_record *f, u32 code, u32 read,
                           u32 expect) {
    f->stop = code;
    f->read = read;
    f->expect = expect;
    return code;
}

static IRAM u32 flash_read_in(struct sos_flash_record *f, u32 op, u32 addr);

// One read of the flash (../ABI.md §18a): `op`, one of `flash_read_bits`'
// five, and for SFDP its address `addr`, sent on SPI1 as IDF's
// `bootloader_flash_execute_command_common` sends it, at the clock SPI1
// holds. Every check is made before SPI1 is written: the opcode is a read
// (0x7A), MISC(1) reads chip select 0 alone (0x73), DDR(1) reads 0, single
// data rate (0x7B), and SPI1 is idle (0x74). Then SPI1's words are saved,
// the command is sent and waited for, and every saved word but MISC(1) is
// written back; MISC(1) is never written. A command that does not finish
// (0x75) writes its line from here and leaves SPI1 as it is, chip select 0
// possibly still low, and the caller halts. Answers 0, with the answer's
// bytes in the record, the first received in [7:0]; or the stop code; for
// 0x79, the record not in DRAM, nothing is written to it.
IRAM u32 sos_flash_read(u32 record_addr, u32 op, u32 addr) {
    if (!in_dram(record_addr, sizeof(struct sos_flash_record))) {
        return STOP_FLASH_NOT_DRAM;
    }
    command_number = 0;
    return flash_read_in((struct sos_flash_record *)(unsigned long)record_addr,
                         op, addr);
}

// `sos_flash_read`'s read, into the DRAM record `f`, as the next command of
// the call under way.
static IRAM u32 flash_read_in(struct sos_flash_record *f, u32 op, u32 addr) {
    f->stop = 0;
    f->read = 0;
    f->expect = 0;
    f->command = op;
    f->address = addr;
    f->answer = 0;
    f->saved = 0;
    f->misc = 0;
    u32 bits = flash_read_bits(op);
    if (bits == 0) {
        return flash_stop(f, STOP_FLASH_NOT_READ, op, 0);
    }
    u32 misc = rd(STEP_F_GATE, SPI1 + SPI_MISC);
    f->misc = misc;
    if ((misc & MISC_CS_MASK) != MISC_FLASH) {
        return flash_stop(f, STOP_FLASH_GATE, misc, MISC_FLASH);
    }
    u32 ddr = rd(STEP_F_DDR, SPI1 + SPI_DDR);
    if (ddr != 0) {
        return flash_stop(f, STOP_FLASH_DDR, ddr, 0);
    }
    if (!poll_idle(STEP_F_IDLE, &f->read)) {
        return flash_stop(f, STOP_FLASH_BUSY, f->read, 0);
    }
    save_spi1(STEP_F_SAVE);
    f->saved = 1;
    struct cmd c;
    c.cs = MISC_FLASH;
    c.ctrl_keep = 0;
    c.ctrl_set = CTRL_WP;
    c.fctrl_keep = ~(FCTRL_ADDR_32 | FCTRL_LINES);
    c.fctrl_set = 0;
    c.cmd = op;
    c.cmd_bits = 8;
    c.addr = addr;
    c.addr_bits = op == FLASH_RDSFDP ? SFDP_ADDR_BITS : 0;
    c.dummy = op == FLASH_RDSFDP ? SFDP_DUMMY : 0;
    c.out_bits = 0;
    c.out = 0;
    c.in_bits = bits;
    u32 w = 0;
    u32 code = link_start(&c, &f->read);
    if (code == 0) {
        code = link_finish(&c, &f->read, &w);
        if (code == LINK_STUCK) {
            return flash_stop(f, STOP_FLASH_STUCK, f->read, 0);
        }
    }
    restore_spi1(SAVED_BUT_MISC);
    if (code == LINK_BUSY) {
        return flash_stop(f, STOP_FLASH_BUSY, f->read, 0);
    }
    if (code == LINK_GATE) {
        return flash_stop(f, STOP_FLASH_GATE, f->read, MISC_FLASH);
    }
    f->answer = bits < 32 ? w & ((1u << bits) - 1u) : w;
    return 0;
}

// ---- the flash switch -------------------------------------------------------

static IRAM u32 switch_stop(struct sos_flash_switch_record *r, u32 code,
                            u32 read, u32 expect) {
    r->stop = code;
    r->read = read;
    r->expect = expect;
    return code;
}

// The canary's byte at offset `i`: the four pairs of `CANARY_PAIRS`, low
// byte first, then each byte its own offset.
static IRAM u32 canary_byte(u32 i) {
    if (i >= CANARY_PATTERN) {
        return i;
    }
    u32 b = (CANARY_PAIRS >> (8u * (i >> 1))) & 0xFFu;
    return (i & 1u) ? b ^ 0xFFu : b;
}

// SYSTIMER's UNIT0, its low word once latched; 0 if the latch is not
// reported within POLL_BOUND reads.
static IRAM u32 systimer_low(void) {
    wr(SYSTIMER_UNIT0_OP, SYSTIMER_UPDATE);
    for (u32 n = 0; n < POLL_BOUND; n++) {
        if (rd(STEP_W_TIME, SYSTIMER_UNIT0_OP) & SYSTIMER_VALID) {
            return rd(STEP_W_TIME, SYSTIMER_UNIT0_LO);
        }
    }
    return 0;
}

// `TIMED_BYTES` from `base` read at `TIMED_STRIDE`, each load a line the
// data cache has not held: how many of SYSTIMER's ticks the loads took.
static IRAM u32 timed_read(u32 base) {
    u32 start = systimer_low();
    for (u32 off = 0; off < TIMED_BYTES; off += TIMED_STRIDE) {
        (void)*(volatile u32 *)(unsigned long)(base + off);
    }
    return systimer_low() - start;
}

// Every word of `switch_addrs`, read at `step` into `w`.
static IRAM void read_words(u32 step, struct sos_flash_words *w) {
    u32 *out = (u32 *)w;
    for (u32 i = 0; i < SWITCH_WORDS; i++) {
        out[i] = rd(step, switch_addrs[i]);
    }
}

// W2's comparison of one of SPI0's words: 0 when `read` is `expect`, else
// stop 0x71 naming the word by its index `at`.
static IRAM u32 spi0_differs(struct sos_flash_switch_record *r, u32 at,
                             u32 read, u32 expect) {
    if (read == expect) {
        return 0;
    }
    r->at = at;
    return switch_stop(r, STOP_SWITCH_SPI0, read, expect);
}

// W2's SPI0 check: CLOCK, CTRL, USER1 and USER2 as described, and DDR,
// TIMING_CALI, DIN_MODE and DIN_NUM 0: single data rate, no input delay and
// no extra input cycle. Answers 0 or 0x71.
static IRAM u32 check_spi0(struct sos_flash_expect *e,
                           struct sos_flash_switch_record *r) {
    struct sos_flash_words *b = &r->before;
    u32 *w = (u32 *)b;
    u32 code = spi0_differs(r, W_CLOCK, b->clock, e->clock);
    if (code == 0) {
        code = spi0_differs(r, W_CTRL, b->ctrl, e->ctrl);
    }
    if (code == 0) {
        code = spi0_differs(r, W_USER1, b->user1, e->user1);
    }
    if (code == 0) {
        code = spi0_differs(r, W_USER2, b->user2, e->user2);
    }
    for (u32 i = W_DDR; code == 0 && i <= W_DIN_NUM; i++) {
        code = spi0_differs(r, i, w[i], 0);
    }
    return code;
}

// A line written from here: the caller's next fetch from flash may not be
// answered.
static IRAM void iram_line(char *line, u32 len) {
    for (u32 i = 0; i < len; i++) {
        uart_byte((u32)(unsigned char)line[i]);
    }
}

// The unfreeze of the cache `cache` (0 the instruction cache, 1 the data
// cache), whose EXTMEM freeze word is `reg`, as the ROM's
// `Cache_Freeze_*Cache_Disable` makes it: FREEZE_ENA cleared, then
// FREEZE_DONE read until clear, at most POLL_BOUND times, at W5's step or,
// with `w7`, W7's. Answers 1 once it reads clear; else 0, with
// `STATE_THAW_I` or `_D` set and the last word in `thaw_word_i` or `_d`.
static IRAM u32 cache_thaw(struct sos_flash_switch_record *r, u32 reg,
                           u32 cache, u32 w7) {
    u32 step = w7 ? STEP_W7_THAW : STEP_W_THAW;
    u32 v = rd(step, reg);
    wr(reg, v & ~FREEZE_ENA);
    for (u32 n = 0; n < POLL_BOUND; n++) {
        v = rd(step, reg);
        if (!(v & FREEZE_DONE)) {
            return 1;
        }
    }
    if (cache) {
        r->state |= STATE_THAW_D;
        r->thaw_word_d = v;
    } else {
        r->state |= STATE_THAW_I;
        r->thaw_word_i = v;
    }
    return 0;
}

// The freeze of the cache `cache` (as `cache_thaw`), as the ROM's
// `Cache_Freeze_*Cache_Enable(CACHE_FREEZE_ACK_BUSY)` makes it: FREEZE_MODE
// cleared, FREEZE_ENA set, then FREEZE_DONE read for, at most POLL_BOUND
// times, at W5's step or, with `w7`, W7's. Answers 1 once it reads set.
// Otherwise the freeze is undone, as an unfreeze, since the cache may yet
// freeze after the bound, and the answer is 0, with `STATE_FREEZE_I` or
// `_D` set and the last word read in `freeze_word`.
static IRAM u32 cache_freeze(struct sos_flash_switch_record *r, u32 reg,
                             u32 cache, u32 w7) {
    u32 step = w7 ? STEP_W7_FREEZE : STEP_W_FREEZE;
    u32 v = rd(step, reg);
    wr(reg, v & ~FREEZE_MODE_ERROR);
    v = rd(step, reg);
    wr(reg, v | FREEZE_ENA);
    for (u32 n = 0; n < POLL_BOUND; n++) {
        v = rd(step, reg);
        if (v & FREEZE_DONE) {
            return 1;
        }
    }
    r->state |= cache ? STATE_FREEZE_D : STATE_FREEZE_I;
    r->freeze_word = v;
    (void)cache_thaw(r, reg, cache, w7);
    return 0;
}

// The freeze of both caches, the instruction cache first, as IDF's
// `cache_ll_freeze_cache(.., CACHE_TYPE_ALL, ..)` orders them. When one does
// not report, its freeze is undone (`cache_freeze`) and so is the
// instruction cache's if it was the data cache's. Answers 0; 0x7D when every
// undo reported; or 0x7E when one did not, whose line the caller writes.
static IRAM u32 caches_freeze(struct sos_flash_switch_record *r, u32 w7) {
    if (cache_freeze(r, ICACHE_FREEZE, 0, w7)) {
        if (cache_freeze(r, DCACHE_FREEZE, 1, w7)) {
            return 0;
        }
        (void)cache_thaw(r, ICACHE_FREEZE, 0, w7);
    }
    return switch_stop(r, (r->state & STATE_THAWS) ? STOP_SWITCH_THAW
                                                   : STOP_SWITCH_FREEZE,
                       r->freeze_word, FREEZE_DONE);
}

// The unfreeze of both, in the same order (`cache_ll_unfreeze_cache`), each
// tried whatever the other reported. Answers 0, or 0x7E, whose line the
// caller writes.
static IRAM u32 caches_thaw(struct sos_flash_switch_record *r, u32 w7) {
    u32 ok = cache_thaw(r, ICACHE_FREEZE, 0, w7);
    ok &= cache_thaw(r, DCACHE_FREEZE, 1, w7);
    if (ok) {
        return 0;
    }
    return switch_stop(r, STOP_SWITCH_THAW, 0, 0);
}

// W5: the stores, in order, each word made from what W2 read (../ABI.md
// §18e), between the caches' freeze and their unfreeze. No flash read is
// made between them, and none can be: the kernel runs from IRAM here, on
// DRAM data, with interrupts masked, both caches' autoload off and both
// caches frozen, so no fill begun before the freeze is still on the bus.
static IRAM void switch_stores(struct sos_flash_switch_record *r, u32 mode) {
    struct sos_flash_words *b = &r->before;
    u32 quad = mode & MODE_QUAD;
    r->state |= STATE_STORED;
    // W5.1: the dummy phase driven high, before any read mode that has one.
    u32 ctrl = b->ctrl | CTRL_DUMMY_OUT;
    wr(SPI0 + SPI_CTRL, ctrl);
    // W5.2: no extra chip-select setup or hold.
    if (b->ctrl2 & CTRL2_CS_TIMES) {
        wr(SPI0 + SPI_CTRL2, b->ctrl2 & ~CTRL2_CS_TIMES);
    }
    if (quad) {
        // W5.3: pads 27 and 28 to SPIHD and SPIWP; W5.4: their input
        // signals straight from the IO MUX (the ROM's
        // `esp_rom_spiflash_select_qio_pins`, 0x4004_95C4).
        wr(IO_MUX_GPIO(PAD_HD), (b->pad_hd & ~MCU_SEL_MASK)
                                | (FUNC_SPIHD_WP << MCU_SEL_SHIFT));
        wr(IO_MUX_GPIO(PAD_WP), (b->pad_wp & ~MCU_SEL_MASK)
                                | (FUNC_SPIHD_WP << MCU_SEL_SHIFT));
        wr(GPIO_IN_SEL(SIG_HD), b->insel_hd & ~SIG_IN_SEL);
        wr(GPIO_IN_SEL(SIG_WP), b->insel_wp & ~SIG_IN_SEL);
    }
    // W5.5: the read mode.
    wr(SPI0 + SPI_CTRL, (ctrl & CTRL_KEEP) | (quad ? CTRL_QIO : CTRL_DIO));
    // W5.6 to W5.8: the phases, the address and the wait, the command.
    wr(SPI0 + SPI_USER, (b->user & ~USR_MOSI) | USER_CACHE_SET);
    wr(SPI0 + SPI_USER1, (b->user1 & USER1_CACHE_KEEP) | USER1_CACHE_ADDR
                         | (quad ? USER1_DUMMY_QIO : USER1_DUMMY_DIO));
    wr(SPI0 + SPI_USER2, (b->user2 & USER2_CACHE_KEEP)
                         | (quad ? FLASH_FAST_QIO : FLASH_FAST_DIO));
    // W5.9: the clock, last.
    wr(SPI0 + SPI_CLOCK, (mode & MODE_HALF) ? CLOCK_DIV_2 : CLOCK_DIV_1);
}

// W7's stores: every word W5 stored put back to what W2 read, the clock
// first.
static IRAM void switch_put_back(struct sos_flash_switch_record *r,
                                 u32 mode) {
    struct sos_flash_words *b = &r->before;
    r->state |= STATE_PUT_BACK;
    wr(SPI0 + SPI_CLOCK, b->clock);
    wr(SPI0 + SPI_USER2, b->user2);
    wr(SPI0 + SPI_USER1, b->user1);
    wr(SPI0 + SPI_USER, b->user);
    wr(SPI0 + SPI_CTRL, b->ctrl);
    if (mode & MODE_QUAD) {
        wr(GPIO_IN_SEL(SIG_HD), b->insel_hd);
        wr(GPIO_IN_SEL(SIG_WP), b->insel_wp);
        wr(IO_MUX_GPIO(PAD_HD), b->pad_hd);
        wr(IO_MUX_GPIO(PAD_WP), b->pad_wp);
    }
    if (b->ctrl2 & CTRL2_CS_TIMES) {
        wr(SPI0 + SPI_CTRL2, b->ctrl2);
    }
}

// W6: the canary at `canary`, read through the data cache, word by word
// against `canary_byte`. At the first word that differs, recorded in
// `canary_*` with `STATE_CANARY`, W7: both caches frozen as for W5, at
// W7's own steps; every word W5 stored put back; both caches unfrozen; then
// the line from here. A freeze that does not report is undone and SPI0 is
// put back all the same: with the caches running when the undo reports, and
// with a cache that may still be frozen when it does not. Answers 0; 0x78
// when every freeze and unfreeze reported; 0x7D when a freeze did not and
// every undo did; 0x7E, once its line is written, when an unfreeze or an
// undo did not.
static IRAM u32 check_canary(struct sos_flash_switch_record *r, u32 mode,
                             u32 canary) {
    for (u32 at = 0; at < CANARY_BYTES; at += 4) {
        u32 want = canary_byte(at) | (canary_byte(at + 1) << 8)
                   | (canary_byte(at + 2) << 16) | (canary_byte(at + 3) << 24);
        u32 got = rd(STEP_W_CANARY, canary + at);
        if (got != want) {
            r->state |= STATE_CANARY;
            r->canary_at = at;
            r->canary_read = got;
            r->canary_want = want;
            u32 code = caches_freeze(r, 1);
            switch_put_back(r, mode);
            if (code == 0) {
                code = caches_thaw(r, 1);
            }
            canary_line[CANARY_LINE_LINES] = (mode & MODE_QUAD) ? 'q' : 'd';
            canary_line[CANARY_LINE_SPEED] = (mode & MODE_HALF) ? '4' : '8';
            iram_line(canary_line, sizeof(canary_line) - 1);
            if (code == STOP_SWITCH_THAW) {
                iram_line(thaw_line, sizeof(thaw_line) - 1);
            }
            if (code) {
                return code;
            }
            r->at = at;
            return switch_stop(r, STOP_SWITCH_CANARY, got, want);
        }
    }
    return 0;
}

// The switch of the cache's flash reads (../ABI.md §18e): from the ROM's
// slow read at divide by 4 to DIO (BBh) or, with `MODE_QUAD`, QIO (EBh), at
// divide by 1 of the memory-SPI core clock or, with `MODE_HALF`, by 2.
// `regions` is the canary and the two timed regions, in the flash's data
// window, which no load has reached. In order:
//   W1  the description and the record lie in DRAM (0x79; nothing is
//       written to the record);
//   W2  the clock words as described (0x70); SPI0's words as described,
//       and DDR, TIMING_CALI, DIN_MODE and DIN_NUM 0 (0x71); neither
//       cache's autoload on (0x72);
//   W3  the flash's JEDEC ID (9Fh) as described (0x76), then status
//       register 2 (35h), whose QE bit `MODE_QUAD` needs set (0x77): each a
//       flash read, with its own stops (0x73 to 0x75, 0x7B);
//   W4  the first timed read;
//   W5  both caches frozen (0x7D, its freeze undone and nothing stored to
//       SPI0), the stores, both caches unfrozen (0x7E, also for an undo
//       that did not report, its line written from here: the caller's next
//       fetch from flash may never be answered);
//   W6  the canary (0x78, once W7 has put SPI0 back; or W7's own 0x7D or
//       0x7E);
//   W8  the second timed read; W9 the words read back.
// No stop before W5's stores writes to SPI0. Answers 0 or the stop code;
// after 0x75 or 0x7E the caller halts.
IRAM u32 sos_flash_switch(u32 expect_addr, u32 record_addr, u32 mode,
                          u32 regions) {
    // W1
    if (!in_dram(expect_addr, sizeof(struct sos_flash_expect))
        || !in_dram(record_addr, sizeof(struct sos_flash_switch_record))) {
        return STOP_SWITCH_NOT_DRAM;
    }
    struct sos_flash_expect *e =
        (struct sos_flash_expect *)(unsigned long)expect_addr;
    struct sos_flash_switch_record *r =
        (struct sos_flash_switch_record *)(unsigned long)record_addr;
    r->stop = 0;
    r->read = 0;
    r->expect = 0;
    r->at = 0;
    r->jedec = 0;
    r->sr2 = 0;
    r->state = 0;
    r->ticks_before = 0;
    r->ticks_after = 0;
    r->canary_at = 0;
    r->canary_read = 0;
    r->canary_want = 0;
    r->freeze_word = 0;
    r->thaw_word_i = 0;
    r->thaw_word_d = 0;
    command_number = 0;
    // W2
    r->sysclk = rd(STEP_W_CLOCK, SYSCLK_CONF);
    r->cpu_per = rd(STEP_W_CLOCK, CPU_PER_CONF);
    r->core_clk_sel = rd(STEP_W_CLOCK, CORE_CLK_SEL);
    if ((r->sysclk & SYSCLK_MASK) != e->sysclk
        || (r->cpu_per & CPU_PER_MASK) != e->cpu_per
        || (r->core_clk_sel & CORE_CLK_SEL_MASK) != e->core_clk_sel) {
        return switch_stop(r, STOP_SWITCH_CLOCK, r->sysclk, e->sysclk);
    }
    read_words(STEP_W_SPI0, &r->before);
    u32 code = check_spi0(e, r);
    if (code) {
        return code;
    }
    r->icache_autoload = rd(STEP_W_CACHE, ICACHE_AUTOLOAD);
    r->dcache_autoload = rd(STEP_W_CACHE, DCACHE_AUTOLOAD);
    if ((r->icache_autoload | r->dcache_autoload) & AUTOLOAD_ENA) {
        return switch_stop(r, STOP_SWITCH_AUTOLOAD, r->icache_autoload,
                           r->dcache_autoload);
    }
    // W3
    code = flash_read_in(&r->flash, FLASH_RDID, 0);
    if (code) {
        return switch_stop(r, code, r->flash.read, r->flash.expect);
    }
    u32 id = r->flash.answer;
    r->jedec = ((id & 0xFFu) << 16) | (id & 0xFF00u) | ((id >> 16) & 0xFFu);
    if (r->jedec != e->jedec) {
        return switch_stop(r, STOP_SWITCH_ID, r->jedec, e->jedec);
    }
    code = flash_read_in(&r->flash, FLASH_RDSR2, 0);
    if (code) {
        return switch_stop(r, code, r->flash.read, r->flash.expect);
    }
    r->sr2 = r->flash.answer;
    if ((mode & MODE_QUAD) && !(r->sr2 & SR2_QE)) {
        return switch_stop(r, STOP_SWITCH_QE, r->sr2, SR2_QE);
    }
    // W4, W5
    r->ticks_before = timed_read(regions + CANARY_BYTES);
    code = caches_freeze(r, 0);
    if (code == 0) {
        switch_stores(r, mode);
        code = caches_thaw(r, 0);
    }
    if (code == STOP_SWITCH_THAW) {
        iram_line(thaw_line, sizeof(thaw_line) - 1);
    }
    if (code) {
        return code;
    }
    // W6, W7
    code = check_canary(r, mode, regions);
    if (code == 0) {
        // W8
        r->ticks_after = timed_read(regions + CANARY_BYTES + TIMED_BYTES);
    }
    // W9
    read_words(STEP_W_AFTER, &r->after);
    return code;
}
