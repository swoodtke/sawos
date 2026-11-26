# SO-165 design: the ESP32-S3 UART0 driver, interrupt-driven both ways

Revision 1, Nov 26. A design only: nothing here is built until the owner and
the reviewer have read it.

Markers. **[read F:L]** is taken from that file and those lines (paths from
the sawos root at `so161`, `30202a81`; `IDF/` is ESP-IDF tag v5.4.1).
**[probed]** is something I ran, and §6 says how. **UNVERIFIED** is neither.

ESP-IDF was not on this machine (`find / -name uart_ll.h -path '*esp32s3*'`
found nothing), so I fetched four files of tag v5.4.1 from
raw.githubusercontent.com/espressif/esp-idf into scratch:
`components/hal/esp32s3/include/hal/uart_ll.h`,
`components/soc/esp32s3/register/soc/uart_reg.h`, `.../uart_struct.h` and
`components/soc/esp32s3/include/soc/soc_caps.h`.

## 0. The decisions, in one place

1. A new package, `drivers/svc-uart-esp32s3`, serves `uartproto` from UART0's
   window and line 27, declares `bands = "full"`, and serves from one thread
   at `Rt0`. Its main thread brings the device up and joins it.
2. Its logic lives in a new hosted package, `drivers/uartcore` (rings, holds,
   the serve decisions, over a device trait), which `blade test` runs against
   a fake UART, so every ordering property is proved without an emulator.
3. Transmit: a `Write` is copied into a ring and answered once queued. The
   ring feeds the 128-byte FIFO, topped up on `TXFIFO_EMPTY`. A `Write` that
   does not fit is held, and while it is held the driver **takes no message
   at all**, so the connection's own ring fills and writers park. Nothing
   spins and there is no transmit thread.
4. `Sync` is answered when every byte queued before it has left the FIFO,
   which with no other writer is "the ring and the FIFO drained".
5. Receive: RX-full plus the RX timeout into a ring. `Read` is unchanged (one
   byte per request, held when none). A loss is reported in band: `uartproto`
   gains a status `Lost` that the reader meets at the gap, and an op `Stats`.
6. The kernel's console stays synchronous and shares the FIFO. Kernel lines
   stay whole; a client's line may be split by one, at a fill boundary, on
   silicon. Nothing is lost, with one named window (§4).
7. The S3 product template takes the new driver; `svc-console` stays.
8. ns16550 and pl011 adopt `uartcore` after the S3 driver, in their own unit.
9. Finding: `kill_churn_profile`'s console stall is the **kernel's own
   teardown line**, not `svc-console`'s, so the new driver cannot move it
   (§10). The board comparison needs a profile that prints through the
   console's server.
10. Probed: the emulator's UART0 receives from the terminal into a 128-byte
   FIFO with the full threshold and the RX timeout, never overflows (it
   stops reading the host instead), and transmits at once (§6). So the
   gate tests receive with the runner's typist, and holds only with a
   lowered fill cap.

## 1. The driver process

**Package and records.** `drivers/svc-uart-esp32s3/`, named as its siblings
`svc-gpio-esp32s3` and `svc-uart-ns16550` are. Its `process.toml`:

```toml
format = 1
arena = 0x1000
ram = "fast"                # it services the UART's interrupt
bands = "full"              # its serving thread runs at Rt0 (§5)

[[boot]]                    # its own lines, at its end, through the debug
name = "system"             # print; and its Process
kind = "system"
tag = 21
rights = ["debug", "process-self"]

[[boot]]
name = "uart"
kind = "window"
type = "esp32s3-uart"
tag = 22

[[boot]]
name = "uart"
kind = "serve"
protocol = "uartproto"
tag = 23

[[boot]]
name = "uart"
kind = "line"
type = "esp32s3-uart"
tag = 24

[quota]
threads = 2
waiters = 1
mappings = 1
handles = 23

[build]
inputs = ["uartconfig"]
defaults = { uartconfig = "config/default.saw" }
```

The tags are the ns16550 driver's [read drivers/svc-uart-ns16550/process.toml],
so a hand-written root can launch either the same way.

- **The window** is UART0's 4 KiB page at 0x6000_0000 and **the line** is
  matrix source 27 [read hal/xtensa-esp32s3/board.toml:65-71]; line 27 is
  bindable (`irq_line_valid` refuses only none, past the matrix, and 57)
  [read hal/xtensa-esp32s3/kernel/lib.saw:1339-1342]. UART0 is the board's
  `kernel = true` device, which the builder binds only when the image names
  it in the process's `device` [read
  tools/sosimage/src/imagefile.saw:1057-1059], as
  `console_service_ns16550.toml` does on riscv32 [read
  tests/images/console_service_ns16550.toml].
- **Handles, 23.** What it holds while serving: System, Process, the main
  thread's own Thread, the serving Thread (held for the join), the Waiter,
  the Interrupt, the IoMemory and its Mapping, and the outlet, which it keeps
  because the attachment borrows it (§3): 9. What it may hold on top: the
  held reads (`MAX_HELD_READS`, 4), the held `Write` (1), the held `Sync`s
  (`MAX_SYNCS`, 4), the request being served (1) and the
  `PIPE_MSG_HANDLES` it may carry (4) [read kernel/abi/src/ops.saw:1682]: 14.
  A row short of that lets a client's request end the driver, the rule
  `svc-gpio-esp32s3` states [read drivers/svc-gpio-esp32s3/process.toml].
- **`uartconfig`**, a build input as `gpioconfig` is: the ring sizes, the
  thresholds, the held-request limits, and the per-fill cap the emulator
  cases lower (§9). The default:

  ```
  TX_RING_BYTES   1024   RX_RING_BYTES  256
  TX_EMPTY        32     RX_FULL        64     RX_TIMEOUT_BITS 30
  TX_FILL_CAP     128    MAX_HELD_READS 4      MAX_SYNCS       4
  ```

**Every existing client works unchanged**: the driver serves the
`uartproto` every console client already speaks, through the same `uartpipe`
transport [read drivers/uartpipe/src/lib.saw].
The protocol grows a status and an op (§2); a client that never meets a loss
and never sends `Stats` sees no difference, and every client in the tree is
rebuilt against the package anyway.

## 2. Receive

**Bring-up** (the main thread, before the serving thread starts):

- `CONF1` (+0x24), read-modify-write: `RXFIFO_FULL_THRHD` [9:0] = `RX_FULL`,
  `TXFIFO_EMPTY_THRHD` [19:10] = `TX_EMPTY`, `RX_TOUT_EN` [23] = 1 [read
  IDF/uart_reg.h:933-977]. `MEM_CONF` (+0x60), read-modify-write:
  `RX_TOUT_THRHD` [26:17] = `RX_TIMEOUT_BITS` [read IDF/uart_reg.h:1268,
  1293-1301], in bit times [read IDF/uart_ll.h:933-946].
- `INT_CLR` (+0x10) the receive bits, then `INT_ENA` (+0x0C) =
  `RXFIFO_FULL` (bit 0) | `RXFIFO_OVF` (4) | `RXFIFO_TOUT` (8) [read
  IDF/uart_ll.h:39-60].
- **Not touched:** `CLKDIV` and `CLK_CONF`, which are the console key's
  [read hal/xtensa-esp32s3/ABI.md:3266-3330]; and `CONF0`'s FIFO resets. The
  TX FIFO may hold the kernel's bytes, and the RX FIFO bytes typed before the
  driver started, which it delivers rather than discards.

**The interrupt arm.** Every register access comes before the ack, as spec §9
requires [read spec.md:4657-4663]. Read `INT_ST` (+0x08). For any receive bit:
read `STATUS.RXFIFO_CNT` [9:0] and pop that many bytes from `FIFO` (+0x00)
into the ring [read IDF/uart_reg.h:17-21, 672-679; IDF/uart_ll.h:337-342],
then read the count again until it is 0; count `RXFIFO_OVF` as a loss (below);
`INT_CLR` the receive bits. The transmit half follows (§3). Then `ack`. After
the ack, the held reads are answered oldest first, from bytes already taken.

**A single typed byte arrives promptly.** It is below `RX_FULL`, so
`RXFIFO_TOUT` raises the line once the receiver has been idle for
`RX_TIMEOUT_BITS` bit times ("when receiver takes more time than
rx_tout_thrhd to receive a byte" [read IDF/uart_reg.h:94-98]): 260 us at
115200, 10 us at 3 Mbaud, plus the wake. That the timeout fires only while
the FIFO holds a byte is UNVERIFIED on the part; §6 says what the emulator
does.

**What a `Read` returns** is what it returns today [read
drivers/uartproto/src/lib.saw:44-49]: `Ok` and the next byte, at once from the
ring; with none, the obligation is held (up to `MAX_HELD_READS`, past that
`Busy`), watched for abandonment with a one-shot attach, and answered at the
next drain; a reply that does not land puts the byte back at the front. That
is the ns16550 driver's logic, lifted into `uartcore` [read
drivers/svc-uart-ns16550/src/main.saw:163-176, 191-252, 520-532, 556-585,
660-682].

**Overflow is counted and reported, never silent.** A byte that finds the
ring full is dropped and counted, as `InBuf` does today [read
drivers/svc-uart-ns16550/src/main.saw:125-161]. An `RXFIFO_OVF` means the
hardware dropped bytes it does not count, so it counts as at least one. It is
reported three ways:

- **In band, where it happened.** `uartproto` gains `UartStatus.Lost = 4`,
  whose reply carries a 4-byte count ("at least this many bytes were lost
  here"), and `UartError.Lost(at_least: UInt32)`. The ring keeps a gap mark
  at the position of the first unreported loss; the reader gets every byte
  before it, then one `Read` answered `Lost`, then the bytes after it. A
  second loss before the first is reported merges its count into the first
  mark, so the report is never late and never silent, though a merged count
  is placed at the earlier gap. The mark consumes no byte, so a reader that
  treats `Lost` as an error and reads on loses nothing more.
- **On request.** A new op, `Stats = 5`, a call answered `Ok` and fixed
  counters, little-endian `u32`s: bytes received, bytes lost, bytes queued,
  bytes sent, writes held. Every driver answers it (`svc-console` with its
  printed count and zeros). It also gives the cases an observable that is not
  a clock (§9).
- **At the end**, in the report the driver prints when its last client goes.

**Line editing does not belong in the driver.** The driver is bytes; a client
does lines. Echo is a client's choice too: a driver that echoed would be a
line discipline every client inherits, and a client that reads a binary
protocol would have to turn it off. The board stage's client echoes each byte
and ends a line at CR or LF, since a terminal's Enter sends CR (§10). A shared
line reader, when a shell exists, is a client package of its own.

## 3. Transmit

**State.** The ring (`TX_RING_BYTES`), `queued` (every byte ever accepted
into it), `pushed` (every byte ever stored to the FIFO), and `Idle` or `Busy`
(`TXFIFO_EMPTY` enabled or not).

**A `Write`.** Its payload is copied out of the message at the take. As much
as fits goes into the ring. If all of it fits it is answered `Ok` at once;
for a tell the answer lands nowhere, which is not an error [read
drivers/svc-console/src/main.saw:186-204]. If not, the driver keeps the copy,
the offset and the obligation as **the held `Write`**, and removes the
outlet's attachment. Then, if `Idle`, it starts the transmitter.

**Start and top-up.** Store bytes from the ring to `FIFO` while
`STATUS.TXFIFO_CNT` [25:16] is below 128 and at most `TX_FILL_CAP` in one
fill, reading the count before every store (§4) [read IDF/uart_reg.h:700-707;
IDF/soc_caps.h:414]. Each store is a 32-bit write, never a read: a read of
that register pops the receiver [read IDF/uart_ll.h:353-361]. Then `INT_CLR`
`TXFIFO_EMPTY` (bit 1) and set it in `INT_ENA`: `Busy`. The line rises when
the FIFO holds fewer than `TX_EMPTY` bytes [read IDF/uart_reg.h:38-45,
942-945], and the interrupt arm, before its ack, tops up, copies the held
`Write`'s remainder into the room that freed, and tops up again, until the
ring is empty, the FIFO is full or the fill cap is reached. With the ring
empty and no `Sync` waiting it clears `TXFIFO_EMPTY` from `INT_ENA`: `Idle`.
After the ack: a held `Write` that is now wholly queued is answered and the
outlet attached again.

With `TX_EMPTY` at 32, every top-up adds at least 96 bytes, and the driver has
32 byte times to run before the wire idles: 2.8 ms at 115200, 107 us at 3
Mbaud. A late top-up costs throughput, never a byte.

**Nothing spins.** The driver never waits for room: each fill stores only
what the count says fits, and otherwise it is parked in `wait`.

**A full ring holds the `Write`, and the driver stops taking.** The console
is one connection that every process of the image posts to: the builder
gives each an inlet of the same serve [read spec.md:5470-5480]. The ring
delivers in post order [read spec.md:229], so a `Write` taken ahead of a
held one would put a later line's bytes first, and holding every later one
would be a set with no bound. So while a `Write` is held the outlet is not
attached: the connection's own ring (`PIPE_INFLIGHT` slots [read
spec.md:220]) fills, and a writer's tell, refused for room, becomes a call that
parks [read drivers/uartpipe/src/lib.saw:9-17]. The writer slows to the wire,
and nothing is lost. The same pause covers a full set of held `Sync`s.

**What the pause costs.** Pausing needs the borrowing attach (`add`) and an
explicit `take`, because the consuming attach owns the outlet and removing it
closes the connection [read kernel/sysapi/src/pipe.saw:1815-1830]; under the
consuming attach the delivery is the take [read spec.md:778-785]. The loop
takes until `WouldBlock` and answers each message, then waits: two traps a
message where today's completion-queue loop spends one, plus one wait per
batch. A `Write` carries at most 127 bytes, 11 ms of wire at 115200, against
a trap of microseconds.

**`Sync`.** At its take it is marked with `queued`. It is answered when
`pushed - TXFIFO_CNT >= mark`, that is, when every byte queued before it has
left the FIFO. The count includes any kernel bytes in the FIFO, so the test
can only answer late, never early. It is checked at every top-up. With the
ring empty and `Sync`s waiting, the driver sets `TXFIFO_EMPTY_THRHD` to
`pushed - mark + 1` for the oldest (a value in 1..128; past 128 it is already
reached) and keeps the interrupt on, so the line rises exactly when the mark
is passed, then puts the threshold back. Up to `MAX_SYNCS` are held; at the
limit intake pauses.

Compared with the brief's "once the ring and the FIFO have drained": the
same when nobody else writes, and a `Sync` cannot be starved by bytes other
clients queue after it. It keeps spec §12's promise that a process's lines
come before its exit or fault line [read spec.md:5481-5486;
rt/main/src/lib.saw:29-31]: the runtime's `Sync` returns only once those
bytes have left the FIFO, and the kernel's line enters it behind them.

**`Mark`** is answered at once, as today. **`Stats`** answers the counters.

## 4. Sharing UART0 with the kernel

**Today.** `svc-console` never touches UART0. It makes one `DebugPrint` per
payload byte [read drivers/svc-console/src/main.saw:174-184], and each is the
kernel's `console_byte`: read `TXFIFO_CNT`, wait while it is 128, store [read
hal/xtensa-esp32s3/kernel/lib.saw:469-489]. So the kernel is the one writer,
and every byte is its own kernel entry. The kernel's own lines (exit,
teardown, fault, panic) are each written inside one kernel entry with
interrupts masked [read spec.md:4014-4015, 4744-4746], so no process runs
inside one and they are always whole. Between processes the grain is the
scheduler's: `svc-console`, root and their clients are all at `Normal`, and a
wake at one level never preempts [read spec.md:4096-4100], so with no tick a
whole `Write` goes out together, and with the tick a slice can end inside one.
On silicon every kernel byte that finds the FIFO full waits with interrupts
masked, which is the stall `svc-console`'s header names [read
drivers/svc-console/src/main.saw:19-25].

**After.** Two writers store into one FIFO, which keeps their order.

- **The kernel's lines stay whole**, for the same reason: no user code runs
  inside the kernel entry that writes one.
- **A kernel line lands behind what the driver has already stored**: a
  fill, cut at any byte. On silicon a kernel line can therefore split a
  client's line anywhere. On the emulator the FIFO drains at once (§6) and
  the driver, above every client, empties its ring before a client runs
  again, so a kernel line lands between `Write`s and transcripts stay
  deterministic.
- **A process's `DebugPrint` line** (root's `root: X ended`) is one trap per
  byte [read kernel/sysapi/src/system.saw:209-221; spec.md:2320]. On silicon
  the driver, above root, can run a top-up between two of them, so such a
  line can hold a fill of a client's bytes. On the emulator it cannot: the
  driver's transmit interrupt is off whenever its ring is empty, and nothing
  else readies it while root prints.
- **What a kernel print does to a FIFO the driver is filling:** it appends.
  If the FIFO is full, the kernel waits for room with interrupts masked, at
  most 128 byte times before its first byte (11.1 ms at 115200, 0.43 ms at 3
  Mbaud), the same bound as today when `svc-console` has filled it. The
  driver's next top-up reads a count that includes the kernel's bytes and
  stores behind them.
- **The one window.** The driver reads the count, then stores. If another
  thread ran between the two and had the kernel print enough to fill the
  FIFO, the store would land in a full FIFO. The HAL's promise that "the two
  interleave and neither loses a byte" [read
  hal/xtensa-esp32s3/kernel/lib.saw:481-486] holds for a writer that polls
  before each store, which the kernel does; the driver keeps the same
  discipline, reading the count before every store, which narrows the window
  to one store. Only a thread at the driver's level or
  above can run there, so in an image where the driver is the only process
  with the full band map, nothing can, but a kernel diagnostic (a line
  nothing is bound to, §9a's reclaim). What the part does with a store to a
  full TX FIFO is UNVERIFIED; the emulator logs `esp_uart: write to UART FIFO
  while it is full` [read: the string in the installed qemu-system-xtensa].
  Whether the builder should enforce "the console's driver alone at Rt0" is
  question 3.
- **The driver never drains before a kernel print.** The kernel cannot ask
  it, and the FIFO's order makes it unnecessary. What does drain is the
  client's `Sync`, which the runtime sends before a process exits or panics
  (§3), so a process's own lines always precede its exit or fault line.
- **A kernel panic** writes its text through the same poll [read
  hal/xtensa-esp32s3/kernel/lib.saw:563-569] and halts in a spin [read
  lib.saw:522-526]. What is in the FIFO goes out ahead of it, and on silicon
  keeps shifting during the spin; what is still in the driver's ring is lost,
  since no process runs again. **A process's panic** syncs first, so its own
  lines come before its fault line; other clients' bytes go on. **The
  driver's own fault** loses its ring; its clients' next posts answer
  `PeerClosed`, the driver-gone path `console_gone` exercises today [read
  tests/images/console_gone_s3.toml:1-4]. **Root's `shutdown`** after a
  `shutdown` child loses what other clients still have queued, as it loses
  messages staged in the pipe today.

## 5. Bands and threads

**Two threads.** The main thread starts at `Normal` [read spec.md:4105]. It
maps the window, makes the Waiter, attaches the line and the outlet, brings
the UART up (§2), starts the serving thread at `Rt0`, joins it, and prints the
report. The serving thread does everything else: messages, the line, the
holds. One thread owns the one Interrupt, spec §9's canonical shape [read
spec.md:4664-4668]; UART0 has one line for both directions, so a separate
transmit thread would be a second servicer of it.

**Rt0, not High.** `bands = "full"` is the identity map [read
tools/sosimage/src/declaration.saw:50-53]; the simple map puts `High` and
every realtime band at level 3, and only the full map reaches 4 to 7 [read
spec.md:4111-4115]. At `High` the driver would share level 3 with any
simple-map client's `High` thread and take turns with it; at `Rt0` (level 4)
no simple-map process can reach it. A client's post readies the driver above
the poster, so it runs at once; the line readies it above every client [read
spec.md:4096-4100]. §7's convention that a server runs at or above its
clients holds [read spec.md:4294-4297], and the builder's band check passes: it
refuses a full-map client of a simple-map server, and this is the reverse
[read spec.md:5392-5397].

**Why a slow client cannot hold the console up.** The driver never waits on
a client. A `Write` is copied and answered, and a tell's answer goes nowhere.
A `Read` with no byte is a held obligation, not a wait, and an abandoned one
is discarded at its level. A reply that does not land is not an error. A
client that never reads only fills the receive ring, which is counted and
reported. A client cannot outrun the driver at its own top band (level 3 at
most under the simple map). The most a client can do is fill the transmit
ring, which slows every writer, since they share one connection, but never
the wire.

## 6. The emulator

**The model** is Espressif's `esp32s3_soc.uart` over `esp_soc.uart`
(`hw/char/esp32s3_uart.c`, `hw/char/esp32_uart.c`), whose functions include
`uart_receive`, `uart_can_receive`, `uart_rx_timeout_timer_cb` and
`uart_transmit` [read: the strings of the installed
`qemu-system-xtensa`, esp_develop_9.2.2_20250817; the source is not on this
machine].

**The probe.** A 0x1B4-byte call0 program linked into IRAM and booted with
`-kernel` (no ROM, no kernel), polling with no interrupts: it prints UART0's
defaults, sets `RXFIFO_FULL_THRHD` 4, `TXFIFO_EMPTY_THRHD` 16 and
`RX_TOUT_EN`, then for about five seconds prints `STATUS` and `INT_RAW` whenever
either changes, clearing `INT_RAW` after each print; then it pops the RX FIFO
and prints each byte; then it stores 100 bytes and reads `STATUS`, `INT_RAW`
and `FSM_STATUS` at once. The host typed `A` at 1.0 s, `bcdefg` at 2.5 s and
200 bytes of `h` at 4.0 s. Run under the suite lock; the program, its runner
and both transcripts are in scratch (`so165/probe/`). **[probed]**:

```
at reset           CONF1=0  MEM_CONF=0  FSM=0  INT_RAW=0x4003
INT_CLR of all     INT_RAW=0x4003           (levels: a held condition re-sets)
thresholds set     INT_RAW=0x4002, then 0x4102 with RXFIFO_CNT=0
1.0 s  'A'         RXFIFO_CNT=1    INT_RAW=0x4102   (timeout: 1 is below 4)
2.5 s  6 bytes     RXFIFO_CNT=7    INT_RAW=0x4103   (full: 7 > 4)
4.0 s  200 bytes   RXFIFO_CNT=0x60, then 0x80 (128), INT_RAW=0x4103, no bit 4
pop 128            'A' 'b'..'g' then 'h' x 121, in order; then RXFIFO_CNT=1
100-byte burst     TXFIFO_CNT=0  FSM=0  INT_RAW=0x4002 (TX-empty, TX-done)
```

What that says, for each thing the brief asks:

- **RX from the terminal: modelled.** The host's bytes enter a 128-byte RX
  FIFO in order and `RXFIFO_CNT` counts them; popping `FIFO` reads them.
- **`RXFIFO_FULL`: modelled** against the threshold.
- **The RX timeout: modelled**, and it raised one with the FIFO empty right
  after it was enabled. So the driver treats a timeout as "look", drains
  what the count says (possibly nothing) and clears it; it never assumes a
  byte. Whether the part does the same is UNVERIFIED.
- **No hardware overflow.** The model stops taking input while its FIFO is
  full, and the rest waits in the host's pipe: 200 bytes typed, the count
  stopped at 128, `RXFIFO_OVF` never rose, and a 129th byte arrived once the
  FIFO had room. `RXFIFO_OVF` is therefore board-only; the driver's own ring
  overflowing is not, and `uart_s3_lost` drives it.
- **Transmit is immediate**: `TXFIFO_CNT` and `FSM_STATUS` read 0 straight
  after a 100-byte burst, as §4 of the HAL's `ABI.md` found [read
  hal/xtensa-esp32s3/ABI.md:511-514]. So `TXFIFO_EMPTY` is always raised
  once enabled, which is how the `external_irq` smoke case raises line 27
  already [read hal/xtensa-esp32s3/ABI.md:803-807;
  hal/xtensa-esp32s3/kernel/lib.saw:1395-1401], and the line follows
  `INT_ENA` [read hal/xtensa-esp32s3/ABI.md:832-834].
- **The reset values differ from the part's.** `CONF1` and `MEM_CONF` read
  0, where the register file gives the part 96, 96 and 10 [read
  IDF/uart_reg.h:934, 942, 1293], and with a full threshold of 0 the full
  bit reads raised on an empty FIFO. The driver writes every field it relies
  on and relies on no reset value.

**How the gate tests receive.** As `uart_echo_*` do: the runner's typist
feeds the case's `stdin` one byte at a time behind `SERIAL_TYPE_DELAY_S`, or
all at once before the boot [read tools/sos_runner.py:17315-17380]. The S3
boot path opens the emulator with stdin closed [read
tools/sos_runner.py:19084-19097] and gains the same typist (U4). The flash
image boots through the ROM, and bytes typed before the driver starts wait
in the model's FIFO, which the driver does not reset (§2). That the ROM's
flash boot leaves UART0's receiver alone is UNVERIFIED; if it does not, U4
gives the typist a key to wait for the client's prompt line before its first
byte.

**What only the board can check:** the FIFO draining at a real rate, so a
nonzero `TXFIFO_CNT`, the empty threshold's timing and a ring that actually
fills under one writer; `RXFIFO_OVF` at a high rate; the RX timeout with a
human at the terminal; a kernel line or root's line split at a fill boundary
(§4); the count-then-store window; and B2's share.

## 7. The same transmit path for ns16550 and pl011

**What changes in each.** Their receive path stays. Their `put_byte` spin
[read drivers/svc-uart-ns16550/src/main.saw:115-118] and its PL011 twin on
`FR_TX_FULL` become `uartcore`'s ring and holds, with the device's transmit
interrupt: on the 16550, IER bit 1 (THR empty), cleared by a THR write or an
IIR read; on the PL011, `IMSC.TXIM` (bit 5), `ICR.TXIC`, and `IFLS`' transmit
level. Both move to the borrowing attach with `take`, so their trap law, "one
trap per answered message, two per discarded" [read
drivers/svc-uart-ns16550/src/main.saw:340-362], becomes two and three, and
every case asserting a `traps=` count from them changes (`uart_service_*`,
`uart_dropped_read`, `console_service_uart`, `console_tag` and others [read
tools/sos_runner.py:11880-11940]).

**When.** After the S3 driver, as one unit. QEMU's 16550 and PL011 transmit
at once (UNVERIFIED: QEMU's `serial.c` and `pl011.c` are not on this machine),
so on those profiles nothing ever back-pressures and the change buys no
throughput. What it buys is one transmit path in three drivers, and the gate's
three profiles running `uartcore`'s holds through real devices on every patch.
The S3 smoke gates too, and `uartcore`'s hosted tests run in every full
`make sos-test`, so the S3 driver does not need them to land first. If the
owner would rather keep the emulators' drivers as the completion-queue
examples they are, they can wait (question 5).

## 8. The image side

**Choosing the driver.** A stock-root image names it exactly as
`hello-riscv32` names the ns16550 driver [read
templates/product/images/hello-riscv32.toml]:

```toml
[image]
console = "console.uart"

[[process]]
name = "console"
package = "sawos:drivers/svc-uart-esp32s3"
device = { uart = "UART0" }
# inputs = { uartconfig = "..." }   only to change the defaults
```

A product image is the same TOML in the product's repository. The template's
`hello-esp32s3.toml`, which names `svc-console` today [read
templates/product/images/hello-esp32s3.toml], moves to the driver, so a new
product gets it, and the S3 smoke's product phase runs and flashes it.

**`svc-console` stays.** It needs no device and runs on every board, and the
`console_*` image family uses it on all four profiles [read tests/images/],
where those cases test the runtime's console record, which does not depend
on the driver. On the S3 it remains the fallback for an image that wants no
UART interrupt or the kernel's sequencing of every byte. Its header's "the
driver suits a product that prints little" [read
drivers/svc-console/src/main.saw:22-23] gains the pointer to the new driver.
Hand-written roots (`kill_churn` and the rest) are untouched.

## 9. Cases

Every emulator case below is on the S3 smoke, with a stock root and the
driver as the image's console unless it says otherwise. None asserts a
duration or a count that accrues with time.

**Hosted, in `uartcore`** (`blade test`, in `HOSTED_TESTS`), over a fake UART
whose FIFO drains only when the test says. This is where the exact properties
live, since the emulator drains at once:

| test | red when |
|---|---|
| every byte out, in order, for many write sizes, ring sizes and fill caps | a byte is dropped, doubled or reordered |
| a `Write` larger than the room is held, intake paused, released at the top-up that makes room, answered only when wholly queued | it is answered early, intake keeps running, or the remainder is copied out of order |
| `Sync` is answered exactly when `pushed - count` passes its mark; with the ring empty the threshold is `pushed - mark + 1` | it answers at the take, or at ring-empty with bytes still ahead of the mark in the FIFO |
| receive: drops counted, `Lost` at the gap, merged counts, `received = delivered + lost` | a drop goes uncounted, or `Lost` is placed after bytes that came later |
| held reads answered oldest first; a reply that does not land puts its byte back | as the ns16550 cases check today |

**On the emulator:**

- **`uart_s3_lines`, no lost bytes out.** A client prints numbered 100-byte
  lines with a running checksum, then exits. Expected: every line in order,
  then `root: client ended`, then the driver's report. Red: a lost, doubled or
  reordered byte; a client line after its `ended` line (a `Sync` answered
  before its bytes).
- **`uart_s3_hold`, a held `Write` when the ring is full.** The same client
  with `uartconfig` at a 16-byte ring and an 8-byte fill cap, so every
  100-byte `Write` is held by construction (it exceeds the ring, and the
  emulator's FIFO count reads 0, so only the cap paces the fills).
  Expected: the lines, and the driver's fixed line `every write that did not
  fit was held, every byte sent`, printed only when `writes held >= 1` and
  bytes sent equals bytes queued; `held=` is printed beside it, not
  asserted. Red: the ring overflowing silently (missing bytes), or a driver
  that never holds.
- **`uart_s3_backpressure`, writers slowed and nothing lost.** The hold
  config, the driver's serving band lowered to `Low` by the test's
  `uartconfig` (a test-only key), and a client that tells 64 `Write`s in a
  burst, so it outruns the driver, the connection's ring fills, and its tells
  become calls. Expected: every line. Red: lost bytes, or a tell refused for
  room surfacing as an error.
- **`uart_s3_read`, receive.** `stdin` = `ECHO_INPUT`, typed one byte at a
  time [read tools/sos_runner.py:1065, 1078], so each byte sits below
  `RX_FULL` and arrives only through the RX timeout. The client reads four
  bytes and prints them. Red: the timeout not enabled (the case hangs to the
  harness timeout); a lost or reordered byte.
- **`uart_s3_lost`, overflow reported.** `uartconfig` with an 8-byte receive
  ring; 32 bytes typed behind the delay. The client calls `Stats` until it
  reports 32 bytes received, without reading, which is the observable that
  every byte has reached the driver; then it reads 8 bytes, then meets
  `Lost(at_least: 24)`, then prints `Stats` again. All exact, by
  construction: no `Read` was taken before every byte had arrived. Red: a
  silent drop, a wrong count, `Lost` placed elsewhere.
- **`uart_s3_kernel_lines`, the kernel's lines intact beside the driver's.**
  A printer prints numbered lines while a second child exits, so the kernel
  writes its `SOS: process exit` and `SOS: process teardown` lines [read
  kernel/core/sched.saw:1063-1069, 1396-1399] and root its `ended` line.
  Expected through the projection `thread_preempt` uses: the kernel's `SOS:`
  lines whole, and the console with them and root's line stripped equal to
  the printer's lines [read CLAUDE.md, the `thread_preempt` paragraph]. The
  projection does not care where the kernel's lines fall, so the order the
  scheduler picks is not asserted. Red: a driver byte inside a kernel line, a
  byte lost when the kernel wrote between two fills, or the driver resetting
  the FIFO.

**The runner** gains `stdin` for the S3's boots, which open the emulator with
stdin closed today [read tools/sos_runner.py:19084-19097], by the typist
`_run_qemu` already has [read tools/sos_runner.py:17315-17380].

## 10. The board stages

**B1, type a line and get it echoed.** A board image (`--emit-flash`) of a
stock root, the driver as console and `tests/uart-line-echo`, which prints a
prompt, echoes each byte it reads, and on CR or LF prints `line: TEXT` and the
`Stats` counters. The owner types `hello` and Enter on the bridge's terminal,
then pastes a 200-character line, at `rom` (115200) and at `b921600`.
Expected: each key echoed as typed, the line back whole, `lost=0` at both.
The paste is the receive path's real load: at 921600 the FIFO fills in 1.4 ms.

**B2, the console's share.** `kill_churn_profile` cannot show it. Its stall
is the kernel's own 156-byte teardown line in every kill, and "during the
rounds the teardown line is the only output" [read
hal/xtensa-esp32s3/ABI.md:3043-3060]. The kernel stays synchronous, so that
number does not move with any driver. The issue's "that stall is the console
share measured in the flash-speed runs" conflates the two.

What the driver changes is the cost of a process's printing. The proposed
profile, `console_share_profile`, board-only: a stock root, the console's
server under test, and one client with two threads, a printer at `Normal`
that prints 400 lines of 100 bytes as fast as it can and a worker at
`Background` counting loop passes in a static. The printer reads the
Monotonic clock around a print-free round and around the burst, and prints
the elapsed times and the worker's count in each. The share is one minus the
ratio of the worker's rates. Built with the stall count on
(`keys/stall-on`), whose total is the kernel's own waiting [read
hal/xtensa-esp32s3/keys/stall-on/lib.saw]. Run for `svc-console` and for the
new driver at each `s3console` key: `rom`, `b921600`, `b2000000`,
`b3000000`. Recorded, never asserted. Expected: with `svc-console` the worker
is starved while the kernel waits for the wire with interrupts masked; with
the driver the CPU is free while the wire drains, and the kernel's stall
count falls to its own lines.

## 11. Units

In order; sizes are rough.

| unit | what | size |
|---|---|---|
| U1 | `uartproto`: `Lost`, `Stats`, the new `Write`/`Sync` wording ("answered once queued", "once every byte before it has left the device"); `svc-console`, `consolecore`, ns16550 and pl011 answer `Stats`; hosted tests | S |
| U2 | `drivers/uartcore`: rings, gap marks, holds, sync marks, the serve state machine over a `UartDevice` trait; the fake UART; hosted tests (§9) | M |
| U3 | the S3 UART block in `esp32s3io` (offsets, bring-up words, the arm's register order) with hosted tests through its `RegisterPort`; `ABI.md` §4b, the driver's use of UART0 and §6's findings | S |
| U4 | `drivers/svc-uart-esp32s3` and its `uartconfig`; the runner's S3 `stdin`; the S3 cases of §9; the product template's S3 image | L |
| U5 | ns16550 and pl011 on `uartcore` (§7), with their cases' trap counts | M |
| B | B1 and B2 on the part (the lead's, through the reviewer's standing approval) | |

U1 and U2 can run in parallel; U3 needs nothing; U4 needs all three.

**Spec text.** §9's "A driver is a service" gains the transmit half: a
`Write` answered once queued, the transmit interrupt, the held `Write` and the
paused intake. §9a gains the two-writer FIFO: kernel lines whole, a driver's
bytes interleaved at a fill, the one window. §12's console record is
unchanged in substance (its `Sync` promise now rests on §3's mark). §11 lists
the window and B2's open number. `uartproto`'s own doc carries `Lost`,
`Stats` and the new wording.

## 12. Questions for the owner

1. **Loss in band.** Is a `Lost` status the reader meets at the gap the
   right report, or should loss be counted only (`Stats` and the driver's
   report) and `Read` stay byte-only?
2. **`Stats`.** Agreed as a new op every driver answers? The overflow case
   is exact only because it can wait on the driver's received count.
3. **The window in §4.** Should the builder refuse an image whose console is
   this driver and which has another full-band process, so the
   count-then-store window is closed by construction, or is a narrated
   window enough?
4. **B2.** `kill_churn_profile` measures the kernel's own lines and cannot
   move. Is `console_share_profile` the comparison you want, or did you mean
   a different stress image?
5. **ns16550 and pl011.** Move them onto `uartcore` (U5, changing their trap
   laws and those cases' counts), or keep them as the completion-queue
   examples until something on those profiles needs back-pressure?
6. **Root's lines.** Root prints through the debug print, a trap a byte, so
   on silicon a top-up can land inside `root: X ended`. Giving root the
   console's inlet when an image names one would make its lines `Write`s
   like everyone's. In scope here, or a follow-up?
