# SO-165 design: the ESP32-S3 UART0 driver, interrupt-driven both ways

Revision 2, Nov 26. A design only: nothing here is built until the owner and
the reviewer have read it.

**Changes from revision 1**, from the owner's answers and rulings:

- Loss is reported in band (`Lost`) and counted (`Stats`), as proposed; B2 is
  `console_share_profile`; ns16550 and pl011 move later (U5).
- §4 is new. A generic **device claim** (`IoMemoryOp.Claim`/`Release`,
  `IoMemoryRight.Claim`) replaces revision 1's shared FIFO: while the
  console is claimed the kernel does not write to it, its debug print answers
  a new `SosStatus.Claimed`, its routine lines go into a **death record**
  (`ProcessOp.Death`) root prints through the console's inlet, and a panic or
  any machine stop takes the console back. A process's panic text reaches
  root only through the record (no runtime fallback). Root's panic is a
  kernel panic. The spec gains the SMP requirement that a panic stops every
  other core first.
- Revision 1's two-writer analysis, its count-then-store window and its
  questions 3 and 6 are gone with it.

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
   at `Rt0`. Its main thread brings the device up, claims it, and joins the
   serving thread.
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
   byte per request, held when none). A loss is reported in band, at the gap,
   as `Lost`, and counted in a new `Stats` op.
6. **The device claim** (§4.1): `IoMemoryOp.Claim` and `Release` on any
   window, behind the opt-in `IoMemoryRight.Claim`. A claim is ownership:
   an intersecting claim by another process answers `SosStatus.Claimed`. It
   does not gate `Map`. It ends at release, the object's free, or the
   claimant's death.
7. **A claim on the console's window hands the console over** (§4.2): the
   kernel writes nothing to UART0, its debug print answers `Claimed`, it
   counts what it held back and says so when the console returns. Panics and
   every machine stop take it back; no FIFO is reset.
8. **The kernel's routine lines move to root** while a claim stands (§4.3 to
   §4.5): a death record, read with `ProcessOp.Death`, keeps the status, the
   trap, the panic text (256 bytes, from the panic op that already carries
   it), the lock holder and the teardown's counts, and says whether the
   kernel printed them. Root prints what the kernel did not. There is no
   runtime fallback for panic text: the record carries it.
9. **Root holds the console's inlet** in every image that names one, prints
   through the debug print until it is refused, then through the driver, and
   back again if the driver dies (§4.6).
10. **Scope: claimed images, on every profile** (§4.8). No existing case
    changes: 233 cases assert the kernel's lines, none of them in a claimed
    image.
11. **Root's panic is a kernel panic**, and so is every root end but an exit;
    root's exit stays as it is (§4.9).
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
name = "system"             # print; and its Process
kind = "system"
tag = 21
rights = ["debug", "process-self"]

[[boot]]
name = "uart"
kind = "window"
type = "esp32s3-uart"
tag = 22
rights = ["map", "claim"]   # claim: the opt-in right (§4.1)

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
  `RXFIFO_FULL` (bit 0) | `RXFIFO_OVF` (4) | `RXFIFO_TOUT` (8) [read
  IDF/uart_ll.h:39-60].
- **Then the claim** (§4.1), once the line is attached and the receive
  interrupt enabled, and only then the serving thread's start (§5): every
  `Write` the driver serves comes after the kernel's last line.
- **Not touched:** `CLKDIV` and `CLK_CONF`, which are the console key's
  [read hal/xtensa-esp32s3/ABI.md:3266-3330]; and `CONF0`'s FIFO resets. The
  TX FIFO may hold the kernel's bytes, and the RX FIFO bytes typed before the
  driver started, which it delivers rather than discards.

**The interrupt arm.** Every register access comes before the ack, as spec §9
requires [read spec.md:4663-4669]. Read `INT_ST` (+0x08). For any receive bit:
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
come before its exit or fault line [read spec.md:5508-5513;
rt/main/src/lib.saw:29-31]: the runtime's `Sync` returns only once those
bytes have left the FIFO, and the line about its end, root's under a claim
(§4.5), is written after the death.

**`Mark`** is answered at once, as today. **`Stats`** answers the counters.

## 4. The device claim, and UART0 between the kernel and the driver

Revision 1's §4 (two writers in one FIFO, lines split at a fill, a
count-then-store window) is gone. Under the owner's ruling one writer holds
the console at a time, so none of it arises.

### 4.1 The claim, on any device

**The ops and the right.** `IoMemoryOp.Claim = 2` and `IoMemoryOp.Release =
3`, after `Carve` and `Map` [read kernel/abi/src/ops.saw:912-915], and
`IoMemoryRight.Claim = 1 << 10`, after `Map` at bit 9 [read
kernel/abi/src/rights.saw:746-751]. Neither takes an argument.

**What it is.** A claim says the calling process owns the registers the
window covers until it releases them, the window's object is freed, or the
process dies. It is kept on the IoMemory slot (the claimant's record index and
pid, as a pipe exchange keeps its sender's, so a reused record is never taken
for the claimant [read spec.md:468-475]), and the overlap test is a scan of the
IoMemory slab, which is bounded (`MAX_IOMEMORIES`, raised only by an image's
limits [read kernel/core/limits.saw:151]); bounded slab scans take no
preemption point [read spec.md:4750-4753].

**The overlap rule.** Two claims conflict when their byte ranges intersect
and their claimants differ:

- a carved part and the whole it was cut from intersect, so either refuses the
  other;
- two parts conflict only if they share a byte, so two drivers can own two
  disjoint register groups of one block (a `part = true` window [read
  tools/sosimage/src/declaration.saw:44-45]);
- two objects naming the same range (a second carve of it) intersect;
- a process may hold claims that overlap each other: it is one owner.

**Its answers, in the order they are decided.** `Claim`: a word that names
nothing is the `BadHandle` fault and a handle without `Claim` is
`AccessDenied`, on §5.7's dispatch order; a caller that already holds this
object's claim is the `BadState` fault; an intersecting claim held by another
live process answers the new status `SosStatus.Claimed` (12), a status
because the caller could not have known. `Release`: the same two faults, then
a caller that does not hold this object's claim is the `BadState` fault.

**The refusal status.** None of the existing words fits. `NoResource` means
no room [read kernel/abi/src/status.saw:171], `WouldBlock` invites a retry
[:180], and `Revoked` says the object is gone [:175]; here the object is
live and another process owns it. `Claimed` also answers a debug print while
the console is claimed (§4.2), the same fact from the other side.

**A claim does not limit `Map`.** The lead's reading holds against the code.
`Map` is already the right on the handle, and a launcher maps windows into
its children through their Process handles [read spec.md:1622-1626], so a
claim that gated `Map` would make every launcher order its maps against its
children's claims. On the flat tier a mapping protects nothing anyway [read
hal/xtensa-esp32s3/kernel/lib.saw:356-358]. A claim is a statement to the
kernel and to other claimants, not a lock on a mapping.

**When it ends.** At `Release`; when the object is freed with its last handle
[read spec.md:2740]; and at the claimant's teardown, which ends every claim
the process holds before it closes the process's handles, so whatever the
teardown prints comes after the claim has ended. A kill is a teardown too.

**On a device the kernel uses.** A claim whose range intersects the HAL's
console window (`DEVICE_GRANT_BASE`, `DEVICE_GRANT_LEN`, which kcore already
reads [read kernel/core/loader.saw:329-333] and to which the builder holds the
board's one `kernel = true` row [read
tools/sosimage/src/boardcheck.saw:296-310])
is a console claim (§4.2). The lead's two semantics hold, with one statement
of scope: the console is the only device the kernel drives that is a board
row. The tick's timer and the interrupt controller are not rows, so no window
names them (the S3's tick source is a reserved line [read
hal/xtensa-esp32s3/board.toml:122-124]). Every claim is ownership, the
console's included; a console claim also hands the console over.

**Which handles carry the right.** `Claim` is opt-in, as `physical` is on
Memory and Pool [read tools/sosimage/src/rights.saw:17-21, 110-112]:

- The kernel's default IoMemory set gains it [read
  kernel/abi/src/rights.saw:1283-1286], so root's device rows carry it and a
  launcher can pass it on; a carve keeps the carving handle's rights [read
  spec.md:1480-1482].
- The stock root narrows every child's IoMemory ceiling to every right but
  `claim`, as it narrows Memory and Pool to every right but `physical` [read
  spec.md:5405-5409], unless the plan grants it.
- A declaration asks for it in its window record's `rights` (or its
  `[ceilings]`), and the image grants it: implicitly, to the image's console
  process on its window of the `kernel = true` device; through the image's
  per-process `ceilings` for any other device [read
  tools/sosimage/src/imagefile.saw:65-72]. The builder refuses, by name,
  `claim` on a `kernel = true` window for any process but the image's
  console.
- A hand-written root gives what it holds, as with every right.

### 4.2 The console under a claim

**The kernel writes nothing to the console while another process holds a
console claim.**

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
  masks the console's line at the controller as well, so the state is the
  same whatever the stop path does next.
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
preemption point [read spec.md:4750-4753]. `Claim`, `Release` and the
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
| `SOS: process teardown handles= threads= events= waiters= interrupts= timers= process=P`, at every end | [read sched.saw:1397-1418, 1444, 1476, 1498, 1556] | 2: the six counts |
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
hal/xtensa-esp32s3/board.toml:130-133] (question 1).

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

An exit or a kill adds only the teardown line; the code is in `status=`.
Root does not need to know whether a claim stood: the record says whether the
kernel printed. Root reads it after its wait returns, which is after the
teardown [read spec.md:4463-4467].

### 4.6 Root's console, before and after the driver

**Root holds the console's inlet** in every image that names a console. Root
creates the image's connections and holds their ends [read
root/src/main.saw:22-33, 55-58]; the plan has it keep one inlet of the
console's connection for itself, minted as a client's is.

- **Before the driver claims**, root prints through the debug print, as
  today, so a launch step's failure line is always printed.
- **On the first `Claimed` answer** the runtime adopts the inlet and sends the
  rest of the line, and every later one, through it. A claim can fall between
  two of root's bytes; the line's first part is already in the FIFO ahead of
  the driver's, so it reads in order. This is a standby mode in `sos.rt`: an
  inlet held but not adopted until the debug print is refused.
- **If the driver dies**, the inlet answers `PeerClosed` and root goes back to
  the debug print, which the kernel honours again: the claim ended at the
  teardown, before the driver's handles closed (§4.1). So `root: console
  ended status=N` and the driver's detail lines print directly.
- **At the end**, root drops its inlet once the console's process is the only
  child it still waits for, after a `Sync`, so the driver sees every inlet
  gone and ends. Without that the driver would serve root's inlet for ever
  and root would wait for it for ever.
- **Before a stop**, root syncs its console. Root stops the machine itself
  rather than exiting [read root/src/main.saw:36-40], so the exit path's sync
  [read rt/main/src/lib.saw:29-31] does not run for it; with the sync its
  last line is out before the kernel's halt line.

In an image whose console is `svc-console`, nothing claims, root's debug print
is never refused, and root never adopts the inlet: those transcripts do not
move.

### 4.7 What each caller of the debug print does with `Claimed`

- **Root**: switches to the inlet (§4.6).
- **`svc-console`** writes every byte through the debug print and fails its
  serve on any error [read drivers/svc-console/src/main.saw:174-184]. In a
  stock image it never meets `Claimed`: nothing in such an image can claim
  the console's window (§4.1). In a hand-written image that lets another
  process claim it, it ends with a new exit code, `EXIT_CLAIMED = 3`, so its
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

**After (owner's ruling).** Root's panic is a kernel panic: the panic path
takes the console back over any claim (§4.2) and writes root's text in the
kernel's panic output. I recommend the same for every other end of root that
is not an exit: a syscall fault, a trap, a delivery's fault, and a kill. Root
holds the system's supervision, so any of them leaves nothing to report the
rest, and each already stops the machine with a failing status of the
kernel's own vocabulary. Making them system faults adds only the reclaim; the
lines and the statuses stay what they are, so no case asserting them moves.

**Root's exit stays exactly as it is**: its teardown line, then the halt with
its code. It is a stop, so under a claim it reclaims first, and the bytes
the driver had stored go out before the kernel's lines.

## 5. Bands and threads

**Two threads.** The main thread starts at `Normal` [read spec.md:4111]. It
maps the window, makes the Waiter, attaches the line and the outlet, brings
the UART up (§2), claims the window (§4.1), starts the serving thread at
`Rt0`, and joins it. When every client is gone the serving thread drains its
ring and the FIFO (its `Sync` mechanism, with the mark at `queued`) and
returns; the main thread releases the claim and prints the report through
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

**The claim comes with them.** Each claims its window as the S3 driver does.
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

**The grant.** In such an image the builder grants `claim` on UART0's window
to the console's process, whose declaration asks for it (§1), and refuses it
on a `kernel = true` window to any other process (§4.1).

**Root's inlet.** The plan keeps one inlet of the console's connection for
root in every image that names a console, and root's handle count and arena
grow by what that needs (`sosimage.plan` sizes both [read
root/src/main.saw:55-62]). Root holds it on standby and adopts it only when
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
| receive: drops counted, `Lost` at the gap, merged counts, `received = delivered + lost` | a drop goes uncounted, or `Lost` is placed after bytes that came later |
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
  claimed`; root's lines for each, the panic's text cut to 256 bytes on its
  `ended` line; then the returned line with `lines held=` the kernel's own
  count of what it did not write and `debug bytes refused=1`. The order
  between the clients is the scheduler's, so the case matches each client's
  lines as a group, not their interleaving. Red: a kernel line written under
  the claim; a record field wrong; a panic text lost or uncut; the refusal
  silent.

**On the gate's three profiles, and the S3 smoke's shared list** (a
hand-written root, no driver; these are the kernel's own cases):

- **`device_claim`.** Root carves the RTC row [read
  hal/riscv32/board.toml:50-55; hal/arm64/board.toml:51] into two disjoint
  parts and gives a child the whole and one part. The child claims a part:
  `Ok`. Root claims the whole: `Claimed`. Root claims the other part: `Ok`.
  The child claims its part again: the `BadState` fault, and its death ends
  its claim, after which root's claim of the whole answers `Ok`. A release by
  a non-claimant and a claim without the right each fault a further child.
  Red: an intersection missed, a disjoint pair refused, a claim surviving its
  release or its claimant's death, a wrong answer's order.
- **`console_claim`.** Root claims the console's window itself, starts a
  child that faults, calls the debug print (it answers `Claimed`), waits for
  the child, releases, then prints what it recorded and the child's death
  record. Expected: no `SOS:` line between the claim and the release; then
  `SOS: console returned, lines held=2, debug bytes refused=1`; then root's
  lines with the record's fields. Red: the kernel writing under the claim; a
  count wrong; the release not giving the console back.
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
| U0 | the kernel: `IoMemoryOp.Claim`/`Release`, `IoMemoryRight.Claim` (opt-in), `SosStatus.Claimed`; the console under a claim (held lines, the refused debug print, the returned line, `hal.console_reclaim` on every stop); the death record and `ProcessOp.Death`, the panic text kept in it; root's non-exit ends as system faults; `sos` wrappers; the cases `device_claim`, `console_claim`, `death_record` on every profile | L |
| U0b | root and the runtime: the plan's inlet for root and the builder's `claim` grant and refusal; `sos.rt`'s standby console; root prints from the record, drops its inlet when only the console is left, syncs before a stop; `svc-console`'s `EXIT_CLAIMED` | M |
| U1 | `uartproto`: `Lost`, `Stats`, the new `Write`/`Sync` wording ("answered once queued", "once every byte before it has left the device"); every driver answers `Stats`; hosted tests | S |
| U2 | `drivers/uartcore`: rings, gap marks, holds, sync marks, the serve state machine over a `UartDevice` trait; the fake UART; hosted tests (§9) | M |
| U3 | the S3 UART block in `esp32s3io` (offsets, bring-up words, the arm's register order) with hosted tests through its `RegisterPort`; `ABI.md` §4b, the driver's use of UART0 and §6's findings | S |
| U4 | `drivers/svc-uart-esp32s3` and its `uartconfig`; the runner's S3 `stdin`; the S3 cases of §9; the product template's S3 image | L |
| U5 | ns16550 and pl011 on `uartcore` with their claims (§7), their cases' trap counts and debug prints | M |
| B | B1 and B2 on the part (the lead's, through the reviewer's standing approval) | |

U0, U1, U2 and U3 can run in parallel within the concurrency limit; U0b
needs U0; U4 needs all of them.

**Spec text.**

- §2.5, `IoMemory`: the ops `Claim` and `Release`, the right, the overlap
  rule, the ends of a claim, and that a claim does not gate `Map`; the
  console claim.
- §3's rights tables and the default IoMemory set; §12's launch convention:
  the stock root narrows `claim` as it narrows `physical`, and the builder's
  grant and refusal.
- §5.7's statuses: `Claimed`. §2.8's `DebugPrint` row [read spec.md:2326]:
  it answers `Claimed` under a console claim.
- §2.7 and §8: `ProcessOp.Death` and the record, replacing "fault forensics
  a supervisor can read is a later design" [read spec.md:4476-4477]; root's
  ends that are system faults, and root's exit unchanged.
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
- §12's console record: root holds the console's inlet on standby; its
  `Sync` promise unchanged.
- §11: B2's open number. `uartproto`'s own doc carries `Lost`, `Stats` and
  the new wording.

## 12. Questions for the owner

1. **The record's size.** 384 bytes a process record (about 3 KiB at the
   S3's default of 8 processes), most of it the 256-byte panic text. Keep the
   text at the panic op's bound, or cut the record's copy to 128?
2. **Root's lines in a claimed image.** Root prints the teardown counts for
   every death the kernel did not print, as the kernel does today. Keep them,
   or print them only for faults and panics?
3. **`svc-console` meeting a claim** (only in a hand-written image that lets
   another process claim the console): end with `EXIT_CLAIMED` (proposed), or
   keep serving and answer each `Write` `Unsupported`?
4. **Root's other ends.** Your ruling makes root's panic a kernel panic. I
   recommend the same for its faults and its kill, with today's lines and
   statuses (§4.9). Agreed?
