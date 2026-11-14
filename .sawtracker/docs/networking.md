# SO-103: networking on the ESP32-S3 (design, revision 2)

Scope: an architecture for networking in sawos (processes, what each holds,
the protocols between them), and a feasibility study of Espressif's closed
radio libraries on the ESP32-S3. Nothing is built from this before the owner
and the reviewer have read it. Revision 2 answers the review's five threads
(§12) and the coordinator's inputs on per-process limits (SO-105).

Conventions. "Verified" means I inspected a file or ran a tool, and §11.3
says which. "Read" means taken from a source document, cited. "Believed"
means neither, and says so. Every library fact names its commit:

```
esp-idf          v6.1      fff9895c82d744c7237be8847347bdd1b07c6643
esp32-wifi-lib   (IDF's)   7cd4d7b2b4acc38aead43b029ac9409c3eba9c54
esp-phy-lib      (IDF's)   5695f4f38108658bc4a33e4712c1ebcb34911434
esp-coex-lib     (IDF's)   ddc56fa524a38df3098d2f36475fbe3eefe9b4f4
esp32c3-bt-lib   (IDF's)   0a08c4b32f3666003080b662a1a61794da24ff0f
esp-lwip         (IDF's)   c6f2f878e7b0f86033214b85547d579be43351e3
esp-nimble       (IDF's)   139cada0ae932957fa06ba37d17e3c9c2c95c773
esp-hal/esp-radio  main    c72d6805fce0764bbe97d1e97a47896c6d4744bf
apache/nuttx       master  5558477c90234b7c82d233c32096d47d5f069858
mask ROM ELF     esp32s3_rev0_rom.elf (esp-rom-elfs 20241011, on this machine)
```

The library commits are the ones IDF v6.1 pins as submodules, so the
libraries and the IDF glue that drives them are one tested set. The local
IDF checkout under `~/Projects/esp32` was not readable from the sandbox;
everything was fetched from Espressif's public repositories (§11.3).

Where this design depends on others: SO-95 (images and packaging) as the
owner ruled it, at revision 5: a 32-entry handle table on every board,
quota defaults per board, the quota door (`ProcessOp.QuotaSet`); SO-102
(PSRAM, revision 1); SO-77 (GPIO and PWM, its stage 0); and SO-105
(per-process limits), which is not yet designed. What this design asks of
each is listed in §5.6.

---

## 1. Verdict

**Go, with conditions.** Wrapping Espressif's Wi-Fi, PHY, coexistence and
BLE controller libraries on the ESP32-S3 is feasible in sawos; nothing
found in the files rules it out. The conditions, each a unit in §10:

1. **Windowed processes.** The libraries are windowed (`ABI=0` in all 362
   S3 objects; 0 `ret` and 0 `callx0`, §2.2). The radio process is a
   windowed program; `sawc --target-features +windowed` emits windowed Saw
   (verified on a probe), so no thunks are needed. The kernel needs window
   and alloca vectors, a 64-register frame and per-thread FP state (K1,
   K2). Windowed Saw is in neither repository's gate, so a windowed row in
   sawlang's freestanding suite comes before any Saw is relied on in the
   radio (W0, §3.3).
2. **The priority scheduler, with a ceiling.** Masking interrupts gave the
   libraries three things an interrupt thread and a lock do not: a handler
   that runs to completion before any task, a critical section that
   excludes every task and handler, and BT's handlers above Wi-Fi's. K3
   must provide strict levels inside the radio process and a priority
   ceiling for its critical sections (requirement 4.1, §4.3). No radio
   traffic runs before K3.
3. **The clock, with the memory-SPI bus.** Wi-Fi needs APB at 80 MHz, so
   the CPU moves to the PLL; on this part that also moves the flash's and
   the PSRAM's clocks. K4 makes the switch once, from IRAM, before
   `hal.memory_init()`, with the flash divider set for the new clock and
   both sides measured on the part (requirement 4.3, §4.5).
4. **Per-process numbers beyond the ruled defaults.** The radio process
   holds about 60 handles and 18 grant rows (§4.4). The handles need
   SO-105 (a table past 32 for one process); the rows need the radio
   board's grant-row budget raised from 16 to 20, which costs 80 bytes in
   every process body.
5. **Kernel SRAM.** A networking image's slabs and pipes, beside the box,
   overrun the S3 kernel range's measured 21.9 KiB of slack by about 5 KiB
   with Wi-Fi and about 15 KiB with BLE too (§4.6.1). The kernel stack's
   peak is measured and the range's split moved (K6).
6. **Process SRAM.** With root in SRAM, as SO-95 and SO-102 keep it, the
   box's pinned processes and the ROM's reserved range, the radio has
   about 96 KiB of internal SRAM. Wi-Fi at IDF's minimum rank, with its
   dynamic frame buffers in PSRAM, fits; IDF's default rank does not; Wi-Fi
   and BLE together beside the box fit only if root's computed region
   comes out at 64 KiB or less (§4.6.2).

**The stacks:** TCP/IP in Saw and the BLE host in Saw, both gated on the
emulators before the radio exists; lwIP (verified to compile call0 for
this core) and NimBLE are the fallbacks (§6). **The first radio belongs
on the S3:** the board is in hand and the S3-only cost is K1, K2 and W0
(§8).

---

## 2. The radio libraries as they are

### 2.1 Files and sizes

The ESP32-S3 directories of the four library repositories: archive sizes,
and the sum of allocatable sections per library from `llvm-readelf -S`
over every member, before `--gc-sections` (so an upper bound):

```
archive (bytes)             objs  alloc   .text  .rodata  data+bss  RAM-code*
wifi  libcore.a        4848    1     401     308      56        37       0
wifi  libnet80211.a 1455290   50  253310  195022   42159      9160    6969
wifi  libpp.a        706172   30   98699   55095    6542      4503   32559
wifi  libmesh.a      995216   15  223478  179658   39664      4156       0
wifi  libespnow.a     66220    2   12035    9930    2030        75       0
wifi  libsmartconfig 197726    8   44338   38067    5574       697       0
wifi  libwapi.a       52394    3   10916    8103    2805         8       0
phy   libphy.a       257184   22   52063   43020    2769       744    5530
phy   libbtbb.a       23346    1    4835    4365     153        16     301
phy   librftest.a    261298    7   55988   46789    8950       249       0
phy   libbttestmode  229768    8   45680   41674    3602       404       0
coex  libcoexist.a   109824   11    9749    6446    1751       760     792
bt    libbtdm_app.a 1543778  102  277512  235499   13545      1998   26470
bt    libbtdm_app_flash 1794170 102 289275 270476  15051      2170    1578
```

`*` RAM-code is every section IDF's fragments may place in IRAM: `.iram1`,
`.wifi0iram`, `.wifirxiram`, `.wifislprxiram`, `.wifislpiram`,
`.wifiorslpiram`, `.wifiextrairam`, `.coexiram`. "data+bss" is `.data`,
`.bss` and `.dram1`.

A station needs `libcore`, `libnet80211`, `libpp`, `libphy`, `libcoexist`.
Mesh, ESP-NOW, smartconfig, WAPI, RF test and BT test mode are optional.
BLE needs `libbtdm_app` (or its `_flash` variant) and `libbtbb`. All four
repositories are Apache-2.0 (each one's `LICENSE`), which permits
redistributing the archives inside our image.

### 2.2 The calling convention: windowed, by three independent signs

1. **The ELF says so.** Every object in the S3 directories, 362 of them,
   carries `.xtensa.info` reading `USE_ABSOLUTE_LITERALS=0 ABI=0`. ABI=0 is
   the windowed ABI (call0 objects say `ABI=2`). The ELF header flags,
   `0x300` (`EF_XTENSA_XT_INSN`, `EF_XTENSA_XT_LIT`), say nothing about it.
2. **The code says so.** GNU `objdump -d` (xtensa-esp-elf 15.2.0):

   ```
   library          entry   retw.n   ret/ret.n  call0/callx0  call4/8/12
   libnet80211       1206     1206       0          0           4991
   libpp              917      916       0          0           1842
   libphy             289      310       0          0           1115
   libcoexist          95       95       0          0            158
   libcore              4        4       0          0             10
   libbtdm_app       1518     1516       0          0           6814
   libbtbb             32       34       0          0             29
   ```

   The other libraries show the same: no `ret`, no `callx0`.
3. **Every other integrator is windowed.** esp-radio links the same
   archives under Rust's windowed Xtensa target; NuttX carries
   `xtensa_windowspill.S`; IDF's FreeRTOS port is windowed. Each
   repository has one archive set per chip.

### 2.3 What else the code uses

- **No privileged or interrupt-control instruction** in any library except
  `libwapi`'s loop-register writes and one `isync`. No `rsil`, `waiti`,
  `wsr intenable`, `rfe`, `s32c1i` or `ee.*` anywhere: every mask and
  every critical section goes through the adapter tables.
- **Zero-overhead loops** in libpp, libnet80211, libphy, libbtdm_app;
  sawos already saves `LBEG`/`LEND`/`LCOUNT` per thread (ABI.md §7c).
- **Floating point:** 121 FP instructions in libpp, 42 in libmesh, 2 in
  librftest; **boolean registers** in libpp (8).
- **`movsp`** in libpp, librftest, libbttestmode: the Alloca exception.
- No MAC16 instruction.

### 2.4 Undefined symbols, grouped by who supplies them

Union of undefined minus union of defined, each name looked up in IDF
v6.1's ROM scripts (`scripts/undef.py`; lists in `an/undef-*.txt`):

```
set                         total  ROM fn  ROM RAM  ROM rodata libc libgcc other
Wi-Fi (core,net80211,pp,
       phy,coexist)          153     11       60        3       9     16     54
BLE (btdm_app,btbb,phy,
       coexist)              202     62       80       42       6      6      6
both                         325     67      134       45       9     17     53
```

1. **The OS adapter tables, reached through fixed ROM RAM.** The libraries
   load table pointers from addresses the ROM reserves: `g_osi_funcs_p`
   (0x3FCE_F940), `g_coa_funcs_p` (0x3FCE_F824) and about 130 more words
   between 0x3FCE_F820 and 0x3FCE_FFA8. The tables are IDF source:
   `wifi_osi_funcs_t` (version 9; 119 initialisers in
   `esp_wifi/esp32s3/esp_adapter.c`), the coexistence adapter (18), the BT
   controller's `osi_funcs_t` (`bt/controller/esp32c3/bt.c`, 63).
2. **ROM code called directly:** 11 functions for Wi-Fi
   (`phy_get_romfuncs`, `rom_phy_param_addr`, `ets_delay_us`, ...), 62 for
   BLE, whose link layer is mostly mask ROM patched by `libbtdm_app`.
3. **ROM copies of library functions.** `esp32s3.rom.ld` assigns ROM
   addresses to functions the libraries also define (`ppEnqueueRxq`,
   `lmacRxDone`); IDF always links it. Whether `ld.lld` resolves these as
   IDF's GNU ld does is not checked (R1).
4. **The C library:** nine string functions in ROM, plus `free`, `puts`,
   `sprintf`; libgcc's helpers in ROM.
5. **IDF glue the libraries name:** `WIFI_EVENT`, the regulatory tables,
   `phy_enter_critical`/`phy_exit_critical`, the `*_printf` hooks.
6. **Glue they need but do not name:** PHY bring-up (`phy_init.c`), PHY
   init data (`phy_init_data.c`), Wi-Fi init, the modem clock and reset
   code, the eFuse MAC read, timers, the event loop, NVS, and for WPA2 the
   supplicant (`components/wpa_supplicant`, hostap code, BSD) with crypto.

### 2.5 Sections that must live somewhere particular

- **The ROM's RAM.** The ROM's Wi-Fi, PHY, coexistence and BT code keeps
  state in DRAM it reserves (`.data_phyrom`, 0x298 bytes at 0x3FCE_F3D4;
  `.bss.interface.bluetooth`, 0x598 at 0x3FCE_FA14; the table words). The
  ROM ELF's `_dram0_rtos_reserved_start` is **0x3FCE_EE34**, and IDF
  reserves `ets_rom_layout_p->dram0_rtos_reserved_start` to 0x3FCF_0000
  for the application's life (`heap/port/memory_layout_utils.c`
  l.58-67). **Rule 2.1:** this range is a fact of the ESP32-S3 board
  description, on every S3 board, for every image: a RAM range of its own
  (`use = "rom"`, §5.5), excluded from the `sram` process range, written
  by nobody, and granted only to a process that names it. The S3's `sram`
  range therefore ends at 0x3FCE_EE34 for the box too (the S3's protection
  grain is 4 bytes, `hal.PROT_GRAIN`, so no rounding is needed).
- **IRAM is a speed choice here.** IDF places `.iram1` and `.wifi*iram`
  in IRAM because it disables the flash cache while writing flash, and for
  speed; its own default scheme sends the `.wifi*iram` sections to
  `flash_text` (`esp_wifi/linker.lf`), and documents the IRAM option as a
  throughput option of about 15 kB. sawos never writes flash at run time,
  and no library names a cache or SPI-flash ROM function. **Rule 2.2:** the
  radio's code runs in place from flash like every process's.
- **DMA-reachable data is internal SRAM:** the adapter's `*_internal`
  allocations ask for `MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL`
  (`esp_adapter.c` l.412-426), queue storage is internal (l.116-124),
  static RX buffers are DMA memory (`overview.rst` l.1249). The
  `_wifi_malloc` family may take PSRAM on this part when IDF's
  `SPIRAM_TRY_ALLOCATE_WIFI_LWIP` is set (l.71-104): IDF lets the
  dynamic frame buffers live in PSRAM.
- **Interrupt context:** IDF registers the Wi-Fi MAC handler at CPU
  interrupt 0, level 1 (`soc.h` l.212; ABI.md §5d), and the BT
  controller's sources at level 3 from IRAM (`bt.c` l.875-877).

### 2.6 eFuses and non-volatile storage

**eFuses: reads only, as far as a binary can show.** A scan of every word
of every PROGBITS section (`scripts/mmio.py`, `scripts/words.py`) finds
four eFuse addresses, all in libphy, all read registers (`RD_MAC_SPI_SYS_0`,
`_1`, `_3`, `RD_SYS_PART2_DATA0`), each used by `l32r`, `memw`, `l32i`.
No write register, command register, op code or block base occurs. In the
ROM, only `ets_efuse_program` and `ets_efuse_read` load the command
registers, and `ets_efuse_program` is reached only from
`ets_efuse_write_key`, `ets_secure_boot_revoke_public_key_digest` and the
`__call_` stubs, none of which a library names. A computed address or a
call through a pointer is not excluded by a literal scan.

**Non-volatile storage: none needed.** The libraries reach NVS only
through `_nvs_*` table entries, and `nvs_enable = 0` keeps the Wi-Fi
configuration out of it (`overview.rst` l.1294). With
`ESP_PHY_CALIBRATION_AND_DATA_STORAGE` off, `phy_init.c` does a full
calibration and touches no NVS (l.946-971), at a cost of "100 ms longer"
per boot (`RF_calibration.rst` l.34). **Rule 2.3:** the radio's `_nvs_*`
entries keep values in RAM, never flash, and print a line on the first
write. No library names the SPI flash controllers (no word-aligned
address in 0x6000_2000-0x6000_3FFF occurs).

### 2.7 What the libraries touch directly

Peripheral pages whose addresses occur as literal words (counts are
occurrences; a count of 1 may be a coincidental constant):

```
libpp        0x6003_3000 (228)  0x6003_4000 (42)  0x6003_5000 (163)  Wi-Fi MAC
libnet80211  0x6003_5000 (29)
libphy       0x6000_6000 FE (130)  0x6001_C000 NRX/BB (100)
             0x6000_E000 analog I2C master (61)  0x6000_8000 RTC_CNTL (13)
             0x6001_D000 BB (9)  0x6002_6000 APB_CTRL (7)  eFuse reads (4)
             0x600C_0000 SYSTEM (1)
libbtbb      0x6001_1000 BT (43)  0x6001_C000 (22)  0x6000_E000 (4)
libbtdm_app  0x6003_1000 (207)  0x6001_1000 BT (31)  0x6004_2000 (15)
             0x600C_0000 SYSTEM (2)
libcoexist   0x6003_5000 (9)  0x6003_3000 (2)
```

Names from IDF's `reg_base.h`. The addresses are absolute in the code, so
the radio's windows sit at their physical addresses on any tier, and the
libraries' own writes to RTC_CNTL, APB_CTRL and SYSTEM cannot be routed
through a broker: the windows on those shared blocks stay (§12, t3).

---

## 3. The calling-convention question

### 3.1 The options, priced

**(a) The radio process is a windowed program.** Everything linked into it
is compiled windowed: the archives, our adapter in C, the syscall stub,
`sosrt`'s C support, and Saw. Verified on a probe:

```
sawc build -O2 -c --freestanding --target xtensa-esp-unknown-elf \
     --target-features +windowed x.saw
probe_side:  entry a1, 32 ... l32r a8, <c_side> ; callx8 a8 ... retw.n
(without the flag:  addi a1, a1, -16 ... callx0 a8 ... ret.n)
```

Blade passes a manifest's `target-features` to both halves of an image
after the triple's `-windowed` (`blade/src/sosimg.saw`,
`native_compile_arguments`), so a package's
`[sos.xtensa-esp-unknown-elf] target-features = "+windowed"` is believed
to build it whole; R1 confirms. The helper libraries Espressif ships for
this core, and the ROM's libc, are windowed (ABI.md §1d).

What the kernel needs (ABI.md §7d lists the shape):

1. the six window vectors become the standard spill and fill handlers
   (Tensilica's, in NuttX and FreeRTOS under MIT); they run in user
   context with EXCM set and need no kernel entry;
2. the Alloca exception (cause 5, `movsp`);
3. the trap frame: save the current window as today, then `rotw 4` three
   times to save the other 48 physical registers, plus `WINDOWBASE` and
   `WINDOWSTART`; never write the user's stack from kernel code. The frame
   grows from 104 to about 304 bytes, inside the 512-byte
   `THREAD_FRAME_STRIDE` that exists today;
4. a windowed thread start (`PS.WOE`, `CALLINC`, one `WINDOWSTART` bit,
   the base save area);
5. a double exception whose `DEPC` lies in the window vectors is the
   process's fault, not a kernel bug;
6. every S3 thread takes the 64-register path (simplest; call0 processes
   are unchanged in behaviour).

Price: one medium unit (K1), gated in the S3 smoke by cases that force
overflow and underflow at every call size, alloca, a tick inside a deep
windowed chain, two windowed threads switching, and a windowed and a
call0 process side by side; then board stage B2.

**(b) Thunks** between call0 and windowed code: (a)'s kernel work plus a
thunk for every library entry and every function the libraries call
(about 200 table entries, libc, logging). **Rejected.**

**(c) Call0 libraries:** Espressif publishes none, and the ROM the
libraries call is windowed regardless. **Rejected as a plan;** asking
costs nothing (question 6).

**(d) Converting the objects to call0:** re-allocating registers in every
function of closed code, per release, while the ROM stays windowed. Not
practical. **Rejected.**

**(e) The radio process all C, Saw behind a pipe:** no longer forced, and
the fallback if W0 shows windowed Saw unsound. **The design takes (a).**

### 3.2 The rest of the thread state

- **FP and `BR`.** libpp executes FP instructions and boolean branches, so
  a radio thread needs `CPENABLE` bit 0, and a switch carries `f0`-`f15`,
  `FCR`, `FSR` and `BR` (19 words), lazily on the cause-32 trap, as spec
  §7 already describes for Profile B (K2). It fits the 512-byte stride
  beside the windowed frame.
- **Word atomics:** the libraries use none.

### 3.3 Windowed Saw has no gate yet

The probe above is the only windowed Saw anyone has compiled. sawlang's
freestanding suite boots its Xtensa row call0
(`tools/freestanding_runner.py`, `XTENSA_FEATURES = ("-windowed", ...)`),
and sawos's gate has no windowed process. **Rule 3.1:** no Saw is compiled
windowed for the radio process until (i) sawlang's freestanding suite has
a windowed Xtensa row that runs, under the emulator, the programs its
call0 row runs (request W0, filed by the lead with the sawlang lead,
beside the Blade gap of §5.6), and (ii) K1's smoke cases include windowed
Saw. Until both, the radio process is C (option e), which the adapter is
anyway.

---

## 4. What the libraries need from an operating system

### 4.1 The adapter, group by group

The radio process implements the three tables in C, compiled windowed, over
sawos objects:

```
group (table entries)           sawos realisation
tasks: create, delete, delay,   a thread per task, its stack from the
  yield, current, max prio      process's internal arena; its level from
                                the requested FreeRTOS priority (§4.3)
semaphores, mutexes, queues,    process-local objects; a blocked thread
  event groups, thread sem      parks on its own Event through its Waiter
timeouts on any of the above    the timer thread signals the parked thread
                                with a timeout mark; one kernel Timer
software timers (setfn, arm,    a deadline list; one sawos Timer at the
  arm_us, disarm, done)         earliest; the timer thread runs callbacks,
                                as IDF's esp_timer task does
time                            a Clock read, microseconds
memory: malloc, *_internal,     two arenas: the process's region (internal,
  wifi_malloc, free heap        DMA-capable) and a PSRAM pool (§4.4)
critical sections               the ceiling of §4.3
interrupts: set_intr, set_isr,  root binds the lines and gives the
  ints_on/off, is_from_isr,     Interrupts; interrupt threads run the
  BT interrupt_alloc/enable     registered handlers (§4.3)
event post (WIFI_EVENT ...)     a queue the serve thread drains into
                                protocol notifications (§5)
random                          WDEV_RND_REG (0x6003_507C) in the MAC window
read_mac                        eFuse RD_MAC_SPI_SYS_0/1 reads
logging                         a small vsnprintf; out through debug_print
storage: nvs_*                  RAM only (rule 2.3)
phy enable, clocks, reset MAC   IDF's esp_phy and modem-clock code, ported
coex hooks; BT sleep hooks      IDF's coex adapter; BT modem sleep off
IPC to the other core           unused: single core, IDF's UNICORE paths
```

### 4.2 Userspace RTOS objects

The libraries never block in a kernel sense; they call `_semphr_take` and
friends, and our implementation decides what blocking means. A process-
local object with a waiter list and one Event per thread gives FreeRTOS's
semantics; esp-radio does the same over its own scheduler. What the
objects cannot give alone is the ordering of §4.3.

### 4.3 Interrupt handlers and critical sections

**What the libraries assume, from the adapter entries and the IDF code
behind them.**

1. **A critical section masks every interrupt at or below level 3 and
   every task switch.** `_wifi_int_disable`, the BT `_global_intr_disable`
   and the coexistence lock all end in `portENTER_CRITICAL`
   (`esp_coex_adapter.c` l.58-67; `bt.c` l.905-921), and IDF's Xtensa
   port implements it by raising `INTLEVEL` to `XCHAL_EXCM_LEVEL`
   (`freertos/.../xtensa/port.c` l.116-126, `XTOS_SET_INTLEVEL(
   XCHAL_EXCM_LEVEL)`), which is 3 on this core (ABI.md §5d). The Wi-Fi
   handler (level 1), the BT handlers (level 3) and the tick (level 1) are
   all masked: nothing else of the radio runs until the section ends.
2. **A handler runs to completion before any task.** IDF's vectors run a
   level-1 handler at `PS.INTLEVEL(1)` and a level-3 one at
   `PS.INTLEVEL(3)` (`xtensa/xtensa_vectors.S` l.1255 and l.1424), on the
   interrupt stack; no task runs until it returns.
3. **BT's handlers preempt Wi-Fi's**, and nothing preempts BT's below
   level 4 (point 2's levels).
4. **A task woken from a handler runs when the handler returns,** if its
   priority is higher: `_task_yield_from_isr` is `portYIELD_FROM_ISR`
   (`bt.c` l.923-926).
5. **Tasks are ordered by priority.** The BT controller task is
   `ESP_TASK_BT_CONTROLLER_PRIO`, `configMAX_PRIORITIES - 2`, and IDF's
   timer task `- 3` (`esp_task.h` l.32, l.46); the Wi-Fi task's priority is
   chosen inside the library relative to `_task_get_max_priority`. Not
   verified: whether the libraries depend on these orders. R2 logs every
   priority the libraries request.
6. **`_ints_off(mask)` disables one CPU interrupt** (`esp_cpu_intr_disable`)
   and does not stop tasks; `_is_from_isr` is asked to choose the `FromISR`
   forms, which never block.

The Wi-Fi handler itself (`wDev_ProcessFiq`, `libpp/wdev.o`, 98 lines of
disassembly) reads and clears the MAC's status and defers through
`pp_post` (`queue_send_from_isr`) and the `lmac*` routines.

**What sawos gives with an interrupt thread and a lock alone** (revision
1's "ISR gate"): an interrupt masks its line and wakes the thread waiting
on it (spec §9); the interrupt thread takes the lock, runs the handler,
releases, acks. That gives point 1 only between handlers and lock-takers.
It does not give point 2 (the scheduler may leave the interrupt thread
half way through a handler and run a task that reads what it was
writing, without the lock), nor the rest of point 1 (a higher radio
thread, woken meanwhile, preempts the lock-holder and runs), nor point 3
(one lock makes a BT handler wait for a whole Wi-Fi handler). Every
integrator cited masks interrupts and runs handlers as interrupts, so
deferral is unproven by precedent in all three respects (§12, t1,
accepted).

**What sawos needs. Requirement 4.1, on the scheduler unit (K3):**

1. **Levels inside one process.** The radio process holds, from highest
   to lowest: the ceiling level C; the BT interrupt thread's level; the
   Wi-Fi interrupt thread's level; then its task threads, ordered as their
   requested FreeRTOS priorities order them (equal priorities, equal
   levels). That is at least four distinct levels, and up to seven if the
   task priorities differ as IDF's do (C, two interrupt levels, BT
   controller, timer, Wi-Fi task, serve). K3 must give a driver process
   that many levels, all above every application thread; spec §7's
   four-band map per process is short of seven, so K3 either lets a
   driver's map name more levels or the task threads share one level
   (believed acceptable; R2's log decides).
2. **Strict priority with preemption on wake,** round-robin only within
   one level, no time slice across levels. Then a handler, whose thread
   is above every task of the process, runs to completion before any of
   them (point 2); a BT handler preempts a Wi-Fi handler (point 3); a
   task woken by a handler runs when the handler's thread blocks again
   (point 4).
3. **A priority ceiling for critical sections.** A thread that enters a
   radio critical section runs at C until it leaves, so no other thread of
   the radio, handler or task, runs while it holds the section (point 1).
   It is the immediate-ceiling protocol; it needs no inheritance, because
   every thread that takes the section is at or below C. Entering and
   leaving happen on every critical section the libraries take (several
   per frame), so the cost matters. K3 chooses between:
   - **a syscall pair** (raise to C, restore): simple, two traps per
     section; or
   - **deferred preemption**: the thread sets a word in its own memory
     the kernel reads at a reschedule decision; while it is set, the
     kernel does not switch to another thread of the same process, and
     sets a "switch pending" word instead; on leaving, the thread checks
     that word and yields if set. No trap on the common path. Threads of
     other processes are unaffected, which is what masking gave the radio
     with respect to its own state (other processes touch none of it).
   Either is a level the process's own map already grants, so it does
   not let a process escape its bands (spec §7's "the map is immutable"
   is unaffected).
4. **`_ints_off(mask)`** is a flag per line in the process: an interrupt
   thread whose line is off waits for `_ints_on` before running the
   handler. No kernel change.
5. **Which thread holds what.** The BT interrupt thread holds the
   Interrupts for sources 5 (`BT_BB`) and 8 (`RWBLE`) on one Waiter; the
   Wi-Fi interrupt thread holds sources 0 (`WIFI_MAC`) and 2 (`WIFI_PWR`);
   the timer thread holds the Timer; the serve thread (the main thread)
   holds the three outlets and the event queue's Event; each library task
   holds only its own Event and Waiter.
6. **Latency.** With 1 to 5, a line reaches its handler after a trap, the
   delivery funnel and one switch, plus at most one `PREEMPT_STRIDE` of a
   long kernel copy. Whether that meets the libraries' deadlines is
   measured at B3 (requirement 4.2).

**Requirement 4.2:** a diagnostic kernel build records, per bound line,
the SYSTIMER time of the fire and of the bound thread's resumption, and
R3 prints the worst case per line. Nothing about latency is concluded
before that number exists.

**Rule 4.3:** before K3 exists, the only radio run allowed is R3's PHY
calibration, which runs in one thread with no radio line bound. Wi-Fi and
BLE traffic (R4 onward, B-1 onward) wait for K3.

### 4.4 The radio process's own numbers

Counted from the design below (§4.1, §4.3, §5), for one process serving
Wi-Fi and BLE, on the PSRAM board. A Wi-Fi-only image is shown beside it.

**Boot set** (SO-95 rule 4.2 kinds):

```
record                                      both    Wi-Fi only
system (a masked sibling)                     1        1
windows: WIFI_MAC, RF_FE, WIFI_BB, ANA_I2C,
  EFUSE, RTC_CNTL, APB_CTRL, SYSTEM           8        8
windows: BT_BB, BLE_CORE                      2        -
lines: WIFI_MAC's mac (0) and pwr (2)         2        2
lines: BT_BB (5), BLE_CORE (8)                2        -
memory: the ROM's range (rule 2.1)            1        1
memory: its PSRAM pool                        1        1
serve: netdev, wlan                           2        2
serve: hci                                    1        -
total                                        20       15
```

**Handles held, steady and peak:**

```
what                                        both    Wi-Fi only
the boot set                                  20       15
its Process handle (ProcessSelf)               1        1
Mappings: windows                             10        8
Mappings: ROM range, pool allocation           2        2
Mappings: rings (netdev; hci)                  2        1
Threads created (Wi-Fi ISR, BT ISR, timer,
  Wi-Fi task, BT task; main not counted)       5        3
Events, one per thread                         6        4
Waiters, one per thread                        6        4
Timer, Clock                                   2        2
PipeRequests held (each WaitEvent; one call)   4        3
steady                                        58       43
transient (a ring's Memory before release;
  a request taken and not yet answered)       +2       +2
peak                                          60       45
```

Each task the libraries create beyond the two assumed adds a thread, an
Event and a Waiter: three handles. R1 and R2 log the true count. Releasing
each window's IoMemory handle once its Mapping exists (the Mapping holds
the row, spec §2.5) would take 11 off the steady count (47 and 35); the
design keeps them, as SO-95's handle floor (rule 4.3) counts them, and
the numbers above are the honest need either way: above 32.

**Its declaration's quotas** (what `process.toml` states, rule 4.3):

```
quota                 both   Wi-Fi only   how derived
handles                 64       48       peak, rounded up
mappings                14       11       windows + ROM + pool + rings
threads                  6        4       main, 2 or 1 ISR, timer, tasks
events                   6        4       one per thread
waiters                  6        4       one per thread
timers                   1        1       the timer thread's
pipes                    0        0       it creates none; root wires them
memories                 1        1       the PSRAM pool allocation
iomemories               0        0       it carves nothing
```

**Grant rows: 18 (both) or 15 (Wi-Fi only):** its mappings plus
`IMAGE_GRANT_ROWS` (4: three segments and the stack, `kcore.limits`).
Whether rows can share: each window is one board device (rule 3.3 of
SO-95); RF_FE already joins FE2 and FE (0x6000_5000-0x6000_6FFF), and
WIFI_BB joins NRX and BB (0x6001_C000-0x6001_DFFF). FE, EFUSE and RTC_CNTL
are contiguous, but EFUSE and RTC_CNTL are separate devices other
processes and the kernel touch, so merging them would bind more than the
radio's. The two rings come from two clients' allocations, and the ROM
range and the pool are in different RAM. So no further sharing.

**RAM it allocates:** its region (internal SRAM, `ram = "fast"`): libraries'
and glue's data, its stack, and an arena holding the internal allocations
and the task stacks (§4.6.2); and a PSRAM pool for the dynamic frame
buffers, 32 KiB with Wi-Fi alone and 40 KiB with BLE too (IDF's minimum
rank peak of (6 + 6) frame buffers of about 1.6 KiB, plus BLE's ACL
buffers, rounded up; believed).

**Rights it needs, per kind** (names from `kernel/abi/src/rights.saw`):

```
kind           rights                               not given
System         ClockGet, ProcessSelf, Debug          everything else
Process (self) ThreadCreate, EventCreate,            Give, Kill, Start,
               WaiterCreate, Map, Exit               PipeCreate, Stats
Thread         Start                                 Join, Control
IoMemory       Map                                   Carve, Transfer
Interrupt      Wait, Ack                             Transfer
Memory         Map (ROM range, rings)                MapExecute, Transfer
Pool           Allocate, Map                         MapExecute, Transfer
Clock          Read, TimerCreate                     Transfer
Timer          Arm, Wait
Event          Signal, Receive, Wait
Waiter         Attach, Wait
PipeOutlet     Take, Wait                            Transfer
PipeRequest    Reply, Wait                           Sender
Mapping        Unmap, Base                           Transfer
```

**What SO-95 as ruled gives, and what it does not:**

```
need                         as ruled (SO-95 rev 5)        needs
threads, events, waiters,    yes: QuotaSet within the
  timers, mappings <= 12       board's slab totals
mappings 14 (both)           no: quota <= budget - 4 = 12   board budget 20
                                                             (or >= 18)
grant rows 18 (both)         no: budget 16                  board budget 20
grant rows 15 (Wi-Fi only)   yes
handles 48 or 64             no: 32-entry table             SO-105
a pool of its own            image [[pool]] and a memory    SO-105 (the pool
                               record exist (rules 4.2,       as its RAM limit,
                               5.2); not stated as a limit    given to it alone)
rights narrowed per kind     root's own choice today        SO-105 (rights
                                                              from the
                                                              declaration)
4 interrupt lines            slab floor 4, machine-wide;    a per-board slab
                               not in SO-95's table           total (§5.6)
```

**Requirements on SO-105** (so its design starts from them):

1. A process may hold more than 32 handles when its launcher sets its
   `Handle` quota higher: the radio needs 64 (48 for Wi-Fi only), root of
   a networking image about 48 (§4.6.1), every other process 32 or fewer.
   Storage for the excess comes from outside the 32-entry inline table,
   so other bodies do not grow.
2. A pool given to one process, by the image, as that process's limit on
   what it allocates (the radio's PSRAM pool).
3. Rights narrowed to the declaration: the table above becomes the
   radio's `process.toml`, and the launcher gives no right outside it.

**Cost in kernel SRAM on the ESP32-S3** (SO-95 §6.3 and §6.4's figures: a
handle 32 bytes, a grant row 20 bytes, a body 1436 bytes, a thread 556
including its frame, an event 24, a Waiter 20, a timer 48, an attachment
24, a mapping slot 20, a Memory 56, an IoMemory 20, a pipe connection
3.5 KiB; an Interrupt slot is about 16 bytes from its five fields in
`waitables.saw`, not measured):

- *Per process, the radio's own,* if SO-105 adds storage only for the
  excess: (64 - 32) x 32 = 1024 bytes (512 for Wi-Fi only).
- *Per board, every body:* grant-row budget 16 to 20, +4 x 20 = 80 bytes a
  body. (A board-wide handle table of 64 instead of SO-105 would be +1024
  bytes in every body: 8 KiB across the box's eight.)
- *Image totals the radio accounts for:* threads 6 x 556 = 3336; events
  6 x 24 = 144; Waiters 6 x 20 = 120; timer 48; Interrupts 4 x ~16 = 64;
  attachments (6 + 4 + 1) x 24 = 264; mapping slots 14 x 20 = 280;
  IoMemories 10 x 20 = 200; Memories 4 x 56 = 224; and the three pipe
  connections it serves, 3 x 3.5 KiB = 10.5 KiB, of which the pipes are
  two thirds. About 15.6 KiB with BLE, 11.6 KiB without.

### 4.5 Clocks, the PLL and the memory-SPI bus

**Why the clock moves.** IDF creates the Wi-Fi power lock as
`ESP_PM_APB_FREQ_MAX` unless `SOC_MODEM_APB_CLOCK_IS_INDEPENDENT`
(`wifi_init.c` l.497-502), and the S3's `soc_caps.h` does not define it.
APB is 80 MHz only with the CPU on the PLL (SO-77 §2.6). The kernel today
runs on whatever the ROM left, never read on the part.

**Why the flash and the PSRAM move with it.** On this part the memory-SPI
clock source is bound to the CPU's: IDF's `mspi_ll.h` for the S3 defines
`MSPI_TIMING_LL_FLASH_CPU_CLK_SRC_BINDED 1` (l.57), and
`clk_utils.c` (l.31-75, v6.1; the reviewer read the same in v5.4.1)
brackets every switch of the CPU's source across the crystal's frequency
with `mspi_timing_change_speed_mode_cache_safe`: down to low-speed mode
before a switch to the crystal, up after a switch to the PLL. `pm_impl.c`
calls the pair around `rtc_clk_cpu_freq_set_config_fast` (l.681, l.699).
The change itself freezes the external-memory cache
(`SOC_CACHE_FREEZE_SUPPORTED` is 1 on the S3), sets SPI0's and SPI1's
flash and PSRAM clocks (low speed is 20 MHz for both,
`mspi_timing_get_psram_low_speed_freq_mhz`, l.498-523), and unfreezes
(`mspi_timing_tuning.c` l.563-621). So a divider chosen from the crystal's
clock doubles its bus speed when the PLL comes on, if the core clock is
the crystal's 40 MHz before (SO-102 §3 says what the core clock is on the
crystal is not in its sources; its stage P0 reads it).

**Requirement 4.3, on the clock unit (K4).** This does not design SO-102's
side; it states what K4 must do and in what order.

1. **Once, at boot, before `hal.memory_init()`.** K4 runs in `kmain` after
   the banner and before the PSRAM sequence, so SO-102's sequence sets its
   divider against the clock it will run at. If SO-102 lands first with
   its divider chosen from the boot clock, K4 also rewrites
   `SPI_MEM_SRAM_CLK_REG(0)` for the new core clock in the same sequence
   (and SO-102's designer is told which order was taken).
2. **The console first:** UART0's clock moved to the crystal (SO-77 §6),
   so its divisor holds across the switch.
3. **The flash divider before the switch:** SPI0's `CLOCK` set so the
   flash runs at no more than 20 MHz at the post-switch core clock (IDF's
   untuned low-speed figure). If the measured boot state is a 40 MHz core
   with the ROM's divide-by-4 (10 MHz, SO-102 §1.1 reads
   `CLOCK=0x00030103`), the post-switch speed is 20 MHz and K4 writes
   nothing to it; K4 decides from the measurement, not from this
   expectation.
4. **The switch as IDF makes it:** the BBPLL powered and calibrated over
   the analog I2C bus, the digital and RTC voltage bias set from their
   eFuse-calibrated values (reads only) for the target frequency, then
   `CPU_PER_CONF`, then `SOC_CLK_SEL`; 160 MHz, not 240 (which needs the
   higher bias). The external-memory cache frozen around the memory-SPI
   change, as `mspi_timing_change_speed_mode_cache_safe` does.
5. **From IRAM:** the code, its literals and its data in IRAM and DRAM;
   no flash fetch or flash-backed load from the freeze to the unfreeze,
   as SO-102's `mspi.c` does for its own sequence; interrupts masked, as
   everywhere in this kernel.
6. **The board description states the clock,** and the kernel compares
   what it set against the registers it reads back (`SOC_CLK_SEL`,
   `CPUPERIOD_SEL`, SPI0's `CLOCK`), and stops on a difference, naming
   it (the no-fallback rule).
7. **Afterwards:** the radio never changes the CPU clock; the PHY's
   periodic PLL tracking uses the same analog I2C master, which the HAL
   no longer touches. `ets_delay_us`'s ROM state is not updated; the
   radio links its own SYSTIMER-based `ets_delay_us` ahead of the ROM's.

**Requirement 4.4: what is measured on the part before any of it is
trusted** (stage B1, after SO-102's P0 if P0 has run):

- before and after the switch, printed: `SYSCLK_CONF` (`SOC_CLK_SEL`),
  `CPU_PER_CONF`, SPI0's `CLOCK`, `SRAM_CLK`, `CORE_CLK_SEL` and `CTRL`,
  UART0's clock configuration, and CCOUNT against SYSTIMER over a fixed
  count of SYSTIMER ticks (printed, never asserted against time);
- after the switch: a CRC of a known flash range read through the data
  window, compared with the build's; on the PSRAM board, SO-102's
  read-back test at the new clock;
- the console still readable at 115200.

The emulator cannot show any of this (its CCOUNT runs at 40 MHz on host
time and it models no memory-SPI timing).

### 4.6 RAM

#### 4.6.1 Kernel SRAM, for a networking image

SO-95 prices the box's linked areas at 12.0 KiB of the S3 kernel range's
measured 21.9 KiB of slack (§6.3 there), with eight 1436-byte bodies. A
networking image beside the box on `esp32s3-r8` (root, the box's four,
the radio, the stack, one application: eight processes, so no new body)
adds, with SO-105 giving handles per process:

```
item                                    Wi-Fi + box       + BLE host
grant budget 16 -> 20, every body        8 x 80 =   640    9 x 80 =   720
handle storage past 32: radio, root      1024 + 512       1024 + 512
the ninth body and its 4 records              -           1436 + 416
threads 8 -> 16 (radio 6, net 2, app 1)  8 x 556 = 4448   +1 ->  5004
events, Waiters to 16                    8x24 + 8x20 = 352      352
Interrupts 4 -> 8                        4 x ~16 =   64          64
attachments 52 -> 64                     12 x 24 =  288   16x24 = 384
pipes 4 -> 6 (gpio, pwm, netdev, wlan,
  sock); 8 with hci, gatt                2 x 3.5 KiB      4 x 3.5 KiB
IoMemories 8 -> 16, Memories 16 -> 24    160 + 448        160 + 448
increment                                ~15.0 KiB        ~24.1 KiB
with the box's 12.0                      ~27.0 KiB        ~36.1 KiB
against 21.9 KiB of slack                ~5.1 KiB over    ~14.2 over
```

With a board-wide 64-entry table instead of SO-105, add about 6.5 KiB.
**What gives:** the kernel range, not the design. SO-95 names the levers:
`KERNEL_STACK_SIZE` (64 KiB, "the cheapest lever", its use never
measured) and the DRAM split (`RAM_BASE`), which would take from process
SRAM, where the radio is already short (§4.6.2). **Unit K6:** measure the
kernel stack's peak (a painted stack read at shutdown in a harness kernel
running the suite's heaviest S3 cases, and on the part at B3) and set the
S3's kernel stack to that peak plus a stated margin; 16 KiB freed covers
both columns. The pipes are 40 to 60 percent of the increment: 3.5 KiB
each is `PIPE_INFLIGHT` (16) exchanges; a smaller per-board ring depth is
SO-95's number and not proposed here.

#### 4.6.2 Process SRAM

The S3's `sram` range, with rule 2.1, is 0x3FCB_0000 to 0x3FCE_EE34:
257,588 bytes (251.6 KiB). With SO-95's figures, root in SRAM (rule 5.10
there; SO-102 question 6), and the box on `esp32s3-r8`:

```
root's region (SO-95's figure; computed by rule 5.6)      96 KiB
GPIO driver (ram = "fast")                                24 KiB
controller (pinned)                                       32 KiB
left for the radio                                       ~99.6 KiB
```

PWM, heartbeat, the stack and the application go to PSRAM; the stack has
no deadline tighter than its TCP timers (believed). On the plain
`esp32s3` (no PSRAM) PWM and heartbeat stay in SRAM and 51.6 KiB is left:
no radio image beside the box there.

What the radio needs internally, with the dynamic frame buffers in its
PSRAM pool:

```
item                                   Wi-Fi only      both     basis
library .data+.bss+.dram1                15.2 KiB    17.2 KiB   measured*
glue and adapter static data              3-5         4-7       believed
static RX DMA buffers (IDF min: 3)        4.8         4.8       docs
BLE controller heap                        -          15-30     believed
PHY state, cal data, supplicant,
  scan results, node tables              15-25        18-30     believed
main stack (the region's)                 16          16        SO-95
task and ISR thread stacks               10-14        16-22     believed
adapter objects, queues                   3-5         5-8       believed
total                                    ~67-85      ~96-135
```

`*` From §2.1's section sums, before `--gc-sections`.

So: **Wi-Fi alone at IDF's minimum rank fits** in about 99.6 KiB with 15
to 30 KiB to spare. **IDF's default rank does not** (its static RX buffers
alone add 11 KiB, and its larger windows exist to use more dynamic
buffers). **Wi-Fi and BLE together beside the box fit only if** root's
computed region is 64 KiB or less (freeing 32 KiB), which nobody has
measured; otherwise the image with both radios omits the box's pinned
controller or the GPIO driver from SRAM, or runs without the box. **The
plan is IDF's minimum rank with the dynamic buffers in PSRAM**
(question 12). **Rule 4.4:** the adapter keeps a high-water mark per arena
and prints it at every board stage, replacing the believed rows with
measurements before any limit is sized on them. That the Wi-Fi MAC's DMA
reaches PSRAM on this part is what IDF's option implies; it is measured
at B4, and if false, the dynamic buffers return to SRAM (19 KiB more) and
only Wi-Fi alone fits.

---

## 5. The architecture

### 5.1 The processes

```
                         applications (Saw)
                      sockproto |      | gattproto
                 +--------------+      +-------------+
                 |                                    |
           svc-net (Saw)                        svc-ble (Saw)
     IPv4/ARP/ICMP/UDP/TCP/DHCP/DNS           L2CAP/ATT/GATT/GAP
                 | netdevproto                        | hciproto
                 |  + pktring                         |  + pktring
       +---------+----------------+                   |
       |                          |                   |
 svc-virtio-net (Saw)   svc-radio-esp32s3 (windowed: C adapter,
  riscv32/arm64 virt     Espressif libs; Saw once W0 holds) -+
 svc-openeth (Saw)          also serves wlanproto to a manager
  S3 emulator only
```

1. **`svc-radio-esp32s3`**, one process for Wi-Fi, BLE, the PHY and
   coexistence, which share the radio, its calibration and one set of ROM
   tables. It holds what §4.4 lists. It is one process for one set of
   devices (SO-95 rule 4.8): the S3 has one radio.
2. **`svc-net`**, the TCP/IP stack, Saw, call0: a client of `netdevproto`;
   serves `sockproto`. No device.
3. **`svc-ble`**, the BLE host, Saw: a client of `hciproto`; serves
   `gattproto`. No device.
4. **A network manager** (root or a small process): the only holder of
   `wlanproto` (scan, connect with SSID and key, disconnect, status).
5. **Applications**, clients of `sockproto` and `gattproto`.
6. **`svc-virtio-net`, `svc-openeth`**: drivers serving `netdevproto` on
   the emulators.

### 5.2 Protocol packages, in the `uartproto` pattern

Each is a library package in `drivers/` that the server and every client
compile against.

- **`pktring`**, the shared-memory packet ring both packet protocols use.
  `PIPE_BODY_BYTES` is 128 and cannot exceed 255 (spec §2.1), so a frame
  or an HCI ACL packet cannot ride a message. The client allocates one
  Memory per connection and gives it at `Attach`; both map it. It holds
  two rings of fixed-size slots (2048 bytes for frames, 512 for HCI) and
  two index words per ring. Messages carry doorbells and slot numbers.
- **`netdevproto`**: `Attach(ring)` (a call; the reply carries the MAC
  address, MTU and slot counts), `Tx(first, count)` (a tell),
  `RxReturn(first, count)` (a tell), `WaitEvent` (a held call answered
  with the next event: frames received in slots a..b, TX slots done, link
  up, link down), `Stats`.
- **`hciproto`**: the same shape with H4 packet types in slots.
- **`wlanproto`**: `Scan`, `Connect(ssid, auth, key)`, `Disconnect`,
  `Status`, `WaitEvent`. The key crosses once.
- **`sockproto`**: one connection per application, sockets named by a
  small id inside it: `UdpOpen(port)`, `TcpConnect(addr, port)`,
  `TcpListen(port)`, `Accept`, `Send(id)`, `Recv(id)`, `Close(id)`,
  `Resolve(name)`, `IfStatus`; bulk data through the connection's
  `pktring`. (Revision 1 gave each socket a pipe connection; at 3.5 KiB
  of kernel SRAM a connection, §4.6.1, that is not affordable on the S3.)
  An application's death is the connection's peer-closed, which closes
  every socket it held.
- **`gattproto`**: `Advertise(data)`, `AddService(table)`, `Notify`,
  `WaitEvent`.

**Rule 5.2, of the protocol packages: a shared ring is input.** The
receiving side reads every field it acts on from shared memory once, into
private memory, checks it, and uses only the copy:

1. an index word is read once and checked against the slot count;
2. a slot's length is read once and checked against the slot size;
3. a slot's header bytes that a parser acts on (Ethernet, IP, TCP, H4
   headers) are copied into private memory before parsing; payload is
   copied out with the checked length and never re-read;
4. a value that fails a check ends the connection with a counted error,
   never a fault of the receiver.

`pktring` provides the read-once accessors, so a protocol written on it
cannot read a shared field twice by accident. On the flat tier the rule
keeps a buggy peer from crashing the stack or the radio; on a protected
board it keeps a hostile one from steering them outside the ring. A1's
hosted tests hold it: an index past the count, a length past the slot, a
field changed between two reads.

### 5.3 Moving buffers, and how many copies

Receive over Wi-Fi: the MAC DMAs into a library buffer; the RX callback
(`esp_wifi_internal_reg_rxcb`) hands it to the radio, which copies it into
an RX slot and frees the library buffer (copy 1); the stack copies the
headers it parses (rule 5.2) and the payload into the application's ring
(copy 2). Transmit: the application writes its ring; the stack builds the
frame in a netdev TX slot (copy 1); `esp_wifi_internal_tx(slot, len)`
copies into a library DMA buffer (copy 2, inside the library,
`overview.rst` l.1261-1268). On virtio-net the driver points descriptors
at the shared slots.

### 5.4 Flow control and failure

1. **Back-pressure is slot exhaustion.** A full TX ring makes the stack
   hold (TCP) or drop (UDP, counted); a full RX ring makes the server drop
   and count. Neither side blocks on the other.
2. **A slow client** stops receiving, alone: every reply to a held
   `WaitEvent` is one message, and the ring is the buffer.
3. **A dead client** is peer-closed; the server drops its mapping and its
   slots.
4. **A dead server** answers every claim `PeerClosed`; the stack marks the
   interface down and closes its TCP connections.
5. **Rule 5.1: a dead radio leaves hardware live.** The kernel masks its
   lines at teardown, but the MAC may still DMA into what was its arena. A
   radio restart resets the Wi-Fi and BT blocks before any memory of the
   dead process is reused, and its region and pool return only after that
   reset. Until a restart unit exists, the radio's death ends the image
   with a console line.

### 5.5 The board and the image

In SO-95 revision 5's formats (§3.0 there: a range is `base` and `len`;
sizes and addresses are TOML integers in bytes; a device has a `type`; a
driver names the types it drives; one process per device instance).

The board, `hal/xtensa-esp32s3-r8/board.toml`, gains (abridged; extents
marked `?` are K5's to settle from the TRM, the libraries' literals giving
only pages):

```toml
[ram.sram]
use = "process"
speed = "fast"
base = 0x3FCB_0000
len = 0x3_EE34              # ends where the ROM's range starts

[ram.rom_radio]
use = "rom"                 # new kind: never placed into by the builder
speed = "fast"
base = 0x3FCE_EE34          # _dram0_rtos_reserved_start (ROM ELF)
len = 0x11CC                # to 0x3FCF_0000: 4,556 bytes

[clock]
cpu = 160_000_000           # K4; the kernel checks and stops on a mismatch
rom = "esp32s3-20210327"    # the mask ROM the radio's tables are for

[devices.WIFI_MAC]
type = "esp32s3-wifi-mac"
base = 0x6003_3000
len = 0x3000                # ? (pages 0x6003_3000..0x6003_5000 seen)
lines = { mac = 0, pwr = 2 }   # new: a device with two lines

[devices.RF_FE]
type = "esp32s3-rf-fe"
base = 0x6000_5000          # FE2 and FE
len = 0x2000

[devices.WIFI_BB]
type = "esp32s3-wifi-bb"
base = 0x6001_C000          # NRX at 0x6001_CC00, BB at 0x6001_D000
len = 0x2000

[devices.ANA_I2C]
type = "esp32s3-ana-i2c"
base = 0x6000_E000
len = 0x1000

[devices.BT_BB]
type = "esp32s3-bt-bb"
base = 0x6001_1000
len = 0x1000
line = 5

[devices.BLE_CORE]
type = "esp32s3-ble-core"
base = 0x6003_1000
len = 0x1000                # ? (and the 0x6004_2000 page, unexplained)
line = 8

[devices.EFUSE]
type = "esp32s3-efuse"
base = 0x6000_7000
len = 0x1000
shared = true               # new: several holders, each named in the plan

[devices.RTC_CNTL]
type = "esp32s3-rtc-cntl"
base = 0x6000_8000
len = 0x1000
shared = true

[devices.APB_CTRL]
type = "esp32s3-apb-ctrl"
base = 0x6002_6000
len = 0x1000
shared = true

# SYSTEM exists in SO-95's file; it gains shared = true

[limits.max]                # what a networking image may ask for
grant_rows = 20             # GRANT_ROW_BUDGET, a HAL number (80 B a body)
```

The radio's declaration names each type above in a `window` record, the
two `esp32s3-wifi-mac` lines and the two BT lines in `line` records, the
`rom_radio` range and its pool in `memory` records, three `serve` records,
`ram = "fast"`, the quotas of §4.4, and `target-features = "+windowed"` in
its manifest. The image:

```toml
[image]
name = "box-net"
board = "esp32s3-r8"
tick = true

[[pool]]
name = "radio_frames"
len = 0xA000                # 40 KiB, §4.4
ram = "psram"

[[process]]
name = "radio"
package = "sawos:drivers/svc-radio-esp32s3"
ram = "sram"
inputs = { radioconfig = "config/radio.saw" }   # country, rank; no key

[[process]]
name = "net"
package = "sawos:services/svc-net"
clients = { netdev = "radio.netdev" }
```

Each board type has one instance, so the image names no `device`. **No
fallback:** a part without the described PSRAM, a ROM other than the one
named, or a clock that does not read back as described stops the boot
with a line naming what differed.

### 5.6 What this asks of other designs

**Of the packaging design** (follow-ups to SO-95 as ruled; the lead
decides where they land):

1. a RAM range with `use = "rom"`: never placed into, granted only through
   a `memory` record that names it;
2. `lines = { name = n, ... }` for a device with more than one line, and
   a `line` record naming which;
3. `shared = true` on a device several processes may bind (SYSTEM,
   EFUSE, RTC_CNTL, APB_CTRL), with every holder named in the plan
   (rule 5.4 refuses a second binding today);
4. a per-board `grant_rows` maximum above 16 (20 for the radio board),
   and the Interrupt slab as a per-board total (4 today, 8 needed);
5. a manifest's `target-features` carried into the plan, so the builder
   refuses an image that mixes conventions in one process;
6. linking prebuilt archives and an extra linker script into one package
   (the ROM scripts): believed missing from Blade (`native` lists
   translation units); a Saw gap for the sawlang lead;
7. **flash:** a slot sized from the linked process, not 128 KiB: the
   radio's code and read-only data are about 0.6 to 0.7 MiB for Wi-Fi
   (§2.1's 403 KiB of library text, read-only data and IRAM-placed code
   for a station, plus the supplicant's and the glue's, believed 200 to
   300 KiB) and about 0.9 to 1.0 MiB with BLE (libbtdm_app's 275 KiB),
   before `--gc-sections`; R1 measures after it. So a slot of at least
   1 MiB for one process, the slot range able to hold it (SO-95's U4
   moves `.drom` behind the last slot), and the whole image, with the
   kernel and every slot, inside the part's 8 MiB.

**Of SO-105:** its three requirements in §4.4.

**Of SO-102:** nothing new beyond being told K4's order (requirement 4.3
item 1); it is a prerequisite for the radio's PSRAM pool and for the box's
processes to leave SRAM.

**Of SO-77:** its stage 0 reading of the clock, before K4.

### 5.7 What a fault in the radio can reach

**On the flat tier, everything:** the S3 core has no privilege (ABI.md
§5e). A fault or a hostile path in the libraries can corrupt the kernel and
every process, reprogram clocks and the interrupt matrix, drive the eFuse
and flash controllers, DMA where the MAC's DMA reaches, and transmit. The
syscall-path checks still hold, and a crash is reported with the radio
named.

**With memory protection** (the S3's PMS, unprobed; the C6's PMP): CPU
accesses are confined to the radio's rows, so a wild store faults the
radio, and the flash controller can be left out of its rows. DMA is not
confined by CPU-side rows (whether the PMS covers the MAC's DMA is not
known), and the shared blocks stay in its rows because the libraries
write them directly (§2.7).

---

## 6. The stacks

### 6.1 TCP/IP

**(a) Port lwIP.** BSD 3-clause. The fork IDF pins compiles call0 for the
S3 (verified: 31 objects, `NO_SYS=1`, IPv4, TCP, UDP, DHCP, DNS, ARP, ACD,
`-Os`; 0 `entry`, 280 `ret.n`, 759 `callx0`; 49.8 KB of text and 44.9 KB
of `.bss` before `--gc-sections`; externals: seven string functions,
`atoi`, `sys_now`). A medium unit to wrap; it stays the fallback.

**(b) Write one in Saw.** What it contains, and what each estimate rests
on:

```
part        lwIP's C (verified)            Saw estimate (believed)
Ethernet    ethernet.c                     200
ARP         etharp.c 1,259 lines           400
IPv4        ip4.c 1,216 + ip4_frag 932     600 (reassembly bounded)
ICMP        icmp.c 407                     200
UDP         udp.c 1,333                    400
DHCP        dhcp.c 2,083                   700
DNS         dns.c 1,876                    600
TCP         tcp.c 2,737 + tcp_in.c 2,240   3,500-5,000
            + tcp_out.c 2,257 + headers
            1,029 = 8,263 lines, about
            2,055 statements
sockets     -                              800
```

**The TCP estimate rests on lwIP's statement count** (about 2,055
semicolons in its three files, `grep -c ';'`), on the expectation that Saw
states the same logic in roughly 1.5 to 2.5 lines per C statement, and on
smoltcp and uIP as the other calibration points (the sawlang archive's
"~8-15k lines Saw" for the stack). **Where the uncertainty is:** not the
lines but the testing: RTO estimation and backoff (RFC 6298), congestion
control and fast recovery (RFC 5681), zero-window probing, out-of-order
queues, and interoperation with real peers. The test harness (state-
transition goldens, then the host's own TCP as the peer) is believed as
large as the code. **So TCP is four units** (A5a-A5d, §10), each medium,
and two to three months of one implementer, not one large unit. IPv6
adds about 2,500 lines and is not planned.

**(c) lwIP first, Saw later** builds glue that is thrown away while the
radio is months off. Rejected.

**Recommendation: (b), staged UDP-first** over virtio-net; lwIP named as
the fallback if TCP stalls.

### 6.2 The BLE host

The controller speaks HCI through an in-process interface
(`API_vhci_host_send_packet`, `bt.c` l.289-291), carried over `hciproto`.

**(a) NimBLE** (Apache-2.0; ESP-NimBLE is qualified, Q371597): needs its
porting layer; size not measured; medium to large.

**(b) Saw:** advertising over raw HCI (small); a GATT peripheral without
pairing (believed 3,000-5,000 lines); pairing, with P-256 and the DH key
done by the controller (HCI LE Read Local P-256 Public Key, LE Generate
DHKey) and AES by HCI LE Encrypt (believed 1,500-2,500 lines).

**Recommendation: (b)**, advertising first; NimBLE the fallback. §9.1 has
the qualification consequence.

### 6.3 WPA2: the supplicant, and why its size was optimistic

Revision 1 called WPA2 one large unit. What it rests on, counted:

- **IDF compiles 94 source files, 67,802 lines,** in the supplicant's
  unconditional source list for every Wi-Fi build
  (`wpa_supplicant/CMakeLists.txt` l.18-89, `wc -l`): AP, WPS, WPA3, OWE
  and NAN glue included.
- **A hand-picked WPA2-PSK station subset is about 9,700 lines** (`wpa.c`,
  `wpa_ie.c`, `wpa_common.c`, SHA-1, PBKDF2, AES wrap and unwrap, the
  internal AES and MD5, `esp_wpa_main.c`, `esp_wpas_glue.c`,
  `os_xtensa.c`), but IDF never builds that subset alone, and its glue
  references the rest.
- **It needs an OS of its own:** `os_task_create`, `os_queue_*` and
  `esp_event_post` (14 calls) and `esp_event_handler_register`, found in
  its port and glue; and crypto, mbedTLS by default
  (`ESP_WIFI_MBEDTLS_CRYPTO` defaults to y), with an internal
  implementation in `src/crypto` as the alternative.

**So R5 is three units:** R5a builds IDF's unconditional list windowed in
the radio, with internal crypto (no mbedTLS) and its OS port over the
adapter, and links it (medium to large; the risk is integration of
68,000 lines of foreign C); R5b prunes what the station does not link,
measured by size (medium); R5c associates on the board (medium). The
uncertainty is the port and the build, not the protocol.

---

## 7. What can be tested where

### 7.1 What the emulators have (verified)

- **The suite's `virt` machines take virtio-net.** Homebrew QEMU 11.1.3
  lists `virtio-net-device` for `-M virt` on `qemu-system-riscv32` and
  `qemu-system-aarch64`. A dumped device tree with one attached shows
  transports from 0x1000_1000 (PLIC line 1) to 0x1000_8000 (line 8) on
  riscv32, and on arm64 the last at 0x0A00_3E00 (SPI 47; 32 transports of
  0x200 from 0x0A00_0000 is believed). A driver probes each transport's
  `DeviceID` (1 is network).
- **Host backends:** `socket`, `stream`, `dgram`, `hubport`, `tap`,
  `user`, and on macOS `vmnet-*`, `vde`, `bridge`.
- **Espressif's QEMU for the S3** offers only `open_eth` as a NIC model,
  and its strings name nothing for Wi-Fi. `open_eth` sits at 0x600C_D000
  (ABI.md §3, NOT SILICON); its interrupt source is found by a probe.

### 7.2 How the stack is gated without a radio

1. **Hosted unit tests** of the stack's pure parts against golden bytes,
   and of `pktring`'s rule 5.2.
2. **`svc-virtio-net`** on riscv32 and arm64 `virt`, flat profile
   included, with `-netdev stream` to a socket the runner serves: a
   scripted, deterministic peer (ARP, a fixed DHCP lease, ICMP echo, UDP
   echo, one DNS name), and the host's own TCP through `-netdev user` with
   `hostfwd` as the real-world oracle. **Rule 7.1:** no case asserts a
   retransmission count, an RTT, a timeout's length or a DHCP retry count;
   a case waits for the event that proves the thing happened.
3. **`svc-openeth`** runs the same stack in `sos-smoke-esp32s3`.
4. **The adapter without the radio:** built for riscv32 in the main gate
   under a program that drives every table entry the way the libraries do
   (handler-context calls from an interrupt thread, a timeout on every
   blocking call, timer races, nested critical sections at the ceiling
   once K3 exists); built windowed for the S3 smoke too.
5. **The radio image links:** build-only, with no undefined symbol.
   Nothing executes Espressif's code in the gate.
6. **Windowed processes and windowed Saw** in the S3 smoke (K1, W0).

### 7.3 What is left to the board, in stages

Each stage is one image written with `write-flash 0x0`, on the owner's
word, with no eFuse write or burn and no erase, the console captured; no
stage writes flash at run time (rule 2.3).

```
B0  read-only probe: CPU clock, the memory-SPI registers, MAC from the
    eFuse read registers (shared with SO-77's stage 0 and SO-102's P0).
B1  the clock raised (K4), with requirement 4.4's prints.
B2  K1's windowed cases and K2's FP case on silicon.
B3  PHY bring-up with full calibration (allowed before K3, rule 4.3):
    PHY version, MAC, arena high-water marks, a ROM data word against the
    ROM ELF's initial value, the kernel stack's peak (K6).
B4  Wi-Fi scan (after K3): SSIDs, channels, RSSI; interrupt latency
    (requirement 4.2); whether frame buffers in PSRAM work.
B5  association, open or WPA2-PSK (R5c).
B6  DHCP, ping and UDP over Wi-Fi through svc-net.
B7  BLE advertising from raw HCI.
B8  a GATT connection from a phone.
B9  Wi-Fi and BLE together.
```

---

## 8. Other boards: the ESP32-C6

- **Convention.** The C6 archives are RISC-V (RVC, ilp32; arch attribute
  `rv32i2p0_m2p0_c2p0`, verified on `libpp`), a subset of sawos's
  `rv32imac`: no K1, K2 or W0.
- **Interface.** The same `g_osi_funcs_p` tables, with six C6-family
  entries (REGDMA, sleep retention) and one for 802.11ax parts
  (`wifi_os_adapter.h` l.152-170); its own ROM with Wi-Fi code and a
  reserved RAM range, at addresses its ROM ELF gives.
- **Shared:** the adapter (apart from interrupt and clock glue), every
  protocol, the stack, the BLE host, K3's requirement, SO-105's.
- **The C6 needs:** a board HAL under `riscv32-common`, its clock bring-up
  (its memory-SPI binding read anew), a board description, the C6 BLE
  library (`esp32c6-bt-lib`, not inspected), and PMP protection.
- **The first radio belongs on the S3:** the board is in hand; the S3-only
  cost is K1, K2 and W0, all gateable on the emulator.

---

## 9. An attack on this design

### 9.1 Regulatory and certification

From the files: the libraries enforce channel and power limits from tables
IDF supplies, the PHY init data (`phy_init_data.c`) and the regulatory
domain tables (`esp_wifi_regulatory.c`); the design links both unchanged
and sets the country explicitly (question 4). The S3's BLE controller is
qualified design 239440 and ESP-NimBLE Q371597 (`ble-qualification.rst`).
Not checked: the module's FCC/IC/CE grant conditions on host software
(believed: not changing RF parameters outside the grant); whether a
different operating system changes anything (believed not, with
Espressif's PHY data, tables and calibration); the SIG's listing rules for
a host of our own. Emission rules apply whether or not anything is sold.

### 9.2 Security

- The radio library is network-facing and, on the flat tier, the most
  privileged code in the system.
- The Wi-Fi key lives in the radio and the supplicant; on the flat tier
  any process can read it.
- A shared ring is attack surface; rule 5.2 is the defence that matters
  on a protected board.
- Nothing audits what leaves the antenna.

### 9.3 Assumptions that would sink the plan if false

1. **Deferred handlers, at the priorities of requirement 4.1, meet the
   libraries' deadlines.** If not: an upcall path in the kernel. Measured
   at B4 (requirement 4.2).
2. **The libraries tolerate the ceiling in place of masking,** and the
   task-priority order K3 can give (question 8). R2 logs requested
   priorities; B4 runs under them.
3. **`ld.lld` resolves the ROM script's assignments as GNU ld does.** If
   not: GNU ld for this package. R1.
4. **The ROM's reserved RAM is intact after direct boot.** B3 compares a
   ROM data word against the ROM ELF.
5. **No library path writes eFuse or flash.** Literal scan and ROM call
   graph only.
6. **One core suffices** (IDF's UNICORE paths exist).
7. **The internal SRAM table holds** (§4.6.2): measured by rule 4.4.
8. **The Wi-Fi MAC's DMA reaches PSRAM** (IDF's option implies it): B4.
9. **The kernel stack has 16 KiB to give** (K6). If not, the DRAM split
   moves and the radio's SRAM shrinks.
10. **The clock switch leaves the flash readable** with the divider K4
    chooses (requirement 4.3); B1 shows it before anything else depends
    on it.
11. **Windowed Saw is sound** for the radio's serve loop; W0 shows it, or
    the radio stays C.
12. **The owner accepts a windowed C program in a Saw system.**

---

## 10. Order of work

Sizes: S (one brief, days), M (one brief, a week or two), L (split before
briefing).

**Prerequisites from other issues:** SO-95 as ruled (board description,
limits, quota door); SO-102 (PSRAM, `hal.memory_init()`); SO-77's stage 0;
**SO-105** (per-process limits: §4.4's three requirements), which is a
prerequisite of the radio process and stands beside the kernel track.

**Track A: the stack and its device.** New packages only, except merges
in the runner's case table.

```
A1  S  pktring (with rule 5.2's read-once accessors) + netdevproto +
       wlanproto + hciproto + sockproto; hosted tests of ring arithmetic,
       encodings, and the rule (index past the count, length past the
       slot, a field changed between reads).
A2  M  Saw stack layer 1, hosted: Ethernet, ARP, IPv4, ICMP, UDP.
A3  M  svc-virtio-net on riscv32 and arm64; the runner's peer; net_ping.
A4  M  DHCP client and DNS stub against the peer.
A5a M  TCP: connection setup and teardown, the state machine, goldens.
A5b M  TCP: data transfer, retransmission and RTO (RFC 6298).
A5c M  TCP: congestion control, fast retransmit and recovery (RFC 5681).
A5d M  TCP: out-of-order queue, zero-window probe, keepalive, TIME_WAIT;
       cases against the host's TCP.
A6  M  svc-net's sockproto server; UDP and TCP echo applications.
A7  M  svc-openeth on the S3 emulator; the same cases in the S3 smoke.
```

**Track K: kernel and HAL** (after SO-102 and SO-95's HAL changes, which
touch the same files).

```
W0  S  sawlang: a windowed Xtensa row in the freestanding suite (a request
       to the sawlang lead, filed now; sawos waits on it, rule 3.1).
K1  M  windowed threads on the S3 (§3.1), with windowed C and, once W0
       lands, windowed Saw cases; stage B2.
K2  S  FP and BR state per S3 thread, lazy.
K3  L  the priority scheduler of spec §7 with requirement 4.1 (levels in
       a driver process, strict preemption on wake, the ceiling) and the
       latency diagnostic of 4.2. Opus design first; gated on all profiles.
K4  M  the clock (requirement 4.3), after SO-77's stage 0 and SO-102's
       P0; stage B1 with requirement 4.4.
K5  S  the S3 board description: the ROM range, radio devices and lines,
       the clock and ROM facts with their boot checks, grant budget 20,
       the per-board slab totals; with SO-95's follow-ups (§5.6).
K6  S  the kernel stack's peak measured; the S3's kernel/process split
       set from it (§4.6.1).
```

**Track R: the radio.**

```
R1  M  the link: the radio package against the archives and the ROM
       scripts, every adapter entry a stub that prints and stops; zero
       undefined symbols; ld.lld against the ROM assignments; code size
       after --gc-sections. Build-only.
R2a M  adapter: RTOS objects and timers (riscv32 gate, then windowed S3).
R2b M  adapter: memory, logging, NVS-in-RAM, random, MAC.
R2c M  adapter: interrupt threads, the ceiling, _ints_off; needs K3.
R3  M  PHY bring-up on the board (B3); allowed before K3 (rule 4.3).
R4  M  Wi-Fi scan (B4) and wlanproto's Scan; needs K3, K4, K5, SO-105.
R5a M-L  the supplicant built windowed with internal crypto and its OS
       port; links (§6.3).
R5b M  the supplicant pruned to what a station links; size measured.
R5c M  association on the board (B5).
R6  M  netdevproto served by the radio; DHCP and ping over Wi-Fi (B6).
```

**Track B: BLE.**

```
B-1 M  controller init and hciproto; advertising from raw HCI (B7);
       needs K3.
B-2 L  Saw host: L2CAP, ATT, GATT server, GAP (split before briefing).
B-3 L  SMP pairing.
B-4 M  coexistence (B9).
```

**Dependencies.** A1-A7 need nothing from the box (A7's stack is call0).
W0 and the K3 design can start now. K1, K2, K5, K6 wait for SO-102's and
SO-95's HAL changes; K4 waits for SO-77's stage 0 and SO-102's P0. R1 and
R2a/R2b can start on a scratch script; R2c needs K3; R3 needs K1, K2, K4,
K5; R4 onward needs K3 and SO-105.

**The first three months** (to mid-February; the box first until
Christmas; at most two implementers at once):

- **Would deliver:** A1 and A2 before Christmas, with W0 filed and the K3
  design written; in January and February A3, A4 and A5a, K1, K2, K5 and
  K6 once SO-102 is in, R1 (the radio links, its size known), R2a and
  R2b; possibly K4 with B1. On the emulators: ping, UDP, DHCP and DNS
  through a Saw stack and a Saw virtio-net driver on riscv32 and arm64;
  windowed processes in the S3 smoke and on the board.
- **Would not deliver:** any frame over the air, TCP beyond setup and
  teardown, the supplicant, BLE. The earliest radio milestone is B3 (PHY
  calibration), possible near the end; a scan (B4) waits for K3 and
  SO-105 and is believed to fall a month or more after the three months.

---

## 11. Alternatives, questions, and what was checked

### 11.1 Alternatives rejected

1. Thunks between call0 and windowed code (§3.1 b).
2. Converting the libraries to call0 (§3.1 d).
3. Running the libraries' handlers in kernel mode.
4. A lock alone in place of priorities and a ceiling (§4.3).
5. A cooperative scheduler inside one thread for the libraries' tasks.
6. Separate processes for Wi-Fi and BLE (shared PHY, coexistence, ROM).
7. Persisting calibration to NVS (flash writes the limits forbid).
8. A broker for the shared clock and reset registers: the libraries write
   RTC_CNTL, APB_CTRL and SYSTEM by absolute address (§2.7).
9. A pipe connection per socket: 3.5 KiB of kernel SRAM each.
10. A board-wide 64-entry handle table: about 8 KiB of kernel SRAM across
    eight bodies, where SO-105 costs about 1.5 KiB.
11. lwIP or NimBLE as the plan (kept as fallbacks); the C6 first; an
    ESP-Hosted companion chip.

### 11.2 Questions for the owner

`(new)` and `(changed)` mark what revision 2 adds or alters.

1. May the radio be a windowed C program inside a Saw system, with Saw in
   it once windowed Saw is gated? Recommended: yes.
2. `(changed)` TCP/IP in Saw, with lwIP as the fallback, knowing TCP is
   four medium units and two to three months? Recommended: yes, UDP first.
3. BLE host in Saw, advertising first? Recommended: yes.
4. Which regulatory country? Recommended: the owner's, explicit.
5. How do Wi-Fi credentials reach the device? Recommended: a git-ignored
   build input sent over `wlanproto` at boot; never committed.
6. Ask Espressif for call0 builds? Recommended: optional; nothing waits.
7. `(changed)` Raise the CPU to 160 MHz on the PSRAM board, once at boot
   and before the PSRAM is brought up, for every image on that board?
   Recommended: yes, after B1's measurements.
8. `(changed)` No radio traffic before the priority scheduler with its
   ceiling (K3); PHY calibration (R3) allowed before it; and, if K3 cannot
   give a driver seven levels, the libraries' tasks share one level?
   Recommended: yes to all three.
9. `(changed)` Raise the PSRAM board's grant-row budget from 16 to 20 (80
   bytes in every body), and its slab totals for networking images
   (threads 16, events and Waiters 16, Interrupts 8, pipes 6 or 8,
   IoMemories 16, Memories 24)? Recommended: yes.
10. When the radio dies, end the image or restart it? Recommended: end the
    image until a restart unit exists.
11. `(new)` Give the radio its 64 handles through SO-105 rather than a
    64-entry table in every body? Recommended: SO-105.
12. `(new)` Plan on IDF's minimum rank with the frame buffers in PSRAM;
    run Wi-Fi and BLE together beside the box only if root's computed
    region is 64 KiB or less? Recommended: yes.
13. `(new)` Find the kernel SRAM a networking image needs by measuring and
    cutting the S3 kernel stack (K6), before moving the kernel/process
    split? Recommended: yes.
14. `(new)` One `sockproto` connection per application, sockets named
    inside it? Recommended: yes.

### 11.3 What was checked, how

Everything under `/private/tmp/claude-501/-Users-shawn-Projects-sawos/
34c0d76b-723c-482d-939f-716d368f6e5c/scratchpad/SO-103/`: downloads in
`dl/`, extracted objects in `ex/`, outputs in `an/`, scripts in
`scripts/`, builds in `build-lwip/` and `probe/`.

**Verified by inspecting files or running tools:**

- the archives at IDF v6.1's submodule commits, sizes and section sums;
  `ABI=0` in every S3 object; the instruction counts;
- the undefined-symbol groups and ROM addresses; the ROM ELF's reserved
  sections, `_dram0_rtos_reserved_start`; IDF's reservation code;
- the eFuse and flash-controller literal scans; the ROM call graph of
  `ets_efuse_program`; the peripheral pages; the Wi-Fi handler's callees;
- the adapter tables; IDF's critical section raising `INTLEVEL` to
  `XCHAL_EXCM_LEVEL` (`port.c` l.126); IDF's vectors' handler levels
  (`xtensa_vectors.S` l.1255, l.1424); the BT task priorities
  (`esp_task.h`);
- the memory-SPI binding on the S3 (`mspi_ll.h` l.57), the speed-mode
  bracket (`clk_utils.c` l.31-75, `pm_impl.c` l.681, l.699), the
  low-speed 20 MHz and the cache freeze (`mspi_timing_tuning.c`,
  `soc_caps.h` `SOC_CACHE_FREEZE_SUPPORTED`), all at v6.1;
- `sawc` emitting windowed code with `+windowed`; Blade's feature passing
  (read, not run);
- lwIP compiling call0, and its per-file line and statement counts;
- the supplicant's unconditional source list (94 files, 67,802 lines), a
  WPA2-PSK subset's line count, its OS and event calls;
- QEMU: virtio-net on both `virt` machines, backends, device-tree
  placement; Espressif's QEMU NIC models and strings;
- the C6 archives' ISA and table use;
- `SOC_MODEM_APB_CLOCK_IS_INDEPENDENT` absent from the S3's `soc_caps.h`;
- the kernel's rights enums (`rights.saw`), slab limits (`limits.saw`),
  `IMAGE_GRANT_ROWS` (4), the S3's `PROT_GRAIN` (4 bytes), the Interrupt
  slot's fields;
- licences: the four library repositories, lwIP, esp-nimble, esp-radio,
  the supplicant's file header.

**Read, not run:** IDF's Wi-Fi memory documents and the S3 rank table;
the 100 ms calibration cost; the BLE qualification listings; the BT
specification's HCI commands; NuttX's and esp-radio's adapters; SO-95
revision 5 (§3-6), SO-102 revision 1, SO-77, the lead's SO-105 notes (not
rulings), and sawos's spec and ABI.md.

**Not checked, and how to find out:**

- whether deferred handlers at K3's priorities meet the libraries'
  deadlines, and whether the libraries tolerate the ceiling: B4;
- the task priorities the libraries request and how many tasks they
  create: R1 and R2 log them;
- whether `ld.lld` resolves the ROM script's assignments as GNU ld does;
  the radio's code size after `--gc-sections`: R1;
- the ROM's reserved RAM after direct boot: B3;
- the believed RAM rows, root's computed region, and the MAC's DMA into
  PSRAM: rule 4.4, B3-B4;
- the kernel stack's peak use: K6;
- an Interrupt slot's exact size: K5 reads it from the symbol table;
- the memory-SPI core clock on the crystal and the boot dividers: SO-102's
  P0 and B1;
- whether Blade can link archives and extra scripts: R1 files it;
- whether windowed Saw is sound beyond the probe: W0;
- the radio blocks' extents (WIFI_MAC, BLE_CORE, the 0x6004_2000 page):
  K5, from the TRM;
- whether the S3's PMS confines the MAC's DMA;
- `open_eth`'s interrupt source: A7's probe;
- NimBLE's size, the C6 BLE library, the FCC/IC/CE grant wording;
- whether the WPA2-PSK subset builds without the rest: R5b;
- what the libraries do at run time: nothing of Espressif's was executed
  or linked into anything that ran.

---

## 12. Response to the review

Each thread: its verdict, then the change and where it is.

- **t1: the gate gives less than masking** (accepted). §4.3 rewritten: what
  the libraries assume (critical sections at `XCHAL_EXCM_LEVEL`, handlers at
  their levels, BT over Wi-Fi, verified in IDF's port and vectors), what a
  lock alone gives, and requirement 4.1 on K3 (levels in a driver process,
  strict preemption, an immediate ceiling, who holds what); rule 4.3 forbids
  radio traffic before K3; §9.3 items 1-2; question 8.
- **t2: K4 moves the flash and PSRAM clocks** (accepted). §4.5: the binding
  (`mspi_ll.h`), IDF's speed-mode bracket and cache freeze (verified at v6.1
  as the reviewer read at v5.4.1); requirement 4.3 (before
  `hal.memory_init()`, the console first, the flash divider before the switch,
  from IRAM, the cache frozen, the board's clock checked) and 4.4 (B1's
  registers on both sides, a flash CRC, SO-102's read-back); SO-102's side not
  designed.
- **t3: over 50 handles, 18 rows** (accepted; one suggestion disputed). §4.4
  recounts exactly (60 handles at peak, 18 rows; 45 and 15 for Wi-Fi only),
  states the declaration's quotas, the rights, what SO-95 as ruled gives,
  three requirements on SO-105, and each cost. Disputed: putting the shared
  registers behind a broker to cut windows; the libraries write RTC_CNTL,
  APB_CTRL and SYSTEM by absolute address (§2.7), so the windows stay on every
  tier. Merging windows is taken only where the board lists one device (RF_FE,
  WIFI_BB).
- **t4: root moved to PSRAM; ROM range; flash** (accepted). §4.6.2 keeps root
  in SRAM: about 99.6 KiB for the radio; Wi-Fi at the minimum rank with
  buffers in PSRAM fits, the default rank does not, both radios beside the box
  only with root's region at 64 KiB or less (question 12). §4.6.1 adds the
  kernel SRAM overrun and K6. Rule 2.1 makes the ROM range a board fact on
  every S3 board (`sram` ends at 0x3FCE_EE34). §5.6 item 7 asks for slots
  sized from the link, at least 1 MiB for the radio.
- **t5: shared ring is input; windowed Saw ungated** (accepted). Rule 5.2 in
  the protocol packages, with `pktring`'s read-once accessors and A1's tests.
  Rule 3.1 and unit W0 (a windowed row in sawlang's freestanding suite); the
  radio stays C until W0 and K1's windowed Saw cases pass.
- **coordinator: SO-95 rev 5's formats** (done). §5.5: `base`/`len`, integers
  in bytes, `type` on every device, a driver naming types, one process per
  instance, no fallback; new keys marked new and listed as follow-ups in §5.6.
- **coordinator: per-process numbers, SO-105** (done). §4.4: the radio's own
  numbers, what the ruled design gives, what needs SO-105 or a per-board
  raise, priced per process and per board separately; SO-105 a prerequisite in
  §10. The lead's SO-105 notes are used as a reading, not a ruling.
- **lead: TCP and WPA2 sizes optimistic** (accepted). §6.1: TCP's estimate
  rests on lwIP's 2,055 statements; four medium units, two to three months.
  §6.3: the supplicant is 67,802 lines as IDF builds it; R5 split into three.
