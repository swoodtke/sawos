# SO-165 design: the ESP32-S3 UART0 driver, interrupt-driven both ways

Revision 3, Nov 26. A design only: nothing here is built until the owner and
the reviewer have read it.

**Changes from revision 2**, from the owner's rulings and the reviewer's
threads on `SO:s3-uart`:

- The claim is a console-specific system op, `SystemOp.ConsoleClaim` and
  `ConsoleRelease` behind `SystemRight.Console`, granted only to the image's
  console process (§4.1). The generic IoMemory claim, its right, its overlap
  rule and its refusal are gone; `Claimed` stays as the debug print's
  answer and a second claimant's.
- Root's console never parks, and a watchdog above the driver kills a
  claimant that stops serving (§4.6, thread 5). Threads 6 and 7 are moot
  (§4.1a).
- Receive enables parity, frame and break errors and reports them at the
  mark as `Garbled` (thread 1); one mark at a time, bytes dropped behind it
  (thread 2) (§2). The `Sync` threshold yields to `TX_EMPTY` whenever the
  ring holds a byte (thread 3, §3). The B row and B1's input are worded as
  thread 4 asks (§10, §11).
- The owner's answers: a 256-byte panic text; teardown lines only for faults
  and panics; root's faults and kill are kernel panics; `svc-console` exits
  `EXIT_CLAIMED`.

Markers. **[read F:L]** is taken from that file and those lines (paths from
the sawos root at `so161`, `a87d0886`; `IDF/` is ESP-IDF tag v5.4.1).
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
   at `Rt0`. Its main thread brings the device up, claims the console, and
   joins the serving thread.
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
5. Receive: RX-full plus the RX timeout into a ring, with parity, frame and
   break errors enabled. `Read` is unchanged (one byte per request, held
   when none). A loss or an error is reported in band at one mark, as `Lost`
   or `Garbled`, with bytes dropped behind an unreported mark, and counted in
   a new `Stats` op.
6. **The console claim** (§4.1): `SystemOp.ConsoleClaim` and
   `ConsoleRelease` on the System handle, behind `SystemRight.Console`, which
   the builder grants only to the image's console process. The claim belongs
   to the claimant process and ends at its release or its death. It is
   interim, until a device object exists.
7. **While it stands the kernel does not write the console** (§4.2): its
   debug print answers `SosStatus.Claimed`, it counts what it held back and
   says so when the console returns. Panics and every machine stop take it
   back; no FIFO is reset.
8. **The kernel's routine lines move to root** while a claim stands (§4.3 to
   §4.5): a death record, read with `ProcessOp.Death`, keeps the status, the
   trap, the panic text (256 bytes, from the panic op that already carries
   it), the lock holder and the teardown's counts, and says whether the
   kernel printed them. Root prints what the kernel did not. There is no
   runtime fallback for panic text: the record carries it.
9. **Root holds the console's inlet** in every image that names one, prints
   through the debug print until it is refused, then through a writer of its
   own that never parks, and back again if the driver dies. A watchdog thread
   above the driver kills a claimant that stops serving root (§4.6).
10. **Scope: claimed images, on every profile** (§4.8). No existing case
    changes: 233 cases assert the kernel's lines, none of them in a claimed
    image.
11. **Root's panic, faults and kill are kernel panics**; root's exit stays as
    it is (§4.9).
12. The S3 product template takes the new driver; `svc-console` stays.
13. Finding: `kill_churn_profile`'s console stall is the **kernel's own
    teardown line**, not `svc-console`'s, so no driver can move it (§10).
14. Probed: the emulator's UART0 receives from the terminal into a 128-byte
    FIFO with the full threshold and the RX timeout, never overflows (it
    stops reading the host instead), and transmits at once (§6).

## 1. The driver process

**Package and records.** `drivers/svc-uart-esp32s3/`, named as its siblings
`svc-gpio-esp32s3` and `svc-uart-ns16550` are. Its `process.toml`:

```toml
format = 1
arena = 0x1000
ram = "fast"                # it services the UART's interrupt
bands = "full"              # its serving thread runs at Rt0 (§5)

[[boot]]                    # its own lines, at its end, through the debug
name = "system"             # print; its Process; and the console claim,
kind = "system"             # which the plan grants it only as the image's
tag = 21                    # console (§4.1)
rights = ["debug", "process-self", "console"]

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
  tools/sosimage/src/imagefile.saw:1093-1095], as
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
  `RXFIFO_FULL` (bit 0) | `PARITY_ERR` (2) | `FRM_ERR` (3) | `RXFIFO_OVF`
  (4) | `BRK_DET` (7) | `RXFIFO_TOUT` (8) [read IDF/uart_ll.h:39-60]. The
  three error bits answer the reviewer's thread 1; ESP-IDF's own driver
  enables parity and break by default (UNVERIFIED here: the reviewer's
  citation, `components/esp_driver_uart/src/uart.c:83-94`, is a file I did
  not fetch).
- **Then the claim** (`SystemOp.ConsoleClaim`, §4.1), once the line is
  attached and the receive interrupt enabled, and only then the serving
  thread's start (§5): every `Write` the driver serves comes after the
  kernel's last line.
- **Not touched:** `CLKDIV` and `CLK_CONF`, which are the console key's
  [read hal/xtensa-esp32s3/ABI.md:3266-3330]; and `CONF0`'s FIFO resets. The
  TX FIFO may hold the kernel's bytes, and the RX FIFO bytes typed before the
  driver started, which it delivers rather than discards.

**The interrupt arm.** Every register access comes before the ack, as spec §9
requires [read spec.md:4663-4669]. Read `INT_ST` (+0x08). For any receive bit:
read `STATUS.RXFIFO_CNT` [9:0] and pop that many bytes from `FIFO` (+0x00)
into the ring [read IDF/uart_reg.h:17-21, 672-679; IDF/uart_ll.h:337-342],
then read the count again until it is 0; count `RXFIFO_OVF` as a loss and
`PARITY_ERR`, `FRM_ERR` and `BRK_DET` as errors (below); `INT_CLR` the
receive bits. The transmit half follows (§3). Then `ack`. After
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

**Loss and damage are counted and reported, never silent.** A byte that
finds the ring full is dropped and counted, as `InBuf` does today [read
drivers/svc-uart-ns16550/src/main.saw:125-161]. An `RXFIFO_OVF` means the
hardware dropped bytes it does not count, so it counts as at least one. A
parity error, a frame error (a bad stop bit: the usual sign of a terminal at
the wrong rate) and a break each count as an error; the damaged byte is still
delivered, since the hardware flags the event and not the byte. They are
reported three ways:

- **In band, at a mark.** The ring keeps one mark, at the position of the
  first unreported event: after the last byte accepted before a loss, or
  after the bytes drained at the interrupt that reported an error, among
  which the damaged byte lies. The reader gets every byte before the mark,
  then one `Read` answered with the mark, then the bytes after it.
  `uartproto` gains two statuses for it, `Lost = 4` when any byte was lost
  and `Garbled = 5` when none was but an error was seen, and both replies
  carry the same counts: bytes lost ("at least"), then frame, parity and
  break errors, little-endian. The client sees `UartError.Lost` or
  `UartError.Garbled`, each with the counts. The mark consumes no byte.
- **One mark at a time (the reviewer's thread 2).** While a mark is
  unreported, every byte that arrives is dropped and counted into it, and
  any further error is counted into it, until the reader passes it. So
  every loss or error sits at the one position the mark names, and the bytes
  after a mark arrived whole and contiguous. Revision 2's merged count could
  leave a silent second gap behind the first mark; this cannot.
- **On request.** A new op, `Stats = 5`, a call answered `Ok` and fixed
  counters, little-endian `u32`s: bytes received, bytes lost, frame errors,
  parity errors, breaks, bytes queued, bytes sent, writes held. Every driver
  answers it (`svc-console` with its printed count and zeros). It also gives
  the cases an observable that is not a clock (§9).
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
fill, reading the count once per fill: while the claim stands nothing else
stores to the FIFO (§4.2) [read IDF/uart_reg.h:700-707; IDF/soc_caps.h:414].
Each store is a 32-bit write, never a read: a read of
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
gives each an inlet of the same serve [read spec.md:5494-5505]. The ring
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
left the FIFO; the test can only answer late, never early. It is checked at
every top-up. **The threshold is `TX_EMPTY` whenever the ring holds a byte,
and the oldest waiting `Sync`'s value, `pushed - mark + 1` (1 to 128; past
128 it is already reached), only while the ring is empty** (the reviewer's
thread 3). So the line rises exactly when the mark is passed, and a `Write`
taken while a `Sync` waits puts `TX_EMPTY` back as it queues, so the next
top-up comes at 32 bytes and the wire does not idle for the interrupt's
latency. Up to `MAX_SYNCS` are held; at the
limit intake pauses.

Compared with the brief's "once the ring and the FIFO have drained": the
same when nobody else writes, and a `Sync` cannot be starved by bytes other
clients queue after it. It keeps spec §12's promise that a process's lines
come before its exit or fault line [read spec.md:5508-5513;
rt/main/src/lib.saw:29-31]: the runtime's `Sync` returns only once those
bytes have left the FIFO, and the line about its end, root's under a claim
(§4.5), is written after the death.

**`Mark`** is answered at once, as today. **`Stats`** answers the counters.

## 4. The console claim, and UART0 between the kernel and the driver

### 4.1 The claim

**The ops and the right.** `SystemOp.ConsoleClaim = 11` and
`SystemOp.ConsoleRelease = 12`, after `ProcessByPid` [read
kernel/abi/src/ops.saw:316-326], on the System handle, and
`SystemRight.Console = 1 << 18`, after `ProcessObserve` at bit 17 [read
kernel/abi/src/rights.saw:166-178]. Neither op takes an argument.

**What it is.** A claim says the calling process writes the console and the
kernel does not. It names no device and no window: it is a fact about the
console the kernel keeps, the claimant's record index and pid, so a reused
record is never taken for the claimant [read spec.md:468-475]. The op is
interim: it is console-specific, and claiming devices in general belongs to
a device object, which is a separate design and not built. The spec says so
in those terms.

**Its answers, in the order they are decided.**

- `ConsoleClaim`: a word that names nothing is the `BadHandle` fault and a
  handle without `Console` is `AccessDenied`, on §5.7's dispatch order. A
  caller that already holds the claim is the `BadState` fault. A claim held
  by another live process answers `SosStatus.Claimed` (12), a status because
  the caller could not have known; it is the debug print's answer too
  (§4.2), the same fact from the other side.
- `ConsoleRelease` needs no right: giving the console back is never
  authority. A word that names nothing is the `BadHandle` fault; a caller
  that is not the claimant is the `BadState` fault. So a claimant can always
  end its claim through whatever System handle it holds.

**It belongs to the claimant process.** It ends at `ConsoleRelease` and at the
claimant's teardown, whatever ended it, a kill included; a panic and every
machine stop override it (§4.2). Giving away a System handle neither moves
nor strands it.

**Who holds the right.**

- Root's System carries every `SystemRight` [read
  kernel/abi/src/rights.saw:1024-1034], so `Console` too. What a
  hand-written root gives at run time is its own business, as with every
  right.
- In a stock image, a declaration's `system` record may name `console`
  beside the launch convention's four [read spec.md:5420-5425], and the
  plan grants it only to the image's console process, the one whose serve
  `[image] console` names. The builder refuses it, by name, in any other
  declaration of the image.
- An image with a hand-written root cannot name a console [read
  tools/sosimage/src/imagefile.saw:362-364], so the builder refuses every
  declaration of such an image that names `console`.

### 4.1a The reviewer's threads 6 and 7

- **t6** (a claim excludes claims, not writers): moot. The claim names no
  window, so there is no `Map` for it to gate; who can write UART0's registers
  is decided by who is given its window, which a stock image gives only to the
  process its `device` names [read tools/sosimage/src/imagefile.saw:1093-1095].
- **t7** (a claimant that gives away its last handle keeps the claim): moot.
  The claim belongs to the process, not to a handle, and `ConsoleRelease`
  needs no right, so a claimant can always end it and its death always does.

### 4.2 The console under a claim

**The kernel writes nothing to the console while another process holds the
claim.**

- **The debug print answers `Claimed`** to every caller, the claimant
  included: the driver prints its own report after it releases (§5). Today
  the op writes after its one rights check [read
  kernel/core/dispatch.saw:395-401]; the new answer comes after that check.
- **The routine lines** (§4.3) are not written. A death's detail goes into the
  process's death record (§4.4), which root prints (§4.5).
- **A line about the claimant itself is printed**, because the claimant is
  dying: every such line comes from its fault or its end, so the kernel ends
  the claim first and then writes. That is the owner's "after the driver
  dies".
- **The kernel counts what it held back**: each line it did not write, and
  each debug-print byte it refused. When the console comes back (a release, or
  the claimant's death), it writes one line if either is nonzero, `SOS:
  console returned, lines held=N, debug bytes refused=M`. So a line nobody
  else printed is never lost without a trace.

**Panics and every machine stop take the console back.** The fatal reports
(`fault`, `kernel bug`, `deadlock` [read kernel/core/diag.saw:150-206]),
root's end and its kill [read kernel/core/sched.saw:1441-1456, 1496-1503], the
runtime's abort in the kernel [read hal/xtensa-esp32s3/kernel/lib.saw:550-569],
the S3's halt lines [:522-541], its out-of-CPU-interrupts stop [:1219-1226]
and `kernel_fault` [read hal/xtensa-esp32s3/kernel/sink.c:207-211] each end
the claim through a new seam, `hal.console_reclaim()`, write the held-back
count line, and then write as today.

- **The driver's interrupt.** The kernel runs to a stop with interrupts
  masked [read spec.md:4020-4021], so no driver code runs again; the seam
  masks the console's line at the controller as well (each HAL names that
  line, as the S3's `CONSOLE_IRQ` already does [read
  hal/xtensa-esp32s3/kernel/lib.saw:1080-1082]), so the state is the same
  whatever the stop path does next.
- **No FIFO reset.** The bytes the driver stored are the run's last output,
  and they go out first; the kernel's poll appends behind them [read
  hal/xtensa-esp32s3/kernel/lib.saw:469-478]. A reset would cut the output
  just before the line that explains the stop. What is still in the driver's
  ring is lost, since no process runs again.
- **It relies on one core.** Nothing else runs while the kernel panics: the
  kernel is uniprocessor [read spec.md:4308], and on the S3 core 1 is held in
  reset with its clock gated on silicon and polls ROM code on the emulator
  [read hal/xtensa-esp32s3/ABI.md:246-249, 1841-1845]. An SMP kernel must stop
  every other core first (§11's spec text).
- **What each board's seam restores.** The S3's write path is a count read
  and a store, which need no enable bit, so its seam only masks. Whether the
  16550 and PL011 need a register put back (LCR's divisor latch, CR's
  transmit enable) after a driver is UNVERIFIED; it is U5's.

**No line is ever half on each side of a claim.** A kernel line is written
inside one kernel entry with interrupts masked, and the console loops take no
preemption point [read spec.md:4750-4753]. `ConsoleClaim`,
`ConsoleRelease` and the
teardown that ends a claim are kernel entries too, on one core, so each line
is wholly before or wholly after them, and it decides once, at its first byte,
whether it prints. A line written just before a claim is in the FIFO ahead of
anything the driver stores, since the driver neither resets nor reorders the
FIFO (§2). After a release or the claimant's death the next line prints, and
so does the next debug print.

### 4.3 Every line the kernel prints after boot

Classes: **1**, root prints it from what it already learns (the §8 status
word through its wait, the pid through `Stats` [read spec.md:2195-2198]);
**2**, root prints it from the death record (§4.4); **3**, boot or a stop,
written directly; **4**, held back and counted (§4.2).

| line | where | class |
|---|---|---|
| `SOS M1: kernel up`, `root image ok`, `boot regions`, `slab area`, `console handover`, the S3 keys' and PSRAM/flash lines | [read kernel/main.saw:29; kernel/core/loader.saw:719; process.saw:1367, 1537; areas.saw:410] | 3 (boot) |
| `SOS: process exit: code=C process=P` | [read kernel/core/sched.saw:1059-1072] | 1: `Exited` and the code are the status word |
| `SOS: process fault: REASON process=P` (a syscall fault, or a delivery's) | [read sched.saw:1635-1650, 1551-1557] | 1: the reason is the status word's code [read spec.md:4472-4475] |
| `SOS: process fault: panic process=P text=TEXT` | [read kernel/core/dispatch.saw:3346-3373] | 2: the text |
| `SOS: fault TAG cause= epc= tval=` (a trap in a process) | [read sched.saw:1656-1670] | 2: cause, pc and address; its status code is 0 [read sched.saw:1667] |
| `SOS: lock wait: the owner is a thread of another process waiter=W holder=H`, then the `BadArg` fault line | [read dispatch.saw:2361-2368] | 2: the holder's pid |
| `SOS: process teardown handles= threads= events= waiters= interrupts= timers= process=P`, at every end | [read sched.saw:1397-1418, 1444, 1476, 1498, 1556] | 2 after a fault, a trap or a panic; after an exit or a kill the counts stay in the record and root does not print them (owner's answer) |
| the teardown of a process never started | [read sched.saw:1474-1477] | 4: its record is reclaimed as the teardown ends, so no handle can read it |
| `SOS: external irq LINE`, a line nothing is bound to | [read kernel/core/irq.saw:275-280] | 4 |
| `SOS: spurious interrupt`, the first only | [read irq.saw:312-318] | 4 |
| `SOS: fp first use` (arm64) | [read dispatch.saw:5803-5807] | 4 |
| `SOS: timer tick` (harness kernels only) | [read irq.saw:238-246] | 4 |
| root's own fault, panic, exit and teardown lines; a killed root | [read sched.saw:1441-1456, 1499-1502; dispatch.saw:3346-3373] | 3 (the machine stops, §4.9) |
| `SOS: fault`, `kernel bug`, `deadlock`, `bad root image` | [read diag.saw:150-206] | 3 |
| S3: `halt pass`/`halt fail`, the stall count, out of CPU interrupts, `kernel fault vector`, `windowed call` | [read hal/xtensa-esp32s3/kernel/lib.saw:522-541, 1219-1226, 1488-1493; sink.c:207-211; keys/stall-on/lib.saw:110] | 3 |
| a debug-print byte | [read dispatch.saw:395-401] | not a kernel line: answered `Claimed` |

None is dropped outright: class 4 is counted.

**Lines about a process root does not watch.** The stock root watches every
child it launches [read root/src/main.saw:572-580]. A process another
launcher created is that launcher's to report: it holds the child's handle
and can read the same record. A hand-written root reports what it likes.
Whatever nobody prints is still in the kernel's held-back count.

### 4.4 The death record

**The op.** `ProcessOp.Death = 18`, after `SectionWord` [read
kernel/abi/src/ops.saw:489-490], on `ProcessRight.Wait`, the right
`GetStatus` spends: a supervisor that may learn a process died learns how.
It answers through a copy-out record, as `Stats` does, and through a `Gone`
process's handle, which is what it is for. A refused record terminates the
caller, as for every record. It is the design spec §8 leaves for later:
"fault forensics a supervisor can read is a later design" [read
spec.md:4476-4477].

**The record**, in doublewords as `Stats`' is:

```
 0  status       the §8 status word; a live process reads Running, the rest 0
 1  pid
 2  cause        0 exited, 1 killed, 2 fault (the reason is the status code),
                 3 trap, 4 panic, 5 lock owner in another process
 3  printed      1 when the kernel wrote this death's lines itself
 4  trap_cause   the HAL's cause word                 (cause 3)
 5  trap_pc      the faulting pc                      (cause 3)
 6  trap_addr    the faulting address                 (cause 3)
 7  holder_pid   the lock's owner's pid               (cause 5)
 8  handles      the teardown's counts, as its line prints them
 9  threads
10  events
11  waiters
12  interrupts
13  timers
14  text_len     (cause 4)
15  0
16  text         PANIC_TEXT_BYTES, as the line would print it: printable
                 ASCII, every other byte `?`
```

It is written where each line is decided today, at every death, claimed or
not, so there is one path and `printed` says which way the console went. It
lives in the process record, which outlives the body [read spec.md:48]. With
`PANIC_TEXT_BYTES` at 256 [read kernel/abi/src/ops.saw:200] it is 384 bytes a
record, about 3 KiB at the S3's default of 8 processes [read
hal/xtensa-esp32s3/board.toml:130-133].

**How the panic text gets there.** The panic op already takes it: the runtime
copies at most `PANIC_TEXT_BYTES` into a word-aligned buffer and calls the op
with it [read kernel/sysapi/src/rt.saw:143-171], and the kernel checks and
copies it in chunks [read kernel/core/dispatch.saw:3346-3410]. The kernel
copies the checked text into the record instead of straight to the console,
and prints it from the record when it prints. No op changes.

**Only the kernel carries it (owner's ruling).** There is no fallback to the
console's inlet: the runtime's panic sink is unchanged, and the text reaches
the console once, through the kernel's line when nothing claims the console
and through root's line from the record when something does.

**A panic before root watches the process.** Root attaches each child to its
Waiter only after every start [read root/src/main.saw:572-581], so a child can
panic first. Its record is not lost: the record outlives the body for as long
as a handle names it, root holds every child's handle from its create, and
attaching a dead process answers its death at the first wait [read
root/src/main.saw:582-585; spec.md:4457-4460]. Root reads the record then.
A process whose last handle is gone at its death has a record no one can read;
its lines are class 4, counted.

### 4.5 Root prints them

After each death it waits on, root reads the record (one `Death` op). When
`printed` is 1 it prints its existing `root: X ended status=N` [read
root/src/main.saw:592-598] and nothing more, so no unclaimed transcript moves.
When `printed` is 0 it prints the panic text with the status, on the same
line, then the detail:

```
root: X ended status=N text=TEXT                      cause 4
root: X trap cause=0x.. pc=0x.. addr=0x..             cause 3
root: X fault: REASON                                 cause 2 (FaultReason's text)
root: X fault: lock owner is a thread of process PID  cause 5
root: X teardown handles=.. threads=.. events=.. waiters=.. interrupts=.. timers=..
```

The teardown line follows a fault, a trap, a panic or a lock fault only
(owner's answer); an exit or a kill is the `ended` line alone, its code in
`status=`.
Root does not need to know whether a claim stood: the record says whether the
kernel printed. Root reads it after its wait returns, which is after the
teardown [read spec.md:4463-4467].

### 4.6 Root's console, and a claimant that stops taking

This answers the reviewer's thread 5. Revision 2 had root print through the
runtime's console path, where a post refused for room becomes a call that
parks [read drivers/uartpipe/src/lib.saw:9-17;
kernel/sysapi/src/rt.saw:405-420].
Behind a claimant that has stopped taking, root would park there, stop
waiting on deaths, and the machine would hang with nothing printed. Root's
console now never parks, and root can take the console back.

**Root holds the console's inlet** in every image that names a console: the
plan keeps one inlet of the console's connection for root, minted as a
client's is [read root/src/main.saw:22-33, 55-58].

- **Before the claim**, root prints through the debug print, as today, so a
  launch step's failure line is always printed.
- **From the first `Claimed` answer**, root's lines go to the inlet through a
  writer of the stock root's own, not `uartpipe` or the runtime's `print`:
  - each line is posted with its claim kept, and the claim attached to
    root's supervise Waiter, so the driver's answer arrives as a delivery
    beside the deaths, and a delivered reply is the resolve [read
    spec.md:301-308; kernel/sysapi/src/pipe.saw:1742-1745];
  - root keeps every line until its answer arrives: at most
    `ROOT_CONSOLE_OUTSTANDING` (4) posted, each a claim and a handle, and
    behind them a backlog of `ROOT_CONSOLE_BACKLOG` bytes (2 KiB);
  - a post refused for room, quota or a table row leaves the line in the
    backlog, and root attaches the inlet's room level one-shot, so it posts
    again when the ring has room [read spec.md:293-296;
    kernel/sysapi/src/pipe.saw:1714-1716];
  - a line that finds the backlog full is dropped and counted, and root
    prints `root: N lines dropped` once its console moves again.
- **Root's one wait is its supervise Waiter**, which carries the deaths, the
  answers and the room level. Root never waits on the console alone, so it
  goes on reading every death record whatever the console does.
- **If the driver dies**, its outstanding claims are delivered as
  `PeerClosed`, the claim ended at its teardown (§4.1), and root writes every
  line it still holds, in order, through the debug print, then goes on there.
  So `root: console ended status=N` prints directly.
- **At the end**, once the console's process is the only child left and
  every line root posted is answered, root drops its inlet, so the driver
  sees every inlet gone and ends.
- **Before a stop**, root waits on the same Waiter for its lines' answers, so
  its last line is out before the halt line. If they do not come (below),
  root kills the console's process first and writes them through the debug
  print.

**The console watchdog.** A claimant that is alive and not serving leaves
root's oldest line unanswered. Root runs a second thread at its `High` band,
which the stock root's header moves from level 3 to 5 [read root/Saw.toml:30;
spec.md:4117-4121], above the driver's `Rt0` (4). It sleeps on a periodic
Timer of `CONSOLE_STALL_PERIOD` (1 s) only while root has a line
unanswered. At each wake it reads the console process's `Stats` [read
spec.md:2195-2198]. When the oldest unanswered line is the same as at the
previous wake and the process's syscall count has not moved since, two wakes
in a row, the console is stalled. Root kills it with the code
`CONSOLE_STALLED`; the teardown ends the claim, and root writes `root:
console stalled, killed` and its held lines through the debug print.

- **Why the test is sound.** The driver runs above every thread but the
  watchdog, so a driver able to run takes root's staged message before
  anything below it runs. One whose syscall count stands still across two
  whole periods, with root's line waiting, is blocked on something that is
  not coming, or spinning: a spinner makes no syscalls and starves everything
  below `Rt0`, which is why the watchdog sits above it.
- **A healthy driver keeps moving.** Holding a `Write` while the wire
  drains, it makes a syscall at every top-up, at most 32 byte times apart
  (2.8 ms at 115200), far inside the period. On the emulator it never waits
  on the wire at all (§6).
- **The residue.** The period is the guest clock, which follows host time. A
  false kill needs the host to stall across a whole period inside the few
  instructions between the watchdog's block and the driver's next syscall,
  twice in a row; I name it rather than claim it cannot happen. A claimant
  that stalls while root has nothing to print goes unnoticed until root next
  prints, which is at the next death, the moment it matters.

**In an image whose console is `svc-console`**, nothing claims, root's debug
print is never refused, and none of this runs: those transcripts do not move.

### 4.7 What each caller of the debug print does with `Claimed`

- **Root**: switches to its own writer on the inlet (§4.6).
- **`svc-console`** writes every byte through the debug print and fails its
  serve on any error [read drivers/svc-console/src/main.saw:174-184]. In a
  stock image it never meets `Claimed`: only the image's console process
  holds the right, and `svc-console`, when it is that process, asks for none
  (§4.1). In a hand-written image whose root lets another process claim, it
  ends with a new exit code, `EXIT_CLAIMED = 3` (owner's answer), so its
  supervisor's status names why; its clients' calls answer `PeerClosed`.
- **The runtime's `print`** in a process with no console record drops a byte
  the debug print refuses, and keeps no status, on purpose [read
  kernel/sysapi/src/rt.saw:48-49, 61-65]. It keeps dropping, since it has
  nowhere else to write, and the kernel counts each refused byte (§4.2), so
  the loss is reported. In a stock image naming a console, every process but
  root and the driver has a console record.
- **The runtime's panic path** never uses the debug print: the text goes to
  the panic op [read kernel/sysapi/src/rt.saw:143-171], so a claim cannot
  refuse it, and the record keeps it (§4.4).
- **The console's driver** prints its report through the debug print only
  after it releases (§5).

### 4.8 Scope: claimed images, on every profile

Counted from `TEST_CASES` by a script that reads each case's expected text
and arches [probed: `so165/count/` in scratch]: 233 of 557 gate cases assert a
kernel `process exit`, `process teardown`, `process fault`, trap or lock-wait
line: 229 on riscv32, 218 on arm64, 217 on riscv32-flat (before that
profile's exclusions), and 30 of the 74 gate cases the S3 smoke runs. 21 of
the 233 boot a described image or a stock root; the other 212 have a
hand-written root. 10 boot images that name a console, and every one is
served by `svc-console`, which claims nothing.

- **Every image** (the kernel never prints routine lines; root does): all 233
  change, and the 212 hand-written roots print none of these lines, so their
  assertions are lost unless each root learns to print the record.
- **Images with a console claim** (recommended): the kernel prints exactly as
  today unless a claim stands. No existing case changes in this work. The
  claim is in kcore, so it behaves the same on every profile: today only the
  new S3 driver claims; after U5 the ns16550 and pl011 drivers do. 9 cases use
  those drivers, and 2 of them assert kernel lines, from drivers that exit
  before they serve and so before any claim; but the hand-written roots and
  clients of those 9 print through the debug print while the driver serves,
  and U5 must move them to the connection (§7).

### 4.9 Root's end

**Today.** Every end of root stops the machine. `end_process` writes the
teardown line for root as for any process, then stops with the status it was
handed instead of rescheduling [read kernel/core/sched.saw:1441-1456]: an
exit stops with root's code, a pass at 0 and a fail otherwise; a syscall
fault with `ProcessFault` (5) and a trap with `UmodeFault` (2) [read
sched.saw:1635-1639, 1656-1670]; a panic, after its fault line with the text,
with `Panic` (64), the status the kernel's own panic stops with, "so a panic
reads the same whichever side raised it" [read kernel/core/diag.saw:135-138;
dispatch.saw:3346-3373]; and a kill with `KilledRoot` (8) [read
sched.saw:1496-1503].

**After (owner's rulings).** Root's panic, its faults (a syscall fault, a
trap, a delivery's fault) and its kill are kernel panics: the panic path
takes the console back over any claim (§4.2) and writes root's line, its
panic text included, in the kernel's panic output. Each already stops the
machine with a failing status of the kernel's own vocabulary, so the change
is the reclaim; the lines and the statuses stay what they are, and no case
asserting them moves.

**Root's exit stays exactly as it is**: its teardown line, then the halt with
its code. It is a stop, so under a claim it reclaims first, and the bytes
the driver had stored go out before the kernel's lines.

## 5. Bands and threads

**Two threads.** The main thread starts at `Normal` [read spec.md:4111]. It
maps the window, makes the Waiter, attaches the line and the outlet, brings
the UART up (§2), claims the console (§4.1), starts the serving thread at
`Rt0`, and joins it. When every client is gone the serving thread drains its
ring and the FIFO (its `Sync` mechanism, with the mark at `queued`) and
returns; the main thread releases the console and prints the report through
the debug print, which the kernel honours again. If the driver dies instead,
its teardown ends the claim (§4.1), and the kernel prints its end directly.
The serving thread does everything else: messages, the line, the
holds. One thread owns the one Interrupt, spec §9's canonical shape [read
spec.md:4670-4674]; UART0 has one line for both directions, so a separate
transmit thread would be a second servicer of it.

**Rt0, not High.** `bands = "full"` is the identity map [read
tools/sosimage/src/declaration.saw:50-53]; the simple map puts `High` and
every realtime band at level 3, and only the full map reaches 4 to 7 [read
spec.md:4117-4121]. At `High` the driver would share level 3 with any
simple-map client's `High` thread and take turns with it; at `Rt0` (level 4)
no simple-map process can reach it. A client's post readies the driver above
the poster, so it runs at once; the line readies it above every client [read
spec.md:4102-4106]. §7's convention that a server runs at or above its
clients holds [read spec.md:4300-4303], and the builder's band check passes: it
refuses a full-map client of a simple-map server, and this is the reverse
[read spec.md:5399-5404].

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
all at once before the boot [read tools/sos_runner.py:17409-17474]. The S3
boot path opens the emulator with stdin closed [read
tools/sos_runner.py:19178-19191] and gains the same typist (U4). The flash
image boots through the ROM, and bytes typed before the driver starts wait
in the model's FIFO, which the driver does not reset (§2). That the ROM's
flash boot leaves UART0's receiver alone is UNVERIFIED; if it does not, U4
gives the typist a key to wait for the client's prompt line before its first
byte.

**What only the board can check:** the FIFO draining at a real rate, so a
nonzero `TXFIFO_CNT`, the empty threshold's timing and a ring that actually
fills under one writer; `RXFIFO_OVF` at a high rate; the RX timeout with a
human at the terminal; a stop taking the console back while the FIFO still
holds the driver's bytes (§4.2); and B2's share.

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
tools/sos_runner.py:11974-12034]).

**When.** After the S3 driver, as one unit. QEMU's 16550 and PL011 transmit
at once (UNVERIFIED: QEMU's `serial.c` and `pl011.c` are not on this machine),
so on those profiles nothing ever back-pressures and the change buys no
throughput. What it buys is one transmit path in three drivers, and the gate's
three profiles running `uartcore`'s holds through real devices on every patch.
The S3 smoke gates too, and `uartcore`'s hosted tests run in every full
`make sos-test`, so the S3 driver does not need them to land first. If the
owner would rather keep the emulators' drivers as the completion-queue
examples they are, they can wait.

**The claim comes with them.** Each claims the console as the S3 driver does.
Under the claim the hand-written roots and clients of the 9 cases that use
these drivers (§4.8) can no longer print through the debug print while the
driver serves: their prints must go through the connection or come after the
driver's release, which is part of U5's sweep. Whether each board's
`console_reclaim` must put a register back is U5's too (§4.2).

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

**The grant.** In such an image the plan grants `console` on the driver's
System record, which its declaration asks for (§1), and the builder refuses
it, by name, in any other declaration (§4.1).

**Root's inlet.** The plan keeps one inlet of the console's connection for
root in every image that names a console, and root's handle count and arena
grow by what that needs (`sosimage.plan` sizes both [read
root/src/main.saw:55-62]). Root uses it, through its own writer, only once
its debug print is refused (§4.6), so the `svc-console` images, where nothing
claims, print as they do today. Root drops it when the console's process is
the last child left, so the console's driver ends as it does today.

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
| a `Write` taken while a `Sync` waits puts the threshold back to `TX_EMPTY` | the fake UART's next top-up comes at the `Sync`'s lowered level |
| receive: drops counted, the mark where the first event was, `received = delivered + lost` | a drop goes uncounted, or the mark is placed after bytes that came later |
| a second loss while a mark is unreported is dropped behind the mark, never after bytes the reader has not passed | a silent second gap among the bytes after a mark |
| an error drained with bytes puts a `Garbled` mark after them; a loss with it makes the mark `Lost`, carrying both counts | an error delivered with no mark, or a count missing from the reply |
| held reads answered oldest first; a reply that does not land puts its byte back | as the ns16550 cases check today |

**On the emulator:**

- **`uart_s3_lines`, no lost bytes out.** A client prints numbered 100-byte
  lines with a running checksum, then exits. Expected: every line in order;
  root's `root: client ended status=...` and `root: client teardown ...`
  through the driver (the kernel held its two lines); then, once root drops
  its inlet and the driver releases, `SOS: console returned, lines held=2,
  debug bytes refused=0`, the driver's report, the kernel's own lines for the
  driver's exit, and `root: console ended`. Red: a lost, doubled or reordered
  byte; a client line after its `ended` line (a `Sync` answered before its
  bytes); any `SOS:` line between the claim and the release.
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
- **`uart_s3_claimed_ends`, deaths under the claim.** Four clients: one
  exits, one panics with a 300-byte text, one faults on a syscall, one calls
  `debug_print_byte` and prints the answer through its console before it
  exits. Expected, through the driver: `debug print: the console is
  claimed`; root's `ended` line for each, the panic's text cut to 256 bytes
  on its own; a `teardown` line for the panic and the fault only; then the
  returned line with `lines held=` the kernel's own count of what it did not
  write and `debug bytes refused=1`. The order between the clients is the
  scheduler's, so the case matches each client's lines as a group, not their
  interleaving. Red: a kernel line written under the claim; a record field
  wrong; a panic text lost or uncut; a teardown line after an exit; the
  refusal silent.
- **No error case on the emulator.** The runner's terminal sends whole bytes,
  so it cannot produce a parity or frame error, and whether the model would
  report one is UNVERIFIED. The hosted rows carry the logic, and B1 the part
  (§10).
- **`uart_s3_stall`.** `uartconfig`'s test-only `STALL_AFTER = 1`: the driver
  serves one message and then stops taking, alive. A client exits. Expected:
  root's watchdog kills the console; the kernel's returned line; then,
  through the debug print, `root: console stalled, killed`, `root: client
  ended status=...` and `root: console ended status=...` (the kill). No time
  is asserted. Red: root parked behind the stalled driver (the case hangs to
  the harness timeout); the client's death unreported.

**On the gate's three profiles, and the S3 smoke's shared list** (a
hand-written root, no driver; these are the kernel's own cases):

- **`console_claim`.** Root claims the console itself (root's System holds
  every right), starts a child that faults, calls the debug print (it
  answers `Claimed`), waits for the child, releases, then prints what it
  recorded and the child's death record. A second child given a System with
  `console` claims while root holds it: `Claimed`; a third releases without
  holding it: the `BadState` fault. Expected: no `SOS:` line between the
  claim and the release; then `SOS: console returned, lines held=N, debug
  bytes refused=1`; then root's lines with the record's fields. Red: the
  kernel writing under the claim; a count wrong; the release not giving the
  console back; a claim surviving its claimant's death.
- **`console_stall`.** A test claimant (`tests/console-staller`) that
  claims, takes one message and stops taking, with a stock root naming it as
  the console. The same expectations as `uart_s3_stall`, on every profile.
- **`death_record`.** No claim. Root reads the record of children that exit,
  are killed, fault on a syscall, trap, panic (a 300-byte text) and wait on a
  lock another child owns, and prints every field. Each record reads
  `printed=1`, and the kernel's own lines are unchanged beside root's. Red:
  any field wrong, the text not cut at 256, a live process's record not
  reading `Running`.
- **`root_panic_claimed`** (S3 smoke): root panics while the console's driver
  holds the claim. Expected: the driver's stored bytes, then the kernel's
  panic output with root's text, then `halt fail code=0x40`. Red: the claim
  not overridden, the text missing.

**The runner** gains `stdin` for the S3's boots, which open the emulator with
stdin closed today [read tools/sos_runner.py:19178-19191], by the typist
`_run_qemu` already has [read tools/sos_runner.py:17409-17474].

## 10. The board stages

**B1, type a line and get it echoed.** A board image (`--emit-flash`) of a
stock root, the driver as console and `tests/uart-line-echo`, which prints a
prompt, echoes each byte it reads, and on CR or LF prints `line: TEXT` and the
`Stats` counters. The owner types `hello` and Enter on the bridge's terminal,
then pastes a 200-character line, at `rom` (115200) and at `b921600`.
Expected: each key echoed as typed, the line back whole, `lost=0` at both.
The paste is the receive path's real load: at 921600 the FIFO fills in 1.4 ms.
Then, with the image at `b921600`, the owner types a few keys from a terminal
opened at 115200. Expected: the client reports `Garbled` with frame errors
counted, which is the failure the reviewer's thread 1 names, never `lost=0`
with garbage.

**Who sends B1's input.** The owner types it at the bridge's terminal. No
tool sends bytes to the board in this design; if the lead wants the reviewer
to send them, the reviewer asks the owner before the first one, since the
standing grant has covered flashing and capturing only.

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
| U0 | the kernel: `SystemOp.ConsoleClaim`/`ConsoleRelease`, `SystemRight.Console`, `SosStatus.Claimed`; the console under the claim (held lines, the refused debug print, the returned line, `hal.console_reclaim` and each HAL's console line on every stop); the death record and `ProcessOp.Death`, the panic text kept in it; root's panic, faults and kill as kernel panics; `sos` wrappers; the cases `console_claim` and `death_record` on every profile | L |
| U0b | root and the builder: `console` as a System record right granted only to the image's console, and its refusals; the plan's inlet for root; root's non-parking console writer, its record printing, its watchdog thread and its header's `High` at 5; `svc-console`'s `EXIT_CLAIMED`; `tests/console-staller` and `console_stall` | M |
| U1 | `uartproto`: `Lost`, `Stats`, the new `Write`/`Sync` wording ("answered once queued", "once every byte before it has left the device"); every driver answers `Stats`; hosted tests | S |
| U2 | `drivers/uartcore`: rings, gap marks, holds, sync marks, the serve state machine over a `UartDevice` trait; the fake UART; hosted tests (§9) | M |
| U3 | the S3 UART block in `esp32s3io` (offsets, bring-up words, the arm's register order) with hosted tests through its `RegisterPort`; `ABI.md` §4b, the driver's use of UART0 and §6's findings | S |
| U4 | `drivers/svc-uart-esp32s3` and its `uartconfig`; the runner's S3 `stdin`; the S3 cases of §9; the product template's S3 image | L |
| U5 | ns16550 and pl011 on `uartcore` with their claims (§7), their cases' trap counts and debug prints | M |
| B | B1 and B2 on the part, under the owner's standing grant to the reviewer; B1's input typed by the owner | |

U0, U1, U2 and U3 can run in parallel within the concurrency limit; U0b
needs U0; U4 needs all of them.

**Spec text.**

- §2.8, `System`: the ops `ConsoleClaim` and `ConsoleRelease` and the right
  `Console`, said as they work: the claim is console-specific and interim,
  and claiming devices belongs to a device object, a separate design that is
  not built. §12's launch convention: `console` as a System record right the
  stock root gives only to the image's console, beside the four.
- §5.7's statuses: `Claimed`. §2.8's `DebugPrint` row [read spec.md:2326]:
  it answers `Claimed` while the console is claimed.
- §2.7 and §8: `ProcessOp.Death` and the record, replacing "fault forensics
  a supervisor can read is a later design" [read spec.md:4476-4477]; root's
  panic, faults and kill as kernel panics, and root's exit unchanged.
- §9: the transmit half of "a driver is a service" (a `Write` answered once
  queued, the transmit interrupt, the held `Write`, the paused intake).
- §9a, rewritten around the claim: boot, then the kernel writes until a
  console claim, nothing while it stands but the claimant's own end, a panic
  and a stop, which take the console back; the held-back count; root's lines.
  The panic path's text says its takeover of UART0 relies on there being one
  core today: nothing else runs while the kernel panics (§7), and on the S3
  core 1 is held in reset with its clock gated on silicon and polls ROM code
  on the emulator [read hal/xtensa-esp32s3/ABI.md:246-249, 1841-1845].
- §7, beside "Uniprocessor kernel in v1" [read spec.md:4308]: **a panic
  stops every other core before it prints or takes any device back**, by a
  stop IPI the other core cannot mask, a bounded wait for each core's
  acknowledgement, and the core held in reset where the board can do that.
- §12's console record: root holds the console's inlet, writes to it without
  parking once the console is claimed, and watches the claimant; the
  `Sync` promise is unchanged. `uartproto`'s doc: `Lost`, `Garbled`,
  `Stats`.
- §11: B2's open number. `uartproto`'s own doc carries `Lost`, `Stats` and
  the new wording.

## 12. Questions for the owner

1. **The watchdog (§4.6).** Root kills a console that serves none of root's
   lines and makes no syscall across two 1-second checks, from a thread its
   header puts above the driver. Is killing the console the way back you
   want, and is a 1-second period right for the part?
2. **The mark's names (§2).** One mark, two statuses: `Lost` when a byte was
   lost, `Garbled` when only an error was seen, both carrying every count.
   Or one status for both?
3. **Root's `High` at level 5.** The watchdog needs a thread above the
   console's `Rt0`; moving the stock root's `High` from 3 to 5 is the
   smallest change that gives one. Acceptable, or should the watchdog use a
   band the header names separately?
