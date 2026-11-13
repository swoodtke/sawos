# SO-77: GPIO and PWM drivers for the ESP32-S3 (design)

For the owner, before anything is built. Written against main at `b3c6e47d`.
Section 2 gives a source for every register number; where a number has no
source, it says so.

**Where the issue's scope needs correcting.** Each point is argued in its
own section.

- **The smoke cannot read the IO MUX back.** The lead's note (c5) asks the
  emulator smoke to read each pad's IO MUX register back after configuring
  it. Under the installed emulator the IO MUX reads 0 before and after any
  write (probed, §8.1). The requirement can still be held, in two halves: the
  smoke asserts the word the driver *wrote* (its shadow), and a board stage
  asserts the readback.
- **The PWM driver cannot route its own pins on this kernel.** Routing a
  LEDC channel to a pad touches three blocks: LEDC, GPIO (the output-signal
  matrix and the enable word) and the IO MUX (the pad's function). A child's
  mapping quota is two rows (`DEFAULT_QUOTA_MAPPINGS`). So an "attach a
  channel to a pin" request would need a kernel change. This design avoids
  one: root applies the board's static pin table once at boot (§3.3), and the
  PWM driver holds LEDC alone.
- **The box (SO-78) does not fit the kernel as built.** The box needs five
  processes and at least five Waiters. The compiled floors are
  `MAX_PROCESSES` 3 and `MAX_WAITERS` 4, and the S3 layout has one child
  slot. None of this blocks SO-77, whose cases are root plus one child. It
  does need a unit of its own before SO-78 (§11, U5).

---

## 1. Verdict

- **What gets built.** Two driver processes with a pipe protocol each:
  - `svc-gpio-esp32s3`, the switch and simple outputs, holding the GPIO and
    IO MUX windows and the GPIO interrupt line;
  - `svc-pwm-esp32s3`, the servos, holding the LEDC window and nothing else.

  Alongside them: one package of typed register blocks shared by root and
  both drivers, a board table in root that turns on LEDC's clock and routes
  each servo pad to its LEDC channel, and two smoke cases.
- **What the kernel gains: nothing.** The region table already mints any
  number of `Device` rows. `device_window_ok` accepts every window this
  design asks for. `irq_line_valid` accepts the GPIO source (16). The
  runner and the docs do change: the stitcher can emit only one device row
  per case today, and spec §12 describes that one row.
- **The PWM clock is the crystal.** LEDC runs from XTAL_CLK at 40 MHz with
  14-bit resolution and a divider of 48.828125. That gives a 20.000 ms frame
  and a 1.22 µs pulse step, independent of whatever CPU and APB clocks the
  ROM left. The CPU clock is not raised for the box (§6).
- **The switch is a level wait.** `WaitLevel(pin, level)` answers at once
  when the pin already reads `level`, and otherwise when an any-edge
  interrupt shows that it does. This has no lost-edge race. A client waits
  on the switch and a Timer together by posting the call and attaching the
  claim to its Waiter. Debouncing stays in the client, as SO-78 says.
- **What only the board can show:** every register effect. On the
  emulator, GPIO, the IO MUX and LEDC read 0 and drop writes (STRAP is the
  one exception), and no GPIO interrupt can be raised. The smoke proves that
  the processes start, map their windows, compute the right register words
  and answer the protocol, including its refusals. Six flashable stages
  (§8.3) prove the rest.

---

## 2. The hardware, with sources

Sources, all public, fetched for this design:

- **[IDF]** ESP-IDF v5.1, `components/soc/esp32s3/include/soc/`:
  `reg_base.h`, `gpio_reg.h`, `gpio_struct.h`, `gpio_sig_map.h`,
  `io_mux_reg.h`, `ledc_reg.h`, `system_reg.h`, `periph_defs.h`,
  `soc_caps.h`, `soc.h`, `rtc_io_reg.h`, `rtc_cntl_reg.h`.
- **[IDF-HAL]** ESP-IDF v5.1, `components/hal/esp32s3/include/hal/`:
  `gpio_ll.h`, `ledc_ll.h` and `clk_gate_ll.h`. Also
  `components/driver/ledc/ledc.c` and
  `components/esp_hw_support/port/esp32s3/rtc_clk.c`.
- **[SVD]** Espressif's `svd/esp32s3.svd` (repository espressif/svd,
  `main`), for field descriptions and reset values.
- **[DOC]** ESP-IDF v5.1 LEDC guide for the ESP32-S3
  (docs.espressif.com, `api-reference/peripherals/ledc`).
- **[PROBE]** This design's own run of the installed emulator (§8.1).

The ESP32-S3 Technical Reference Manual was downloaded, but no tool on this
machine extracts its text, so no number below comes from it. The reviewer
quoted `io_mux_reg.h` at v5.1 (SO-77 c4), and every function number quoted
there matches the copy fetched here.

### 2.1 Blocks

```
block      base         registers used   source
UART0      0x6000_0000  (the console)    [IDF] reg_base.h
GPIO       0x6000_4000  0x000..0x62F     [IDF] reg_base.h
RTC_CNTL   0x6000_8000  none             [IDF] reg_base.h
RTC IO     0x6000_8400  none (see 2.3)   [IDF] reg_base.h
IO MUX     0x6000_9000  0x000..0x0C7     [IDF] reg_base.h, [SVD] size 0xCC
LEDC       0x6001_9000  0x000..0x0FF     [IDF] reg_base.h
SYSTIMER   0x6002_3000  (the kernel's)   [IDF] reg_base.h
SYSTEM     0x600C_0000  0x018, 0x020     [IDF] reg_base.h
INTMATRIX  0x600C_2000  (the kernel's)   ABI.md §6c
```

The GPIO, LEDC and UART0 bases agree with `hal/xtensa-esp32s3/ABI.md` §3
and §9.

### 2.2 GPIO (0x6000_4000) — [IDF] gpio_reg.h, gpio_struct.h; [SVD]

```
off    register             use
0x004  OUT                  pins 0-31 output level
0x008  OUT_W1TS             set bits (write-only)
0x00C  OUT_W1TC             clear bits (write-only)
0x010  OUT1 / 0x014 W1TS / 0x018 W1TC        pins 32-53
0x020  ENABLE / 0x024 W1TS / 0x028 W1TC      output enable, pins 0-31
0x02C  ENABLE1 / 0x030 W1TS / 0x034 W1TC     pins 32-53
0x038  STRAP                (read-only; the emulator models only this)
0x03C  IN                   pins 0-31 pad input (read-only)
0x040  IN1                  pins 32-53
0x044  STATUS / 0x048 W1TS / 0x04C W1TC      interrupt status, pins 0-31
0x050  STATUS1 / 0x054 W1TS / 0x058 W1TC     pins 32-53
0x05C  PCPU_INT             status of pins enabled for the CPU, 0-31 (RO)
0x068  PCPU_INT1            the same, 32-53 (RO)
0x074  PINn (n 0..53)       0x074 + 4n: per-pin interrupt config
0x154  FUNCs_IN_SEL_CFG     0x154 + 4s, s 0..255 (not used here)
0x554  FUNCn_OUT_SEL_CFG    0x554 + 4n, n 0..53: pad n's output signal
0x62C  CLOCK_GATE           bit 0 GPIO_CLK_EN, reset 1 (left alone)
```

`PINn` fields ([IDF] gpio_reg.h bit positions, [SVD] meanings):

```
bits 17:13  INT_ENA     bit 13: CPU interrupt; bit 14: CPU NMI
                        ([IDF-HAL] GPIO_LL_INTR_ENA = BIT(0) of the field)
bits  9:7   INT_TYPE    0 off, 1 rising, 2 falling, 3 any edge,
                        4 low level, 5 high level ([IDF] gpio_types.h)
bit   10    WAKEUP_ENABLE   bit 2 PAD_DRIVER (1 open drain)
```

On this part `PCPU_INT` and `PCPU_INT1` are the status for both cores
([IDF-HAL] gpio_ll.h comment). Clearing is `STATUS_W1TC` and
`STATUS1_W1TC` ([IDF-HAL] `gpio_ll_clear_intr_status`).

`FUNCn_OUT_SEL_CFG` ([IDF] gpio_reg.h; [SVD]), reset `0x100`:

```
bits 8:0  OUT_SEL      output signal; 256 (0x100) = SIG_GPIO_OUT_IDX, the
                       plain OUT/ENABLE bits ([IDF] gpio_sig_map.h)
bit  9    OUT_INV_SEL  invert the output
bit  10   OEN_SEL      1: output enable from ENABLE[n]; 0: from the
                       peripheral's own enable signal ([SVD])
bit  11   OEN_INV_SEL
```

Signals ([IDF] gpio_sig_map.h): `LEDC_LS_SIG_OUT0_IDX` is 73, and channels
0..7 are 73..80. `SIG_GPIO_OUT_IDX` is 256.

The interrupt matrix source ([IDF] periph_defs.h, counted from the enum):
`ETS_GPIO_INTR_SOURCE` is **16** ("interrupt of GPIO, level"); 17 is its
NMI, 18 and 19 a second pair. The same count gives UART0 27 and SYSTIMER
TARGET0 57, the two numbers `ABI.md` §6c found by probing, which
corroborates the count. `ETS_LEDC_INTR_SOURCE` is 35 (not used).

Valid GPIOs are 0..48 except 22..25 ([IDF] soc_caps.h
`SOC_GPIO_VALID_GPIO_MASK`). GPIO 26..48 are powered from VDD_SPI
([IDF] soc_caps.h). That matters for 47 and 48 on a 1.8 V module only; this
board's flash is 3.3 V (SO-76 c6).

### 2.3 IO MUX (0x6000_9000) — [IDF] io_mux_reg.h; [SVD]

Pad n's register is at `0x004 + 4n` for every n. This was checked against
each named pad: 21 at 0x58, 41 at 0xA8, 42 at 0xAC, 47 at 0xC0 and 14 at
0x3C.

```
bit  15     FILTER_EN   input glitch filter (width not found in any source)
bits 14:12  MCU_SEL     IO MUX function; GPIO is function 1 on every pad
                        ([IDF] PIN_FUNC_GPIO = 1)
bits 11:10  FUN_DRV     drive: 0 ~5 mA, 1 ~10 mA, 2 ~20 mA, 3 ~40 mA [SVD]
bit   9     FUN_IE      input enable
bit   8     FUN_WPU     pull-up
bit   7     FUN_WPD     pull-down
bits  6:0   sleep-mode copies (MCU_OE, SLP_SEL, MCU_WPD/WPU/IE, MCU_DRV)
```

The pads the plan names ([IDF] io_mux_reg.h):

```
GPIO  pad name   function 0       function 1  others
 21   GPIO21     GPIO21 (func 0)  GPIO21      -
 41   MTDI       MTDI (JTAG)      GPIO41      2: CLK_OUT1
 42   MTMS       MTMS (JTAG)      GPIO42      -
 47   SPICLK_P   SPICLK_DIFF      GPIO47      2: SUBSPICLK_DIFF
 14   GPIO14     GPIO14 (func 0)  GPIO14      2 FSPIDQS, 3 SUBSPIWP, 4 FSPIWP
  1   GPIO1      GPIO1 (func 0)   GPIO1       -
  2   GPIO2      GPIO2 (func 0)   GPIO2       -
```

**The reviewer's finding holds** (SO-77 c4). On 41 and 42, function 0 is
JTAG, and the GPIO matrix drives a pad only while its function is the GPIO
one. So whoever configures a pad writes `MCU_SEL = 1`, whatever the pin and
whatever its reset value.

The 14 row corrects one claim. The lead's note offers 47 and 14 as pads
"with no alternate function at 0". For 47 that is wrong: its function 0 is
SPICLK_DIFF. For 14, function 0 is GPIO. Neither matters once the driver
always writes function 1.

**Reset values are not confirmed per pad.** The SVD gives one reset value
for the whole array, `0x0000_0B00` (function 0, IE, WPU, 20 mA), and that
is pad 0's. Stage 0 (§8.3) reads 21, 41, 42, 47 and 14 on the part. The
driver writes the whole word, so its behaviour does not depend on the reset
value. Only the line state between reset and configuration does (§9).

**RTC pads.** GPIO 0..21 are also RTC pads. The RTC IO mux bit for 21
(`RTC_IO_RTC_PAD21_REG` at 0x6000_8400 + 0xD8, bit 19 `MUX_SEL`) resets to
0, which leaves the digital IO MUX in control ([IDF] rtc_io_reg.h). On this
part the IO MUX's own pull bits govern RTC pads too ([IDF] soc_caps.h:
`SOC_GPIO_SUPPORT_RTC_INDEPENDENT`, "Digital IOs have their own registers to
control pullup/down"). Nothing writes the RTC IO block.

The words this design writes:

```
input, pull-up (the switch)   MCU_SEL 1 | IE | WPU | DRV 2    = 0x0000_1B00
output (simple or LEDC)       MCU_SEL 1 | DRV 2               = 0x0000_1800
output, diagnostic loopback   MCU_SEL 1 | IE | DRV 2          = 0x0000_1A00
```

### 2.4 LEDC (0x6001_9000) — [IDF] ledc_reg.h, [IDF-HAL] ledc_ll.h; [SVD]

The S3's LEDC has low-speed mode only: 8 channels, 4 timers and a 14-bit
counter ([IDF] soc_caps.h `SOC_LEDC_CHANNEL_NUM` 8,
`SOC_LEDC_TIMER_BIT_WIDTH` 14).

```
off           register         fields
0x00 + 0x14c  CHc_CONF0        1:0 TIMER_SEL, 2 SIG_OUT_EN, 3 IDLE_LV,
                               4 PARA_UP (write-only), 14:5 OVF_NUM, ...
0x04 + 0x14c  CHc_HPOINT       13:0
0x08 + 0x14c  CHc_DUTY         18:0; the low 4 bits are a fraction
0x0C + 0x14c  CHc_CONF1        31 DUTY_START, 30 DUTY_INC (reset 1),
                               29:20 DUTY_NUM, 19:10 DUTY_CYCLE,
                               9:0 DUTY_SCALE
0x10 + 0x14c  CHc_DUTY_R       18:0 the duty in force (read-only)
0xA0 + 8t     TIMERt_CONF      3:0 DUTY_RES, 21:4 CLK_DIV (10.8 fixed
                               point), 22 PAUSE, 23 RST (reset 1),
                               24 TICK_SEL, 25 PARA_UP (write-only);
                               reset 0x0080_0000 [SVD]
0xA4 + 8t     TIMERt_VALUE     13:0 the counter (read-only)
0xC0..0xCC    INT_RAW/ST/ENA/CLR  (not used)
0xD0          CONF             1:0 APB_CLK_SEL, 31 CLK_EN
0xFC          DATE             reset 0x1904_0200
```

- `APB_CLK_SEL` selects the clock for all four timers: 1 APB_CLK,
  2 RC_FAST_CLK, 3 XTAL_CLK ([IDF-HAL] `ledc_ll_set_slow_clk_sel`; [SVD]).
  Its reset value, 0, selects nothing. ESP-IDF selects a source *before*
  touching any channel, because "set channel configurations and update bits
  before core clock is on could lead to error" ([IDF-HAL] ledc.c).
- `TICK_SEL` 0 is SLOW_CLK, the clock `APB_CLK_SEL` chose; 1 is REF_TICK
  ([SVD]). It is left at 0.
- `CLK_EN` (CONF bit 31) forces the register file's clock on. ESP-IDF never
  sets it, and this design follows.
- The duty sequence ESP-IDF uses for a fixed duty ([IDF-HAL] ledc.c,
  `ledc_set_duty_with_hpoint`, `_ledc_update_duty`) is: HPOINT; DUTY =
  counts << 4; CONF1 = DUTY_START | DUTY_INC | DUTY_NUM 1 | DUTY_CYCLE 1 |
  DUTY_SCALE 0 (= `0xC010_0400`); CONF0 |= SIG_OUT_EN | PARA_UP. To stop:
  IDLE_LV, SIG_OUT_EN 0, DUTY_START 0, PARA_UP.
- The timer sequence (`ledc_timer_set`, then `ledc_timer_rst`) is: write
  DUTY_RES and CLK_DIV, set PARA_UP, then set RST and clear it.
- **New settings take effect at the next PWM cycle** ([DOC]: "the new LEDC
  parameters don't take effect until the next PWM cycle"). A duty update
  therefore never cuts a pulse short.

### 2.5 SYSTEM (0x600C_0000): the LEDC clock gate — [IDF] system_reg.h; [SVD]

```
0x018  PERIP_CLK_EN0   bit 11 LEDC_CLK_EN, reset 0
                       (whole register reset 0xF9C1_E06F [SVD]: bit 11 clear)
0x020  PERIP_RST_EN0   bit 11 LEDC_RST, reset 0 ([IDF]; the SVD gives none)
```

**LEDC is clock-gated at reset.** Enabling it means setting
`PERIP_CLK_EN0` bit 11 and clearing `PERIP_RST_EN0` bit 11, which is what
`periph_module_enable(PERIPH_LEDC_MODULE)` does ([IDF-HAL] clk_gate_ll.h).
Both registers gate every peripheral on the chip: bit 2 of `PERIP_CLK_EN0`
is UART0's clock, the console. §3.3 says which process writes them.

### 2.6 Clocks

- **XTAL_CLK is 40 MHz.** Sources: [IDF] soc.h `XTAL_CLK_FREQ`; the board's
  `flash-id` reported a 40 MHz crystal (SO-76 c6); the kernel's SYSTIMER
  runs at 16 MHz from it (ABI.md §6a).
- **The ROM's CPU and APB clocks.** [IDF] soc.h gives
  `CPU_CLK_FREQ_ROM = APB_CLK_FREQ_ROM = 40 MHz`. The register resets
  ([IDF], [SVD]) are `SYSCLK_CONF` (0x600C_0060) = `0x1`, which is
  `SOC_CLK_SEL` 0 (XTAL) with `PRE_DIV_CNT` 1, and `CPU_PER_CONF`
  (0x600C_0010) = `0xC`. On XTAL, APB equals the CPU clock; on the PLL, APB
  is 80 MHz ([IDF-HAL] rtc_clk.c: `rtc_clk_cpu_freq_to_xtal` sets APB to the
  CPU frequency, the PLL path sets 80 MHz). **The part has never been
  read.** The 36 s spin (SO-76 c11) is a 2-instruction loop of about 10^8
  iterations. At 40 MHz that is roughly 14 cycles per iteration, too many
  for a loop that hits the cache. So either the clock is lower than the
  ROM constant, or fetch costs more than it should. Stage 0 measures both
  (§6).
- **APB_CLK is therefore 40 MHz or less on the ROM's configuration and
  80 MHz on the PLL.** A LEDC divider computed for one is wrong by 2x or
  more on the other. That rules APB_CLK out as the LEDC source.
- **RC_FAST_CLK** is about 20 MHz nominal ([DOC]; ESP-IDF calibrates it
  before use). It needs `RTC_CNTL_CLK_CONF` (0x6000_8074) bit 10
  `DIG_CLK8M_EN` set ([IDF] rtc_cntl_reg.h), and RTC_CNTL is the block with
  the watchdogs. Rejected.

---

## 3. What reaches a driver

### 3.1 Device rows

Four rows are added to the region table of each case that needs them.
Every window is a naturally aligned power of two of at least 4 bytes, which
is all `device_window_ok` asks on this board (`lib.saw`, the riscv32
profiles' rule; spec §2.5 "the machine's grain is checked at the carve").

```
row      base         len     holder after boot
GPIO     0x6000_4000  0x1000  root, then the GPIO driver
IO MUX   0x6000_9000  0x1000  root, then the GPIO driver
LEDC     0x6001_9000  0x1000  the PWM driver
SYSCLK   0x600C_0000  0x40    root only (PERIP_CLK_EN0, PERIP_RST_EN0)
```

The SYSCLK row is the smallest aligned window that covers both 0x018 and
0x020. It also covers core 1's control words (0x000, 0x004) and
`CPU_PER_CONF` (0x010), which root is trusted with. Two 4-byte rows would
exclude them, at the cost of one more row and one more mapping. That is not
worth it for root.

What the kernel does with them is already built: `mint_boot_regions` mints
an `IoMemory` per `Device` row (`kernel/core/process.saw`), and
`MAX_IOMEMORIES` is 8. Root's mapping quota is
`min(MAX_MAPPINGS, GRANT_ROW_BUDGET - 4)`, so 4 rows on this board. That is
enough for root to map GPIO, IO MUX and SYSCLK at once while it applies
the board table, then unmap all three.

**The runner change.** `_region_rows` emits one device row, taken from the
arch entry's `device_base` and `device_len` (`tools/sos_runner.py`). It
gains a case key naming several rows from a per-board table, for example
`"devices": ["gpio", "iomux", "ledc", "sysclk"]` with
`ESP32S3_ARCH["device_windows"]`. They are emitted after the pool row, in
the order the case lists them. Spec §12's bullet "then the RAM pool if the
case has one and the device window if it has one" changes to match.

### 3.2 How fine the isolation is

**This board's tier is Flat** (ABI.md §5e; spec §5b). Any process can read
and write any address, the kernel's included. "Holding only its own window"
is capability discipline: it is what each process was given and what its
grant record says. Nothing enforces it. On the syscall path the checks are
real (a map of a window the process does not hold is refused). Memory
protection is not.

Even on a tier that enforced rows, per-pin isolation is coarse:

- `OUT`, `ENABLE`, `IN` and `STATUS` are one word for pins 0..31 and one for
  32..53. A window over the word is a window over 32 pins. The W1TS and
  W1TC forms let two writers set and clear their own bits without a
  read-modify-write race, but they separate nothing.
- `PINn`, `FUNCn_OUT_SEL_CFG` and each IO MUX pad are 4-byte registers.
  A 4-byte window per register per pin is expressible (`device_window_ok`
  accepts it), but it costs a grant row each, far past any quota.

So the design isolates by **role**, not by pin:

- the GPIO driver owns pads. It touches only the pins in its setup, and
  refuses requests for any other pin (`NotMine`);
- the PWM driver owns no pad at all. It holds LEDC and cannot change which
  pad a channel reaches;
- root wires the servo pads to LEDC once, at boot, and then holds no window.

### 3.3 Who writes the clock gate and the servo routing: root, once

Three options were weighed for the clock gate.

- **Root writes it (recommended).** Root is init and already holds every
  window. It maps SYSCLK, sets `PERIP_CLK_EN0` bit 11, clears
  `PERIP_RST_EN0` bit 11 and unmaps, before it creates the PWM driver.
  These are the only writes to those registers in the system, so the
  read-modify-write has no competitor.
- **The kernel's board code at boot.** That would be a kernel change, and
  policy in the kernel: the kernel would enable a device it never uses.
- **The PWM driver.** Those registers gate UART0, the timers and SPI, and
  SYSCLK would be its second row. Too much authority for a PWM driver.

The servo routing goes to root for the same reason the PWM driver cannot do
it (the quota above). For each servo pad, root applies the board table:
IO MUX word `0x1800`, `FUNCn_OUT_SEL_CFG` = `73 + channel | OEN_SEL`, and
the pad's bit in `ENABLE_W1TS` or `ENABLE1_W1TS`. Before routing it turns
the LEDC clock on and selects XTAL (§2.4), so the routed pad shows the
channel's idle level, 0, from that moment (§5.5).

For servos on 41 and 42 the words are `FUNC41 = 0x449`, `FUNC42 = 0x44A`
and `ENABLE1_W1TS` bits 9 and 10.

`OEN_SEL = 1` takes the output enable from `ENABLE[n]`, which root has just
set. The alternative trusts LEDC to drive the peripheral output-enable
signal, which no source here describes. ESP-IDF's own LEDC path sets the
`ENABLE` bit too (`gpio_set_direction(OUTPUT)` before
`esp_rom_gpio_connect_out_signal`).

The alternative is to make the GPIO driver the pin controller: its setup
would also say "route pad 41 from signal 73". That is cleaner, since one
process would own every pad, but it couples the drivers. Every PWM test
would then need both drivers running, and on this board's current layout
that is two children, which do not fit (§11, U5). That makes it Open
question 1.

### 3.4 The interrupt line and the Timer

- **The line.** Root binds matrix source 16 with
  `system.interrupt_bind(line: 16)` and gives the Interrupt to the GPIO
  driver under a tag, as `tests/uart-service` gives the UART's line. The
  HAL routes the source to one of its level-1 CPU interrupts on unmask
  (ABI.md §6c). GPIO's source is a level: `PCPU_INT & INT_ENA`, falling when
  `STATUS_W1TC` clears the bit. That fits the kernel's mask-on-fire,
  ack-to-rearm model (spec §9) with no HAL change.
- **Timers.** The drivers need none. The client (the controller in SO-78)
  holds its own Timer for debounce and travel.

---

## 4. The GPIO driver

### 4.1 The process

`tests/svc-gpio-esp32s3/`, a child built for `xtensa-esp-unknown-elf` with
`hal/xtensa-esp32s3/user/child.ld`. Its boot set, under tags its launcher
chooses (the uart-service pattern): a masked System handle, the GPIO and IO
MUX windows, the bound Interrupt and the connection's outlet.

It maps both windows into itself and finds each base with `Mapping.base()`
rather than a constant, so the code would not change on a translating tier.
It attaches the Interrupt and gives the outlet to one Waiter, then serves:
one `wait`, one arm per delivery kind, as `svc-uart-ns16550` does.

**Budgets it meets:**

- mapping rows: 2 of 2;
- handles: System, Process, 2 windows, 2 Mappings, Interrupt and Waiter
  make 8, plus one per held wait, against a child's quota of 12. So hold at
  most 3 waits (`MAX_HELD`), or release the System handle after deriving
  Process. The smoke holds the maximum to prove the budget.

### 4.2 The protocol (`tests/gpioproto/`)

The wire is the `uartproto` shape: byte 0 is the op, then the payload. A
reply's byte 0 is a status, then its payload. Every op is a call, so a
refusal always reaches the caller.

```
op           payload                     reply (after the status byte)
Setup  1     n x {pin, mode, init}       -
Read   2     pin                         level
Write  3     pin, level                  -
WaitLevel 4  pin, level                  level, fires (u32)
Inspect 5    pin                         iomux, pin_cfg, out_sel written;
                                         iomux, pin_cfg, out_sel read back
```

- **Modes:** `InputPullUp`, `InputPullDown`, `InputFloating`, `Output`
  (with an initial level), and `OutputLoopback` (`Output` with `FUN_IE` set,
  so `IN` follows the driven level: a self-check that needs no meter).
- **Statuses:** `Ok`; `BadOp`; `BadPin` (not a GPIO of this part); `NotMine`
  (not in the setup); `BadMode` (a write to an input, a wait on an output);
  `BadState` (anything before setup, or a second setup); `Busy` (the held
  table is full).
- **`Setup` is accepted once.** Its sender is root, which posts it before
  any client holds a sibling inlet. Every op before it, and any second one,
  is `BadState`. So pins are configuration that arrives as data: the same
  driver binary serves the smoke and the box with different pins.
- **Per-client pin authority** inside one driver (by sender pid, SO-49) is
  not in this version: Open question 5.

What `Setup` writes for an input pin with a pull-up (pin 21):

- IO MUX `0x1B00`;
- `PIN21` = `INT_TYPE 3 << 7 | INT_ENA bit 13` = `0x2180`;
- `FUNC21_OUT_SEL` = `0x100` (no output);
- the pin's `ENABLE_W1TC` bit.

For an output it writes IO MUX `0x1800` (or `0x1A00` for loopback),
`FUNCn_OUT_SEL` = `0x100 | OEN_SEL` = `0x500`, the level through
`OUT_W1TS`/`W1TC` first, then `ENABLE_W1TS`, so the pad never drives a
stale level.

### 4.3 Waiting for the switch

**`WaitLevel(pin, level)` answers when the pin reads `level`.**

- When the request arrives, the driver reads `IN`. If the pin already
  reads `level`, it replies at once.
- Otherwise it holds the obligation, attaches it to its Waiter one-shot to
  learn of abandonment (the UART driver's pattern), and answers when an
  interrupt shows the level.
- The edge interrupt is armed from `Setup` on, so a change between the read
  and the hold is a fire still to be delivered. There is no window in which
  an edge is lost. An edge-wait ("tell me about the next edge") has exactly
  that window, between the client's own read and its request, which is why
  it is not offered.
- `fires` counts the interrupts that saw this pin's status bit since setup.
  Several edges between two services count once. It is diagnostic, and
  under bounce its value depends on timing, so a case may print it and
  never asserts it.
- A level held for less than the driver's service time may never be read.
  For a toggle switch that sits where it is put, that cannot matter.

**How the controller waits on the switch and a Timer at once.** It posts
`WaitLevel` with `post` rather than `send`, which yields a `PipeReply`
claim. It then adds the claim (`waiter.add(reply:)`) and its Timer
(`waiter.add(timer:)`) to one Waiter and waits. Whichever arrives first is
the record. To give up on the switch, it drops the claim, and the driver
sees the abandonment and discards the obligation.

**Weighed against an Event the driver signals.** The client would have to
create an Event and pass it to the driver. The driver would keep a
pin-to-Event table. And the client would still need a second round trip
(`Read`) to learn the level the Event only announced. The held call carries
the answer in the wake itself and reuses the shape the UART driver already
proves. Recommended: the held call.

### 4.4 Edge, level, and the ack sequence

`INT_TYPE` is 3, any edge. The level types (4 and 5) assert for as long as
the level holds, which would make the driver toggle `INT_ENA` per wait;
edges plus the level read above give the same semantics without that.

On an interrupt delivery, the driver:

1. reads `PCPU_INT` and `PCPU_INT1` (which pins fired);
2. writes exactly those bits to `STATUS_W1TC` and `STATUS1_W1TC`;
3. reads `IN` and `IN1`;
4. calls `irq.ack()`;
5. answers each held wait whose pin now reads its level, and adds one to
   `fires` for each pin that fired.

**The order is the point.** Clearing (2) before reading the level (3)
means that an edge after the clear sets the status again and fires again
after the ack, so the level read in (3) is never stale without a new fire
pending. Reading the level first would let an edge between (3) and (2) be
cleared unseen. Everything that touches the device happens before the ack,
as spec §9 requires.

### 4.5 Debouncing belongs to the client

SO-78 has the controller debounce with a Timer: wait for "on", sleep the
settle time, `Read`, and act only if the switch still reads "on". That is
right.

- The driver stays mechanism. The settle time is the application's policy.
- Level-wait semantics make the client's loop race-free.
- A bounce costs a few extra driver wakes and nothing else. Each bounce
  edge is one fire and one ack, the line masked in between.

`FILTER_EN` (IO MUX bit 15) is a glitch filter whose width no source states.
Its name and the SVD's wording suggest nanoseconds, not the milliseconds of
contact bounce. It is left off, and becomes a setup flag only if the board
shows spurious edges from the servo motors' noise.

### 4.6 When the driver dies

- **The client.** The kernel's teardown releases the driver's held
  obligations, so a parked call or an attached claim resolves as
  `PeerClosed` or abandoned (spec §2.1: dropping an obligation is how the
  client learns the answer will never come). The controller reports and
  exits. Root, which waits on each child's death (SO-78), prints which one
  died.
- **The line.** The driver's Interrupt dies with it, and the kernel masks
  the line (`destroy_interrupt`).
- **The pads.** They keep their configuration: the pull-up stays, and an
  output stays at its last level.
- **No restart in this version.** A restarted driver would re-run `Setup`,
  which is idempotent for pads.

---

## 5. The PWM driver

### 5.1 The process

`tests/svc-pwm-esp32s3/`. Its boot set: the LEDC window, the connection's
outlet and a masked System handle. It maps LEDC (1 of its 2 mapping rows),
gives the outlet to a Waiter, and serves.

It needs no interrupt and no Timer. The LEDC clock is on before it starts
(§3.3).

### 5.2 Timer arithmetic for a 50 Hz frame

The timer counts `2^res` ticks per frame, and each tick is `div` cycles of
the source, so `f_frame = f_src / (div * 2^res)`. `div` is 10.8 fixed point
in `CLK_DIV`, valid from 1.0 to 1023.996: field values 256 to 0x3FFFF, the
range ESP-IDF's `LEDC_IS_DIV_INVALID` checks. Rounding a pulse to the
nearest tick costs at most half a tick.

```
source         f_src     res  div (field)          tick      frame
XTAL_CLK       40 MHz    14   48.828125 (12500)    1.2207us  16384 ticks
                                                             = 20.000 ms
APB_CLK, PLL   80 MHz    14   97.65625 (25000)     1.2207us  20.000 ms
APB_CLK, ROM   40 MHz?   14   (12500, same as      1.2207us  20.000 ms only
                              XTAL)                          if 40 MHz
RC_FAST_CLK    ~20 MHz   14   ~24.4 (uncalibrated) ~1.2us    +- RC error
```

With XTAL at 14 bits, a pulse of `p` µs is `counts = (p * 16384 + 10000) /
20000`, and the field is `counts << 4`:

```
pulse     counts  as output      steps across the range
0.5 ms     410    0.50049 ms
1.0 ms     819    0.99976 ms     1.0 to 2.0 ms: 819 steps of 1.2207 us
1.5 ms    1229    1.50024 ms
2.0 ms    1638    1.99951 ms
2.5 ms    2048    2.50000 ms     0.5 to 2.5 ms: 1638 steps
```

Both ranges are representable with three orders of magnitude to spare. A
hobby servo's dead band is a few microseconds, so the steps are finer than
the servo can resolve.

**The divider word, `div * 256`, is `frame_us * 5 / 8` at 40 MHz and 14
bits** (`40 * frame_us / 64`). For 20 000 µs that is exactly 12500. The
arithmetic stays in 32 bits, and needs no 64-bit divide, which would call
`__udivdi3` on this target (ABI.md §1d).

12500/256 = 48 + 212/256, so the divider alternates between 48 and 49
source cycles to average 48.828125. Each tick jitters by one 25 ns XTAL
period around an exact mean; a servo cannot see it.

**Recommendation: XTAL_CLK, 14 bits.**

- It is the only source whose frequency is known whatever the ROM or a
  later clock change did.
- It is the one ESP-IDF marks "Dynamic Frequency Scaling compatible" with
  a fixed frequency ([DOC]).
- It needs no other block written.

APB would be wrong by a factor of two the day the CPU moves to the PLL.

### 5.3 The request: microseconds, not degrees

The request is `Set(channel, pulse_us)`.

- **The driver knows the signal.** A pulse width is what the hardware
  generates, and it is the same for every servo.
- **Degrees belong to the application.** An angle depends on the particular
  servo (centre, travel and direction differ between units of one model),
  so angle-to-pulse is calibration data, found on the bench. It lives in
  the controller's motion table (SO-78), where changing it is a data edit.

The issue's acceptance ("a servo moves to a commanded angle") is met by the
controller's table naming the pulse for that angle.

### 5.4 The protocol (`tests/pwmproto/`)

```
op          payload                          reply (after status)
Setup  1    frame_us, n x {ch, min_us,       div field
            max_us}
Set    2    ch, pulse_us                     counts
Stop   3    ch                               -
Inspect 4   ch                               words written: timer conf,
                                             conf0, conf1, duty;
                                             read back: duty_r, timer value
```

- **Statuses:** `Ok`, `BadOp`, `BadState` (before setup, or a second
  setup), `BadChannel` (not in the setup, or above 7), `OutOfRange`, and
  `BadFrame` (a divider outside 256..0x3FFFF).
- **Limits are refused, not clamped.** `Set` outside the channel's
  `[min_us, max_us]` answers `OutOfRange` and writes nothing. A clamped
  command would move the arm somewhere the caller did not ask for, silently.
- The limits come from root's setup. A client cannot widen them, because
  only the first message is a setup. Root's table should hold each servo's
  bench-measured safe range, so a wrong entry in the motion table cannot
  drive a servo into its end stop.

### 5.5 What `Setup`, `Set` and `Stop` write

- **Setup.**
  1. `CONF` = 3 (XTAL), first.
  2. `TIMER0_CONF` = `14 | div << 4` (`0x0003_0D4E` for 20 ms), with
     `RST` still set from reset; then `PARA_UP`; then `RST` set and cleared.
  3. For each channel: `HPOINT` = 0; `CONF0` = `TIMER_SEL 0 | IDLE_LV 0`,
     `SIG_OUT_EN` 0, `PARA_UP`.

  Every channel is configured and **off: the output holds its idle level,
  low.**
- **Set.**
  1. `DUTY` = `counts << 4` (`0x4CD0` for 1.5 ms).
  2. `CONF1` = `0xC010_0400`.
  3. `CONF0` = `SIG_OUT_EN | PARA_UP` (`0x14`).

  The new duty starts at the next frame boundary. A frame in progress
  finishes at its old width, so no update makes a runt pulse.
- **Stop.** `CONF0` = `IDLE_LV 0 | PARA_UP`, with `SIG_OUT_EN` and
  `DUTY_START` clear. The line goes low at the frame boundary and the servo
  goes limp.

### 5.6 Before the first request, and after the driver exits

The servo line goes through three states:

- **From reset until root routes the pad:** the pad's reset state. On 41
  and 42 that is probably the JTAG function, with an input enable and pull
  this design has not confirmed (§2.3; stage 0 reads them). The matrix's
  reset `OUT_SEL` 256 with `ENABLE` 0 drives nothing, so the servo sees no
  valid frame and stays limp.
- **From the routing until the first `Set`:** low, the channel's idle
  level. The servo stays limp.
- **From the first `Set`:** frames at the commanded width.

So the controller's first act is to command each servo to rest, one at a
time (§9). The first frame a servo sees is a deliberate one.

**After the driver exits or dies,** nothing touches LEDC. The timer and the
channels run on at the last duty, so **the servos hold their last position**.
The kernel's teardown does not touch devices. For a box that holds the arm
wherever it was, that is the right default. A driver that wants the servos
limp on a clean exit sends itself the `Stop`s first.

A restarted driver's `Setup` resets the timer: one frame boundary is
disturbed, the servo holds through it.

---

## 6. The CPU clock

**What is known.** The emulator's CCOUNT runs at 40 MHz on host time
(3 999 400 per 1 600 000 SYSTIMER ticks, §8.1; ABI.md §6a). The part's
clock has not been read. ESP-IDF's ROM constant says 40 MHz. The reset
value of `PRE_DIV_CNT` would make it 20 MHz. The `timer_tick` spin implies
about 2.8 million loop iterations per second, which is slow for either.

**Does the box need it raised?** No.

- Both drivers are interrupt- and request-driven, and spend their time
  parked.
- LEDC runs from XTAL, so the servo signal is exact at any CPU clock.
- The kernel times with SYSTIMER, also from XTAL.
- What the CPU clock decides is latency: the time from the switch's edge,
  through the GPIO driver, the controller and the PWM driver, to the next
  servo frame. Against an arm that takes hundreds of milliseconds to travel,
  tens of system calls are unlikely to matter even at the rate the spin
  suggests.
- Stage 2 measures this latency on the part: the driver prints the
  SYSTIMER time between the fire and its answer. That replaces the guess
  with a number before anyone touches the PLL.

**What raising it would take** ([IDF-HAL] rtc_clk.c,
`rtc_clk_cpu_freq_to_pll_mhz`):

- power up and calibrate the BBPLL over the analog I2C bus (regi2c);
- raise the digital and RTC voltage bias from eFuse-calibrated values, and
  step the LDO slaves in the right order around the switch;
- set `CPU_PER_CONF.CPUPERIOD_SEL` (80, 160 or 240 MHz from the 480 MHz
  PLL), then `SYSCLK_CONF.SOC_CLK_SEL` = 1.

APB then becomes 80 MHz. If the ROM left UART0 on APB, the console's
divisor doubles with it, so the kernel would have to move UART0 to XTAL
first. None of this can be checked under the emulator.

**Recommendation: not for the box, and not in this unit.**

- Stage 0 reads the clock and separates "clock low" from "fetch slow" by
  measuring CCOUNT against SYSTIMER and cycles per loop iteration. That is
  cheap and read-only.
- If stage 2's latency is unacceptable, raising the clock becomes a HAL
  unit of its own: the console's clock source first, then the PLL, with a
  board stage for each.

---

## 7. Where the code lives and what is typed

### 7.1 Packages

```
tests/esp32s3regs/      the register blocks (GPIO, IO MUX, LEDC, the two
                        SYSTEM words), the pad and signal constants, and
                        the pure functions that compute register words
                        (pad_word(mode), out_sel(signal), pulse_counts(us),
                        divider(frame_us)); a library, as uartproto is
tests/gpioproto/        the GPIO wire: ops, statuses, body builders
tests/pwmproto/         the PWM wire
tests/svc-gpio-esp32s3/ the GPIO driver
tests/svc-pwm-esp32s3/  the PWM driver
tests/s3-gpio-service/  the GPIO smoke's root (client and launcher)
tests/s3-pwm-service/   the PWM smoke's root, with the board table
```

They live in `tests/` because the tree's only drivers and protocols do
(`svc-uart-*`, `uartproto`). A top-level `drivers/` is a decision for when
there is a second board: Open question 4.

### 7.2 Register blocks as typed views

The tree's idiom, and the saw-lang skill's rule for every MMIO device, is
two structs per device:

- a register-block struct that *is* the layout, in declaration order, with
  `ReadOnly<T>` and `WriteOnly<T>` markers and explicit reserved words, and
  its size pinned by `static_assert`;
- a safe driver struct holding `UnsafeMemory<Block, Device>`, with the
  device API as its methods (`EspUart` in `hal/xtensa-esp32s3/kernel/
  lib.saw`, `Uart` in `svc-uart-ns16550`).

**Arrays work through a Device view.** Probed for this design with the
pinned compiler (sawc 0.23.0, `--target xtensa-esp-unknown-elf -Oz`). A
struct with `pin: [UInt32; 54]` at 0x74, accessed as
`self.regs.pin[n].write(v)` and `.read()`, compiles to one `memw` and one
`s32i` or `l32i` at `0x6000_40C8` (PIN21) and `0x6000_4118` (PIN41). So
`PINn`, `FUNCn_OUT_SEL_CFG` and the IO MUX pads can be array fields.

An array of *structs* (LEDC's eight channels of five words) was not probed.
The implementer compiles that first. If it is refused, the fallback is five
`[UInt32; 8]`-shaped strided fields or a per-channel view constructed in one
method.

Field types: `UInt32` registers. `PCPU_INT`, `IN`, `IN1`, `STRAP`,
`DUTY_R` and `TIMERt_VALUE` are `ReadOnly<UInt32>`. Every W1TS and W1TC
register is `WriteOnly<UInt32>`. The S3 is a `UInt` HAL, so addresses are
`UInt`.

### 7.3 Configuration

Pins and limits are data in one place: **a static board table in the
launching root**. For the smoke it is the root package. For the box it is
SO-78's root:

```
switch:  pin 21, InputPullUp
servo A: pin 41, LEDC channel 0, 500..2500 us (until the bench says)
servo B: pin 42, LEDC channel 1, 500..2500 us
```

Root applies the servo rows itself (§3.3) and sends the rest as each
driver's `Setup` message. Moving a servo to pin 47 is one row of root's
table, with no driver rebuilt. A start argument cannot carry this: a child's
entry gets one boot handle and nothing else (spec §12).

---

## 8. Testing

### 8.1 What the emulator models (probed)

The probe was a copy of the SO-73 bare stub
(`.claude/lead/so73/stub/`), built in this design's scratchpad. It was run
once under `~/.espressif/tools/qemu-xtensa/esp_develop_9.2.2_20250817`,
under the suite lock. Each line is a read, or a read, a write and a read
back:

```
IO MUX 21, 41, 42                 read 0x00000000
IO MUX 41 <- 0x1800, 21 <- 0x1B00 read back 0x00000000
GPIO STRAP                        0x00000004 (the one modelled register)
GPIO DATE, ENABLE1, IN, STATUS,   0x00000000; writes to ENABLE1_W1TS,
  PCPU_INT                        FUNC41_OUT_SEL, PIN21, STATUS_W1TS read 0
LEDC DATE, CONF, TIMER0_CONF,     0x00000000 before and after every write
  CH0_DUTY, CH0_CONF0
SYSTEM PERIP_CLK_EN0, RST_EN0,    0x00000000; a write to CLK_EN0 reads 0
  CPU_PER_CONF
SYSTEM SYSCLK_CONF                0x000A8401 (the emulator's own model:
                                  PLL selected; says nothing of the part)
matrix map[16] <- 2               reads 2; INTERRUPT stays 0
CCOUNT over 1 600 000 SYSTIMER    3 999 400 (40 MHz)
```

So under this emulator:

- nothing faults. Writes to all four blocks are dropped and reads answer
  0, so both drivers can run their full setup in a smoke case;
- `IN` always reads 0. With the switch wired to ground through a pull-up,
  0 means "closed". A `WaitLevel(21, low)` answers at once, and a
  `WaitLevel(21, high)` can never be answered;
- no GPIO interrupt can be raised. Neither a real edge nor a software
  `STATUS_W1TS` sets anything, and source 16 pends nothing. The installed
  model has no input path a test could drive.

### 8.2 The smoke cases

These are S3 cases of the board's own, added to `_esp32s3_smoke`'s process
cases as `irq_line_cycle` is. Each is root plus one child, the layout the S3
has. Each case's header says that the device is absent under this emulator
and what the case proves anyway.

**`gpio_service`.** Rows: GPIO and IO MUX. Root binds 16, creates the
driver, gives it the windows, the line and the outlet, and is its client.

- `Read(21)` before setup answers `BadState`.
- `Setup {21 InputPullUp, 41 OutputLoopback low}` answers `Ok`. A second
  setup answers `BadState`.
- `Inspect(21)` shows written words IO MUX `0x00001b00` and PIN
  `0x00002180`. `Inspect(41)` shows `0x00001a00` and out_sel `0x00000500`.
  **This holds the reviewer's requirement under the emulator:** a driver
  that writes `MCU_SEL` 0 prints `0x00000b00` and fails the case. Read-back
  words are printed only on a board image.
- `WaitLevel(21, low)` answers at once with level 0. `Read(21)` answers 0.
- `WaitLevel(21, high)`, posted and attached beside a Timer: the Timer
  fires first (no edge exists here, so the order is fixed by construction).
  Root drops the claim, and the driver's closing report says
  `abandoned=1`.
- Three held waits answer `Busy` on the fourth (the handle budget, §4.1).
- Refusals: `Read(22)` `BadPin`; `Read(19)` `NotMine`; `Write(21, 1)`
  `BadMode`; an op byte 0x7F `BadOp`.
- Root releases its inlet. The driver's loop ends and it exits 0, and the
  transcript shows root's death wait.
- Mutation checks: `MCU_SEL` 0; `INT_TYPE` 1; `INT_ENA` cleared; the
  setup-once rule removed; the `Busy` bound removed.

**`pwm_service`.** Rows: GPIO, IO MUX, LEDC, SYSCLK. Root applies the board
table and prints the words it wrote: CLK_EN0 bit 11, RST_EN0 bit 11 clear,
IO MUX 41 `0x00001800`, FUNC41 `0x00000449`, ENABLE1 bit 9. It then creates
the driver with LEDC alone and is its client.

- `Setup {frame 20000, ch0 500..2500}` answers div `12500`.
  `Setup {frame 1000000}` would need 625 000 > 0x3FFFF and answers
  `BadFrame`; that check runs before the real setup, in a case of its own.
- `Set(0, 1500)` answers counts `1229`. `Inspect(0)` shows timer conf
  `0x00030d4e`, duty `0x00004cd0`, conf1 `0xc0100400` and conf0 `0x14`.
- `Set(0, 2600)` answers `OutOfRange`, and `Inspect` shows the duty
  unchanged.
- `Set(1, 1500)` answers `BadChannel`. `Stop(0)` answers `Ok`, and the
  conf0 word it wrote is `0x10` (`PARA_UP` alone).
- Mutation checks: the rounding (+10000 removed gives 1228); the range
  check; `CONF` written after the timer (the case asserts the order of the
  words written, in a written-words log).

Neither case asserts a time or a count that accrues with time.

### 8.3 Board stages

Each stage is a flashable image built with
`tools/sos_runner.py --board esp32s3 --emit-flash CASE --out PATH`, sent to
the reviewer, who has the board. The only write is
`write-flash 0x0 IMAGE`: no eFuse write and no erase. Each request carries
the image, its SHA-256 and the expected console text.

Stages 1 to 4 are board-only cases. On the emulator they would wait forever
for an edge or a meter, so the runner needs a `"board_only": True` key that
`--emit-flash` builds and a smoke run skips.

**Stage 0: `s3_periph_probe`.** A harness kernel; no wiring needed. It
reads and prints:

- `SYSCLK_CONF`, `CPU_PER_CONF`, `PERIP_CLK_EN0` and `PERIP_RST_EN0`;
- the IO MUX words of 14, 21, 41, 42 and 47 at reset;
- `RTC_IO_RTC_PAD21` bit 19;
- CCOUNT over 1 600 000 SYSTIMER ticks, and CCOUNT per 1 000 000 iterations
  of `payload_spin`'s loop.

It then:

- routes source 16 to a free CPU interrupt, sets `PIN21` = `0x2180`, writes
  `STATUS_W1TS` bit 21, and prints whether `INTERRUPT` shows that CPU
  interrupt. It then writes `STATUS_W1TC` and prints that it cleared. This
  confirms source 16 and the status registers on the part without a switch;
- turns on LEDC's clock, selects XTAL, configures timer 0 as §5.5 does,
  and prints `TIMER0_VALUE` twice, some SYSTIMER ticks apart.

Expected text: the two yes/no lines read `yes`, the counter advances, and
the values are recorded in ABI.md.

**Stage 1: `gpio_toggle_board`.** The GPIO driver and root as client.
Wiring: an LED with a 330 Ω resistor from pin 41 to GND, or a meter.

Root sets 41 to `OutputLoopback`, then toggles it every 2 s ten times,
printing `pin 41 drove 1, IN reads 1` and the reverse. It prints
`Inspect(41)`'s read-back words. Expected: IO MUX `0x00001a00` read back,
which is **the reviewer's check on silicon**, and IN following OUT on every
line. The LED blinks.

**Stage 2: `gpio_switch_board`.** The same processes. Wiring: the switch
from 21 to GND. Root loops on `WaitLevel` for the opposite of the last
level, ten times, printing `switch on` or `switch off` with `fires=` and
the SYSTIMER microseconds from fire to answer (both printed, not asserted).
The owner flips the switch five times. Expected: ten lines alternating
on/off, starting from the switch's position at boot, and `Inspect(21)` read
back `0x00001b00`. This meets the issue's "the switch's edge wakes its
driver".

**Stage 3: `pwm_signal_board`.** Root applies the board table and starts the
PWM driver with LEDC alone. Wiring: a meter or LED on 41, no servo.

Root sets 1500 µs and prints the read-backs: IO MUX 41 `0x00001800`,
FUNC41 `0x00000449`, `DUTY_R` `0x00004cd0`, and `TIMER0_VALUE` advancing.
Expected: those lines. A DC meter reads about 3.3 V x 1.5/20 = 0.25 V, and
an LED is dim.

**Stage 4: `pwm_sweep_board`.** As stage 3, with one servo on 41, powered
as §9 says. Root commands 1000 µs, then 2000, five times each, 2 s apart,
then 1500, printing each command. Expected: the printed sequence, and the
servo moving between two positions and stopping centred. This meets the
issue's "a servo moves to a commanded angle", once the bench gives the
angle its pulse.

**Stage 5 (after U5): both drivers together.** Switch on: servo A to
2000 µs; switch off: 1000 µs. Servo B moves after A, never together. This
needs two child slots, so it belongs to U5 or SO-78.

---

## 9. Power and wiring

- **Supply.**
  - The servos take **5 V from the board's 5 V pin** (USB VBUS), never the
    3.3 V pin.
  - **All grounds are common**: board, servos and switch.
  - The 3.3 V signal drives a hobby servo's input directly.
  - Whether the board's 5 V pin passes through a diode or a fuse, and how
    much current it carries, is not known here: check the schematic or the
    silkscreen.
- **Bulk capacitor.** 470 to 1000 µF across the servos' 5 V and GND, near
  the servos, plus 100 nF ceramic. A micro servo draws a few hundred mA
  while moving and more stalled. USB 2 promises 500 mA, and a sag can reset
  the part: the brownout detector's state is unprobed (ABI.md §11).
- **One servo at a time.** The controller sequences lid, then arm, with a
  Timer between. The drivers do not enforce this. It is the motion table's
  job, and the table is data.
- **What the drivers do about power.**
  - A servo stays limp until its first command (§5.6), so power-up draws
    nothing from the servos.
  - The controller's first act is to command rest, servo by servo, with a
    pause between.
  - A ramp is possible in hardware with no CPU involvement (LEDC's fade:
    `DUTY_NUM`, `DUTY_CYCLE`, `DUTY_SCALE`, §2.4), but it is not in this
    version: Open question 3.
- **Optional:** a 10 kΩ pull-down from each servo signal to GND holds the
  line low from reset until root routes the pad, whatever the JTAG pads'
  reset pulls turn out to be.
- **GPIO 48** is the board's WS2812, an addressable LED that needs a timed
  serial protocol, not a level. A heartbeat LED for SO-78 goes on a spare
  pin with a resistor, or stays a console line.

---

## 10. Open questions, each with a recommendation

1. **Who routes the servo pads?** Recommended: root applies the static board
   table at boot (§3.3), and the PWM driver holds LEDC alone. The
   alternative is a pin-controller GPIO driver that routes on request. It
   has the cleaner authority story, but every PWM test then needs both
   drivers, which waits on U5.
2. **Servo pins 41 and 42, or 47 and 14?** Recommended: keep 41 and 42. The
   drivers select function 1 on every pad, so the JTAG pads cost nothing.
   (Using them as GPIO should not affect the built-in USB JTAG, which does
   not use these pads, but that is not verified here.)
3. **A ramp in the PWM driver?** Recommended: not in this version. Measure
   the supply on stage 4 with one servo first, and add a hardware-fade
   `Move(channel, pulse_us, duration_ms)` only if a step command sags it.
4. **Drivers in `tests/` or a new `drivers/`?** Recommended: `tests/` now,
   beside the UART service. Move them when a second board needs them.
5. **Per-client pin authority inside the GPIO driver?** Recommended: not
   now. The box has one client per driver. The drivers enforce their own
   pin sets, and on the Flat tier nothing below the protocol enforces more.

---

## 11. The order of work

No spec or ABI change to the kernel comes first, because none is needed.

- **U0, stage 0 probe (small; Sonnet).** The `s3_periph_probe` harness
  kernel (§8.3), which also runs in the emulator smoke, its emulator lines
  asserted. The board run gives `ABI.md` the clock, the pad reset values,
  source 16 and LEDC's counter on the part. Needs from the board: one
  flash, about 60 s of capture. It can start now, independent of the rest.
- **U1, rows and facts (small; Sonnet).**
  - The runner's multi-row `devices` key and the S3 table of windows;
  - the `board_only` key;
  - spec §12's stitcher sentence;
  - `ABI.md` §9 rewritten from §2 here, with sources, and the emulator
    lines from §8.1;
  - `tests/esp32s3regs/` with its static_asserts, and the LEDC
    array-of-structs compile check.

  No board needed.
- **U2, the GPIO driver (medium; Sonnet).** `gpioproto`, the driver, the
  `gpio_service` smoke and its mutation checks, and the stage 1 and stage 2
  board cases. Needs from the board: stage 1 (an LED or meter on 41) and
  stage 2 (the switch wired to 21).
- **U3, the PWM driver and root's board table (medium; Sonnet).**
  `pwmproto`, the driver, root's clock gate and routing, the `pwm_service`
  smoke, and the stage 3 and stage 4 board cases. Needs from the board:
  stage 3 (a meter), then stage 4 (one servo, the capacitor, a USB supply
  good for an amp).
- **U4, board stages (the lead and the reviewer).** Stages 0 to 4 in order,
  each run's console text pasted into the issue, and each finding recorded
  in `ABI.md` beside the emulator's answer.
- **U5, before SO-78: room for the box (medium; Opus for the budget
  decision).** Five processes, about five Waiters, three or four pipes and
  four child images:
  - the S3 layout grows to four child slots: flash slots, DRAM regions
    sized to what a driver needs rather than 96 KiB each, per-slot linker
    scripts as `hal/riscv32/user/child2.ld` does, and the runner's
    `child_region` rows;
  - `MAX_PROCESSES` and `MAX_WAITERS` are met by `SlabDonate` from root's
    pool (no kernel change), or by raising the compiled floors (a kernel
    constant every profile pays for).

  Stage 5 runs here.

U2 and U3 can run in parallel once U1 has merged. U0 can run alongside
anything.
