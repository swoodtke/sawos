# hal/xtensa-esp32s3 — the Xtensa HAL and the ESP32-S3 board

SOS boots here, on the `Flat` protection tier. `make sos-smoke-esp32s3` runs
the board smoke against Espressif's QEMU (`-machine esp32s3`); it needs a
sawlang whose `sawc` targets `xtensa-esp-unknown-elf`, and the Espressif
toolchain under `~/.espressif` (that QEMU, and esp-clang with the `ld.lld`
beside it, which can link Xtensa). It is not part of `make sos-test`.

There is one Xtensa board, so the architecture and the board share one
directory, as `hal/arm64/` does.

```
kernel/boot.S       the direct-boot entry, the reset path out of the ROM's
                    register window, the vector table, the trap entry and
                    the restore path (`sos_resume_frame`)
kernel/sink.c       the special-register leaves (INTENABLE, INTERRUPT,
                    INTCLEAR), `simcall` SYS_exit, the linker-symbol
                    accessors, and `kernel_fault`
kernel/lib.saw      the `hal` module: the trap frame and its accessors, the
                    cause decoding, the Flat protection seam, UART0,
                    SYSTIMER, the interrupt matrix, the memory map
kernel/esp32s3.ld   the flash layout across the two bus windows, IRAM for the
                    vectors, DRAM for the kernel's data
user/syscall.c      the `syscall` stub every root and child links
user/root.ld        root at 0x403A_0000      user/child.ld  child at 0x403B_8000
ABI.md              every board fact, with the probe that produced it; §13 is
                    the process layout
```

**Read `ABI.md` before changing anything here.** It is the record of what
this emulator does, probe by probe; its §11 lists where silicon differs or
may differ, and marks what was probed on a board.

The things most likely to surprise someone arriving here:

- **Everything is call0.** The ROM enters the image windowed (`callx8`);
  `_start` resets the register window and turns window exceptions off, and
  nothing after it is windowed. The compiler's objects are call0 with no FPU
  (the triple's default features), and so is every C and assembly file here
  (`-Xclang -target-feature -Xclang -windowed`, the same for `-fp`). No call0
  libgcc, compiler-rt or ROM helper exists, so no image may link one.
- **There is no privilege.** PS.UM selects which vector a trap takes, not what
  code may do: user code can write every special register (ABI.md §5e). So the
  tier is `Flat`: `prot_isolated()` answers false and every `prot_*` call
  records nothing in hardware, while the kernel's checks on the syscall path
  hold as on every tier. The vector is the mode witness: user syscalls,
  interrupts and faults reach the user vector, and the kernel's own traps, with
  PS.UM = 0, reach the kernel or double vector and stop the machine through
  `kernel_fault`. Two things a process can do the tier cannot deny: a `break`
  in user mode reaches the debug vector and stops the machine the same way
  (`vector=0x00000280`; compiled code traps with `ill`, so only hand-written
  assembly gets there), and `rsil` raises the process's own INTLEVEL, which
  turns the tick off for as long as it runs.
- **What a switch does not carry, for whoever writes assembly here.** The
  trap frame holds a0..a15, PC, PS, `SAR`, `LBEG`/`LEND`/`LCOUNT`,
  `SCOMPARE1` and `THREADPTR` (ABI.md §7c). MAC16's accumulator and
  registers (`ACCLO`, `ACCHI`, `M0..M3`), the boolean registers (`BR`),
  `MISC0..3` and the FP state are not in it, so they do not survive a switch;
  compiled code never touches them. And a process can write `CPENABLE`
  itself, so "an FP instruction is a fault" holds only for a process that
  leaves it alone: one that sets it runs FP instructions, and loses its FP
  registers at the next switch.
- **Flash is two windows.** Code executes in place at 0x4200_0000 (IBUS);
  read-only data, the appended images and the region table are read at
  0x3C00_0000 (DBUS). `esp32s3.ld` lays the image out as one run of flash:
  the fetched sections first, with the load images of `.data` and the
  vectors, and the sections read in place after. `boot.S` copies `.data` and
  the vectors out of flash through the data bus. Literal pools sit below
  their code (`l32r` reaches only backwards).
- **The watchdogs are switched off first.** `_start` turns off the RTC and
  TIMG0 watchdogs and sets the super watchdog to feed itself before it
  copies anything (ABI.md §2e has every register, key and source).
- **SRAM is one array twice on silicon.** IRAM and DRAM are separate on the
  emulator; on silicon IRAM 0x4037_8000 + n is DRAM 0x3FC8_8000 + n. The
  vectors, the kernel's data and the process regions are laid out so that no
  two are twins (`esp32s3.ld` has the map).
- **Interrupts are level-1 and level-triggered.** SYSTIMER TARGET0 (matrix
  source 57) is CPU interrupt 1. A device line is given one of nine CPU
  interrupts when it is unmasked and keeps it while masked; an unmask that
  finds none unassigned takes one back from a masked line, so at most nine
  lines are unmasked at once, never a count of lines ever bound. The mask is
  INTENABLE, not the matrix: this emulator moves a CPU interrupt's level only
  when a source changes level, not when a route does (ABI.md §6c).
  The kernel runs at INTLEVEL 15 from `_start` on (ABI.md §7f has the probe
  and the stress run), so interrupts are taken from user mode only, and
  `wait_for_irq()` is empty because `waiti` would take the interrupt in
  kernel mode (ABI.md §6d): the idle loop spins on `irq_poll`, which reads
  SYSTIMER and the INTERRUPT register.
- **The scheduler tick preempts processes here.** This emulator delivers the
  timer's level-1 interrupt to user mode, so the shipping kernel's tick takes
  the processor from a process that never traps; the smoke's `tick_spinner`
  shows it. Every other case's image carries the "no tick" boot flag, as on
  the gate.
- **The machine stops through semihosting.** `exit_pass`/`exit_fail` print the
  verdict line (`SOS-S3: halt pass` / `halt fail code=0x...`) and then
  `simcall` SYS_exit, so the harness asserts the exit status as well as the
  transcript. The emulator runs with `-semihosting`; without it the kernel
  halts after the line. A board image is linked so that it never executes
  `simcall` and halts in a quiet loop after the line (ABI.md §10).
- **Processes run from IRAM at fixed link addresses.** Nothing translates, so
  root links at `ROOT_LOAD_BASE` and a resident child at the destination the
  runner's region table names, one linker script per resident image as on
  riscv32; the region map is in `kernel/lib.saw` above `RAM_BASE`.

The smoke's cases are in `_esp32s3_smoke` in `tools/sos_runner.py`; `--case`
narrows it. The kernel-only ones are the gate's harness kernels of the same
names on hand-assembled payloads from `tests/xtensa/`, and `irq_takeback`,
which drives this HAL's CPU interrupt table directly. The process ones are
the gate's own cases, named there, and two of the board's own:
`irq_line_cycle` (more lines bound over a life than there are CPU
interrupts) and `frame_hold` (every register of the trap frame held across
ticks, in `tests/frame-hold/`). Their packages carry a
`[sos.xtensa-esp-unknown-elf]` section naming `user/root.ld` or
`user/child.ld` and, as native sources, `user/syscall.c` and
`rt/common_c/support.c`, so the same programs run on this board. Blade builds
them with Espressif's clang and `ld.lld` (`SOS_CLANG`, `SOS_LD`) and tags them
with the image format's Xtensa tag, which `arch_tag()` answers.

`support.c` is still linked for the arena accessors. Its word-atomic routines
are not reached here: the compiler lowers word atomics to inline `s32c1i`, so
nothing references them and `--gc-sections` drops them.

## First run on a board

The stages `boot`, `umode_syscall`, `timer_tick` and `bus_probe` below run
to their end on an ESP32-S3 rev v0.2 board whose ROM is `esp32s3-20210327`;
`root_server_boot` does not, and `bus_probe` says why. The rest is checked
on the emulator or read from Espressif's tools. `ABI.md` §11 lists, item by
item, what silicon does differently or may, what the board showed and what
was prepared. Each step reads the board or writes our image to its flash and
nothing else: no command here writes an eFuse, and the only write is
`write-flash` of our image, which a later flash of any other firmware
replaces.

### What you need

- An ESP32-S3 board with a USB-to-UART bridge port (on Espressif's
  DevKitC-1 boards, the port marked UART). The kernel prints on UART0, which
  that port carries. A board with only the chip's native USB port shows the
  ROM's banner there and nothing from the kernel (`ABI.md` §4a).
- Python 3 with esptool, which brings espefuse and pyserial along:

  ```
  python3 -m venv ~/sos-esptool
  ~/sos-esptool/bin/python -m pip install esptool
  source ~/sos-esptool/bin/activate
  ```

  The commands below are esptool 5's spelling (`write-flash`, `read-mem`);
  esptool 4 spells them with underscores (`write_flash`, `read_mem`).
- The serial device. On macOS, list `/dev/cu.*` before and after plugging the
  board in; the new entry is the port. `PORT` below stands for it. The name
  does not say which port it is: a bridge appears as `/dev/cu.usbserial-…`,
  `/dev/cu.SLAB_USBtoUART` or `/dev/cu.wchusbserial…`, but a CH34x-style
  bridge can appear as `/dev/cu.usbmodem…`, which is also how the chip's
  native USB port appears. To tell them apart, run step 1's `flash-id`: on
  the bridge, esptool resets the chip "via RTS pin" and reports no
  USB-Serial/JTAG mode. The USB vendor ID says it too
  (`system_profiler SPUSBDataType`): Espressif's own, 0x303a, is the native
  port, and any other is a bridge.

Most boards enter the ROM's download mode by themselves when esptool
connects. If esptool cannot connect, hold BOOT, press and release RST, then
release BOOT, and run the command again.

### 1. Can this chip direct-boot at all? (read-only)

```
python3 -m esptool --chip esp32s3 --port PORT flash-id
python3 -m espefuse --chip esp32s3 --port PORT summary \
    DIS_DIRECT_BOOT SECURE_BOOT_EN SPI_BOOT_CRYPT_CNT \
    DIS_DOWNLOAD_MODE UART_PRINT_CONTROL DIS_USB_SERIAL_JTAG
```

`flash-id` names the chip revision and the flash size; send its output.
`summary` prints each named eFuse with its value. Direct boot can work only
if:

| eFuse | must read | if not |
|---|---|---|
| `DIS_DIRECT_BOOT` | `False` | the ROM never runs a direct-boot image: stop here |
| `SECURE_BOOT_EN` | `False` | the ROM accepts only signed images: stop here |
| `SPI_BOOT_CRYPT_CNT` | `Disable` (0) | flash encryption is on and the ROM would read our image as ciphertext: stop here |
| `DIS_DOWNLOAD_MODE` | `False` | esptool cannot write the flash |
| `UART_PRINT_CONTROL` | `Enable` (0) | the ROM's banner may be missing; the kernel's lines are not affected |

The same two direct-boot bits can be read without espefuse, as words:
`python3 -m esptool --chip esp32s3 --port PORT read-mem 0x6000703c` must
print a value with bit 1 clear (`DIS_DIRECT_BOOT`), and `read-mem 0x60007038`
a value with bit 20 clear (`SECURE_BOOT_EN`). `ABI.md` §2a and §11 give the
sources.

### 2. Build the images (the machine with this tree)

```
SAWLANG_ROOT=/path/to/sawlang python3 tools/sos_runner.py --board esp32s3 \
    --emit-flash boot --out sos-boot.bin
```

and the same with `umode_syscall`, `timer_tick`, `bus_probe` and
`root_server_boot` for the later stages. Each is the smoke case's image,
linked without the emulator's exit (it halts in place instead) and trimmed to
its length, about 110 KiB; the runner checks the direct-boot magic at flash
offset 0x400 and that byte 0 is not an esptool header.

### 3. Write the image

```
python3 -m esptool --chip esp32s3 --port PORT write-flash 0x0 sos-boot.bin
```

The offset is 0x0: the ROM finds the magic at 0x400 and enters at 0x408.
esptool resets the board into the new image when it finishes.

### 4. Watch the console

```
python3 -m serial.tools.miniterm --raw --dtr 0 --rts 0 PORT 115200
```

then press the board's RST button to see the boot from the start. `--dtr 0
--rts 0` keeps the auto-reset lines released, which on most boards would
otherwise hold the chip in reset or in download mode. Ctrl-] quits. Copy
everything from the first `ESP-ROM:` line on.

### The order to go in

Each stage needs the one before it to have worked. Send the console text of
each.

1. **`boot`**: the kernel alone, no process. It prints its banner, finds no
   root image and halts. Halted means the last line stays the last line: the
   board does not reset and prints nothing more until RST is pressed, which
   repeats the same text. Expected, after the ROM's lines:

   ```
   SOS M1: kernel up on xtensa (ESP32-S3)
   SOS: bad root image: no root image appended (0x00000000)
   SOS-S3: halt fail code=0x00000004
   ```

   The ROM's lines come first: `ESP-ROM:esp32s3-…`, a `Build:` line and an
   `rst:0x1 (POWERON),boot:0x… (SPI_FAST_FLASH_BOOT)` line (or
   `SPI_FLASH_BOOT`), possibly with more ROM lines after it.
2. **`umode_syscall`**: the first entry to user mode, a hand-written payload
   that prints through system calls and asks to stop:

   ```
   SOS M1: entering U-mode
   SOS: console handover
   SOS-U
   SOS-S3: halt pass
   ```
3. **`timer_tick`**: the timer interrupt taken from user mode. At least two
   tick lines; the addresses may differ:

   ```
   SOS M2: timer bring-up on xtensa (ESP32-S3)
   SOS M2: timer armed
   SOS M2: entering U-mode
   SOS: console handover
   SOS: timer tick 0x00000001 at 0x42019680
   SOS: timer tick 0x00000002 at 0x42019686
   ...
   SOS-S3: halt pass
   ```

   On a part the last line comes on the order of half a minute after the
   ticks: the payload spins a fixed count of instructions, and the kernel
   leaves the CPU clock as the ROM sets it. Wait for it before calling the
   stage stuck.
4. **`bus_probe`**: the memory accesses the emulator forgives and a part may
   refuse, one at a time. Each step prints its name and address before the
   access, then `ok` and what it read, or a refusal, and the probe carries on
   to the next step:

   ```
   SOS probe: byte load, process IRAM at 0x403a0021
   SOS probe: refused cause=0x00000003 vaddr=0x403a0021
   ```

   `cause` is the EXCCAUSE the access raised (`kernel/lib.saw`'s `TrapCause`
   names them: 2 `InstructionFetchError` and 20 `InstFetchProhibited`, an
   instruction fetch from an address that cannot execute; 3
   `LoadStoreError`, a width or bus the address does not take; 9 an
   unaligned access; `ABI.md` §5c has the ones seen) and `vaddr` the
   EXCVADDR, the address refused. A refusal is an answer, not a failure: the
   step that made it is named on the line before. The alias steps end in
   `shared` or `not shared`: whether a word stored through a process
   address's DRAM name is read back through its IRAM name, and the reverse.
   The user-mode step comes last because a refusal there ends the run: the
   program it runs prints `SOS-U` and the byte it read back, then the probe's
   last line, and stops the machine.

   The rev v0.2 board (ROM `esp32s3-20210327`) printed this after the ROM's
   lines, byte for byte the same on two boots, where `F` is a flash address
   that moves with the code (0x4201b740 in that image) and `R` the region
   table's (0x3c01ea80):

   ```
   SOS probe: bus probe on xtensa (ESP32-S3)
   SOS probe: word store and load, process IRAM at 0x403a0000
   SOS probe: ok 0x5a5aa5a5
   SOS probe: word store and load, process IRAM at 0x403dfffc
   SOS probe: ok 0x5a5aa5a5
   SOS probe: alias, stored at 0x403a0000 then 0x3fcb0000, read at 0x403a0000
   SOS probe: ok 0xc3d2e1f0
   SOS probe: shared
   SOS probe: alias, stored at 0x3fcefffc then 0x403dfffc, read at 0x3fcefffc
   SOS probe: ok 0xc3d2e1f0
   SOS probe: shared
   SOS probe: word store and load, DRAM at 0x3fcb0000
   SOS probe: ok 0x5a5aa5a5
   SOS probe: halfword store and load, DRAM at 0x3fcb0002
   SOS probe: ok 0x0000c33c
   SOS probe: byte store and load, DRAM at 0x3fcb0003
   SOS probe: ok 0x00000096
   SOS probe: word store and load, DRAM at 0x3fcefffc
   SOS probe: ok 0x5a5aa5a5
   SOS probe: halfword store and load, DRAM at 0x3fcefffe
   SOS probe: ok 0x0000c33c
   SOS probe: byte store and load, DRAM at 0x3fceffff
   SOS probe: ok 0x00000096
   SOS probe: word store and load, DRAM at 0x3fcb0004
   SOS probe: ok 0x5a5aa5a5
   SOS probe: unaligned word load, DRAM at 0x3fcb0001
   SOS probe: ok 0xa5963ca5
   SOS probe: ATOMCTL 0x00000015
   SOS probe: word store, then s32c1i swapping it, DRAM at 0x3fcb0010
   SOS probe: ok 0x00000005
   SOS probe: word load after s32c1i, DRAM at 0x3fcb0010
   SOS probe: ok 0x00000007
   SOS probe: word store and load, process IRAM at 0x403a0020
   SOS probe: ok 0x5a5aa5a5
   SOS probe: byte load, process IRAM at 0x403a0021
   SOS probe: refused cause=0x00000003 vaddr=0x403a0021
   SOS probe: halfword load, process IRAM at 0x403a0022
   SOS probe: refused cause=0x00000003 vaddr=0x403a0022
   SOS probe: byte store, word loaded back, process IRAM at 0x403a0021
   SOS probe: refused cause=0x00000003 vaddr=0x403a0021
   SOS probe: halfword store, word loaded back, process IRAM at 0x403a0022
   SOS probe: refused cause=0x00000003 vaddr=0x403a0022
   SOS probe: payload copied by words, process IRAM at 0x403a0100
   SOS probe: ok 0x802ac222
   SOS probe: call, process IRAM at 0x403a0100
   SOS probe: ok 0x0000102a
   SOS probe: payload copied by words, DRAM at 0x3fcb0100
   SOS probe: ok 0x802ac222
   SOS probe: call, DRAM at 0x3fcb0100
   SOS probe: refused cause=0x00000002 vaddr=0x3fcb0100
   SOS probe: word load, instruction window at F
   SOS probe: ok 0x802ac222
   SOS probe: byte load, instruction window at F + 1
   SOS probe: refused cause=0x00000003 vaddr=F + 1
   SOS probe: word load, data window at R
   SOS probe: ok 0x4e475253
   SOS probe: byte load, data window at R + 1
   SOS probe: ok 0x00000052
   SOS probe: user literal, word store and load, process IRAM at 0x403a0108
   SOS probe: ok 0x3fcb0400
   SOS probe: entering user mode at 0x403a010c, byte access at 0x3fcb0400
   SOS probe: every line after the console handover is from user mode
   SOS: console handover
   SOS-U K
   SOS probe: done
   SOS-S3: halt pass
   ```

   A `payload copied` step's `ok` is the copy's first word, loaded back, and
   a `call` step's is what the copied routine returned. The emulator prints
   the same lines but for the part's six refusals, which it answers `ok`
   (`0x000000a5`, `0x00005a5a`, `0x5a5a96a5`, `0xc33c96a5`, `0x0000102a`,
   `0x000000c2`), and its alias steps, which read back `ok 0x0f1e2d3c` and
   end `not shared`; the smoke's `bus_probe` case asserts that text.

   Another revision of the chip, or another ROM, may answer differently. The
   addresses stay the same, so compare line by line: a step that answers
   `ok` here and is refused there, or the reverse, names the access that
   differs, and `cause` says how (`ABI.md` §3c has this part's table and
   §11 what each answer means for the process layout). A difference in the
   alias steps, or a refusal in the user-mode step, matters most: a layout
   with code at IRAM addresses and data at their DRAM names rests on them.
5. **`root_server_boot`**: the first real process. Expected, if `bus_probe`
   passed every step:

   ```
   SOS M1: kernel up on xtensa (ESP32-S3)
   SOS: root image ok segments=0x00000002 entry=0x403a043c prio=0x01010100
   SOS: console handover
   SOS root: hello from U-mode via a System op
   SOS root: boot handle 1
   SOS-S3: halt pass
   ```

   On the rev v0.2 board this stage stops in the loader instead, at its first
   byte store into root's region:

   ```
   SOS: root image ok segments=0x00000002 entry=0x403a043c prio=0x01010100
   SOS-S3: kernel fault vector=0x00000300 exccause=0x00000003
     epc1=0x42011863 excvaddr=0x403a0000 depc=0x00000000
   ```

   (one line on the console; `epc1` moves with the code). The part refuses a
   byte store at an IRAM address, and the layout places every process
   segment at IRAM addresses (`ABI.md` §11, §13).

### If you see X, it means Y

| you see | it means |
|---|---|
| nothing at all, not even `ESP-ROM:` | the wrong port (try the other `/dev/cu.*`), the wrong baud (it is 115200), the monitor holding the chip in reset (keep `--dtr 0 --rts 0`), or a board whose UART0 is not on this USB port. Press RST with the monitor open. |
| `ESP-ROM:` and the `rst:` line, then nothing | the ROM did not enter the image, or the image stopped before its first line. Check step 1, then confirm the flash holds the image: `python3 -m esptool --chip esp32s3 --port PORT verify-flash 0x0 sos-boot.bin`. |
| the banner over and over, the `rst:` line naming `TG0WDT_SYS_RST`, `RTCWDT_RTC_RST`, `RTCWDT_SYS_RST`, `SUPER_WDT_RST` or another `…WDT…` reset | a watchdog reset the chip: the disable in `_start` did not take. Send the `rst:` line. |
| `invalid header: 0xffffffff` | the ROM looked for an esptool image instead of direct-booting: one of step 1's eFuses forbids direct boot. |
| `invalid header:` with another value | the flash at 0x0 holds something other than our image; write it again at 0x0. |
| `Direct boot returned` | the kernel's entry returned to the ROM: a kernel bug. Send everything. |
| `boot:0x… (DOWNLOAD(USB/UART0))` or `waiting for download` | the chip is in download mode: release BOOT and press RST. |
| `SOS-S3: kernel fault vector=… exccause=… epc1=… excvaddr=…` | the kernel trapped. Send the line exactly; `exccause=0x00000003` with `excvaddr` in 0x403a… is a store the bus refused. |
| garbage characters | a baud mismatch: the monitor is not at 115200, or this part's UART clock differs from what the ROM's divisor assumes. Run the monitor without `--raw` and with `--encoding hexlify`, and send the bytes. |
