# SO-103: networking on the ESP32-S3 (design)

Scope: an architecture for networking in sawos (processes, what each holds,
the protocols between them), and a feasibility study of Espressif's closed
radio libraries on the ESP32-S3. Nothing is built from this before the owner
and the reviewer have read it.

Conventions. "Verified" means I inspected a file or ran a tool and §11.3 says
which. "Read" means taken from a source document, cited. "Believed" means
neither, and says so. Every library fact names the commit it came from:

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
IDF checkout under `~/Projects/esp32` was not readable from the sandbox, so
everything was fetched from Espressif's public repositories (§11.3).

---

## 1. Verdict

**Go, with conditions.** Wrapping Espressif's Wi-Fi, PHY, coexistence and
BLE controller libraries on the ESP32-S3 is feasible in sawos, and nothing
found in the files rules it out. The conditions are kernel and HAL work that
is bounded and mostly already specified:

1. The libraries are **windowed** (every object says `ABI=0`; 0 `ret` and
   0 `callx0` in 362 objects, §2.2). The radio process is therefore a
   windowed program. `sawc` already emits windowed code on request
   (`--target-features +windowed`, verified), so the process can hold Saw
   and C together with no thunks. The kernel must support windowed threads
   on the S3: window and alloca exception vectors, a 64-register frame, FP
   and boolean state (§3). Medium-sized.
2. The kernel must deliver a radio interrupt to its thread promptly: the
   **priority scheduler of spec §7** (specified, not built), with
   preemption on wake. Today a wake waits for a yield, a block or a 10 ms
   tick (§4.3).
3. The HAL must raise the **CPU to the PLL with APB at 80 MHz** (Wi-Fi
   holds `ESP_PM_APB_FREQ_MAX` on this part, §4.5), and the board must
   **reserve the ROM's radio data at DRAM 0x3FCE_EE34-0x3FCF_0000**, which
   today lies inside the process range (§2.5).
4. The radio needs roughly 70-110 KB of internal SRAM for Wi-Fi alone
   (estimated, §4.6), which in practice means SO-102 moves other processes
   to PSRAM first.

The stacks: **TCP/IP in Saw**, built and gated on the emulators against a
virtio-net driver long before the radio works; **BLE host in Saw**, starting
with advertising over raw HCI. lwIP compiles call0 for this core (verified)
and stays the named fallback, not the plan (§6). The first radio bring-up
belongs on the S3, because the board is in hand and the S3-only cost is two
medium units (§8).

---

## 2. The radio libraries as they are

### 2.1 Files and sizes

The ESP32-S3 directories of the four library repositories (byte sizes of
the archives; the sum of allocatable sections per library, from
`llvm-readelf -S` over every member, before any `--gc-sections`, so an
upper bound on what a link keeps):

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

`*` RAM-code is every section IDF's linker fragments may place in IRAM:
`.iram1.*`, `.wifi0iram.*`, `.wifirxiram.*`, `.wifislprxiram.*`,
`.wifislpiram.*`, `.wifiorslpiram.*`, `.wifiextrairam.*`, `.coexiram.*`.
"data+bss" is `.data`, `.bss` and `.dram1`.

A station needs `libcore`, `libnet80211`, `libpp`, `libphy` and
`libcoexist` (IDF links coexistence whenever both radios may be used, and
the libraries call it through a table). Mesh, ESP-NOW, smartconfig, WAPI,
RF test and BT test mode are optional. BLE needs `libbtdm_app` (or its
`_flash` variant, which keeps more in flash) and `libbtbb`.

Licences, each from its repository's own `LICENSE`: all four library
repositories are Apache-2.0. The libraries are binary; Apache-2.0 permits
redistributing them inside our image.

### 2.2 The calling convention: windowed, by three independent signs

1. **The ELF says so.** Every object in the S3 directories carries an
   `.xtensa.info` section reading `USE_ABSOLUTE_LITERALS=0 ABI=0`. ABI=0 is
   the windowed ABI (call0 objects say `ABI=2`). Counted per library over
   all 362 S3 objects: every one is `ABI=0`. The ELF header flags are
   `0x300` (`EF_XTENSA_XT_INSN`, `EF_XTENSA_XT_LIT`), which say nothing
   about the ABI.
2. **The code says so.** GNU `objdump -d` (xtensa-esp-elf 15.2.0 on this
   machine) over every member:

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

   (The other libraries show the same: no `ret`, no `callx0`.) The first
   function of `pp.o` opens `004136  entry a1, 32`.
3. **Every other integrator is windowed.** esp-radio (Rust) links the same
   archives on the S3, and Rust's Xtensa target is windowed; NuttX's Xtensa
   port carries `xtensa_windowspill.S` (MIT, Tensilica/Nutt); ESP-IDF's
   FreeRTOS port is windowed. Nobody publishes a call0 build: each
   repository has one set of archives per chip (§3.4).

### 2.3 What else the code uses

Counted over the disassembly (script `scripts/insn.py`):

- **No privileged or interrupt-control instruction** in any library except
  `libwapi`, which writes `LBEG`/`LEND` and reads `LCOUNT` (loop registers,
  one site) and has one `isync`. There is no `rsil`, `waiti`, `wsr
  intenable`, `rfe`/`rfi`, `s32c1i` or `ee.*` anywhere. Every interrupt mask
  and every critical section goes through the OS adapter table.
- **Zero-overhead loops:** `loop`/`loopnez`/`loopgtz` in libpp (9),
  libnet80211 (18), libphy (35), libbtdm_app (57). sawos already saves
  `LBEG`/`LEND`/`LCOUNT` per thread (ABI.md §7c).
- **Floating point:** 121 FP instructions in libpp, 42 in libmesh, 2 in
  librftest; **boolean registers** (`bt`/`bf`) in libpp (8). The kernel
  must carry FP and `BR` state for these threads (§3.2).
- **`movsp`** (the windowed `alloca`) in libpp (4), librftest, libbttestmode:
  the kernel must handle the Alloca exception (cause 5).
- No MAC16 instruction.

### 2.4 Undefined symbols, grouped by who supplies them

Union of undefined minus union of defined, with each name looked up in IDF
v6.1's ROM linker scripts (`components/esp_rom/esp32s3/ld/*.ld`). Script:
`scripts/undef.py`; lists in `an/undef-{wifi,bt,both}.txt`.

```
set                         total  ROM fn  ROM RAM  ROM rodata libc libgcc other
Wi-Fi (core,net80211,pp,
       phy,coexist)          153     11       60        3       9     16     54
BLE (btdm_app,btbb,phy,
       coexist)              202     62       80       42       6      6      6
both                         325     67      134       45       9     17     53
```

(ROM fn excludes the libc and libgcc rows.) The groups:

1. **The OS adapter tables, reached through fixed ROM RAM.** The libraries
   do not call an operating system by name. They load a table pointer from
   a fixed address the ROM reserves: `g_osi_funcs_p` = 0x3FCE_F940,
   `g_coa_funcs_p` = 0x3FCE_F824 (coexistence), and ~130 more ROM-reserved
   words such as `pp_wdev_funcs`, `net80211_funcs`, `coexist_funcs`,
   `g_wifi_global_lock`, `s_wifi_queue`, `pp_task_hdl` (all between
   0x3FCE_F820 and 0x3FCE_FFA8). The tables themselves are IDF source:
   - `wifi_osi_funcs_t` (`esp_wifi/include/esp_private/wifi_os_adapter.h`,
     version 9): 119 initialisers in `esp_wifi/esp32s3/esp_adapter.c`.
   - the coexistence adapter (`esp_coex/esp32s3/esp_coex_adapter.c`): 18.
   - the BT controller's `struct osi_funcs_t`
     (`bt/controller/esp32c3/bt.c`, the C3 family file that covers the S3,
     version 0x0001000B): 63.
2. **ROM code the libraries call directly** (11 for Wi-Fi):
   `phy_get_romfuncs` (0x4000_5C58, which returns the ROM PHY function
   table), `rom_phy_param_addr`, `rom_read_hw_noisefloor`,
   `rom_rx_gain_force`, `ets_delay_us` (0x4000_0600), `ets_backup_dma_copy`,
   `roundup2`, `coex_core_ble_conn_dyn_prio_get`, and three ROM version
   getters. The BLE controller calls 62 ROM functions (`r_*`, `lld_*`, ...):
   on this part most of the BLE link layer is mask ROM, patched by
   `libbtdm_app`.
3. **The ROM also holds a share of the Wi-Fi MAC.** `esp32s3.rom.ld`'s
   groups `rom_pp`, `rom_net80211`, `rom_coexist` and `rom_phy` assign ROM
   addresses to functions the libraries also define (`ppEnqueueRxq`,
   `lmacRxDone`, ...: defined in `pp.o`/`lmac.o`, assigned in the ROM
   script). IDF always links that script, so in an IDF build the ROM copy
   is the one used for those names. A sawos link must reproduce IDF's
   choice exactly; whether `ld.lld` resolves a script assignment against
   an archive definition the way IDF's GNU ld does is **not checked** (§9,
   unit R1 checks it).
4. **The C library:** `memcpy`, `memset`, `memmove`, `memcmp`, `strlen`,
   `strncmp`, `strcpy`, `strncpy`, `strnlen` (all in ROM, windowed), plus
   `free`, `puts` and `sprintf` with no ROM entry. libgcc's double and
   64-bit helpers (`__adddf3`, `__udivdi3`, ...) are in ROM too.
5. **IDF glue the libraries name** (the "other" column): `WIFI_EVENT` (an
   event base string), `regdomain_table` and `regulatory_data`
   (`esp_wifi/regulatory/esp_wifi_regulatory.c`, Apache-2.0),
   `phy_enter_critical`/`phy_exit_critical` and the `*_printf` log hooks
   (`esp_phy/src`, `esp_wifi/src/lib_printf.c`), and, for FTM and mesh
   objects a station never links, `est_PHY_*` and `mesh_*`.
6. **Glue the libraries do not name but need, reached through the tables
   or called by the IDF code that drives them:** PHY bring-up
   (`esp_phy/src/phy_init.c`, `register_chipv7_phy`), the PHY init data
   with its TX power limits (`esp_phy/esp32s3/phy_init_data.c`), Wi-Fi
   init (`esp_wifi/src/wifi_init.c`), the modem clock and reset code, the
   eFuse MAC read, timers (`ets_timer_*` over `esp_timer`), the event loop,
   NVS, and, for WPA2, the supplicant (`components/wpa_supplicant`, hostap
   code, BSD per its file headers) with its crypto.

### 2.5 Sections that must live somewhere particular

- **The ROM's RAM.** The ROM's Wi-Fi, PHY, coexistence and BT code keeps
  state in DRAM it reserves: `.data_pp_rom`, `.bss_pp_rom`,
  `.data_phyrom` (0x298 bytes at 0x3FCE_F3D4), the `.bss.interface.*`
  table words, `.bss.interface.bluetooth` (0x598 at 0x3FCE_FA14), from the
  ROM ELF's section headers. The ROM ELF's symbol
  `_dram0_rtos_reserved_start` is **0x3FCE_EE34**, and IDF reserves
  `ets_rom_layout_p->dram0_rtos_reserved_start` up to 0x3FCF_0000 for the
  life of the application (`heap/port/memory_layout_utils.c`, lines
  58-67). sawos today hands 0x3FCB_0000-0x3FCF_0000 to processes
  (`hal.RAM_TOP` = 0x3FCF_0000) and puts the case pool at
  0x3FCE_0000-0x3FCE_FFFF, over it. **Rule 2.1:** a board image that may
  run the radio reserves 0x3FCE_EE34-0x3FCF_0000 (4,556 bytes) from boot,
  writes nothing there, and grants it to the radio process alone. Whether
  the ROM's start-up initialises `.data_phyrom` before direct boot and
  nothing re-initialises it later is believed (IDF never re-initialises
  it) and not checked; R3 reads it back on the board.
- **IRAM is a performance choice here, not a requirement.** IDF places
  `.iram1` and the `.wifi*iram` sections in IRAM because IDF disables the
  flash cache while it writes flash, and because of speed. IDF's own
  default scheme sends every `.wifi*iram` section to `flash_text` when the
  IRAM options are off (`esp_wifi/linker.lf`, `scheme:wifi_default`), and
  documents `ESP_WIFI_IRAM_OPT` as a throughput option worth ~15 kB of IRAM
  (`wifi-performance-and-power-save.rst`). sawos never writes flash at run
  time and never disables the cache, and none of the libraries references
  a cache, SPI-flash or `Cache_*` ROM function (verified: no such name is
  undefined in any of them). **Rule 2.2:** the radio's code runs in place
  from flash like every process's; `.iram1` included. A later unit may move
  hot sections to IRAM for speed.
- **DMA-reachable data must be internal SRAM.** The adapter's
  `_malloc_internal`, `_calloc_internal`, `_zalloc_internal` ask for
  `MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL` (`esp_adapter.c` lines 412-426);
  queue storage is internal (line 116-124: "Wi-Fi still use internal
  RAM"); static RX buffers are "a kind of DMA memory" (`overview.rst`
  l.1249). The `_wifi_malloc` family may prefer PSRAM on this part when
  IDF's `SPIRAM_TRY_ALLOCATE_WIFI_LWIP` is set (lines 71-104), so dynamic
  frame buffers may live in PSRAM, as IDF allows.
- **Interrupt context.** In IDF the Wi-Fi MAC handler is registered at CPU
  interrupt 0 (`ETS_WMAC_INUM`, `soc/soc.h` l.212), a level-1 interrupt
  (ABI.md §5d's table), and the BT controller allocates its sources at
  level 3 with `ESP_INTR_FLAG_IRAM` (`bt.c` l.875-877). §4.3 is what sawos
  does instead.

### 2.6 eFuses and non-volatile storage: what the files show

**eFuses: reads only, as far as a binary can show.**

- Every word in every PROGBITS section of the Wi-Fi, PHY, coexistence and
  BLE objects was scanned for the eFuse block (0x6000_7000-0x6000_71FF;
  `scripts/mmio.py`, `scripts/words.py`). Four words occur, all in
  `libphy`, all read registers: `EFUSE_RD_MAC_SPI_SYS_0` (+0x44) and
  `_1` (+0x48) in `esp_phy_efuse_get_mac`, `EFUSE_RD_MAC_SPI_SYS_3` (+0x50)
  in `esp_phy_efuse_get_chip_ver_pkg`, `EFUSE_RD_SYS_PART2_DATA0` (+0x15C)
  in `esp_recover_efuse_data` (RF test only). The disassembly of each is
  `l32r` of the address, `memw`, `l32i`: loads, no stores.
- None of `EFUSE_PGM_DATA0` (+0x000), `EFUSE_CONF` (+0x1CC, which takes the
  write op code), `EFUSE_CMD` (+0x1D4, `PGM_CMD`), the block base itself,
  or the op codes 0x5A5A/0x5AA5 occurs as a literal word anywhere in them.
- In the mask ROM, only `ets_efuse_program` and `ets_efuse_read` load the
  `CONF`/`CMD` addresses, and `ets_efuse_program` is called only from
  `ets_efuse_write_key`, `ets_secure_boot_revoke_public_key_digest` and the
  ROM's `__call_` export stubs (call graph from the ROM ELF's disassembly).
  No library references any of those names.
- **What a binary cannot show:** an address computed at run time (a base
  plus an offset built in registers) or a call through a function pointer
  into ROM. A literal scan proves the obvious paths absent, not every path.
  IDF's `phy_override.c` (l.104-110) says the libphy archive "directly
  access[es] the eFuse ... (e.g. REG_READ)", which matches what was found.

**Non-volatile storage: none needed.**

- The libraries reach NVS only through the adapter's `_nvs_*` entries. IDF
  passes `nvs_enable` in `wifi_init_config_t`; with it 0 the Wi-Fi
  configuration is not stored (`overview.rst` l.1294).
- PHY calibration storage is IDF source, not library code: with
  `ESP_PHY_CALIBRATION_AND_DATA_STORAGE` off, `phy_init.c` calls
  `register_chipv7_phy(init_data, cal_data, PHY_RF_CAL_FULL)` and touches
  no NVS (l.946-971). The cost is "100 ms longer" per boot, and IDF
  recommends full calibration when boot time does not matter
  (`RF_calibration.rst` l.34). The calibration blob is 1,904 bytes
  (`esp_phy_init.h`: version 4, mac 6, opaque 1894).
- **Rule 2.3:** the radio process supplies an `_nvs_*` implementation held
  in RAM, never flash, and prints a line on the first write, so a library
  that tries to persist anything is visible in the transcript.
- None of the libraries references the SPI flash controllers: no
  word-aligned address in the SPI0/SPI1 pages (0x6000_2000, 0x6000_3000)
  occurs; the one word in that range (0x6000_2682 in `lld_adv.o`) is not
  word-aligned, so not a register address.

### 2.7 What the libraries touch directly

Peripheral pages whose addresses occur as literal words (counts are
occurrences; a count of 1 can be a coincidental constant):

```
libpp        0x6003_3000 (228)  0x6003_4000 (42)  0x6003_5000 (163)  Wi-Fi MAC
libnet80211  0x6003_5000 (29)
libphy       0x6000_6000 FE (130)  0x6001_C000 NRX/BB (100)
             0x6000_E000 analog I2C master (61)  0x6000_8000 RTC_CNTL (13)
             0x6001_D000 BB (9)  0x6002_6000 APB_CTRL (7)  eFuse reads (4)
libbtbb      0x6001_1000 BT (43)  0x6001_C000 (22)  0x6000_E000 (4)
libbtdm_app  0x6003_1000 (207)  0x6001_1000 BT (31)  0x6004_2000 (15)
libcoexist   0x6003_5000 (9)  0x6003_3000 (2)
```

Names from IDF's `soc/esp32s3/register/soc/reg_base.h`; 0x6003_3000-
0x6003_5FFF has no name there (`WDEV_RND_REG` = 0x6003_507C is in it,
`wdev_reg.h`). The addresses are absolute in the code, so on any tier the
radio's windows must sit at their physical addresses: SO-95's rule 4.5
("a driver finds its registers through `Mapping.base()`") cannot hold for
a closed library, and on the S3 needs not, since nothing translates.

---

## 3. The calling-convention question

### 3.1 The options, priced

**(a) The radio process is a windowed program.** Everything linked into it
is compiled windowed: Espressif's archives as they are, our adapter in C,
the syscall stub, `sosrt`'s C support, and Saw. Verified on a probe:

```
sawc build -O2 -c --freestanding --target xtensa-esp-unknown-elf \
     --target-features +windowed x.saw
probe_side:  entry a1, 32 ... l32r a8, <c_side> ; callx8 a8 ... retw.n
(the same source without the flag: addi a1, a1, -16 ... callx0 a8 ... ret.n)
```

Blade already passes a manifest's `target-features` to both halves of an
image, after the triple's `-windowed` (`blade/src/sosimg.saw`,
`native_compile_arguments`: "a later mention wins"), so
`[sos.xtensa-esp-unknown-elf] target-features = "+windowed"` is believed to
build a whole package windowed; R1 confirms it. A bonus: the only helper
libraries Espressif ships for this core, libgcc and compiler-rt, and the
ROM's libc, are windowed (ABI.md §1d), so a windowed process has them for
free.

What the kernel needs (ABI.md §7d lists the shape):

1. **The six window vectors** at VECBASE+0x000..0x17F become the standard
   spill and fill handlers (about a dozen instructions each; the code is
   Tensilica's, in NuttX and FreeRTOS under MIT). They run in user context
   with EXCM set and touch only the faulting thread's stack, so they need
   no kernel entry. Today they are fatal stubs (`boot.S` l.250-266).
2. **The Alloca exception** (cause 5, from `movsp`): spill the caller's
   window and return to the `movsp`. A few dozen instructions.
3. **The trap frame.** At entry from a windowed thread, save the current
   window's a0-a15 as today, then rotate (`rotw 4`, three times) and save
   the other 48 physical registers, plus `WINDOWBASE` and `WINDOWSTART`;
   run the kernel with one window and WOE clear, as now; restore in
   reverse. This never writes the user's stack from kernel code (spilling
   at entry would, and would make every trap a copy door). Cost: about 110
   more loads and stores per trap. Memory cost: none, since the frame grows
   from 104 to about 304 bytes and `THREAD_FRAME_STRIDE` already rounds to
   512 (`kcore.limits`, `FRAME_STRIDE_UNIT` 512).
4. **Thread start.** A windowed entry needs `PS.WOE=1`, `CALLINC` set so
   its `entry` rotates, `WINDOWSTART` with one bit, a null return address
   and the 16-byte base save area above the stack top.
5. **The double exception.** A spill handler that faults on a bad stack
   arrives at the double vector. Today that is a kernel bug; it becomes a
   fault of the process when `DEPC` lies in the window vectors.
6. A per-thread or per-process flag says which frame shape a thread has,
   or every thread on the S3 takes the 64-register path (simpler; the cost
   above is small). Call0 processes are unchanged either way.

Price: one medium unit (K1), emulator-gated by smoke cases that force
overflow and underflow at every call size, alloca, a preemption deep in a
windowed call chain, two windowed threads switching, and a windowed and a
call0 process side by side; then one board stage.

**(b) Thunks at every call across the boundary.** A call0 radio process
would wrap each library entry point (call0 to windowed: enable WOE, set up
a frame, `callx8`) and, worse, every function the libraries call: the
adapter tables' ~200 entries, libc, logging, each a windowed-to-call0
thunk, since the libraries call them with `callx8`. Window exceptions still
fire inside the libraries, so the kernel needs every item of (a) anyway.
Price: (a) plus a few hundred thunks, for nothing, since `sawc` can emit
windowed Saw. **Rejected.**

**(c) Call0 libraries.** Espressif publishes none: each repository has one
archive set per chip and every S3 object is `ABI=0` (§2.2). Asking
Espressif for call0 builds is possible (a GitHub issue on the library
repositories); nothing suggests they would maintain a variant, and the
mask ROM's Wi-Fi, PHY and BLE code that the libraries call is windowed
regardless and cannot be rebuilt. **Rejected as a plan; costs nothing to
ask** (question 6).

**(d) Converting windowed objects to call0 mechanically.** Windowed code
names registers relative to a rotating window: a `call8` callee's a0-a7 are
its caller's a8-a15. A translator would have to re-allocate every
function's registers into one flat set of 16, insert saves where the
window used to hide them, handle `movsp` and the hand-written assembly
paths, rewrite every indirect call through the adapter tables, and redo it
for each library release. It must also stop the libraries calling ROM
code, which is windowed. Not practical. **Rejected.**

**(e) The radio process all C, Saw elsewhere behind a pipe.** This was the
fallback if Saw could not be windowed. It can, so the radio process may
contain Saw where that helps (its serve loop and protocol code), and it
remains a choice rather than a constraint.

**The design takes (a).**

### 3.2 The rest of the thread state

- **FP and `BR`.** The S3 HAL keeps `CPENABLE` = 0 and carries no FP state
  (spec §7). libpp executes FP instructions and boolean branches, so a
  radio thread needs `CPENABLE` bit 0, and a switch must carry `f0`-`f15`,
  `FCR`, `FSR` and `BR` (19 words). Lazily, on the cause-32 trap, as spec
  §7 already describes for Profile B. One small-to-medium unit (K2); the
  state fits in the 512-byte frame stride beside the windowed frame.
- **Word atomics.** The libraries use none; the adapter's own `s32c1i`
  works at DRAM addresses under the ROM's `ATOMCTL` (ABI.md §8).
- **The loop registers** are already in the frame.

---

## 4. What the libraries need from an operating system

### 4.1 The adapter, group by group

The radio process implements the three tables in C, compiled windowed,
over sawos objects. "Thread" below is a sawos thread of the radio process.

```
group (table entries)           sawos realisation                    new?
tasks: create, delete, delay,   a thread per task, stack from the     no
  yield, current, max prio      process's internal arena; delay = a
                                timed block (below); priorities map
                                to one band (§4.3)
semaphores, mutexes (recursive),userspace objects on word atomics;    no
  queues, event groups,         a blocked thread parks on its own
  "thread semaphore"            Event; give/send signals the
                                waiter's Event
timeouts on any of the above    the process's timer thread signals    no
                                the parked thread's Event with a
                                timeout mark; one kernel Timer
software timers (setfn, arm,    a deadline list in the process; one   no
  arm_us, disarm, done)         sawos Timer at the earliest; the
                                timer thread runs callbacks (IDF's
                                esp_timer task does the same)
time: esp_timer_get_time,       a Clock read, microseconds            no
  get_time, log_timestamp
memory: malloc, free,           an allocator over two arenas the      no
  *_internal (DMA|INTERNAL),    process maps at start: internal SRAM
  wifi_malloc (may be PSRAM),   (DMA-capable) and, if given, PSRAM;
  get_free_heap_size            no kernel call after start
critical sections:              one process lock that excludes the    no
  wifi_int_disable/restore,     interrupt threads (the "ISR gate");
  spin_lock_create, phy         recursive; no interrupt masking
  enter/exit critical,
  BT global_intr_disable
interrupts: set_intr, set_isr,  root binds the sources and gives the  limit
  ints_on/off, clear_intr,      Interrupts; an interrupt thread per   raise
  is_from_isr, BT               line runs the registered handler
  interrupt_alloc/enable/free   (§4.3); ints_off/on take and release
                                the ISR gate; is_from_isr reads a
                                per-thread flag
event post (WIFI_EVENT ...)     a queue the serve thread drains into  no
                                protocol notifications (§5)
random: rand, random,           the RNG register WDEV_RND_REG         no
  get_random                    (0x6003_507C) in the radio's window
read_mac                        eFuse EFUSE_RD_MAC_SPI_SYS_0/1 read   no
logging: log_write(v),          a small vsnprintf in C, out through   no
  *_printf, puts, sprintf       the console (debug_print, or a
                                console-service pipe)
storage: nvs_*                  RAM only, never flash (rule 2.3)      no
phy enable/disable, clocks,     IDF's esp_phy and modem-clock code,   port
  reset MAC, RTC isolation      ported (Apache-2.0), writing SYSTEM,
                                APB_CTRL, RTC_CNTL bits (§4.5)
coex hooks (~20)                IDF's coex adapter over the same      no
                                primitives
BT sleep hooks, backup DMA,     unused: build with BT modem sleep     no
  IPC to the other core         off; the S3 is run single-core
dport stall                     empty, as IDF's
```

Nothing in the table needs a new kernel object kind. What it needs is
capacity (§4.4), interrupt latency (§4.3), and windowed and FP threads
(§3).

### 4.2 Why a userspace implementation of the RTOS objects is enough

The libraries never block in a kernel sense; they call `_semphr_take`,
`_queue_recv` and friends, and our implementation decides what blocking
means. A process-local object with a waiter list, made atomic with the
process lock, and one Event per thread to park on, gives FreeRTOS
semantics without kernel help. esp-radio does the same over its own
scheduler (`esp-radio/src/compat/`). The libraries' `_is_from_isr` decides
whether they call the `FromISR` forms; esp-radio's answers `true`
unconditionally, on every chip (`os_adapter/mod.rs`, `is_from_isr`), which
suggests the libraries tolerate a coarse answer, but the design answers
truthfully per thread.

### 4.3 Interrupt handlers

**What the libraries expect.** IDF runs the Wi-Fi MAC handler
(`wDev_ProcessFiq`, `libpp/wdev.o`) as a level-1 interrupt handler, and the
BT controller's at level 3 from IRAM. The Wi-Fi handler's disassembly is
short (98 lines) and calls `hal_mac_interrupt_get_event`,
`hal_mac_interrupt_clr_event`, `hal_pwr_interrupt_get_event`/`clr_event`,
`lmacProcessRxSucData`, `lmacPostTxComplete`, `lmacProcessAllTxTimeout`,
`lmacProcessCollisions`, `wdev_process_tbtt`, `wdev_process_tsf_timer`,
`wdev_process_beacon_filter` and `pp_post`. It reads and clears the MAC's
status itself and hands work to the `pp` task through `pp_post` (a
`queue_send_from_isr`). So the handler is a short top half that clears the
device and defers.

**What sawos does.** An interrupt masks its line and wakes the thread
waiting on its Interrupt (spec §9). The radio process runs one interrupt
thread per line: wait, take the ISR gate, set the per-thread "in ISR"
flag, call the registered handler with its argument, clear the flag,
release the gate, `ack`. This is spec §9's canonical driver shape exactly:
everything that touches the device happens before the ack, and the
handler clears the device's status, so the re-armed line does not refire.

**Can it meet the radio's needs?** Partly settled, partly not:

1. **Ordering is satisfied by construction.** The handler cannot run
   inside a task's critical section, because both take the ISR gate. A task
   cannot run "inside" the handler, because the handler thread holds the
   gate until it returns. That is what masking gave the libraries.
2. **Latency is the open question.** Today a wake never preempts (spec
   §7: only a tick, a yield, a block or an exit reschedules), the tick is
   10 ms, and the run queue is round-robin. A radio interrupt taken while
   another process spins waits up to one tick per runnable thread ahead of
   it. The Wi-Fi hardware acknowledges frames itself and holds received
   frames in the static RX ring (default 10 buffers; IDF's minimum rank on
   the S3 is 3) until the handler runs; at the box's traffic levels a few
   milliseconds of delay costs throughput, not correctness, but
   `lmacProcessAllTxTimeout`, TBTT and beacon handling have deadlines nobody
   outside Espressif has written down. The BLE controller's level-3
   allocation says Espressif wants it ahead of ordinary work; a connection
   event can come every 7.5 ms.
3. **No integrator defers these handlers to a thread.** esp-radio binds
   `WIFI_MAC`/`WIFI_PWR` as real interrupts at priority 1 and calls the
   handler from them (`esp-radio/src/wifi/os_adapter/esp32s3.rs`); NuttX
   attaches them as interrupts too. So deferral is unproven by precedent.

**What the kernel must offer.** Rule 4.1: the radio's interrupt threads run
at a priority above every non-driver thread, and readying one preempts on
the way out of the kernel. This is spec §7's specified, unbuilt
fixed-priority scheduler ("Preemption is immediate: readying a
higher-priority thread switches on the way out of the kernel") together
with its direct-switch fastpath. With it, the latency from the line to the
handler is a trap, the delivery funnel and one switch, plus at most one
`PREEMPT_STRIDE` of a long kernel copy: microseconds at the PLL clock.
Without it, the Wi-Fi radio is believed to work slowly and BLE to drop
connections at short intervals; both are guesses, so R3 measures the
latency (the interrupt thread reads SYSTIMER on waking and the kernel's
fire time is printed beside it by a diagnostic build).

If even that were not enough, the next step is a kernel that runs the
bound thread's handler entry directly from the trap (an upcall), and the
last is running the handler in kernel context, which puts closed code in
the kernel's mode and is rejected here: on the flat tier it buys nothing
protection-wise and on any other tier it gives the library everything.

**Priority inversion.** A task holding the ISR gate can be preempted by a
higher thread elsewhere while the interrupt thread waits for the gate.
Rule 4.2: all of the radio process's threads run in one band above every
application, so only radio threads compete for the gate, and the gate is
held only across the libraries' own short critical sections.

### 4.4 Kernel capacity

The radio process alone wants about: 6-7 threads (the Wi-Fi task, the BT
controller task, two to four interrupt threads, a timer thread, a serve
thread), as many Events, 3-4 Waiters, 1-2 Timers, 4 Interrupts (sources 0
`WIFI_MAC`, 2 `WIFI_PWR`, 5 `BT_BB`, 8 `RWBLE`, from IDF's
`soc/interrupts.h`), 3 served pipes, 2-3 Memory objects and about 10
device windows. Today's machine-wide slabs (`kcore.limits`):
`MAX_THREADS` 8, `MAX_INTERRUPTS` 4, `MAX_WAITERS` 4, `MAX_PIPES` 4,
`MAX_TIMERS` 8, `MAX_PROCESSES` 3, `MAX_MAPPINGS` 8. A networking image
with a radio, a stack, a BLE host and one application cannot fit. Under the
owner's rule these are raised per board through SO-95's limits, not
designed around. The S3's nine device CPU interrupts (`DEVICE_CPU_INTS`)
are enough for four radio lines and the box's GPIO.

### 4.5 Clocks and the PLL

- **Wi-Fi needs APB at its maximum.** IDF creates the Wi-Fi power lock as
  `ESP_PM_APB_FREQ_MAX` unless `SOC_MODEM_APB_CLOCK_IS_INDEPENDENT`
  (`wifi_init.c` l.497-502), and the S3's `soc_caps.h` does not define it.
  On this part APB is 80 MHz only when the CPU runs from the PLL; on XTAL,
  APB equals the CPU clock (SO-77 §2.6, from IDF's `rtc_clk.c`).
- **The kernel runs on the ROM's clock** and changes none (ABI.md §11);
  SO-77 §6 records that the part's CPU clock has never been read and that
  raising it means the BBPLL over the analog I2C bus, the eFuse-calibrated
  voltage bias, `CPU_PER_CONF` and `SOC_CLK_SEL`, and moving UART0 to the
  crystal first so the console's divisor stays right.
- **Rule 4.3:** the HAL brings the CPU to the PLL (80 or 160 MHz; 240 MHz
  needs the higher voltage bias and is not needed) once, at boot, before any
  process runs, and only on boards whose description asks for it. The radio
  never changes the CPU clock. The PHY then manages the radio's own clocks
  and tracks the PLL against temperature from a periodic timer
  (`phy_common.c`, `phy_track_pll`), through the same analog I2C master
  the HAL used at boot, so the two never run at once.
- **`ets_delay_us`** in ROM spins on `CCOUNT` against a ticks-per-µs value
  the ROM keeps in its reserved RAM. Either the HAL updates it when it
  raises the clock (the ROM's `ets_update_cpu_frequency`) or the radio
  links its own `ets_delay_us` on SYSTIMER ahead of the ROM's; the second
  touches no ROM state and is what the design takes.
- SYSTIMER (16 MHz, from the crystal) and LEDC on XTAL (SO-77) are
  unaffected.

### 4.6 RAM, against SO-102's PSRAM

What must be internal SRAM: DMA buffers and everything the libraries
allocate as internal; the ROM's reserved 4,556 bytes; the radio's stacks
and the interrupt threads' working set (believed, for latency: PSRAM is
behind the cache). What may be PSRAM: dynamic frame buffers (IDF allows it
on this part), the stack process's buffers, everything else.

```
item                               Wi-Fi only    BLE only     both    basis
ROM reserved (fixed address)          4.5 KB      4.5 KB     4.5 KB   ELF
library .data+.bss+.dram1           15.2 KB      2.8 KB    17.2 KB   measured*
static RX DMA buffers (3..10)     4.8-16 KB          -  4.8-16 KB   docs
dynamic RX+TX peak (6+6..32+32)    19-100 KB         -   19-100 KB   docs
controller heap (BLE)                    -    15-30 KB   15-30 KB   believed
PHY state, cal data, supplicant,
  scan results, node tables        15-25 KB     3-5 KB   18-30 KB   believed
stacks (Wi-Fi/BT tasks, ISR, timer,
  serve)                            12-16 KB    8-12 KB   18-24 KB   believed
adapter, queues, event structures    3-5 KB      2-3 KB     5-8 KB   believed
total internal, minimum rank         ~70 KB      ~40 KB    ~95 KB
total internal, IDF default rank    ~110 KB      ~50 KB   ~140 KB
```

`*` From §2.1's section sums for core, net80211, pp, phy and coexist
(Wi-Fi) and btdm_app, btbb, phy (BLE): an upper bound before
`--gc-sections`. IDF's own table for the S3 (`wifi-performance-and-power-
save.rst`) reports 273.6 KB free with its minimum rank and 183.9 KB with
its default, a 90 KB difference of which 52 KB is lwIP's larger TCP
windows; that is the basis for the gap between the two totals.

Against the board: 256 KB of DRAM is process RAM today (0x3FCB_0000-
0x3FCF_0000), less the ROM's 4.5 KB. With SO-102 moving root, the box's
processes and the stack's buffers to PSRAM, 100-140 KB of internal SRAM for
the radio is available. Without SO-102 it is not, once the box runs.
**Rule 4.4:** the radio's adapter keeps a high-water mark of each arena and
prints it at every board stage, so these estimates are replaced by
measurements before anyone sizes a limit on them.

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
  riscv32/arm64 virt     Espressif libs, Saw serve loop) ---+
 svc-openeth (Saw)          also serves wlanproto to a manager
  S3 emulator only
```

1. **`svc-radio-esp32s3`**, one process, because Wi-Fi, BLE, the PHY and
   the coexistence library share the radio, its calibration and one set of
   ROM tables. It holds: the radio's device windows (Wi-Fi MAC
   0x6003_3000-0x6003_5FFF, FE/FE2, BB/NRX, the analog I2C master, BT, the
   eFuse block for reads, and the shared clock and reset registers it
   writes, §5.6), the ROM's reserved RAM, its four Interrupts, a Clock, its
   internal SRAM arena (`ram = "fast"`), optionally a PSRAM arena, and the
   outlets of three protocols. It is a windowed program.
2. **`svc-net`**, the TCP/IP stack, in Saw, call0. A client of one or more
   `netdevproto` connections; serves `sockproto`. Holds no device.
3. **`svc-ble`**, the BLE host, in Saw. A client of `hciproto`; serves
   `gattproto`. Holds no device.
4. **A network manager** (may be root, or a small process): the only holder
   of the `wlanproto` connection, which carries scan, connect (SSID and
   key), disconnect and status. Applications never hold it.
5. **Applications**, clients of `sockproto` and `gattproto`.
6. **`svc-virtio-net`** and **`svc-openeth`**: device drivers that serve
   the same `netdevproto` on the emulators, so the stack never knows which
   it has.

### 5.2 Protocol packages, in the `uartproto` pattern

Each is a library package in `drivers/` that the server and every client
compile against: op byte, payload, constants, `describe()`.

- **`pktring`**: not a protocol but the shared-memory packet ring both
  packet protocols use. `PIPE_BODY_BYTES` is 128 and cannot exceed 255
  (spec §2.1), so a 1,514-byte frame or a 255-byte HCI ACL packet cannot
  ride a message. The client allocates one Memory object per connection
  from its pool and gives it to the server at `Attach`; both map it. It
  holds two rings of fixed-size slots (2 KB for frames; 512 bytes for HCI)
  and two index words per ring. Messages carry only doorbells and slot
  numbers.
- **`netdevproto`** ops: `Attach(ring memory)` (a call; the reply carries
  the MAC address, MTU, slot counts), `Tx(first, count)` (a tell: frames in
  TX slots are ready), `RxReturn(first, count)` (a tell: RX slots consumed),
  `WaitEvent` (a held call the server answers with the next event: frames
  received in slots a..b, TX slots completed, link up with speed, link
  down), `Stats`.
- **`hciproto`**: the same shape with H4 packet types (command, ACL, event,
  ISO) in slots.
- **`wlanproto`** ops: `Scan` (a call; results in a reply series or the
  ring), `Connect(ssid, auth, key)`, `Disconnect`, `Status`,
  `WaitEvent`. The key travels once, over this connection only.
- **`sockproto`**: `UdpOpen(port)`, `TcpConnect(addr, port)`,
  `TcpListen(port)`, `Accept`, `Send`, `Recv`, `Close`, `Resolve(name)`,
  `IfStatus`. Each socket is a pipe connection of its own (a `PipeInlet`
  the stack hands back), so closing it is dropping a handle and the
  stack learns of a dead client from peer-closed. Bulk data rides a
  per-socket `pktring`.
- **`gattproto`**: `Advertise(data)`, `AddService(table)`, `Notify`,
  `WaitEvent` (connect, disconnect, write to a characteristic).

### 5.3 Moving buffers, and how many copies

On receive over Wi-Fi: the MAC DMAs into a library buffer; the library's RX
callback (`esp_wifi_internal_reg_rxcb`) hands it to the radio, which copies
it into an RX slot and frees the library buffer (copy 1); the stack parses
in the slot; TCP or UDP payload is copied into the socket's ring (copy 2).
On transmit: the application writes into its socket ring; the stack builds
the frame in a TX slot of the netdev ring, payload copied once (copy 1);
the radio calls `esp_wifi_internal_tx(slot, len)`, which copies into a
library DMA buffer (copy 2, inside the library: "makes a copy of the
upper-layer buffer", `overview.rst` l.1261-1268).

Copy 1 on receive could be avoided only by letting the stack read the
library's own DMA buffers, which on a protected tier would mean sharing
the radio's heap. One copy per side at the driver boundary is the price of
keeping the closed library's memory its own. On virtio-net the driver
points the device's descriptors straight at the shared slots, so that
boundary copies nothing.

### 5.4 Flow control and failure

1. **Back-pressure is slot exhaustion.** A full TX ring makes the stack
   hold the packet (TCP) or drop it (UDP, counted). A full RX ring makes
   the server drop the frame and count it; the device ring keeps
   receiving. Neither side ever blocks on the other.
2. **A slow client** cannot stall a server: every server reply to a held
   `WaitEvent` is one message, the ring is the buffer, and a client that
   does not return RX slots stops receiving, alone.
3. **A dead client** is peer-closed on its connection (spec §2.1). The
   server drops its ring mapping and its slots; other clients are untouched.
4. **A dead server** answers every claim with `PeerClosed`. The stack marks
   the interface down and closes every TCP connection over it; applications
   see `PeerClosed` on their sockets. A supervisor (root) may restart the
   radio process; the new one recalibrates and the manager reconnects.
5. **A dead radio process leaves hardware live.** Interrupt lines are
   masked by the kernel at teardown (an Interrupt's last rites), but the
   MAC and the PHY keep their state and the MAC's DMA may still write its
   descriptors' targets, which were in the dead process's arena. Rule 5.1:
   a radio restart resets the Wi-Fi and BT blocks (`wifi_reset_mac`,
   the modem reset bits) before any memory of the dead process is reused,
   and the radio's arena returns to its pool only after that reset. Until
   a restart unit exists, the radio's death ends the image with a console
   line.

### 5.5 How the image description names it

In SO-95's terms (rules 3.6, 4.3, 5.2), abridged:

```toml
[[process]]
name = "radio"
package = "sawos:drivers/svc-radio-esp32s3"
ram = "sram"                 # process.toml declares ram = "fast"
windows = ["WIFI", "WIFI_BB", "FE", "ANA_I2C", "BT", "EFUSE",
           "APB_CTRL", "SYSTEM", "RTC_CNTL"]
lines = ["WIFI_MAC", "WIFI_PWR", "BT_BB", "RWBLE"]
memory = ["rom_radio"]       # 0x3FCE_EE34..0x3FCF_0000, rule 2.1
serve = ["netdev0", "hci0", "wlan0"]

[[process]]
name = "net"
package = "sawos:services/svc-net"
ram = "psram"
clients = ["netdev0"]
serve = ["sock"]
```

What this asks of SO-95 that it does not have yet:

1. A board RAM range with `use = "rom"`: present on the S3 always, never
   placed into by the builder, granted only to a process that lists it.
2. Device rows for the radio blocks and the analog I2C master, and
   `[devices]` rows whose `shared = true` lets two processes hold windows
   on one block (SYSTEM, APB_CTRL, RTC_CNTL), with the builder naming
   every holder in its plan.
3. A package-level way to say "windowed": `target-features` in the
   manifest's triple section, carried into the builder's plan, so the
   builder can refuse an image that mixes the two in one process.
4. Linking prebuilt archives and an extra linker script (the ROM symbol
   scripts) into one package. Believed not supported by Blade today
   (`native` lists translation units); filed as a Saw gap (§9).

### 5.6 What a fault in the radio can reach

**On the flat tier, everything.** The S3 core has no privilege (ABI.md
§5e); any code can rewrite `PS`, `VECBASE`, `INTENABLE`, any memory and any
register. A fault or a hostile path in the libraries can: corrupt the
kernel and every process; reprogram clocks, the interrupt matrix and
watchdogs; drive the eFuse controller (no library references it, §2.6, but
the hardware lets any code); command the flash controller; DMA anywhere the
MAC's DMA reaches; and transmit. What sawos still gives on the flat tier:
the libraries' wild pointer usually lands in the radio's own memory or
faults; the kernel's checks on the syscall path hold; a crash is reported
with the radio named.

**With memory protection** (the S3's PMS and World Controller, unprobed;
or the C6's PMP, §8): CPU accesses from the radio process are confined to
its rows, so a wild store faults the radio and not the kernel, and the
eFuse and flash controllers can be left out of its rows entirely (eFuse
reads would then go through a small service, or the kernel). Two things
protection does not change: **DMA**, which is the MAC's and not the CPU's,
is not confined by CPU-side rows (whether the S3's PMS covers the Wi-Fi
MAC's DMA is not known); and **the shared clock and reset registers**,
which want a broker (a clock service or a kernel op) rather than shared
windows on a protected tier.

---

## 6. The stacks

### 6.1 TCP/IP

**(a) Port lwIP.** BSD 3-clause (lwIP's `COPYING`). The Espressif fork IDF
pins compiles call0 for the S3 with Espressif's clang (verified: 31 objects
of `core/`, `core/ipv4/` and `netif/ethernet.c`, `NO_SYS=1`, IPv4, TCP,
UDP, DHCP, DNS, ARP, ACD, `-Os`; 0 `entry`, 280 `ret.n`, 759 `callx0`).
Size before any `--gc-sections`: 49.8 KB of text, 44.9 KB of `.bss` with a
16 KB heap and 16 pbufs. Its only external needs: `memcpy`, `memset`,
`memmove`, `memcmp`, `strlen`, `strncmp`, `atoi`, `sys_now`. With
`NO_SYS=1` it needs no OS: one thread calls it, and timers are a
`sys_check_timeouts()` call. The fork needs three Espressif-only macros
defined (`ESP_LWIP_*_TIMERS_ONDEMAND`). Cost: a medium unit to wrap it as
`svc-net` behind `netdevproto` and `sockproto`, mostly the pbuf-to-slot
glue. Risk: C in a Saw system, a second memory allocator, and a code base
nobody here wrote.

Other C stacks: uIP (BSD, tiny, TCP with one segment in flight); picoTCP
(GPL v2, which a sawos image could not mix with closed archives
comfortably); Zephyr's native stack (Apache-2.0, tied to Zephyr's kernel
APIs); smoltcp is Rust. None beats lwIP for a C port. (Licences other than
lwIP's are from documentation, not checked here.)

**(b) Write one in Saw.** The minimal honest stack:

```
part        contents                                        size (Saw lines,
                                                            believed)
Ethernet    frame parse/build, MAC filter                       200
ARP         request/reply, cache with expiry, pending queue     400
IPv4        header check, checksum, no fragmentation sent,      600
            reassembly of fragments received (bounded)
ICMP        echo reply, unreachable                             200
UDP         sockets, checksum                                   400
DHCP        client: discover, request, renew, rebind, lease     700
DNS         stub resolver, A records, one server, cache         600
TCP         state machine (11 states), RTO and backoff, slow   3500-5000
            start/congestion avoidance, window management,
            out-of-order queue, keepalive, TIME_WAIT
sockets     sockproto server, per-socket rings                  800
total                                                        7400-8900
```

The sawlang archive's calibration agrees ("Whole stack ~8-15k lines Saw
(smoltcp/uIP are the calibration points)", `designs/todo.md` in the sawlang
checkout, the HARDWARE PATH section). IPv6 adds neighbour discovery,
SLAAC, ICMPv6 and MLD, roughly another 2,500 lines and a second address
family through every socket path; nothing in the box or the first year
needs it. **Where the risk is: TCP.** The state machine is specified; the
hard parts are the timers (RTO estimation, persist, delayed ACK), loss
recovery and interoperating with real peers' quirks. The mitigation is the
test harness (§7): packet-level goldens for each state transition, then a
real Linux TCP as the peer.

**(c) lwIP first, Saw later.** It would put a network on the radio sooner,
but the radio is months away (§10) and the Saw stack can be ready before
it; porting lwIP would build glue that is thrown away.

**Recommendation: (b), in Saw**, staged so UDP, DHCP and ping work over
virtio-net before TCP starts. This follows the owner's preference and the
earlier ruled direction (a small TCP/IP stack in Saw, the sawlang
archive). lwIP stays named as the fallback if TCP in Saw stalls: it is a
medium unit, verified to compile for this core.

### 6.2 The BLE host (BLE only on this part)

The S3's controller speaks HCI through an in-process call interface
(`API_vhci_host_send_packet`, `API_vhci_host_register_callback`, `bt.c`
l.289-291), so the host can be anywhere the radio process can reach over a
pipe: `hciproto`.

**(a) Port NimBLE.** Apache-2.0 (esp-nimble's `LICENSE`). Needs NimBLE's
porting layer (event queues, mutexes, semaphores, callouts), which IDF
implements in `bt/porting/npl`; ESP-NimBLE is a qualified host
(`ble-qualification.rst`: Q371597, spec 6.1). Size not measured here;
believed to be 80-150 KB of code for a GATT peripheral. A medium-to-large
port.

**(b) Write one in Saw.** In order of need:

1. **Advertising only**: a handful of HCI commands (reset, set advertising
   parameters and data, enable). No host stack at all; a Saw program
   speaks raw HCI. Small.
2. **GATT peripheral without pairing**: L2CAP (fixed channels), ATT (the
   request/response PDUs), GATT server (service table, read, write,
   notify), GAP (connection parameters). Believed 3,000-5,000 lines.
3. **Pairing (SMP), LE Secure Connections**: the P-256 key pair and the
   Diffie-Hellman key can be done by the controller (HCI LE Read Local
   P-256 Public Key, LE Generate DHKey, from the Bluetooth Core
   Specification), and AES by HCI LE Encrypt or the S3's AES block, so
   the host does protocol, not crypto. Believed 1,500-2,500 lines.

Others: BTstack (dual licence; commercial use needs a paid licence, from
documentation), Bluedroid (Apache-2.0, large, Classic and LE), Zephyr's
host (tied to Zephyr), TrouBLE (Rust).

**Recommendation: (b), in Saw**, in that order, starting with advertising,
which is useful on its own and is the cheapest proof that the controller
runs. NimBLE is the fallback. Note §9.1's qualification consequence of a
host of our own.

---

## 7. What can be tested where

### 7.1 What the emulators have (verified, §11.3)

- **The suite's `virt` machines take virtio-net.** Homebrew QEMU 11.1.3
  lists `virtio-net-device` (virtio-mmio bus) for `-M virt` on both
  `qemu-system-riscv32` and `qemu-system-aarch64`. A dumped device tree
  with one `-device virtio-net-device` attached shows transports from
  0x1000_1000 (PLIC line 1) to 0x1000_8000 (line 8) on riscv32, and on
  arm64 the last at 0x0A00_3E00 (SPI 47; 32 transports of 0x200 from
  0x0A00_0000 is believed, from QEMU's `virt` layout). A driver finds its
  device by probing each transport's `DeviceID` register (1 is network),
  since the device tree lists every transport whether or not a device sits
  behind it.
- **Host-side backends:** `socket`, `stream`, `dgram`, `hubport`, `tap`,
  `user` (and on macOS `vmnet-*`, `vde`, `bridge`).
- **Espressif's QEMU for the S3** (`esp_develop_9.2.2_20250817`): `-nic
  model=help` with `-M esp32s3` answers "Available NIC models for this
  configuration: open_eth", and the binary's strings name
  `hw/net/opencores_eth.c` and nothing for Wi-Fi or 802.11. ABI.md §3's
  memory map already lists `open_eth` at 0x600C_D000 as NOT SILICON. Its
  interrupt source on the S3 machine is not known; a bisection probe like
  ABI.md §6c's finds it.

### 7.2 How the stack is gated without a radio

1. **Hosted unit tests** of the Saw stack's pure parts (parse and build of
   every header, ARP cache, TCP state transitions driven by input
   segments, DHCP client steps) against golden byte strings. No emulator;
   fast; deterministic.
2. **`svc-virtio-net`** on riscv32 and arm64 `virt`, flat profile
   included, with `-netdev stream` (or `dgram`) to a Unix socket the
   runner serves. The runner's peer is a scripted, deterministic Python
   program: it answers ARP, runs a DHCP server with a fixed lease, answers
   ICMP echo, echoes UDP, runs a DNS responder with one name, and for TCP
   uses the host's own TCP stack through a second netdev (`user` with
   `hostfwd`) as the real-world oracle. Every assertion is about content
   (the reply's sequence number, the echoed bytes, the leased address),
   never about time. **Rule 7.1:** no case asserts a retransmission count,
   an RTT, a timeout's length or the number of DHCP retries, which a loaded
   host can move (CLAUDE.md's ruling); a case waits for the event that
   proves the thing happened.
3. **`svc-openeth`** on the S3 emulator runs the same stack in the
   `sos-smoke-esp32s3`, so the stack is exercised on Xtensa call0 too.
4. **The radio's adapter, without the radio.** The adapter is C with no
   radio in it; built for riscv32 it runs in the main gate under a test
   program that drives each table entry the way the libraries do (FromISR
   calls from an interrupt thread, a timeout on every blocking call, timer
   arm/disarm races, nested critical sections). Built windowed for the S3,
   the same program runs in the S3 smoke and exercises windowed threads.
5. **The radio image links.** A build-only gate: the radio package links
   against Espressif's archives with no undefined symbol, its ROM-assigned
   names resolved as IDF resolves them, its sections where §2.5 says.
   Nothing executes Espressif's code in the gate.
6. **Windowed processes** in the S3 smoke (K1's cases).

### 7.3 What is left to the board, in stages

Each stage is one image written with `write-flash 0x0`, on the owner's
word, with no eFuse write or burn, no erase, and the console captured. No
stage writes flash at run time (rule 2.3). Each stage adds one thing:

```
B0  read-only probe: CPU clock (CCOUNT against SYSTIMER), MAC from the
    eFuse read registers, chip revision. (Shares SO-77's stage 0.)
B1  the clock raised to the PLL, console still readable (K4).
B2  K1's windowed cases on silicon, and K2's FP case.
B3  PHY bring-up: esp_phy_enable with full calibration; prints the PHY
    version, the MAC, the arena high-water marks, the interrupt latency.
    Calibration exercises the RF front end as every IDF Wi-Fi boot does.
B4  Wi-Fi scan: prints SSIDs, channels, RSSI. Active scan transmits
    probe requests.
B5  association with the owner's access point, open or WPA2-PSK.
B6  DHCP, ping and UDP over Wi-Fi through svc-net.
B7  BLE advertising from raw HCI; visible from a phone.
B8  a GATT connection from a phone; read and write a characteristic.
B9  Wi-Fi and BLE at once (coexistence).
```

---

## 8. Other boards: the ESP32-C6

- **The libraries' convention.** The C6 archives in the same repositories
  are RISC-V (`e_machine` RISC-V, flags `0x1` RVC, the soft-float ilp32
  ABI; arch attribute `rv32i2p0_m2p0_c2p0`, verified on `libpp`). sawos's
  riscv32 code is `rv32imac` ilp32, a superset, so the archives link into
  an ordinary sawos process. No windowed question, no K1 or K2.
- **The same interface.** The C6 libraries also reach the OS through
  `g_osi_funcs_p` (undefined in libcore, libnet80211 and libpp, as on the
  S3). `wifi_osi_funcs_t` is the same structure with six entries only the
  C6 family has (REGDMA and sleep retention) and one only parts with
  802.11ax have (`wifi_os_adapter.h` l.152-170). IDF links
  C6 ROM scripts for phy, coexist, net80211 and pp (`esp_rom/CMakeLists`),
  so the C6 also has ROM-resident Wi-Fi code and a ROM-reserved RAM area,
  at addresses its own ROM ELF gives.
- **What is shared:** the adapter (C, arch-free apart from the interrupt
  and clock glue), every protocol, the stack, the BLE host, the image
  shape, the test plan, the priority scheduler. Most of the work.
- **What the C6 port needs:** a C6 board HAL under `riscv32-common` (the C3
  HAL is the closest existing one: a different interrupt controller
  arrangement, SYSTIMER, the UART), its clock bring-up, its board
  description, the C6 BLE controller library (a different repository,
  `esp32c6-bt-lib`, not inspected), and PMP-based protection, which would
  give the radio the protected tier the S3 lacks.
- **Where the first bring-up belongs: the S3.** The board is in hand, the
  owner has not got C6 boards yet, and the S3-only cost is K1 and K2, two
  medium units whose emulator gating is good. Waiting for the C6 would
  delay everything radio-related for an unknown time to save those two.
  The C6 is then a port of a working design.

---

## 9. An attack on this design

### 9.1 Regulatory and certification

What can be cited from the files: the libraries enforce channel and power
limits from tables IDF supplies: the PHY init data (`phy_init_data.c`,
per-rate TX power limits) and the regulatory domain tables
(`esp_wifi_regulatory.c`, "to ensure that the device operates in
compliance with local regulations", its README). The design links both
unchanged and sets the country explicitly (question 4). Espressif's BLE
controller for the S3 is listed under qualified design 239440 (spec 5.4)
and ESP-NimBLE under Q371597 (`ble-qualification.rst`).

What I could not check and the owner should know: the wording of the
module's FCC/IC/CE grants and what they require of host software (that it
not change RF parameters outside the grant is the usual condition for a
modular approval); whether using Espressif's libraries under a different
operating system changes anything (believed not, provided the PHY init
data, the regulatory tables and the calibration are Espressif's own); and
the Bluetooth SIG's listing rules, under which a product using a host of
our own could not reuse ESP-NimBLE's listing for the host layer. For a
device that is not sold, the listing does not arise; radio emission rules
apply regardless.

### 9.2 Security

- An opaque library holding the radio is the most privileged code in the
  system on the flat tier (§5.6), and it is network-facing: a remote
  attacker who finds a bug in Espressif's 802.11 or BLE parsing owns the
  machine. On the flat tier sawos cannot limit that. The capability system
  still matters above it: applications hold sockets, not the radio, and on
  a protected board the radio's reach shrinks to its rows plus DMA.
- The Wi-Fi key crosses `wlanproto` once and lives in the radio and the
  supplicant thereafter. On the flat tier any process can read it.
- The libraries can transmit whatever they like; a capability system
  cannot audit what leaves the antenna.

### 9.3 Assumptions that would sink the plan if false

1. **Deferred handlers meet the radio's deadlines** once the priority
   scheduler exists (§4.3). If false, the kernel needs an upcall path, or
   the radio stays slow. Measured at B3.
2. **`ld.lld` reproduces IDF's resolution** of the ROM script's
   assignments over the archives' definitions. If false, the link must use
   GNU ld (on this machine, xtensa-esp-elf 15.2.0) or a script transform.
   Checked at R1.
3. **The ROM's reserved RAM is intact at boot** after direct boot, as
   IDF's application assumes after its bootloader. Checked at B3 by
   reading a known ROM data word against the ROM ELF's initial value.
4. **No library path writes eFuse or flash.** The literal scan and the ROM
   call graph support it (§2.6); a computed address is not excluded. The
   residual risk is the same as running IDF.
5. **The libraries tolerate one core.** IDF supports `FREERTOS_UNICORE` on
   the S3 (`bt.c` has the unicore path); believed fine.
6. **The libraries do not depend on FreeRTOS internals** beyond the tables
   (no undefined FreeRTOS symbol was found, §2.4). Verified by the symbol
   harvest; behaviour (e.g. priority assumptions between the Wi-Fi task and
   others) is not.
7. **Internal SRAM suffices** once SO-102 lands (§4.6). If false, the
   radio runs at IDF's minimum rank or BLE and Wi-Fi are not run together.
8. **The S3's analog and clock registers can be shared** between the HAL's
   boot-time clock code and the PHY's run-time PLL tracking without a
   lock, because the first is finished before the second starts. True by
   construction if rule 4.3 holds.
9. **The owner accepts a C program, windowed, in a Saw system** for the
   radio. The libraries leave no other way on this part.

### 9.4 What I would attack next if I were the reviewer

The latency claim (no measurements exist), the RAM table (half of it is
believed), the 64-register frame (the window vectors' interaction with a
preemption in the middle of a spill handler is the subtle case: the spill
handlers run with EXCM set, which masks level 1, so the tick cannot land
inside one; K1 must have a case for it anyway), and whether one process
for Wi-Fi and BLE is right (it is forced by the shared PHY and ROM tables,
but it means a BLE fault takes Wi-Fi down).

---

## 10. Order of work

Sizes: S small (one brief, days), M medium (one brief, a week or two), L
large (should be split again before briefing).

**Track A: the stack and its device, no radio, no box files.** These touch
only new packages, except the runner's case table (`tools/sos_runner.py`),
which the box's units also edit; that is a merge, not a design, conflict.

```
A1 S  pktring + netdevproto + wlanproto + hciproto packages, hosted
      tests of ring arithmetic and op encoding. Proves the wire.
A2 M  Saw stack layer 1, hosted: Ethernet, ARP, IPv4, ICMP, UDP with
      golden frames. Proves parsing and building.
A3 M  svc-virtio-net on riscv32 and arm64 virt; the runner's scripted
      peer over -netdev stream; case net_ping (ARP + ICMP echo through
      driver and stack). Proves the device protocol end to end.
A4 M  DHCP client and DNS stub, against the peer's fixed lease and name.
A5 L  TCP in Saw: hosted state-machine goldens, then cases against the
      peer and against the host's TCP via -netdev user. Split before
      briefing (connection setup and teardown; data and retransmission;
      congestion control).
A6 M  sockproto and svc-net's server face; UDP and TCP echo apps.
A7 M  svc-openeth on the S3 emulator, the same cases in the S3 smoke.
```

**Track K: kernel and HAL.** After SO-102 and SO-95's HAL changes land,
because these edit `hal/xtensa-esp32s3/kernel/` and `kcore.limits` too.

```
K1 M  windowed threads on the S3 (§3.1 items 1-6), smoke cases, Blade
      windowed package check; board stage B2.
K2 S  FP and BR state per thread on the S3, lazy; smoke case.
K3 L  the priority scheduler of spec §7 with preemption on wake and the
      direct-switch fastpath. Opus design first; gated on all profiles.
K4 M  clock bring-up: UART0 to XTAL, BBPLL, CPU 80/160 MHz, APB 80;
      board stage B1. After SO-77's stage 0 reads the clock.
K5 S  the S3 board description: rule 2.1's reserved range, radio
      devices and lines, raised per-board limits (SO-95's machinery).
```

**Track R: the radio.**

```
R1 M  the link harvest: radio package linking Espressif's archives,
      every adapter entry a stub that prints and stops; build-only gate
      with zero undefined symbols; checks ld.lld against the ROM script
      assignments. Files the Blade gap for archives and extra scripts.
R2 L  the adapter over sawos (§4.1), tested without a radio on riscv32
      and windowed on the S3 (§7.2 item 4). Split: RTOS objects and
      timers; memory and logging; interrupts and the ISR gate.
R3 M  PHY bring-up on the board (B3), with the latency and RAM prints.
R4 M  Wi-Fi scan (B4) and wlanproto's Scan.
R5 L  association: IDF's supplicant and its crypto built windowed in the
      radio (B5).
R6 M  netdevproto served by the radio; DHCP and ping over Wi-Fi (B6).
```

**Track B: BLE.**

```
B-1 M  controller init and hciproto; advertising from raw HCI (B7).
B-2 L  Saw host: L2CAP, ATT, GATT server, GAP (hosted goldens, then B8).
B-3 L  SMP pairing.
B-4 M  coexistence of Wi-Fi and BLE (B9).
```

**Dependencies.** A1-A6 need nothing from the box. A7 needs nothing either
(the stack is call0). K1, K2, K5 wait for SO-102's and SO-95's HAL and
limit changes. K4 waits for SO-77's stage 0. R1 needs K5's description
only for its link script, and can start on a scratch script. R2's riscv32
half needs nothing; its windowed half needs K1. R3 and later need K1, K2,
K3, K4, K5 and SO-102.

**The first three months** (to mid-February; the box first until
Christmas, at most two implementers at once):

- **Would deliver:** A1 and A2 before Christmas (no box files); A3, A4
  and the start of A5 in January; K1, K2 and K5 once SO-102 is in;
  R1 (the radio image links); the K3 design written and its first unit
  started; possibly K4 with board stage B1. On the emulators: ping, UDP,
  DHCP and DNS through a Saw stack and a Saw virtio-net driver on riscv32
  and arm64, gated; windowed processes gated in the S3 smoke and seen on
  the board.
- **Would not deliver:** any frame over the air; TCP finished; the
  adapter complete; BLE beyond a design. A Wi-Fi scan on the board (B4)
  is the earliest radio milestone and is believed to fall just after the
  three months, once K3 exists.

---

## 11. Alternatives rejected, questions, and what was checked

### 11.1 Alternatives rejected

1. **Thunks between call0 and windowed code** (§3.1 b): the kernel work of
   windowed support plus hundreds of thunks, for no gain.
2. **Converting the libraries to call0** (§3.1 d): not practical, and the
   ROM they call stays windowed.
3. **Running the libraries' handlers in kernel mode**: closed code in the
   kernel's mode; rejected in favour of the priority scheduler.
4. **A cooperative scheduler inside one sawos thread for the libraries'
   tasks**: FreeRTOS tasks are preemptive and the libraries busy-wait in
   places (`ets_delay_us`); and switching windowed stacks in user mode
   needs the window spill anyway. Real threads are simpler.
5. **Separate processes for Wi-Fi and BLE**: the PHY, its calibration,
   the coexistence library and the ROM tables are shared state.
6. **Persisting calibration to NVS**: saves 100 ms per boot, costs flash
   writes the owner's limits forbid and a flash driver. Full calibration
   each boot.
7. **lwIP as the plan** (§6.1 c), **NimBLE as the plan** (§6.2): kept as
   fallbacks.
8. **The C6 first** (§8): no board yet, and the S3-only cost is small.
9. **An ESP-Hosted companion** (a second chip as the radio, over SPI or
   SDIO, the sawlang archive's P4 path): the owner's board has the radio
   on the chip; noted for a radio-less part.

### 11.2 Questions for the owner

1. **May the radio process be a windowed C program inside a Saw system,
   with Saw allowed in it?** Recommendation: yes; the libraries leave no
   other way on the S3, and Saw is still usable there.
2. **TCP/IP in Saw, with lwIP only as a fallback?** Recommendation: yes,
   staged UDP-first over virtio-net.
3. **BLE host in Saw, starting with raw-HCI advertising?** Recommendation:
   yes; NimBLE as the fallback.
4. **Which regulatory country should the image set?** Recommendation: the
   owner's, set explicitly in the image, never left to the library's
   default.
5. **How should Wi-Fi credentials reach the device?** Recommendation: an
   image input read from a git-ignored file at build time for now, sent
   over `wlanproto` by the manager at boot; never committed.
6. **Ask Espressif for call0 builds?** Recommendation: not needed; it
   costs nothing to open an issue, and the design does not wait on it.
7. **May the S3's CPU clock be raised to the PLL (80 or 160 MHz) for radio
   images?** Recommendation: yes, 160 MHz, through K4's board stage, on
   images that use the radio only.
8. **Does the priority scheduler (K3) come before the radio's board
   stages?** Recommendation: yes; R3 measures without it first only if
   K3 is late, and nothing about latency is concluded from that run.
9. **Raise the per-board limits on the S3 for networking images**
   (threads, interrupts, pipes, waiters, processes, mappings)?
   Recommendation: yes, through SO-95's per-board limits, sized from R3's
   measurements.
10. **When the radio process dies, end the image or restart it?**
    Recommendation: end the image with a console line until a restart
    unit exists (rule 5.1).

### 11.3 What was checked, how

**Verified by inspecting files or running tools** (everything under
`/private/tmp/claude-501/-Users-shawn-Projects-sawos/34c0d76b-723c-482d-
939f-716d368f6e5c/scratchpad/SO-103/`: downloads in `dl/`, extracted
objects in `ex/`, outputs in `an/`, scripts in `scripts/`, builds in
`build-lwip/` and `probe/`):

- The library archives at IDF v6.1's submodule commits, their sizes and
  section sums (`llvm-readelf`, `scripts/sections.py`).
- `ABI=0` in every S3 object's `.xtensa.info` (`objdump -s`); the
  instruction counts of §2.2-2.3 (`objdump -d`, `scripts/insn.py`).
- The undefined-symbol groups and ROM addresses (`llvm-nm`,
  `scripts/undef.py`, IDF's ROM scripts).
- The ROM ELF's reserved sections and `_dram0_rtos_reserved_start`
  (`llvm-readelf -S`, `llvm-nm` on the local ROM ELF); IDF's reservation
  code (`memory_layout_utils.c`).
- The eFuse and flash-controller literal scans and the disassembly of the
  three eFuse readers; the ROM call graph of `ets_efuse_program`.
- The peripheral pages the libraries name (`scripts/mmio.py`).
- The Wi-Fi handler's callees (`objdump -d -r` of `wdev.o`).
- The adapter table sizes and contents in IDF's sources.
- `sawc` emitting `entry`/`callx8` with `+windowed` and `callx0`
  without (probe built with the pinned `sawlang-0.23` compiler).
- Blade passing manifest `target-features` to the C half (read in
  `blade/src/sosimg.saw`; not run).
- lwIP compiling call0 for the S3 (31 objects, sizes, externals).
- QEMU: `virtio-net-device` on both `virt` machines, the netdev backends,
  the device-tree placement; Espressif's QEMU NIC models and strings.
- The C6 archives' ISA and their `g_osi_funcs_p` use.
- `SOC_MODEM_APB_CLOCK_IS_INDEPENDENT` absent from the S3's `soc_caps.h`.
- Licences: the four library repositories (Apache-2.0), lwIP (BSD
  3-clause), esp-nimble (Apache-2.0), esp-radio (MIT or Apache-2.0),
  wpa_supplicant file header (GPL v2 or BSD).

**Read in documentation, not checked by running:** IDF's Wi-Fi buffer and
memory documentation and the S3 rank table; the 100 ms cost of full
calibration; the IRAM options' cost; the BLE qualification listings; the
behaviour of the libraries in IDF (handlers at level 1 and 3, the event
flow); the BT spec's HCI commands for P-256 and AES; NuttX's and
esp-radio's adapters (read, not built); sawos's spec, ABI.md, SO-95,
SO-77 and SO-102 for the sawos side.

**Not checked, and how to find out:**

- Whether a deferred handler meets the libraries' timing: R3 measures.
- Whether `ld.lld` resolves ROM script assignments as GNU ld does: R1.
- Whether the ROM's reserved RAM keeps its initial values after direct
  boot and the sawos boot: R3 compares a ROM data word with the ROM ELF.
- The RAM figures marked believed: rule 4.4's high-water marks at R3-R6.
- Whether Blade can link archives and extra linker scripts: R1 files it.
- Whether the S3's PMS confines the Wi-Fi MAC's DMA: the TRM's permission
  chapter, before any protected-tier design.
- The open_eth model's interrupt source on the S3 machine: a bisection
  probe in A7.
- NimBLE's size, licences other than those listed, FCC/IC/CE grant
  conditions, and the C6 BLE library: not fetched.
- What the libraries do at run time: nothing of Espressif's was executed
  or linked into anything that ran.
