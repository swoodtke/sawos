# SawOS design 20 — ESP32-C3 board HAL, smoke only

User-ruled scope (Sep 2, the concurrent-pair session): BRINGUP
(boot -> transcript, trap entry, timer tick) plus MEMORY CONFIG (PMP
over the real C3 map, one isolation smoke) — and NOTHING ELSE. This
is design 19's MCU-class flavor made real on the machine-local
Espressif QEMU; the C3 is the FAMILY PROXY (the P4, the first
hardware target, has no QEMU machine — it arrives later as a board
variant reusing these peripheral drivers). NON-GATING: never part of
`make sos-test`, never CI — the QEMU is machine-local
(memory: espressif-qemu-tools.md). Runs concurrent with design 22;
unit 5 (design 21) follows both.

## The pre-carved seams (the stub pass, `ec9e56e`)

- `tools/sos_runner.py` has `--board` with a marked EMPTY esp32c3
  section — everything board-specific lands THERE and only there;
  the virt tables above it belong to other units.
- `Makefile` has `sos-smoke-esp32c3` refusing with a pointer; it
  becomes real.
- `hal/riscv32-esp32c3/README.md` states THE SIBLING-COPY RULE: this
  HAL is its own directory with its OWN copies of whatever it needs
  from `hal/riscv32` — NO restructure of `hal/riscv32` (unit 5 owns
  that directory exclusively); shared-core dedup is a named later
  pass once both land. The README is replaced by a real one when
  filled.

## The board facts (constraints, all ruled or verified)

- QEMU: `~/.espressif/tools/qemu-riscv32/esp_develop_9.2.2_20250817/
  qemu/bin/qemu-system-riscv32 -machine esp32c3`. THE EMULATOR IS
  THE ORACLE for the memory map: derive addresses from
  `info mtree` (monitor) on the actual machine and record them in
  the HAL's ABI.md — cross-check against the C3 TRM where possible,
  but what the smoke target must match is the QEMU model.
- DIRECT BOOT: magic words at flash offset 0, the bundled ROM jumps
  in — NO esptool image format yet (that is a hardware-bringup
  concern, out of scope). The agent verifies the exact magic/entry
  layout empirically against the local QEMU.
- Espressif UART, NOT 16550 — a new console sink (write-byte +
  status poll is all the smoke needs).
- Interrupt matrix + SYSTIMER, NOT CLINT/PLIC: route one SYSTIMER
  alarm through the matrix to the CPU line; the trap entry stays
  standard RISC-V CSRs.
- RV32IMC — NO A EXTENSION: atomics via `rt/common_c/support.c`
  libcalls, irq-off; `SpinLock` is language-refused there;
  single-core makes it sound. The no-A build spelling (sawc target
  flag vs esp-clang override through the toolchain resolver) is the
  agent's FIRST PROBE — record the answer in ABI.md and the
  As-built.
- C3 linker script(s), sibling-copied and reshaped: 400 KB SRAM.
  For SMOKE the ruled default is COPY-TO-SRAM (a tiny flash stub
  copies the image and jumps): XIP placement is design 19's later
  C3/P4-port consideration, explicitly out of scope here.
- PMP: 16 entries (design 19 tier 2's ruled floor met exactly) over
  the REAL C3 map — SRAM, flash window, peripheral space.

## Review points (the API gate's subject — user reviews BEFORE dispatch)

There is no kernel/abi or sysapi surface in this unit; what is
reviewable is the harness and HAL shape:

1. **The smoke case list, smallest that proves the ruling's two
   halves**:
   - `boot`: kernel boots to its transcript banner on the Espressif
     UART — proves direct boot, linker layout, console sink.
   - `timer`: a SYSTIMER alarm arrives through the interrupt matrix
     and ticks N times — proves trap entry + interrupt routing.
   - `isolation`: one PMP proof — a user-mode touch of a
     kernel-owned address faults and is reported — proves the PMP
     program over the real map. Reuse `process-isolation`'s entry
     if its payload proves map-portable; otherwise a minimal
     C3-local case, the agent argues which in the As-built.
2. **The harness shape**: `--board esp32c3` fills the stub section
   only; report printed in case-definition order, same transcript
   discipline as the gate (diffable, deterministic rows); exit
   nonzero on any failure. `make sos-smoke-esp32c3` = exactly that
   invocation. It builds kernel + payloads for riscv32imc via the
   standard toolchain resolver.
3. **HAL layout, the sibling copy**: `hal/riscv32-esp32c3/kernel/`
   (boot.S, trap entry, board sinks — UART/SYSTIMER/matrix, PMP
   over the C3 map, `esp32c3.ld`) + `user/` (ecall stub + link
   script) + `ABI.md` recording the map, the boot protocol, and the
   no-A build spelling. Copies are LICENSED and expected; each
   copied file notes its origin at the top so the later dedup pass
   can find the pairs.

## Out of scope (all named, none negotiable without the user)

- XIP placement, esptool image format, real hardware, the P4
  variant, ESP32-S3/Xtensa.
- Any restructure of `hal/riscv32` (unit 5 owns it); the
  shared-core dedup pass.
- Gating: this target never joins `sos-test` or CI. The GATE for
  this unit's own commit is still `make sos-test` on virt both
  arches — the smoke must not move it.
- New kernel objects, ops, rights, or sysapi surface. If bringup
  seems to need one, STOP and park.

## The proof

`make sos-smoke-esp32c3` runs its cases and prints a diffable
transcript; the As-built records it verbatim as the board's oracle
transcript (the sawlang#238 unit-0 tradition), plus `make sos-test`
unmoved on virt/both-arches.

---

# Amendments (user, Sep 2, after the first flight parked)

Both of the unit's blockers were carried back to the user and both were
answered. The brief above is otherwise unchanged; these two supersede it
where they touch it.

**A. XIP TEXT PLACEMENT IS RULED IN, superseding the brief's
copy-to-SRAM default.** The kernel's loadable image is ~392 KiB against
400 KiB of SRAM, so copy-to-SRAM is arithmetically impossible — `.text`
alone is 352 KiB. `.text` and `.rodata` stay IN PLACE in the flash IBUS
window at 0x4200_0000 (the linker places them there, and the ROM's
`jalr` target 0x4200_0008 is the entry); the boot code copies ONLY
`.data` and zeroes `.bss` in SRAM. Design 19's Sep-2 addendum already
named XIP the ESP32 family's execution model. **Scope stays minimal: NO
sosimg loader XIP mode** — child payloads keep loading to RAM as today.

**B. THE NO-INTERRUPT-DELIVERY FINDING WAS A RETRY, NOT A RULING — and
the retry succeeded.** The machine source shows the matrix outputs wired
to the CPU, and the systimer is an EDGE source, so the first flight's
"nothing reaches the CPU" was suspected to be a lost pulse. It was a
misconfiguration, though not that one: see §7 below. The pre-authorised
polled-clock fallback was NOT needed and is NOT used.

---

# As-built (Sep 2 2026) — LANDED

**SOS boots on the ESP32-C3, and `make sos-smoke-esp32c3` is green on
all three reviewed cases.** The first flight of this unit parked on two
blockers; both are closed, and one of them was closed by discovering
that the park's own finding was wrong.

## 1. What landed

```
hal/riscv32-esp32c3/
  ABI.md              every address with the probe that produced it
  README.md           the sibling-copy rule, and what filled the directory
  kernel/boot.S       XIP .data copy; a console-printing kernel_fault
  kernel/lib.saw      Espressif UART, SYSTIMER, interrupt matrix, the C3 map
  kernel/sink.c       unchanged from the sibling (CSR/linker-symbol leaves)
  kernel/esp32c3.ld   the .magic section IS the direct-boot protocol
  user/root.ld        root at 0x403A_6000
  user/child.ld       child at 0x403C_5000
  user/syscall.c      unchanged from the sibling (the ecall stub)
tests/c3-timer/       root: five deterministic ticks
tests/c3-isolation/   root: launch, park, report the child's fault
tests/c3-child-poke/  child: store into a kernel-owned address
tools/sos_runner.py   the pre-carved board section, filled — and nothing
                      outside it (one contiguous diff hunk)
Makefile              sos-smoke-esp32c3, real
```

Every copied file names its origin at the top, per the sibling-copy
rule, so the later dedup pass can find the pairs. What is genuinely the
sibling's and untouched is everything ARCHITECTURAL: the trap entry, the
32-word frame layout, the resume path, `sos_resume_frame`, the whole PMP
staging apparatus, the cause decoding, the syscall accessors. The C3 is
the same RV32 M/U machine with the same sixteen PMP entries; what
differs is the board, and each board difference is marked `BOARD (C3)`
where it sits.

## 2. The memory map, as derived

From the monitor's `info mtree` on the running machine, cross-checked by
probing. Full table in `hal/riscv32-esp32c3/ABI.md` §1.

```
0x3C00_0000-0x3C7F_FFFF  flash DBUS window (romd, 8M)
0x3FC8_0000-0x3FCD_FFFF  SRAM via the DRAM window (alias of IRAM+0x4000)
0x4000_0000-0x4005_FFFF  mask ROM, 384K
0x4037_C000-0x403D_FFFF  internal SRAM, 400K      <- the only RAM
0x4200_0000-0x427F_FFFF  flash IBUS window (romd, 8M)
0x5000_0000-0x5000_1FFF  RTC RAM, 8K
0x6000_0000 UART0   0x6001_F000 TIMG0   0x6002_3000 SYSTIMER
0x6001_0000 UART1   0x6002_0000 TIMG1   0x600C_2000 INTERRUPT MATRIX
```

The SRAM alias was verified rather than assumed: a store to IRAM
0x4038_0000 reads back at DRAM 0x3FC8_0000, and IRAM's low 16 KiB has no
alias at all. It is not an isolation hole — PMP is default-deny, so only
granted addresses are reachable — but a HAL must never grant BOTH
windows for one region, or one `unmap` would revoke one of two doors.

**The SRAM budget, every number measured:**

```
0x4037_C000  kernel .data + .bss   168K   needs 166,608 of 172,032
0x403A_6000  ROOT region           124K   needs  99,392 of 110,592
0x403C_5000  CHILD region           92K   needs  67,600 of  77,824
0x403D_C000  RAM pool               16K
0x403E_0000  end of SRAM
```

The slack is thousands of bytes, not tens of thousands. That is what
400 KiB looks like with this kernel in it, and the reason every process
image is so large is one number: `sosrt`'s 64 KiB `ARENA`, which every
freestanding image links. An overshoot is a loud `ld.lld: will not fit
in region` error, never a silent overlap.

## 3. The boot protocol

Read out of the bundled mask ROM by disassembly, then confirmed by
booting. Flash words at offset `0x00` and `0x04` must BOTH be
`0xAEDB041D`; the ROM maps flash offset 0 at 0x4200_0000 (IBUS) and
0x3C00_0000 (DBUS), then **`jalr`s to 0x4200_0008** — it CALLS the
image, with `ra` pointing at a ROM path that prints `Direct boot
returned`, so an image that returns is diagnosed rather than hanging.
No esptool format, no header, no checksum: the two magic words are the
entire protocol.

`esp32c3.ld` emits them as a `.magic` output section, and the harness
re-checks them in the flattened image before booting — without them the
ROM never enters the image and the run fails as a silent timeout, which
is a failure mode that says nothing.

## 4. The no-A build spelling, and its third half

```
sawc    --target riscv32-unknown-none-elf --target-features +m,+c
clang   --target=riscv32-esp-unknown-elf
        -march=rv32imc_zicsr_zifencei -mabi=ilp32
blade   march = "rv32imc_zicsr_zifencei"      (in each package's
        mabi = "ilp32"                         [sos.<triple>] section)
        target-features = "+m,+c"
```

The `_zifencei` is not decoration: `-march=rv32imc_zicsr` warns
`no multilib found matching flags` and prints the bundled set, in which
`rv32imc_zicsr_zifencei` is the exact C3 row.

**THE BLADE HALF IS THE ONE THAT WOULD HAVE SHIPPED A BROKEN IMAGE IN
SILENCE.** Blade's built-in default for any `riscv32*` triple is the
virt/ESP32-P4 Profile A baseline — `rv32imac_zicsr` / `+m,+a,+c` — so a
package that says nothing gets the A extension, and the first build of
these packages did. It was caught by reading blade's own build line, not
by anything failing, because **nothing can fail**: this emulator's CPU
reports `misa = 0x401411AD`, advertising A, hardware float, the
hypervisor extension and S-mode, none of which exist on C3 silicon. An
accidental `+a` build runs green here and faults on the part. The no-A
discipline is a BUILD invariant, never something the smoke can catch.

## 5. The isolation case: C3-local, and the brief's question answered

The brief asks whether `process-isolation`'s entry is map-portable. **It
is not, for two independent reasons**, and the second is the interesting
one.

The MECHANICAL reason: a package names its linker script BY TARGET
TRIPLE, and the C3 profile is the same `riscv32-unknown-none-elf` triple
the gate's virt profile is. So a package can name virt's script or the
C3's and not both, and reusing `process-isolation` would link root at
0x8020_0000 — an address this part does not have.

The SUBSTANTIVE reason: `tests/child-poke` is arch-free because it finds
its target by ROUNDING ITS OWN ADDRESS DOWN to a 256 KiB grid — both
virt profiles put every process region on that grid, so a child's own
base is a rounding away and root's top is the same number. **That
arithmetic is a fact about the virt memory map, not about processes.**
This board has 400 KiB of SRAM in total and its regions are 168 / 124 /
92 / 16 KiB, sized to fit the kernel rather than to tile a grid; nothing
rounds. So the C3 child names its target instead, which is honest for a
board-local case.

**And it names a KERNEL-owned address rather than root's**, which is
what the brief's `isolation` case actually asks for and is the stronger
of the two available claims: root's memory is denied because the
protection domain was RELOADED at the switch, but the kernel's is denied
because U-mode matches no PMP entry there AT ALL. The second is the
property the whole protection model rests on, and it is the one a fresh
board port can get wrong by programming its sixteen entries at the wrong
addresses — precisely what this smoke exists to catch. The child stores
to 0x4037_C000, the first word of SRAM, which is the kernel's own
`.data` and is granted to nobody.

## 6. The XIP layout as landed

```
0x4200_0000  .magic     8 B        two words of 0xAEDB041D
0x4200_0008  .text      361,824 B  THE ROM's CALL TARGET
             .rodata     27,356 B
             .payload               root's sosimg, page-aligned both ends
             .regions               the boot region table
             .childimg              child sosimgs
             (.data's load image)
0x4037_C000  .data       14,432 B  VMA in SRAM, LMA in flash
0x4037_F860  .bss       152,176 B  ends 0x403A_4AD0
```

`.payload`, `.regions` and `.childimg` stay in FLASH, which the brief's
copy-to-SRAM shape would not have allowed: they are read-only blobs the
KERNEL copies out of (the sosimg loader copies root's segments into the
root region), so nothing is granted where it sits, and leaving them
there buys back ~39 KiB of SRAM. A payload GRANTED IN PLACE would not
survive this — it would be a read-only flash grant — but no case does
that.

`boot.S` copies `.data` from `_data_lma`, zeroes `.bss`, `fence.i`s, and
calls `kmain`. A 407,632-byte flash image for the `boot` case.

## 7. The interrupt outcome: DELIVERED, and the park's finding was wrong

**The `timer` case takes five real interrupts. The polled-clock fallback
was not needed and is not used.** What follows is the correction, with
its evidence, because the first flight of this unit parked on the
opposite conclusion.

**THE PARK WAS WRONG ABOUT `mie`.** The interrupt matrix drives the
core's MACHINE EXTERNAL interrupt, so `mie` bit 11 (MEIE) is the
architectural gate; per-line masking happens in the matrix's own
`CPU_INT_ENABLE`, not in `mie`. But `mcause` on entry reports the
MATRIX's CPU interrupt NUMBER, not 11. The first sweeps set `mie` to the
line bit alone, saw nothing arrive, and concluded the matrix was not
wired. Probed one variable at a time, source 37 routed to CPU interrupt
7 throughout:

```
mie = (1<<7)             the number mcause reports    -> NOT delivered
mie = (1<<7) | (1<<3)                                 -> NOT delivered
mie = (1<<7) | (1<<11)                                -> delivered
mie = (1<<11)            MEIE alone                   -> delivered
mie = 0xFFFF0080         bit 7 + everything above 15  -> NOT delivered
mie = 0xFFFFFFFF                                      -> delivered
```

and with the recipe fixed at `mie |= 1<<11`, five successive one-shot
arms delivered five interrupts, `mcause = 0x80000007` each time.

**`TIMER_CPU_INT` IS CHOSEN AS 7 ON PURPOSE.** Because `mcause` carries
the matrix's line number, routing SYSTIMER to line 7 makes a C3 tick
arrive with exactly the cause word a standard machine-timer interrupt
has — so `InterruptCause.MachineTimer`, `is_interrupt`, and every
arch-generic reader above the HAL keep working unexamined. A
`static_assert` ties the two numbers together so the claim cannot rot.

**SYSTIMER TARGET0 IS SOURCE 37**, established by taking the interrupt
rather than by reading a table: mapping only source 37 delivered,
mapping only source 6 (the other candidate an earlier sweep left open)
did not. The source numbering is independently corroborated by the mask
ROM's own footprint — it leaves the map register at +0x054 reading 5,
i.e. source 21 (UART0) routed to CPU interrupt 5, which pins both the
4-byte stride and Espressif's indices.

**THE EDGE ORDERING IS REAL AND THE HAL RESPECTS IT.** SYSTIMER TARGET0
is an edge source, so its alarm is a pulse: armed with the delivery path
down, it is lost and no later remapping recovers it. `intc_init` brings
the whole path up at boot — map, priority, threshold, CPU-int enable,
`mie` — before any deadline is ever armed, and `timer_set_deadline_ns`
only ever runs afterwards.

### 7a. ONE REAL EMULATOR GAP, and the HAL absorbs it

**This matrix never raises `mip`, so `wfi` NEVER WAKES on it.** Three
observations: `mip` reads 0x00000000 in the same breath as an interrupt
being taken with `mcause = 0x8000_0007`; `CPU_INT_EIP_STATUS` reads 0
while an interrupt is being delivered; and a `wfi` with the entire
delivery path up sleeps forever — with the CPU interrupt configured
LEVEL and configured EDGE alike.

That matters because SOS idles as
`while nothing runnable { wait_for_irq(); irq_poll() }` with
`mstatus.MIE` never set (design 178 D2 — interrupts are taken from USER
mode only), so the wake it depends on is `wfi` returning on a PENDING
interrupt. On virt the CLINT and PLIC raise `mip`, which is exactly
`wfi`'s wake condition; here nothing does. **This is what the `timer`
case was hanging on after the `mie` fix, and it is a genuine gap in the
machine model rather than a property of the part.**

The HAL absorbs it, which is what a HAL is for: `wait_for_irq()` is
EMPTY on this board and the idle loop spins, while `irq_poll()` reads
the SYSTIMER's own latch, which does work. It costs a core burned while
idle — invisible under emulation, and this target is emulator-only by
ruling. **Nothing above the HAL changes**: D2 is untouched, the kernel
still never takes a trap in kernel mode, and the same `deliver_line`
services the line. `sink.c`'s `sos_wait_for_irq` is left in place,
unused, because on real silicon `wfi` DOES wake from the interrupt
matrix (the C3 TRM's low-power section is explicit that any enabled
interrupt resumes the core) and a hardware bring-up should restore it.

### 7b. A bug of my own, worth recording

Between the two fixes above sat a third failure with the same symptom.
The C3's classes both map to one hardware bit, and collapsing the
kernel-side TAGS onto it too made `intc_init`'s EXTERNAL enable also
read as the TIMER class — which opened `irq_poll`'s systimer gate at
boot, where an unprogrammed comparator reads as permanently expired
(deadline zero). The virt HAL's own comment warns about exactly that.
The tags are kept distinct now and only `irq_class_enable` maps them
onto MEIE. Three different causes, one symptom (`the kernel never
halted`), which is the argument for probing one variable at a time.

## 8. The harness

`--board esp32c3` fills the pre-carved stub section and nothing outside
it — one contiguous diff hunk in `tools/sos_runner.py`. It shares no
table, no build directory and no run path with the gate, on three
counts. The BOARD differs in every way that matters (direct boot from a
flash image rather than `-kernel`, XIP text, no exit door). The BUILD
DIRECTORY has to differ even though the target triple does not — both
profiles are `riscv32-unknown-none-elf`, so a shared `.build/<triple>/`
would let the smoke silently clobber the objects `make sos-test` is
about to link. And the VERDICT is read differently:

**ON THIS BOARD THE EXIT STATUS CARRIES NOTHING.** The C3 has no
`sifive_test` finisher and Espressif QEMU gives a guest no shutdown
door, so the emulator never exits on its own. The kernel's `exit_pass` /
`exit_fail` print a verdict line and halt; the harness matches the
transcript and kills QEMU. That inverts one of the gate's habits and is
the first thing to know before writing another case here.

Serial, not `-j`: three cases, and a smoke whose job is to be diffable
gains nothing from overlapping them. The report prints in
case-definition order and the target exits non-zero on any failure.

## 9. The smoke oracle transcript (VERBATIM)

`make sos-smoke-esp32c3`:

```
SOS ESP32-C3 board smoke (design 20, NON-GATING)
  qemu   /Users/swoodtke/.espressif/tools/qemu-riscv32/esp_develop_9.2.2_20250817/qemu/bin/qemu-system-riscv32
  clang  /Users/swoodtke/.espressif/tools/esp-clang/esp-20.1.1_20250829/esp-clang/bin/clang
  target riscv32-unknown-none-elf --target-features +m,+c (rv32imc_zicsr_zifencei — no A extension)
[1/3] ✓ boot  (407632 bytes of flash)
[2/3] ✓ timer  (436304 bytes of flash)
[3/3] ✓ isolation  (446656 bytes of flash)

============================================================
ESP32-C3 SMOKE PASSED (3 cases)
============================================================
```

And the three consoles behind it, each captured from the same flash
images the run booted. THE BOARD'S ORACLE TRANSCRIPT — diff this.

### boot

```
ESP-ROM:esp32c3-api1-20210207
Build:Feb  7 2021
rst:0x1 (POWERON),boot:0x8 (SPI_FAST_FLASH_BOOT)
SOS M1: kernel up on riscv32 (ESP32-C3)
SOS: bad root image: no root image appended (0x00000000)
SOS-C3: halt fail code=0x00000004
```

### timer

```
ESP-ROM:esp32c3-api1-20210207
Build:Feb  7 2021
rst:0x1 (POWERON),boot:0x8 (SPI_FAST_FLASH_BOOT)
SOS M1: kernel up on riscv32 (ESP32-C3)
SOS: root image ok segments=0x00000002 entry=0x403a82cc prio=0x01010100
SOS: console handover
SOS c3timer: arming
SOS c3timer: tick 1 key=37 fires=1

SOS c3timer: tick 2 key=37 fires=1

SOS c3timer: tick 3 key=37 fires=1

SOS c3timer: tick 4 key=37 fires=1

SOS c3timer: tick 5 key=37 fires=1

SOS c3timer: done ticks=5 slept=1

SOS: process teardown handles=0x00000007 threads=0x00000001 events=0x00000000 waiters=0x00000001 interrupts=0x00000000 timers=0x00000001 process=0x00000000
SOS-C3: halt pass
```

### isolation

```
ESP-ROM:esp32c3-api1-20210207
Build:Feb  7 2021
rst:0x1 (POWERON),boot:0x8 (SPI_FAST_FLASH_BOOT)
SOS M1: kernel up on riscv32 (ESP32-C3)
SOS: root image ok segments=0x00000002 entry=0x403a8388 prio=0x01010100
SOS: boot regions=0x00000002
SOS: console handover
SOS c3iso: started
SOS: fault store-access-fault cause=0x00000007 epc=0x403c5528 tval=0x4037c000
SOS: process teardown handles=0x00000000 threads=0x00000001 events=0x00000000 waiters=0x00000000 interrupts=0x00000000 timers=0x00000000 process=0x00000001
SOS c3iso: root survived child status=131072
SOS c3iso: child syscalls=0 faults=1

SOS c3iso: done

SOS: process teardown handles=0x0000000a threads=0x00000001 events=0x00000000 waiters=0x00000001 interrupts=0x00000000 timers=0x00000001 process=0x00000000
SOS-C3: halt pass
```

Two things in those transcripts are worth naming so a future reader does
not treat them as findings. The BLANK LINES after each `SOS c3*:` line
are the tree's own convention, not this board's: a test writes
`print("...\n", args)` and `print` adds its own newline, so every
`print`-with-arguments line in every case on every profile doubles. The
lines that do not double came from `debug_print`, which takes its
newline literally. And `tval=0x4037c000` in the isolation case is the
exact address `c3-child-poke` names — the kernel's own `.data` base —
which is the whole of that case's assertion.

## 10. Deviations

None. Both departures from the brief as written are the user's own
amendments, recorded at the top of this As-built: XIP text placement
(superseding the copy-to-SRAM default) and the interrupt retry. The
pre-authorised polled-clock fallback was available and was not used —
the interrupt is real.

Two things inside the ruled scope are worth naming as deliberate
choices rather than deviations. `wait_for_irq()` is empty on this board
(§7a) — a HAL-local absorption of an emulator gap, with no change above
the HAL and design 178 D2 untouched. And the three C3 test packages are
NEW directories under `tests/`, which the dispatch explicitly permits;
no existing test was edited.

## 11. Findings

1. **The park's own conclusion was wrong, and the shape of the mistake
   is the lesson.** "Nothing reaches the CPU" was three different
   misconfigurations wearing one symptom — a missing MEIE bit, a
   collapsed class tag, and a genuine `wfi`/`mip` gap — each of which
   individually produced a silent hang with every register reading
   correct. The only thing that separated them was probing one variable
   at a time against a known-good control. A sweep that changes several
   things at once cannot distinguish "not wired" from "wired and I asked
   wrongly", and this unit spent a park learning that.
2. **`mcause` naming a controller's line number rather than the
   architectural cause is a portability trap with a cheap fix.**
   Choosing the matrix's CPU interrupt 7 so the cause word matches the
   standard machine-timer encoding cost one constant and kept every
   arch-generic reader above the HAL working unexamined. Worth doing on
   any board whose controller multiplexes into `mcause`.
3. **The C3 is a tier-2 part AT the floor in two dimensions, not one.**
   Design 19 sizes tier 2 by PMP slots and the C3 meets that exactly
   (16 entries, all implemented, all four `pmpcfg` words writable). What
   design 19 did not price is RAM: 400 KiB against a 392 KiB kernel
   image, which is why XIP is the entry price rather than a placement
   preference. Both belong in the M5 sketch's tier table.
4. **`sosrt`'s 64 KiB ARENA dominates every process image here.** Root
   needs 99,392 bytes and the child 67,600, of which 65,536 is the arena
   in each. On a 400 KiB part that is the single biggest lever on how
   many processes fit, and it is a `rt/` question rather than a board
   one.
5. **The emulator is a weaker oracle than the brief assumed, in three
   specific ways**, each marked at its section in `ABI.md`: it
   over-reports the ISA (`misa` advertises A, F, D, H and S-mode, none
   on real C3 silicon), so it cannot police the no-A discipline; it
   drains the UART TX FIFO synchronously and reports `TXFIFO_CNT` as
   zero even mid-burst, so the console sink's poll is silicon-only
   correctness with no test; and it never raises `mip` for a matrix
   interrupt, so `wfi` is unusable. A P4 or S3 port should re-read those
   three before trusting a green run.
6. **`irq_raise_selftest_line` is the one line of this HAL that is
   UNEXERCISED**, and it says so in its own doc comment. None of the
   three smoke cases takes a device interrupt, and a probe that enabled
   UART interrupts wholesale saw the console output stop dead — an
   interaction this unit did not chase. Treat a first use as bring-up.
7. **A `u32`-suffixed shift still folds signed**, so bit 31 of a
   `UInt32` cannot be written as a shift on a 32-bit target and the
   house bit-flag style breaks at exactly one bit. Filed as SL-19.
