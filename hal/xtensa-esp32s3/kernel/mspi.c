// SOS Xtensa kernel HAL: the octal PSRAM's command layer, on the memory-SPI
// controller the flash shares (../ABI.md §16).
//
// C because the whole of it must run without one instruction or one byte of
// data fetched from flash: while SPI1 holds the chip-select gate, or a
// command to the RAM chip is under way, nothing may ask SPI0 to fetch from
// the flash. Saw code runs in place from flash, so the bring-up is one call
// into this file, which makes every check between its steps and returns at
// the first that fails. Saw prints what the record says afterwards.
//
// The rules that keep it out of flash, held by the runner's `check_iram`
// (tools/sos_runner.py) on every S3 image that links it:
//   1. every function is in `.text.iram`, so its code and its literal pool
//      (`.literal.iram`) are copied to IRAM with the vectors (`esp32s3.ld`);
//   2. no `const` data, no string, no switch dense enough for a jump table,
//      no struct or array initializer large enough to become `memset` or
//      `memcpy`, no 64-bit division: anything that is not an immediate or a
//      literal-pool word lives in a non-const static, in DRAM;
//   3. no call out of this file: the one line it writes itself (`short_line`)
//      goes to UART0 through its own loop.
//
// Every register address and value is cited where it is defined, from
// ESP-IDF v5.4.1 ("IDF", components/ of that tag) or from the mask ROM's
// disassembly (`esp32s3_rev0_rom.elf`, "ROM"). The step names (S0.1 to S10)
// and the stop codes (0x50 to 0x5D, 0x61 and 0x62) are ../ABI.md §16's.
//
// Nothing here writes non-volatile state: the eFuse is only read
// (EFUSE_RD_*), no RTC register is written, no command is ever sent with chip
// select 0 enabled, and the commands sent to the RAM chip read its mode
// registers and its array and write its mode register 0, which it loses at
// power-off.
//
// Built call0 with no FPU like the rest of the kernel (`-windowed`, `-fp`).
//
// The fault build. Every register read goes through `rd`. Built with
// `-DSOS_PSRAM_FAULT` (a harness image only: the runner refuses any other
// image that defines `sos_psram_fault`), `rd` first consults the DRAM record
// `sos_psram_fault` the harness fills in: when the call is at that step, in
// that command, and reads that address, it gets the record's value instead.
// Writes always reach the real register, so the whole real call runs with
// one read lied to.

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
};

// The steps, as `last_step` names them and as the fault record names a read:
// 0xMN is step SM.N (N in hex: S7.10 is 0x7A), 0xM0 step SM. The reads S7.2
// to S7.6 make to change a word are STEP_S7's; STEP_S7_PROBE is the
// measurement's one read of SPI_MEM_CMD(1) between S7.9's start and its wait.
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
#define STOP_MEASUREMENT    0x61u
#define STOP_STILL_BUSY     0x62u

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
#define SPI_CLOCK           0x14u
#define SPI_USER            0x18u
#define SPI_USER1           0x1Cu
#define SPI_USER2           0x20u
#define SPI_MOSI_DLEN       0x24u
#define SPI_MISO_DLEN       0x28u
#define SPI_MISC            0x34u
#define SPI_CACHE_FCTRL     0x3Cu
#define SPI_SRAM_CLK        0x50u
#define SPI_W0              0x58u
#define SPI_SMEM_AC         0xDCu
#define SPI_DDR             0xE0u

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
#define USER1_ADDR_32       (31u << 26)
#define USER2_CMD_16        (15u << 28)
#define FCTRL_ADDR_32       (1u << 1)

// The RAM chip's commands and mode registers (IDF esp_psram_impl_octal.c:
// OPI_PSRAM_REG_READ, OPI_PSRAM_REG_WRITE, OPI_PSRAM_SYNC_READ;
// OCT_PSRAM_RD_REG_DUMMY_BITLEN 2 x (5 - 1), OCT_PSRAM_RD_DUMMY_BITLEN
// 2 x (10 - 1); `opi_psram_mode_reg_t`'s fields).
#define CMD_REG_READ        0x4040u
#define CMD_REG_WRITE       0xC0C0u
#define CMD_SYNC_READ       0x0000u
#define REG_DUMMY           8u
#define ARRAY_DUMMY         18u
#define MR1_VENDOR_MASK     0x1Fu
#define MR2_GOOD_DIE        0x80u
#define MR2_DENSITY_MASK    0x07u
// MR0: drive strength [1:0] 0, read latency code [4:2] 2, fixed latency
// [5] 1; [7:6] kept (IDF `esp_psram_impl_enable`'s lt 1, read_latency 2,
// drive_str 0).
#define MR0_KEEP            0xC0u
#define MR0_MODE            0x28u
#define MR0_MODE_MASK       0x3Fu

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
#endif

// The number of the command under way in this call: 1 for the first.
static u32 command_number;

// SPI1's words as S5 found them, in `SAVED`'s order, for S10.
#define SAVED 11u
static u32 saved_spi1[SAVED];

// S5's registers, in the order S10 writes them back: MISC last, so the gate
// is the last thing undone. In `.data` by name, as `short_line` is.
__attribute__((section(".data.sos_psram_saved_offsets")))
static u32 saved_offsets[SAVED] = {
    SPI_CLOCK, SPI_CTRL, SPI_USER, SPI_USER1, SPI_USER2, SPI_ADDR,
    SPI_MOSI_DLEN, SPI_MISO_DLEN, SPI_CACHE_FCTRL, SPI_DDR, SPI_MISC,
};

// The line written by S7.9 before it returns, the command's four hex digits
// at `SHORT_LINE_DIGITS`. In `.data` by name: a static nothing writes may
// otherwise be promoted to `.rodata`, which is flash.
#define SHORT_LINE_DIGITS   30u
__attribute__((section(".data.sos_psram_short_line")))
static char short_line[] =
    "SOS-S3: psram: SPI1 command 0x.... did not finish\n";

// ---- access -----------------------------------------------------------------

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

// ---- the short line ---------------------------------------------------------

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

static IRAM void short_line_out(u32 command) {
    for (u32 i = 0; i < 4; i++) {
        u32 d = (command >> (12 - 4 * i)) & 0xFu;
        short_line[SHORT_LINE_DIGITS + i] = (char)(d < 10 ? '0' + d
                                                          : 'a' + d - 10);
    }
    for (u32 i = 0; i < sizeof(short_line) - 1; i++) {
        uart_byte((u32)(unsigned char)short_line[i]);
    }
}

// ---- the steps --------------------------------------------------------------

// The bounded wait: SPI_MEM_CMD(1) read until it is 0, at most POLL_BOUND
// times. Answers 1 when idle; otherwise leaves the last word read in
// `r->read` and answers 0.
static IRAM u32 wait_idle(struct sos_psram_record *r, u32 step) {
    u32 v = 0;
    for (u32 n = 0; n < POLL_BOUND; n++) {
        v = rd(step, SPI1 + SPI_CMD);
        if (v == 0) {
            return 1;
        }
    }
    r->read = v;
    return 0;
}

// S5: save SPI1, put the commands' clock word and DTR mode on it.
static IRAM void prepare_spi1(u32 clock, struct sos_psram_record *r) {
    for (u32 i = 0; i < SAVED; i++) {
        saved_spi1[i] = rd(STEP_S5, SPI1 + saved_offsets[i]);
    }
    r->saved = 1;
    wr(SPI1 + SPI_CLOCK, clock);
    r->spi1_clock = rd(STEP_S5, SPI1 + SPI_CLOCK);
    u32 ddr = rd(STEP_S5, SPI1 + SPI_DDR);
    wr(SPI1 + SPI_DDR, (ddr & ~DDR_CLEAR) | DDR_SET);
}

// S10: every word S5 saved written back, MISC last.
static IRAM void restore_spi1(void) {
    for (u32 i = 0; i < SAVED; i++) {
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

// S7.1 to S7.9's start: one SPI1 user command to chip select 1, set up and
// started. `dummy` is in cycles, 0 for none; `out_bits` and `in_bits` are
// the data phases, 0 for none. Answers 0, or the stop code with the record
// filled in.
static IRAM u32 command_start(struct sos_psram_record *r, u32 cmd, u32 addr,
                              u32 dummy, u32 out_bits, u32 out,
                              u32 in_bits) {
    command_number++;
    r->command = cmd;
    r->address = addr;
    // S7.1
    if (!wait_idle(r, STEP_S7_1)) {
        return stop(r, STOP_BUSY_COMMAND, r->read, 0);
    }
    // S7.2
    u32 ctrl = rd(STEP_S7, SPI1 + SPI_CTRL);
    wr(SPI1 + SPI_CTRL, (ctrl & CTRL_KEEP) | CTRL_OCTAL);
    // S7.3
    u32 phases = USR_COMMAND | USR_ADDR;
    if (dummy != 0) {
        phases |= USR_DUMMY;
    }
    if (in_bits != 0) {
        phases |= USR_MISO;
    }
    if (out_bits != 0) {
        phases |= USR_MOSI;
    }
    u32 user = rd(STEP_S7, SPI1 + SPI_USER);
    wr(SPI1 + SPI_USER, (user & USER_KEEP) | phases);
    // S7.4, S7.5
    wr(SPI1 + SPI_USER1, USER1_ADDR_32 | (dummy != 0 ? dummy - 1 : 0));
    wr(SPI1 + SPI_USER2, USER2_CMD_16 | cmd);
    // S7.6
    wr(SPI1 + SPI_ADDR, addr);
    u32 fctrl = rd(STEP_S7, SPI1 + SPI_CACHE_FCTRL);
    wr(SPI1 + SPI_CACHE_FCTRL, fctrl | FCTRL_ADDR_32);
    // S7.7
    if (out_bits != 0) {
        wr(SPI1 + SPI_MOSI_DLEN, out_bits - 1);
        wr(SPI1 + SPI_W0, out);
    }
    if (in_bits != 0) {
        wr(SPI1 + SPI_MISO_DLEN, in_bits - 1);
    }
    // S7.8
    u32 misc = rd(STEP_S7_8, SPI1 + SPI_MISC);
    if ((misc & MISC_CS_MASK) != MISC_GATE) {
        return stop(r, STOP_GATE_LOST, misc, MISC_GATE);
    }
    wr(SPI1 + SPI_CMD, SPI_MEM_USR);
    return 0;
}

// S7.9's wait and S7.10, for the command `command_start` started: a command
// that does not finish leaves the gate as it is, so a transfer still under
// way cannot be turned towards the flash by a restore, and says so from here
// first. The answer is W0(1), read when `answer` is not 0.
static IRAM u32 command_finish(struct sos_psram_record *r, u32 cmd,
                               u32 *answer) {
    if (!wait_idle(r, STEP_S7_9)) {
        short_line_out(cmd);
        return stop(r, STOP_NOT_FINISHED, r->read, 0);
    }
    // S7.10
    if (answer != 0) {
        *answer = rd(STEP_S7_10, SPI1 + SPI_W0);
    }
    return 0;
}

// S7: one command, started and waited for. Answers 0 or the stop code.
static IRAM u32 command(struct sos_psram_record *r, u32 cmd, u32 addr,
                       u32 dummy, u32 out_bits, u32 out, u32 in_bits,
                       u32 *answer) {
    u32 code = command_start(r, cmd, addr, dummy, out_bits, out, in_bits);
    if (code) {
        return code;
    }
    return command_finish(r, cmd, answer);
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

// One probe of the measurement: the command started, CCOUNT left to advance
// by `spin`, SPI_MEM_CMD(1) read once, then the command waited for as S7.9
// waits. `*done` says whether that one read found it finished. Answers 0 or
// the stop code.
static IRAM u32 probe(struct sos_psram_record *r, u32 cmd, u32 dummy,
                      u32 in_bits, u32 spin, u32 *done) {
    u32 code = command_start(r, cmd, 0, dummy, 0, 0, in_bits);
    if (code) {
        return code;
    }
    u32 start = ccount();
    while (ccount() - start < spin) {
    }
    u32 v = rd(STEP_S7_PROBE, SPI1 + SPI_CMD);
    *done = v == 0;
    code = command_finish(r, cmd, 0);
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
        restore_spi1();
        return r->stop;
    }
    u32 code = g->what == GATED_TIMING
               ? measure(r, g->cmd, g->dummy, g->in_bits, g->searches)
               : identify_and_set(e, r, g->last_step);
    if (code == STOP_NOT_FINISHED) {
        return code;
    }
    restore_spi1();
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
    command_number = 0;
    return 1;
}

// ---- the entries ------------------------------------------------------------

// The bring-up, S0 to S10: S8's identity is checked before S9 writes the
// chip, and `last_step` ends it after S8 (STEP_S8) or S9 (STEP_S9 or more,
// S9 being this file's last command step). Answers 0 or the stop code; for
// 0x50 nothing is written to the record, which is then not known to be RAM.
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
    return under_gate(e, r, e->divider, &g);
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
