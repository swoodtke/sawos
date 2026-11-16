# SO-77: GPIO and PWM drivers for the ESP32-S3 (design, revision 3)

For the owner, before anything is built. Written against main at `cf3518c6`,
with SO-125 (per-board limits) merged, and SO-95's design at revision 6
(approved) for the formats and the box's image. Section 2 gives a source for
every register number; where a number has no source, it says so.

---

## 0. What changed from revision 2

1. **Each servo pad is routed function first** (t1). The PWM driver writes
   a pad's output select back to `0x100`, then the pad's IO MUX word, then
   the output select naming its channel: ESP-IDF's own order. Root gains a
   fourth setup write, `OUT1_W1TC = 0x600`, before the output enable, so
   at function 1 the pad drives a written 0. The pad goes from its reset
   state to a written low to the idle channel's low, each step a level a
   source gives (§3.3, §5.5). A plain GPIO output gets the same order: its
   enable is cleared first and set last (§4.4).
2. **The launcher sets each child's declared quota rows** (t3). An image's
   quota defaults are raise-only (`tools/sosimage/src/limits.saw`
   `plan_limits`), so revision 2's runner key, which put a declaration in
   the image's defaults, is refused at the link. The test roots now call
   `ProcessOp.QuotaSet` for each declared row before any give, which is
   SO-95's launch convention. The rows reach the root as a module the
   runner generates from the driver's `process.toml`, so the numbers are
   stated once (§3.5, §12).
3. **The setup writes are tested against the part's word** (t4). One
   routine in `esp32s3io` makes a setup write; a hosted test runs it
   against a fake register reading `0xf9c1e06f` and expects `0xf9c1e86f`
   for the set and the right words for the clear and the write. A plain
   store would turn off the flash, the console and SYSTIMER on the part
   (§8.2).
4. **Three checks gain red cases** (t5). Every op that names a channel or a
   pin is asked with a bad one, `Stop(8)` and `Inspect(8)` included; a
   channel is looked up in one place before any address is formed, because
   channel 8's `CONF0` slot is `TIMER0_CONF`. `GpioPort` carries the ack,
   so the hosted test asserts all four steps of the interrupt service. The
   bad-configuration cases run under a Mapping row of 0, so a driver that
   maps before it checks prints a different line (§5.4, §4.6, §8.3).
5. **The pulse bounds are checked in counts** (t6): `pulse_counts(min_us)`
   above 0 and `pulse_counts(max_us)` below 16384. Revision 2's bound in
   microseconds let a 100% duty through, which ESP-IDF says the hardware
   cannot make at full resolution (§5.2).
6. **Stage P1 looks at both pads and counts frames right.** It samples pads
   41 and 42 before any `Set`, sets both channels, and times 51 rising
   edges, 50 frames, per pad (§8.5).
7. **A driver stops on any error from its wait.** A declaration one row
   short then shows as the driver's stop line, which is how each quota row
   goes red (§4.1, §8.4).
8. **Smaller.** SO-125 has merged, so §3.6 and §10 drop the 16-entry
   caveat. ESP-IDF v5.4.1's paths are given beside v5.1's (§2). §2.7 moves
   `TIMERt_CONF`'s reset `RST` to the header and adds `PARA_UP`'s latch as
   SVD-only. The board file gains `OUT_W1TC` and `OUT1_W1TC`. Open
   question 5 now counts four statements of the servo pins.

**Thread t1:** taken as the reviewer proposes, with one more write: the
driver resets each pad's output select to `0x100` before the pad's IO MUX
word, so the order holds whatever an earlier run left there.

**Thread t3:** agreed; the test roots set the declared rows with
`QuotaSet`, read from a module generated from `process.toml`, and §8.4
names the red case for every declared row, `handles = 10` included.

**Thread t4:** agreed; a hosted test runs the setup-write routine against
the part's `0xf9c1e06f`. The same test belongs to the stock root's setup
writes in SO-95's U6.

**Thread t5:** agreed on all three: bad channels and pins on every op,
the ack inside `GpioPort`, and the bad-configuration cases under a Mapping
row of 0.

**Thread t6:** agreed; the check is on the counts.

---

## 1. Verdict

- **What gets built.** Two driver processes, each with a pipe protocol:
  - `svc-gpio-esp32s3`, for the switch and simple outputs. It holds the
    GPIO and IO MUX blocks and the GPIO interrupt line;
  - `svc-pwm-esp32s3`, for the servos. It holds LEDC and the routing
    registers of its own pads, and nothing of SYSTEM.

  Alongside them: `esp32s3io`, the part's numbers and the pure functions
  both drivers and their test roots share; `gpioproto` and `pwmproto`;
  smoke cases and board stages.
- **What the kernel gains: nothing.** `device_window_ok` accepts every
  window and part this design names; `irq_line_valid` accepts source 16.
  The board file, the builder, the runner and two documents change (§12).
- **The PWM clock is the crystal.** LEDC runs from XTAL_CLK at 40 MHz,
  14-bit resolution, divider 48.828125: a 20.000 ms frame in 1.22 µs steps
  under every clock key (§2.6).
- **Configuration is compiled in.** Pins, modes, the frame and each
  servo's pulse limits are a build-time module per driver instance. A
  client cannot widen a limit or claim a pin.
- **The switch is a level wait.** `WaitLevel(pin, level)` answers at once
  when the pin reads `level`, and otherwise when an any-edge interrupt shows
  it. Debouncing stays in the client (§4.5).
- **What only the board can show:** every register effect. The emulator
  drops writes to GPIO, the IO MUX and LEDC and raises no GPIO interrupt.
  The smoke proves the processes start, map their windows, write the right
  words in the right order and answer the protocol, refusals included. Four
  board stages prove the rest, and two of them need no wiring (§8.5).

---

## 2. The hardware, with sources

Sources, all public:

- **[IDF]** ESP-IDF v5.1, `components/soc/esp32s3/include/soc/`:
  `reg_base.h`, `gpio_reg.h`, `gpio_struct.h`, `gpio_sig_map.h`,
  `io_mux_reg.h`, `ledc_reg.h`, `ledc_struct.h`, `system_reg.h`,
  `periph_defs.h`, `soc_caps.h`, `soc.h`, `rtc_io_reg.h`,
  `rtc_cntl_reg.h`, `clk_tree_defs.h`.
- **[IDF-HAL]** ESP-IDF v5.1, `components/hal/esp32s3/include/hal/`:
  `gpio_ll.h`, `ledc_ll.h`, `rtc_io_ll.h`, `clk_gate_ll.h`. Also
  `components/driver/ledc/ledc.c` and `components/driver/gpio/gpio.h`
  (`gpio_types.h` for the interrupt types).
- **[SVD]** Espressif's `svd/esp32s3.svd`, for field descriptions and
  reset values.
- **[DOC]** ESP-IDF v5.1 LEDC guide for the ESP32-S3.
- **[ABI]** `hal/xtensa-esp32s3/ABI.md` in this tree: §9 (the emulator's
  GPIO and LEDC), §14 (the clock keys, measured on the part), §15 (the
  board probe).
- **[PART]** The board probe's run on the part, ESP32-S3 rev v0.2, ROM
  `esp32s3-20210327`, Nov 15 (`s3_mem_probe`, SO-109 c9).
- **[PROBE]** This design's own run of the installed emulator (§8.1).

The Technical Reference Manual was downloaded, but no tool on this machine
extracts its text or renders its pages, so no number below comes from it.
The copies of the headers are in this design's scratch folder (`idf51/`,
`svd/`). At v5.4.1, the tag `ABI.md` cites, the register headers are under
`components/soc/esp32s3/register/soc/`, the interrupt sources are in
`interrupts.h`, and `ledc.c` is `components/esp_driver_ledc/src/ledc.c`;
the reviewer re-read every number here at both tags (t2).

### 2.1 Blocks

```
block      base         registers used   source
UART0      0x6000_0000  (the console)    [IDF] reg_base.h
GPIO       0x6000_4000  0x004..0x62B     [IDF] reg_base.h
RTC IO     0x6000_8400  none (see 2.3)   [IDF] reg_base.h
IO MUX     0x6000_9000  0x004..0x0C7     [IDF] reg_base.h, [SVD] size 0xCC
LEDC       0x6001_9000  0x000..0x0D3     [IDF] reg_base.h
SYSTEM     0x600C_0000  0x018, 0x020     [IDF] reg_base.h; the next block,
                                         SENSITIVE, is at 0x600C_1000
INTMATRIX  0x600C_2000  (the kernel's)   [ABI] §6c
```

### 2.2 GPIO (0x6000_4000) — [IDF] gpio_reg.h, gpio_struct.h; [SVD]

```
off    register             use
0x004  OUT / 0x008 W1TS / 0x00C W1TC         output level, pins 0-31
0x010  OUT1 / 0x014 W1TS / 0x018 W1TC        pins 32-53
0x020  ENABLE / 0x024 W1TS / 0x028 W1TC      output enable, pins 0-31
0x02C  ENABLE1 / 0x030 W1TS / 0x034 W1TC     pins 32-53
0x038  STRAP                (read-only; the one register the emulator models)
0x03C  IN / 0x040 IN1       pad input, pins 0-31 and 32-53 (read-only)
0x044  STATUS / 0x048 W1TS / 0x04C W1TC      interrupt status, pins 0-31
0x050  STATUS1 / 0x054 W1TS / 0x058 W1TC     pins 32-53
0x05C  PCPU_INT             status of pins enabled for the CPU, 0-31 (RO)
0x068  PCPU_INT1            the same, 32-53 (RO)
0x074  PINn (n 0..53)       0x074 + 4n: per-pin interrupt configuration
0x554  FUNCn_OUT_SEL_CFG    0x554 + 4n, n 0..53: pad n's output signal
0x62C  CLOCK_GATE           bit 0 the GPIO clock, reset 1 (left alone)
```

`PINn` ([IDF] gpio_reg.h positions; [SVD] meanings):

```
bits 17:13  INT_ENA     bit 13: CPU interrupt ([IDF-HAL] GPIO_LL_INTR_ENA
                        is BIT(0) of the field); bit 14: the CPU's NMI [SVD]
bits  9:7   INT_TYPE    0 off, 1 rising, 2 falling, 3 any edge, 4 low level,
                        5 high level ([IDF-HAL] gpio_types.h)
bit   2     PAD_DRIVER  1 open drain
```

`PCPU_INT` and `PCPU_INT1` are the status for both cores on this part
([IDF-HAL] gpio_ll.h). Clearing is `STATUS_W1TC` and `STATUS1_W1TC`
([IDF-HAL] `gpio_ll_clear_intr_status`).

`FUNCn_OUT_SEL_CFG` ([IDF] gpio_reg.h; [SVD]), reset `0x100`:

```
bits 8:0  OUT_SEL      the output signal; 256 = SIG_GPIO_OUT_IDX, the plain
                       OUT/ENABLE bits ([IDF] gpio_sig_map.h)
bit  9    OUT_INV_SEL  invert the output
bit  10   OEN_SEL      1: the output enable is ENABLE[n]; 0: the
                       peripheral's own enable signal ([SVD])
bit  11   OEN_INV_SEL
```

Signals ([IDF] gpio_sig_map.h): `LEDC_LS_SIG_OUT0_IDX` is 73 and channels
0..7 are 73..80; `SIG_GPIO_OUT_IDX` is 256.

The interrupt matrix source ([IDF] periph_defs.h, counted from the enum):
`ETS_GPIO_INTR_SOURCE` is **16**, "interrupt of GPIO, level". The same
count gives UART0 27 and SYSTIMER TARGET0 57, the two numbers [ABI] §6c
found by probing. `ETS_LEDC_INTR_SOURCE` is 35 (not used).

Valid GPIOs are 0..48 except 22..25, and every valid GPIO can drive
([IDF] soc_caps.h `SOC_GPIO_VALID_GPIO_MASK`,
`SOC_GPIO_VALID_OUTPUT_GPIO_MASK`). GPIO 26..48 are powered from VDD_SPI
([IDF] soc_caps.h); this board's module runs it at 3.3 V (SO-76 c6).

### 2.3 IO MUX (0x6000_9000) — [IDF] io_mux_reg.h; [SVD]

Pad n's register is at `0x004 + 4n`, checked against each named pad: 1 at
0x08, 14 at 0x3C, 21 at 0x58, 41 at 0xA8 (`MTDI`), 42 at 0xAC (`MTMS`), 47
at 0xC0 (`SPICLK_P`).

```
bit  15     FILTER_EN   input filter: "pulse width shorter than 2 clock
                        cycles will be filtered out" ([IDF]; which clock is
                        not stated)
bits 14:12  MCU_SEL     the pad's function; GPIO is function 1 on every pad
                        ([IDF] PIN_FUNC_GPIO = 1)
bits 11:10  FUN_DRV     drive: 0 ~5 mA, 1 ~10 mA, 2 ~20 mA, 3 ~40 mA [SVD]
bit   9     FUN_IE      input enable
bit   8     FUN_PU      pull-up ([SVD]: FUN_WPU)
bit   7     FUN_PD      pull-down ([SVD]: FUN_WPD)
bits  6:0   SLP_DRV, SLP_IE, SLP_PU, SLP_PD, SLP_SEL, SLP_OE: the sleep
            copies, left 0
```

The pads this design names ([IDF] io_mux_reg.h), and each pad's word as the
part's ROM leaves it ([PART], decoded with the fields above):

```
GPIO  pad name  function 0      as the part leaves it
  1   GPIO1     GPIO1           (not read)
  2   GPIO2     GPIO2           (not read)
 14   GPIO14    GPIO14          0x0A00: function 0, IE, 20 mA, no pull
 21   GPIO21    GPIO21          0x0800: function 0, no IE, 20 mA, no pull
 41   MTDI      MTDI (JTAG)     0x0A00: function 0 (JTAG), IE, no pull
 42   MTMS      MTMS (JTAG)     0x0A00: function 0 (JTAG), IE, no pull
 47   SPICLK_P  SPICLK_DIFF     0x1A00: function 1 (GPIO), IE, no pull
```

- **The reviewer's finding holds** (SO-77 c4): on 41 and 42 function 0 is
  JTAG, and the part leaves them there. Whoever configures a pad writes
  `MCU_SEL = 1` and the whole word, whatever its reset value.
- **No pad above has a pull at reset.** The SVD's single reset value for
  the array, `0x0B00` (IE and a pull-up), is not what the part shows for
  these pads. A servo line on 41 or 42 is undriven and unpulled from reset
  until the PWM driver routes it (§5.6, §9).
- **Pad 21 has its input disabled at reset.** The switch reads nothing
  until the GPIO driver sets `FUN_IE`.
- **The filter** is two cycles of an unstated clock: nanoseconds at any
  clock this part runs, not the milliseconds of contact bounce. It is left
  off (§4.5).

**RTC pads.** GPIO 0..21 are also RTC pads. The part reads RTC pad 21's
`MUX_SEL` (`RTC_IO_RTC_PAD21_REG` bit 19) as 0, so the digital IO MUX has
the pad ([PART] `rtc_pad21_mux_sel=0`; [IDF] rtc_io_reg.h). The same word
reads `0x5000_0000`, which is `DRV` 2 and `RDE` 1: the RTC side's
pull-down, at its documented default of 1. On this part the digital IO MUX
has pull bits of its own, "independent with RTC registers" ([IDF]
soc_caps.h `SOC_GPIO_SUPPORT_RTC_INDEPENDENT`), and ESP-IDF sets a digital
pad's pulls through the IO MUX alone ([IDF-HAL] `gpio_ll_pullup_en`).
Nothing here writes the RTC IO block. Whether `RDE` still loads the pad
while `MUX_SEL` is 0 is not stated in any source here; stage G2 shows it,
because an open switch must read 1 through the pull-up (§8.5).

**The words this design writes:**

```
input, pull-up          MCU_SEL 1 | IE | PU | DRV 2   0x0000_1B00
input, pull-down        MCU_SEL 1 | IE | PD | DRV 2   0x0000_1A80
input, floating         MCU_SEL 1 | IE | DRV 2        0x0000_1A00
output                  MCU_SEL 1 | DRV 2             0x0000_1800
output with loopback,   MCU_SEL 1 | IE | DRV 2        0x0000_1A00
  and every servo pad
```

### 2.4 LEDC (0x6001_9000) — [IDF] ledc_reg.h, ledc_struct.h; [IDF-HAL]
ledc_ll.h; [SVD]

The S3's LEDC has low-speed mode only: 8 channels, 4 timers, a 14-bit
counter ([IDF] soc_caps.h `SOC_LEDC_CHANNEL_NUM` 8,
`SOC_LEDC_TIMER_BIT_WIDTH` 14, `SOC_LEDC_SUPPORT_XTAL_CLOCK` 1).

```
off           register         fields
0x00 + 0x14c  CHc_CONF0        1:0 TIMER_SEL, 2 SIG_OUT_EN, 3 IDLE_LV,
                               4 PARA_UP (write-only), ...
0x04 + 0x14c  CHc_HPOINT       13:0
0x08 + 0x14c  CHc_DUTY         18:0; the low 4 bits are a fraction
0x0C + 0x14c  CHc_CONF1        31 DUTY_START, 30 DUTY_INC, 29:20 DUTY_NUM,
                               19:10 DUTY_CYCLE, 9:0 DUTY_SCALE
0x10 + 0x14c  CHc_DUTY_R       18:0 the duty in force (read-only)
0xA0 + 8t     TIMERt_CONF      3:0 DUTY_RES, 21:4 CLK_DIV (10.8 fixed
                               point), 22 PAUSE, 23 RST, 24 TICK_SEL,
                               25 PARA_UP (write-only); RST resets to 1
                               [SVD]
0xA4 + 8t     TIMERt_VALUE     13:0 the counter (read-only)
0xC0..0xCC    INT_RAW/ST/ENA/CLR  (not used)
0xD0          CONF             1:0 APB_CLK_SEL, 31 CLK_EN
```

- **`APB_CLK_SEL`** selects the clock of all four timers: 1 APB_CLK,
  2 RC_FAST_CLK, 3 XTAL_CLK ([IDF-HAL] `ledc_ll_set_slow_clk_sel`; [SVD]).
  Its reset value, 0, selects nothing; ESP-IDF's getter aborts on it
  ([IDF-HAL] `ledc_ll_get_slow_clk_sel`). ESP-IDF selects a source before
  it touches a channel ([IDF-HAL] ledc.c: "set channel configurations and
  update bits before core clock is on could lead to error").
- **`TICK_SEL`** 0 is the clock `APB_CLK_SEL` chose; 1 is REF_TICK, which
  wants `APB_CLK_SEL` 1 ([SVD]). It stays 0.
- **`CLK_EN`** forces the register file's clock on; at 0 it is clocked
  "only when application writes registers" ([SVD]). ESP-IDF v5.1 never
  sets it ([IDF-HAL] ledc_ll.h, ledc.c), and this design follows.
- **`IDLE_LV`** is "the output value when channel is inactive (when
  `SIG_OUT_EN` is 0)" ([SVD]). It is not among the fields `PARA_UP`
  latches; `SIG_OUT_EN` is ([SVD] `PARA_UP`'s description).
- **A fixed duty** ([IDF-HAL] ledc.c `ledc_set_duty_with_hpoint`,
  `_ledc_update_duty`): HPOINT; DUTY = counts << 4; CONF1 =
  DUTY_START | DUTY_INC | DUTY_NUM 1 | DUTY_CYCLE 1 | DUTY_SCALE 0
  (`0xC010_0400`); then CONF0 with `SIG_OUT_EN | PARA_UP`. **Stop**
  (`ledc_stop`): IDLE_LV, `SIG_OUT_EN` 0, `DUTY_START` 0, `PARA_UP`.
- **The timer** (`ledc_timer_set`, `ledc_timer_rst`): DUTY_RES and
  CLK_DIV, `PARA_UP`, then RST set and cleared.
- **New settings take effect at the next PWM cycle** ([DOC]), so an
  update never cuts a pulse short.

### 2.5 SYSTEM (0x600C_0000): LEDC's clock gate — [IDF] system_reg.h; [SVD]

```
0x018  PERIP_CLK_EN0   bit 11 LEDC_CLK_EN
0x020  PERIP_RST_EN0   bit 11 LEDC_RST
```

Enabling LEDC is setting `PERIP_CLK_EN0` bit 11 and clearing
`PERIP_RST_EN0` bit 11, which is `periph_module_enable(PERIPH_LEDC_MODULE)`
([IDF-HAL] clk_gate_ll.h). The part reads `perip_clk_en0=0xf9c1e06f` (bit
11 clear: LEDC is gated, as the SVD's reset value says) and
`perip_rst_en0=0x00000000` (LEDC not held in reset) ([PART]). Both words
gate every peripheral on the chip; bit 2 of `PERIP_CLK_EN0` is UART0, the
console. Root writes them, as a set and a clear of bit 11 (§3.3).

### 2.6 Clocks

- **XTAL_CLK is 40 MHz** ([IDF] soc.h `XTAL_CLK_FREQ`; esptool on the part
  reported a 40 MHz crystal, SO-109 c9). It "comes from the external 40MHz
  crystal", while APB "is highly dependent on the CPU_CLK source" ([IDF]
  clk_tree_defs.h).
- **The CPU and APB under each key** ([ABI] §14d, measured on the part in
  §14h): `rom` 20 MHz (the crystal divided by two), `xtal40` 40 MHz,
  `pll80` 80 MHz. APB equals the CPU clock on the crystal and is 80 MHz on
  the PLL ([ABI] §14a, from `rtc_clk.c`). So APB is 20, 40 or 80 MHz.
- **The crystal reaches the peripherals undivided under every key.** UART0
  runs from XTAL_CLK with the ROM's divisor for 40 MHz, and the console
  reads correctly at 115200 under `rom`, `xtal40` and `pll80` alike;
  SYSTIMER counts the crystal at 16 MHz under every key ([ABI] §14a, §14d,
  §14h). No key writes LEDC or `PERIP_CLK_EN0` ([ABI] §14d lists every
  register each key writes).
- **So LEDC on XTAL_CLK gives the same frame under every key,** and LEDC on
  APB_CLK would not: a divider computed for 40 MHz gives a 40 ms frame
  under `rom` and a 10 ms frame under `pll80`. APB is ruled out.
- **RC_FAST_CLK** (about 20 MHz, uncalibrated) needs `RTC_CNTL_CLK_CONF`
  written, the block with the watchdogs. Ruled out.
- **Not checked:** LEDC timing from a 40 MHz XTAL_CLK while APB, the bus
  that carries its register writes, runs at 20 MHz (the `rom` key) is a
  combination ESP-IDF never runs, since its bootloader leaves APB at
  80 MHz. No source forbids it. Stage P1 measures the frame under `rom` and
  under `pll80` (§8.5).

### 2.7 Numbers that rest on one source

The reviewer re-read every base, offset, field position, signal number and
interrupt source of §2.1 to §2.5 against ESP-IDF v5.1 and v5.4.1 and
recomputed every word (t2). What rests on one source alone:

- **The SVD alone:** the drive strengths; which `INT_ENA` bit is the NMI;
  the meaning of `OEN_SEL`, `IDLE_LV`, `CLK_EN` and `TICK_SEL`; and which
  fields `PARA_UP` latches, on which "the channel is idle until `Set`"
  rests. The SVD's `PERIP_CLK_EN0` reset value is confirmed on the part;
  its IO MUX reset value is contradicted by the part for every pad read.
- **ESP-IDF's HAL alone:** the `INT_TYPE` values (`gpio_types.h`), the LEDC
  sequences read as prose from `ledc.c`, and that a plain output's enable
  is `ENABLE[n]` with its output select at `0x100` (`gpio_ll.h`'s
  `gpio_ll_output_enable` and `gpio_ll_output_disable`).
- **Header defaults not read on the part:** `GPIO_OUT1` and `GPIO_ENABLE`
  reset to 0. Nothing here depends on either: root and the drivers write
  the bits they rely on (§3.3, §4.4).
- **No source:** whether LEDC drives the peripheral output enable that
  `OEN_SEL` 0 would select (sidestepped: `OEN_SEL` 1); what a pad in
  function 0 (`MTDI`, `MTMS`) drives (left as the part leaves it, never
  enabled through the matrix); which clock `FILTER_EN` counts; whether RTC
  pad 21's `RDE` loads the pad while the digital IO MUX has it (§2.3).

---

## 3. What reaches a driver

### 3.1 The board's devices

`hal/xtensa-esp32s3/board.toml` gains four devices (SO-95 rule 3.2, the
format `tools/sosimage/src/board.saw` reads):

```toml
# GPIO: pad levels, output enables, interrupts and the output matrix.
[devices.GPIO]
type = "esp32s3-gpio"
base = 0x6000_4000
len = 0x1000
line = 16                   # ETS_GPIO_INTR_SOURCE
registers.ENABLE_W1TS = { offset = 0x024 }
registers.OUT_W1TC = { offset = 0x00C }
registers.OUT1_W1TC = { offset = 0x018 }
registers.ENABLE1_W1TS = { offset = 0x030 }
arrays.out_sel = { offset = 0x554, stride = 4, count = 54 }

# The IO MUX: each pad's function, pulls, input enable and drive.
[devices.IO_MUX]
type = "esp32s3-io-mux"
base = 0x6000_9000
len = 0x1000
arrays.pad = { offset = 0x004, stride = 4, count = 49 }

[devices.LEDC]
type = "esp32s3-ledc"
base = 0x6001_9000
len = 0x1000
line = 35                   # ETS_LEDC_INTR_SOURCE; no driver binds it

# Clock gates and resets of every peripheral, and the CPU's clock.
[devices.SYSTEM]
type = "esp32s3-system"
base = 0x600C_0000
len = 0x1000
registers.PERIP_CLK_EN0 = { offset = 0x018 }
registers.PERIP_RST_EN0 = { offset = 0x020 }
```

- Every window is 4 KiB and 4 KiB aligned, which `device_window_ok`
  accepts (`hal/xtensa-esp32s3/kernel/lib.saw`: a power of two of at least
  `PROT_GRAIN`, naturally aligned).
- `[types]` gains nothing: none of these types has a board input (rule 4.6;
  Open question 4 asks whether the GPIO type should).
- `SYSTEM` holds the CPU's clock registers too. Only root ever holds its
  row, and root writes two of its words, each through a 4-byte carve
  (SO-95 rule 8.5). The clock keys write `SYSCLK_CONF` before root runs.

### 3.2 How fine the isolation is

**This board's tier is Flat** ([ABI] §5e; spec §5b). Any process can read
and write any address. Holding only its own windows is capability
discipline: the syscall-path checks are real (a map of a window a process
does not hold is refused), memory protection is not.

Per-pin windows are coarse even on a tier that enforces them. `OUT`,
`ENABLE`, `IN` and `STATUS` are one word for 32 pins; the W1TS and W1TC
forms let writers set and clear their own bits without a race, but separate
nothing. So the design isolates by **role**:

- **the GPIO driver** holds GPIO and the IO MUX whole, touches only the
  pins its configuration names, and refuses requests for any other pin
  (`NotMine`);
- **the PWM driver** holds LEDC and two parts: `GPIO.out_sel[41..=42]`
  (0x6000_45F8, 8 bytes) and `IO_MUX.pad[41..=42]` (0x6000_90A8, 8 bytes).
  It can route its own two pads and no other, the switch's included;
- **root** writes LEDC's clock gate, its reset, and the two pads' output
  level and output enable once, before any child starts, and gives the rows
  away or releases them.

**A pad pair is one part when it is `(n, n + 1)` with n odd.** Both arrays
start 4 bytes past an 8-byte boundary (`0x554 = 0x550 + 4`, `0x004`), so
pad n's registers sit on an 8-byte boundary in both blocks exactly when n
is odd. 41/42 and 1/2 qualify; 47/14 does not (Open question 1).

### 3.3 Who writes what, in what order (t1)

The PWM path, in the order it happens, in the box and in every PWM case:

```
who     what                                  pad 41 after
reset                                         function 0 (MTDI), IE, no
                                              pull, OUT_SEL 0x100: the
                                              part's reset state,
                                              undriven by anything here
root    PERIP_CLK_EN0 |= 0x800  LEDC clocked  unchanged
root    PERIP_RST_EN0 &= ~0x800 out of reset  unchanged
root    OUT1_W1TC = 0x600       OUT1 bits     unchanged
                                9, 10 = 0
root    ENABLE1_W1TS = 0x600    enables set   unchanged: OEN_SEL is 0, so
                                              the enable is function 0's
                                              own (a)
root    creates the PWM driver, gives it LEDC, the two parts, a masked
        System and the outlet, starts it
driver  CONF = 3                XTAL_CLK      unchanged
driver  TIMER0_CONF, three      14 bits,      unchanged
        words                   20 ms, runs
driver  per channel: HPOINT 0,  idle: output  unchanged
        CONF0 = PARA_UP         IDLE_LV 0
driver  FUNC41_OUT_SEL = 0x100  plain GPIO    unchanged on a first start
        signal, OEN_SEL 0
driver  IO MUX 41 = 0x1A00      function 1    driven low: OUT1 bit 9,
                                (GPIO)        written 0, enabled by
                                              ENABLE1 (b)
driver  FUNC41_OUT_SEL = 0x449  signal 73,    driven low: the idle
                                OEN_SEL 1     channel's IDLE_LV (c)
        (then the same three for pad 42)
client  Set(ch, pulse_us)                     frames at the width
```

(a) [SVD] `OEN_SEL`: "0: use peripheral output enable signal"; at function
0 the peripheral is the pad's IO MUX function, `MTDI`, the JTAG port's
data input. (b) At function 1 with the output select at `0x100` the pad is
ESP-IDF's plain GPIO output: data from `OUT1`, enable from `ENABLE1`
([IDF-HAL] `gpio_ll_output_disable` writes `0x100`, `gpio_ll_output_enable`
sets the `ENABLE` bit). (c) [SVD] `IDLE_LV`, `OEN_SEL`.

- **Every driven level is a written one.** The pad is undriven until its
  function becomes GPIO, then drives `OUT1`'s written 0, then the idle
  channel's 0. No step depends on what function 0 drives.
- **The order is ESP-IDF's:** `ledc_set_pin` ([IDF-HAL] ledc.c, v5.1; at
  v5.4.1 `_ledc_set_pin` in `esp_driver_ledc/src/ledc.c`) selects
  `PIN_FUNC_GPIO`, sets the direction, then connects the signal. ESP-IDF's
  `gpio_ll_iomux_out` writes `OEN_SEL` for a pad routed through the IO MUX
  ([IDF-HAL] gpio_ll.h), so the output-enable select is live for a pad in
  another function, which is why the channel's select comes last.
- **The first output-select write** (`0x100`) is a no-op on a first start,
  and makes the order true on any later one, whatever the select held.
- **Root never touches LEDC.** Its four words are SO-95 rule 8.5's setup
  writes: the stock root's `[[setup]]` in the box's image, and the same four
  writes, made by the same routine, in the PWM test root. Each set or clear
  is a read-modify-write of a word that gates every peripheral; a plain
  store would turn off the flash, the console and SYSTIMER (t4, §8.2).
- **Root's writes come first by construction:** the stock root makes every
  setup write before it starts anyone (rule 8.5), and the driver's first
  LEDC write needs the module's clock.
- **A restarted driver** runs the same start: the timer is reset (one
  frame boundary disturbed), each channel goes idle and each pad passes
  through a written low, so a servo is limp until its next `Set`. No
  restart is built in this version.

### 3.4 The interrupt line

Root binds matrix source 16 (`system.interrupt_bind(line: 16)`) and gives
the Interrupt to the GPIO driver, as `tests/uart-service` gives the UART's.
The HAL routes the source to a level-1 CPU interrupt on unmask ([ABI] §6c).
The source is a level, `PCPU_INT & INT_ENA`, which falls when
`STATUS_W1TC` clears the bit: the kernel's mask-on-fire, ack-to-rearm model
(spec §9) with no HAL change. Neither driver needs a Timer.

### 3.5 Each driver's declaration

`drivers/svc-gpio-esp32s3/process.toml`:

```toml
format = 1
arena = 0x1000
ram = "fast"                # it services the switch's interrupt

[[boot]]
name = "system"
kind = "system"
tag = 1

[[boot]]
name = "gpio"
kind = "window"
type = "esp32s3-gpio"
tag = 2

[[boot]]
name = "iomux"
kind = "window"
type = "esp32s3-io-mux"
tag = 3

[[boot]]
name = "gpio"
kind = "line"
type = "esp32s3-gpio"
tag = 4

[[boot]]
name = "gpio"
kind = "serve"
protocol = "gpioproto"
tag = 5

[quota]
mappings = 2
handles = 13
waiters = 1

[build]
inputs = ["gpioconfig"]
```

`drivers/svc-pwm-esp32s3/process.toml`: the same shape, with `system`
(tag 1), `ledc` (a window of `esp32s3-ledc`, tag 2), `outsel` (a window of
`esp32s3-gpio`, `part = true`, tag 3), `pads` (a window of
`esp32s3-io-mux`, `part = true`, tag 4) and `pwm` (serve, `pwmproto`,
tag 5); no `ram` line; `[quota]` `mappings = 3`, `handles = 10`,
`waiters = 1`; `[build] inputs = ["pwmconfig"]`.

**The handle counts.** A driver's outlet is consumed by its Waiter's
attach (the UART driver's completion-queue shape), so it leaves the table:

```
                              GPIO   PWM
records at start                5     5
outlet consumed by the attach  -1    -1
Process (process_self)          1     1
Mappings                        2     3
Waiter                          1     1
the request being served        1     1
held waits (MAX_HELD)           4     -
                               13    10
```

**The launcher sets these rows** with `ProcessOp.QuotaSet` after the
create and before any give (SO-95's launch convention; spec §2.7). An
image's quota defaults cannot carry them: they are raise-only, and the
builder refuses a default below the compiled one (`plan_limits` in
`tools/sosimage/src/limits.saw`), which waiters 1 and handles 10 are. A
row set by the launcher may go either way: GPIO's handles 13 is above the
compiled 12, the waiters rows below the compiled 2.

Neither driver declares a default for its input: the image (or the case)
must name the configuration, so nothing runs with pins nobody chose
(rule 4.6 refuses an input with no value).

SO-95's §4 gives the GPIO driver 11 handles; the count above, with four
held waits, is 13.

### 3.6 Root's handle table

Root's table holds 32 on the S3 (`hal.MAX_HANDLES`, SO-125). The builder
refuses an image whose root holds more than that at boot (`plan_limits`:
System, Process, Thread and one per row). Counted step by step, root
releasing each thing as soon as nothing later needs it; `QuotaSet` mints
nothing:

**`gpio_service`'s root** (rows: the driver's blob and destination, GPIO,
IO_MUX):

```
entry: System, Process, Thread, 4 rows                  7
process_self                                            8
create the driver (+1), release blob and destination    9, 7
set the declared quota rows                             7
bind line 16                                            8
System sibling made, given; GPIO, IO_MUX, line given    9, 8, 7, 6, 5
pipe_create (+2), outlet given (-1)                     7, 6
Waiter, Timer                                           8
posted claims, at most MAX_HELD + 1                     13
```

**`pwm_service`'s root** (rows: blob, destination, GPIO, IO_MUX, LEDC,
SYSTEM):

```
entry: System, Process, Thread, 6 rows                  9
process_self                                            10
create the driver (+1), release blob and destination    11, 9
set the declared quota rows                             9
each setup write: carve (+1), map (+1), unmap and
  release both (-2); SYSTEM's row released after its
  two                                                   11, 9 (x4); 8
System sibling made, given; LEDC given                  9, 8, 7
each part: carve (+1), give (-1)                        8, 7 (x2)
IO_MUX released; GPIO kept (stage P1 maps it)           6
pipe_create (+2), outlet given (-1)                     8, 7
Waiter, Timer, GPIO mapping (P1 only)                   10
posted claims, at most 2                                12
```

Peaks 13 and 12 of 32. The box's stock root peaks at 17 of 32 (SO-95 §5.3,
which counts the same reach; a fourth setup write adds nothing to the
peak, since each is released before the next).

---

## 4. The GPIO driver

### 4.1 The process

`drivers/svc-gpio-esp32s3/`, a child for `xtensa-esp-unknown-elf` linked
with `hal/xtensa-esp32s3/user/child.ld`. In order, it:

1. takes its boot records by the tags of §3.5 and derives its Process
   handle;
2. checks its configuration (§4.2), and stops on a bad one **before it maps
   anything**;
3. maps both windows and finds each base with `Mapping.base()` (SO-95
   rule 4.5);
4. configures every pin (§4.4);
5. attaches the Interrupt and the outlet to one Waiter, and serves: one
   `wait`, one arm per delivery kind, as `svc-uart-ns16550` does.

**A wait that answers an error is a stop.** The driver prints the status
and exits non-zero. A wait answers `QuotaExceeded` when the delivery's mint
does not fit the driver's handle row (spec §2.2), so a declaration one row
short shows as that line, not as a driver that quietly serves less.

### 4.2 Configuration: `gpioconfig`

A Saw module compiled into the driver (§7.3):

```saw
import esp32s3io.{GpioPin, PinMode}

/// The pins this driver serves, in the order it configures them.
public static PINS: [GpioPin; 1] = [
    GpioPin(pin: 21, mode: PinMode.InputPullUp, level: 0),
]
```

Modes: `InputPullUp`, `InputPullDown`, `InputFloating`, `Output` (driven
from `level` at start), `OutputLoopback` (an output with its input enabled
and an any-edge interrupt, so `IN` follows the driven level and a level
change raises the line: a self-check that needs no wiring).

**Checked when the driver starts,** before it maps anything
(`esp32s3io.check_gpio_config`). A failure prints one line naming the pin
and the reason and the driver exits with a non-zero code:

- a pin that is not a GPIO of the part (22..25, above 48);
- a pin the chip holds for itself on every S3 image: 26..32, the SPI bus
  the code runs from ([IDF] io_mux_reg.h pad names `SPICS1`, `SPIHD`,
  `SPIWP`, `SPICS0`, `SPICLK`, `SPIQ`, `SPID`), and 43 and 44, UART0's pads
  `U0TXD` and `U0RXD`, the kernel's console;
- a pin named twice;
- an output's initial level other than 0 or 1.

### 4.3 The protocol: `drivers/gpioproto/`

The wire is `uartproto`'s shape: byte 0 the op, then the payload; a reply's
byte 0 is a status, then its payload. Every op is a call. Integers are
little-endian.

```
op            payload       reply payload
Read     1    pin           level
Write    2    pin, level    -
WaitLevel 3   pin, level    level, fires (u32)
Inspect  4    pin           IO MUX word, PIN word, OUT_SEL word, IN bit,
                            each read from the device now
Log      5    from (u8)     total (u8), dropped (u16), n (u8), then n
                            entries of {address u32, word u32}, n <= 15
Status   6    -             held (u8), abandoned (u16), fires (u32)
```

**Statuses:** `Ok`; `BadOp` (an unknown op, or a payload of the wrong
length); `BadArg` (a level other than 0 or 1); `BadPin` (not a GPIO of this
part); `NotMine` (a GPIO not in the configuration); `BadMode` (a `Write` to
an input, a `WaitLevel` on a plain output); `Busy` (the held set is full).
They are checked in that order.

- **`fires`** counts the deliveries in which a pin's status bit was set,
  since start. Several edges between two services count once. It is
  diagnostic: a case prints it and never asserts it.
- **`Log`** is the driver's record of every register word it has written,
  in order, with its address: the first 64 writes, and a count of the rest.
  Every write goes through one method that writes the register and appends
  the entry, so the log is the writes, not a second computation of them.
  Under the emulator, which drops the writes, it is the only view of them.
- **`Inspect`** reads the device. Under the emulator it answers zeros; a
  board stage asserts it.

**Per-client pin authority** inside one driver (by sender pid) is not in
this version: Open question 3.

### 4.4 What the start writes

For each pin, in configuration order, whole words, never a read-modify-
write (the emulator reads 0, and the log must be the truth):

```
input (any pull)          output                    output, loopback
ENABLE(1)_W1TC  bit       ENABLE(1)_W1TC  bit       ENABLE(1)_W1TC  bit
FUNCn_OUT_SEL   0x100     OUT(1)_W1TS/W1TC  bit     OUT(1)_W1TS/W1TC  bit
IO MUX          (§2.3)    IO MUX          0x1800    IO MUX          0x1A00
STATUS(1)_W1TC  bit       FUNCn_OUT_SEL   0x500     FUNCn_OUT_SEL   0x500
PINn            0x2180    ENABLE(1)_W1TS  bit       ENABLE(1)_W1TS  bit
                          PINn            0         STATUS(1)_W1TC  bit
                                                    PINn            0x2180
```

- `PINn` = `INT_TYPE 3 << 7 | INT_ENA bit 13` = `0x2180`: any edge, to the
  CPU. `0x500` = `SIG_GPIO_OUT | OEN_SEL`.
- **An output's enable is cleared first and set last,** after its level,
  its function and its output select: the pad is undriven until it drives
  the written level, whatever function it reset to (§3.3's reasoning).
- **An input's interrupt is enabled last,** after its input enable and
  pull are set and its status cleared, so setting `FUN_IE` (pad 21's is off
  at reset) cannot leave a stale edge pending.

For pin 21 (`InputPullUp`) the log reads:

```
0x60004028 <- 0x00200000    ENABLE_W1TC, bit 21
0x600045a8 <- 0x00000100    FUNC21_OUT_SEL_CFG
0x60009058 <- 0x00001b00    IO MUX pad 21
0x6000404c <- 0x00200000    STATUS_W1TC, bit 21
0x600040c8 <- 0x00002180    PIN21
```

and for pin 47 (`OutputLoopback`, low):

```
0x60004034 <- 0x00008000    ENABLE1_W1TC, bit 15
0x60004018 <- 0x00008000    OUT1_W1TC, bit 15
0x600090c0 <- 0x00001a00    IO MUX pad 47
0x60004610 <- 0x00000500    FUNC47_OUT_SEL_CFG
0x60004030 <- 0x00008000    ENABLE1_W1TS, bit 15
0x60004058 <- 0x00008000    STATUS1_W1TC, bit 15
0x60004130 <- 0x00002180    PIN47
```

`Write(pin, level)` writes one word, the pin's bit to `OUT_W1TS` or
`OUT_W1TC` (`OUT1_*` for 32 and up).

### 4.5 Waiting for the switch

**`WaitLevel(pin, level)` answers when the pin reads `level`.**

- When the request arrives, the driver reads `IN` (or `IN1`). If the pin
  already reads `level`, it answers at once.
- Otherwise it holds the obligation, attaches it to its Waiter one-shot to
  learn of abandonment (the UART driver's pattern), and answers when an
  interrupt shows the level.
- The edge interrupt is armed from the start, so a change between the read
  and the hold is a fire still to be delivered: no edge can be lost. An
  edge-wait ("tell me the next edge") has exactly that window, between the
  client's own read and its request, which is why it is not offered.
- A level held for less than the driver's service time may never be read.
  A toggle switch sits where it is put.
- **At most `MAX_HELD` (4) waits are held.** A fifth answers `Busy`, never
  a dropped obligation, so the client learns why.

**How the controller waits on the switch and a Timer at once:** it posts
`WaitLevel` with `post`, adds the `PipeReply` claim and its Timer to one
Waiter, and waits. To give up on the switch it drops the claim; the driver
sees the abandonment and discards the obligation.

**Debouncing belongs to the client.** SO-78 has the controller wait for
"on", sleep the settle time, `Read`, and act only if the switch still reads
"on". The settle time is the application's policy; a bounce costs a few
driver wakes, each edge one fire and one ack. `FILTER_EN` filters
nanoseconds (§2.3) and stays off.

### 4.6 The interrupt service, and its order

On a delivery the driver:

1. reads `PCPU_INT` and `PCPU_INT1` (which pins fired);
2. writes exactly those bits to `STATUS_W1TC` and `STATUS1_W1TC`;
3. reads `IN` and `IN1`;
4. calls `irq.ack()`;
5. answers each held wait whose pin now reads its level, and adds one to
   `fires` if any pin fired.

**The order is the point.** Clearing (2) before reading the levels (3)
means an edge after the clear sets the status again and fires again after
the ack, so the levels read in (3) are never stale without a new fire
pending. Reading first would let an edge between (3) and (2) be cleared
unseen. Everything that touches the device happens before the ack (4), as
spec §9 requires, and the answers (5) are replies, which come after.

Steps 1 to 4 and the choice in step 5 are written once, generic over a
`GpioPort` trait in `esp32s3io` (`service_interrupt<P: GpioPort>`), whose
methods are `pcpu_int`, `clear_status`, `levels` and `ack`. The driver
implements it with its register blocks and its Interrupt; the hosted tests
with a recording fake that asserts all four calls in order (§8.2). The
emulator can raise no GPIO interrupt, so the hosted test is the case that
holds the order; stages G1 and G2 run it on the part.

### 4.7 When the driver dies

- **Clients:** the kernel's teardown releases the held obligations, so a
  parked call or an attached claim resolves as `PeerClosed` or abandoned
  (spec §2.1). The controller reports and exits; root, which waits on each
  child's death, prints which one died.
- **The line:** the Interrupt dies with the driver, and the kernel masks
  the line.
- **The pads** keep their configuration: the pull-up stays, an output
  stays at its last level.
- **No restart** in this version. A restarted driver would run the same
  start, which writes whole words and is idempotent for the pads.

---

## 5. The PWM driver

### 5.1 The process

`drivers/svc-pwm-esp32s3/`. In order, it takes its records by the tags of
§3.5, checks its configuration (stopping on a bad one before it maps
anything), maps its three windows (LEDC, `outsel`, `pads`), runs the start
of §5.5, gives the outlet to a Waiter and serves. A wait that answers an
error is a stop, as for the GPIO driver (§4.1). It needs no interrupt and
no Timer. LEDC's clock is on before it starts (§3.3).

### 5.2 Configuration: `pwmconfig`

```saw
import esp32s3io.{Servo}

/// The frame every channel shares, in microseconds.
public static FRAME_US: UInt32 = 20000

/// One entry per servo: its LEDC channel, its pad, and the pulse range it
/// may be commanded to, from the bench.
public static SERVOS: [Servo; 2] = [
    Servo(channel: 0, pin: 41, min_us: 500, max_us: 2500),
    Servo(channel: 1, pin: 42, min_us: 500, max_us: 2500),
]
```

**Checked when the driver starts,** before it maps anything
(`esp32s3io.check_pwm_config`), each failure one named line and a non-zero
exit:

- `FRAME_US` from 416 to 200 000, and a multiple of 8 (§5.3);
- each channel 0..7, none twice;
- for each servo, `min_us <= max_us < FRAME_US` (the last keeps the pulse
  arithmetic inside 32 bits, §5.3), then, in counts,
  `pulse_counts(min_us) > 0` and `pulse_counts(max_us) < 16384` (t6). A
  pulse that rounds to 0 counts is no pulse. One that rounds to 16384 is a
  100% duty, which ESP-IDF refuses because it "is not reachable when the
  binded timer selects the maximum duty resolution" and "will mess up the
  duty calculation in hardware" ([IDF-HAL] ledc.c's duty check). At
  20 000 µs the range this allows is 1 to 19 999 µs; at 200 000 µs it is 7
  to 199 993;
- each pin a GPIO the part can drive and not one the chip holds (§4.2's
  list);
- **the pins are one naturally aligned window in both blocks:** consecutive
  from the lowest, a power-of-two count, and the lowest pin's `out_sel` and
  `pad` offsets aligned to the window's length. For two servos that is a
  pair `(n, n + 1)` with n odd (§3.2).

`Set` refuses anything outside `[min_us, max_us]`, so every commanded duty
is inside `(0, 16384)` counts too.

**The pins are stated in four places in the box's image:** `pwmconfig`,
the two parts the image binds (`GPIO.out_sel[41..=42]`,
`IO_MUX.pad[41..=42]`), and the `OUT1_W1TC` and `ENABLE1_W1TS` setup
writes. The driver cannot see a part's length, and the builder cannot read
a Saw module, so nothing checks them against each other in the box. The
test roots import `pwmconfig` and compute their carves and both setup
words from it, so in the cases there is one statement. In the box, a wrong
part writes another pad's registers and a missing enable bit leaves the
pad undriven; stage P1's sampled pads show the second. Open question 5 asks
whether that is enough.

The driver writes pin p's registers at `outsel.base() + 4 (p - lowest)`
and `pads.base() + 4 (p - lowest)`.

### 5.3 Timer arithmetic

The timer counts `2^14` ticks per frame, each `div` cycles of the source:
`f_frame = f_src / (div * 2^14)`. `div` is 10.8 fixed point in `CLK_DIV`,
valid from 1.0 to 1023.996: field values 256 to 0x3FFFF, the range
ESP-IDF's `LEDC_IS_DIV_INVALID` checks.

- **The divider field is `FRAME_US * 5 / 8`** at 40 MHz and 14 bits
  (`40 * FRAME_US * 256 / 16384`). For 20 000 µs that is exactly 12 500,
  so the frame is exactly 20.000 ms; 12500/256 = 48 + 212/256, so the
  divider alternates between 48 and 49 crystal cycles, a 25 ns jitter per
  tick around an exact mean.
- **Its range:** 256 <= field <= 0x3FFFF is 409.6 <= `FRAME_US` <=
  419 428. A frame that is not a multiple of 8 would truncate the field,
  and is refused rather than silently lengthened.
- **The pulse:** `counts = (pulse_us * 16384 + FRAME_US / 2) / FRAME_US`,
  the duty field `counts << 4`. The product must fit 32 bits: with
  `pulse_us < FRAME_US <= 200 000` it is below 3.28e9. A 64-bit divide
  would call `__udivdi3`, which no call0 library ships ([ABI] §1d); 32-bit
  multiply and divide are inline. Hence the upper bound of 200 000 µs
  (5 Hz), far below any servo's need.

At 20 ms:

```
pulse     counts  as output      steps
0.5 ms     410    0.50049 ms
1.0 ms     819    0.99976 ms     1.0 to 2.0 ms: 819 steps of 1.2207 us
1.5 ms    1229    1.50024 ms
2.0 ms    1638    1.99951 ms
2.5 ms    2048    2.50000 ms     0.5 to 2.5 ms: 1638 steps
```

A hobby servo's dead band is a few microseconds; the steps are finer.

### 5.4 The protocol: `drivers/pwmproto/`

```
op          payload                   reply payload
Set    1    channel, pulse_us (u32)   counts (u32)
Stop   2    channel                   -
Inspect 3   channel                   DUTY_R, TIMER0_VALUE, CONF0, the pad's
                                      OUT_SEL and IO MUX words, each read
                                      from the device now
Log    4    from (u8)                 as gpioproto's Log
```

**Statuses:** `Ok`, `BadOp` (an unknown op or a wrong length),
`BadChannel` (not in the configuration, or above 7), `OutOfRange` (outside
the channel's `[min_us, max_us]`).

- **A channel is looked up in one place,** `servo_of(channel)`, which
  answers the configured servo or `BadChannel`, and every op that names a
  channel goes through it before any register address is formed. Channel
  c's `CONF0` is at LEDC + `0x14 c`, so an unchecked channel 8 is
  `0x600190a0`, `TIMER0_CONF`: a `Stop(8)` would write `0x10` over the
  timer both servos run on (t5). `pwm_service` asks `Set`, `Stop` and
  `Inspect` each with channel 2 and channel 8.
- **The request is a pulse width, not an angle.** A pulse width is what the
  hardware makes and is the same for every servo; an angle depends on the
  servo (centre, travel and direction differ between units of one model),
  so angle-to-pulse is calibration data in the controller's motion table
  (SO-78). The issue's "a servo moves to a commanded angle" is met by that
  table naming the pulse for the angle.
- **Limits are refused, not clamped.** A clamped command would move the arm
  somewhere the caller did not ask for, silently. `OutOfRange` writes
  nothing.

### 5.5 What the start, `Set` and `Stop` write

**The start,** in this order (the log of the `pwm_service` case's
configuration, two servos on 41 and 42):

```
0x600190d0 <- 0x00000003    CONF: APB_CLK_SEL 3, XTAL_CLK
0x600190a0 <- 0x00830d4e    TIMER0_CONF: RST held, DUTY_RES 14, CLK_DIV 12500
0x600190a0 <- 0x02830d4e    the same with PARA_UP
0x600190a0 <- 0x00030d4e    RST released: the counter runs
0x60019004 <- 0x00000000    CH0_HPOINT
0x60019000 <- 0x00000010    CH0_CONF0: timer 0, SIG_OUT_EN 0, IDLE_LV 0,
                            PARA_UP
0x60019018 <- 0x00000000    CH1_HPOINT
0x60019014 <- 0x00000010    CH1_CONF0
0x600045f8 <- 0x00000100    FUNC41_OUT_SEL_CFG: the plain GPIO signal
0x600090a8 <- 0x00001a00    IO MUX pad 41: function 1, IE, 20 mA
0x600045f8 <- 0x00000449    FUNC41_OUT_SEL_CFG: signal 73 | OEN_SEL
0x600045fc <- 0x00000100    FUNC42_OUT_SEL_CFG
0x600090ac <- 0x00001a00    IO MUX pad 42
0x600045fc <- 0x0000044a    FUNC42_OUT_SEL_CFG: signal 74 | OEN_SEL
```

- `TIMER0_CONF`'s `RST` resets to 1 ([IDF] ledc_reg.h, `LEDC_LSTIMER0_RST
  ... default: 1'b1`), so the first word keeps it set while the fields are
  loaded, as `ledc_timer_set` then `ledc_timer_rst` leave it.
- Every channel is configured and **off: its output is `IDLE_LV`, low**
  ([SVD] `IDLE_LV`).
- Each pad's three words are §3.3's order: the output select to the plain
  signal, the function, then the channel.
- The pad's IO MUX word carries `FUN_IE` so a board stage can read the
  pad's level through `IN1` (§8.5). An input enable on a driven pad is
  ESP-IDF's `GPIO_MODE_INPUT_OUTPUT`.

**`Set(0, 1500)`:** `DUTY` = `0x00004cd0` (1229 << 4); `CONF1` =
`0xc0100400`; `CONF0` = `0x00000014` (`SIG_OUT_EN | PARA_UP`). The new duty
starts at the next frame boundary; a frame in progress finishes at its old
width, so no update makes a runt pulse.

**`Stop(0)`:** `CONF1` = `0x40100400` (`DUTY_START` clear); `CONF0` =
`0x00000010` (`IDLE_LV` 0, `SIG_OUT_EN` 0, `PARA_UP`). The line goes low at
the frame boundary and the servo goes limp.

The start is written once, generic over a `LedcPort` trait in `esp32s3io`
(`start_pwm<P: LedcPort>`), so the hosted test holds its order with a
recording fake, and the smoke holds the real driver's log (§8).

### 5.6 The servo line, from reset to exit

- **From reset until the driver gives the pad its function:** function 0
  (JTAG) with no pull ([PART]), undriven by anything here, so the line
  floats; the servo sees no valid frame. A 10 kΩ pull-down from each signal
  to ground holds it low (§9).
- **From the function until the channel's select:** low, `OUT1`'s written
  0 (§3.3).
- **From the channel's select until the first `Set`:** low, the idle
  channel's `IDLE_LV`, with its clock selected and its timer running.
- **From the first `Set`:** frames at the commanded width.

So the controller's first act is to command each servo to rest, one at a
time (§9), and the first frame a servo sees is a deliberate one.

**After the driver exits or dies,** nothing touches LEDC: the timer and
channels run on at the last duty, and **the servos hold their last
position**. The kernel's teardown does not touch devices. A driver that
wants the servos limp on a clean exit sends itself the `Stop`s first.

---

## 6. The CPU clock

**What is known now** ([ABI] §14h, on the part): the ROM leaves the CPU on
the crystal divided by two, 20 MHz; the `xtal40` key makes it 40 MHz and
`pll80` 80 MHz. `kill_churn` runs in 26.05 s, 13.44 s and 7.88 s
respectively. The key is chosen per image (`s3clock`).

**What it decides for these drivers:** latency only.

- **The servo signal does not move with the key.** LEDC runs from
  XTAL_CLK, which reaches the peripherals undivided under every key, as
  UART0 and SYSTIMER show (§2.6). Stage P1 measures the frame under `rom`
  and `pll80`.
- **The kernel times with SYSTIMER,** also from the crystal, at 16 MHz
  under every key.
- **Latency:** from the switch's edge, through the GPIO driver, the
  controller and the PWM driver, to the next servo frame. Against a 20 ms
  frame and an arm that travels for hundreds of milliseconds, a few dozen
  system calls do not matter even at 20 MHz, where a whole `kill_churn`
  round (create, give, start, kill, release) takes about 130 ms.
- **Which key the box runs at is SO-78's choice.** The drivers work at
  every key, and the board stages run the PWM at both ends.

**Call0 and windowed.** The drivers are Saw processes, call0 like every
Saw process on the S3 until SO-126. Nothing in them depends on the calling
convention beyond keeping to 32-bit division (§5.3). A windowed build would
change nothing here.

---

## 7. Where the code lives, what is typed, how configuration arrives

### 7.1 Packages

```
drivers/esp32s3io/          the part's numbers and pure functions, shared
                            by both drivers and their test roots: block
                            bases and offsets, pad and signal numbers, the
                            word functions (pad_word, pin_cfg, out_sel,
                            divider, pulse_counts), the config types and
                            their checks, the setup write over a WordPort,
                            the GpioPort and LedcPort traits with
                            service_interrupt and start_pwm; hosted tests
                            in tests/
drivers/gpioproto/          the GPIO wire: ops, statuses, body builders
drivers/pwmproto/           the PWM wire
drivers/svc-gpio-esp32s3/   the GPIO driver, its process.toml
drivers/svc-pwm-esp32s3/    the PWM driver, its process.toml
tests/s3-gpio-service/      the GPIO cases' root: launcher and client
tests/s3-pwm-service/       the PWM cases' root: setup writes, parts,
                            launcher and client
tests/s3-gpio-config/       gpioconfig files, one per case
tests/s3-pwm-config/        pwmconfig files, one per case
```

`esp32s3io` imports nothing but the standard library, so `blade test` runs
its tests on the host. The protocols are separate packages because SO-78's
QEMU case runs stand-in drivers that speak them (SO-78 c2). The setup write
(`apply_setup`: `set` ORs into the word read, `clear` ANDs out, `write`
stores) has nothing of the S3 in it; it sits in `esp32s3io` because the PWM
test root is its first user, and SO-95's stock root needs the same routine
or the same test (§12).

### 7.2 Register blocks as typed views

The tree's idiom, and the saw-lang skill's rule for MMIO, is a
register-block struct that *is* the layout (`ReadOnly<T>` and
`WriteOnly<T>` markers, explicit reserved words, its size pinned by
`static_assert`), held by a safe driver struct through
`UnsafeMemory<Block, Device>` (`EspUart` in `hal/xtensa-esp32s3/kernel/
lib.saw`, `Uart` in `svc-uart-ns16550`). The blocks live in each driver:

- the GPIO driver's GPIO and IO MUX blocks, with `pin: [UInt32; 54]`,
  `out_sel: [UInt32; 54]` and `pad: [UInt32; 49]` as array fields. A probe
  with sawc 0.23.0 (`-Oz`) compiled `self.regs.pin[n].write(v)` through a
  Device view to one `memw` and one `s32i` at the right address; the pin is
  now 0.24.2, so the implementer compiles it again first;
- the PWM driver's LEDC block, and two 2-word views for its parts. An array
  of LEDC's eight 5-word channel structs was not probed; the implementer
  compiles it first, and the fallback is a per-channel view built in one
  method.

`PCPU_INT`, `IN`, `IN1`, `STRAP`, `DUTY_R` and `TIMERt_VALUE` are
`ReadOnly<UInt32>`; every W1TS and W1TC register is `WriteOnly<UInt32>`.

### 7.3 How configuration arrives

SO-95 rule 4.6 makes a driver's own settings a **build-time input**: a
module the driver imports by the input's name, whose file the image names
(`inputs = { gpioconfig = "config/gpio.saw" }`). Two builds of one driver
with two files are two instances (rule 5.11).

The mechanism is not built yet; SO-95's U6 builds it for images. Blade
already takes `--module-path NAME=DIR` on `blade build`, so the convention
is: input NAME with value FILE is compiled as module NAME, a directory
holding FILE as its `lib.saw`, passed to `blade build` as
`--module-path NAME=DIR`. U1 builds that for the runner's cases (§12), and
U6 uses the same convention for images.

A start argument cannot carry this, and a `Setup` message is not needed:
a child's entry gets one boot handle (spec §12), and a compiled table
cannot be changed by a client.

---

## 8. Testing

### 8.1 What the emulator models

This design's probe of the installed emulator
(`esp_develop_9.2.2_20250817`), and `ABI.md` §9:

- the IO MUX, GPIO (but `STRAP`), LEDC and `PERIP_CLK_EN0` read 0 before
  and after every write; nothing faults;
- `IN` always reads 0. The switch wired to ground through a pull-up reads
  0 when closed, so `WaitLevel(21, 0)` answers at once and
  `WaitLevel(21, 1)` can never be answered;
- no GPIO interrupt can be raised: neither a software `STATUS_W1TS` nor
  anything else pends source 16.

### 8.2 Hosted tests (`blade test` in `drivers/esp32s3io/`)

Run by the runner in a full `make sos-test`, as the builder's own tests are
(U1). They print nothing when they pass.

- **Words:** `pad_word` for each mode (`0x1B00`, `0x1A80`, `0x1A00`,
  `0x1800`, `0x1A00`); `pin_cfg` for an input and an output (`0x2180`,
  `0`); `out_sel` for a plain GPIO and an LEDC channel (`0x500`, `0x449`);
  the register address of each array element for pins 0, 21, 31, 32, 47.
- **Arithmetic:** `divider(20000)` = 12500; `pulse_counts` for 500, 1000,
  1500, 2000, 2500 µs at 20 ms (410, 819, 1229, 1638, 2048); the largest
  allowed frame and pulse with no overflow.
- **Configuration checks,** one test per refusal of §4.2 and §5.2, each
  asserting the refusal's name, and one accepted configuration of each.
  The count bounds (t6) have four: at 200 000 µs, `max_us` 199 994 is
  refused (16384 counts) and 199 993 accepted; `min_us` 6 is refused (0
  counts) and 7 accepted.
- **The setup write (t4):** against a fake register reading `0xf9c1e06f`,
  `set 0x800` stores `0xf9c1e86f`; against one reading `0x00000800`,
  `clear 0x800` stores `0x00000000`, and against `0xffffffff` it stores
  `0xfffff7ff`; `write 0x600` stores `0x00000600` whatever the register
  reads. Each test asserts the one store and that the register was read
  first for a set or a clear and not read for a write.
- **The interrupt service's order (t5):** a recording `GpioPort` that
  reports pin 21 fired, then answers the levels read; the test asserts the
  calls were `pcpu_int`, `clear_status` with exactly the fired bits,
  `levels`, `ack`, in that order, and which held waits are chosen. A second
  fake sets a new status bit when `clear_status` runs, as an edge just
  after the clear would; the test asserts the levels were read after the
  clear.
- **The PWM start's order:** a recording `LedcPort`; the test asserts
  `CONF` first, the timer's three words, each channel idle, then for each
  pad the output select to `0x100`, the IO MUX word, the output select to
  the channel, and no pad word before every channel is idle.
- **A plain output's order:** the GPIO start for an `Output` pin, through
  the same recording port, clears the enable first and sets it last.

### 8.3 The smoke cases

S3 cases of the board's own, in `_esp32s3_smoke`'s process cases as
`irq_line_cycle` is, so `make sos-smoke-esp32s3` runs them. Each is root
plus one child, the layout the S3 has today. Each case's header says the
device is absent under this emulator and what the case proves anyway.

**The child runs under its declaration.** A test image keeps the compiled
quota defaults, and an image may only raise them (`plan_limits`), so the
test root sets the child's declared rows itself, with `QuotaSet` after the
create and before any give, as SO-95's launch convention has a launcher do.
The rows reach the root as `childquota`, a module the runner generates from
the child's `process.toml` (U1). Raising and lowering are both legal
there: root's own rows are unlimited, and a `Handle` row of 13 is within
`MAX_HANDLES`.

**`gpio_service`.** Rows: GPIO, IO_MUX. Config `tests/s3-gpio-config/
smoke.saw`: 21 `InputPullUp`, 14 `InputPullDown`, 1 `InputFloating`, 2
`Output` high, 47 `OutputLoopback` low. Root sets the declared rows, binds
16, creates the driver, gives it its records and is its client.

- `Log(0)`, `Log(15)`: the 28 start writes of §4.4, asserted in order.
- `Read(21)` answers 0; `WaitLevel(21, 0)` answers at once with 0.
- Refusals, on every op that names a pin: `Read`, `Write`, `WaitLevel` and
  `Inspect` each with 22 (`BadPin`) and 19 (`NotMine`); `Read(49)`
  `BadPin`; `Write(21, 1)` `BadMode`; `WaitLevel(2, 1)` `BadMode`;
  `Write(2, 2)` `BadArg`; op 0x7F `BadOp`; a `Read` with no pin byte
  `BadOp`. The log's total does not move.
- `Write(2, 0)` and `Write(47, 1)` answer `Ok`; `Log(28)` shows
  `0x6000400c <- 0x00000004` and `0x60004014 <- 0x00008000`.
- `WaitLevel(21, 1)` posted beside a Timer: the Timer's record comes first,
  since no edge can arrive here (the order holds by construction). Root
  drops the claim and calls `Status` until `held` is 0, yielding between
  calls, and prints `abandoned=1`.
- Four `WaitLevel(21, 1)` held; the fifth answers `Busy`. Root drops the
  four and calls `Status` until `held` is 0: `abandoned=5`.
- `WaitLevel(47, 1)` is accepted (a loopback pin may be waited on) and
  dropped the same way.
- `Inspect(21)` answers `Ok`; its words are printed, not asserted.
- Root releases its inlet; the driver's loop ends, it prints its closing
  line (`served=` asserted; `fires=` printed only) and exits 0, and root's
  death wait prints the status.

**`gpio_bad_config`.** The same root with `bad.saw`, which names pin 30,
and the driver's Mapping row set to 0 instead of its declared 2: the driver
prints `config refused: pin 30 is held by the chip (the SPI flash bus)` and
exits non-zero; root prints the death status. A driver that mapped before
it checked would print its map's `QuotaExceeded` instead.

**`pwm_service`.** Rows: GPIO, IO_MUX, LEDC, SYSTEM. Config `tests/s3-pwm-
config/smoke.saw`: 20 000 µs; channel 0 on 41, 500..2500; channel 1 on 42,
1000..2000. Root imports the same `pwmconfig` and sets the declared rows.

- Root's setup writes, each printed as written: `PERIP_CLK_EN0
  0x00000800` (the emulator reads 0; on the part it is `0xf9c1e86f`, which
  §8.2's test holds), `PERIP_RST_EN0 0x00000000`, `OUT1_W1TC 0x00000600`,
  `ENABLE1_W1TS 0x00000600`; the parts it carves, GPIO + 0x5F8 and
  IO_MUX + 0xA8, 8 bytes each.
- `Log(0)`: the 14 start writes of §5.5, asserted in order.
- `Set(0, 1500)` answers counts 1229; the log gains `DUTY` `0x00004cd0`,
  `CONF1` `0xc0100400`, `CONF0` `0x00000014`.
- `Set(1, 1000)` answers 819; the log gains channel 1's three words
  (`0x6001901c <- 0x00003330`, `0x60019020 <- 0xc0100400`,
  `0x60019014 <- 0x00000014`).
- `Set(0, 2600)`, `Set(0, 400)` and `Set(1, 2500)` answer `OutOfRange`,
  and the log's total does not move.
- `Set`, `Stop` and `Inspect` each with channel 2 and channel 8 answer
  `BadChannel`; op 0x7F and a `Set` with no pulse answer `BadOp`. The log's
  total does not move.
- `Stop(0)` answers `Ok`; the log gains `CONF1` `0x40100400` and `CONF0`
  `0x00000010`.
- `Inspect(0)` answers `Ok`, its words printed only.
- Root releases its inlet; the driver exits 0.

**`pwm_bad_config`.** The same root with a frame of 1 000 000 µs and the
driver's Mapping row set to 0: the driver prints `config refused: frame
1000000 us is outside 416..200000` and exits non-zero.

No case asserts a time or a count that accrues with time.

### 8.4 Every check and stop, and the case that goes red

| check or stop | where | red when removed |
|---|---|---|
| each pad, pin and out-select word | `esp32s3io` | hosted word tests |
| `MCU_SEL` 1 in every pad word (c4) | `esp32s3io`, driver | hosted; `gpio_service` and `pwm_service` logs |
| each GPIO start write and its order | driver | `gpio_service` log; hosted (a plain output's order) |
| `INT_TYPE` 3 and `INT_ENA` | `esp32s3io` | hosted; `gpio_service` log |
| each GPIO config refusal | `esp32s3io` | hosted, one test each |
| the check before any map | GPIO driver | `gpio_bad_config` under a Mapping row of 0 |
| `BadOp`, `BadArg`, `BadPin`, `NotMine`, `BadMode`, on every op | GPIO driver | `gpio_service` refusals |
| the `Busy` bound | GPIO driver | `gpio_service` (the fifth wait) |
| answer at once when the level holds | GPIO driver | `gpio_service` (`WaitLevel(21, 0)`) |
| abandonment discards the obligation | GPIO driver | `gpio_service` (`abandoned=`) |
| the interrupt service's order, the ack fourth | `esp32s3io` | hosted, two fakes; G1 and G2 on the part |
| declared `mappings = 2` | `process.toml`, test root | `gpio_service`: at 1 the second map fails at start |
| declared `handles = 13` | `process.toml`, test root | `gpio_service`: at 12 the fifth wait's delivery answers `QuotaExceeded` and the driver stops |
| declared `waiters = 1` | `process.toml`, test root | `gpio_service`: at 0 the driver's Waiter is refused at start |
| root sets the declared rows | test roots | both services: at the compiled defaults (handles 12, mappings 2) the GPIO driver stops at the fifth wait and the PWM driver at its third map |
| the divider and its range | `esp32s3io` | hosted; `pwm_service` (`0x00830d4e`) |
| the pulse rounding | `esp32s3io` | hosted; `pwm_service` (1229, not 1228) |
| each PWM config refusal, the count bounds included | `esp32s3io` | hosted, one test each |
| the check before any map | PWM driver | `pwm_bad_config` under a Mapping row of 0 |
| the start's order: clock, timer, idle channels, then each pad plain, function, channel | `esp32s3io`, driver | hosted; `pwm_service` log |
| the range check, writing nothing | PWM driver | `pwm_service` (`OutOfRange`, log total) |
| `BadChannel` on `Set`, `Stop` and `Inspect` | PWM driver | `pwm_service` (channels 2 and 8, log total) |
| `BadOp` | PWM driver | `pwm_service` |
| `Stop`'s words | PWM driver | `pwm_service` log |
| declared `mappings = 3` | `process.toml`, test root | `pwm_service`: at 2 the third map fails at start |
| declared `handles = 10` | `process.toml`, test root | `pwm_service`: at 9 the first request's delivery answers `QuotaExceeded` and the driver stops |
| declared `waiters = 1` | `process.toml`, test root | `pwm_service`: at 0 the Waiter is refused at start |
| a set or clear reads the word first | `esp32s3io` | hosted, against `0xf9c1e06f` (t4) |
| root's setup words and parts | test root | `pwm_service` (printed words) |
| silicon: every read-back, source 16, the frame, the pads low until `Set` | the part | G1, G2, P1, P2 (§8.5) |

A declared row is a number, not code; "removed" there means set one lower,
which is the mutant the patch note runs for each row.

### 8.5 Board stages

Each stage is a `"board_only"` case (the runner's existing key) built with
`tools/sos_runner.py --board esp32s3 --emit-flash CASE [--clock KEY] --out
PATH`. The lead asks the reviewer, who has the board, to run it; the only
write is `write-flash 0x0 IMAGE`, no erase and no eFuse. Each request
carries the image, its SHA-256 and the expected console text, and each
run's capture is pasted into the issue.

**G1, `gpio_loopback_board`: the interrupt path, no wiring needed.** The
GPIO driver with `board.saw`: 14 `OutputLoopback` low, 21 `InputPullUp`.
An LED with a 330 Ω resistor from 14 to ground is optional. Root toggles
14 ten times, 1 s apart: it posts `WaitLevel(14, new)` (held: the pin is at
the old level), then `Write(14, new)`, and waits on the claim beside a 2 s
Timer. Expected: ten lines `14 -> 1 answered by interrupt` (and `-> 0`),
each with `fires=` printed; a Timer first prints `no interrupt in 2 s` and
fails the stage. Then `Inspect(14)` reads back IO MUX `0x00001a00` (the
part's reset word is `0x00000a00`, so `MCU_SEL` 1 shows the write took),
PIN `0x00002180`, OUT_SEL `0x00000500`; `Inspect(21)` reads `0x00001b00`.
This proves source 16, the status and ack order, and the IO MUX on
silicon, with the board as it sits.

**G2, `gpio_switch_board`: the switch.** The same image; the switch wired
from 21 to ground. Root loops on `WaitLevel(21, opposite of last)` ten
times, printing `switch on` or `switch off` with `fires=` (printed, not
asserted). The owner flips the switch ten times. Expected: ten lines
alternating from the switch's position at boot; with the switch open
before the first flip, `Read(21)` is 1 (which also shows RTC pad 21's
pull-down does not load the pad, §2.3). This meets the issue's "the
switch's edge wakes its driver".

**P1, `pwm_signal_board`: the signal, no servo and no meter.** The PWM
driver with the box's pins; root keeps GPIO's row and maps it. Run twice,
`--clock rom` and `--clock pll80`.

- Root's setup words: `PERIP_CLK_EN0 0xf9c1e86f`, `PERIP_RST_EN0
  0x00000000`, `OUT1_W1TC 0x00000600`, `ENABLE1_W1TS 0x00000600`.
- After the driver's first answer, root samples `IN1` bits 9 and 10 (pads
  41 and 42, whose inputs are enabled) a million times. Expected: `pad 41
  before Set: 0 high of 1000000` and the same for pad 42 (t1's "low until
  commanded", on silicon, for both pads).
- `Set(0, 1500)` and `Set(1, 1000)`; after a 100 ms Timer, root measures
  each pad in turn: it counts 51 rising edges, reading the Monotonic clock
  at the first and the 51st, which bound 50 frames, and the high samples
  among all samples in between. It prints `frame_us=` (the interval over
  50) and `pulse_us=` (the high fraction times the frame). Expected: about
  20000 for both pads under both keys, and about 1500 on 41 and 1000 on 42.
  APB as the source would read 40000 under `rom` and 10000 under `pll80`.
- `Inspect(0)` and `Inspect(1)`: `DUTY_R` `0x00004cd0` and `0x00003330`,
  the pads' OUT_SEL `0x00000449` and `0x0000044a` and IO MUX `0x00001a00`
  (reset `0x00000a00`: the reviewer's c4 check on both JTAG pads),
  `TIMER0_VALUE` printed twice, some ticks apart, and moving.

**P2, `pwm_servo_board`: the servos.** As P1 at the box's key, with a servo
on 41, powered as §9 says, and optionally a second on 42. Root commands
channel 0 to 1000 µs and 2000 µs alternately, five times each, 2 s apart,
then 1500; then channel 1 the same, never both at once. Expected: the
printed commands, each servo moving between two positions and stopping
centred. Stage P2 meets the issue's "a servo moves to a commanded angle",
once the bench gives an angle its pulse.

**Both drivers in one image** need two child slots, which the S3's layout
does not have until SO-95's U4 (or SO-78's own layout). That run belongs
to SO-78.

---

## 9. Power and wiring

- **Supply.** The servos take 5 V from the board's 5 V pin (USB VBUS),
  never the 3.3 V pin. All grounds are common: board, servos, switch. The
  3.3 V signal drives a hobby servo's input directly. Whether the board's
  5 V pin passes through a diode or a fuse, and how much current it
  carries, is not known here: check the schematic or the silkscreen.
- **Bulk capacitor.** 470 to 1000 µF across the servos' 5 V and ground,
  near the servos, plus 100 nF ceramic. A micro servo draws a few hundred
  mA moving and more stalled; USB 2 promises 500 mA, and a sag can reset
  the part (the brownout detector's state is unprobed, [ABI] §11).
- **A 10 kΩ pull-down from each servo signal to ground (recommended).**
  The part leaves 41 and 42 with no pull ([PART]), so without it the line
  floats from reset until the PWM driver routes it, and a servo may twitch
  on noise.
- **One servo at a time.** The controller sequences lid, then arm, with a
  Timer between. The drivers do not enforce this; it is the motion table's
  job.
- **What the drivers do about power.** A servo stays limp until its first
  command (§5.6), so power-up draws nothing from the servos; the
  controller's first act is to command rest, servo by servo. A hardware
  ramp is possible (LEDC's fade, §2.4) and not in this version: Open
  question 2.
- **GPIO 48** is the board's WS2812, an addressable LED that needs a timed
  serial protocol. A heartbeat LED for SO-78 goes on a spare pin with a
  resistor, or stays a console line.

---

## 10. The box against the S3's limits

The box (SO-78; SO-95 §5.3's image): root, the GPIO driver, the PWM
driver, the controller and a heartbeat. A hand-written image gets its
board's `[limits.default]`: on the S3, processes 8 and waiters 8, quotas
handles 28 and mappings 12, every other kind at its floor, the derived
totals following, number by number where the image states none (SO-95
§6.4; SO-125, merged). The stock root then sets each child's declared rows
with `QuotaSet` (§3.5).

```
resource                 the box needs                     S3 default   fits
processes                5                                 8            yes
threads                  5 (one each)                      8            yes
waiters                  5 (root's, one per child)         8            yes
timers                   2 (controller, heartbeat)         8            yes
interrupts               1 (source 16)                     4            yes
pipes                    2 (GPIO, PWM), 16 exchanges each  4            yes
events                   0                                 8            yes
Memory objects           8 (4 blobs, 4 destinations)       16           yes
IoMemory objects         7 (4 rows, 2 parts, 1 transient   8            yes
                         setup carve)
Mappings                 6 (GPIO 2, PWM 3, root 1)         32           yes
attachments              <= 14                             52           yes
boot rows                12                                24           yes
root's handle table      peak 17                           32           yes
GPIO driver handles      13                                quota 28     yes
PWM driver handles       10                                quota 28     yes
controller handles       about 8                           quota 28     yes
PWM driver mappings      3                                 quota 12     yes
kernel DRAM              no limit raised past the default; 10,160 B     yes
                         free with the defaults linked (SO-125's note)
process SRAM             about 200 KiB (SO-95's estimate: 256 KiB      yes, with
                         root 96, drivers 24 each,                     computed
                         controller 32, heartbeat 24)                  regions
flash slots              4 children                        1 today      no
```

- **No limit is missing.** The tightest is IoMemory, 7 of 8; a third part
  or a second concurrent setup carve would meet it.
- **What is missing is layout and launch:**
  - **flash slots and child regions.** The S3 has one child slot and fixed
    96 KiB regions (`hal/xtensa-esp32s3/kernel/esp32s3.ld`, [ABI] §13a);
    four children need SO-95's U4 (generated slots and computed regions),
    or a layout SO-78 writes by hand;
  - **the stock root:** setup writes, parts carved from rows, declared
    quotas set, and build inputs per instance (SO-95's U6), or a
    hand-written root in SO-78 that does the same, as the test roots here
    do.
- **Nothing in the kernel.**

---

## 11. Open questions, each with a recommendation

1. **Servo pins: 41 and 42, or 47 and 14?** Recommended: **41 and 42.**
   Under the ruled reach a servo pair must be one 8-byte part in each block,
   which is a pair `(n, n + 1)` with n odd (§3.2). Of the free pins, 41/42
   and 1/2 qualify (2 may carry an onboard LED). 47/14 would be two parts
   per block, which a declaration cannot state today (two device needs of
   one type is the reserved list form, SO-95 rule 4.8). The JTAG pads cost
   nothing once the driver writes function 1, which stage P1 reads back.
   Using them as GPIO should not affect the built-in USB JTAG, which does
   not use these pads; that is not verified here.
2. **A ramp in the PWM driver?** Recommended: **not in this version.**
   Measure the supply on stage P2 with one servo first, and add a
   hardware-fade `Move(channel, pulse_us, duration_ms)` only if a step
   command sags it.
3. **Per-client pin authority inside the GPIO driver?** Recommended: **not
   now.** The box has one client per driver (two, if the heartbeat drives
   an LED, and then it owns a different pin by convention). Nothing below
   the protocol could enforce more on the Flat tier. A related gap, stated
   so it is not found later: nothing checks that the GPIO and PWM
   configurations name disjoint pins, and a GPIO configuration naming 41
   would retarget a servo's pad.
4. **(new) Pins the board holds, refused by the GPIO driver?** The driver
   refuses the pins every S3 image holds (the flash bus, the console). A
   board also holds others: the octal PSRAM's 33..37 on SO-102's `-r8`
   board, the USB pair 19/20, the WS2812 on 48. Recommended: **a board
   input of the `esp32s3-gpio` type** (`[types.esp32s3-gpio] inputs =
   ["gpioheld"]`, each board's file naming its list), added with SO-102's
   `-r8` board, when 33..37 first matter. Until then the chip's list
   compiled into `esp32s3io` is enough for the box.
5. **(new) The servo pins are stated four times in the box's image**
   (`pwmconfig`, the two parts, the output-level and enable writes), with
   nothing checking them against each other (§5.2). Recommended: **accept
   it for the box.** The driver's check catches a pair that is not one
   window, stage P1's pad samples catch a missing enable, and the image's
   comment names the four. A builder check would need the pins in TOML
   rather than a Saw module, a format change worth making when a second
   product drives servos.

---

## 12. Units of work

No spec or ABI change to the kernel comes first, because none is needed.
SO-125 has merged. The read-only stage 0 is done (SO-109) and has no unit
here. At most two agents run at once; SO-95 places the GPIO driver beside
its U4 and the PWM driver beside its U6.

**U1. Groundwork (Sonnet; small to medium).**

- `hal/xtensa-esp32s3/board.toml`: the four devices of §3.1.
- `sosimage devices BOARD`: prints each device's base, length and line;
  `sosimage declared PROCESS_TOML`: prints a declaration's `[quota]` and
  `[build] inputs`, and with `--saw` prints the quota as a Saw module
  (`public static QUOTA_HANDLES: Int = 13`, one line per kind, -1 for a
  kind not declared). Both reuse the readers that exist.
- The runner:
  - a case's `"devices": ["GPIO", ...]` emits one device row per name,
    after the pool row, in the case's order, from `sosimage devices`; an
    unknown name is refused naming it;
  - a case's `"inputs": {NAME: FILE}` builds each package whose
    `process.toml` lists NAME, and the case's root package, with
    `--module-path NAME=DIR` (§7.3), one build per distinct set of inputs
    in its own `--out-dir`; a child that lists an input the case does not
    give is refused naming it;
  - a case's `"declared_quota": PACKAGE` gives the case's root the module
    `childquota`, generated from that package's `process.toml`, with an
    optional `"quota_override": {KIND: N}` over it (the bad-configuration
    cases' Mapping row of 0). Nothing goes in the image's limits, whose
    quota defaults are raise-only (t3);
  - `blade test` in `drivers/esp32s3io/` in a full run.
- `drivers/esp32s3io/` with its hosted tests (§8.2).
- `hal/xtensa-esp32s3/ABI.md`: §9 gains the facts of §2 with their
  sources; §15c's cells for the pads, `perip_clk_en0`, `perip_rst_en0` and
  `rtc_pad21` from SO-109 c9.
- spec §12: the stitcher sentence ("and the device window if it has one")
  becomes "then each device row the case names".
- `CLAUDE.md`'s repo map: `drivers/` gains the S3 packages.
- Tests: the builder's tests for both new subcommands (a board's devices
  printed; a declaration's quota and inputs printed, and as a module; an
  unknown device refused); the hosted tests; every existing case
  unchanged.
- No board needed.

**U2. The GPIO driver (Sonnet; medium; after U1).** `gpioproto`, the
driver and its `process.toml`, `tests/s3-gpio-service/` (setting the
declared rows from `childquota`), the configs, the `gpio_service` and
`gpio_bad_config` smoke cases, and the G1 and G2 board cases. Its red cases
are §8.4's GPIO rows; the patch note lists the mutants run, one per row.
Needs from the board: G1 (nothing wired), then G2 (the switch on 21).

**U3. The PWM driver (Sonnet; medium; after U1, beside U2).** `pwmproto`,
the driver and its `process.toml`, `tests/s3-pwm-service/` with its four
setup writes (through `apply_setup`), its parts and the declared rows, the
configs, the `pwm_service` and `pwm_bad_config` smoke cases, and the P1 and
P2 board cases. Its red cases are §8.4's PWM rows. Needs from the board: P1
twice (nothing wired), then P2 (one servo, the capacitor, a supply good for
an amp).

**U4. Board stages (the lead and the reviewer).** G1, G2, P1 at `rom` and
`pll80`, P2, in that order, each on the lead's request to the reviewer,
each capture pasted into the issue and each finding recorded in `ABI.md`
beside the emulator's answer. Nothing is flashed otherwise.

**For SO-95's U6 (the stock root), from this design:** the box's
`[[setup]]` has four entries (`SYSTEM.PERIP_CLK_EN0` set `0x800`,
`SYSTEM.PERIP_RST_EN0` clear `0x800`, `GPIO.OUT1_W1TC` write `0x600`,
`GPIO.ENABLE1_W1TS` write `0x600`), where SO-95 §5.3 lists three; the stock
root's set and clear need t4's test against `0xf9c1e06f`, or this design's
`apply_setup`; and the stock root sets each child's declared rows with
`QuotaSet`, since an image's defaults cannot lower a row.

**After SO-77:** both drivers in one image, and the box, are SO-78's, once
SO-95's U4 and U6 (or SO-78's own layout and root) exist (§10).
