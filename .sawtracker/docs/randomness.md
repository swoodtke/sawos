# Design: a source of randomness for SOS

Revision 1, Nov 28. A design only: nothing here is built until the owner and
the reviewer have read it. The owner's ruling (Nov 29) is that SOS provides a
randomness generator; this says which, where, how it is granted, and what
moves to it.

Markers. **[read F:L]** is taken from that file and those lines (paths from
the sawos root at `869ba1e0`, main; `3df1179b:` prefixes a file of SO-184.p1,
which is not on main; `IDF/` is ESP-IDF tag v5.4.1; `TRM` is the ESP32-S3
Technical Reference Manual, version 1.8, by its printed page; `EQEMU/` is
Espressif's QEMU fork, branch `esp-develop`; `ZK` is the RISC-V ISA manual's
`src/unpriv/zk.adoc` on `main`; `$SAWLANG` is the pinned toolchain,
`.toolchains/23b7dfab...`). **[probed]** is something I ran under
`.leadwork/locked.sh`, and §1.5 says how. **UNVERIFIED** is neither.

The ESP-IDF files, the TRM, Espressif's RNG model and the RISC-V chapter were
fetched into the scratchpad's `rng/` (`idf/`, `trm/`, `qemusrc/`,
`riscvspec/`); the probes and their raw output are in `rng/probe/`.

## 0. The decisions, in one place

1. **The generator is the kernel's** (§2): one ChaCha20 generator with fast
   key erasure over a BLAKE2s entropy pool, in a new hosted package
   (`rt/crypto`, `soscrypto`) of which the kernel holds one instance.
2. **The kernel reads the board's source itself where the source is a
   register or an instruction** (no interrupt, no queue, no DMA), as it reads
   the clock: RISC-V Zkr's `seed` CSR on riscv32 `virt` and the flat profile,
   arm64's `RNDRRS` on `virt`, `RNG_DATA_REG` on the ESP32-S3. A source that
   needs a transport (virtio-rng) is a userspace driver that feeds the pool
   through an op, and none is built now (§2.3).
3. **It is seeded before root's first instruction** on every gate board and
   on the S3, so root, the loader and every process can draw from the start.
4. **The gate machines' CPU models change** (§1.2, §1.3): riscv32 and the
   flat profile run the default `rv32` with `zkr=on`, and arm64 moves from
   `cortex-a53`, which has no `FEAT_RNG`, to `neoverse-v1`, which has it.
   Each profile passed whole on its new CPU, unchanged [probed, P9, P10]. The
   arm64 move is still the owner's call (§8, Q3).
5. **On the S3 the HAL turns the entropy sources on** (§1.1): the RNG's
   clock, `RTC_CNTL_DIG_CLK8M_EN` for the whole run, and the SAR ADC for the
   boot's seed only, as ESP-IDF's bootloader does. Without them the TRM says
   the register is pseudo-random, which is the state sawos leaves it in today.
6. **A draw before the pool holds 256 credited bits is refused** with a new
   status, `SosStatus.Unseeded`, writing nothing (§3.5). A board whose source
   fails at boot prints one line and runs unseeded; nothing stops.
7. **The API is two System ops** (§4): `SystemOp.RandomFill(address, len)` on
   `SystemRight.Random`, up to `RANDOM_FILL_MAX` (256) bytes a call, and
   `SystemOp.EntropyAdd(address, len, credit)` on `SystemRight.EntropyAdd`.
8. **`random` joins the System rights every process gets by default**, and
   `entropy-add` is a right only a declaration may name (§4.3, Q1).
9. **No std seam exists** (sawlang's runtime ABI has none); the `sos`
   wrappers are the API, and a seam is requested of the sawlang lead (§4.5).
10. **The network moves to it** (§5): `svc-net` draws a 32-byte secret at
    start; netstack keys ISNs, a draw generator and its ephemeral ports from
    it; the DHCP xid, DNS ids and now DNS source ports come from that
    generator; the `netsecret` build input is dropped.
11. **Tests assert no random value** (§6): the gate shows two boots of one
    image draw differently (the runner compares, nothing is expected), the
    unseeded refusal through a harness kernel that skips the boot seed, and
    the ops' refusals; the crypto is pinned by its RFCs' vectors, hosted.
12. **On the Flat tier the generator's state is readable by every process**,
    the S3's included (§3.7). That is the tier's disclaimer, stated, not
    fixed here.

## 1. The sources, per board

### 1.1 ESP32-S3: `RNG_DATA_REG`

**What the part has.** One read-only register, `RNG_DATA_REG` at
0x6003_507C, "Random number data" [read TRM p.921, §25.5; IDF/components/
soc/esp32s3/include/soc/wdev_reg.h:20]. The TRM calls it a true random number
generator fed by thermal noise and clock mismatch [read TRM p.920, §25.3]:

- **The SAR ADC or the high-speed ADC**, whenever either is enabled, feeds
  bit streams in through an XOR [read TRM p.920]. The high-speed ADC is
  "enabled automatically when the Wi-Fi or Bluetooth modules is enabled"
  [read TRM p.921, §25.4].
- **RC_FAST_CLK (20 MHz)**, "when ... enabled for the digital core", is
  sampled as a further bit seed through metastability; "to ensure maximum
  entropy, it's recommended to always enable an ADC source as well" [read
  TRM p.920]. It is enabled by `RTC_CNTL_DIG_CLK8M_EN` in
  `RTC_CNTL_CLK_CONF_REG` [read TRM p.921].
- **The rates.** With SAR ADC noise, 2 bits per RC_FAST cycle, so read at
  most at 500 kHz; with the high-speed ADC, 2 bits per APB cycle, read at most
  at 5 MHz [read TRM p.921, §25.3].
- **With none of the three enabled, "pseudo-random numbers will be
  returned"** [read TRM p.921, §25.4].

**When it is a true source, by ESP-IDF's stricter reading.** IDF guarantees
true random numbers only while the RF subsystem runs, while the SAR ADC
entropy source is on (`bootloader_random_enable` until
`bootloader_random_disable`), or while IDF's second-stage bootloader runs,
which calls the pair around itself; otherwise "the output of the RNG should be
considered as pseudo-random only" [read IDF/docs/en/api-reference/system/
random.rst:11-21]. It describes the RC_FAST source as always enabled in IDF,
enough to pass Dieharder alone, but not guaranteed [read random.rst:57-60].
IDF's application turns RC_FAST on in `esp_clk_init`
(`rtc_clk_8m_enable(true, ...)`) and the RNG's clock gate at the end of its
peripheral clock setup [read IDF/components/esp_system/port/soc/esp32s3/
clk.c:81-82, 335-336].

**What sawos has today: none of it.** The S3 boots by the ROM's direct boot,
not IDF's bootloader [read hal/xtensa-esp32s3/ABI.md:139-199], so nothing has
called `bootloader_random_enable`, and the radio does not run. Of the three
sources:

- `RTC_CNTL_DIG_CLK8M_EN` is bit 10 of 0x6000_8074 (`DR_REG_RTCCNTL_BASE`
  0x6000_8000 + 0x74) and **resets to 0** [read TRM p.607, Register 10.25;
  IDF/components/soc/esp32s3/register/soc/rtc_cntl_reg.h:1295, 1358-1361;
  reg_base.h:14]. Whether the ROM sets it on the way to a direct boot is
  UNVERIFIED (B1, §6.4).
- The SAR ADC is off, and its analog bus is held off: the HAL's clock
  bring-up leaves `ANA_CONFIG_REG`'s bits holding every analog block's bus but
  the BBPLL's, "the SAR ADC's (bit 18, `I2C_SAR_M`) among them" [read
  hal/xtensa-esp32s3/ABI.md:2484-2486, 2832-2837].
- The RNG's clock gate, `SYSTEM_WIFI_CLK_RNG_EN`, is bit 15 of
  `SYSCON_WIFI_CLK_EN_REG` (0x6002_6014), whose reset value 0xFFFC_E030 has
  it set [read IDF/components/soc/esp32s3/register/soc/syscon_reg.h:145-147,
  192; reg_base.h:40].

So on a board today the register reads, by the TRM, pseudo-random.

**What the HAL does.** At boot, before the seed:

1. Set `SYSTEM_WIFI_CLK_RNG_EN`, as IDF's bootloader does whatever the reset
   value [read IDF/components/bootloader_support/src/
   bootloader_random_esp32s3.c:19].
2. Set `RTC_CNTL_DIG_CLK8M_EN`, and leave it set for the run: RC_FAST becomes
   a continuous source for reseeds [read bootloader_random_esp32s3.c:23].
3. Turn the SAR ADC's digital controller on to read an internal reference,
   IDF's `bootloader_random_enable` [read bootloader_random_esp32s3.c:25-74],
   take the boot's seed, then turn it off with `bootloader_random_disable`'s
   sequence [read bootloader_random_esp32s3.c:78-102]. That is about 30
   register writes and four writes on the analog bus. The four go through the
   ROM's `rom_i2c_writeReg_Mask`, which the HAL already calls for the PLL
   through `sos_windowed_call`, a door that is open only before the loader
   [read hal/xtensa-esp32s3/ABI.md:1578-1597, 2488-2492]; the boot's seed is
   taken before the loader, so it fits. The window first releases
   `I2C_SAR_M`'s hold and restores it after. The kernel touches the ADC only
   before root exists, so no driver can be using it. Whether to do step 3 at
   all is Q4.

Once the radio exists (networking's R-track), the high-speed ADC feeds the
register as well, and nothing here changes: the reseeds simply draw on a
better source.

**The read rate and the credit.** IDF reads one 32-bit word per 1,778 APB
cycles in `esp_random`, about 22 µs at 80 MHz, "the maximum we have tested so
far on S3", XOR-folding every read in the gap [read IDF/components/
esp_hw_support/hw_random.c:27-29, 46-88]; the bootloader reads one byte per
80 × 23 CPU cycles, also about 23 µs [read IDF/components/bootloader_support/
src/bootloader_random.c:38, 41-63]. The design keeps IDF's tested pacing:
`S3_SAMPLE_GAP_NS`, 23 µs between credited reads, measured on SYSTIMER
(`timer_now_ns`), folding every intermediate read in as IDF does. It credits,
by its own choice and well below the TRM's rate:

| state | credit per paced word | the boot's seed |
|---|---|---|
| SAR ADC on (boot only) | 4 bits | 64 words, about 1.5 ms |
| RC_FAST only (after boot) | 1 bit | reseeds only |

Both numbers are margin, not measurement. B2 (§6.4) is what would justify or
lower them.

**What the emulator shows.** Espressif's QEMU maps `misc.esp32s3.rng` at
0x6003_507C [read hal/xtensa-esp32s3/ABI.md:358], and every read answers
`qemu_guest_getrandom_nofail` [read EQEMU/hw/misc/esp32s3_rng.c:21-26]: host
randomness on every read, whatever the clocks and the ADC say. Two reads of
the paused machine gave 0x7AF6_CEDD and 0x03E2_506A [probed, P8]. The SAR
ADC's block (0x6004_0000) and SENS (0x6000_8800) are not among the emulator's
devices, so they read 0 and drop writes [read hal/xtensa-esp32s3/
ABI.md:342-371]; that the ROM's analog-bus routine returns on the emulator for
the SAR's block, as it does for the BBPLL's, is UNVERIFIED, and R2's first S3
smoke boot answers it. **So the emulator shows the plumbing and nothing about
entropy**: not whether the HAL's writes enable anything, not the
pseudo-random state, not the pacing's worth. Only a board shows those (§6.4).

### 1.2 riscv32 `virt` and the flat profile: Zkr's `seed` CSR

**What QEMU offers.** Three host-entropy paths reach a `virt` guest, each fed
by `qemu_guest_getrandom`:

- **The device tree's `/chosen/rng-seed`**, 32 bytes, different on every
  boot [probed, P1: two `dumpdtb` runs gave `rng-seed = <0x52406346 ...>` and
  `<0x2bc2840b ...>`]. With `-bios none`, the reset vector at 0x1000 loads
  `a1` from 0x1020, which held 0x87E0_0000 [probed, P7], inside process RAM
  (`RAM_TOP` 0x8800_0000 [read hal/riscv32/kernel/lib.saw:348]).
- **Zkr's `seed` CSR** (0x015), with `-cpu rv32,zkr=on`. In M-mode it needs
  no `mseccfg` grant [read ZK:3605-3609]; it must be read with `csrrw` [read
  ZK:3584]. Status `ES16` (bits 31:30 = 10) carries 16 bits; `BIST` and
  `WAIT` carry none and are not errors; `DEAD` is unrecoverable [read
  ZK:3574-3581, 3611-3668]. The default `rv32` has no Zkr: the read traps
  [probed, P3: mcause 1, the fetch at `mtvec` 0 after the illegal-instruction
  trap]. With `zkr=on` two boots read 0x8000_953C and 0x8000_4A64, both
  `ES16`, and with `-seed 7` both read 0x8000_3828 [probed, P3].
- **`virtio-rng-device`** on the virtio-mmio bus [probed, P2].

**The choice: Zkr.** It is an instruction, so the source is a CSR read in
`rv32core`, shared by both riscv32 boards that run under QEMU, with no parsing
and no device. It is continuous, so reseeds have a source. Its credit is the
specification's: an ES16 source must yield 128 bits of full entropy from
every 16 ES16 words through a vetted conditioner [read ZK:3710-3716], so
**8 bits per ES16 word**, and the boot's seed is 32 words.

The device tree is the runner-up and is rejected: it is boot-only, so no
reseed; it needs an FDT walker in the kernel; and its 32 bytes lie in RAM that
a pool of root's covers, so the kernel would have to wipe them before root
could read the boot's seed, as Linux does with the property. virtio-rng needs
a transport (§2.3).

**The cost.** `qemu_args` gains `-cpu rv32,zkr=on` for riscv32 and the flat
profile [read tools/sos_runner.py:1275, 1445]. P10 ran both profiles whole on
that CPU and every case passed, 674 on riscv32 (its product phase included)
and 578 on flat [probed, P10]. Without the flag the seed read is an illegal
instruction in M-mode, which the kernel does not catch, so a kernel run on a
CPU without Zkr
stops at boot through `kernel_fault` with mcause 2 [read hal/riscv32/kernel/
boot.S:59-80]. The board's `ABI.md` states the requirement, and the HAL names
it at the read.

**The flat profile** is the same machine and `rv32core`'s source; it differs
only in that a process can read the kernel's memory (§3.7).

### 1.3 arm64 `virt`: `RNDRRS`

**`FEAT_RNG` is not on the suite's CPU.** `ID_AA64ISAR0_EL1` bits 63:60
(`RNDR`) read 0 on `cortex-a53` (0x0000_0000_0001_1120) and on `cortex-a76`,
and 1 on `neoverse-v1` (0x1011_1111_1021_2120) and on `max` [probed, P4]. On
`neoverse-v1`, `mrs x1, RNDR` answered 0x9C18_E4DA_1586_AEF1 and, on a second
boot, 0x6EF1_3124_0BC9_7415, with NZCV clear, which is success [probed, P5].

**The device tree does not reach this kernel.** QEMU places a DTB at the base
of RAM for an ELF kernel only when the ELF leaves room there: with an ELF at
0x4020_0000 the FDT's magic sat at 0x4000_0000, and with one at 0x4000_0000
the kernel's own first word did [probed, P6]. The arm64 kernel is linked at
0x4000_0000 and must end by 0x4020_0000, the top of its one executable block
[read hal/arm64/kernel/virt.ld:112-117, 225-243], and the DTB QEMU loads is
1 MiB (its header's `totalsize`, 0x0010_0000 [probed, P6]). Moving the kernel
up 1 MiB would halve its room.

**The choice: `RNDRRS`, on `-cpu neoverse-v1`.** The HAL checks
`ID_AA64ISAR0_EL1.RNDR` at boot and reads `RNDRRS` (`S3_3_C2_C4_1`, which
reseeds before it answers) rather than `RNDR`; a read with Z set in NZCV is a
failure that credits nothing. The design credits 32 bits of each 64-bit
success, a margin of its own, since the Arm ARM leaves the source's rate to
the implementation (UNVERIFIED: the ARM ARM's text was not read here). On a
CPU without `FEAT_RNG` the check finds no source and the kernel runs unseeded
(§3.5), with no trap.

**`neoverse-v1` is a different CPU under the whole arm64 profile.** It is
named rather than `max`, whose features grow with QEMU. It brings SVE, PAuth,
BTI, `FEAT_PAN` and others the kernel does not enable. **P9 ran the gate's
whole arm64 profile on it, with nothing changed but the CPU: all 586 cases
passed** [probed, P9; `rng/probe/arm64_neoverse.txt`]. The alternative that
keeps `cortex-a53` is a harness-loaded seed (`-device loader` writing 32 fresh
host bytes at an address the HAL reads and wipes), a QEMU-only convention
dressed as a boot loader's. Q3.

### 1.4 The C3

No image builds for the C3 [read CLAUDE.md, Testing: "Nothing builds for it
now"], so nothing is built for it here. Its RNG is the same family, at
`WDEV_RND_REG` 0x6002_60B0 (UNVERIFIED: not read from IDF's C3 headers), and
its HAL would take the S3's shape when the C3 builds again. The C6, which the
owner expects later, reads its RNG a byte at a time against the LP timer
[read IDF/components/esp_hw_support/hw_random.c:30-35, 71-79]; its port would
bring its own HAL source.

### 1.5 The probes

All under `.leadwork/locked.sh`, on Homebrew QEMU 11.1.3 and Espressif's QEMU
9.2.2 (`esp_develop_9.2.2_20250817`). The scripts are `rng/probe/
run_probes.sh` and `run_probes2.sh`; `mkelf.py` writes a one-segment ELF of
hand-assembled words and `hmp.py` drives QEMU's monitor. P9 and P10 ran the
runner (`SAWLANG_ROOT` at the pin, v0.24.6) with a `$PATH` shim in
`rng/shim/` that swaps the CPU and logs each call, so the cases ran on the
swapped CPU (the log shows `-cpu neoverse-v1` on `thread_basics.elf`).

| probe | what it ran | what it showed |
|---|---|---|
| P1 | `-M virt,dumpdtb=` twice per machine | `/chosen/rng-seed`, 32 bytes, new each boot; arm64 adds `kaslr-seed` |
| P2 | `-device help` | `virtio-rng-device` on both |
| P3 | `csrrw a0, seed, x0` (0x0150_1573) in M-mode on `rv32`, then `rv32,zkr=on`, then with `-seed 7` | traps without Zkr; ES16 words, new each boot; `-seed` repeats them |
| P4 | `mrs x0, ID_AA64ISAR0_EL1` on four CPUs | `RNDR` field 0 on a53 and a76, 1 on neoverse-v1 and max |
| P5 | `mrs x1, RNDR` on neoverse-v1, two boots | two values, NZCV clear |
| P6 | an ELF at 0x4000_0000 and one at 0x4020_0000, paused, `xp 0x40000000` | the DTB lands at RAM's base only when the ELF leaves it free |
| P7 | the riscv32 reset vector, paused | `a1` = 0x87E0_0000 |
| P8 | `xp 0x6003507c` twice on `-machine esp32s3`, paused | two different words |
| P9 | the arm64 profile, `cortex-a53` swapped for `neoverse-v1` | 586 of 586 passed |
| P10 | the riscv32 and flat profiles with `-cpu rv32,zkr=on` added | 674 of 674 (riscv32 and its product phase) and 578 of 578 passed |

**What the emulators can show**, then: that the kernel reads its source,
seeds, refuses before it is seeded, and that two boots differ. **What they
cannot**: any source's entropy, since every one of them is the host's
`getrandom`; the S3's enabling writes and its pseudo-random state; the cost
of a draw on a part. QEMU's `-seed N` makes every one of these sources repeat
[probed, P3], which is a way to reproduce a run, and which the two-boot case
must never pass.

## 2. Where the generator lives

### 2.1 Three placements, priced

**(a) Userspace: a driver serving a protocol** (`svc-rng` and an
`rngproto`, as `uartproto` and `netdevproto` are served).

- A connection per client, and on the S3 a connection is about 3.5 KiB of
  kernel SRAM [read .leadwork/networking.md:905-909].
- A call per draw, or per seed of a client-side generator.
- Nothing before the driver runs: root, the loader, and every process
  started before it. Each user needs an ordering with the driver, and the
  stock root a rule to start it first.
- On the S3 the device is one 4-byte register, so a whole process would
  serve one load.

**(b) The kernel, reading its sources.** The generator in `kcore`, and a HAL
seam per board.

- Crypto in the kernel: ChaCha20 and BLAKE2s, a few hundred lines of Saw
  (UNVERIFIED count), in a hosted package the kernel imports.
- Device code in the kernel: an instruction on riscv32 and arm64; on the S3
  a register, two clock bits, and the SAR ADC's boot window, bring-up of the
  kind `clock_init` and the PSRAM's `memory_init` already do at boot [read
  kernel/main.saw:28-43].
- Seeded before root's first instruction; one trap per 256 bytes; no
  connection; no ordering.

**(c) Split: a kernel generator seeded only by a userspace driver** through
an op.

- No device code in the kernel.
- The generator is unseeded until the driver has run, which is (a)'s ordering
  problem moved into every early caller; a caller would also need something
  to wait on for "seeded", a waitable that does not exist.

### 2.2 The recommendation: (b), with (c)'s door

The microkernel's habit is that drivers live in userspace [read
spec.md:4298-4304]. The clock is the precedent for what the kernel reads
itself: a machine-wide fact with no interrupt, no queue and no state the
device keeps for a client, granted as a capability [read spec.md:1836-1868].
The sources of §1 are that kind of thing. So **the rule: the kernel reads a
source that is a register or an instruction, and a source that needs a
transport is a userspace driver that feeds the pool** through
`SystemOp.EntropyAdd` (§4.2). The kernel's generator serves every draw
either way.

What it buys: root and the loader can draw (no user today, but stack
canaries or a randomized layout would be kernel-side), every process can
draw at its first instruction, and a draw costs one trap.

### 2.3 What is not built

- **A virtio-rng driver.** No gate board needs one once §1.2 and §1.3 hold.
  `drivers/virtio` already carries the transport and split queues behind
  `Registers` and `Dma` [read drivers/virtio/src/lib.saw:1-21], so the day a
  board needs one it is a small process feeding `EntropyAdd`.
- **A userspace generator per process** (`arc4random`'s shape). SOS has no
  fork, so a process-local generator seeded once from the kernel would be
  safe, and it would save the trap on a hot path. No user draws often enough
  to need it (§5).
- **A waitable "seeded" event.** With every board seeded at boot, `Unseeded`
  is a failure, not a phase.

## 3. The generator

### 3.1 The construction

Linux's since 5.18, in two parts:

- **The pool** is a BLAKE2s-256 hash state (RFC 7693). Every input is hashed
  in, and the pool counts the bits credited since its last extraction.
- **The generator** is a 32-byte ChaCha20 key (RFC 8439) used with **fast key
  erasure**: each fill computes ChaCha20 blocks under the key with a zero
  nonce and counters 0, 1, ...; the first 32 bytes of block 0 replace the key
  before anything is copied out, and the output is the rest of block 0 and
  the blocks after it. The key changes on every fill, so a counter and nonce
  that restart at zero never repeat a keystream.

**Why these.** Both run on 32-bit words with additions, rotations and XORs:
constant-time without tables, and suited to riscv32 and the S3's LX7, neither
of which has an AES or SHA instruction (the S3's AES and SHA blocks are
devices). An SP 800-90A HMAC_DRBG over SHA-256 is the certified alternative,
at about twice the code and a slower block; Q2 asks whether certification
matters for the box.

### 3.2 State

`soscrypto.Drbg`, one in the kernel's `.bss`:

```
pool       BLAKE2s state: h[8], t[2], f, buffer[64], buffered
credit     bits credited since the last extraction, held at 2 * SEED_BITS
key        [UInt8; 32]
seeded     Bool
reseeded   the Monotonic time of the last extraction, ns
last       the last sample word, for the repetition test
repeats    consecutive equal samples
failed     Bool: the source failed its repetition test this boot
```

About 180 bytes. The constants, in `soscrypto`, with the two the ABI
publishes re-exported by `sosabi`:

| name | value | what |
|---|---|---|
| `SEED_BITS` | 256 | the credit an extraction needs, the first and every later one |
| `RESEED_INTERVAL_NS` | 60 s | the least time between extractions after the first |
| `RANDOM_FILL_MAX` | 256 | bytes per fill: five ChaCha20 blocks with the rekey |
| `ENTROPY_ADD_MAX` | 256 | bytes per add |
| `REPEAT_CUTOFF` | 4 | consecutive equal samples that fail the source |

### 3.3 Seeding

`kcore.random_init()`, called by `kmain` after `hal.memory_init()` (the S3's
pacing reads the clock `clock_init` left) and before `hal.intc_init()` [read
kernel/main.saw:31-54]:

1. `hal.entropy_init()`: the board's source brought up for the boot (the S3's
   three steps of §1.1; nothing on riscv32; the ID check on arm64).
2. Up to `BOOT_SAMPLE_LIMIT` (1,024) times: `hal.entropy_sample()`, mixed in
   with its credit, until `credit >= SEED_BITS`.
3. `hal.entropy_boot_done()`: the S3 turns the SAR ADC off.
4. Extract (§3.4) and set `seeded`; or, if the credit fell short, print one
   line, `SOS: random unseeded: REASON` (no source, the source failed its
   repetition test, or the limit was reached), and go on.

The HAL seam, on every HAL:

```saw
/// One sample of the board's entropy source: up to 64 bits, the bits of
/// entropy the board credits them with, and whether the source has failed
/// for good (Zkr's DEAD).
public struct EntropySample { word: UInt64, credit: UInt8, dead: Bool }

public func entropy_init() unsafe
public func entropy_sample() unsafe -> EntropySample
public func entropy_boot_done() unsafe
```

- riscv32 (`rv32core`): `csrrw` of `seed`; ES16 credits 8, `BIST` and `WAIT`
  credit 0, `DEAD` is dead.
- arm64: `RNDRRS` when `ID_AA64ISAR0_EL1.RNDR` is nonzero, 32 bits credited
  per success; without the feature every sample is dead.
- S3: §1.1's paced, folded read, 4 bits during the boot window and 1 after; a
  read sooner than `S3_SAMPLE_GAP_NS` after the last credits 0.
- C3, and any HAL without a source: dead.

**The repetition test** is SP 800-90B's simplest health test, in `kcore` over
every HAL: a sample word equal to the last credits 0, and `REPEAT_CUTOFF` in a
row fails the source until the next boot. It catches a stuck register or a
dead bus; it cannot tell a pseudo-random register from a true one, which is
why the S3's enabling is checked on the board (§6.4).

**Costs at boot.** riscv32: 32 CSR reads. arm64: 8 `RNDRRS`. S3: 64 paced
words, about 1.5 ms, with interrupts masked as at every step of boot. No
case's transcript changes: a boot that seeds prints nothing.

### 3.4 Extraction and reseeding

An **extraction** finalizes the pool to a 32-byte `seed`, then:

- `key = BLAKE2s(key: seed, message: "crng" || key)`: the old key is kept in,
  so nothing the generator had is lost;
- the pool restarts as BLAKE2s keyed with `BLAKE2s(key: seed, message:
  "pool")`, so what the pool had carries into the next;
- `credit = 0`, `reseeded = now`.

**When.** The first extraction happens as soon as the credit reaches
`SEED_BITS`, at boot or at whatever later call brings it there. Later ones
happen at a fill whose credit is at least `SEED_BITS` and whose time is at
least `RESEED_INTERVAL_NS` after the last. The kernel has no thread of its
own, so **every fill takes one sample** and mixes it, with the clock's reading
credited 0; on a board with a continuous source that is what fills the pool
for the next reseed. So a fill costs one sample read (on the S3 usually
credited, since fills are rarely 23 µs apart) and, at most once a minute, an
extraction.

**What reseeding is for.** One extraction of 256 credited bits is enough for
every draw of a boot; a reseed limits what a compromise of the state reveals
of later draws. Fast key erasure is what keeps a compromise from revealing
earlier ones.

### 3.5 Before the pool is seeded

**A fill answers `SosStatus.Unseeded`, writes nothing, and still mixes its
sample.** The alternatives:

- **Block**: a park with a wake when the credit arrives. The kernel would
  need a wait queue for it and something to wake it; on a board whose source
  died at boot nothing ever would.
- **Answer flagged**: bytes with a flag saying they are weak. A value the
  caller can use without reading the flag is the failure Linux's
  `get_random_bytes` warnings record, and CLAUDE.md's rule is that a
  transient condition rides the error channel.
- **Refuse**: a status, as `WouldBlock` is, which a caller retries. On a
  board with a continuous source the sample each fill mixes brings the
  credit up, so a retry can succeed; on one with no source the caller is told
  plainly.

On every board this design names, the kernel is seeded before root runs, so
`Unseeded` reaches a caller only from a failed or missing source, or from the
harness kernel of §6.2 that skips the boot's seed. Q5 asks whether a board
whose source fails should stop instead.

### 3.6 What a fill returns and promises

`RandomFill(address, len)` writes `len` bytes and answers `Ok` with `len` in
the value register, or answers `Unseeded` and writes nothing. Its bytes:

- come from a generator whose pool was credited at least `SEED_BITS` by the
  board's source (or by `EntropyAdd`) this boot, and are not predictable by
  anyone who does not hold the kernel's state;
- do not repeat across boots, given a source that does not;
- are **backtracking-resistant**: the key that made them is overwritten
  before they are copied out, so a later read of the state does not reveal
  them;
- are **not promised prediction-resistant** between extractions: someone who
  reads the state can compute every fill until the next one.

### 3.7 What the Flat tier does to it

On `Flat` a process can read any address, the kernel's included [read
spec.md:4163-4170]. So on the flat profile and on the S3 any process can read
the key and compute every other process's draws until the next reseed. The
tier disclaims exactly this, and the design does not try to hide the key from
a process that can read the kernel. The radio's Wi-Fi key on the S3 is in the
same position [read .leadwork/networking.md:1327-1328].

## 4. The API and its authority

### 4.1 `SystemOp.RandomFill`

`SystemOp.RandomFill(address, len)`, on `SystemRight.Random` (bit 19, after
`Console` at bit 18 [read kernel/abi/src/rights.saw:172-186]; the next op
number after `ConsoleRelease` [read kernel/abi/src/ops.saw:363-377]). It is
on System because the generator is machine-wide and has one verb, where a
right "is the cheaper answer" than an object [read spec.md:3799-3805].

**Its answers, in the order they are decided:**

1. A word naming nothing is the `BadHandle` fault, a handle without
   `SystemRight.Random` `AccessDenied` (§5.7's dispatch order).
2. `len` above `RANDOM_FILL_MAX` is the `BadArg` fault: the bound is
   published in `sosabi`, so a caller could have checked it.
3. `len` of 0 answers `Ok` and touches nothing.
4. The buffer: `address` not word-aligned, or `len` bytes there not in the
   caller's writable memory, ends the caller through `copy_out_check`, as
   every copy door does [read kernel/core/process.saw:961-968].
5. `Unseeded`, with nothing written (§3.5).
6. `Ok`, `len` in the value register. The bytes are made in a buffer on the
   kernel's stack, copied out, and the buffer zeroed.

A fill of 256 bytes is five ChaCha20 blocks with interrupts masked; on the
S3 that cost is B3's to measure.

### 4.2 `SystemOp.EntropyAdd`

`SystemOp.EntropyAdd(address, len, credit)`, on `SystemRight.EntropyAdd`
(bit 20): hash `len` bytes from the caller into the pool, credited `credit`
bits.

1. `BadHandle`, then `AccessDenied`.
2. `len` above `ENTROPY_ADD_MAX`, or `credit` above `8 * len`, is the `BadArg`
   fault.
3. The buffer, through `copy_in_check` [read kernel/core/process.saw:1053].
4. `Ok`. If the credit brings an unseeded pool to `SEED_BITS`, the
   extraction runs inside the call.

**What the right trusts.** Bytes hashed into the pool cannot remove entropy
already there, but a holder can claim credit it does not have and make an
unseeded kernel seed on nothing. So the right is narrow: root holds it, and a
process holds it only when its declaration names it (§4.3). Its users are a
transport-borne source's driver (§2.3) and the unseeded case's root (§6.2).

### 4.3 Who holds them

- `root_system_rights()` carries both.
- **`random` is in the launch convention's default.** Today a process whose
  record names no System rights gets `debug`, `process-self`, `clock-get` and
  `transfer`, or the last three in an image that names a console [read
  spec.md:6358-6372; tools/sosimage/src/declaration.saw:351-362]; `random`
  joins both lists, and the five are the ceiling an image may name. A draw
  reads nothing another process owns and gives the caller nothing it could
  act on another with, and the generator does not deplete, so one process's
  draws starve no other's.
- **It is still a right**, for two reasons. The doctrine has no ambient
  facility [read spec.md:3799-3801]. And it is the clock's substitution
  point [read spec.md:1862-1866]: a launcher that withholds it can serve a
  child deterministic bytes over a pipe, which is how a simulation that must
  replay exactly gets its randomness (sawlang's Raft simulation design wants
  seeding explicit, with "no implicit entropy read" [read
  $SAWLANG/designs/214-raft-simulation-dogfood.md:108-111]).
- **`entropy-add` is a right a declaration names**, beside `console`, and
  never one in an image's `[system]` or in a default. Unlike `console` it is
  not tied to one process of the image.
- Q1 asks whether `random` should be opt-in instead.

### 4.4 `sos` wrappers

```saw
extension System {
    /// Fills `into` with random bytes: one call per RANDOM_FILL_MAX bytes,
    /// through an aligned buffer of the wrapper's, so `into` may be any
    /// slice. `Unseeded` when the kernel's generator has no seed yet.
    public func random_fill(&self, into: &var [UInt8]) -> Result<Void, SosStatus>
    public func random_u32(&self) -> Result<UInt32, SosStatus>
    public func random_u64(&self) -> Result<UInt64, SosStatus>
    /// Hashes `bytes` into the kernel's pool, credited `credit_bits`.
    public func entropy_add(&self, bytes: &[UInt8],
                            credit_bits: UInt) -> Result<Void, SosStatus>
}
```

`SosStatus.Unseeded` is 15, after `Claimed` [read kernel/abi/src/
status.saw:168-182], described as "the kernel has no random seed yet". The C
surface gains `sos_system_random_fill(handle, ptr, len)` and
`sos_system_entropy_add(handle, ptr, len, credit)` beside §5.7's other typed
C exports. A program written with `main` reaches the wrappers through
`sosmain.context().system()` [read rt/main/src/lib.saw:309-345].

### 4.5 A std seam

There is none. sawlang's runtime ABI (v2) has no randomness seam [read
$SAWLANG/runtime/ABI.md, its sections from line 104 to 1344], and std has no
random module ($SAWLANG/std/, by its file list). So the `sos` wrappers are the
API, and nothing a Saw program writes today expects more.

The seam to request of the sawlang lead, when a portable program needs one:
`__saw_rt_random_fill(buf: i8*, len: word) -> word (0/-tag)`, behind a std
`random.fill`, implemented on a hosted target by `getentropy` and on SOS by
`sosrt` through the System handle the runtime already holds, `Unseeded`
mapping to the tag a "not now, try again" answers with. Nothing here waits on
it.

## 5. The users, moved

**What is there.** The stack has no randomness of its own and takes a key:

- `isn.saw` is RFC 6528's `M + F`, with F a SipHash-2-4 of the four-tuple
  under "the caller's" key [read drivers/netstack/src/isn.saw:1-7, 128-144];
  `Stack.tcp_secret` gives it, and until then no TCP opens
  (`OpenError.NoSecret`) [read drivers/netstack/src/lib.saw:822-836].
- The DHCP client wants its first xid "unpredictable (random, from a source
  the network cannot see)" [read drivers/netstack/src/dhcp.saw:69-73].
- In SO-184.p1, `netserve` draws the xid and each DNS id as
  `SipHash(key, draws)` from 0 [read 3df1179b:drivers/netserve/src/
  server.saw:655-659, 836-837, 982-993], and the key is the `netsecret` build
  input every image names [read 3df1179b:drivers/svc-net/process.toml:53-58];
  the gate's is in the tree [read 3df1179b:tests/images/svcnet/
  secret.saw:1-7].
- Ephemeral ports are searched upward from 49152, in the same order each boot
  [read drivers/netstack/src/udp.saw:29-30, 100-102, 155-170;
  drivers/netstack/src/tcpconn.saw:1555-1565].

So SO-184.p1's review found the xid and every DNS (id, port) pair the same on
two boots [read .sawtracker/issues/SO-184.md:243-252], and the ISNs' F is the
same function on every boot of an image.

**What moves.**

1. **`svc-net` draws a 32-byte secret at its start**, through
   `system.random_fill`; its System record gains `random` [read
   3df1179b:drivers/svc-net/process.toml:9-17]. An `Unseeded` answer ends it
   with a line naming it, since it cannot open TCP or ask DHCP safely; on
   every board here that answer means the source failed.
2. **netstack takes one secret**: `Stack.secret(key: &[UInt8; 32])` replaces
   `tcp_secret`. Its first 16 bytes key the ISNs' F, as today. Its last 16
   key a draw generator, `Stack.draw() -> UInt32`, a SipHash-2-4 of a
   counter, which is the stack's source for everything below. SipHash is a
   PRF, so without the key its outputs are unpredictable, and the key is new
   each boot. `OpenError.NoSecret` stays for a stack never given one.
3. **The DHCP xid** is `stack.draw()` where `netserve` drew it.
4. **DNS ids** are `stack.draw()`'s low 16 bits.
5. **DNS source ports** are drawn too, as RFC 5452 §9.2 asks and the review's
   fix names [read .sawtracker/issues/SO-184.md:252]: a query binds a port
   drawn from the dynamic range, drawing again on `InUse`, a bounded number
   of times.
6. **Ephemeral ports in general** follow RFC 6056: UDP's `bind(0)` starts
   its search at a drawn port (its algorithm 1), and TCP's `ephemeral_port`
   offsets its search by a SipHash of the local address and the remote
   address and port (algorithm 3), so one connection's port says nothing of
   the next's.
7. **`netsecret` is dropped**, with `tests/images/svcnet/secret.saw` and the
   input in every image. No test needs a fixed key: the peer echoes whatever
   xid and ids it is sent, and no case asserts one (§6). A run that must
   repeat for debugging repeats under QEMU's `-seed` (§1.5), end to end.
8. **The docs**: `isn.saw`'s "the stack has no randomness of its own" stays
   true, and `svc-net`'s module doc says where the secret comes from; the
   cross-boot hazard the review asked to state goes away rather than being
   written down.

Every draw of the stack is local and cheap; `svc-net` makes one kernel call
per boot.

**The radio's adapter** (networking's R2b) has a `random` entry, planned as a
direct read of `WDEV_RND_REG` [read .leadwork/networking.md:394]. It becomes
`system.random_fill`, so the radio's libraries draw from the conditioned
generator rather than the raw register, and the WPA2 supplicant's nonces
(networking §6.3) take the same path.

## 6. Tests

**No test asserts a random value's content.** A case may assert a property a
correct generator has with overwhelming probability (two fills of 256 bytes
differ; a fill changes a buffer of a known pattern), and the runner may
compare two boots' values with each other. A printed draw is kept out of
every expected text.

### 6.1 Hosted, in `rt/crypto` (`blade test`, in `HOSTED_TESTS`)

The crypto as functions of fixed inputs. A known-answer test of a
deterministic function on a fixed input is not a random value.

| test | red when |
|---|---|
| ChaCha20's block function on RFC 8439 §2.3.2's key, nonce and counter | any word of the block differs |
| ChaCha20's keystream on §2.4.2's plaintext | the ciphertext differs |
| BLAKE2s-256 of "abc", RFC 7693 Appendix B; keyed BLAKE2s on the reference KAT's key over 0, 1, 63, 64 and 65 bytes | a digest differs (the block boundaries are where a buffering bug shows) |
| a `Drbg` answers `Unseeded` until `SEED_BITS` are credited: refused at 255, served at 256 | it serves early, or refuses at the threshold |
| a fill replaces the key before it writes; two fills differ; one state copied and filled twice gives the same bytes | the key survives a fill, or the output is not a function of the state |
| no extraction before `RESEED_INTERVAL_NS`; one after it with the credit; none after it without | the schedule is off |
| an extraction keeps the old key in: two `Drbg`s with one pool history and different keys stay different | the old key is dropped |
| `REPEAT_CUTOFF` equal samples fail the source, one fewer does not, and a repeat credits 0 | the test is off by one, or a repeat is credited |
| the credit is held at `2 * SEED_BITS` | it overflows or wraps |

**Mutants**, each a one-line change run against `blade test`: a rotation
constant in ChaCha's quarter-round or BLAKE2s's G, a round dropped, the
rekey dropped, the old key left out of an extraction, `>` for `>=` at the
seed threshold, the repetition counter not reset, the cap removed. Each must
turn a row red.

### 6.2 The gate (riscv32, arm64, riscv32-flat) and the S3 smoke

Each a hand-written root; the S3 smoke's shared list takes all three.

- **`random_fill`.** Root fills 256 bytes over a buffer of 0xA5 and prints
  whether it changed and whether a second fill differs, then fills 0 bytes
  and 1 byte. Then children, each started with a System lacking one thing,
  whose deaths root reports: one without `Random` (the `AccessDenied`
  fault), one filling `RANDOM_FILL_MAX + 1` bytes (`BadArg`), one filling at
  an unaligned address and one into its read-only data (the copy door's
  faults). Expected: `changed=1 differs=1`, the zero- and one-byte fills
  `Ok`, the four faults. **Mutants:** the right check dropped (the child's
  fill succeeds); the bound dropped; the copy-out skipped with `Ok`
  (`changed=0`); fast key erasure dropped in the kernel (`differs=0`).
- **`random_boots_differ`.** The runner boots the image twice, each a fresh
  QEMU with no `-seed`; root prints `draw: ` and 32 hex digits of a fill. A
  new case key, `boots = 2` with `differ = "draw: "`, makes the runner take
  the matching line out of each boot's transcript before it compares the
  rest with `expect`, and fail the case unless each boot printed one and the
  two differ. **Mutants:** the HAL's sample returning a constant (the
  repetition test leaves the kernel unseeded, the fill answers `Unseeded`,
  no `draw:` line); `kcore` mixing zeros in with the sample's credit (both
  boots draw the same); the runner's comparison inverted.
- **`random_unseeded`.** A harness kernel entry, `tests/random_unseeded.saw`,
  that is `kmain` without `random_init`, as `tests/threads_fast_tick.saw` is
  a kernel entry with one change [read tests/threads_fast_tick.saw:1-31]. It
  also sets a `kcore` switch that makes every fill's sample (0, 0) rather
  than the HAL's, so that on riscv32 and arm64 the fills bring no credit of
  their own and the counts below are exact by construction. Root fills over
  0xA5 (`Unseeded`, the buffer unchanged), adds 32 bytes crediting 128
  (`Unseeded` still), adds 32 more crediting 128, and fills (`Ok`, changed).
  Then children: one adding without `EntropyAdd` (`AccessDenied`), one
  crediting 257 bits for 32 bytes (`BadArg`). **Mutants:** the threshold at
  128; the refused fill writing its bytes; the credit bound dropped; the
  right check dropped.

On the S3 smoke the three run on the emulator's host-random register, so they
show the op, the seam and the S3 HAL's boot path not hanging (§1.1), and no
more.

### 6.3 The network, once SO-184 lands

- **`net_svc_ids_differ`** (riscv32, arm64, flat): two boots of
  `net_svc_udp`'s image with the peer logging each DHCP xid and each DNS
  (id, port) on a line of its own, compared between boots as
  `random_boots_differ` compares draws, and kept out of `expect_peer`. It is
  the review's probe [read .sawtracker/issues/SO-184.md:243-248] made a
  case, red on today's behaviour.
- **Hosted, in netstack and `netserve`:** the xid and ids come from
  `Stack.draw`; a DNS query's port is drawn and drawn again on `InUse`; UDP's
  `bind(0)` starts at the drawn port; TCP's port offset depends on the remote
  address and port; a stack with no secret refuses TCP as today. Each runs
  with a fixed secret, so the tests assert where a draw goes, not what it
  is. **Mutants:** the port draw dropped (sequential ports); `draw` keyed
  with the ISN half; the retry on `InUse` dropped.

### 6.4 What the board stage checks

Each stage is a board image written with `write-flash 0x0` under the
standing grant, the console captured; nothing writes flash at run time.

- **B1, the boot's seed on a part.** A stock root and a client that prints
  its first draw (32 hex digits), the boot seed's time and repetition count,
  and, reading the registers directly (the S3 is `Flat`),
  `RTC_CNTL_DIG_CLK8M_EN` and the SAR ADC's power bits; the kernel also
  prints `DIG_CLK8M_EN` as it found it before its own write. Reset twice from
  the bridge, and power-cycle once (the owner's hand, or the reviewer's on
  the owner's word). Expected: three different draws; `DIG_CLK8M_EN=1`; the
  SAR powered down after boot; no repeats. The before-write bit answers
  whether the ROM's direct boot leaves RC_FAST on (§1.1).
- **B2, the raw source.** A board-only image that writes 16 KiB of raw
  `RNG_DATA_REG` words, paced and unfolded, in each of four states:
  everything off (`DIG_CLK8M_EN` clear, SAR off), RC_FAST only, SAR on, and
  SAR on unpaced. Two resets each, captured. A host script, recorded and
  never asserted, compares each state's two resets (does the all-off state
  repeat after a reset?) and prints each state's byte histogram's chi-square
  and its compressed size. That is what would show the TRM's pseudo-random
  state on the part, and what §1.1's credits rest on until then.
- **B3, the cost.** CCOUNT around `RandomFill` of 32 and of 256 bytes, around
  the boot's seed, and around an extraction, at the `rom` and `pll240` clock
  keys. Recorded, never asserted: it says how long a fill masks interrupts.

## 7. Units

In order; sizes are rough (S days, M a week or two, L split before briefing).

| unit | what | size |
|---|---|---|
| R0 | `rt/crypto` (`soscrypto`): ChaCha20, BLAKE2s (keyed), `Drbg` (pool, fast key erasure, extraction, schedule, repetition test, credit cap), with the clock and the samples passed in; hosted tests and mutants (§6.1); `HOSTED_TESTS`; CLAUDE.md's repo map | S |
| R1a | the gate's CPUs: `-cpu neoverse-v1` for arm64 and `-cpu rv32,zkr=on` for riscv32 and flat in `qemu_args`, with whatever comment and board facts they need; nothing else; the full gate | S |
| R1b | the kernel: `kcore.random` holding one `Drbg`, `random_init` in `kmain`; the HAL seam on every HAL, Zkr in `rv32core`, `RNDRRS` on arm64, the S3 and C3 answering no source until R2; `SystemOp.RandomFill` and `EntropyAdd`, `SystemRight.Random` and `EntropyAdd`, `SosStatus.Unseeded`, `RANDOM_FILL_MAX` and `ENTROPY_ADD_MAX` in `sosabi`; `root_system_rights()`; the `sos` wrappers and C exports; the runner's `boots` and `differ` keys; `tests/random_unseeded.saw`; §6.2's cases on the gate; the spec text below | M |
| R2 | the S3 HAL's source: the RNG clock gate, `DIG_CLK8M_EN`, the SAR ADC's boot window through the ROM's analog-bus routine, the paced folded read and its credits; the RNG section of `hal/xtensa-esp32s3/ABI.md` with §1.1's facts; §6.2's cases on the S3 smoke; B1's image | M |
| R3 | the builder and root: `random` in the launch convention's defaults (`child_rights`, `console_rights`, the plan's names), `entropy-add` a declaration-only right refused in an image's `[system]`; the builder's tests | S |
| R4 | the network, once SO-184 merges: netstack's `secret`, `draw` and RFC 6056 ports; `netserve` and `svc-net` moved, `netsecret` dropped; the peer's xid and DNS lines; `net_svc_ids_differ`; hosted tests (§6.3) | M |
| B | B1 to B3 on the part, under the owner's standing grant to the reviewer; B1's power cycle on the owner's word | |

R0 and R1a can run in parallel within the concurrency limit; R1b needs both;
R2, R3 and R4 need R1b and can run in parallel, two at a time; R4 also needs
SO-184 on main. The std seam (§4.5) is a request to the sawlang lead, not a
unit here.

**Spec text** (R1b, with R2's and R3's parts):

- §2.8: the two ops and two rights in the System table; a paragraph on the
  generator (what a fill promises, §3.6; `Unseeded`; the Flat disclaimer,
  §3.7) and on `EntropyAdd`'s trust (§4.2).
- §5.7's statuses: `Unseeded` (15), a status because the caller cannot see
  the source.
- §6's list stays true: no driver moves into the kernel. Beside it, the rule
  of §2.2: the kernel reads a source that is a register or an instruction,
  and a source behind a transport is a driver that feeds `EntropyAdd`.
- §5b's table: riscv32 and flat run `zkr=on`, arm64 `neoverse-v1`; the S3's
  row names its RNG and the sources the HAL turns on.
- §12's launch convention: `random` among the defaults, `entropy-add` a
  declaration's right.
- §11: a per-process generator and a virtio-rng driver, not built; the S3's
  credits rest on B2.

## 8. Questions for the owner

1. **Is `random` a default right of every process** (recommended: a draw
   grants nothing and costs no one), or opt-in through a declaration's
   `rights`, as `console` is?
2. **ChaCha20 over BLAKE2s, or a NIST SP 800-90A DRBG** (HMAC_DRBG over
   SHA-256)? The first is recommended: smaller, faster on these cores, and
   Linux's. The second matters only if the box is to be certified against
   FIPS 140-3 or the like.
3. **May the arm64 gate move from `cortex-a53` to `neoverse-v1`** for
   `FEAT_RNG`? The whole arm64 profile passed on it unchanged (P9). The
   alternative keeps the CPU and has the runner hand the kernel a seed
   through QEMU's generic loader, which only an emulator would ever do.
4. **On the S3, enable the SAR ADC for the boot's seed**: ESP-IDF's
   bootloader sequence in the kernel's boot, about 30 register writes and
   four ROM calls on the analog bus? Recommended: it is the only source IDF
   guarantees without the radio. Without it the seed rests on RC_FAST alone,
   which the TRM calls true random and IDF does not guarantee.
5. **A board whose source fails at boot: run unseeded with a line**
   (recommended: only a program that draws is affected, and it is told), or
   stop the boot?
6. **Drop `netsecret`** (recommended), or keep it as a test-only override of
   the drawn secret? No case needs it.
