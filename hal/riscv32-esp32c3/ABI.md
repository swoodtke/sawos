# hal/riscv32-esp32c3 — the board facts

The contract for this board HAL, and the record of how every number in it was
obtained. `boot.S`, `lib.saw`, `esp32c3.ld` and the two user linker scripts
beside them are the board half of the HAL; the shared riscv32 half — `trap.S`,
`sink.c`, the `rv32core` module and the `ecall` stub — lives in
`../riscv32-common/` and is compiled into this board's image unchanged. This
file says why each address is what it is.

Every number below was read off the running machine, not off a datasheet. Where
the emulator and the ESP32-C3 TRM disagree, the disagreement is called out,
because what the smoke target must match is the QEMU model and what real
silicon owes is the TRM.

Three things in this file are emulator gaps rather than board facts, and each
says so where it sits: the UART's transmit-FIFO count (§5), the interrupt
matrix's `mip` behaviour (§7b), and the CPU's advertised ISA (§3a). A hardware
bring-up should re-read those three sections first.

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
0x4037_C000-0x403D_FFFF  esp32c3.iram     ram  400K   the only RAM
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
0x4000 + X. Probed: a store of `0xa5a5a5a5` to IRAM `0x4038_0000` reads
back at DRAM `0x3FC8_0000`. Stores at both ends of the IRAM window
(`0x4037_C000` and `0x403D_FFFC`) read back their own values, so the
whole 400 KiB is plain read/write memory reachable through the IBUS
window — there is no I/D split to design around on this part.

```
IRAM 0x4037_C000 .. 0x4037_FFFF   16K   no DRAM alias
IRAM 0x4038_0000 .. 0x403D_FFFF  384K   == DRAM 0x3FC8_0000 .. 0x3FCD_FFFF
```

The alias is not an isolation hole: RISC-V PMP is default-deny for
U-mode, so a process reaches only what it was granted, and a grant
written against IRAM addresses leaves the DRAM addresses of the same
bytes unreachable. A HAL must nonetheless never grant both windows for
one region, or a single `unmap` would revoke only one of two doors.

### 1b. Total RAM is 400 KiB and there is no more

`esp32c3.iram` (409,600 B) plus `esp32c3.rtcram` (8,192 B) is the whole
writable map. The `esp-rgb-vram` region at 0x2000_0000 is QEMU's
display extension, not ESP32-C3 silicon, and must not be counted.

---

## 2. Boot protocol: direct boot (verified empirically)

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

**The contract:**

- Flash words at offset `0x00` and `0x04` must both be `0xAEDB041D`.
- The ROM maps flash offset 0 at `0x4200_0000` (IBUS) and `0x3C00_0000`
  (DBUS) before jumping, so the image is XIP-readable from word one.
- The ROM enters with `jalr`, i.e. it calls **`0x4200_0008`** — flash
  offset 8 — with `ra` pointing at a ROM path that prints
  `Direct boot returned`. An image that returns is therefore diagnosed
  rather than silently hanging.
- Entry is M-mode. UART0 is already configured by the ROM (see §5).
- No esptool image format is involved. No header, no checksum, no
  segment table — the two magic words are the entire protocol.

Attaching the flash: `-drive file=<image>,if=mtd,format=raw`. Without
it QEMU prints `Not initializing SPI Flash` on stderr and the machine
boots to the ROM's download-mode path. QEMU also prints
`Adding SPI flash device` on stderr when a drive is attached — stderr
noise the harness must filter out of a diffable transcript.

The ROM's own banner is fixed text and is therefore transcript-stable:

```
ESP-ROM:esp32c3-api1-20210207
Build:Feb  7 2021
rst:0x1 (POWERON),boot:0x8 (SPI_FAST_FLASH_BOOT)
```

---

## 3. The no-A build spelling

The ESP32-C3 is **RV32IMC**: no A extension, so atomics are
plain read-modify-write libcalls that the environment
(`rt/common_c/support.c`) supplies, and `SpinLock` is language-refused. That
is sound on a single core in the kernel, which runs with interrupts masked,
and in a user process only while nothing preempts it.

**sawc** — the triple names an architecture, `--target-features` names
the extensions, so the C3 spelling is the virt profile's list minus
`+a`:

```
--target riscv32-unknown-none-elf --target-features +m,+c
```

(virt riscv32 uses `+m,+a,+c`; `tools/sos_runner.py`'s `ARCHES` riscv32
entry is the comparison point.)

**clang / ld.lld** — esp-clang, whose default triple is already
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

**blade** — the half that is easiest to miss, because it is in a manifest
rather than on a command line. Blade's built-in default
for any `riscv32*` triple is the virt/ESP32-P4 Profile A baseline
(`march = rv32imac_zicsr`, `target-features = +m,+a,+c`), so a package
that says nothing gets the A extension. Every C3 userspace package
restates all three keys in its `[sos.<triple>]` section:

```toml
[sos.riscv32-unknown-none-elf]
linker-script = "../../hal/riscv32-esp32c3/user/root.ld"
native = "../../hal/riscv32-common/user/syscall.c ../../rt/common_c/support.c"
march = "rv32imc_zicsr_zifencei"
mabi = "ilp32"
target-features = "+m,+c"
```

Without them the image links `amo*`/`lr`/`sc` instructions the part
cannot execute, and nothing fails on the emulator — see §3a.

### 3a. The emulator will not catch a stray `+a`

Probed: this QEMU's esp32c3 CPU reports

```
misa = 0x401411AD   ->  RV32, extensions A C D F H I M S U
```

That is a generic QEMU RV32 core, not a faithful C3: it advertises the
**A** extension, hardware float (**F**/**D**), the hypervisor extension
(**H**) and **S-mode**, none of which exist on ESP32-C3 silicon (M and U
only, RV32IMC). Consequence, and it is the important half of this
section: an accidental `+a` build, an FPU spill, or an S-mode
dependency runs green on this emulator and fails on the part. The no-A
discipline is a build invariant enforced by the flags above, never
something the smoke target can be relied on to catch.

---

## 4. PMP (verified: 16 entries)

- `pmpaddr0`..`pmpaddr15` all implemented: each accepted `0x3FFFFFFF`
  and read it back, with zero illegal-instruction traps. Implemented
  mask `0x0000FFFF`, count **16**.
- `pmpcfg0`..`pmpcfg3` all writable: wrote `0x0F0F0F0F` to each, read
  all four back verbatim, zero traps.
- 16 entries is the same budget `hal/riscv32` runs on virt, so the TOR-pair
  arithmetic in `hal/riscv32-common` (`PMP_REGIONS` logical regions from 16
  entries) transfers unchanged. It leaves no headroom: the C3 has exactly the 16
  the kernel needs, where the P4 has 32.

### 4a. U-mode isolation works end to end

The mechanism the `isolation` smoke case exercises is fully supported
here. Probed: one TOR pair granting R/W/X over `[0x403C_0000, 0x403C_1000)`
(`pmpaddr0 = base>>2`, `pmpaddr1 = top>>2`, `pmpcfg0 = 0x0F00`), then
`mstatus.MPP` cleared to U and `mret` into the granted window.

```
granted window, ecall from U-mode        -> mcause = 0x00000008
U-mode load of 0x4038_0000 (not granted) -> mcause = 0x00000005  (load access fault)
                                            mtval  = 0x40380000  (the denied address)
                                            mepc   = the faulting instruction
```

So default-deny, the TOR encoding, the fault cause, `mtval` and `mepc` all
behave exactly as the PMP code in `hal/riscv32-common` (`rv32core`) assumes.
Nothing in the protection path needs rethinking for this board.

---

## 5. UART0 console (`esp_soc.uart` @ 0x6000_0000) — not 16550

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

**The status poll is a no-op under this emulator.** Probed: 200 bytes
written back-to-back to `UART_FIFO`, then `UART_STATUS` read immediately — it
reads `0x00000000`, and all 200 bytes arrived. QEMU's `esp_soc.uart` hands each
byte to the chardev synchronously and never reports a TX FIFO count, so a
`TXFIFO_CNT` poll never spins here. It is still the right thing to write
(silicon's 128-byte FIFO does fill), but the smoke target cannot exercise it,
and a poll written against the wrong bit field would pass on QEMU and hang on
hardware.

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
`INT_ENA` bit 0, `CONF |= TARGET0_WORK_EN`) sets `INT_RAW` bit 0 and
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

**SYSTIMER TARGET0 is source 37**, established by taking the interrupt:
mapping only that source and arming delivered, mapping only source 6
(the other candidate the first sweep left open) did not.

### 7a. The `mie` bit that gates it is 11, not the one `mcause` reports

The matrix drives the core's machine external interrupt, and `mie.MEIE`
(bit 11) is the only architectural gate; per-line masking happens in
`CPU_INT_ENABLE`, not in `mie`. But `mcause` on entry reports the matrix's own
CPU interrupt number, not 11. Probed, one variable at a time, source 37 routed
to CPU interrupt 7 throughout:

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

Consequence for the HAL: `TIMER_CPU_INT` is **7** so that a C3 tick arrives
with the same `mcause` a standard machine-timer interrupt has, which is what
lets the arch-generic cause decoding above the HAL stay arch-generic. A probe
that sets `mie` to the line bit alone concludes, wrongly, that the matrix is
not wired.

### 7b. `wfi` never wakes on a matrix interrupt — an emulator gap

The matrix drives the core's request line directly and **never raises
`mip`**. Three independent observations:

- `mip` reads `0x00000000` in the same breath as an interrupt being
  taken with `mcause = 0x8000_0007`;
- `CPU_INT_EIP_STATUS` reads `0x00000000` while an interrupt is being
  delivered;
- a `wfi` with the entire delivery path up sleeps forever — with the CPU
  interrupt configured level and configured edge alike.

That matters because SOS's idle path is
`while nothing runnable { wait_for_irq(); irq_poll() }` with
`mstatus.MIE` never set (interrupts are taken from user mode only), so the
wake it depends on is `wfi` returning on a pending interrupt. On virt the CLINT
and PLIC raise `mip`, which is `wfi`'s wake condition (`mip & mie`); here
nothing does.

The HAL absorbs it: `wait_for_irq()` is empty on this board and the idle loop
spins, while `irq_poll()` reads the SYSTIMER's own latch — which does work. It
costs a core burned while idle, invisible under emulation, and this target is
emulator-only. Nothing above the HAL changes: the kernel still never takes a
trap in kernel mode. The shared `sink.c`'s `sos_wait_for_irq` is left in place,
unused and undeclared by this board's `extern` block (the block is split by
caller, so `--gc-sections` drops the leaf), because on real silicon `wfi` does
wake from the interrupt matrix (the C3 TRM's low-power section is explicit that
any enabled interrupt resumes the core) and a hardware bring-up should restore
the call.

### 7c. The recipe, as the HAL programs it

At boot (`intc_init`), before any alarm is armed — SYSTIMER TARGET0 is an
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

Reverse lookup: there is no claim register and `EIP_STATUS` is unusable,
so `irq_claim` maps a CPU interrupt number back to a source by scanning
the map registers the kernel itself wrote: one device read per source, so slow,
stateless, and impossible to get out of step with the hardware.

---

## 8. The SRAM budget, and why the layout is XIP

This part has 400 KiB of SRAM in total, and the layout is execute-in-place:
`.text` and `.rodata` execute and are read in place in the flash IBUS window at
0x4200_0000, with `.text` at 0x4200_0008, the ROM's call target; only `.data` is
copied to SRAM (by `boot.S`) and `.bss` zeroed there. `.payload`, `.regions` and
`.childimg` stay in flash too — the kernel copies out of them, so nothing is
granted where it sits.

The whole 400 KiB, as `esp32c3.ld` and the two user scripts divide it:

```
0x4037_C000  kernel .data + .bss (kernel stack in .bss)      168 KiB
0x403A_6000  ROOT REGION      (108K image + 16K stack)       124 KiB
0x403C_5000  CHILD REGION     ( 76K image + 16K stack)        92 KiB
0x403D_C000  RAM POOL                                         16 KiB
0x403E_0000  end of SRAM
```

The flash image must fit the 4 MiB `FLASH` region of `esp32c3.ld`, which is the
flash size the smoke harness provides; the runner's `C3_FLASH_BYTES` checks the
same limit before booting.

In a freestanding process image the `.bss` is dominated by one number: the
arena, `ARENA_SIZE` in the linker script. A process package may choose its own
size (`tests/arena-small/`); a package that says nothing gets the default in
`root.ld` / `child.ld`. The kernel's arena is `PROVIDE(ARENA_SIZE = 16K)` in
`esp32c3.ld`: the kernel allocates nothing on its steady path (every kernel
source builds under `--no-hidden-alloc`; the arena backs only the compiler's
panic path), so the arena is the first lever to pull when the kernel's `.data` +
`.bss` outgrow their region. The kernel's `.bss` also holds the kernel stack
(`KERNEL_STACK_SIZE`, a second lever) and the per-process stats region, which
has `STATS_ROWS` = `PROT_DOMAIN_SLOTS` rows. `PROT_DOMAIN_SLOTS` is
`rv32core`'s, shared by every riscv32 profile; it sizes no domain pool on
riscv32 (there is none), so its storage effect here is the stats region. The
arena is `[_arena_start, _arena_end)`, reserved inside `.bss` by the linker
script and read back through `rt/common_c/support.c`.

An overshoot of any region is a loud
`ld.lld: section '.bss' will not fit in region` error, never a silent overlap.

---

## 9. Stopping: this part has no finisher

QEMU `virt` has a `sifive_test` device a guest writes to exit the
emulator with a chosen status. The ESP32-C3 has nothing of the kind, and
Espressif QEMU offers a guest no shutdown door at all — so the kernel
stops by saying so and halting:

```
SOS-C3: halt pass
SOS-C3: halt fail code=0x........
SOS-C3: kernel fault mcause=0x........      (boot.S, for a kernel-mode trap)
```

**On this board the exit status carries nothing.** The emulator never
exits on its own; the harness reads the verdict off the transcript and
kills QEMU. That inverts one of the gate's habits and is the single most
important thing to know before writing another case here.
