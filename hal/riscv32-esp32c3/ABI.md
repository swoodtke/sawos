# hal/riscv32-esp32c3 — the board facts (design 20, derived Sep 2 2026)

THE CONTRACT FOR THIS BOARD HAL, and the record of how every number in
it was obtained. `boot.S`, `lib.saw`, `sink.c`, `esp32c3.ld` and the two
user linker scripts beside them are the HAL; this file says why each
address is what it is.

Every number below was read off the running machine, not off a
datasheet. Where the emulator and the ESP32-C3 TRM disagree, the
disagreement is called out, because what the smoke target must match is
the QEMU model and what real silicon owes is the TRM.

**THREE THINGS IN THIS FILE ARE EMULATOR GAPS RATHER THAN BOARD FACTS**,
and each says so where it sits: the UART's transmit-FIFO count (§5), the
interrupt matrix's `mip` behaviour (§7a), and the CPU's advertised ISA
(§3a). A hardware bring-up should re-read those three sections first.

Oracle: `~/.espressif/tools/qemu-riscv32/esp_develop_9.2.2_20250817/
qemu/bin/qemu-system-riscv32 -machine esp32c3`
(`QEMU emulator version 9.2.2 (esp_develop_9.2.2_20250817)`).
Bundled mask ROM: `ESP-ROM:esp32c3-api1-20210207`, `Build:Feb  7 2021`.

---

## 1. Memory map (monitor `info mtree`, machine `esp32c3`)

```
0x2000_0000-0x201D_4BFF  esp-rgb-vram                 NOT SILICON: QEMU's
0x2100_0000-0x2100_001B  display.esp.rgb              display extension
0x3C00_0000-0x3C7F_FFFF  cpu0-dcache      romd   8M   flash DBUS window
0x3FC8_0000-0x3FCD_FFFF  esp32c3.dram     ram  384K   alias of iram+0x4000
0x3FF0_0000-0x3FF1_FFFF  esp32c3.drom     rom  128K   alias of irom+0x40000
0x4000_0000-0x4005_FFFF  esp32c3.irom     rom  384K   mask ROM
0x4037_C000-0x403D_FFFF  esp32c3.iram     ram  400K   THE ONLY RAM
0x4200_0000-0x427F_FFFF  cpu0-icache      romd   8M   flash IBUS window
                                                      (alias of cpu0-dcache)
0x5000_0000-0x5000_1FFF  esp32c3.rtcram   ram    8K
0x6000_0000-0x600D_0FFF  esp32c3.iomem    i/o         peripheral window
```

Peripherals inside `esp32c3.iomem`:

```
0x6000_0000-0x6000_007B  esp_soc.uart                 UART0 (the console)
0x6000_2000-0x6000_23FF  ssi.esp32c3.spi
0x6000_4000-0x6000_4FFF  esp32.gpio
0x6000_8000-0x6000_81FF  misc.esp32c3.rtc_cntl
0x6000_8800-0x6000_8A03  nvram.esp.efuse
0x6001_0000-0x6001_007B  esp_soc.uart                 UART1
0x6001_F000-0x6001_F0FF  timer.esp.timg               TIMG0
0x6002_0000-0x6002_00FF  timer.esp.timg               TIMG1
0x6002_3000-0x6002_30FF  esp.systimer                 SYSTIMER
0x6002_B000-0x6002_B1FF  esp32.twai
0x6003_A000-0x6003_A0BB  misc.esp.aes
0x6003_B000-0x6003_B0FF  misc.esp.sha
0x6003_C000-0x6003_C833  misc.esp.rsa
0x6003_D000-0x6003_DE23  misc.esp.ds
0x6003_E000-0x6003_E0FE  misc.esp.hmac
0x6003_F000-0x6003_F283  esp32c3.gdma
0x6004_3000-0x6004_3083  misc.esp32c3.usb_serial_jtag
0x600C_0000-0x600C_009B  esp32c3.soc.clk
0x600C_2000-0x600C_27FF  misc.esp32c3.intmatrix
0x600C_4000-0x600C_51FF  esp32c3.cache
0x600C_C000-0x600C_C05F  misc.esp32c3.xts_aes
0x600C_D000-0x600C_D053  open_eth.regs
0x600C_D400-0x600C_D7FF  open_eth.desc
```

### 1a. The SRAM alias, verified rather than assumed

`info mtree` reports `esp32c3.dram` as `alias esp32c3.iram
0x0000_4000-0x0006_3FFF`, so the arithmetic is DRAM + X == IRAM +
0x4000 + X. PROBED: a store of `0xa5a5a5a5` to IRAM `0x4038_0000` reads
back at DRAM `0x3FC8_0000`. Stores at both ends of the IRAM window
(`0x4037_C000` and `0x403D_FFFC`) read back their own values, so the
whole 400 KiB is plain read/write memory reachable through the IBUS
window — there is no I/D split to design around on this part.

```
IRAM 0x4037_C000 .. 0x4037_FFFF   16K   NO DRAM alias
IRAM 0x4038_0000 .. 0x403D_FFFF  384K   == DRAM 0x3FC8_0000 .. 0x3FCD_FFFF
```

The alias is NOT an isolation hole: RISC-V PMP is default-deny for
U-mode, so a process reaches only what it was granted, and a grant
written against IRAM addresses leaves the DRAM addresses of the same
bytes unreachable. A HAL must nonetheless never grant BOTH windows for
one region, or a single `unmap` would revoke only one of two doors.

### 1b. Total RAM is 400 KiB and there is no more

`esp32c3.iram` (409,600 B) plus `esp32c3.rtcram` (8,192 B) is the whole
writable map. The `esp-rgb-vram` region at 0x2000_0000 is QEMU's
display extension, not ESP32-C3 silicon, and must not be counted.

---

## 2. Boot protocol: DIRECT BOOT (verified empirically)

The ROM's direct-boot check was read out of the bundled mask ROM and
then confirmed by booting. Disassembly of the ROM at the `lui a5,
0xaedb0` site:

```
lw   a4, 0(a0)
lui  a5, 0xaedb0
addi a5, a5, 0x41d          ; a5 = 0xAEDB041D
bne  a4, a5, <not direct boot>
lw   a0, 4(a0)
lui  a5, 0x51250
addi a5, a5, -0x41d         ; a5 = 0x5124FBE3
add  a0, a0, a5
seqz a0, a0                 ; word1 + 0x5124FBE3 == 0  <=>  word1 == 0xAEDB041D
ret
```

and, once both words match:

```
lui  a4, 0x600c4 ; lw a5, 4(a4) ; andi a5, a5, -2 ; sw a5, 4(a4)
lui  a1, 0x42000 ; ... ; jal <cache_ibus_mmu_set>    ; flash -> 0x4200_0000
lui  a1, 0x3c000 ; ... ; jal <cache_dbus_mmu_set>    ; flash -> 0x3C00_0000
lui  a5, 0x42000 ; addi a5, a5, 8 ; jalr a5          ; CALL 0x4200_0008
lui  a0, 0x3ff1c ; addi a0, a0, -0xd8 ; jal <printf> ; "Direct boot returned"
```

**THE CONTRACT:**

- Flash words at offset `0x00` and `0x04` must BOTH be `0xAEDB041D`.
- The ROM maps flash offset 0 at `0x4200_0000` (IBUS) and `0x3C00_0000`
  (DBUS) before jumping, so the image is XIP-readable from word one.
- The ROM enters with `jalr`, i.e. it CALLS **`0x4200_0008`** — flash
  offset 8 — with `ra` pointing at a ROM path that prints
  `Direct boot returned`. An image that returns is therefore diagnosed
  rather than silently hanging.
- Entry is M-mode. UART0 is already configured by the ROM (see §5).
- No esptool image format is involved. No header, no checksum, no
  segment table — the two magic words are the entire protocol.

Attaching the flash: `-drive file=<image>,if=mtd,format=raw`. Without
it QEMU prints `Not initializing SPI Flash` on stderr and the machine
boots to the ROM's download-mode path. QEMU also prints
`Adding SPI flash device` on stderr when a drive IS attached — stderr
noise the harness must filter out of a diffable transcript.

The ROM's own banner is fixed text and is therefore transcript-stable:

```
ESP-ROM:esp32c3-api1-20210207
Build:Feb  7 2021
rst:0x1 (POWERON),boot:0x8 (SPI_FAST_FLASH_BOOT)
```

### 2a. The copy-to-SRAM stub — NOT what the HAL uses, kept for the record

The brief's ORIGINAL ruled boot mode, superseded by the XIP ruling in §8
because the kernel does not fit in SRAM. It is recorded because the
mechanism itself is sound and was verified: this stub was built, booted,
and used to run every probe program behind §4-§7 from `0x4038_0000`.
What the HAL ships instead is simpler — the kernel's own `.text` sits at
the ROM's call target and `boot.S` copies only `.data` — so there is no
stub in the build at all. Keep this if a future image ever wants to
relocate itself wholesale.

Flash layout:

```
0x000  0xAEDB041D                 direct-boot magic word 1
0x004  0xAEDB041D                 direct-boot magic word 2
0x008  the stub (50 bytes)        ROM's jalr target
0x040  header: dest, byte_len, entry, 0
0x100  payload (the SRAM image)
```

```asm
_stub:
    li   a0, 0x42000040          /* header */
    lw   a1, 0(a0)               /* dest   */
    lw   a2, 4(a0)               /* len    */
    lw   a3, 8(a0)               /* entry  */
    li   a4, 0x42000100          /* src    */
    add  a5, a1, a2
1:  beq  a1, a5, 2f
    lw   t0, 0(a4)
    sw   t0, 0(a1)
    addi a4, a4, 4
    addi a1, a1, 4
    j    1b
2:  fence.i
    jr   a3
```

`fence.i` is required and available: the C3's multilib set names
`zifencei` (§3).

---

## 3. The no-A build spelling (the brief's FIRST PROBE)

The ESP32-C3 is **RV32IMC**: no A extension, so atomics are
`rt/common_c/support.c` libcalls with interrupts off, and `SpinLock` is
language-refused. Single core makes that sound.

**sawc** — the triple names an architecture, `--target-features` names
the extensions, so the C3 spelling is the virt profile's list MINUS
`+a`:

```
--target riscv32-unknown-none-elf --target-features +m,+c
```

(virt riscv32 uses `+m,+a,+c`; `tools/sos_runner.py`'s `ARCHES` riscv32
entry is the comparison point.)

**clang / ld.lld** — esp-clang, whose DEFAULT triple is already
riscv32:

```
/Users/swoodtke/.espressif/tools/esp-clang/esp-20.1.1_20250829/esp-clang/bin/clang
    --target=riscv32-esp-unknown-elf
    -march=rv32imc_zicsr_zifencei
    -mabi=ilp32
```

`Espressif clang version 20.1.1 (esp-20.1.1_20250829)`,
`Target: riscv32-esp-unknown-elf`. The `_zifencei` is not optional
decoration: `-march=rv32imc_zicsr` assembles but warns
`clang: warning: no multilib found matching flags [-Wmissing-multilib]`
and then lists the bundled set, which is the authority —

```
riscv32-esp-unknown-elf -march=rv32i_zicsr_zifencei         -mabi=ilp32
riscv32-esp-unknown-elf -march=rv32imc_zicsr_zifencei       -mabi=ilp32
riscv32-esp-unknown-elf -march=rv32imac_zicsr_zifencei      -mabi=ilp32
riscv32-esp-unknown-elf -march=rv32imafc_zicsr_zifencei_... -mabi=ilp32f
```

— so `rv32imc_zicsr_zifencei` is the exact C3 row. `ld.lld`,
`llvm-objcopy` and the rest live in the same `bin/`.

**blade** — and this is the half that is easiest to miss, because it is
in a MANIFEST rather than on a command line. Blade's built-in default
for any `riscv32*` triple is the virt/ESP32-P4 Profile A baseline
(`march = rv32imac_zicsr`, `target-features = +m,+a,+c`), so a package
that says nothing gets the A extension. Every C3 userspace package
restates all three keys in its `[sos.<triple>]` section:

```toml
[sos.riscv32-unknown-none-elf]
linker-script = "../../hal/riscv32-esp32c3/user/root.ld"
native = "../../hal/riscv32-esp32c3/user/syscall.c ../../rt/common_c/support.c"
march = "rv32imc_zicsr_zifencei"
mabi = "ilp32"
target-features = "+m,+c"
```

Without them the image links `amo*`/`lr`/`sc` instructions the part
cannot execute. This was caught by reading blade's own build line, not
by anything failing — see §3a for why nothing failed.

### 3a. THE EMULATOR WILL NOT CATCH A STRAY `+a`

PROBED: this QEMU's esp32c3 CPU reports

```
misa = 0x401411AD   ->  RV32, extensions A C D F H I M S U
```

That is a generic QEMU RV32 core, not a faithful C3: it advertises the
**A** extension, hardware float (**F**/**D**), the hypervisor extension
(**H**) and **S-mode**, none of which exist on ESP32-C3 silicon (M and U
only, RV32IMC). Consequence, and it is the important half of this
section: an accidental `+a` build, an FPU spill, or an S-mode
dependency RUNS GREEN ON THIS EMULATOR and fails on the part. The no-A
discipline is a BUILD invariant enforced by the flags above, never
something the smoke target can be relied on to catch.

---

## 4. PMP (verified: 16 entries, design 19 tier 2's floor met exactly)

- `pmpaddr0`..`pmpaddr15` all implemented: each accepted `0x3FFFFFFF`
  and read it back, with zero illegal-instruction traps. Implemented
  mask `0x0000FFFF`, count **16**.
- `pmpcfg0`..`pmpcfg3` all writable: wrote `0x0F0F0F0F` to each, read
  all four back verbatim, zero traps.
- 16 entries is design 19 tier 2's ruled floor met EXACTLY — the same
  budget `hal/riscv32` runs on virt, so the TOR-pair arithmetic there
  (`PMP_REGIONS = 8` logical regions from 16 entries) transfers
  unchanged. It leaves no headroom: the C3 is at the floor, not above
  it like the P4's 32.

### 4a. U-mode isolation works end to end

The `isolation` smoke case's MECHANISM is fully supported here.
Probed: one TOR pair granting R/W/X over `[0x403C_0000, 0x403C_1000)`
(`pmpaddr0 = base>>2`, `pmpaddr1 = top>>2`, `pmpcfg0 = 0x0F00`), then
`mstatus.MPP` cleared to U and `mret` into the granted window.

```
granted window, ecall from U-mode        -> mcause = 0x00000008
U-mode load of 0x4038_0000 (not granted) -> mcause = 0x00000005  (load access fault)
                                            mtval  = 0x40380000  (the denied address)
                                            mepc   = the faulting instruction
```

So default-deny, the TOR encoding, the fault cause, `mtval` and `mepc`
all behave exactly as `hal/riscv32`'s PMP code already assumes. Nothing
in the protection path needs rethinking for this board.

---

## 5. UART0 console (`esp_soc.uart` @ 0x6000_0000) — NOT 16550

Register offsets confirmed in use:

```
+0x00  UART_FIFO        write a byte to transmit; reading it when empty
                        logs "esp_uart: read UART FIFO while it is empty"
+0x04  UART_INT_RAW     modelled (observed 0x00004002 = TXFIFO_EMPTY | TX_DONE)
+0x08  UART_INT_ST
+0x0C  UART_INT_ENA
+0x10  UART_INT_CLR
+0x14  UART_CLKDIV      0x000002B6 at reset; 0x0030015B after ROM init
+0x1C  UART_STATUS
```

`UART_CLKDIV` after ROM init decodes as integer divisor 347 with
fractional 3 — 40 MHz / 115200 — so **the ROM leaves UART0 configured
and a console sink needs no baud setup**, only the FIFO write.

**The status poll is a no-op under this emulator, and that is a fact to
record rather than to hide.** PROBED: 200 bytes written back-to-back to
`UART_FIFO`, then `UART_STATUS` read immediately — it reads
`0x00000000`, and all 200 bytes arrived. QEMU's `esp_soc.uart` hands
each byte to the chardev synchronously and never reports a TX FIFO
count, so a `TXFIFO_CNT` poll never spins here. It is still the right
thing to write (silicon's 128-byte FIFO does fill), but the smoke
target cannot exercise it, and a poll written against the wrong bit
field would pass on QEMU and hang on hardware.

---

## 6. SYSTIMER (`esp.systimer` @ 0x6002_3000) — the counter works

```
+0x0000  CONF            reset value 0x46000000
                         bit31 CLK_EN, bit30 UNIT0_WORK_EN,
                         bit24 TARGET0_WORK_EN
+0x0004  UNIT0_OP        write bit30 UPDATE; poll bit29 VALUE_VALID
+0x001C  TARGET0_HI
+0x0020  TARGET0_LO
+0x0034  TARGET0_CONF    0 = unit0, one-shot
+0x0040  UNIT0_VALUE_HI
+0x0044  UNIT0_VALUE_LO
+0x0050  COMP0_LOAD      write 1 to load the comparator
+0x0064  INT_ENA
+0x0068  INT_RAW
+0x006C  INT_CLR
+0x0070  INT_ST
+0x00FC  DATE            reads 0x00000000 (not modelled)
```

Verified: with `CLK_EN|UNIT0_WORK_EN` set, the UPDATE/VALUE_VALID
handshake works and `UNIT0_VALUE_LO` advances. Arming TARGET0 one-shot
(`TARGET0_CONF=0`, `TARGET0_LO = now + delta`, `COMP0_LOAD=1`,
`INT_ENA` bit 0, `CONF |= TARGET0_WORK_EN`) sets `INT_RAW` bit 0 AND
`INT_ST` bit 0; `INT_CLR` bit 0 clears `INT_ST`. The alarm, the enable
mask and the clear are all real.

TIMG0 (`0x6001_F000`) was probed as a cross-check and is equally
functional: `T0CONFIG` readback `0xC0020000` (EN | INCREASE |
DIVIDER=2, with `ALARM_EN` self-cleared by the alarm, which is correct
hardware behaviour), `T0LO` advancing, `INT_RAW`/`INT_ST` bit 0 set on
alarm.

---
## 7. Interrupt matrix (`misc.esp32c3.intmatrix` @ 0x600C_2000)

The register layout is the C3's:

```
+0x000 + 4*n   source map register for source n   (writable; 0 = routed nowhere)
+0x104         CPU_INT_ENABLE        writable, one bit per CPU interrupt
+0x108         CPU_INT_TYPE          writable (0 = level, 1 = edge)
+0x10C         CPU_INT_CLEAR         write-1-to-clear an edge latch
+0x110         CPU_INT_EIP_STATUS    read-only, and ALWAYS 0 here (see below)
+0x114 + 4*n   CPU_INT_PRI_n         n = 1..30 writable
+0x194         CPU_INT_THRESH        writable, reset value 1
```

The source numbering is confirmed by the ROM's own footprint: the mask
ROM leaves `+0x054` reading `5`, i.e. source index 21 mapped to CPU
interrupt 5 — and 21 is UART0 in Espressif's source list. So both the
4-byte stride and the ESP-IDF source indices are right.

**SYSTIMER TARGET0 IS SOURCE 37**, established by taking the interrupt:
mapping only that source and arming delivered, mapping only source 6
(the other candidate the first sweep left open) did not.

### 7a. The `mie` bit that gates it is 11, NOT the one `mcause` reports

THIS IS THE FINDING THAT COST THIS UNIT A PARK, so it is stated with its
evidence. The matrix drives the core's MACHINE EXTERNAL interrupt, and
`mie.MEIE` (bit 11) is the only architectural gate; per-line masking
happens in `CPU_INT_ENABLE`, not in `mie`. But `mcause` on entry reports
the matrix's own CPU INTERRUPT NUMBER, not 11. Probed, one variable at a
time, source 37 routed to CPU interrupt 7 throughout:

```
mie = (1<<7)            (the number mcause reports)  -> NOT delivered
mie = (1<<7) | (1<<3)                                -> NOT delivered
mie = (1<<7) | (1<<11)                               -> delivered, mcause 0x80000007
mie = (1<<11)           (MEIE alone)                 -> delivered
mie = 0xFFFF0080        (bit 7 + everything above 15) -> NOT delivered
mie = 0xFFFFFFFF                                     -> delivered
```

and with the recipe fixed at `mie = (1<<11)|(1<<7)`, five successive
one-shot arms delivered five interrupts, `mcause = 0x80000007` each time.

`mie` itself has bits 0, 4 and 8 hardwired to zero (an all-ones write
reads back `0xFFFFFEEE`).

CONSEQUENCE FOR THE HAL: `TIMER_CPU_INT` is chosen as **7** precisely so
that a C3 tick arrives with the same `mcause` a standard machine-timer
interrupt has, which is what lets the arch-generic cause decoding above
the HAL stay arch-generic. The first sweeps of this unit failed because
they set `mie` to the line bit alone and concluded the matrix was not
wired at all — it is; the probe was wrong.

### 7b. `wfi` NEVER WAKES on a matrix interrupt — an EMULATOR GAP

The matrix drives the core's request line directly and **never raises
`mip`**. Three independent observations:

- `mip` reads `0x00000000` in the same breath as an interrupt being
  taken with `mcause = 0x8000_0007`;
- `CPU_INT_EIP_STATUS` reads `0x00000000` while an interrupt is being
  delivered;
- a `wfi` with the entire delivery path up sleeps FOREVER — with the CPU
  interrupt configured LEVEL and configured EDGE alike.

That matters because SOS's idle path is
`while nothing runnable { wait_for_irq(); irq_poll() }` with
`mstatus.MIE` never set (design 178 D2 — interrupts are taken from USER
mode only), so the wake it depends on is `wfi` returning on a PENDING
interrupt. On virt the CLINT and PLIC raise `mip`, which is exactly
`wfi`'s wake condition (`mip & mie`); here nothing does.

THE HAL ABSORBS IT: `wait_for_irq()` is EMPTY on this board and the idle
loop spins, while `irq_poll()` reads the SYSTIMER's own latch — which
does work. It costs a core burned while idle, invisible under emulation
and this target is emulator-only by ruling. Nothing above the HAL
changes: D2 is untouched, the kernel still never takes a trap in kernel
mode. `sink.c`'s `sos_wait_for_irq` is left in place, unused, because on
REAL SILICON `wfi` does wake from the interrupt matrix (the C3 TRM's
low-power section is explicit that any enabled interrupt resumes the
core) and a hardware bring-up should restore the call.

### 7c. The recipe, as the HAL programs it

At boot (`intc_init`), BEFORE any alarm is armed — SYSTIMER TARGET0 is an
EDGE source, so an alarm armed with the path down is a lost pulse that no
later remapping recovers:

```
CPU_INT_ENABLE  = 0                       (and every map register cleared)
CPU_INT_THRESH  = 1
CPU_INT_PRI_7   = 7                       (strictly above the threshold)
map[37]         = 7                       (SYSTIMER TARGET0 -> CPU int 7)
CPU_INT_ENABLE |= 1<<7
mie            |= 1<<11                   (MEIE — see §7a)
```

On service (`irq_complete(IRQ_TIMER)`): clear SYSTIMER `INT_CLR` bit 0,
then pulse `CPU_INT_CLEAR` bit 7. Both are needed — the alarm latches in
SYSTIMER's status and the matrix holds the CPU interrupt until its own
latch is pulsed. The virt profile's comparator needs neither: writing a
future deadline lowers its level.

REVERSE LOOKUP: there is no claim register and `EIP_STATUS` is unusable,
so `irq_claim` maps a CPU interrupt number back to a source by SCANNING
the map registers the kernel itself wrote. 63 device reads per external
interrupt; slow, stateless, and impossible to get out of step with the
hardware.

---

## 8. The SRAM budget, and why the layout is XIP

The kernel's loadable image is ~392 KiB (`.text` 360,624 + `.rodata`
27,004 + `.data` 14,224) and this part has 400 KiB of SRAM in total, so
the brief's original copy-to-SRAM boot mode cannot hold it — `.text`
alone is 352 KiB. USER-RULED Sep 2: **XIP text placement.** `.text` and
`.rodata` execute and are read in place in the flash IBUS window at
0x4200_0000; only `.data` is copied to SRAM and `.bss` zeroed there.
`.payload`, `.regions` and `.childimg` stay in flash too — the kernel
copies out of them, so nothing is granted where it sits.

The whole 400 KiB, as `esp32c3.ld` and the two user scripts divide it:

```
0x4037_C000  kernel .data + .bss (64 KiB stack inside .bss)  168 KiB
0x403A_6000  ROOT REGION      (108K image + 16K stack)       124 KiB
0x403C_5000  CHILD REGION     ( 76K image + 16K stack)        92 KiB
0x403D_C000  RAM POOL                                         16 KiB
0x403E_0000  end of SRAM
```

Every one of those is MEASURED, not chosen for tidiness:

```
kernel .data + .bss                     166,608 B   of 172,032 granted
root, largest image (c3-isolation)       99,392 B   of 110,592 granted
child (c3-child-poke)                    67,600 B   of  77,824 granted
```

The slack is thousands of bytes, not tens of thousands. That is what
400 KiB looks like once this kernel is in it, and it is why the `.bss`
of every process image is dominated by ONE number: `sosrt`'s 64 KiB
`ARENA`, which every freestanding image links. An overshoot is a loud
`ld.lld: section '.bss' will not fit in region` error, never a silent
overlap.

A linked kernel image, for the record: `.text` at 0x4200_0008 (the ROM's
call target), 361,824 B; `.rodata` 27,356 B; `.data` VMA 0x4037_C000 /
LMA in flash, 14,432 B; `.bss` 152,176 B ending at 0x403A_4AD0; a
407,648-byte flash image, of 4 MiB.

---

## 9. Stopping: this part has no finisher

QEMU `virt` has a `sifive_test` device a guest writes to exit the
emulator with a chosen status. The ESP32-C3 has nothing of the kind, and
Espressif QEMU offers a guest no shutdown door at all — so the kernel
STOPS BY SAYING SO and halting:

```
SOS-C3: halt pass
SOS-C3: halt fail code=0x........
SOS-C3: kernel fault mcause=0x........      (boot.S, for a kernel-mode trap)
```

**ON THIS BOARD THE EXIT STATUS CARRIES NOTHING.** The emulator never
exits on its own; the harness reads the verdict off the transcript and
kills QEMU. That inverts one of the gate's habits and is the single most
important thing to know before writing another case here.
